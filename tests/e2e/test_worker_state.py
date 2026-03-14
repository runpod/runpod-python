import uuid

import pytest

pytestmark = [pytest.mark.qb, pytest.mark.usefixtures("require_api_key")]


@pytest.mark.asyncio
async def test_state_persists_across_calls(flash_server, http_client):
    """Setting a value via one call is retrievable in the next call."""
    url = f"{flash_server['base_url']}/stateful_handler/runsync"
    test_key = f"test-{uuid.uuid4().hex[:8]}"

    set_resp = await http_client.post(
        url,
        json={"input": {"action": "set", "key": test_key, "value": "hello"}},
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["output"]["stored"] is True

    get_resp = await http_client.post(
        url,
        json={"input": {"action": "get", "key": test_key}},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["output"]["value"] == "hello"


@pytest.mark.asyncio
async def test_state_independent_keys(flash_server, http_client):
    """Multiple keys persist independently."""
    url = f"{flash_server['base_url']}/stateful_handler/runsync"
    key_a = f"key-a-{uuid.uuid4().hex[:8]}"
    key_b = f"key-b-{uuid.uuid4().hex[:8]}"

    set_a = await http_client.post(
        url,
        json={"input": {"action": "set", "key": key_a, "value": "alpha"}},
    )
    assert set_a.status_code == 200

    set_b = await http_client.post(
        url,
        json={"input": {"action": "set", "key": key_b, "value": "beta"}},
    )
    assert set_b.status_code == 200

    resp_a = await http_client.post(
        url,
        json={"input": {"action": "get", "key": key_a}},
    )
    resp_b = await http_client.post(
        url,
        json={"input": {"action": "get", "key": key_b}},
    )

    assert resp_a.json()["output"]["value"] == "alpha"
    assert resp_b.json()["output"]["value"] == "beta"
