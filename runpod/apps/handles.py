"""handles returned by app decorators.

a handle replaces the decorated object and is the single way to interact
with the resource:

    @app.queue(name="transcribe", gpu=GpuType.NVIDIA_GEFORCE_RTX_4090)
    async def transcribe(audio_url: str): ...

    transcribe.remote("https://...")        # sync remote call
    await transcribe.remote.aio("https://...")
    transcribe.spawn("https://...")          # fire and forget -> job handle
    transcribe.local("https://...")          # run the body here

    @transcribe.init
    def load_model(): ...                    # worker startup hook
"""

import inspect
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Type

from .context import Context, current_context
from .invoker import Invoker
from .markers import INIT_ATTR, RESERVED_PATHS, is_init, route_of
from .schedule import SCHEDULE_ATTR
from .spec import ResourceKind, ResourceSpec, RouteSpec
from .errors import InvalidResourceError, RemoteExecutionError

if TYPE_CHECKING:
    from .app import App


class Job:
    """a submitted queue job, returned by .spawn()."""

    def __init__(self, data: Dict[str, Any], handle: "FunctionHandle"):
        self._data = data
        self._handle = handle

    @property
    def id(self) -> str:
        return self._data.get("id", "")

    @property
    def status(self) -> str:
        return self._data.get("status", "UNKNOWN")

    def __repr__(self) -> str:
        return f"Job(id={self.id!r}, status={self.status!r})"


class FunctionHandle:
    """handle for @app.queue and @app.task functions."""

    def __init__(
        self,
        app: "App",
        fn: Callable,
        spec: ResourceSpec,
    ):
        self._app = app
        self._fn = fn
        self.spec = spec
        self._init_fn: Optional[Callable] = None

        # adopt a schedule stamped by @schedule below the app decorator
        stamped = getattr(fn, SCHEDULE_ATTR, None)
        if stamped and not spec.schedule:
            spec.schedule = stamped

        self.remote = Invoker(self._remote_async)
        self.spawn = Invoker(self._spawn_async)

        self.__name__ = getattr(fn, "__name__", spec.name)
        self.__doc__ = getattr(fn, "__doc__", None)
        self.__wrapped__ = fn

    # -- lifecycle --

    def init(self, fn: Callable) -> Callable:
        """register a worker-startup hook; runs before the worker is ready,
        never locally."""
        setattr(fn, INIT_ATTR, True)
        self._init_fn = fn
        return fn

    # -- local execution --

    def local(self, *args: Any, **kwargs: Any) -> Any:
        """run the function body here. returns a coroutine iff the
        function is async."""
        return self._fn(*args, **kwargs)

    # -- remote execution --

    async def _remote_async(self, *args: Any, **kwargs: Any) -> Any:
        ctx = current_context()

        if ctx is Context.WORKER and self._is_current_worker():
            result = self._fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

        target = await self._app._resolve(self.spec)
        payload = target.build_payload(self._fn, self.spec, args, kwargs)
        return await target.invoke(payload)

    async def _spawn_async(self, *args: Any, **kwargs: Any) -> Any:
        target = await self._app._resolve(self.spec)
        payload = target.build_payload(self._fn, self.spec, args, kwargs)
        data = await target.submit(payload)
        # queue targets return raw job data; task targets return a TaskJob
        if isinstance(data, dict):
            return Job(data, self)
        return data

    def _is_current_worker(self) -> bool:
        import os

        current = os.getenv("FLASH_RESOURCE_NAME") or os.getenv(
            "RUNPOD_RESOURCE_NAME"
        )
        return current is not None and current == self.spec.name

    # calling the handle directly is a common mistake; be explicit

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError(
            f"'{self.__name__}' is a runpod {self.spec.kind.value} handle. "
            f"use .remote(...) to execute remotely, .local(...) to run here, "
            f"or .spawn(...) to fire and forget."
        )

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} {self.spec.kind.value}:{self.spec.name} "
            f"app={self._app.name!r}>"
        )


class _RouteCaller:
    """client-side http verb on an ApiHandle: Inference.post("/x", body)."""

    __slots__ = ("_handle", "_method")

    def __init__(self, handle: "ApiHandle", method: str):
        self._handle = handle
        self._method = method

    def __call__(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        from .context import block

        return block(self.aio(path, body, **kwargs))

    async def aio(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        target = await self._handle._app._resolve(self._handle.spec)
        return await target.request(self._method, path, body, **kwargs)


class ApiHandle:
    """handle for @app.api classes and asgi factories.

    route registration happens inside the decorated class via markers
    (@get/@post/...); the handle only ever makes client calls, so there is
    no decorator/client dual mode.
    """

    def __init__(self, app: "App", target: Any, spec: ResourceSpec):
        self._app = app
        self.spec = spec
        self._cls: Optional[Type] = None
        self._asgi_factory: Optional[Callable] = None
        self._init_name: Optional[str] = None

        if inspect.isclass(target):
            self._cls = target
            self._collect_routes(target)
        elif callable(target):
            self._asgi_factory = target
            spec.asgi_factory = getattr(target, "__qualname__", target.__name__)
        else:
            raise InvalidResourceError(
                "@app.api must decorate a class with route markers or a "
                "zero-argument function returning an asgi app"
            )

        self.get = _RouteCaller(self, "GET")
        self.post = _RouteCaller(self, "POST")
        self.put = _RouteCaller(self, "PUT")
        self.delete = _RouteCaller(self, "DELETE")
        self.patch = _RouteCaller(self, "PATCH")

        self.__name__ = getattr(target, "__name__", spec.name)
        self.__doc__ = getattr(target, "__doc__", None)
        self.__wrapped__ = target

    def _collect_routes(self, cls: Type) -> None:
        seen: Dict[tuple, str] = {}
        routes: List[RouteSpec] = []
        for name, member in inspect.getmembers(cls, callable):
            route = route_of(member)
            if route is not None:
                method, path = route
                if (method, path) in seen:
                    raise InvalidResourceError(
                        f"duplicate route {method} {path} on {cls.__name__}: "
                        f"'{seen[(method, path)]}' and '{name}'"
                    )
                seen[(method, path)] = name
                routes.append(RouteSpec(method=method, path=path, handler_name=name))
            if is_init(member):
                if self._init_name is not None and self._init_name != name:
                    raise InvalidResourceError(
                        f"multiple @init methods on {cls.__name__}: "
                        f"'{self._init_name}' and '{name}'"
                    )
                self._init_name = name
        if not routes:
            raise InvalidResourceError(
                f"@app.api class {cls.__name__} defines no routes; mark "
                f"methods with @get/@post/@put/@delete/@patch"
            )
        self.spec.routes = routes

    def __repr__(self) -> str:
        n_routes = len(self.spec.routes)
        return (
            f"<ApiHandle api:{self.spec.name} routes={n_routes} "
            f"app={self._app.name!r}>"
        )
