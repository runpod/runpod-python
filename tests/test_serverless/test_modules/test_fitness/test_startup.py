"""Tests for fitness checks running at import/startup time (DR-1409)."""

import builtins
import os
import sys
import types
from unittest.mock import patch

import pytest

from runpod.serverless.modules import rp_fitness
from runpod.serverless.modules.rp_fitness import (
    register_fitness_check,
    run_fitness_checks,
    run_startup_fitness_checks,
)


@pytest.fixture()
def worker_env(monkeypatch):
    """Make the process look like a real Runpod worker."""
    monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "https://example.com/job")
    monkeypatch.delenv("RUNPOD_SKIP_FITNESS_CHECKS", raising=False)
    monkeypatch.delenv("RUNPOD_DEFER_FITNESS_CHECKS", raising=False)


class TestSkipEnvVar:
    @pytest.mark.asyncio
    async def test_skip_env_var_bypasses_all_checks(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_SKIP_FITNESS_CHECKS", "true")
        called = []

        @register_fitness_check
        def check():
            called.append(True)

        await run_fitness_checks()
        assert called == []

    @pytest.mark.asyncio
    async def test_checks_run_when_skip_unset(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_SKIP_FITNESS_CHECKS", raising=False)
        called = []

        @register_fitness_check
        def check():
            called.append(True)

        await run_fitness_checks()
        assert called == [True]


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_passed_check_does_not_rerun(self):
        calls = []

        @register_fitness_check
        def first():
            calls.append("first")

        await run_fitness_checks()

        @register_fitness_check
        def second():
            calls.append("second")

        await run_fitness_checks()

        assert calls == ["first", "second"]

    @pytest.mark.asyncio
    async def test_equal_but_distinct_registration_still_runs(self):
        # Bound-method objects are distinct but compare equal; an == check
        # against _completed_checks would wrongly skip the re-registration.
        calls = []

        class Checker:
            def check(self):
                calls.append("bound")

        obj = Checker()

        register_fitness_check(obj.check)
        await run_fitness_checks()

        register_fitness_check(obj.check)
        await run_fitness_checks()

        assert calls == ["bound", "bound"]


class TestStartupEntrypoint:
    def test_runs_checks_on_worker(self, worker_env):
        calls = []

        @register_fitness_check
        def check():
            calls.append(True)

        run_startup_fitness_checks()
        assert calls == [True]

    def test_noop_outside_worker(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_WEBHOOK_GET_JOB", raising=False)
        calls = []

        @register_fitness_check
        def check():
            calls.append(True)

        run_startup_fitness_checks()
        assert calls == []

    def test_defer_env_var_postpones_to_worker_start(self, worker_env, monkeypatch):
        monkeypatch.setenv("RUNPOD_DEFER_FITNESS_CHECKS", "true")
        calls = []

        @register_fitness_check
        def check():
            calls.append(True)

        run_startup_fitness_checks()
        assert calls == []

    def test_skip_env_var_respected(self, worker_env, monkeypatch):
        monkeypatch.setenv("RUNPOD_SKIP_FITNESS_CHECKS", "1")
        calls = []

        @register_fitness_check
        def check():
            calls.append(True)

        run_startup_fitness_checks()
        assert calls == []

    def test_unexpected_error_does_not_propagate(self, worker_env):
        # Patch loop construction, not loop execution: patching asyncio.run
        # would orphan the coroutine argument and trip unraisable warnings.
        with patch.object(
            rp_fitness.asyncio, "new_event_loop", side_effect=RuntimeError("boom")
        ):
            run_startup_fitness_checks()

    @pytest.mark.asyncio
    async def test_noop_inside_running_loop(self, worker_env):
        calls = []

        @register_fitness_check
        def check():
            calls.append(True)

        run_startup_fitness_checks()
        assert calls == []


class TestDeferredChecks:
    """Checks that touch CUDA in-process must not run at import time."""

    def test_deferred_check_skipped_at_import(self, worker_env):
        calls = []

        @register_fitness_check
        def early():
            calls.append("early")

        @register_fitness_check
        @rp_fitness.defer_to_worker_start
        def late():
            calls.append("late")

        run_startup_fitness_checks()
        assert calls == ["early"]

    @pytest.mark.asyncio
    async def test_deferred_check_runs_at_worker_start(self, worker_env):
        calls = []

        @register_fitness_check
        @rp_fitness.defer_to_worker_start
        def late():
            calls.append("late")

        run_startup_fitness_checks()
        assert calls == []

        await run_fitness_checks()
        assert calls == ["late"]

    def test_cuda_checks_are_marked_deferred(self):
        from runpod.serverless.modules import rp_system_fitness

        with patch.object(rp_system_fitness, "gpu_available", return_value=True):
            rp_system_fitness.auto_register_system_checks()

        by_name = {check.__name__: check for check in rp_fitness._fitness_checks}
        assert rp_fitness._is_deferred(by_name["_cuda_init_check"])
        assert rp_fitness._is_deferred(by_name["_benchmark_check"])
        assert not rp_fitness._is_deferred(by_name["_memory_check"])


class TestDoneMarker:
    """Spawned children re-import this module and must not re-run the checks."""

    def test_done_marker_skips_startup_pass(self, worker_env, monkeypatch):
        monkeypatch.setenv(rp_fitness._CHECKS_DONE_ENV, "1")
        calls = []

        @register_fitness_check
        def check():
            calls.append(True)

        run_startup_fitness_checks()
        assert calls == []

    def test_startup_pass_sets_done_marker(self, worker_env):
        run_startup_fitness_checks()
        assert os.environ.get(rp_fitness._CHECKS_DONE_ENV) == "1"


class TestDeferFullBehavior:
    """RUNPOD_DEFER_FITNESS_CHECKS restores exact pre-PR start()-only timing."""

    @pytest.mark.asyncio
    async def test_deferred_to_start_runs_everything(self, worker_env, monkeypatch):
        monkeypatch.setenv("RUNPOD_DEFER_FITNESS_CHECKS", "true")
        calls = []

        @register_fitness_check
        def check():
            calls.append(True)

        @register_fitness_check
        @rp_fitness.defer_to_worker_start
        def deferred():
            calls.append("deferred")

        run_startup_fitness_checks()
        assert calls == []

        await run_fitness_checks()
        assert calls == [True, "deferred"]


class TestAutoRegistrationPath:
    """Exercise the real _ensure_*_registered path during the startup pass."""

    def test_startup_runs_auto_registered_checks_without_torch(
        self, worker_env, monkeypatch
    ):
        calls = []

        fake_gpu_module = types.SimpleNamespace(
            auto_register_gpu_check=lambda: register_fitness_check(
                lambda: calls.append("gpu")
            )
        )

        def register_system_checks():
            register_fitness_check(lambda: calls.append("system"))
            register_fitness_check(
                rp_fitness.defer_to_worker_start(lambda: calls.append("deferred"))
            )

        fake_system_module = types.SimpleNamespace(
            auto_register_system_checks=register_system_checks
        )

        monkeypatch.delenv("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS", raising=False)
        monkeypatch.delenv("RUNPOD_SKIP_GPU_CHECK", raising=False)
        monkeypatch.setitem(
            sys.modules, "runpod.serverless.modules.rp_gpu_fitness", fake_gpu_module
        )
        monkeypatch.setitem(
            sys.modules,
            "runpod.serverless.modules.rp_system_fitness",
            fake_system_module,
        )

        real_import = builtins.__import__

        def guard_no_torch(name, *args, **kwargs):
            if name.split(".")[0] == "torch":
                raise AssertionError("torch imported during startup checks")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", guard_no_torch)

        run_startup_fitness_checks()

        assert calls == ["gpu", "system"]  # deferred check stays for run_worker


class TestImportWiring:
    """Deleting the wiring must fail a test, not just real workers."""

    def test_serverless_import_calls_startup_checks(self, monkeypatch):
        import importlib

        import runpod.serverless

        calls = []
        monkeypatch.setattr(
            rp_fitness, "run_startup_fitness_checks", lambda: calls.append(True)
        )

        importlib.reload(runpod.serverless)

        assert calls == [True]


class TestRegistrationLatch:
    """A malformed env value must fail loudly in run_worker, not fail open."""

    @pytest.mark.asyncio
    async def test_malformed_env_reraises_at_start(self, worker_env, monkeypatch):
        monkeypatch.delenv("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS", raising=False)
        monkeypatch.setenv("RUNPOD_MIN_MEMORY_GB", "not-a-number")
        # Drop the cached module so the env parse re-executes on import.
        monkeypatch.delitem(
            sys.modules, "runpod.serverless.modules.rp_system_fitness", raising=False
        )

        run_startup_fitness_checks()  # swallowed and logged — but not latched
        assert rp_fitness._registration_state["system_checks"] is False

        with pytest.raises(ValueError):
            await run_fitness_checks()
