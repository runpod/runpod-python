import pytest

pytestmark = [pytest.mark.qb, pytest.mark.usefixtures("require_api_key")]


@pytest.mark.asyncio
async def test_handler_error_propagation(flash_server, http_client):
    """Malformed input surfaces an error response."""
    url = f"{flash_server['base_url']}/sync_handler/runsync"
    resp = await http_client.post(url, json={"input": None})

    assert resp.status_code in (400, 422, 500)
