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
import hashlib
import json
import os
import sys
import time
import traceback
from collections.abc import Callable

from runpod._logger import RunPodLogger
from . import is_early_check_eligible
from .coordination import ContainerChecks, CoordinationUnavailable, CoordinationBusy

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

# Tuning vars consumed when the checks run. Snapshotted at the import-time
# pass so a later pass can warn about post-import changes, which would
# otherwise be silently ignored.
_CONFIG_ENV_VARS = (
    "RUNPOD_MIN_MEMORY_GB",
    "RUNPOD_MIN_DISK_PERCENT",
    "RUNPOD_MIN_CUDA_VERSION",
    "RUNPOD_NETWORK_CHECK_TIMEOUT",
    "RUNPOD_GPU_BENCHMARK_TIMEOUT",
    "RUNPOD_GPU_TEST_TIMEOUT",
    "RUNPOD_GPU_MAX_ERROR_MESSAGES",
    "RUNPOD_BINARY_GPU_TEST_PATH",
    "RUNPOD_SKIP_AUTO_SYSTEM_CHECKS",
    "RUNPOD_SKIP_GPU_CHECK",
)

_CHECK_CONFIG_DEPENDENCIES = {
    "_memory_check": {"RUNPOD_MIN_MEMORY_GB"},
    "_disk_check": {"RUNPOD_MIN_DISK_PERCENT"},
    "_cuda_version_check": {"RUNPOD_MIN_CUDA_VERSION"},
    "_network_check": {"RUNPOD_NETWORK_CHECK_TIMEOUT"},
    "_benchmark_check": {"RUNPOD_GPU_BENCHMARK_TIMEOUT"},
    "_gpu_health_check": {
        "RUNPOD_GPU_TEST_TIMEOUT",
        "RUNPOD_GPU_MAX_ERROR_MESSAGES",
        "RUNPOD_BINARY_GPU_TEST_PATH",
    },
}

_config_snapshot: dict[str, str | None] = {}


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
        from requests import Session
        from runpod.version import __version__ as runpod_version

        worker_id = os.environ.get("RUNPOD_POD_ID")
        if "$RUNPOD_POD_ID" in ping_url and not worker_id:
            return
        ping_url = ping_url.replace("$RUNPOD_POD_ID", worker_id or "")
        params = {
            "status": "unhealthy",
            "check": check,
            "reason": reason[:256],
            "runpod_version": runpod_version,
        }
        session = Session()
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
    from .gpu import auto_register_gpu_check

    before = len(_fitness_checks)
    auto_register_gpu_check()
    for check in _fitness_checks[before:]:
        check._runpod_builtin = "gpu_check"
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
    from .system import auto_register_system_checks

    before = len(_fitness_checks)
    auto_register_system_checks()
    for check in _fitness_checks[before:]:
        check._runpod_builtin = "system_checks"
    _registration_state["system_checks"] = True


def _register_builtins() -> None:
    """Register atomically: failed setup must not leave duplicate/partial checks."""
    before = list(_fitness_checks)
    state = dict(_registration_state)
    try:
        _ensure_gpu_check_registered()
        _ensure_system_checks_registered()
    except Exception:
        _fitness_checks[:] = before
        _registration_state.update(state)
        raise


def _refresh_late_config() -> None:
    """Apply changed settings and rerun only checks whose inputs changed."""
    changed = {
        name for name, old in _config_snapshot.items() if os.environ.get(name) != old
    }
    if not changed:
        return
    log.warn(
        "Fitness check config changed since early checks; applying at worker start: "
        + ", ".join(sorted(changed))
    )
    # Runtime tuning lives in the standalone check modules, not frozen imports.
    if not _env_flag("RUNPOD_SKIP_GPU_CHECK"):
        from . import gpu

        gpu.configure()
    if not _env_flag("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS"):
        from . import system

        system.configure()
    _completed_checks[:] = [
        check
        for check in _completed_checks
        if not (
            getattr(check, "_runpod_builtin", False)
            and _CHECK_CONFIG_DEPENDENCIES.get(check.__name__, set()) & changed
        )
    ]
    for flag, group in (
        ("RUNPOD_SKIP_GPU_CHECK", "gpu_check"),
        ("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS", "system_checks"),
    ):
        if flag not in changed:
            continue
        _fitness_checks[:] = [
            check
            for check in _fitness_checks
            if getattr(check, "_runpod_builtin", None) != group
        ]
        _completed_checks[:] = [
            check
            for check in _completed_checks
            if getattr(check, "_runpod_builtin", None) != group
        ]
        _registration_state[group] = False
    _config_snapshot.update({v: os.environ.get(v) for v in _CONFIG_ENV_VARS})


def _fail_worker(check_name: str, exc: Exception) -> None:
    """Report a check/setup failure, then exit even if reporting or logging fails."""
    try:
        reason = f"{type(exc).__name__}: {exc}"
        with contextlib.suppress(Exception):
            log.error(f"Fitness check failed: {check_name} | {reason}")
            log.debug(f"Traceback: {traceback.format_exc()}")
        with contextlib.suppress(Exception):
            _report_unhealthy(check_name, reason)
        with contextlib.suppress(Exception):
            log.error("Worker is unhealthy, exiting.")
    finally:
        _terminate_unhealthy(1)


