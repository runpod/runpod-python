"""deployment policy validation and shared resource resolution."""

import json
import tarfile
from unittest.mock import AsyncMock, patch

import pytest

from runpod.apps import App
from runpod.apps.api import AppsApiClient
from runpod.apps.app import _clear_registry
from runpod.apps.build import BuildResult
from runpod.apps.deploy import (
    _deployed_endpoint_input,
    build_artifact,
    build_manifest,
    deploy_app,
)
from runpod.apps.errors import InvalidResourceError
from runpod.apps.placement import solve_placement
from runpod.apps.tasks import DeployedTaskExecution, _validate_timeout
from runpod.apps.volume import GlobalVolume, NetworkVolume


@pytest.fixture(autouse=True)
def isolated_app_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.fixture
def deployment_api():
    api = AsyncMock()
    api.get_app_by_name.return_value = {
        "id": "app",
        "flashEnvironments": [{"id": "env", "name": "default"}],
    }
    api.prepare_artifact_upload.return_value = {
        "uploadUrl": "upload",
        "objectKey": "key",
    }
    api.finalize_artifact_upload.return_value = {"id": "build"}
    api.save_endpoint.return_value = {"id": "endpoint"}
    return api


@pytest.mark.parametrize(
    "key",
    [
        "RUNPOD_API_KEY",
        "runpod_custom",
        "FLASH_APP_NAME",
        " flash_custom ",
        "PYTHONPATH",
        "pythonhome",
        "LD_PRELOAD",
    ],
)
def test_task_manifest_rejects_reserved_env_keys(tmp_path, key):
    app = App("policy-app")

    @app.task(name="work", cpu="cpu3c-1-2", env={key: "test-value"})
    def work():
        pass

    with pytest.raises(InvalidResourceError) as exc:
        build_manifest(app, tmp_path)
    assert key in str(exc.value)
    assert "test-value" not in str(exc.value)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/root",
        "/root/.runpod",
        "/root/.runpod/cache",
        "/runpod-volume",
        "/runpod-volume/data",
        "/proc",
        "/proc/data",
        "/sys",
        "/sys/data",
        "/dev",
        "/dev/data",
    ],
)
def test_task_manifest_rejects_reserved_mount_overlap(tmp_path, path):
    app = App("policy-app")

    @app.task(name="work", cpu="cpu3c-1-2", mounts={path: NetworkVolume("data")})
    def work():
        pass

    with pytest.raises(InvalidResourceError, match="reserved runtime path"):
        build_manifest(app, tmp_path)


@pytest.mark.parametrize("path", ["/data", "/root/.runpod-data", "/proc-data"])
def test_task_manifest_accepts_non_reserved_env_and_paths(tmp_path, path):
    app = App("policy-app")

    @app.task(
        name="work",
        cpu="cpu3c-1-2",
        mounts={path: NetworkVolume("data")},
        env={"APP_TOKEN": "test-value", "PYTHONPATH_EXTRA": "allowed"},
    )
    def work():
        pass

    resource = build_manifest(app, tmp_path)["resources"][0]
    assert resource["mounts"][0]["path"] == path
    assert resource["env"] == work.spec.env


@pytest.mark.parametrize(
    "options",
    [
        {"env": {"RUNPOD_API_KEY": "test-value"}},
        {"mounts": {"/root": NetworkVolume("data")}},
    ],
)
async def test_invalid_tasks_fail_before_vendoring_or_remote_calls(tmp_path, options):
    app = App("policy-app")

    @app.task(name="work", cpu="cpu3c-1-2", **options)
    def work():
        pass

    api = AsyncMock()
    with patch("runpod.apps.deploy.build_environment") as vendor:
        with pytest.raises(InvalidResourceError):
            build_artifact(app, tmp_path)
        with pytest.raises(InvalidResourceError):
            await deploy_app(app, tmp_path, api=api)
        vendor.assert_not_called()
    assert not api.method_calls


@pytest.mark.parametrize("kind", ["queue", "api"])
@pytest.mark.parametrize(
    "key",
    [
        "RUNPOD_API_KEY",
        "RUNPOD_AI_API_KEY",
        "RUNPOD_ENDPOINT_ID",
        "RUNPOD_POD_ID",
        "RUNPOD_TASK_TOKEN",
        "RUNPOD_TASK_PORT",
        "FLASH_BUILD_ARTIFACT_PATH",
        "RUNPOD_RUNTIME_KIND",
        "RUNPOD_DEV_APP",
        "RUNPOD_DEV_RESOURCE",
        "RUNPOD_DEV_SESSION",
    ],
)
async def test_later_endpoint_reserved_env_fails_before_any_remote_change(
    tmp_path, deployment_api, kind, key
):
    app = App("policy-app")

    @app.queue(name="alpha", cpu="cpu3c-1-2")
    def alpha():
        pass

    @getattr(app, kind)(name="zulu", cpu="cpu3c-1-2", env={key: "test-value"})
    def zulu():
        pass

    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    with patch(
        "runpod.apps.deploy.build_environment",
        return_value=BuildResult(env_dir=vendor_dir),
    ) as vendor:
        with pytest.raises(InvalidResourceError, match=key):
            await deploy_app(app, tmp_path, api=deployment_api)
        vendor.assert_not_called()
    deployment_api.upload_tarball.assert_not_awaited()
    deployment_api.deploy_build.assert_not_awaited()
    deployment_api.save_endpoint.assert_not_awaited()
    deployment_api.delete_endpoint.assert_not_awaited()
    assert not deployment_api.method_calls
    with pytest.raises(InvalidResourceError, match=key):
        _deployed_endpoint_input(app, zulu.spec, "env", "build", "3.12")


