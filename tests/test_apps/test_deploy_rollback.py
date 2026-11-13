"""endpoint snapshots and deployment rollback ordering."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from runpod.api.queries.apps import QUERY_ENDPOINT_SNAPSHOT
from runpod.api.mutations.apps import MUTATION_SAVE_ENDPOINT
from runpod.apps import App
from runpod.apps.api import AppsApiClient
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import reconcile_endpoints
from runpod.apps.errors import EndpointReconciliationError


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def snapshot(name="worker", endpoint_id="ep-old"):
    return {
        "id": endpoint_id,
        "name": name,
        "type": "QB",
        "instanceIds": ["cpu3c-1-2"],
        "gpuIds": None,
        "gpuCount": 1,
        "locations": "EU-RO-1",
        "workersMin": 1,
        "workersMax": 2,
        "workersPFBTarget": 0,
        "idleTimeout": 120,
        "scalerType": "QUEUE_DELAY",
        "scalerValue": 5,
        "executionTimeoutMs": 5000,
        "workerInitTimeoutMs": 10000,
        "flashBootType": "OFF",
        "minCudaVersion": None,
        "allowedCudaVersions": "",
        "modelReferences": [],
        "env": [{"key": "CUSTOM", "value": "synthetic-private-value"}],
        "networkVolumeIds": [{"networkVolumeId": "volume-old"}],
        "volumes": [{"volumeId": "volume-old", "volumeType": "NETWORK_VOLUME"}],
        "template": {
            "name": "old-template",
            "imageName": "old-image",
            "containerDiskInGb": 10,
            "dockerArgs": "old-command",
            "env": [{"key": "RUNPOD_BUILD_ID", "value": "old-build"}],
            "ports": None,
            "containerRegistryAuthId": None,
            "advancedStart": False,
            "startScript": None,
            "config": None,
        },
    }


async def test_snapshot_is_a_restorable_copy_of_endpoint_and_template():
    original = snapshot()
    client = AppsApiClient()
    client._execute = AsyncMock(return_value={"myself": {"endpoint": original}})
    saved = await client.snapshot_endpoint("ep-old")
    client._execute.assert_awaited_once_with(
        QUERY_ENDPOINT_SNAPSHOT, {"id": "ep-old"}, retry=True
    )
    assert saved == {
        **original,
        "template": {**original["template"], "containerRegistryAuthId": ""},
        "volumes": [],
    }
    saved["template"]["env"][0]["value"] = "modified"
    assert original["template"]["env"][0]["value"] == "old-build"
    assert original["template"]["containerRegistryAuthId"] is None


async def test_snapshot_retains_global_volumes_and_empty_values():
    original = snapshot()
    original.update(env=None, modelReferences=None, networkVolumeIds=None)
    original["template"].update(env=None, dockerArgs=None)
    original["volumes"] = [
        {"volumeId": "global-old", "volumeType": "OBJECT_STORE_VOLUME"}
    ]
    client = AppsApiClient()
    client._execute = AsyncMock(return_value={"myself": {"endpoint": original}})
    saved = await client.snapshot_endpoint("ep-old")
    assert saved["volumes"] == original["volumes"]
    assert saved["env"] == saved["modelReferences"] == saved["networkVolumeIds"] == []
    assert saved["template"]["env"] == []
    assert saved["template"]["dockerArgs"] == ""


async def test_snapshot_can_be_saved_through_the_control_plane_client():
    client = AppsApiClient()
    client._execute = AsyncMock(
        side_effect=[
            {"myself": {"endpoint": snapshot()}},
            {"saveEndpoint": {"id": "ep-old"}},
        ]
    )
    saved = await client.snapshot_endpoint("ep-old")
    assert await client.save_endpoint(saved) == {"id": "ep-old"}
    client._execute.assert_awaited_with(MUTATION_SAVE_ENDPOINT, {"input": saved})


@pytest.mark.parametrize(
    "current", [None, {"activeBuildId": "new-build"}, {"activeBuildId": None}]
)
async def test_first_activation_failure_reports_unrestorable_build_state(current):
    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    api.deploy_build.side_effect = TimeoutError("response lost")
    api.get_environment_by_name.return_value = current
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api,
            app,
            {"id": "env", "name": "prod"},
            "new-build",
            "3.12",
            activate_build=True,
        )
    if current == {"activeBuildId": None}:
        assert "Changes rolled back." in str(caught.value)
    else:
        assert "Rollback incomplete:\n  build:" in str(caught.value)
    api.save_endpoint.assert_not_awaited()


@pytest.mark.parametrize(
    "endpoint", [None, {"id": "other"}, {"id": "ep-old", "template": None}]
)
async def test_snapshot_fails_closed_when_current_state_is_unavailable(endpoint):
    client = AppsApiClient()
    client._execute = AsyncMock(return_value={"myself": {"endpoint": endpoint}})
    with pytest.raises(RuntimeError, match="no restorable configuration"):
        await client.snapshot_endpoint("ep-old")


def app_and_environment():
    app = App("demo")
    for name in ("alpha", "beta", "gamma"):
        app.queue(name=name, cpu="cpu3c-1-2")(lambda x: x)
    environment = {
        "id": "env",
        "name": "prod",
        "activeBuildId": "old-build",
        "endpoints": [
            {"id": "ep-alpha", "name": "alpha"},
            {"id": "ep-beta", "name": "beta"},
        ],
    }
    return app, environment


async def test_restores_full_state_of_successful_and_failed_updates_in_reverse_order():
    app, environment = app_and_environment()
    alpha = snapshot("alpha", "ep-alpha")
    beta = snapshot("beta", "ep-beta")
    api = AsyncMock()
    api.snapshot_endpoint.side_effect = [deepcopy(alpha), deepcopy(beta)]
    api.save_endpoint.side_effect = [
        {"id": "ep-alpha"},
        TimeoutError("response lost"),
        {"id": "ep-beta"},
        {"id": "ep-alpha"},
    ]
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api, app, environment, "new-build", "3.12", activate_build=True
        )
    assert (
        str(caught.value)
        == "Couldn't deploy 'beta': response lost\nChanges rolled back."
    )
    calls = api.save_endpoint.await_args_list
    assert calls[2].args[0] == {**beta, "flashEnvironmentId": "env"}
    assert calls[3].args[0] == {**alpha, "flashEnvironmentId": "env"}
    assert [call[0] for call in api.mock_calls[:3]] == [
        "snapshot_endpoint",
        "snapshot_endpoint",
        "deploy_build",
    ]
    assert [call.args for call in api.deploy_build.await_args_list] == [
        ("env", "new-build"),
        ("env", "old-build"),
    ]
    assert api.mock_calls[-3][0] == "deploy_build"
    api.delete_endpoint.assert_not_awaited()
    assert "synthetic-private-value" not in str(caught.value)


async def test_snapshot_failure_stops_before_any_writes():
    app, environment = app_and_environment()
    api = AsyncMock()
    api.snapshot_endpoint.side_effect = [snapshot(), RuntimeError("access denied")]
    with pytest.raises(
        EndpointReconciliationError, match="Couldn't read 'beta': access denied"
    ):
        await reconcile_endpoints(
            api, app, environment, "new-build", "3.12", activate_build=True
        )
    api.save_endpoint.assert_not_awaited()
    api.deploy_build.assert_not_awaited()
    api.delete_endpoint.assert_not_awaited()


@pytest.mark.parametrize("restore_failure", [False, True])
async def test_activation_failure_restores_endpoints_and_previous_build(
    restore_failure,
):
    app, environment = app_and_environment()
    api = AsyncMock()
    api.snapshot_endpoint.side_effect = [
        snapshot("alpha", "ep-alpha"),
        snapshot("beta", "ep-beta"),
    ]
    api.save_endpoint.side_effect = lambda payload: {
        "id": payload.get("id", "ep-gamma")
    }
    api.deploy_build.side_effect = [
        TimeoutError("activation timed out"),
        RuntimeError("build restore denied") if restore_failure else {"id": "env"},
    ]
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api, app, environment, "new-build", "3.12", activate_build=True
        )
    assert [call.args for call in api.deploy_build.await_args_list] == [
        ("env", "new-build"),
        ("env", "old-build"),
    ]
    api.delete_endpoint.assert_not_awaited()
    api.save_endpoint.assert_not_awaited()
    if restore_failure:
        assert "Rollback incomplete:\n  build: build restore denied" in str(
            caught.value
        )
    else:
        assert (
            str(caught.value)
            == "Couldn't activate the build: activation timed out\nChanges rolled back."
        )


async def test_restore_failure_does_not_skip_other_endpoints():
    app, environment = app_and_environment()
    api = AsyncMock()
    api.snapshot_endpoint.side_effect = [
        snapshot("alpha", "ep-alpha"),
        snapshot("beta", "ep-beta"),
    ]
    api.save_endpoint.side_effect = [
        {"id": "ep-alpha"},
        RuntimeError("update failed"),
        RuntimeError("restore denied"),
        {"id": "ep-alpha"},
    ]
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(api, app, environment, "new-build", "3.12")
    assert "Rollback incomplete:\n  beta (ep-beta): restore denied" in str(caught.value)
    assert api.save_endpoint.await_args.args[0]["id"] == "ep-alpha"


async def test_lost_create_response_is_recovered_by_unique_template_name():
    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    environment = {"id": "env", "name": "prod", "endpoints": []}
    api = AsyncMock()
    api.save_endpoint.side_effect = TimeoutError("response lost")
    api.get_environment_by_name.return_value = {
        "endpoints": [
            {
                "id": "ep-created",
                "name": "worker",
                "template": {"name": "demo-worker-template-token"},
            },
            {
                "id": "ep-other",
                "name": "worker",
                "template": {"name": "another-deploy"},
            },
        ]
    }
    with patch("runpod.apps.deploy.secrets.token_hex", return_value="token"):
        with pytest.raises(EndpointReconciliationError) as caught:
            await reconcile_endpoints(api, app, environment, "new-build", "3.12")
    api.delete_endpoint.assert_awaited_once_with("ep-created")
    assert (
        str(caught.value)
        == "Couldn't deploy 'worker': response lost\nChanges rolled back."
    )


async def test_activation_precedes_irreversible_cleanup():
    app, environment = app_and_environment()
    environment["endpoints"].append({"name": "obsolete", "id": "ep-obsolete"})
    api = AsyncMock()
    api.save_endpoint.side_effect = lambda payload: {
        "id": payload.get("id", "ep-gamma")
    }
    await reconcile_endpoints(
        api, app, environment, "new-build", "3.12", activate_build=True
    )
    calls = [call[0] for call in api.mock_calls]
    assert calls.index("deploy_build") < min(
        i for i, name in enumerate(calls) if name == "save_endpoint"
    )
    assert calls.index("delete_endpoint") > max(
        i for i, name in enumerate(calls) if name == "save_endpoint"
    )


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_new_environment_is_removed_after_endpoint_rollback(cleanup_fails):
    app = App("demo")
    app.queue(name="alpha", cpu="cpu3c-1-2")(lambda: None)
    app.queue(name="beta", cpu="cpu3c-1-2")(lambda: None)
    environment = {"id": "env", "name": "prod", "endpoints": []}
    api = AsyncMock()
    api.save_endpoint.side_effect = [{"id": "ep-alpha"}, RuntimeError("quota exceeded")]
    api.get_environment_by_name.return_value = {
        "id": "env",
        "endpoints": [],
        "activeBuildId": "new-build",
    }
    if cleanup_fails:
        api.delete_endpoint.side_effect = RuntimeError("delete denied")
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api,
            app,
            environment,
            "new-build",
            "3.12",
            activate_build=True,
            environment_created=True,
        )
    if cleanup_fails:
        api.delete_environment.assert_not_awaited()
        assert "Rollback incomplete:" in str(caught.value)
    else:
        api.delete_environment.assert_awaited_once_with("env")
        calls = [call[0] for call in api.mock_calls]
        assert calls.index("delete_endpoint") < calls.index("delete_environment")
        assert (
            str(caught.value)
            == "Couldn't deploy 'beta': quota exceeded\nChanges rolled back."
        )
