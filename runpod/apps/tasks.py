"""ephemeral pod execution for @app.task.

lifecycle per call:
  1. deploy a pod on a task runtime image (runpod/task*, built from
     runtimes/task in this repo). custom images get the runner source
     delivered base64-encoded in the pod env and booted via dockerArgs,
     so any image with a python3 binary works.
  2. wait for the runner's /ping via the pod http proxy
  3. POST the FunctionRequest to /execute (remote) or /submit (spawn)
  4. collect the response, terminate the pod

`terminateAfter` is set at deploy time as a server-side safety net so a
crashed client cannot leak a running pod indefinitely.
"""

import asyncio
import base64
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from .api import AppsApiClient
from .errors import RemoteExecutionError
from .spec import ResourceSpec

log = logging.getLogger(__name__)

TASK_PORT = 8080

# safety net: pods self-terminate server-side after this long
DEFAULT_MAX_LIFETIME = timedelta(hours=1)

import os as _os

READY_POLL_INTERVAL = 2.0
READY_TIMEOUT = 600.0
RESULT_POLL_INTERVAL = 2.0


def _runner_source() -> str:
    return (
        Path(__file__).parent.parent / "runtimes" / "task" / "runner.py"
    ).read_text()


def _bootstrap_command() -> str:
    """shell command that materializes and starts the runner."""
    from .shim import shell_launcher

    return shell_launcher("RUNPOD_TASK_RUNNER_B64", "/task_runner.py")


def _device_names(gpu: Optional[List[str]]) -> List[str]:
    """pods take exact device names, not pool ids; expand pool ids
    (e.g. ADA_24 -> NVIDIA GeForce RTX 4090) and pass device names
    through. no gpu constraint means any device, which the pod api
    only understands as the full device list."""
    from .gpu import POOLS_TO_TYPES, GpuGroup

    if not gpu:
        return sorted(
            {t.value for types in POOLS_TO_TYPES.values() for t in types}
        )
    names: List[str] = []
    for entry in gpu:
        try:
            names.extend(GpuGroup(entry).device_names() or [entry])
        except ValueError:
            names.append(entry)
    return names


def _proxy_url(pod_id: str) -> str:
    return f"https://{pod_id}-{TASK_PORT}.proxy.runpod.net"


def _pod_input(spec: ResourceSpec, token: str, task_name: str) -> Dict[str, Any]:
    """build the pod deploy input for one task execution.

    runtime images have the runner baked in (CMD starts it); custom
    images bootstrap the runner from a base64 env payload via dockerArgs.
    """
    terminate_after = (
        datetime.now(timezone.utc) + DEFAULT_MAX_LIFETIME
    ).isoformat()

    env = {
        "RUNPOD_TASK_TOKEN": token,
        "RUNPOD_TASK_PORT": str(TASK_PORT),
        **(spec.env or {}),
    }

    from .images import image_for_spec, local_python_version

    pod: Dict[str, Any] = {
        "name": f"task-{task_name}-{secrets.token_hex(4)}",
        "imageName": image_for_spec(
            spec, python_version=local_python_version()
        ),
        "ports": f"{TASK_PORT}/http",
        "containerDiskInGb": 10 if spec.is_cpu else 30,
        "terminateAfter": terminate_after,
        "supportPublicIp": True,
    }

    if spec.image:
        # custom image: inject the runner source and boot it explicitly
        env["RUNPOD_TASK_RUNNER_B64"] = base64.b64encode(
            _runner_source().encode()
        ).decode()
        package_spec = _os.environ.get("RUNPOD_PACKAGE_SPEC")
        if package_spec:
            env["RUNPOD_PACKAGE_SPEC"] = package_spec
        pod["dockerArgs"] = _bootstrap_command()

    pod["env"] = [{"key": k, "value": v} for k, v in env.items()]
    if spec.datacenter:
        pod["dataCenterIds"] = spec.datacenter
    else:
        # spread across the storage-supported set; a null DC can pin
        # repeated deploys to one (possibly broken) machine
        from .datacenter import CPU3_DATACENTERS, DataCenter

        pool = CPU3_DATACENTERS if spec.is_cpu else DataCenter.all()
        pod["dataCenterIds"] = [dc.value for dc in pool]
    if spec.volume:
        pod["networkVolumeId"] = spec.volume
    if spec.is_cpu:
        pod["instanceIds"] = spec.cpu
    else:
        pod["gpuTypeIdList"] = _device_names(spec.gpu)
        pod["gpuCount"] = spec.gpu_count
    return pod