def test_endpoint_reserved_env_error_identifies_keys_without_values():
    app = App("policy-app")

    @app.queue(
        name="worker",
        cpu="cpu3c-1-2",
        env={"RUNPOD_API_KEY": "test-value", "RUNPOD_AI_API_KEY": "other-value"},
    )
    def worker():
        pass

    with pytest.raises(InvalidResourceError) as exc:
        _deployed_endpoint_input(app, worker.spec, "env", "build", "3.12")
    assert "RUNPOD_API_KEY" in str(exc.value)
    assert "RUNPOD_AI_API_KEY" in str(exc.value)
    assert "test-value" not in str(exc.value)
    assert "other-value" not in str(exc.value)


@pytest.mark.parametrize("timeout", [0, -1, True, "1", float("inf"), float("nan")])
def test_deployed_task_rejects_invalid_timeout_before_create(timeout):
    api = AsyncMock()
    with pytest.raises(ValueError, match="timeout"):
        DeployedTaskExecution("app", "env", "work", api, timeout=timeout)
    api.create.assert_not_called()


@pytest.mark.parametrize("timeout", [None, 0.5, 10])
async def test_deployed_task_accepts_positive_or_omitted_timeout(timeout):
    api = AsyncMock()
    api.create.return_value = {"podId": "pod"}
    execution = DeployedTaskExecution("app", "env", "work", api, timeout=timeout)
    await execution.start()
    request = api.create.await_args.args[0]
    if timeout is None:
        assert "executionTimeoutSeconds" not in request
    else:
        assert request["executionTimeoutSeconds"] == timeout


def test_transport_still_accepts_zero_timeout():
    _validate_timeout(0)


@pytest.mark.parametrize("kind", [None, "queue", "task"])
async def test_finalize_selects_task_policy_only_for_task_manifests(kind):
    api = AppsApiClient(api_key="test-key")
    manifest = {"resources": [] if kind is None else [{"name": "work", "kind": kind}]}
    response = {"id": "build"}
    with patch.object(
        api,
        "_execute",
        AsyncMock(return_value={"finalizeFlashArtifactUpload": response}),
    ) as execute:
        assert await api.finalize_artifact_upload("app", "key", manifest) == response
    query, variables = execute.await_args.args
    assert ("resourceSpec" in query) is (kind == "task")
    assert variables["input"]["manifest"] == manifest


async def test_taskless_deployment_accepts_finalize_without_task_policy(
    tmp_path, deployment_api
):
    app = App("policy-app")

    @app.queue(name="worker", cpu="cpu3c-1-2")
    def worker():
        pass

    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    with patch(
        "runpod.apps.deploy.build_environment",
        return_value=BuildResult(env_dir=vendor_dir, excluded=["torch"]),
    ):
        result = await deploy_app(app, tmp_path, api=deployment_api)
    assert result.endpoints == {"worker": "endpoint"}
    deployment_api.deploy_build.assert_awaited_once_with("env", "build")
    manifest = deployment_api.finalize_artifact_upload.await_args.args[2]
    assert manifest["excludedPackages"] == ["torch"]


def test_build_artifact_preserves_excluded_packages(tmp_path):
    app = App("policy-app")

    @app.task(name="work", cpu="cpu3c-1-2")
    def work():
        pass

    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    with patch(
        "runpod.apps.deploy.build_environment",
        return_value=BuildResult(env_dir=vendor_dir, excluded=["torch"]),
    ):
        artifact = build_artifact(app, tmp_path)
    with tarfile.open(artifact) as tar:
        manifest = json.load(tar.extractfile("runpod_manifest.json"))
    assert manifest["excludedPackages"] == ["torch"]


@pytest.mark.parametrize("volume_type", [GlobalVolume, NetworkVolume])
async def test_deploy_resolves_shared_task_endpoint_volume_once(
    tmp_path, deployment_api, volume_type
):
    app = App("policy-app")
    volume = volume_type("shared")

    @app.task(name="work", gpu="4090", mounts={"/data": volume})
    def work():
        pass

    @app.queue(name="worker", gpu="4090", mounts={"/runpod-volume": volume})
    def worker():
        pass

    deployment_api.list_global_volumes.return_value = [
        {"id": "volume", "name": "shared"}
    ]
    deployment_api.list_network_volumes.return_value = [
        {"id": "volume", "name": "shared", "dataCenter": "EU-RO-1"}
    ]
    deployment_api.gpu_stock_status.return_value = "High"
    deployment_api.finalize_artifact_upload.return_value["resourceSpec"] = {
        "tasks": {"work": {"imageName": "approved"}}
    }
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    with (
        patch(
            "runpod.apps.deploy.build_environment",
            return_value=BuildResult(env_dir=vendor_dir),
        ),
        patch("runpod.apps.placement.solve_placement", wraps=solve_placement) as solve,
    ):
        await deploy_app(app, tmp_path, api=deployment_api)
    manifest = deployment_api.finalize_artifact_upload.await_args.args[2]
    task = next(r for r in manifest["resources"] if r["kind"] == "task")
    assert task["mounts"][0]["id"] == "volume"
    endpoint = deployment_api.save_endpoint.await_args.args[0]
    if volume_type is GlobalVolume:
        deployment_api.list_global_volumes.assert_awaited_once()
        solve.assert_not_called()
        assert endpoint["volumes"] == [
            {"volumeId": "volume", "volumeType": "OBJECT_STORE_VOLUME"}
        ]
    else:
        deployment_api.list_network_volumes.assert_awaited_once()
        solve.assert_called_once()
        assert endpoint["networkVolumeIds"] == [{"networkVolumeId": "volume"}]
        assert endpoint["locations"] == "EU-RO-1"
