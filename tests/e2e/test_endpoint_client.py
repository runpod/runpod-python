import pytest
import runpod

pytestmark = [pytest.mark.qb, pytest.mark.usefixtures("require_api_key")]


@pytest.mark.asyncio
async def test_run_sync(flash_server):
    """SDK Endpoint.run_sync() submits a job and gets the result."""
    endpoint = runpod.Endpoint("sync_handler")
    result = endpoint.run_sync({"input_data": {"prompt": "test"}}, timeout=120)

    assert result["input_received"] == {"prompt": "test"}
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_run_sync_error(flash_server):
    """SDK Endpoint.run_sync() surfaces handler errors on bad input."""
    endpoint = runpod.Endpoint("sync_handler")

    with pytest.raises((TypeError, ValueError, RuntimeError)):
        endpoint.run_sync(None, timeout=30)
