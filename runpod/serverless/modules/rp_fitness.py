"""
Fitness check system for worker startup validation.

Fitness checks run before handler initialization on the actual RunPod serverless
platform to validate the worker environment. Any check failure force-kills the
worker via os._exit(1), signaling unhealthy state to the container orchestrator.

Fitness checks do NOT run in local development mode or testing mode.
"""

from __future__ import annotations

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
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)

# Global registry for fitness check functions, preserves registration order
_fitness_checks: list[Callable] = []


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


def _ensure_gpu_check_registered() -> None:
    """
    Ensure GPU fitness check is registered.

    Deferred until first run to avoid circular import issues during module
    initialization. Called from run_fitness_checks() on first invocation.
    """
    if _registration_state["gpu_check"]:
        return

    _registration_state["gpu_check"] = True

    try:
        from .rp_gpu_fitness import auto_register_gpu_check

        auto_register_gpu_check()
    except ImportError:
        log.debug("GPU fitness check module not found, skipping auto-registration")


def _ensure_system_checks_registered() -> None:
    """
    Ensure system resource fitness checks are registered.

    Deferred until first run to avoid circular import issues during module
    initialization. Called from run_fitness_checks() on first invocation.
    """
    import os

    if _registration_state["system_checks"]:
        return

    # Allow disabling system checks for testing
    if os.environ.get("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS", "").lower() == "true":
        log.debug(
            "System fitness checks disabled via environment (RUNPOD_SKIP_AUTO_SYSTEM_CHECKS)"
        )
        _registration_state["system_checks"] = True
        return

    _registration_state["system_checks"] = True

    try:
        from .rp_system_fitness import auto_register_system_checks

        auto_register_system_checks()
    except ImportError:
        log.debug("System fitness check module not found, skipping auto-registration")


async def run_fitness_checks() -> None:
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

    Note:
        Checks run in registration order (list preserves order).
        Sequential execution (not parallel) ensures clear error reporting
        and handles checks with dependencies correctly.
        Timing uses high-precision perf_counter for accurate measurements.

    Note:
        A failing check terminates the process via os._exit(1); this function
        does not return in that case and does not raise SystemExit.
    """
    # Defer GPU check auto-registration until fitness checks are about to run
    # This avoids circular import issues during module initialization
    _ensure_gpu_check_registered()

    # Defer system check auto-registration until fitness checks are about to run
    _ensure_system_checks_registered()

    if not _fitness_checks:
        log.debug("No fitness checks registered, skipping.")
        return

    log.info(f"Running {len(_fitness_checks)} fitness check(s)...")

    total_start_time = time.perf_counter()

    for check_func in _fitness_checks:
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

            # Force-kill immediately; see _terminate_unhealthy for why this is
            # os._exit rather than sys.exit.
            log.error("Worker is unhealthy, exiting.")
            _terminate_unhealthy(1)

    total_elapsed_ms = (time.perf_counter() - total_start_time) * 1000
    log.info(f"All fitness checks passed. ({total_elapsed_ms:.2f}ms)")
