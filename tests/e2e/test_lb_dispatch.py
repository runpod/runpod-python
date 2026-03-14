import os

import pytest

pytestmark = pytest.mark.lb


@pytest.mark.asyncio
async def test_lb_echo(flash_server, http_client, require_api_key):
    """LB endpoint echoes text through remote dispatch."""
    url = f"{flash_server['base_url']}/echo"
    resp = await http_client.post(url, json={"text": "hello"})

    assert resp.status_code == 200
    assert resp.json()["echoed"] == "hello"


@pytest.mark.asyncio
async def test_lb_uses_target_branch(flash_server, http_client, require_api_key):
    """Provisioned LB endpoint runs the target runpod-python branch."""
    expected_branch = os.environ.get("RUNPOD_PYTHON_BRANCH", "main")

    url = f"{flash_server['base_url']}/echo"
    resp = await http_client.post(url, json={"text": expected_branch})

    assert resp.status_code == 200
    assert resp.json()["echoed"] == expected_branch
