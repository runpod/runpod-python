"""execution context detection and the sync/async bridge."""

import asyncio
import os
import threading
from enum import Enum
from typing import Any, Coroutine


class Context(Enum):
    """where the current process is running."""

    LOCAL = "local"
    """dev machine, plain `python main.py`."""

    DEV = "dev"
    """inside an `rp dev` session (ephemeral live provisioning)."""

    WORKER = "worker"
    """inside a runpod serverless endpoint or pod."""


def current_context() -> Context:
    """detect the execution context from the environment."""
    if os.getenv("RUNPOD_ENDPOINT_ID") or os.getenv("RUNPOD_POD_ID"):
        return Context.WORKER
    if os.getenv("RUNPOD_DEV_SESSION"):
        return Context.DEV
    return Context.LOCAL


def is_local() -> bool:
    """true when not running inside a runpod container.

    usable as a module-level guard so code only runs on the dev machine:

        if runpod.is_local():
            print("running or imported locally")
    """
    return current_context() is not Context.WORKER


class _LoopThread:
    """a dedicated background event loop for driving async engine code
    from synchronous callers.

    this makes `.remote()` safe to call both from plain sync code and from
    inside an already-running event loop (where `asyncio.run` would raise).
    """

    _loop: "asyncio.AbstractEventLoop | None" = None
    _lock = threading.Lock()

    @classmethod
    def _ensure_loop(cls) -> asyncio.AbstractEventLoop:
        with cls._lock:
            if cls._loop is None or cls._loop.is_closed():
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=loop.run_forever,
                    name="runpod-apps-loop",
                    daemon=True,
                )
                thread.start()
                cls._loop = loop
        return cls._loop

    @classmethod
    def run(cls, coro: Coroutine[Any, Any, Any]) -> Any:
        loop = cls._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            while True:
                try:
                    return future.result(timeout=0.2)
                except TimeoutError:
                    if future.done():
                        raise
        except BaseException:
            future.cancel()
            raise


def block(coro: Coroutine[Any, Any, Any]) -> Any:
    """run a coroutine to completion from a synchronous caller."""
    return _LoopThread.run(coro)
