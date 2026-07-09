"""the App registry: where a project declares its resources.

    import runpod
    from runpod import App, GpuType

    app = App("my-app")

    @app.queue(name="transcribe", gpu=GpuType.NVIDIA_GEFORCE_RTX_4090)
    async def transcribe(audio_url: str): ...

`rp deploy` imports project modules, collects App instances from the
module-level registry, and ships each app's resources. resolution of
deployed resources is fully server-side (sentinel headers), so apps hold
no persistent local state.
"""

import os
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .context import Context, current_context
from .errors import EndpointNotFound, InvalidResourceError
from .gpu import GpuLike
from .handles import ApiHandle, FunctionHandle
from .spec import (
    DEFAULT_SCALER_VALUE,
    ResourceKind,
    ResourceSpec,
    normalize_cpu,
    normalize_cuda_version,
    normalize_gpu,
    normalize_scaler_type,
    normalize_workers,
)
from .targets import InvocationTarget, PodTarget, SentinelTarget

DEFAULT_ENV = "default"

# module-level registry populated as user modules import; `rp deploy` and
# `rp dev` read it after importing the target modules
_REGISTRY: List["App"] = []


def get_registered_apps() -> List["App"]:
    """all App instances created in this process."""
    return list(_REGISTRY)


def _clear_registry() -> None:
    """testing only."""
    _REGISTRY.clear()


