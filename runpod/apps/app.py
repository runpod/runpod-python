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
    ResourceKind,
    ResourceSpec,
    normalize_cpu,
    normalize_gpu,
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
        env: Optional[Dict[str, str]] = None,
    ) -> Callable[[Callable], FunctionHandle]:
        """declare a queue-based serverless endpoint from a function."""

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
        env: Optional[Dict[str, str]] = None,
        image: Optional[str] = None,
    ) -> Callable[[Callable], FunctionHandle]:
        """declare ephemeral pod compute from a function.

        tasks have no standing infrastructure: `.remote()` provisions a
        pod, runs the body, returns the result, and terminates the pod.
        they never require `rp deploy` (except to register a schedule).
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
        env: Optional[Dict[str, str]] = None,
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
            return PodTarget(spec, fn)

        ctx = current_context()

        if ctx is Context.DEV:
            target = self._dev_targets.get(spec.name)
            if target is not None:
                return target
            # a dev session should have provisioned every endpoint at
            # startup; a miss means the resource was added mid-session
            raise EndpointNotFound(self.name, spec.name)

        env_name = os.getenv("FLASH_ENVIRONMENT") or self.env
        return SentinelTarget(self.name, env_name, spec.name)

    def __repr__(self) -> str:
        return f"<App {self.name!r} resources={len(self._resources)}>"


def _volume_ref(volume: Any) -> Optional[str]:
    """normalize a volume argument to a name-or-id string reference."""
    if volume is None:
        return None
    if isinstance(volume, str):
        return volume
    name = getattr(volume, "name", None) or getattr(volume, "id", None)
    if name is None:
        raise InvalidResourceError(
            f"volume must be a string name/id or an object with .name/.id, "
            f"got {type(volume).__name__}"
        )
    return str(name)
