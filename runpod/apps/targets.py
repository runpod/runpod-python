"""invocation targets: where a `.remote()` call actually goes.

a target is resolved per-call by App._resolve and knows how to reach one
deployed resource. all resolution is server-side (sentinel headers or the
runpod api); no local state is kept.
"""

import inspect
import json
import os
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

import aiohttp

from ..user_agent import USER_AGENT
from .errors import EndpointNotFound, RemoteExecutionError
from .protocol import FunctionRequest, FunctionResponse
from .spec import ResourceSpec

# the sentinel pseudo-endpoint id; ai-api resolves the real endpoint from
# the X-Flash-App / X-Flash-Environment / X-Flash-Endpoint headers.
SENTINEL_ID = "flash"

DEFAULT_TIMEOUT_SECONDS = 300.0

# terminal job statuses on the queue data plane
FINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"})


def _api_key() -> str:
    key = os.getenv("RUNPOD_API_KEY")
    if not key:
        import runpod

        key = runpod.api_key
    if not key:
        raise RuntimeError(
            "no api key configured. run `rp login` or set RUNPOD_API_KEY."
        )
    return key


def _endpoint_url_base() -> str:
    import runpod

    return runpod.endpoint_url_base.rstrip("/")


def _lb_domain() -> str:
    """host portion of the data-plane base url, for lb subdomain urls."""
    base = _endpoint_url_base()
    host = base.split("://", 1)[-1]
    return host.split("/", 1)[0]


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if extra:
        headers.update(extra)
    return headers


