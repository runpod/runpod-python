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

    async def request(
        self, method: str, path: str, body: Any = None, *, timeout: float
    ) -> Any:
        """plain http call (lb resources only)."""
        raise NotImplementedError(f"{type(self).__name__} does not serve http routes")


async def _post_json(
    url: str,
    payload: Any,
    headers: Dict[str, str],
    timeout: float,
    *,
    app_name: str = "",
    resource_name: str = "",
) -> Dict[str, Any]:
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 404:
                raise EndpointNotFound(app_name, resource_name)
            resp.raise_for_status()
            return await resp.json()


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
        url = f"{_endpoint_url_base()}/{SENTINEL_ID}/runsync"
        data = await _post_json(
            url,
            payload,
            self._sentinel_headers(),
            timeout,
            app_name=self.app_name,
            resource_name=self.resource_name,
        )
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
    """

    def __init__(self, endpoint_id: str):
        self.endpoint_id = endpoint_id

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

    async def invoke(
        self, payload: Dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> Any:
        url = f"{_endpoint_url_base()}/{self.endpoint_id}/runsync"
        data = await _post_json(url, payload, _headers(), timeout)
        return self.unwrap(data)

    async def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{_endpoint_url_base()}/{self.endpoint_id}/run"
        return await _post_json(url, payload, _headers(), DEFAULT_TIMEOUT_SECONDS)

    async def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Any:
        url = f"https://{self.endpoint_id}.{_lb_domain()}{path}"
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.request(
                method, url, json=body, headers=_headers()
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text


class PodTarget(InvocationTarget):
    """ephemeral pod execution for @app.task: provision a pod, run the
    function body, collect the result, terminate.

    transport lands with the task execution engine.
    """

    def __init__(self, spec: ResourceSpec, fn: Callable):
        self.spec = spec
        self.fn = fn

    async def invoke(
        self, payload: Dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> Any:
        raise NotImplementedError(
            "task pod execution is not wired up yet; use .local() meanwhile"
        )

    async def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError(
            "task pod execution is not wired up yet; use .local() meanwhile"
        )
