import pytest
import runpod

pytestmark = [pytest.mark.qb, pytest.mark.usefixtures("require_api_key")]


@pytest.mark.asyncio
async def test_async_run_sync(flash_server):
    """Sync SDK Endpoint.run_sync() works against async handler endpoint."""
    endpoint = runpod.Endpoint("async_handler")
    result = endpoint.run_sync(
        {"input_data": {"prompt": "sync-to-async"}}, timeout=120
    )

    assert result["input_received"] == {"prompt": "sync-to-async"}
    assert result["status"] == "ok"
