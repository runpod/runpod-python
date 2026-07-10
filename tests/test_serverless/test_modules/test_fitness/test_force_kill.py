"""
Regression tests for SLS-379.

A failed fitness check must force-kill the worker even when non-daemon
background threads are alive (e.g. vLLM's AsyncLLMEngine, which is
constructed at import time before fitness checks run). ``sys.exit(1)`` only
raises ``SystemExit`` and then blocks in interpreter shutdown joining those
threads, so the worker logs "unhealthy, exiting." but never terminates.
The exit must go through ``os._exit`` to terminate unconditionally.

Also covers SLS-380: the best-effort unhealthy report the worker sends to the
host before force-exiting.
"""

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from runpod.serverless.modules import rp_fitness


# Child program mirrors worker.py: a non-daemon thread is alive (like vLLM's
# engine loop) when a failing fitness check triggers the unhealthy exit.
_CHILD_PROGRAM = """
import asyncio
import os
import threading
import time

os.environ["RUNPOD_SKIP_AUTO_SYSTEM_CHECKS"] = "true"
os.environ["RUNPOD_SKIP_GPU_CHECK"] = "true"

from runpod.serverless.modules.rp_fitness import (
    register_fitness_check,
    run_fitness_checks,
)


def _never_returns():
    while True:
        time.sleep(0.5)


@register_fitness_check
def _failing_check():
    raise RuntimeError("simulated fitness failure")


threading.Thread(target=_never_returns, daemon=False).start()
asyncio.run(run_fitness_checks())
"""


def test_unhealthy_exit_terminates_with_live_non_daemon_thread():
    """Worker must die (exit 1) within the timeout, not hang, when a
    non-daemon thread is alive at the time of the fitness failure."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_PROGRAM],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            "Worker hung instead of exiting on fitness failure "
            "(sys.exit could not join the live non-daemon thread)."
        ) from exc

    assert result.returncode == 1, (
        f"expected exit code 1, got {result.returncode}. "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Worker is unhealthy, exiting." in (result.stdout + result.stderr)


def test_terminate_unhealthy_uses_os_exit():
    """The unhealthy exit must call os._exit (unconditional) rather than
    sys.exit (cooperative)."""
    with patch("runpod.serverless.modules.rp_fitness.os._exit") as mock_exit:
        rp_fitness._terminate_unhealthy(1)

    mock_exit.assert_called_once_with(1)


def test_terminate_unhealthy_exits_even_if_flush_raises():
    """A closed/broken stdio stream must not stop the hard exit; a failing
    flush is swallowed so os._exit always runs."""
    with patch("runpod.serverless.modules.rp_fitness.os._exit") as mock_exit, \
        patch("sys.stdout") as mock_stdout, \
        patch("sys.stderr") as mock_stderr:
        mock_stdout.flush.side_effect = ValueError("I/O operation on closed file")
        mock_stderr.flush.side_effect = ValueError("I/O operation on closed file")

        rp_fitness._terminate_unhealthy(1)

    mock_exit.assert_called_once_with(1)


def test_report_unhealthy_posts_check_and_reason(monkeypatch):
    monkeypatch.setenv("RUNPOD_WEBHOOK_PING", "https://api.test/ping/$RUNPOD_POD_ID")
    monkeypatch.setenv("RUNPOD_AI_API_KEY", "key-123")

    fake_session = MagicMock()
    with patch("runpod.http_client.SyncClientSession", return_value=fake_session), \
        patch("runpod.serverless.modules.worker_state.WORKER_ID", "podABC"):
        rp_fitness._report_unhealthy("_cuda_init_check", "RuntimeError: boom")

    assert fake_session.get.call_count == 1
    call = fake_session.get.call_args
    url = call.args[0] if call.args else call.kwargs["url"]
    params = call.kwargs["params"]
    assert "podABC" in url  # $RUNPOD_POD_ID substituted
    assert params["status"] == "unhealthy"
    assert params["check"] == "_cuda_init_check"
    assert params["reason"] == "RuntimeError: boom"
    fake_session.headers.update.assert_called_once_with({"Authorization": "key-123"})


def test_report_unhealthy_skipped_without_ping_url(monkeypatch):
    monkeypatch.delenv("RUNPOD_WEBHOOK_PING", raising=False)
    monkeypatch.setenv("RUNPOD_AI_API_KEY", "key-123")
    with patch("runpod.http_client.SyncClientSession") as session_cls:
        rp_fitness._report_unhealthy("_memory_check", "RuntimeError: low")
    session_cls.assert_not_called()


def test_report_unhealthy_swallows_errors(monkeypatch):
    monkeypatch.setenv("RUNPOD_WEBHOOK_PING", "https://api.test/ping")
    monkeypatch.setenv("RUNPOD_AI_API_KEY", "key-123")
    fake_session = MagicMock()
    fake_session.get.side_effect = RuntimeError("network down")
    with patch("runpod.http_client.SyncClientSession", return_value=fake_session):
        rp_fitness._report_unhealthy("_disk_check", "RuntimeError: full")  # must not raise


@pytest.mark.asyncio
async def test_failure_reports_before_exit():
    from runpod.serverless.modules.rp_fitness import register_fitness_check, run_fitness_checks

    @register_fitness_check
    def _cuda_init_check():
        raise RuntimeError("device busy")

    with patch("runpod.serverless.modules.rp_fitness._report_unhealthy") as mock_report:
        with pytest.raises(SystemExit):
            await run_fitness_checks()

    mock_report.assert_called_once()
    args = mock_report.call_args.args
    assert args[0] == "_cuda_init_check"
    assert "RuntimeError" in args[1] and "device busy" in args[1]
