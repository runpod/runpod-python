import pytest

pytestmark = [pytest.mark.qb, pytest.mark.usefixtures("require_api_key")]


@pytest.mark.asyncio
async def test_stateful_handler_set(flash_server, http_client):
    """Stateful handler accepts a set action and returns stored=True."""
    url = f"{flash_server['base_url']}/stateful_handler/runsync"

    resp = await http_client.post(
        url,
        json={"input": {"action": "set", "key": "e2e-test", "value": "hello"}},
    )
    assert resp.status_code == 200, f"Set failed: {resp.text}"
    assert resp.json()["output"]["stored"] is True


@pytest.mark.asyncio
async def test_stateful_handler_get(flash_server, http_client):
    """Stateful handler accepts a get action and returns a value."""
    url = f"{flash_server['base_url']}/stateful_handler/runsync"

    resp = await http_client.post(
        url,
        json={"input": {"action": "get", "key": "nonexistent"}},
    )
    assert resp.status_code == 200, f"Get failed: {resp.text}"
    assert resp.json()["output"]["value"] is None