class TaskExecution:
    """one pod running one function."""

    def __init__(self, spec: ResourceSpec, api: Optional[AppsApiClient] = None):
        self.spec = spec
        self.api = api or AppsApiClient()
        self.token = secrets.token_urlsafe(32)
        self.pod_id: Optional[str] = None

    @property
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def start(self) -> None:
        result = await self.api.deploy_task_pod(
            _pod_input(self.spec, self.token, self.spec.name),
            is_cpu=self.spec.is_cpu,
        )
        self.pod_id = result["id"]
        log.info("task pod %s deployed for %s", self.pod_id, self.spec.name)

    async def wait_ready(self, timeout: float = READY_TIMEOUT) -> None:
        """poll the runner's /ping through the pod proxy until it answers."""
        url = f"{_proxy_url(self.pod_id)}/ping"
        deadline = time.monotonic() + timeout
        async with aiohttp.ClientSession() as session:
            while time.monotonic() < deadline:
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            return
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                await asyncio.sleep(READY_POLL_INTERVAL)
        raise TimeoutError(
            f"task pod {self.pod_id} did not become ready within {timeout}s"
        )

    async def execute(self, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """run to completion: submit, then poll for the result.

        the pod proxy caps how long a single request can stay open
        (long jobs would 524), so completion always goes through the
        background slot + short /result polls.
        """
        await self.submit(request)
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"task on pod {self.pod_id} did not finish in {timeout}s"
                )
            response = await self.poll_result()
            if response is not None:
                return response
            await asyncio.sleep(RESULT_POLL_INTERVAL)

    async def submit(self, request: Dict[str, Any]) -> None:
        """start in the background via /submit.

        the proxy answers 404 for pods an edge node has not learned
        about yet; since this pod demonstrably exists (we created it
        and /ping succeeded), 404s here are propagation races and are
        retried briefly rather than surfaced.
        """
        url = f"{_proxy_url(self.pod_id)}/submit"
        attempts = 6
        async with aiohttp.ClientSession() as session:
            for attempt in range(attempts):
                async with session.post(
                    url,
                    json=request,
                    headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 404 and attempt < attempts - 1:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    return

    async def poll_result(self) -> Optional[Dict[str, Any]]:
        """fetch /result; returns the response dict once DONE, else None."""
        url = f"{_proxy_url(self.pod_id)}/result"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("status") == "DONE":
            return data.get("response")
        return None

    async def terminate(self) -> None:
        if self.pod_id is None:
            return
        try:
            await self.api.terminate_pod(self.pod_id)
            log.info("task pod %s terminated", self.pod_id)
        except Exception as exc:  # noqa: BLE001 - terminateAfter is the backstop
            log.warning(
                "failed to terminate task pod %s (terminateAfter is the "
                "backstop): %s",
                self.pod_id,
                exc,
            )
        self.pod_id = None


def unwrap_task_response(response: Dict[str, Any]) -> Any:
    """extract the function result from a runner response."""
    if not response.get("success"):
        raise RemoteExecutionError(
            f"task execution failed: {response.get('error', 'unknown')}"
        )
    if response.get("result") is not None:
        from .serialization import deserialize_result

        return deserialize_result(response["result"])
    return response.get("json_result")


class TaskJob:
    """handle for a spawned task; owns the pod until the result is read."""

    def __init__(self, execution: TaskExecution):
        self._execution = execution
        self._result: Any = None
        self._done = False

    @property
    def pod_id(self) -> Optional[str]:
        return self._execution.pod_id

    async def wait(self, timeout: Optional[float] = None) -> Any:
        """block until the task finishes; terminates the pod and returns
        the result."""
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            while not self._done:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"task on pod {self.pod_id} did not finish in {timeout}s"
                    )
                response = await self._execution.poll_result()
                if response is not None:
                    self._result = unwrap_task_response(response)
                    self._done = True
                    break
                await asyncio.sleep(RESULT_POLL_INTERVAL)
        finally:
            if self._done:
                await self._execution.terminate()
        return self._result

    async def cancel(self) -> None:
        """terminate the pod, abandoning the task."""
        await self._execution.terminate()
        self._done = True
