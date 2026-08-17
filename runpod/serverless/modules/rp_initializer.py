"""
runpod | serverless | initializer

Runs the user's startup initialization code concurrently with the job loop.
The loop may take a request right away, but the handler is not called until the initializer
finishes. On failure or timeout, the error + stdout/stderr are attached to
the current request.

A sync/blocking initializer is offloaded to a worker thread so it does not starve the
loop; an async one is awaited directly.
"""

import asyncio
import contextlib
import contextvars
import inspect
import threading
import traceback
from collections.abc import Callable
from typing import Any

from runpod.serverless.modules.rp_capture import MAX_CAPTURED_CHARS, clip
from runpod.serverless.modules.rp_logger import RunPodLogger
from runpod.serverless.modules.worker_state import WORKER_ID
from runpod.version import __version__ as runpod_version

log = RunPodLogger()

INIT_FAILED_EVENT = "init_failed"


class InitializerTimeout(Exception):
    """Raised when the initializer exceeds `init_timeout`."""


class InitializerError(Exception):
    """Wraps any exception raised by the user's initializer."""

    def __init__(self, original: BaseException):
        self.original = original
        super().__init__(str(original))


def build_init_failed_payload(exc: BaseException, logs: str = "") -> dict[str, Any]:
    """Failure reason as a structured dict, using the same core fields as a handler error
    (type, message, traceback). `logs` contains stdout/stderr."""
    original = getattr(exc, "original", exc)
    payload = {
        "event": INIT_FAILED_EVENT,
        "error_type": type(original).__name__,
        "error_message": clip(str(original)),
        "error_traceback": clip(
            "".join(
                traceback.format_exception(
                    type(original), original, original.__traceback__
                )
            )
        ),
        "worker_id": WORKER_ID,
        "runpod_version": runpod_version,
    }
    if logs:
        payload["logs"] = logs[-MAX_CAPTURED_CHARS:]
    return payload


async def _run_sync_in_daemon(fn: Callable) -> Any:
    """Run a blocking callable on a daemon thread instead of `asyncio.to_thread` so if stuck,
    it can be abandoned and die without blocking executor shutdown or process exit."""
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    results: list[Any] = []
    errors: list[BaseException] = []
    ctx = contextvars.copy_context()

    def worker():
        try:
            results.append(ctx.run(fn))
        except Exception as exc:  # noqa: BLE001 - transferred to the event loop below
            errors.append(exc)
        except (
            KeyboardInterrupt,
            SystemExit,
            GeneratorExit,
            asyncio.CancelledError,
        ) as exc:
            errors.append(exc)
        finally:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(done.set)

    threading.Thread(target=worker, name="rp-initializer", daemon=True).start()
    await done.wait()
    if errors:
        raise errors[0]
    return results[0] if results else None


async def _invoke_initializer(initializer: Callable) -> None:
    if inspect.iscoroutinefunction(initializer) or inspect.iscoroutinefunction(
        initializer.__call__
    ):
        result = initializer()
    else:
        result = await _run_sync_in_daemon(initializer)

    if inspect.isawaitable(result):
        await result


async def run_initializer_async(
    initializer: Callable, timeout: int | None = None
) -> None:
    """Run the initializer to completion inside the running event loop, raising
    `InitializerTimeout` on timeout or `InitializerError` for any other failure."""
    log.info("Initializer | init started")
    try:
        awaitable = _invoke_initializer(initializer)
        if timeout is not None:
            await asyncio.wait_for(awaitable, timeout=timeout)
        else:
            await awaitable
    except asyncio.TimeoutError as exc:
        raise InitializerTimeout(
            f"initializer exceeded init_timeout of {timeout}s"
        ) from exc
    except (InitializerError, InitializerTimeout):
        raise
    except SystemExit as exc:
        raise InitializerError(exc) from exc
    except Exception as exc:
        raise InitializerError(exc) from exc
    log.info("Initializer | ready")
