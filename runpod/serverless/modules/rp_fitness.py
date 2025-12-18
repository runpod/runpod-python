"""
Fitness check system for worker startup validation.

Fitness checks run before handler initialization on the actual RunPod serverless
platform to validate the worker environment. Any check failure causes immediate
exit with sys.exit(1), signaling unhealthy state to the container orchestrator.

Fitness checks do NOT run in local development mode or testing mode.
"""

import asyncio
import inspect
import sys
import traceback
from typing import Callable, List

from .rp_logger import RunPodLogger

log = RunPodLogger()

# Global registry for fitness check functions, preserves registration order
_fitness_checks: List[Callable] = []


def register_fitness_check(func: Callable) -> Callable:
    """
    Decorator to register a fitness check function.

    Fitness checks validate worker health at startup before handler initialization.
    If any check fails, the worker exits with sys.exit(1).

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


_gpu_check_registered = False


def _ensure_gpu_check_registered() -> None:
    """
    Ensure GPU fitness check is registered.

    Deferred until first run to avoid circular import issues during module
    initialization. Called from run_fitness_checks() on first invocation.
    """
    global _gpu_check_registered

    if _gpu_check_registered:
        return

    _gpu_check_registered = True

    try:
        from .rp_gpu_fitness import auto_register_gpu_check

        auto_register_gpu_check()
    except ImportError:
        # GPU fitness module not available
        log.debug("GPU fitness check module not found, skipping auto-registration")
    except Exception as e:
        # Don't fail fitness checks if auto-registration has issues
        log.warning(f"Failed to auto-register GPU fitness check: {e}")


async def run_fitness_checks() -> None:
    """
    Execute all registered fitness checks sequentially at startup.

    Execution flow:
    1. Auto-register GPU check on first run (deferred to avoid circular imports)
    2. Check if registry is empty (early return if no checks)
    3. Log start of fitness check phase
    4. For each registered check:
       - Auto-detect sync vs async using inspect.iscoroutinefunction()
       - Execute check (await if async, call if sync)
       - Log success or failure with check name
    5. On any exception:
       - Log detailed error with check name, exception type, and message
       - Log traceback at DEBUG level
       - Call sys.exit(1) immediately (fail-fast)
    6. On successful completion of all checks:
       - Log completion message

    Note:
        Checks run in registration order (list preserves order).
        Sequential execution (not parallel) ensures clear error reporting
        and handles checks with dependencies correctly.

    Raises:
        SystemExit: Calls sys.exit(1) if any check fails.
    """
    # Defer GPU check auto-registration until fitness checks are about to run
    # This avoids circular import issues during module initialization
    _ensure_gpu_check_registered()

    if not _fitness_checks:
        log.debug("No fitness checks registered, skipping.")
        return

    log.info(f"Running {len(_fitness_checks)} fitness check(s)...")

    for check_func in _fitness_checks:
        check_name = check_func.__name__

        try:
            log.debug(f"Executing fitness check: {check_name}")

            # Auto-detect async vs sync using inspect
            if inspect.iscoroutinefunction(check_func):
                await check_func()
            else:
                check_func()

            log.debug(f"Fitness check passed: {check_name}")

        except Exception as exc:
            # Log detailed error information
            error_type = type(exc).__name__
            error_message = str(exc)
            full_traceback = traceback.format_exc()

            log.error(
                f"Fitness check failed: {check_name} | "
                f"{error_type}: {error_message}"
            )
            log.debug(f"Traceback:\n{full_traceback}")

            # Exit immediately with failure code
            log.error("Worker is unhealthy, exiting.")
            sys.exit(1)

    log.info("All fitness checks passed.")
