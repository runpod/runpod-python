"""execution context detection and the sync/async bridge."""

import asyncio
import concurrent.futures
import logging
import os
import threading
import time
from enum import Enum
from typing import Any, Coroutine

log = logging.getLogger(__name__)
CLEANUP_TIMEOUT = 30.0
BRIDGE_CLEANUP_TIMEOUT = CLEANUP_TIMEOUT + 5.0


class Context(Enum):
    """where the current process is running."""

    LOCAL = "local"
    """dev machine, plain `python main.py`."""

    DEV = "dev"
    """inside an `rp flash dev` session (ephemeral live provisioning)."""

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
        completed = threading.Event()
        interrupted = threading.Event()
        running = []

        async def run():
            running.append(asyncio.current_task())
            try:
                if interrupted.is_set():
                    coro.close()
                    raise asyncio.CancelledError()
                return await coro
            finally:
                completed.set()

        def cancel():
            interrupted.set()
            if running:
                running[0].cancel()

        future = asyncio.run_coroutine_threadsafe(run(), loop)
        try:
            while True:
                try:
                    return future.result(timeout=0.2)
                except concurrent.futures.TimeoutError:
                    if future.done():
                        raise
        except BaseException:
            loop.call_soon_threadsafe(cancel)
            deadline = time.monotonic() + BRIDGE_CLEANUP_TIMEOUT
            while not completed.is_set():
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        log.warning("interrupted operation cleanup is still pending")
                        break
                    completed.wait(min(remaining, 0.2))
                except BaseException:
                    continue
            raise


def block(coro: Coroutine[Any, Any, Any]) -> Any:
    """run a coroutine to completion from a synchronous caller."""
    return _LoopThread.run(coro)
