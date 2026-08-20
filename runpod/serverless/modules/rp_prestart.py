"""
Prestart hook registration and execution for supported Serverless modes.

Hooks run sequentially before handler execution. Queue workers may acquire
requests concurrently while the handler gate stays closed. Local input and
single-worker hosted API modes finish prestart before invoking their handler
or serving HTTP. Sync hooks run on an abandonable daemon thread; async hooks
run on the active mode's event loop.
"""

import asyncio
import contextlib
import contextvars
import inspect
import json
import threading
import traceback
from collections.abc import Callable, Sequence
from typing import Any

from runpod.serverless.modules.rp_capture import (
    MAX_CAPTURED_CHARS,
    capture,
    clip,
    paused,
)
from runpod.serverless.modules.rp_logger import RunPodLogger
from runpod.serverless.modules.worker_state import WORKER_ID
from runpod.version import __version__ as runpod_version

log = RunPodLogger()

PRESTART_FAILED_EVENT = "prestart_failed"

_prestart_hooks: list[Callable[[], Any]] = []


def _progress(message: str) -> None:
    """Log SDK progress without it landing in the worker's captured output."""
    with paused():
        log.info(message)


def _hook_name(hook: Callable[[], Any] | None) -> str:
    if hook is None:
        return "unknown"
    return getattr(hook, "__name__", type(hook).__name__)


def register_prestart_hook(hook: Callable[[], Any]) -> Callable[[], Any]:
    """Register a sync or async hook to run before handler execution."""
    _prestart_hooks.append(hook)
    log.debug(f"Registered prestart hook: {_hook_name(hook)}")
    return hook


def get_prestart_hooks() -> tuple[Callable[[], Any], ...]:
    """Return an immutable snapshot of hooks in registration order."""
    return tuple(_prestart_hooks)


def has_prestart_hooks() -> bool:
    """Return whether any prestart hooks are registered."""
    return bool(_prestart_hooks)


def clear_prestart_hooks() -> None:
    """Clear registered hooks. Intended for test isolation."""
    _prestart_hooks.clear()


class PrestartTimeout(Exception):
    """Raised when the complete prestart phase exceeds `prestart_timeout`."""

    def __init__(self, hook: str, timeout: float):
        self.hook = hook
        super().__init__(
            f"prestart hook '{hook}' exceeded prestart_timeout of {timeout}s"
        )


class PrestartError(Exception):
    """Wrap an exception raised by a prestart hook."""

    def __init__(self, original: BaseException, hook: str):
        self.original = original
        self.hook = hook
        super().__init__(str(original))


def build_prestart_failed_payload(exc: BaseException, logs: str = "") -> dict[str, Any]:
    """Build the structured `prestart_failed` reason sent through `/job-done`."""
    original = getattr(exc, "original", exc)
    payload = {
        "event": PRESTART_FAILED_EVENT,
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
    if hook := getattr(exc, "hook", None):
        payload["hook"] = hook
    if logs:
        payload["logs"] = logs[-MAX_CAPTURED_CHARS:]
    return payload


async def _run_sync_in_daemon(hook: Callable[[], Any]) -> Any:
    """Run a blocking hook without creating an executor thread that blocks exit."""
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    results: list[Any] = []
    errors: list[BaseException] = []
    ctx = contextvars.copy_context()

    def worker():
        try:
            results.append(ctx.run(hook))
        except BaseException as exc:  # noqa: BLE001 - transferred to the event loop
            errors.append(exc)
        finally:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(done.set)

    threading.Thread(target=worker, name="rp-prestart", daemon=True).start()
    await done.wait()
    if errors:
        raise errors[0]
    return results[0] if results else None


async def _invoke_hook(hook: Callable[[], Any]) -> None:
    if inspect.iscoroutinefunction(hook) or inspect.iscoroutinefunction(hook.__call__):
        result = hook()
    else:
        result = await _run_sync_in_daemon(hook)

    if inspect.isawaitable(result):
        await result


async def _invoke_hook_preserving_external_cancellation(
    hook: Callable[[], Any],
) -> None:
    """Treat hook-raised cancellation as failure, but pass through cancellation
    from a timeout or worker shutdown."""

    async def invoke() -> None:
        try:
            await _invoke_hook(hook)
        except asyncio.CancelledError:
            raise
        except SystemExit as exc:
            raise PrestartError(exc, _hook_name(hook)) from exc
        except Exception as exc:
            raise PrestartError(exc, _hook_name(hook)) from exc

    hook_task = asyncio.create_task(invoke())
    try:
        await asyncio.shield(hook_task)
    except asyncio.CancelledError as exc:
        if hook_task.cancelled():
            raise PrestartError(exc, _hook_name(hook)) from exc

        # The caller cancelled the prestart task.
        # Now cancel and drain the child task before propagating shutdown.
        hook_task.cancel()
        with contextlib.suppress(BaseException):
            await hook_task
        raise


async def run_prestart_hooks_async(
    hooks: Sequence[Callable[[], Any]], timeout: float | None = None
) -> None:
    """Run hooks in order under one optional deadline."""
    current_hook: Callable[[], Any] | None = None

    async def run_all() -> None:
        nonlocal current_hook
        for current_hook in hooks:
            _progress(f"Prestart | running hook: {_hook_name(current_hook)}")
            await _invoke_hook_preserving_external_cancellation(current_hook)

    _progress("Prestart | phase started")
    if timeout is None:
        await run_all()
    else:
        try:
            await asyncio.wait_for(run_all(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise PrestartTimeout(_hook_name(current_hook), timeout) from exc
    _progress("Prestart | ready")


async def run_prestart_phase(
    hooks: Sequence[Callable[[], Any]], timeout: float | None = None
) -> dict[str, Any] | None:
    """Run the whole prestart phase and report a failure once.

    Returns the `prestart_failed` payload, or None when every hook succeeded or
    no hooks are registered. Every mode shares this so the failure contract
    lives in one place; each mode decides on its own what to do with the
    payload, because their obligations differ (a queue worker must fail the
    requests it already holds before exiting).
    """
    if not hooks:
        return None

    with capture() as captured:
        try:
            await run_prestart_hooks_async(hooks, timeout)
            return None
        except (PrestartError, PrestartTimeout) as exc:
            payload = build_prestart_failed_payload(exc, captured.getvalue())

    log.error(f"prestart_failed | {json.dumps(payload)}")
    return payload
