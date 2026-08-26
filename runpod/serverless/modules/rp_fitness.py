"""
Fitness check system for worker startup validation.

Fitness checks run before handler initialization on the actual RunPod serverless
platform to validate the worker environment. Any check failure force-kills the
worker via os._exit(1), signaling unhealthy state to the container orchestrator.

Fitness checks do NOT run in local development mode or testing mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import sys
import time
import traceback
from collections.abc import Callable

from .rp_logger import RunPodLogger

log = RunPodLogger()


def _terminate_unhealthy(code: int = 1) -> None:
    """
    Force-kill the worker after a fitness check failure.

    Uses os._exit rather than sys.exit because a fitness failure means the
    environment is broken and the worker must die immediately so the
    orchestrator can restart it. sys.exit only raises SystemExit, which
    triggers cooperative interpreter shutdown and blocks joining non-daemon
    threads. Workers routinely have such threads alive by the time checks run
    (e.g. vLLM's AsyncLLMEngine, constructed at import before the checks), so
    sys.exit can hang forever and the worker keeps serving jobs. os._exit
    bypasses thread joins, atexit handlers, and asyncgen cleanup.

    Args:
        code: Process exit code (default 1, signaling unhealthy).
    """
    # Best-effort flush of buffered logs before the hard exit skips normal
    # cleanup. A broken worker may have a closed/None stdio stream; never let a
    # flush failure stop the exit, which is the whole point of this helper.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(code)

# Global registry for fitness check functions, preserves registration order
_fitness_checks: list[Callable] = []

# Checks that already passed. Checks run twice per worker -- at import and in
# run_worker -- so the second pass only runs what was registered in between.
_completed_checks: list[Callable] = []

# Disables every check, built-in and user-registered.
SKIP_FITNESS_CHECKS_ENV = "RUNPOD_SKIP_FITNESS_CHECKS"

# Keeps the checks but runs them only in run_worker, as before.
DEFER_FITNESS_CHECKS_ENV = "RUNPOD_DEFER_FITNESS_CHECKS"

# Set once this process has claimed the startup pass. Child processes spawned
# with multiprocessing 'spawn' (vLLM, DeepSpeed) re-import this module and
# inherit the environment; the marker tells them to skip the checks.
_CHECKS_DONE_ENV = "RUNPOD_FITNESS_CHECKS_DONE"


def _env_flag(name: str) -> bool:
    """True if the env var is set to a truthy value."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def defer_to_worker_start(func: Callable) -> Callable:
    """
    Mark a check as unsafe to run at import.

    The import-time pass skips these; they run in run_worker as before. Used
    for checks that initialize CUDA in this process -- doing that before the
    handler module runs would leave a CUDA context in a process the handler
    may later fork (vLLM, DeepSpeed), which CUDA does not support.
    """
    func._runpod_defer_to_worker_start = True
    return func


def _is_deferred(func: Callable) -> bool:
    return getattr(func, "_runpod_defer_to_worker_start", False)


def register_fitness_check(func: Callable) -> Callable:
    """
    Decorator to register a fitness check function.

    Fitness checks validate worker health at startup before handler initialization.
    If any check fails, the worker is force-killed with os._exit(1).

    Supports both sync and async functions (auto-detected via inspect.iscoroutinefunction()).

    Example:
        @runpod.serverless.register_fitness_check
        def check_gpu():
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError("GPU not available")

        @runpod.serverless.register_fitness_check
        async def check_model_files():
            import aiofiles.os
            if not await aiofiles.os.path.exists("/models/model.safetensors"):
                raise RuntimeError("Model file not found")

    Args:
        func: Function to register as fitness check. Can be sync or async.

    Returns:
        Original function unchanged (allows decorator stacking).
    """
    _fitness_checks.append(func)
    log.debug(f"Registered fitness check: {func.__name__}")
    return func


def clear_fitness_checks() -> None:
    """
    Clear all registered fitness checks.

    Used primarily for testing to reset global state between test cases.
    Not intended for production use.
    """
    _fitness_checks.clear()
    _completed_checks.clear()


_registration_state: dict[str, bool] = {
    "gpu_check": False,
    "system_checks": False,
}


def _reset_registration_state() -> None:
    """
    Reset global registration state.

    Used for testing to ensure clean state between tests.
    """
    _registration_state["gpu_check"] = False
    _registration_state["system_checks"] = False


# Bound how long the best-effort unhealthy report may delay the exit.
_REPORT_TIMEOUT_SECONDS = 2


def _report_unhealthy(check: str, reason: str) -> None:
    """
    Best-effort report of a fitness-check failure to the host before exit.

    Sends a single GET to the ping URL (same URL/credentials the heartbeat
    uses) with status=unhealthy plus the failing check name and reason, so the
    host can emit a queryable worker.fitness_failed event. Any failure — no
    ping URL, no API key, HTTP error, timeout — is swallowed, so this can never
    prevent the os._exit that follows. It is synchronous, so it may delay that
    exit by up to _REPORT_TIMEOUT_SECONDS (network phases only; it adds no
    delay when there is no ping URL/API key to report to).
    """
    ping_url = os.environ.get("RUNPOD_WEBHOOK_PING")
    api_key = os.environ.get("RUNPOD_AI_API_KEY")
    if not ping_url or ping_url == "PING_NOT_SET" or not api_key:
        return

    try:
        # Deferred imports: keep module import light and avoid import cycles.
        from runpod.http_client import SyncClientSession
        from runpod.serverless.modules.worker_state import WORKER_ID
        from runpod.version import __version__ as runpod_version

        ping_url = ping_url.replace("$RUNPOD_POD_ID", WORKER_ID)
        params = {
            "status": "unhealthy",
            "check": check,
            "reason": reason[:256],
            "runpod_version": runpod_version,
        }
        session = SyncClientSession()
        try:
            session.headers.update({"Authorization": api_key})
            session.get(ping_url, params=params, timeout=_REPORT_TIMEOUT_SECONDS)
        finally:
            session.close()
    except Exception:
        # Best-effort only; the exit is the guarantee, not this report.
        pass


def _ensure_gpu_check_registered() -> None:
    """
    Ensure GPU fitness check is registered.

    Deferred until first run to avoid circular import issues during module
    initialization. Called from run_fitness_checks() on first invocation.
    """
    if _registration_state["gpu_check"]:
        return

    # Latch only on success: a registration failure (e.g. a malformed
    # RUNPOD_GPU_TEST_TIMEOUT) must re-raise in run_worker, not silently
    # disable the checks in both passes.
    try:
        from .rp_gpu_fitness import auto_register_gpu_check
    except ImportError:
        log.debug("GPU fitness check module not found, skipping auto-registration")
        _registration_state["gpu_check"] = True
        return

    auto_register_gpu_check()
    _registration_state["gpu_check"] = True


def _ensure_system_checks_registered() -> None:
    """
    Ensure system resource fitness checks are registered.

    Deferred until first run to avoid circular import issues during module
    initialization. Called from run_fitness_checks() on first invocation.
    """
    if _registration_state["system_checks"]:
        return

    # Allow disabling system checks for testing
    if _env_flag("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS"):
        log.debug(
            "System fitness checks disabled via environment (RUNPOD_SKIP_AUTO_SYSTEM_CHECKS)"
        )
        _registration_state["system_checks"] = True
        return

    # Same latch-on-success rule as _ensure_gpu_check_registered.
    try:
        from .rp_system_fitness import auto_register_system_checks
    except ImportError:
        log.debug("System fitness check module not found, skipping auto-registration")
        _registration_state["system_checks"] = True
        return

    auto_register_system_checks()
    _registration_state["system_checks"] = True


async def run_fitness_checks(include_deferred: bool = True) -> None:
    """
    Execute all registered fitness checks sequentially at startup.

    Execution flow:
    1. Auto-register GPU check on first run (deferred to avoid circular imports)
    2. Check if registry is empty (early return if no checks)
    3. Log start of fitness check phase
    4. For each registered check:
       - Auto-detect sync vs async using inspect.iscoroutinefunction()
       - Execute check with timing instrumentation (await if async, call if sync)
       - Log success or failure with check name and execution time
    5. On any exception:
       - Log detailed error with check name, exception type, and message
       - Log traceback at DEBUG level
       - Force-kill the worker via os._exit(1) immediately (fail-fast). This is
         a hard exit, not a cooperative sys.exit/SystemExit: it does not unwind
         the stack or run cleanup, so callers cannot catch it and it cannot be
         blocked by live non-daemon threads.
    6. On successful completion of all checks:
       - Log completion message with total execution time

    Each check runs once per process: completed checks are skipped on later
    calls, and @defer_to_worker_start checks are skipped when include_deferred
    is False (the import-time pass).

    Note:
        Checks run in registration order (list preserves order).
        Sequential execution (not parallel) ensures clear error reporting
        and handles checks with dependencies correctly.
        Timing uses high-precision perf_counter for accurate measurements.

    Note:
        A failing check terminates the process via os._exit(1); this function
        does not return in that case and does not raise SystemExit.
    """
    if _env_flag(SKIP_FITNESS_CHECKS_ENV):
        log.info(f"Fitness checks disabled via {SKIP_FITNESS_CHECKS_ENV}, skipping.")
        return

    # Defer GPU check auto-registration until fitness checks are about to run
    # This avoids circular import issues during module initialization
    _ensure_gpu_check_registered()

    # Defer system check auto-registration until fitness checks are about to run
    _ensure_system_checks_registered()

    # Identity, not equality: two distinct registrations may compare equal
    # (e.g. fresh bound-method objects of one method), and `==` would skip one.
    pending = [
        check
        for check in _fitness_checks
        if not any(check is done for done in _completed_checks)
    ]

    if not include_deferred:
        pending = [check for check in pending if not _is_deferred(check)]

    if not pending:
        log.debug("No pending fitness checks, skipping.")
        return

    log.info(f"Running {len(pending)} fitness check(s)...")

    total_start_time = time.perf_counter()

    for check_func in pending:
        check_name = check_func.__name__

        try:
            log.debug(f"Executing fitness check: {check_name}")
            check_start_time = time.perf_counter()

            # Auto-detect async vs sync using inspect
            if inspect.iscoroutinefunction(check_func):
                await check_func()
            else:
                check_func()

            check_elapsed_ms = (time.perf_counter() - check_start_time) * 1000
            _completed_checks.append(check_func)
            log.debug(f"Fitness check passed: {check_name} ({check_elapsed_ms:.2f}ms)")

        except Exception as exc:
            # Log detailed error information
            error_type = type(exc).__name__
            error_message = str(exc)
            full_traceback = traceback.format_exc()

            log.error(
                f"Fitness check failed: {check_name} | {error_type}: {error_message}"
            )
            log.debug(f"Traceback:\n{full_traceback}")

            # Best-effort report to the host so the failure is queryable. It is
            # bounded (see _REPORT_TIMEOUT_SECONDS) and fully swallowed, so it
            # can delay the force-exit below but can never prevent it.
            try:
                _report_unhealthy(check_name, f"{error_type}: {error_message}")
            except Exception:  # a report failure must never prevent the exit
                pass

            # Force-kill immediately; see _terminate_unhealthy for why this is
            # os._exit rather than sys.exit.
            log.error("Worker is unhealthy, exiting.")
            _terminate_unhealthy(1)

    total_elapsed_ms = (time.perf_counter() - total_start_time) * 1000
    log.info(f"All fitness checks passed. ({total_elapsed_ms:.2f}ms)")


def _event_loop_running() -> bool:
    """True if called from inside a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def run_startup_fitness_checks() -> None:
    """
    Run the built-in fitness checks at import, before the handler loads a model.

    A user's @register_fitness_check functions are registered after this import,
    so they still run in run_worker, which skips whatever passed here. Checks
    marked with @defer_to_worker_start are also left to run_worker.

    No-ops outside a real worker (no RUNPOD_WEBHOOK_GET_JOB), when the checks
    are disabled or deferred, inside a running event loop, and in child
    processes (multiprocessing 'spawn' re-imports this module; the worker marks
    itself done via env so children skip). Ordinary exceptions from running the
    checks are logged and swallowed: a failure to run the checks must not stop
    a worker from booting. A failing check still force-exits, which is the point.
    """
    if _env_flag(SKIP_FITNESS_CHECKS_ENV) or _env_flag(DEFER_FITNESS_CHECKS_ENV):
        return

    if not os.environ.get("RUNPOD_WEBHOOK_GET_JOB"):
        return

    if os.environ.get(_CHECKS_DONE_ENV):
        return
    os.environ[_CHECKS_DONE_ENV] = "1"

    if _event_loop_running():
        log.debug("Event loop already running, deferring fitness checks to run_worker.")
        return

    try:
        # Own loop rather than asyncio.run: run() resets the thread's loop
        # policy state, after which asyncio.get_event_loop() in handler code
        # raises RuntimeError on Python 3.10+.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run_fitness_checks(include_deferred=False))
        finally:
            loop.close()
    except Exception as exc:  # pragma: no cover - defensive
        log.error(f"Startup fitness checks could not run: {exc}")
