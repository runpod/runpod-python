"""json contracts across development and deployed invocation targets."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from runpod.apps import App, Context
from runpod.apps.app import _clear_registry
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.targets import (
    DeployedTaskTarget,
    LiveTarget,
    PodTarget,
    SentinelTarget,
    _request_json,
)
from runpod.apps.tasks import unwrap_task_response


@pytest.fixture(autouse=True)
def isolated_apps():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.parametrize(
    "target", [LiveTarget("dev"), SentinelTarget("app", "env", "work")]
)
@pytest.mark.parametrize(
    "value", [b"bytes", datetime(2026, 1, 1), float("nan"), float("inf")]
)
def test_queue_arguments_obey_same_json_contract(target, value):
    def work(value):
        return value

    spec = ResourceSpec(kind=ResourceKind.QUEUE, name="work")
    with pytest.raises(TypeError, match="json-serializable"):
        target.build_payload(work, spec, (value,), {})


@pytest.mark.parametrize(
    "value", [b"bytes", datetime(2026, 1, 1), float("nan"), float("inf")]
)
def test_task_arguments_obey_same_json_contract(value):
    def work(value):
        return value

    spec = ResourceSpec(kind=ResourceKind.TASK, name="work")
    with pytest.raises(ValueError, match="only JSON args and kwargs"):
        PodTarget(spec, work).build_payload(work, spec, (), {"value": value})
    with pytest.raises(ValueError, match="only JSON args and kwargs"):
        DeployedTaskTarget("app", "env", "work")._execution(
            {"args": [], "kwargs": {"value": value}}
        )


def test_dev_task_json_arguments_match_deployed_payload():
    def work(value, flag):
        return value

    spec = ResourceSpec(kind=ResourceKind.TASK, name="work")
    args, kwargs = ({"values": [1, None]},), {"flag": True}
    dev = PodTarget(spec, work).build_payload(work, spec, args, kwargs)
    deployed = DeployedTaskTarget("app", "env", "work").build_payload(
        work, spec, args, kwargs
    )
    assert dev["serialization_format"] == "json"
    assert {key: dev[key] for key in ("args", "kwargs")} == deployed


@pytest.mark.parametrize(
    "target", [LiveTarget("dev"), SentinelTarget("app", "env", "work")]
)
@pytest.mark.parametrize("value", [b"bytes", float("nan"), float("inf")])
async def test_api_body_validation_precedes_requests_and_source_sync(target, value):
    with (
        patch("runpod.apps.targets.aiohttp.ClientSession") as session,
        patch.object(LiveTarget, "_sync_source", AsyncMock()) as sync,
    ):
        with pytest.raises(
            ValueError, match="api request body must be JSON-serializable"
        ):
            await target.request("POST", "/work", {"value": value})
        session.assert_not_called()
        sync.assert_not_awaited()


async def test_raw_queue_requests_reject_non_json_options_before_network():
    with patch("runpod.apps.targets.aiohttp.ClientSession") as session:
        with pytest.raises(ValueError, match="remote request body"):
            await _request_json(
                "POST",
                "https://invalid",
                {},
                30,
                payload={"input": {}, "policy": {"ttl": float("nan")}},
            )
        session.assert_not_called()


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_queue_and_task_results_reject_non_finite_json(value):
    for target in (LiveTarget("dev"), SentinelTarget("app", "env", "work")):
        with pytest.raises(ValueError, match="results must be JSON-serializable"):
            target.unwrap({"status": "COMPLETED", "output": {"value": value}})
    with pytest.raises(ValueError, match="results must be JSON-serializable"):
        LiveTarget("dev")._unwrap_response({"success": True, "json_result": value})
    with pytest.raises(ValueError, match="results must be JSON-serializable"):
        unwrap_task_response({"success": True, "json_result": value})


@pytest.mark.parametrize(
    "text,expected", [(' {"value": 1} ', {"value": 1}), ("plain text", "plain text")]
)
@pytest.mark.parametrize(
    "target", [LiveTarget("dev"), SentinelTarget("app", "env", "work")]
)
async def test_api_json_and_text_responses_share_transport(target, text, expected):
    with (
        patch("runpod.apps.targets._headers", return_value={}),
        patch("runpod.apps.targets.aiohttp.ClientSession") as factory,
    ):
        session = factory.return_value.__aenter__.return_value
        session.request = MagicMock()
        response = session.request.return_value.__aenter__.return_value
        response.status = 200
        response.text = AsyncMock(return_value=text)
        response.raise_for_status = lambda: None
        assert await target.request("POST", "/work", {"value": 1}) == expected
        assert session.request.call_args.kwargs["json"] == {"value": 1}


@pytest.mark.parametrize("async_call", [False, True])
async def test_worker_self_calls_apply_json_conversion(async_call):
    app = App("app")

    @app.queue(name="work")
    def work(value):
        assert isinstance(value, list)
        return (value,)

    with (
        patch("runpod.apps.handles.current_context", return_value=Context.WORKER),
        patch.object(work, "_is_current_worker", return_value=True),
    ):
        result = await work.remote.aio((1, 2)) if async_call else work.remote((1, 2))
        assert result == [[1, 2]]
        with pytest.raises(ValueError, match="JSON-serializable"):
            if async_call:
                await work.remote.aio(b"bytes")
            else:
                work.remote(b"bytes")


async def test_worker_self_stream_rejects_non_json_chunks():
    app = App("app")

    @app.queue(name="work")
    def work():
        yield b"bytes"

    with (
        patch("runpod.apps.handles.current_context", return_value=Context.WORKER),
        patch.object(work, "_is_current_worker", return_value=True),
    ):
        with pytest.raises(ValueError, match="JSON-serializable"):
            async for _ in work.stream.aio():
                pass
