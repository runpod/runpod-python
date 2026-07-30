"""route and lifecycle markers for @app.api classes.

these decorators stamp metadata on methods; the ApiHandle collects them
when the class is registered. they do not wrap or change the function.

    @app.api(name="inference", gpu=GpuType.NVIDIA_GEFORCE_RTX_4090)
    class Inference:
        @init
        def setup(self):
            self.model = load_model()

        @post("/generate")
        async def generate(self, body: dict):
            return {"text": self.model.run(body["prompt"])}
"""

from typing import Any, Callable

ROUTE_ATTR = "__runpod_route__"
INIT_ATTR = "__runpod_init__"

_VALID_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})

# paths used by the worker runtime; user routes must not collide
RESERVED_PATHS = frozenset({"/execute", "/ping"})


def _route_marker(method: str, path: str) -> Callable[[Callable], Callable]:
    if method not in _VALID_METHODS:
        raise ValueError(f"method must be one of {sorted(_VALID_METHODS)}")
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/', got: {path!r}")
    if path in RESERVED_PATHS:
        raise ValueError(
            f"path {path!r} is reserved by the worker runtime "
            f"(reserved: {', '.join(sorted(RESERVED_PATHS))})"
        )

    def marker(fn: Callable) -> Callable:
        setattr(fn, ROUTE_ATTR, (method, path))
        return fn

    return marker


def get(path: str) -> Callable[[Callable], Callable]:
    """mark a method as a GET route."""
    return _route_marker("GET", path)


def post(path: str) -> Callable[[Callable], Callable]:
    """mark a method as a POST route."""
    return _route_marker("POST", path)


def put(path: str) -> Callable[[Callable], Callable]:
    """mark a method as a PUT route."""
    return _route_marker("PUT", path)


def delete(path: str) -> Callable[[Callable], Callable]:
    """mark a method as a DELETE route."""
    return _route_marker("DELETE", path)


def patch(path: str) -> Callable[[Callable], Callable]:
    """mark a method as a PATCH route."""
    return _route_marker("PATCH", path)


def init(fn: Callable) -> Callable:
    """mark a method (or function) as a worker-startup hook.

    on an @app.api class, the marked method runs after instantiation and
    before the worker reports healthy. on a queue/task handle, use
    `@handle.init` instead.
    """
    setattr(fn, INIT_ATTR, True)
    return fn


def route_of(fn: Any) -> "tuple[str, str] | None":
    """return (method, path) if fn is marked as a route."""
    return getattr(fn, ROUTE_ATTR, None)


def is_init(fn: Any) -> bool:
    """true if fn is marked as an init hook."""
    return getattr(fn, INIT_ATTR, False) is True
