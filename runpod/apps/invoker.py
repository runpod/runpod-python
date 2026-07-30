"""sync-by-default invocation with an async escape hatch.

an Invoker wraps a coroutine factory. calling it blocks and returns the
result; calling `.aio(...)` returns the coroutine for the caller to await.

    handle.remote(x)            # sync, blocks
    await handle.remote.aio(x)  # async

a StreamInvoker is the same idea for async generators: calling it
returns a sync iterator, `.aio(...)` returns the async iterator.
"""

from typing import Any, AsyncIterator, Callable, Coroutine, Iterator

from .context import block

CoroFactory = Callable[..., Coroutine[Any, Any, Any]]
AsyncGenFactory = Callable[..., AsyncIterator[Any]]


class Invoker:
    __slots__ = ("_factory",)

    def __init__(self, factory: CoroFactory):
        self._factory = factory

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return block(self._factory(*args, **kwargs))

    def aio(self, *args: Any, **kwargs: Any) -> Coroutine[Any, Any, Any]:
        return self._factory(*args, **kwargs)


class StreamInvoker:
    __slots__ = ("_factory",)

    def __init__(self, factory: AsyncGenFactory):
        self._factory = factory

    def __call__(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        agen = self._factory(*args, **kwargs)
        while True:
            try:
                yield block(agen.__anext__())
            except StopAsyncIteration:
                return

    def aio(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        return self._factory(*args, **kwargs)
