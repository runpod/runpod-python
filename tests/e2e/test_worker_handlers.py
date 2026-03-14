import pytest

pytestmark = [pytest.mark.qb, pytest.mark.usefixtures("require_api_key")]


@pytest.mark.asyncio
async def test_sync_handler(flash_server, http_client):
    """Sync QB handler receives input and returns expected output."""
    url = f"{flash_server['base_url']}/sync_handler/runsync"
    resp = await http_client.post(
        url, json={"input": {"input_data": {"prompt": "hello"}}}
    )

    assert resp.status_code == 200, f"sync_handler returned {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["output"]["input_received"] == {"prompt": "hello"}
    assert body["output"]["status"] == "ok"


@pytest.mark.asyncio
async def test_async_handler(flash_server, http_client):
    """Async QB handler receives input and returns expected output."""
    url = f"{flash_server['base_url']}/async_handler/runsync"
    resp = await http_client.post(
        url, json={"input": {"input_data": {"prompt": "hello"}}}
    )

    assert resp.status_code == 200, f"async_handler returned {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["output"]["input_received"] == {"prompt": "hello"}
    assert body["output"]["status"] == "ok"


@pytest.mark.asyncio
async def test_handler_error_propagation(flash_server, http_client):
    """Malformed input surfaces an error response."""
    url = f"{flash_server['base_url']}/sync_handler/runsync"
    resp = await http_client.post(url, json={"input": None})

    assert resp.status_code in (400, 422, 500)
