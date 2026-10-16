"""Cancelled jobs are terminal for sync and async output streams."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from runpod.endpoint.runner import Job as SyncJob
from runpod.endpoint.asyncio.asyncio_runner import Job as AsyncJob


@pytest.mark.parametrize("status", ["CANCELLED", "COMPLETED", "FAILED", "TIMED_OUT"])
@pytest.mark.parametrize("chunks", [[], [{"output": "last chunk"}]])
def test_sync_stream_stops_on_terminal_status(status, chunks):
    client = MagicMock()
    job = SyncJob("endpoint", "job", client)
    responses = [{"status": status, "stream": chunks}]
    if chunks:
        responses.append({"status": status, "stream": []})
    client.get.side_effect = responses
    with patch("runpod.endpoint.runner.time.sleep"):
        assert list(job.stream()) == [c["output"] for c in chunks]
    assert client.get.call_count == len(responses)


@pytest.mark.parametrize("status", ["CANCELLED", "COMPLETED", "FAILED", "TIMED_OUT"])
@pytest.mark.parametrize("chunks", [[], [{"output": "last chunk"}]])
@pytest.mark.asyncio
async def test_async_stream_stops_on_terminal_status(status, chunks):
    session = MagicMock()
    responses = [{"status": status, "stream": chunks}]
    if chunks:
        responses.append({"status": status, "stream": []})
    session.get = AsyncMock()
    session.get.return_value.json = AsyncMock(side_effect=responses)
    job = AsyncJob("endpoint", "job", session, {})
    with patch(
        "runpod.endpoint.asyncio.asyncio_runner.asyncio.sleep", new_callable=AsyncMock
    ):
        assert [c async for c in job.stream()] == [c["output"] for c in chunks]
    assert session.get.await_count == len(responses)


def test_run_sync_returns_cancelled_result_without_polling():
    from runpod.endpoint.runner import Endpoint

    with patch("runpod.endpoint.runner.RunPodClient") as client_class:
        client = client_class.return_value
        client.post.return_value = {"id": "job", "status": "CANCELLED", "output": None}
        client.get.side_effect = AssertionError("Terminal run must not poll")
        endpoint = Endpoint("endpoint", api_key="test-only")
        assert endpoint.run_sync({"input": {"value": 1}}) is None
        client.get.assert_not_called()
