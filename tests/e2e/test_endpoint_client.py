import pytest
import runpod

pytestmark = [pytest.mark.qb, pytest.mark.usefixtures("require_api_key")]


@pytest.mark.asyncio
async def test_run_sync(flash_server):
    """SDK Endpoint.run_sync() submits a job and gets the result."""
    endpoint = runpod.Endpoint("sync_handler")
    result = endpoint.run_sync({"input_data": {"prompt": "test"}})

    assert result["input_received"] == {"prompt": "test"}
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_run_async_poll(flash_server):
    """SDK Endpoint.run() submits async job, poll status, get output."""
    endpoint = runpod.Endpoint("sync_handler")
    run_request = endpoint.run({"input_data": {"prompt": "poll-test"}})

    status = run_request.status()
    assert status in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED")

    output = run_request.output(timeout=120)
    assert output["input_received"] == {"prompt": "poll-test"}
    assert output["status"] == "ok"


@pytest.mark.asyncio
async def test_run_sync_error(flash_server):
    """SDK Endpoint.run_sync() surfaces handler errors on bad input."""
    endpoint = runpod.Endpoint("sync_handler")

    with pytest.raises((TypeError, ValueError, RuntimeError)):
        endpoint.run_sync(None)
