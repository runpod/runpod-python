"""Shared fixtures for fitness check tests."""

import pytest

from runpod.serverless.modules import rp_fitness
from runpod.serverless.modules.rp_fitness import (
    clear_fitness_checks,
    _reset_registration_state,
)


@pytest.fixture(autouse=True)
def cleanup_fitness_checks(monkeypatch):
    """Automatically clean up fitness checks before and after each test.

    Disables auto-registration of system checks to avoid interference
    with fitness check framework tests.

    Also neutralizes the unhealthy force-kill: in production a failed check
    calls os._exit, which would kill the pytest process itself. Redirect it
    to raise SystemExit(1) so tests can assert exit behavior in-process.
    Tests that need the real os._exit patch it themselves.
    """
    monkeypatch.setenv("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS", "true")
    monkeypatch.setenv("RUNPOD_SKIP_GPU_CHECK", "true")

    def _raise_system_exit(code=0):
        raise SystemExit(code)

    monkeypatch.setattr(rp_fitness.os, "_exit", _raise_system_exit)

    _reset_registration_state()
    clear_fitness_checks()
    yield
    _reset_registration_state()
    clear_fitness_checks()
