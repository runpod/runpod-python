"""Event renderer failures must not interrupt lifecycle operations."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from runpod.apps.manage import undeploy_environment
from runpod.apps.utils.events import emit


def broken_sink(event):
    """A lazily initialized renderer whose callback property fails to load."""

    def load_handler(self):
        raise RuntimeError("renderer unavailable")

    return type("BrokenRenderer", (), {event: property(load_handler)})()


def test_lookup_failure_is_logged_and_isolated(caplog):
    with caplog.at_level("DEBUG", logger="runpod.apps.utils.events"):
        emit(broken_sink("phase"), "phase", "build")
    assert "event sink phase failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["cleanup_started", "deleting", "deleted"])
async def test_lookup_failure_does_not_abort_undeploy(event):
    api = AsyncMock()
    api.get_environment_by_name.return_value = {
        "id": "environment",
        "endpoints": [{"id": "endpoint", "name": "worker"}],
    }
    result = await undeploy_environment(
        "app", "default", api=api, events=broken_sink(event)
    )
    api.delete_endpoint.assert_awaited_once_with("endpoint")
    api.delete_environment.assert_awaited_once_with("environment")
    assert result.endpoints_deleted == 1
    assert result.environment_deleted
    assert result.failures == []


def test_handler_failure_remains_isolated():
    def handler(*args):
        raise RuntimeError("renderer failed")

    emit(SimpleNamespace(phase=handler), "phase", "build")


def test_handler_receives_arguments():
    calls = []
    emit(SimpleNamespace(phase=lambda *args: calls.append(args)), "phase", "build", 2)
    assert calls == [("build", 2)]


@pytest.mark.parametrize("sink", [None, object()])
def test_missing_handler_is_ignored(sink):
    emit(sink, "phase", "build")


def test_process_interrupt_is_not_swallowed():
    def handler():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        emit(SimpleNamespace(phase=handler), "phase")
