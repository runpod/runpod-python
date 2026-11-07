"""Debugger capture must not prevent valid handler results from being delivered."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, call

import pytest

from runpod.serverless.modules import rp_job


@pytest.fixture
def job_io(monkeypatch):
    """Replace result transport and the debugger's hardware-dependent probe."""
    send_result = AsyncMock()
    stream_result = AsyncMock()
    debugger_output = Mock(return_value={"checkpoints": []})
    clear_debugger = Mock()
    monkeypatch.setattr(rp_job, "send_result", send_result)
    monkeypatch.setattr(rp_job, "stream_result", stream_result)
    monkeypatch.setattr(rp_job.rp_debugger, "get_debugger_output", debugger_output)
    monkeypatch.setattr(rp_job.rp_debugger, "clear_debugger_output", clear_debugger)
    return send_result, stream_result, debugger_output, clear_debugger


def _config(handler):
    return {
        "handler": handler,
        "rp_args": {"rp_debugger": True},
        "reference_counter_start": rp_job.REF_COUNT_ZERO + 1,
    }


@pytest.mark.parametrize("output", ["text", ["first", "second"], None, True, False, 0])
def test_debugger_delivers_non_dictionary_output(job_io, output):
    """Keep the JSON result's type when it cannot hold debugger metadata."""
    send_result, stream_result, debugger_output, clear_debugger = job_io

    def handler(_job):
        return output

    session = object()
    job = {"id": "debugger-job", "input": {}}
    result = asyncio.run(rp_job.handle_job(session, _config(handler), job))

    assert result == {"output": output}
    assert type(result["output"]) is type(output)
    send_result.assert_awaited_once_with(session, result, job, is_stream=False)
    stream_result.assert_not_awaited()
    debugger_output.assert_not_called()
    clear_debugger.assert_called_once_with()


@pytest.mark.parametrize("raises", [False, True], ids=["custom-error", "raised-error"])
def test_debugger_delivers_error_without_output(job_io, raises):
    """Both documented error paths must reach result delivery."""
    send_result, stream_result, debugger_output, clear_debugger = job_io

    def handler(_job):
        if raises:
            raise RuntimeError("handler failed")
        return {"error": "handler failed"}

    session = object()
    job = {"id": "debugger-job", "input": {}}
    result = asyncio.run(rp_job.handle_job(session, _config(handler), job))

    if raises:
        assert json.loads(result["error"])["error_message"] == "handler failed"
    else:
        assert result == {"error": "handler failed"}
    assert "output" not in result
    send_result.assert_awaited_once_with(session, result, job, is_stream=False)
    stream_result.assert_not_awaited()
    debugger_output.assert_not_called()
    clear_debugger.assert_called_once_with()


def test_debugger_still_attaches_metadata_to_dictionary_output(job_io):
    """Dictionary results retain debugger data and its ready delay."""
    send_result, stream_result, debugger_output, clear_debugger = job_io

    def handler(_job):
        return {"answer": "ok"}

    session = object()
    job = {"id": "debugger-job", "input": {}}
    result = asyncio.run(rp_job.handle_job(session, _config(handler), job))

    assert result == {
        "output": {
            "answer": "ok",
            "rp_debugger": {"checkpoints": [], "ready_delay_ms": 1000},
        }
    }
    send_result.assert_awaited_once_with(session, result, job, is_stream=False)
    stream_result.assert_not_awaited()
    debugger_output.assert_called_once_with()
    clear_debugger.assert_not_called()


def test_debugger_delivers_aggregated_stream_output(job_io):
    """Aggregated generator output is a list and is delivered unchanged."""
    send_result, stream_result, debugger_output, clear_debugger = job_io

    def handler(_job):
        yield "first"
        yield "second"

    config = {**_config(handler), "return_aggregate_stream": True}
    session = object()
    job = {"id": "debugger-job", "input": {}}
    result = asyncio.run(rp_job.handle_job(session, config, job))

    assert result == {"output": ["first", "second"]}
    send_result.assert_awaited_once_with(session, result, job, is_stream=True)
    assert stream_result.await_args_list == [
        call(session, {"output": "first"}, job),
        call(session, {"output": "second"}, job),
    ]
    debugger_output.assert_not_called()
    clear_debugger.assert_called_once_with()
