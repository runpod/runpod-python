"""Shared fixtures for fitness check tests."""

import pytest

from runpod.serverless.modules.rp_fitness import (
    clear_fitness_checks,
    _reset_registration_state,
)


@pytest.fixture(autouse=True)
def cleanup_fitness_checks(monkeypatch):
    """Automatically clean up fitness checks before and after each test.

    Disables auto-registration of system checks to avoid interference
    with fitness check framework tests.
    """
    monkeypatch.setenv("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS", "true")
    _reset_registration_state()
    clear_fitness_checks()
    yield
    _reset_registration_state()
    clear_fitness_checks()
