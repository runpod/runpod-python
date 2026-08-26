"""Tests for fitness checks running at import/startup time (DR-1409)."""

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
        with patch.object(rp_fitness.asyncio, "run", side_effect=RuntimeError("boom")):
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
