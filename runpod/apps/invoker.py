"""sync-by-default invocation with an async escape hatch.

an Invoker wraps a coroutine factory. calling it blocks and returns the
result; calling `.aio(...)` returns the coroutine for the caller to await.

    handle.remote(x)            # sync, blocks
    await handle.remote.aio(x)  # async
"""

from typing import Any, Callable, Coroutine

from .context import block

CoroFactory = Callable[..., Coroutine[Any, Any, Any]]


class Invoker:
    __slots__ = ("_factory",)

    def __init__(self, factory: CoroFactory):
        self._factory = factory

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return block(self._factory(*args, **kwargs))

    def aio(self, *args: Any, **kwargs: Any) -> Coroutine[Any, Any, Any]:
        return self._factory(*args, **kwargs)
