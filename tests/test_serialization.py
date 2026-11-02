"""shared json contracts for queues, tasks, and serverless workers."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from runpod._serialization import validate_json
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.errors import RemoteExecutionError
from runpod.apps.protocol import FunctionRequest, FunctionResponse
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.targets import LiveTarget, PodTarget, SentinelTarget
from runpod.apps.tasks import unwrap_task_response
from runpod.serverless.modules import rp_fastapi, rp_http, rp_job


def identity(value):
    return value


@pytest.mark.parametrize(
    "value", [None, True, False, 0, -1, 1.25, "hello", [], {}, {"x": [None, 2]}]
)
def test_json_values_roundtrip_without_coercion(value):
    validate_json(value)
    assert json.loads(json.dumps(value, allow_nan=False)) == value


@pytest.mark.parametrize(
    "value",
    [
        b"bytes",
        (1, 2),
        {1: "key"},
        {True: "key"},
        {1, 2},
        object(),
        datetime(2026, 1, 1),
        float("nan"),
        float("inf"),
        -float("inf"),
    ],
)
def test_non_json_values_are_rejected(value):
    with pytest.raises(TypeError, match="json-serializable"):
        validate_json({"nested": [value]})


def test_cycles_are_rejected_but_shared_values_are_valid():
    value = []
    validate_json([value, value])
    value.append(value)
    with pytest.raises(TypeError, match="circular reference"):
        validate_json(value)


def test_nested_error_identifies_path_without_printing_value():
    with pytest.raises(TypeError, match=r"arguments\['items'\]\[0\]") as error:
        validate_json({"items": [b"secret-content"]}, "arguments")
    assert "secret-content" not in str(error.value)


def test_function_request_defaults_to_json():
    request = FunctionRequest(function_name="identity", args=[None], kwargs={})
    payload = request.to_input()
    assert payload["serialization_format"] == "json"
    assert FunctionRequest.from_input(payload) == request


@pytest.mark.parametrize("fmt", ["cloudpickle", "typo", "", None, False])
def test_request_rejects_unsupported_formats(fmt):
    with pytest.raises(ValueError, match="serialization_format"):
        FunctionRequest.from_input(
            {"function_name": "identity", "serialization_format": fmt}
        )


@pytest.mark.parametrize("args,kwargs", [(None, {}), ({}, {}), ([], []), ([], None)])
def test_request_rejects_invalid_argument_containers(args, kwargs):
    with pytest.raises(TypeError, match="args must be a list"):
        FunctionRequest.from_input(
            {"function_name": "identity", "args": args, "kwargs": kwargs}
        )


def test_request_revalidates_mutations_before_encoding():
    request = FunctionRequest(function_name="identity")
    request.args.append(b"bytes")
    with pytest.raises(TypeError, match="json-serializable"):
        request.to_input()


@pytest.mark.parametrize("kind", ["live", "deployed", "task"])
@pytest.mark.parametrize("value", [b"bytes", (1, 2), {1: "key"}, float("nan")])
def test_all_targets_reject_non_json_arguments(kind, value, monkeypatch):
    spec = ResourceSpec(
        kind=ResourceKind.TASK if kind == "task" else ResourceKind.QUEUE,
        name="identity",
    )
    target = {
        "live": LiveTarget("ep"),
        "deployed": SentinelTarget("a", "e", "q"),
        "task": PodTarget(spec, identity),
    }[kind]
    monkeypatch.setattr(
        "runpod.apps.serialization.get_function_source",
        lambda fn: "def identity(value): return value",
    )
    with pytest.raises(TypeError, match="identity.*json-serializable"):
        target.build_payload(identity, spec, (value,), {})


def test_task_and_live_requests_share_json_payloads(monkeypatch):
    spec = ResourceSpec(kind=ResourceKind.TASK, name="identity")
    monkeypatch.setattr(
        "runpod.apps.serialization.get_function_source",
        lambda fn: "def identity(value): return value",
    )
    args = ({"items": [1, None]},)
    task = PodTarget(spec, identity).build_payload(identity, spec, args, {})
    live = LiveTarget("ep").build_payload(identity, spec, args, {})["input"]
    assert task == live
    assert task["args"] == list(args)
    assert task["serialization_format"] == "json"


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}, {"x": [1, 2]}])
def test_task_and_live_decode_identically(value):
    response = {"success": True, "json_result": value}
    assert unwrap_task_response(response) == value
    assert LiveTarget("ep")._unwrap_response(response) == value


@pytest.mark.parametrize(
    "response",
    [
        {"success": True},
        {"success": True, "result": "encoded-pickle"},
        {"success": True, "result": "encoded-pickle", "json_result": 42},
        {"success": True, "serialization_format": "cloudpickle", "json_result": 42},
        {"success": True, "json_result": float("nan")},
        {"success": "true", "json_result": 42},
    ],
)
def test_task_and_live_reject_invalid_envelopes(response):
    for decode in (unwrap_task_response, LiveTarget("ep")._unwrap_response):
        with pytest.raises(RemoteExecutionError, match="invalid remote response"):
            decode(response)


def test_response_requires_boolean_success():
    with pytest.raises(TypeError, match="boolean success"):
        FunctionResponse.from_output({"json_result": 1})


@pytest.fixture
def app(monkeypatch):
    _clear_registry()
    for variable in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
        monkeypatch.delenv(variable, raising=False)
    yield App("json-contract")
    _clear_registry()


@pytest.mark.parametrize("kind", ["queue", "task"])
async def test_invalid_arguments_fail_before_resource_resolution(app, kind):
    handle = getattr(app, kind)(name="identity")(identity)
    resolve = AsyncMock()
    app._resolve = resolve
    for call in (handle.remote.aio, handle.spawn.aio):
        with pytest.raises(TypeError, match="json-serializable"):
            await call(b"bytes")
    resolve.assert_not_called()


@pytest.mark.parametrize("kind", ["queue", "task"])
def test_same_worker_remote_obeys_json_but_local_is_python(app, kind, monkeypatch):
    handle = getattr(app, kind)(name="identity")(identity)
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep")
    monkeypatch.setattr(handle, "_is_current_worker", lambda: True)
    with pytest.raises(TypeError, match="json-serializable"):
        handle.remote(b"bytes")
    assert handle.local(b"bytes") == b"bytes"
    assert handle.remote({"x": [1, None]}) == {"x": [1, None]}


@pytest.mark.parametrize("asynchronous", [True, False])
async def test_same_worker_validates_results(app, monkeypatch, asynchronous):
    async def async_value():
        return b"bytes"

    def sync_value():
        return b"bytes"

    handle = app.queue(name="value")(async_value if asynchronous else sync_value)
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep")
    monkeypatch.setattr(handle, "_is_current_worker", lambda: True)
    with pytest.raises(TypeError, match="return value.*json-serializable"):
        await handle.remote.aio()


@pytest.mark.parametrize("asynchronous", [True, False])
async def test_same_worker_validates_stream_chunks(app, monkeypatch, asynchronous):
    async def async_stream():
        yield 1
        yield b"bytes"

    def sync_stream():
        yield 1
        yield b"bytes"

    handle = app.queue(name="stream")(async_stream if asynchronous else sync_stream)
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep")
    monkeypatch.setattr(handle, "_is_current_worker", lambda: True)
    stream = handle.stream.aio()
    assert await anext(stream) == 1
    with pytest.raises(TypeError, match="stream chunk.*json-serializable"):
        await anext(stream)


@pytest.mark.parametrize("asynchronous", [True, False])
async def test_same_worker_aggregate_stops_at_invalid_chunk(
    app, monkeypatch, asynchronous
):
    advanced = []

    async def async_stream():
        yield (1, 2)
        advanced.append(True)

    def sync_stream():
        yield (1, 2)
        advanced.append(True)

    handle = app.queue(name="stream")(async_stream if asynchronous else sync_stream)
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep")
    monkeypatch.setattr(handle, "_is_current_worker", lambda: True)
    with pytest.raises(TypeError, match="return value.*json-serializable"):
        await handle.remote.aio()
    assert advanced == []


@pytest.mark.parametrize(
    "value", [b"bytes", datetime(2026, 1, 1), float("nan"), (1, 2)]
)
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
