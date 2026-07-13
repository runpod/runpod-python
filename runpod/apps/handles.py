"""handles returned by app decorators.

a handle replaces the decorated object and is the single way to interact
with the resource:

    @app.queue(name="transcribe", gpu=GpuType.NVIDIA_GEFORCE_RTX_4090)
    async def transcribe(audio_url: str): ...

    transcribe.remote("https://...")        # sync remote call
    await transcribe.remote.aio("https://...")
    transcribe.spawn("https://...")          # fire and forget -> job handle
    transcribe.local("https://...")          # run the body here

    for chunk in generate.stream(prompt="hi"):   # generator functions
        ...

    @transcribe.init
    def load_model(): ...                    # worker startup hook
"""

import inspect
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    Dict,
    List,
    Optional,
    Type,
)

from .context import Context, current_context
from .discovery_state import DiscoveryInvocationError, in_discovery
from .errors import InvalidResourceError
from .invoker import Invoker, StreamInvoker
from .job import Job
from .markers import INIT_ATTR, is_init, route_of
from .schedule import SCHEDULE_ATTR
from .spec import ResourceKind, ResourceSpec, RouteSpec

if TYPE_CHECKING:
    from .app import App


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
        # per-job options merged into the run payload; set by with_options
        self._job_options: Dict[str, Any] = {}

        # adopt a schedule stamped by @schedule below the app decorator
        stamped = getattr(fn, SCHEDULE_ATTR, None)
        if stamped and not spec.schedule:
            spec.schedule = stamped

        self.remote = Invoker(self._remote_async)
        self.stream = StreamInvoker(self._stream_async)
        self.spawn = Invoker(self._spawn_async)
        self.job = Invoker(self._job_async)

        self.__name__ = getattr(fn, "__name__", spec.name)
        self.__doc__ = getattr(fn, "__doc__", None)
        self.__wrapped__ = fn

    def with_options(
        self,
        *,
        webhook: Optional[str] = None,
        execution_timeout: Optional[int] = None,
        ttl: Optional[int] = None,
        low_priority: Optional[bool] = None,
        s3_config: Optional[Dict[str, Any]] = None,
    ) -> "FunctionHandle":
        """bind per-job options for the next call, returning a new handle.

            transcribe.with_options(webhook="https://...").spawn(url)

        options apply per invocation and stack across chained calls;
        the original handle is untouched. queue only: tasks run on a
        dedicated pod and take no job payload options.
        """
        from .targets import build_job_options, merge_job_options

        if self.spec.kind is ResourceKind.TASK:
            raise InvalidResourceError(
                "per-job options apply only to @app.queue functions; "
                "tasks run on a dedicated pod"
            )
        new_options = build_job_options(
            webhook, execution_timeout, ttl, low_priority, s3_config
        )
        clone = FunctionHandle(self._app, self._fn, self.spec)
        clone._init_fn = self._init_fn
        clone._job_options = merge_job_options(self._job_options, new_options)
        return clone

    def init(self, fn: Callable) -> Callable:
        """register a worker-startup hook; runs before the worker is ready,
        never locally."""
        setattr(fn, INIT_ATTR, True)
        self._init_fn = fn
        return fn

    def local(self, *args: Any, **kwargs: Any) -> Any:
        """run the function body here. returns a coroutine iff the
        function is async."""
        return self._fn(*args, **kwargs)

    async def _remote_async(self, *args: Any, **kwargs: Any) -> Any:
        self._guard_discovery()
        ctx = current_context()

        if ctx is Context.WORKER and self._is_current_worker():
            result = self._fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            # generators aggregate, matching the deployed worker's
            # return_aggregate_stream output for .remote()
            if inspect.isasyncgen(result):
                return [chunk async for chunk in result]
            if inspect.isgenerator(result):
                return list(result)
            return result

        target = await self._app._resolve(self.spec)
        payload = target.build_payload(self._fn, self.spec, args, kwargs)
        return await target.invoke(self._apply_options(payload))

    async def _stream_async(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        """invoke a generator function remotely, yielding partial outputs
        as the worker produces them."""
        self._guard_discovery()
        self._guard_generator()
        ctx = current_context()

        if ctx is Context.WORKER and self._is_current_worker():
            gen = self._fn(*args, **kwargs)
            if inspect.isasyncgen(gen):
                async for chunk in gen:
                    yield chunk
            else:
                for chunk in gen:
                    yield chunk
            return

        target = await self._app._resolve(self.spec)
        payload = target.build_payload(self._fn, self.spec, args, kwargs)
        data = await target.submit(self._apply_options(payload))
        async for chunk in target.stream_job(data["id"]):
            yield chunk

    def _guard_generator(self) -> None:
        if self.spec.kind is ResourceKind.TASK:
            raise InvalidResourceError(
                "tasks do not stream; use @app.queue for generator functions"
            )
        if not (
            inspect.isgeneratorfunction(self._fn)
            or inspect.isasyncgenfunction(self._fn)
        ):
            raise InvalidResourceError(
                f"'{self.__name__}' is not a generator function; "
                f"use .remote(...) instead"
            )

    def _apply_options(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._job_options:
            return payload
        return {**payload, **self._job_options}

    async def _spawn_async(self, *args: Any, **kwargs: Any) -> Any:
        self._guard_discovery()
        target = await self._app._resolve(self.spec)
        payload = target.build_payload(self._fn, self.spec, args, kwargs)
        data = await target.submit(self._apply_options(payload))
        # queue targets return raw job data; task targets return a TaskJob
        if isinstance(data, dict):
            return Job(data, target)
        return data

    async def _job_async(self, job_id: str) -> Job:
        """reconnect to a submitted queue job by id."""
        if self.spec.kind is ResourceKind.TASK:
            raise InvalidResourceError(
                "task jobs cannot be reconnected by id; retain the job "
                "returned by .spawn()"
            )
        target = await self._app._resolve(self.spec)
        return Job({"id": job_id, "status": "UNKNOWN"}, target)

    def _guard_discovery(self) -> None:
        if in_discovery():
            raise DiscoveryInvocationError(self.spec.name)

    def _is_current_worker(self) -> bool:
        import os

        current = os.getenv("FLASH_RESOURCE_NAME") or os.getenv("RUNPOD_RESOURCE_NAME")
        return current is not None and current == self.spec.name

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
        if in_discovery():
            raise DiscoveryInvocationError(self._handle.spec.name)
        target = await self._handle._app._resolve(self._handle.spec)
        # live targets need the module source to materialize the api
        # server-side; other targets ignore the reference
        setter = getattr(target, "attach_source", None)
        if setter is not None:
            setter(
                self._handle.__wrapped__,
                self._handle.spec.name,
                self._handle.spec,
            )
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
