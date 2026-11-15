"""json normalization and response safety shared with worker runtimes."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from runpod._serialization import normalize_json, validate_json
from runpod.apps.errors import RemoteExecutionError
from runpod.apps.protocol import FunctionRequest, FunctionResponse
from runpod.apps.targets import LiveTarget
from runpod.apps.tasks import unwrap_task_response
from runpod.serverless.modules import rp_fastapi, rp_http, rp_job


@pytest.mark.parametrize(
    "value,expected",
    [
        ((1, 2), [1, 2]),
        ({1: (None, True)}, {"1": [None, True]}),
        ({None: False}, {"null": False}),
        (None, None),
    ],
)
def test_shared_json_conversion_matches_wire(value, expected):
    validate_json(value)
    assert normalize_json(value) == expected


@pytest.mark.parametrize(
    "value",
    [b"bytes", datetime(2026, 1, 1), object(), float("nan"), float("inf"), {1, 2}],
)
def test_shared_validation_rejects_non_json_values(value):
    with pytest.raises(TypeError, match="json-serializable"):
        validate_json(value)
    with pytest.raises(TypeError, match="json-serializable"):
        normalize_json(value)


def test_shared_validation_rejects_cycles():
    value = []
    value.append(value)
    with pytest.raises(TypeError, match="(?i)circular reference"):
        validate_json(value)


def test_function_request_defaults_to_json_and_normalizes_values():
    request = FunctionRequest(function_name="echo", args=[(1, 2)])
    assert request.to_input()["serialization_format"] == "json"
    assert FunctionRequest.from_input(request.to_input()).args == [[1, 2]]


@pytest.mark.parametrize("fmt", ["cloudpickle", "typo", None, False])
def test_request_rejects_unsupported_formats(fmt):
    with pytest.raises(ValueError, match="serialization_format"):
        FunctionRequest.from_input(
            {"function_name": "echo", "serialization_format": fmt}
        )


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}, (1, 2), {1: "key"}])
def test_task_and_live_responses_share_json_conversion(value):
    response = {"success": True, "json_result": value}
    expected = normalize_json(value)
    assert unwrap_task_response(response) == expected
    assert LiveTarget("ep")._unwrap_response(response) == expected


@pytest.mark.parametrize(
    "response",
    [
        {"success": True},
        {"success": True, "result": "encoded-pickle"},
        {"success": True, "result": "encoded-pickle", "json_result": 42},
        {"success": True, "serialization_format": "cloudpickle", "json_result": 42},
        {"success": "true", "json_result": 42},
    ],
)
def test_task_and_live_reject_invalid_response_envelopes(response):
    for decode in (unwrap_task_response, LiveTarget("ep")._unwrap_response):
        with pytest.raises(RemoteExecutionError, match="invalid remote response"):
            decode(response)


def test_response_requires_boolean_success():
    with pytest.raises(TypeError, match="boolean success"):
        FunctionResponse.from_output({"json_result": 1})


@pytest.mark.parametrize("value", [b"bytes", datetime(2026, 1, 1), float("nan")])
async def test_serverless_reports_non_json_results(value):
    result = await rp_job.run_job(lambda job: value, {"id": "test", "input": {}})
    error = json.loads(result["error"])
    assert "json-serializable" in error["error_message"]
    assert "output" not in result


async def test_serverless_rejects_input_before_handler():
    called = []
    result = await rp_job.run_job(
        lambda job: called.append(job), {"id": "test", "input": {"x": float("nan")}}
    )
    assert not called
    assert "job input" in json.loads(result["error"])["error_message"]


@pytest.mark.parametrize("asynchronous", [True, False])
async def test_serverless_stream_reports_invalid_chunk(asynchronous):
    async def async_stream(job):
        yield 1
        yield b"bytes"

    def sync_stream(job):
        yield 1
        yield b"bytes"

    handler = async_stream if asynchronous else sync_stream
    chunks = [item async for item in rp_job.run_job_generator(handler, {"id": "test"})]
    assert chunks[0] == {"output": 1}
    assert "json-serializable" in chunks[1]["error"]
    assert len(chunks) == 2


@pytest.mark.parametrize("stream", [True, False])
async def test_http_serialization_failure_sends_terminal_error(monkeypatch, stream):
    transmit = AsyncMock()
    monkeypatch.setattr(rp_http, "_transmit", transmit)
    session = SimpleNamespace(headers={})
    send = rp_http.stream_result if stream else rp_http.send_result
    await send(session, {"output": b"bytes"}, {"id": "test"})
    transmit.assert_awaited_once()
    _, url, body = transmit.await_args.args
    assert url == rp_http.JOB_DONE_URL.replace("$ID", "test") + "&isStream=false"
    error = json.loads(json.loads(body)["error"])
    assert error["error_type"] == "SerializationError"
    assert "json-serializable" in error["error_message"]


@pytest.mark.parametrize("route", ["runsync", "status", "stream"])
async def test_local_api_reports_invalid_stream_chunk(monkeypatch, route):
    def handler(job):
        yield "first"
        yield datetime(2026, 1, 1)

    removed = []
    monkeypatch.setattr(
        rp_fastapi,
        "job_list",
        SimpleNamespace(
            get=lambda job_id: SimpleNamespace(id=job_id, input={}, webhook=None),
            remove=removed.append,
        ),
    )
    worker = object.__new__(rp_fastapi.WorkerAPI)
    worker.config = {"handler": handler}
    argument = rp_fastapi.DefaultRequest(input={}) if route == "runsync" else "test"
    result = await getattr(worker, f"_sim_{route}")(argument)
    assert result["status"] == "FAILED"
    assert "json-serializable" in result["error"]
    assert "output" not in result
    if route != "runsync":
        assert removed == ["test"]


@pytest.mark.parametrize("value", [None, {}, {"items": []}])
async def test_local_api_preserves_json_results(value):
    worker = object.__new__(rp_fastapi.WorkerAPI)
    worker.config = {"handler": lambda job: value}
    result = await worker._sim_runsync(rp_fastapi.DefaultRequest(input={}))
    assert result["status"] == "COMPLETED"
    assert result["output"] == value


@pytest.mark.parametrize("value", [None, {}, [], False, 0, ""])
async def test_serverless_preserves_empty_json_results(value):
    result = await rp_job.run_job(lambda job: value, {"id": "test", "input": {}})
    assert result == {"output": value}


@pytest.mark.parametrize("route", ["runsync", "status/test"])
@pytest.mark.parametrize("value", [None, {}])
def test_local_http_preserves_empty_json_results(monkeypatch, route, value):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(rp_fastapi.Heartbeat, "start_ping", lambda self, mirror: None)
    monkeypatch.setattr(rp_fastapi.rp_capture, "install", lambda **kwargs: None)
    monkeypatch.setattr(
        rp_fastapi,
        "job_list",
        SimpleNamespace(
            set_mirror=lambda mirror: None,
            get=lambda job_id: SimpleNamespace(id=job_id, input={}, webhook=None),
            remove=lambda job_id: None,
        ),
    )
    worker = rp_fastapi.WorkerAPI({"handler": lambda job: value})
    client = TestClient(worker.rp_app)
    try:
        response = client.post(f"/{route}", json={"input": {}})
    finally:
        client.close()
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert "output" in response.json()
    assert response.json()["output"] == value


@pytest.mark.parametrize("stream", [True, False])
async def test_deployed_job_sends_serialization_failure(monkeypatch, stream):
    def generator(job):
        yield "first"
        yield b"bytes"

    send = AsyncMock()
    send_chunk = AsyncMock()
    monkeypatch.setattr(rp_job, "send_result", send)
    monkeypatch.setattr(rp_job, "stream_result", send_chunk)
    handler = generator if stream else lambda job: datetime(2026, 1, 1)
    await rp_job.handle_job(
        SimpleNamespace(headers={}),
        {"handler": handler, "return_aggregate_stream": True},
        {"id": "test", "input": {}},
    )
    send.assert_awaited_once()
    result = send.await_args.args[1]
    assert "json-serializable" in result["error"]
    assert "output" not in result
    assert send_chunk.await_count == (1 if stream else 0)
