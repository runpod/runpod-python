import asyncio
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from runpod.apps import App
from runpod.apps.api import AppsApiClient
from runpod.apps.app import _clear_registry
from runpod.apps.build import BuildResult
from runpod.apps.deploy import deploy_app, reconcile_endpoints
from runpod.apps.errors import EndpointReconciliationError
from runpod.apps.volume import GlobalVolume, NetworkVolume, VolumeResolver
from runpod.error import QueryError


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.parametrize(
    "error",
    [
        QueryError("model reconciliation failed"),
        json.JSONDecodeError("invalid response", "", 0),
        RuntimeError("response processing failed"),
    ],
)
async def test_postcommit_create_errors_recover_the_committed_endpoint(error):
    app = App("demo")
    app.queue(name="worker", gpu="AMPERE_24", model="owner/model")(lambda: None)
    api = AppsApiClient()
    environment = {"id": "env", "name": "prod", "activeBuildId": "old", "endpoints": []}
    state = deepcopy(environment)

    async def execute(query, variables=None, **kwargs):
        state["endpoints"].append(
            {
                "id": "created",
                "name": "worker",
                "template": variables["input"]["template"],
            }
        )
        raise error

    async def activate(environment_id, build_id):
        state["activeBuildId"] = build_id

    async def delete(endpoint_id):
        state["endpoints"] = [e for e in state["endpoints"] if e["id"] != endpoint_id]
        return True

    api._execute = AsyncMock(side_effect=execute)
    api.deploy_build = AsyncMock(side_effect=activate)
    api.get_environment_by_name = AsyncMock(side_effect=lambda *args: deepcopy(state))
    api.delete_endpoint = AsyncMock(side_effect=delete)
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api, app, environment, "new", "3.12", activate_build=True
        )
    assert caught.value.__cause__ is error
    assert state == environment
    api.delete_endpoint.assert_awaited_once_with("created")
    assert "Best-effort rollback completed." in str(caught.value)


@pytest.mark.parametrize("current", [None, {}, {"endpoints": []}, {"endpoints": None}])
async def test_unconfirmed_create_never_claims_complete_rollback(current):
    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    api.save_endpoint.side_effect = QueryError("request failed")
    api.get_environment_by_name.return_value = current
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api, app, {"id": "env", "name": "prod"}, "new", "3.12"
        )
    assert "creation status unknown" in str(caught.value)
    assert "Best-effort rollback completed." not in str(caught.value)
    api.delete_endpoint.assert_not_awaited()


async def test_multiple_create_matches_are_not_deleted():
    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()

    async def save(payload):
        api.get_environment_by_name.return_value = {
            "endpoints": [
                {"id": endpoint_id, "template": payload["template"]}
                for endpoint_id in ("first", "second")
            ]
        }
        raise QueryError("request failed")

    api.save_endpoint.side_effect = save
    with pytest.raises(EndpointReconciliationError, match="creation status unknown"):
        await reconcile_endpoints(
            api, app, {"id": "env", "name": "prod"}, "new", "3.12"
        )
    api.delete_endpoint.assert_not_awaited()


@pytest.mark.parametrize("environment_created", [False, True])
async def test_upload_intervening_deployment_is_the_rollback_baseline(
    tmp_path, environment_created
):
    app = App("demo")
    for name in ("alpha", "beta"):
        app.queue(name=name, cpu="cpu3c-1-2")(lambda: None)
    original = {
        "id": "env",
        "name": "prod",
        "activeBuildId": None if environment_created else "old",
        "endpoints": [],
    }
    state = deepcopy(original)
    endpoint_build = "old"
    api = AsyncMock()
    api.get_app_by_name.return_value = {
        "id": "app",
        "flashEnvironments": [] if environment_created else [deepcopy(original)],
    }
    api.create_environment.return_value = deepcopy(original)
    api.prepare_artifact_upload.return_value = {
        "uploadUrl": "unused",
        "objectKey": "unused",
    }
    api.finalize_artifact_upload.return_value = {"id": "new"}

    async def upload(*args, **kwargs):
        nonlocal endpoint_build
        endpoint_build = "successful-other-deploy"
        state.update(
            activeBuildId=endpoint_build,
            endpoints=[
                {
                    "id": "ep-alpha",
                    "name": "alpha",
                    "template": {"name": "other-template"},
                }
            ],
        )

    async def snapshot(endpoint_id):
        return {
            "id": endpoint_id,
            "name": "alpha",
            "template": {"env": [{"key": "RUNPOD_BUILD_ID", "value": endpoint_build}]},
        }

    async def activate(environment_id, build_id):
        state["activeBuildId"] = build_id

    async def save(payload):
        nonlocal endpoint_build
        if payload["name"] == "beta":
            raise QueryError("quota exceeded")
        endpoint_build = next(
            e["value"]
            for e in payload["template"]["env"]
            if e["key"] == "RUNPOD_BUILD_ID"
        )
        return {"id": "ep-alpha"}

    api.upload_tarball.side_effect = upload
    api.snapshot_endpoint.side_effect = snapshot
    api.deploy_build.side_effect = activate
    api.save_endpoint.side_effect = save
    api.get_environment_by_name.side_effect = lambda *args: deepcopy(state)
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_bytes(b"synthetic")
    with (
        patch(
            "runpod.apps.deploy.build_environment",
            return_value=BuildResult(tmp_path, []),
        ),
        patch("runpod.apps.deploy.package_project", return_value=artifact),
    ):
        with pytest.raises(EndpointReconciliationError):
            await deploy_app(app, tmp_path, env_name="prod", api=api)
    assert state["activeBuildId"] == endpoint_build == "successful-other-deploy"
    api.snapshot_endpoint.assert_awaited_once_with("ep-alpha")
    assert api.save_endpoint.await_args_list[0].args[0]["id"] == "ep-alpha"
    assert (
        api.save_endpoint.await_args_list[0].args[0]["template"]["name"]
        == "other-template"
    )
    api.delete_environment.assert_not_awaited()
    calls = [call[0] for call in api.mock_calls]
    assert (
        calls.index("finalize_artifact_upload")
        < calls.index("get_environment_by_name")
        < calls.index("snapshot_endpoint")
        < calls.index("deploy_build")
    )