def args_to_input(fn: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """map positional args onto parameter names so the job input is a dict."""
    sig = inspect.signature(fn)
    params = [n for n in sig.parameters if n not in ("self", "cls")]
    body: Dict[str, Any] = {}
    for i, arg in enumerate(args):
        if i >= len(params):
            raise TypeError(
                f"{fn.__name__}() got {len(args)} positional args, "
                f"expected at most {len(params)}"
            )
        body[params[i]] = arg
    body.update(kwargs)
    # the platform strips empty input dicts from jobs; keep one field so
    # the worker's job polling never sees a missing input
    if not body:
        body = {"__empty": True}
    return body


def unwrap_job_output(data: Dict[str, Any]) -> Any:
    """extract output from a runsync-style response, raising on failure."""
    if data.get("status") == "FAILED" or data.get("error"):
        err = data.get("error") or data.get("output", {}).get("error", "unknown")
        raise RemoteExecutionError(f"remote execution failed: {err}")
    output = data.get("output", data)
    if isinstance(output, dict) and "error" in output:
        raise RemoteExecutionError(f"remote execution failed: {output['error']}")
    return output


class InvocationTarget(ABC):
    """one resolved destination for remote calls.

    each target owns its payload shape: sentinel targets send plain
    kwargs (the deployed worker resolves functions from the unpacked
    build), live targets send the full FunctionRequest with source.
    """

    def build_payload(
        self, fn: Callable, spec: ResourceSpec, args: tuple, kwargs: dict
    ) -> Dict[str, Any]:
        return {"input": args_to_input(fn, args, kwargs)}

    def unwrap(self, data: Dict[str, Any]) -> Any:
        return unwrap_job_output(data)

    @abstractmethod
    async def invoke(self, payload: Dict[str, Any], *, timeout: float) -> Any:
        """submit and wait for the result."""

    @abstractmethod
    async def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """submit without waiting; returns the raw job data."""

    async def wait(
        self, job_data: Dict[str, Any], *, timeout: float
    ) -> Any:
        """wait for a submitted job to finish and return its output."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support job polling"
        )

    async def request(
        self, method: str, path: str, body: Any = None, *, timeout: float
    ) -> Any:
        """plain http call (lb resources only)."""
        raise NotImplementedError(f"{type(self).__name__} does not serve http routes")


# transient statuses worth retrying: gateway/edge errors that occur
# while the sentinel cache warms or an edge node hiccups. 4xx (other
# than 429) are never retried; the request itself is wrong.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504, 520, 522, 524})
RETRY_ATTEMPTS = 4
RETRY_BASE_DELAY = 0.5


async def _request_json(
    method: str,
    url: str,
    headers: Dict[str, str],
    timeout: float,
    *,
    payload: Any = None,
    app_name: str = "",
    resource_name: str = "",
) -> Dict[str, Any]:
    """http json call with exponential backoff on transient failures."""
    import asyncio

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    last_exc: Optional[Exception] = None
    for attempt in range(RETRY_ATTEMPTS):
        if attempt:
            await asyncio.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.request(
                    method, url, json=payload, headers=headers
                ) as resp:
                    if resp.status == 404:
                        raise EndpointNotFound(app_name, resource_name)
                    if resp.status in RETRYABLE_STATUSES:
                        last_exc = aiohttp.ClientResponseError(
                            resp.request_info,
                            resp.history,
                            status=resp.status,
                            message=await resp.text(),
                        )
                        continue
                    resp.raise_for_status()
                    return await resp.json()
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
            # connection-level failures (reset, ssl hiccup, dns) are
            # transient by nature; response-level errors above already
            # decided retryability
            last_exc = exc
            continue
    raise last_exc  # type: ignore[misc]


async def _post_json(
    url: str,
    payload: Any,
    headers: Dict[str, str],
    timeout: float,
    *,
    app_name: str = "",
    resource_name: str = "",
) -> Dict[str, Any]:
    return await _request_json(
        "POST",
        url,
        headers,
        timeout,
        payload=payload,
        app_name=app_name,
        resource_name=resource_name,
    )


async def _get_json(
    url: str, headers: Dict[str, str], timeout: float
) -> Dict[str, Any]:
    return await _request_json("GET", url, headers, timeout)


async def _wait_terminal(
    base_url: str,
    data: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
    on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """poll /status until the job reaches a terminal state.

    runsync returns early (e.g. IN_QUEUE) when the job outlives the sync
    window, typically on cold starts; polling covers the rest. on_status,
    when given, sees every payload (observability hooks read workerId).
    """
    import asyncio
    import time

    if on_status is not None:
        on_status(data)
    deadline = time.monotonic() + timeout
    interval = 0.5
    # observed polls (dev sessions) stay tight so the worker id
    # surfaces fast enough for log streams to attach in realtime
    max_interval = 1.0 if on_status is not None else 5.0
    while data.get("status") not in FINAL_STATUSES:
        job_id = data.get("id")
        if not job_id:
            return data
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"job {job_id} did not complete within {timeout}s "
                f"(last status: {data.get('status', 'UNKNOWN')})"
            )
        await asyncio.sleep(interval)
        interval = min(interval * 1.5, max_interval)
        data = await _get_json(f"{base_url}/status/{job_id}", headers, 30.0)
        if on_status is not None:
            on_status(data)
    return data


class SentinelTarget(InvocationTarget):
    """routes through the sentinel endpoint; ai-api resolves the real
    endpoint server-side from app/env/resource headers.

    used for deployed resources from every context (worker-to-worker and
    local caller alike), which is what makes resolution stateless.
    """

    def __init__(self, app_name: str, env_name: str, resource_name: str):
        self.app_name = app_name
        self.env_name = env_name
        self.resource_name = resource_name

    def _sentinel_headers(self) -> Dict[str, str]:
        return _headers(
            {
                "X-Flash-App": self.app_name,
                "X-Flash-Environment": self.env_name,
                "X-Flash-Endpoint": self.resource_name,
            }
        )

    async def invoke(
        self, payload: Dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> Any:
        base = f"{_endpoint_url_base()}/{SENTINEL_ID}"
        headers = self._sentinel_headers()
        data = await _post_json(
            f"{base}/runsync",
            payload,
            headers,
            timeout,
            app_name=self.app_name,
            resource_name=self.resource_name,
        )
        data = await _wait_terminal(base, data, headers, timeout)
        return unwrap_job_output(data)

    async def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{_endpoint_url_base()}/{SENTINEL_ID}/run"
        return await _post_json(
            url,
            payload,
            self._sentinel_headers(),
            DEFAULT_TIMEOUT_SECONDS,
            app_name=self.app_name,
            resource_name=self.resource_name,
        )

    async def wait(
        self,
        job_data: Dict[str, Any],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Any:
        base = f"{_endpoint_url_base()}/{SENTINEL_ID}"
        data = await _wait_terminal(
            base, job_data, self._sentinel_headers(), timeout
        )
        return unwrap_job_output(data)

    async def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Any:
        url = f"https://{SENTINEL_ID}.{_lb_domain()}{path}"
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.request(
                method, url, json=body, headers=self._sentinel_headers()
            ) as resp:
                if resp.status == 404:
                    raise EndpointNotFound(self.app_name, self.resource_name)
                resp.raise_for_status()
                text = await resp.text()
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text


class LiveTarget(InvocationTarget):
    """a dev-session live endpoint: source code ships with every request,
    so the worker executes whatever is currently on disk.

    provisioning of the live endpoint itself is owned by the dev session
    (see runpod.apps.dev); this target only speaks to an endpoint id.
    when an event sink is attached, each call gets a dispatch line,
    live worker status/log feed, and a completion line.
    """

    def __init__(
        self,
        endpoint_id: str,
        resource_name: str = "",
        events: Optional[object] = None,
        metrics_key: Optional[str] = None,
    ):
        self.endpoint_id = endpoint_id
        self.resource_name = resource_name
        self.events = events
        self.metrics_key = metrics_key
        self._source_target: Optional[Any] = None
        self._source_resource: str = ""
        # source hash the worker last confirmed; None forces a sync
        self._synced_hash: Optional[str] = None

    def attach_source(self, target: Any, resource: str) -> None:
        """register the object whose module backs this api resource.

        live api endpoints materialize their routes from module source
        pushed over /_runpod/sync (once per source change), keeping dev
        behavior identical to deployed.
        """
        self._source_target = target
        self._source_resource = resource

    async def _sync_source(self, timeout: float) -> None:
        """push the current module source to the worker if it changed."""
        if self._source_target is None:
            return
        import hashlib

        from .serialization import get_function_source

        source = get_function_source(self._source_target)
        digest = hashlib.sha256(source.encode()).hexdigest()
        if digest == self._synced_hash:
            return
        url = f"https://{self.endpoint_id}.{_lb_domain()}/_runpod/sync"
        await _post_json(
            url,
            {"source": source, "resource": self._source_resource},
            _headers(),
            timeout,
        )
        self._synced_hash = digest

    def build_payload(
        self, fn: Callable, spec: ResourceSpec, args: tuple, kwargs: dict
    ) -> Dict[str, Any]:
        from .serialization import (
            get_function_source,
            serialize_args,
            serialize_kwargs,
        )

        request = FunctionRequest(
            function_name=fn.__name__,
            function_code=get_function_source(fn),
            args=serialize_args(args),
            kwargs=serialize_kwargs(kwargs),
            dependencies=spec.dependencies,
            system_dependencies=spec.system_dependencies,
            accelerate_downloads=spec.accelerate_downloads,
        )
        return {"input": request.to_input()}

    def unwrap(self, data: Dict[str, Any]) -> Any:
        output = unwrap_job_output(data)
        if isinstance(output, dict) and "success" in output:
            response = FunctionResponse.from_output(output)
            if not response.success:
                raise RemoteExecutionError(
                    f"remote execution failed: {response.error or 'unknown'}"
                )
            if response.result is not None:
                from .serialization import deserialize_result

                return deserialize_result(response.result)
            return response.json_result
        return output

    def _monitor(self):
        if self.events is None:
            return None
        from .monitor import WorkerMonitor

        return WorkerMonitor(
            self.endpoint_id,
            self.resource_name,
            self.events,
            metrics_key=self.metrics_key,
        )

    async def invoke(
        self, payload: Dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> Any:
        import time

        from .monitor import emit

        base = f"{_endpoint_url_base()}/{self.endpoint_id}"
        headers = _headers()
        monitor = self._monitor()
        emit(self.events, "dispatch", self.resource_name)
        start = time.monotonic()
        if monitor is not None:
            await monitor.start()
        try:
            if monitor is not None:
                # async submit + status polling: runsync would hold the
                # connection, hiding the workerId until completion and
                # starving the live worker feed
                data = await _post_json(f"{base}/run", payload, headers, timeout)
            else:
                data = await _post_json(
                    f"{base}/runsync", payload, headers, timeout
                )
            data = await _wait_terminal(
                base,
                data,
                headers,
                timeout,
                on_status=monitor.on_status if monitor else None,
            )
            result = self.unwrap(data)
        except Exception:
            emit(
                self.events,
                "request_failed",
                self.resource_name,
                time.monotonic() - start,
            )
            raise
        finally:
            if monitor is not None:
                await monitor.stop()
        emit(
            self.events,
            "request_completed",
            self.resource_name,
            time.monotonic() - start,
        )
        return result

    async def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{_endpoint_url_base()}/{self.endpoint_id}/run"
        return await _post_json(url, payload, _headers(), DEFAULT_TIMEOUT_SECONDS)

    async def wait(
        self,
        job_data: Dict[str, Any],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Any:
        base = f"{_endpoint_url_base()}/{self.endpoint_id}"
        data = await _wait_terminal(base, job_data, _headers(), timeout)
        return self.unwrap(data)

    async def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Any:
        import time

        from .monitor import emit

        emit(self.events, "dispatch", self.resource_name, f"{method} {path}")
        start = time.monotonic()
        url = f"https://{self.endpoint_id}.{_lb_domain()}{path}"
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        try:
            await self._sync_source(timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.request(
                    method, url, json=body, headers=_headers()
                ) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
        except Exception:
            emit(
                self.events,
                "request_failed",
                self.resource_name,
                time.monotonic() - start,
            )
            raise
        emit(
            self.events,
            "request_completed",
            self.resource_name,
            time.monotonic() - start,
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


class PodTarget(InvocationTarget):
    """ephemeral pod execution for @app.task: provision a pod, run the
    function body, collect the result, terminate.

    tasks run to completion far beyond the queue data plane's sync
    window, so the transport is a direct http channel to a single-shot
    runner on the pod (see runpod.apps.tasks).
    """

    # tasks default to a long window; the pod's terminateAfter is the backstop
    TASK_TIMEOUT_SECONDS = 3600.0

    def __init__(
        self,
        spec: ResourceSpec,
        fn: Callable,
        events: Optional[object] = None,
    ):
        self.spec = spec
        self.fn = fn
        self.events = events

    def build_payload(
        self, fn: Callable, spec: ResourceSpec, args: tuple, kwargs: dict
    ) -> Dict[str, Any]:
        from .serialization import (
            get_function_source,
            serialize_args,
            serialize_kwargs,
        )

        request = FunctionRequest(
            function_name=fn.__name__,
            function_code=get_function_source(fn),
            args=serialize_args(args),
            kwargs=serialize_kwargs(kwargs),
            dependencies=spec.dependencies,
            system_dependencies=spec.system_dependencies,
            accelerate_downloads=spec.accelerate_downloads,
        )
        return request.to_input()

    async def invoke(
        self, payload: Dict[str, Any], *, timeout: float = TASK_TIMEOUT_SECONDS
    ) -> Any:
        import time

        from .monitor import emit
        from .tasks import TaskExecution, unwrap_task_response

        name = self.spec.name
        hardware = ",".join(self.spec.cpu or self.spec.gpu or ["any"])
        emit(self.events, "dispatch", name)
        emit(self.events, "task_status", name, f"provisioning pod on {hardware}")
        start = time.monotonic()

        stream = None
        execution = TaskExecution(self.spec)
        try:
            await execution.start()
            if execution.pod_id:
                emit(
                    self.events,
                    "task_status",
                    name,
                    f"pod {execution.pod_id[:12]} waiting for runtime",
                )
                if self.events is not None:
                    from .monitor import PodLogStream

                    stream = PodLogStream(execution.pod_id, name, self.events)
                    stream.attach()
            await execution.wait_ready()
            emit(self.events, "worker_ready", name, execution.pod_id or "")
            response = await execution.execute(payload, timeout)
        except Exception:
            emit(
                self.events,
                "request_failed",
                name,
                time.monotonic() - start,
            )
            raise
        finally:
            if stream is not None:
                await stream.stop()
            await execution.terminate()
        result = unwrap_task_response(response)
        emit(
            self.events,
            "request_completed",
            name,
            time.monotonic() - start,
        )
        return result

    async def submit(self, payload: Dict[str, Any]) -> Any:
        from .tasks import TaskExecution, TaskJob

        execution = TaskExecution(self.spec)
        await execution.start()
        await execution.wait_ready()
        await execution.submit(payload)
        return TaskJob(execution)
