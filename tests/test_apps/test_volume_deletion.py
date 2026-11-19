"""explicit volume deletion and app storage retention."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from runpod.apps.api import AppsApiClient
from runpod.apps.manage import delete_app
from runpod.error import AuthenticationError, QueryError
from runpod.user_agent import APPS_USER_AGENT


@pytest.mark.parametrize("kind", ["network", "global"])
@pytest.mark.parametrize("response", [None, {}])
async def test_delete_volume_accepts_empty_success_response(kind, response):
    rest = AsyncMock(return_value=response)
    with patch("runpod.apps.api.run_rest_request_async", rest):
        result = await getattr(
            AppsApiClient(api_key="test-key"), f"delete_{kind}_volume"
        )("volume-id")
    assert result is True
    rest.assert_awaited_once_with(
        "DELETE",
        f"/v2/{kind}-volumes/volume-id",
        user_agent=APPS_USER_AGENT,
        api_key="test-key",
    )


@pytest.mark.parametrize("kind", ["network", "global"])
async def test_delete_volume_encodes_id_as_one_path_segment(kind):
    rest = AsyncMock(return_value=None)
    with patch("runpod.apps.api.run_rest_request_async", rest):
        await getattr(AppsApiClient(), f"delete_{kind}_volume")("id/other?x=1#fragment")
    assert rest.call_args.args == (
        "DELETE",
        f"/v2/{kind}-volumes/id%2Fother%3Fx%3D1%23fragment",
    )


@pytest.mark.parametrize("kind", ["network", "global"])
@pytest.mark.parametrize(
    "failure",
    [
        AuthenticationError("invalid key"),
        QueryError("not found", status_code=404),
        QueryError("still mounted", status_code=409),
        aiohttp.ClientError("response lost"),
    ],
)
async def test_delete_volume_propagates_failure_without_retry(kind, failure):
    rest = AsyncMock(side_effect=failure)
    with (
        patch("runpod.apps.api.run_rest_request_async", rest),
        pytest.raises(type(failure)) as raised,
    ):
        await getattr(AppsApiClient(), f"delete_{kind}_volume")("volume-id")
    assert raised.value is failure
    rest.assert_awaited_once()


async def test_app_deletion_retains_shared_volumes():
    api = AsyncMock(spec=AppsApiClient)
    api.get_app_by_name.return_value = {
        "id": "app-id",
        "flashEnvironments": [{"name": "default"}],
    }
    api.get_environment_by_name.return_value = {
        "id": "env-id",
        "endpoints": [{"id": "endpoint-id", "name": "worker"}],
        "networkVolumes": [{"id": "shared-volume", "name": "models"}],
    }
    result = await delete_app("demo", api=api)
    assert result.app_deleted
    assert not result.failures
    api.delete_endpoint.assert_awaited_once_with("endpoint-id")
    api.delete_environment.assert_awaited_once_with("env-id")
    api.delete_app.assert_awaited_once_with("app-id")
    api.delete_network_volume.assert_not_awaited()
    api.delete_global_volume.assert_not_awaited()