@pytest.mark.parametrize(
    "current",
    [
        None,
        {},
        {
            "id": "replacement",
            "name": "prod",
            "activeBuildId": "other",
            "endpoints": [],
        },
    ],
)
async def test_missing_or_replaced_postupload_baseline_stops_before_writes(
    tmp_path, current
):
    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    api.get_app_by_name.return_value = {
        "id": "app",
        "flashEnvironments": [{"id": "env", "name": "prod"}],
    }
    api.prepare_artifact_upload.return_value = {
        "uploadUrl": "unused",
        "objectKey": "unused",
    }
    api.finalize_artifact_upload.return_value = {"id": "new"}
    api.get_environment_by_name.return_value = current
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_bytes(b"synthetic")
    with (
        patch(
            "runpod.apps.deploy.build_environment",
            return_value=BuildResult(tmp_path, []),
        ),
        patch("runpod.apps.deploy.package_project", return_value=artifact),
    ):
        with pytest.raises(EndpointReconciliationError, match="baseline after upload"):
            await deploy_app(app, tmp_path, env_name="prod", api=api)
    api.deploy_build.assert_not_awaited()
    api.save_endpoint.assert_not_awaited()
    api.delete_endpoint.assert_not_awaited()
    api.delete_environment.assert_not_awaited()


@pytest.mark.parametrize("volume_type", [GlobalVolume, NetworkVolume])
@pytest.mark.parametrize("reused", [False, True])
@pytest.mark.parametrize("task_storage", [False, True])
async def test_retained_storage_reports_only_new_volumes(
    volume_type, reused, task_storage
):
    app = App("demo")
    volume = volume_type("data")
    if task_storage:
        app.task(name="task", gpu="AMPERE_24", mounts={"/data": volume})(lambda: None)
    app.queue(
        name="alpha",
        gpu="AMPERE_24",
        mounts={} if task_storage else {"/runpod-volume": volume},
    )(lambda: None)
    app.queue(name="beta", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    record = {"id": "retained-volume", "name": "data", "dataCenter": "EU-RO-1"}
    api.list_global_volumes.return_value = [record] if reused else []
    api.list_network_volumes.return_value = [record] if reused else []
    api.create_global_volume.return_value = record
    api.create_network_volume.return_value = record
    api.network_volume_datacenters.return_value = {"EU-RO-1"}
    api.gpu_stock_status.return_value = "High"
    api.delete_endpoint.return_value = True
    api.get_environment_by_name.return_value = {"endpoints": []}
    resolver = VolumeResolver(api)
    if task_storage:
        from runpod.apps.deploy import _prepare_task_resources, build_manifest

        manifest = build_manifest(app, Path.cwd())
        await _prepare_task_resources(app, manifest, api, resolver=resolver)
    api.snapshot_endpoint.return_value = {"id": "ep-beta", "name": "beta"}
    api.save_endpoint.side_effect = [
        {"id": "ep-alpha"},
        QueryError("quota exceeded"),
        {"id": "ep-beta"},
    ]
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api,
            app,
            {
                "id": "env",
                "name": "prod",
                "endpoints": [{"id": "ep-beta", "name": "beta"}],
            },
            "new",
            "3.12",
            resolver=resolver,
        )
    message = str(caught.value)
    assert ("New storage retained:" in message) is not reused
    assert ("retained-volume" in message) is not reused
    assert ("Best-effort rollback completed." in message) is reused
    if not reused:
        assert "Best-effort endpoint and build rollback completed." in message
        assert f"{volume.kind} volume 'data' (retained-volume)" in message
        assert "delete only if unused" in message
        assert resolver.created_volumes == [
            {"kind": volume.kind, "name": "data", "id": "retained-volume"}
        ]
        await resolver.resolve(volume)
        assert len(resolver.created_volumes) == 1
    api.delete_endpoint.assert_awaited_once_with("ep-alpha")
    api.delete_global_volume.assert_not_awaited()
    api.delete_network_volume.assert_not_awaited()


@pytest.mark.parametrize("cleanup_cancelled", [False, True])
async def test_retained_volume_diagnostic_survives_cancellation(
    cleanup_cancelled, caplog
):
    app = App("demo")
    app.queue(
        name="alpha", gpu="AMPERE_24", mounts={"/runpod-volume": GlobalVolume("data")}
    )(lambda: None)
    app.queue(name="beta", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    api.list_global_volumes.return_value = []
    api.create_global_volume.return_value = {"id": "retained-volume"}
    api.get_environment_by_name.return_value = {"endpoints": []}
    api.save_endpoint.side_effect = [{"id": "ep-alpha"}, asyncio.CancelledError()]
    api.delete_endpoint.side_effect = (
        asyncio.CancelledError() if cleanup_cancelled else None
    )
    api.delete_endpoint.return_value = True
    with pytest.raises(asyncio.CancelledError):
        await reconcile_endpoints(
            api, app, {"id": "env", "name": "prod"}, "new", "3.12"
        )
    assert "global volume 'data' (retained-volume)" in caplog.text
    assert "delete only if unused" in caplog.text
    api.delete_global_volume.assert_not_awaited()
