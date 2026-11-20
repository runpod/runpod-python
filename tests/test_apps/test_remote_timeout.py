from unittest.mock import AsyncMock, Mock, patch

import pytest

from runpod.apps.handles import FunctionHandle
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.stubs import Queue
from runpod.apps.targets import LiveTarget, QueueClient, SentinelTarget


@pytest.mark.parametrize(
    "policy,timeout,expected",
    [
        ({}, 300, 300),
        (None, 300, 300),
        ("invalid", 300, 300),
        ({"executionTimeout": None}, 300, 300),
        ({"executionTimeout": "900000"}, 300, 300),
        ({"executionTimeout": {}}, 300, 300),
        ({"executionTimeout": True}, 1, 1),
        ({"executionTimeout": -1}, 1, 1),
        ({"executionTimeout": float("nan")}, 300, 300),
        ({"executionTimeout": float("inf")}, 300, 300),
        ({"executionTimeout": 0}, 300, 300),
        ({"executionTimeout": 0}, 1, 1),
        ({"executionTimeout": 60000}, 300, 300),
        ({"executionTimeout": 300000}, 300, 360),
        ({"executionTimeout": 900000}, 300, 960),
        ({"executionTimeout": 900500}, 300, 960.5),
        ({"executionTimeout": 900000}, 1200, 1200),
    ],
)
@pytest.mark.parametrize("observed", [False, True])
async def test_queue_client_waits_for_larger_timeout(
    policy, timeout, expected, observed
):
    client = QueueClient("endpoint", lambda: {})
    payload = {"input": {"x": 1}, "policy": policy}
    submitted = {"id": "job", "status": "IN_QUEUE"}
    completed = {"id": "job", "status": "COMPLETED", "output": 42}
    on_status = Mock() if observed else None

    with (
        patch.object(client, "_call", AsyncMock(return_value=submitted)) as call,
        patch.object(client, "wait", AsyncMock(return_value=completed)) as wait,
        patch("time.monotonic", side_effect=[100, 120]),
    ):
        result = await client.invoke(payload, timeout=timeout, on_status=on_status)

    assert result is completed
    call.assert_awaited_once_with(
        "POST",
        "runsync?wait=1000" if observed or expected <= 2 else "runsync?wait=90000",
        payload,
        expected,
    )
    wait.assert_awaited_once_with(
        submitted, timeout=max(0, expected - 20), on_status=on_status
    )
    assert payload == {"input": {"x": 1}, "policy": policy}


@pytest.mark.parametrize("kind", ["live", "sentinel"])
async def test_targets_apply_execution_timeout(kind):
    target = (
        LiveTarget("endpoint", "queue")
        if kind == "live"
        else SentinelTarget("app", "default", "queue")
    )
    completed = {"id": "job", "status": "COMPLETED", "output": 42}
    payload = {"input": {"x": 1}, "policy": {"executionTimeout": 900000}}

    with (
        patch.object(
            target._client, "_call", AsyncMock(return_value=completed)
        ) as call,
        patch.object(target._client, "wait", AsyncMock(return_value=completed)),
    ):
        assert await target.invoke(payload) == 42

    call.assert_awaited_once_with("POST", "runsync?wait=90000", payload, 960)


@pytest.mark.parametrize("status", ["COMPLETED", "TIMED_OUT"])
async def test_queue_client_allows_queue_delay_before_full_execution_window(status):
    client = QueueClient("endpoint", lambda: {})
    payload = {"input": {"x": 1}, "policy": {"executionTimeout": 900000}}
    clock = [0.0]
    terminal = {"id": "job", "status": status}

    async def submit(*args):
        clock[0] = 20.0
        return {"id": "job", "status": "IN_PROGRESS"}

    async def poll(*args, **kwargs):
        if clock[0] < 900.0:
            clock[0] = 900.0
            return {"id": "job", "status": "IN_PROGRESS"}
        clock[0] = 920.0
        return terminal

    with (
        patch.object(client, "_call", side_effect=submit),
        patch("runpod.apps.targets._get_json", side_effect=poll),
        patch("runpod.apps.targets.asyncio.sleep", new_callable=AsyncMock),
        patch("time.monotonic", side_effect=lambda: clock[0]),
    ):
        result = await client.invoke(payload, timeout=300)

    assert result is terminal
    assert clock[0] == 920.0


@pytest.mark.parametrize("kind", ["handle", "stub"])
async def test_remote_with_execution_timeout(kind):
    target = SentinelTarget("app", "default", "queue")
    if kind == "handle":
        app = Mock(_resolve=AsyncMock(return_value=target))
        handle = FunctionHandle(
            app, lambda x: x, ResourceSpec(kind=ResourceKind.QUEUE, name="queue")
        )
    else:
        handle = Queue(app="app", name="queue")

    handle = handle.with_options(execution_timeout=900000)
    completed = {"id": "job", "status": "COMPLETED", "output": 42}
    with (
        patch.object(Queue, "_target", return_value=target),
        patch.object(
            target._client, "_call", AsyncMock(return_value=completed)
        ) as call,
        patch.object(target._client, "wait", AsyncMock(return_value=completed)),
    ):
        assert await handle.remote.aio(x=1) == 42

    call.assert_awaited_once_with(
        "POST",
        "runsync?wait=90000",
        {"input": {"x": 1}, "policy": {"executionTimeout": 900000}},
        960,
    )


@pytest.mark.parametrize("kind", ["handle", "stub"])
@pytest.mark.parametrize("execution_timeout", [10**400, -(10**400)])
async def test_remote_rejects_oversized_execution_timeout(kind, execution_timeout):
    target = SentinelTarget("app", "default", "queue")
    if kind == "handle":
        app = Mock(_resolve=AsyncMock(return_value=target))
        handle = FunctionHandle(
            app, lambda x: x, ResourceSpec(kind=ResourceKind.QUEUE, name="queue")
        )
    else:
        handle = Queue(app="app", name="queue")

    handle = handle.with_options(execution_timeout=execution_timeout)
    with (
        patch.object(Queue, "_target", return_value=target),
        patch.object(target._client, "_call", new_callable=AsyncMock) as call,
        pytest.raises(
            ValueError, match="execution_timeout is too large for the client timeout"
        ),
    ):
        await handle.remote.aio(x=1)

    call.assert_not_awaited()