def _is_shared_check(check: Callable) -> bool:
    return bool(getattr(check, "_runpod_builtin", False)) and not _is_deferred(check)


def _shared_check_key(check: Callable) -> str:
    """Identify a built-in result by SDK version and relevant configuration."""
    from runpod.version import __version__

    settings = {
        name: os.environ.get(name)
        for name in _CHECK_CONFIG_DEPENDENCIES.get(check.__name__, ())
    }
    identity = [__version__, check._runpod_builtin, check.__name__, settings]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


async def _invoke_check(check: Callable) -> None:
    if inspect.iscoroutinefunction(check):
        await check()
    else:
        check()


async def _run_and_save_shared_check(check: Callable, shared: ContainerChecks) -> None:
    """Save failures before exiting; save successes only after execution."""
    key = _shared_check_key(check)
    if key in shared.state["passed"]:
        return
    try:
        await _invoke_check(check)
    except Exception as exc:
        shared.state["failure"] = f"{check.__name__}: {type(exc).__name__}"
        try:
            shared.save()
        finally:
            _fail_worker(check.__name__, exc)
        return
    shared.state["passed"].append(key)
    shared.save()


def _coordination_wait_seconds() -> float:
    """Cover sequential shared probes plus scheduling and result-write overhead."""
    budget = 5.0
    if not _env_flag("RUNPOD_SKIP_GPU_CHECK"):
        from . import gpu

        budget += gpu.TIMEOUT_SECONDS + gpu.FALLBACK_TIMEOUT_SECONDS
    if not _env_flag("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS"):
        from . import system

        # CUDA version probes nvcc, then nvidia-smi if nvcc fails.
        budget += 2 * system.CUDA_VERSION_PROBE_TIMEOUT
    return max(35.0, budget)


async def _run_shared_checks(include_deferred: bool) -> None:
    """Reuse container checks across imports, including independent helpers."""
    try:
        timeout = _coordination_wait_seconds() if include_deferred else 35.0
        async with ContainerChecks(timeout=timeout) as shared:
            if shared.state.get("failure"):
                _fail_worker(
                    "early_container_check", RuntimeError(shared.state["failure"])
                )
                return
            for check in filter(_is_shared_check, _fitness_checks):
                await _run_and_save_shared_check(check, shared)
                if not any(check is done for done in _completed_checks):
                    _completed_checks.append(check)
    except CoordinationUnavailable as exc:
        log.warn(
            f"Early check coordination unavailable; using worker-start checks: {exc}"
        )
    except CoordinationBusy as exc:
        if include_deferred:
            _fail_worker("fitness_check_coordination", exc)
            return
        log.warn(
            "Early checks still running in another process; deferring to worker start."
        )
    except OSError as exc:
        log.warn(f"Cannot save shared checks; using worker-start checks: {exc}")


async def run_fitness_checks(include_deferred: bool = True) -> None:
    """Validate startup health before accepting jobs.

    Shared built-ins reuse container results; process-specific and customer
    checks run only in the final pass. Successful registrations are tracked by
    identity so repeated calls skip them unless their configuration changes.

    Failed checks report unhealthy and force-exit, even with live threads.
    Setup/coordination unavailability during import defers to worker start.
    """
    if _env_flag(SKIP_FITNESS_CHECKS_ENV):
        log.info(f"Fitness checks disabled via {SKIP_FITNESS_CHECKS_ENV}, skipping.")
        return

    try:
        if include_deferred and _config_snapshot:
            _refresh_late_config()
        _register_builtins()
    except Exception as exc:
        if not include_deferred:
            log.error(
                f"Fitness checks could not be prepared; retrying at worker start: {exc}"
            )
            return
        _fail_worker("fitness_check_setup", exc)
        return

    if is_early_check_eligible() and (
        include_deferred or not _env_flag(DEFER_FITNESS_CHECKS_ENV)
    ):
        await _run_shared_checks(include_deferred)
        if not include_deferred:
            return

    # Identity, not equality: two distinct registrations may compare equal
    # (e.g. fresh bound-method objects of one method), and `==` would skip one.
    pending = [
        check
        for check in _fitness_checks
        if not any(check is done for done in _completed_checks)
    ]

    if not include_deferred:
        pending = [check for check in pending if _is_shared_check(check)]

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

            await _invoke_check(check_func)

            check_elapsed_ms = (time.perf_counter() - check_start_time) * 1000
            _completed_checks.append(check_func)
            log.debug(f"Fitness check passed: {check_name} ({check_elapsed_ms:.2f}ms)")

        except Exception as exc:
            _fail_worker(check_name, exc)
            return

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

    Shared built-ins run once per container startup. Child processes reuse the
    result; process-specific and customer checks wait for worker start.
    """
    if _env_flag(SKIP_FITNESS_CHECKS_ENV) or _env_flag(DEFER_FITNESS_CHECKS_ENV):
        return

    if not is_early_check_eligible():
        return

    if _event_loop_running():
        log.debug("Event loop already running, deferring fitness checks to run_worker.")
        return

    # Remember the tuning values as consumed, so a later pass can warn about
    # post-import changes (set in the handler, too late to apply).
    _config_snapshot.update({v: os.environ.get(v) for v in _CONFIG_ENV_VARS})

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