class App:
    """a named collection of deployable resources."""

    def __init__(self, name: str, *, env: str = DEFAULT_ENV):
        if not name or not isinstance(name, str):
            raise InvalidResourceError("App name must be a non-empty string")
        self.name = name
        self.env = env
        self._resources: Dict[str, Union[FunctionHandle, ApiHandle]] = {}
        # event sink for task lifecycle rendering, set by dev sessions
        self._dev_events: Optional[object] = None
        # populated by an `rp dev` session: resource name -> live target
        self._dev_targets: Dict[str, InvocationTarget] = {}
        _REGISTRY.append(self)

    @property
    def resources(self) -> Dict[str, Union[FunctionHandle, ApiHandle]]:
        return dict(self._resources)

    def _register(
        self, name: str, handle: Union[FunctionHandle, ApiHandle]
    ) -> None:
        if name in self._resources:
            raise InvalidResourceError(
                f"duplicate resource name '{name}' in app '{self.name}'"
            )
        self._resources[name] = handle

    # -- decorators --

    def queue(
        self,
        *,
        name: Optional[str] = None,
        gpu: Optional[Union[GpuLike, List[GpuLike]]] = None,
        cpu: Optional[Union[str, List[str]]] = None,
        gpu_count: int = 1,
        workers: Union[int, Tuple[int, int], None] = None,
        idle_timeout: int = 60,
        dependencies: Optional[List[str]] = None,
        system_dependencies: Optional[List[str]] = None,
        volume: Optional[Any] = None,
        env: Optional[Dict[str, Any]] = None,
        datacenter: Optional[Union[str, List[str]]] = None,
        image: Optional[str] = None,
        registry_auth: Optional[str] = None,
        model: Optional[Any] = None,
        max_concurrency: int = 1,
        execution_timeout_ms: int = 0,
        flashboot: bool = True,
        scaler_type: Optional[str] = None,
        scaler_value: int = DEFAULT_SCALER_VALUE,
        min_cuda_version: Optional[str] = None,
        accelerate_downloads: bool = True,
        container_disk_gb: Optional[int] = None,
    ) -> Callable[[Callable], FunctionHandle]:
        """declare a queue-based serverless endpoint from a function.

        image selects a custom base image for the workers; the deployed
        code still arrives via the build artifact and is booted by the
        runtime bootstrap, so any image with a python3 binary works.

        max_concurrency lets one worker process several jobs at once;
        values above 1 only achieve real concurrency with async
        functions. execution_timeout_ms caps a single job (0 = no cap).
        """

        def decorator(fn: Callable) -> FunctionHandle:
            spec = ResourceSpec(
                kind=ResourceKind.QUEUE,
                name=name or fn.__name__,
                gpu=normalize_gpu(gpu),
                cpu=normalize_cpu(cpu),
                gpu_count=gpu_count,
                workers=normalize_workers(workers),
                idle_timeout=idle_timeout,
                dependencies=dependencies,
                system_dependencies=system_dependencies,
                volume=_volume_ref(volume),
                env=env,
                datacenter=_datacenter_list(datacenter),
                image=image,
                registry_auth=registry_auth,
                model=model,
                max_concurrency=max_concurrency,
                execution_timeout_ms=execution_timeout_ms,
                flashboot=flashboot,
                scaler_type=normalize_scaler_type(scaler_type),
                scaler_value=scaler_value,
                min_cuda_version=normalize_cuda_version(min_cuda_version),
                accelerate_downloads=accelerate_downloads,
                container_disk_gb=container_disk_gb,
            )
            handle = FunctionHandle(self, fn, spec)
            self._register(spec.name, handle)
            return handle

        return decorator

    def task(
        self,
        *,
        name: Optional[str] = None,
        gpu: Optional[Union[GpuLike, List[GpuLike]]] = None,
        cpu: Optional[Union[str, List[str]]] = None,
        gpu_count: int = 1,
        dependencies: Optional[List[str]] = None,
        system_dependencies: Optional[List[str]] = None,
        volume: Optional[Any] = None,
        env: Optional[Dict[str, Any]] = None,
        image: Optional[str] = None,
        registry_auth: Optional[str] = None,
        datacenter: Optional[Union[str, List[str]]] = None,
        min_cuda_version: Optional[str] = None,
        accelerate_downloads: bool = True,
        container_disk_gb: Optional[int] = None,
    ) -> Callable[[Callable], FunctionHandle]:
        """declare ephemeral pod compute from a function.

        tasks have no standing infrastructure: `.remote()` provisions a
        pod, runs the body, returns the result, and terminates the pod.
        they never require `rp deploy` (except to register a schedule).

        platform-cached models (`model=`) are only available on queue
        and api resources; tasks download weights themselves.
        """

        def decorator(fn: Callable) -> FunctionHandle:
            spec = ResourceSpec(
                kind=ResourceKind.TASK,
                name=name or fn.__name__,
                gpu=normalize_gpu(gpu),
                cpu=normalize_cpu(cpu),
                gpu_count=gpu_count,
                dependencies=dependencies,
                system_dependencies=system_dependencies,
                volume=_volume_ref(volume),
                env=env,
                image=image,
                registry_auth=registry_auth,
                datacenter=_datacenter_list(datacenter),
                min_cuda_version=normalize_cuda_version(min_cuda_version),
                accelerate_downloads=accelerate_downloads,
                container_disk_gb=container_disk_gb,
            )
            handle = FunctionHandle(self, fn, spec)
            self._register(spec.name, handle)
            return handle

        return decorator

    def api(
        self,
        *,
        name: Optional[str] = None,
        gpu: Optional[Union[GpuLike, List[GpuLike]]] = None,
        cpu: Optional[Union[str, List[str]]] = None,
        gpu_count: int = 1,
        workers: Union[int, Tuple[int, int], None] = None,
        idle_timeout: int = 60,
        dependencies: Optional[List[str]] = None,
        system_dependencies: Optional[List[str]] = None,
        volume: Optional[Any] = None,
        env: Optional[Dict[str, Any]] = None,
        datacenter: Optional[Union[str, List[str]]] = None,
        image: Optional[str] = None,
        registry_auth: Optional[str] = None,
        model: Optional[Any] = None,
        execution_timeout_ms: int = 0,
        flashboot: bool = True,
        scaler_type: Optional[str] = None,
        scaler_value: int = DEFAULT_SCALER_VALUE,
        min_cuda_version: Optional[str] = None,
        accelerate_downloads: bool = True,
        container_disk_gb: Optional[int] = None,
    ) -> Callable[[Any], ApiHandle]:
        """declare a load-balanced serverless endpoint.

        decorate a class with route markers for the native experience, or
        a zero-argument function returning an asgi app to serve an
        existing fastapi/starlette application:

            @app.api(name="inference", gpu=GpuType.NVIDIA_L4)
            class Inference:
                @init
                def setup(self): self.model = load()

                @post("/generate")
                async def generate(self, body: dict): ...

            @app.api(name="fast", cpu="cpu5c-2-4")
            def web():
                from fastapi import FastAPI
                server = FastAPI()
                ...
                return server
        """

        def decorator(target: Any) -> ApiHandle:
            spec = ResourceSpec(
                kind=ResourceKind.API,
                name=name or target.__name__,
                gpu=normalize_gpu(gpu),
                cpu=normalize_cpu(cpu),
                gpu_count=gpu_count,
                workers=normalize_workers(workers),
                idle_timeout=idle_timeout,
                dependencies=dependencies,
                system_dependencies=system_dependencies,
                volume=_volume_ref(volume),
                env=env,
                datacenter=_datacenter_list(datacenter),
                image=image,
                registry_auth=registry_auth,
                model=model,
                execution_timeout_ms=execution_timeout_ms,
                flashboot=flashboot,
                scaler_type=normalize_scaler_type(scaler_type),
                scaler_value=scaler_value,
                min_cuda_version=normalize_cuda_version(min_cuda_version),
                accelerate_downloads=accelerate_downloads,
                container_disk_gb=container_disk_gb,
            )
            handle = ApiHandle(self, target, spec)
            self._register(spec.name, handle)
            return handle

        return decorator

    # -- resolution --

    async def _resolve(self, spec: ResourceSpec) -> InvocationTarget:
        """resolve a resource spec to an invocation target.

        stateless by design: deployed resources resolve through the
        sentinel (server-side name resolution); dev sessions register
        live targets in memory; tasks provision per call.
        """
        if spec.kind is ResourceKind.TASK:
            handle = self._resources.get(spec.name)
            fn = getattr(handle, "_fn", None)
            # dev sessions attach an event sink for lifecycle rendering
            return PodTarget(spec, fn, events=self._dev_events)

        ctx = current_context()

        if ctx is Context.DEV:
            target = self._dev_targets.get(spec.name)
            if target is not None:
                return target
            # a dev session should have provisioned every endpoint at
            # startup; a miss means the resource was added mid-session
            raise EndpointNotFound(self.name, spec.name)

        if ctx is Context.WORKER and os.getenv("RUNPOD_DEV_APP") == self.name:
            # nested call inside a dev worker: siblings live on dev
            # endpoints (named dev-{app}-{resource}), not the sentinel
            return await self._resolve_dev_sibling(spec)

        env_name = os.getenv("FLASH_ENVIRONMENT") or self.env
        return SentinelTarget(self.name, env_name, spec.name)

    async def _resolve_dev_sibling(self, spec: ResourceSpec) -> InvocationTarget:
        """find the dev endpoint for a sibling resource by name."""
        from .targets import LiveTarget

        target = self._dev_targets.get(spec.name)
        if target is not None:
            return target

        from .api import AppsApiClient
        from .dev import dev_endpoint_name

        wanted = dev_endpoint_name(self.name, spec.name)
        endpoints = await AppsApiClient().list_my_endpoints()
        for endpoint in endpoints:
            if endpoint["name"] == wanted:
                target = LiveTarget(endpoint["id"], spec.name)
                self._dev_targets[spec.name] = target
                return target
        raise EndpointNotFound(self.name, spec.name)

    def __repr__(self) -> str:
        return f"<App {self.name!r} resources={len(self._resources)}>"


def _datacenter_list(
    datacenter: Optional[Union[str, List[str]]],
) -> Optional[List[str]]:
    """normalize datacenter input to a list of location strings."""
    if datacenter is None:
        return None
    if isinstance(datacenter, str):
        return [datacenter]
    return [str(d) for d in datacenter]


def _volume_ref(volume: Any) -> Optional[Any]:
    """validate a volume argument: a Volume, or a name/id string.

    Volume objects pass through whole so creation config (size,
    datacenter) survives to provision time.
    """
    if volume is None:
        return None
    from .volume import Volume

    if isinstance(volume, (str, Volume)):
        return volume
    raise InvalidResourceError(
        f"volume must be a runpod.Volume or a name/id string, "
        f"got {type(volume).__name__}"
    )
