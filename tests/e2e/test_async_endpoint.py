import pytest
import runpod
from runpod.http_client import ClientSession

from runpod.endpoint.asyncio.asyncio_runner import Endpoint as AsyncEndpoint

pytestmark = [pytest.mark.qb, pytest.mark.usefixtures("require_api_key")]


@pytest.mark.asyncio
async def test_async_run(flash_server):
    """Async SDK client submits a job and polls for output."""
    async with ClientSession() as session:
        endpoint = AsyncEndpoint("async_handler", session)
        job = await endpoint.run({"input_data": {"prompt": "async-test"}})

        status = await job.status()
        assert status in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED")

        output = await job.output(timeout=120)
        assert output["input_received"] == {"prompt": "async-test"}
        assert output["status"] == "ok"


@pytest.mark.asyncio
async def test_async_run_sync_fallback(flash_server):
    """Sync SDK Endpoint works against async handler endpoint."""
    endpoint = runpod.Endpoint("async_handler")
    result = endpoint.run_sync({"input_data": {"prompt": "sync-to-async"}})

    assert result["input_received"] == {"prompt": "sync-to-async"}
    assert result["status"] == "ok"
