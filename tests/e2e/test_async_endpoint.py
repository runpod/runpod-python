import pytest
import runpod
from runpod.endpoint.asyncio import asyncio_runner

pytestmark = pytest.mark.qb


@pytest.fixture(autouse=True)
def _patch_runpod_base_url(flash_server):
    """Point the SDK Endpoint client at the local flash server."""
    original = runpod.endpoint_url_base
    runpod.endpoint_url_base = flash_server["base_url"]
    yield
    runpod.endpoint_url_base = original


@pytest.mark.asyncio
async def test_async_run(flash_server):
    """Async SDK client submits a job and polls for output."""
    endpoint = asyncio_runner.Job("async_handler")
    await endpoint.run({"input_data": {"prompt": "async-test"}})

    status = await endpoint.status()
    assert status in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED")

    output = await endpoint.output(timeout=30)
    assert output["input_received"] == {"prompt": "async-test"}
    assert output["status"] == "ok"


@pytest.mark.asyncio
async def test_async_run_sync_fallback(flash_server):
    """Sync SDK Endpoint works against async handler endpoint."""
    endpoint = runpod.Endpoint("async_handler")
    result = endpoint.run_sync({"input_data": {"prompt": "sync-to-async"}})

    assert result["input_received"] == {"prompt": "sync-to-async"}
    assert result["status"] == "ok"
