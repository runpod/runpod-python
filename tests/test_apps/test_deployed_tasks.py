"""production task identity, lifecycle, and deployment contract tests."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import runpod
from runpod.apps import App, Task
from runpod.apps.api import FlashTaskApiClient
from runpod.apps.app import _clear_registry
from runpod.apps.build import BuildResult
from runpod.apps.deploy import (
    _deployed_endpoint_input,
    _prepare_task_resources,
    build_manifest,
    deploy_app,
)
from runpod.apps.errors import InvalidResourceError
from runpod.apps.targets import DeployedTaskTarget, PodTarget, _headers
from runpod.apps.tasks import DeployedTaskExecution, _pod_input
from runpod.apps.utils.network import runtime_headers
from runpod.apps.volume import GlobalVolume, NetworkVolume


@pytest.fixture(autouse=True)
def isolated_identity(monkeypatch):
    _clear_registry()
    for name in (
        "RUNPOD_RUNTIME_KIND",
        "RUNPOD_BUILD_ID",
        "RUNPOD_ENDPOINT_ID",
        "RUNPOD_POD_ID",
        "RUNPOD_AI_API_KEY",
        "RUNPOD_API_KEY",
        "FLASH_APP_NAME",
        "FLASH_ENVIRONMENT",
        "FLASH_RESOURCE_NAME",
        "RUNPOD_DEV_APP",
        "RUNPOD_DEV_SESSION",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    _clear_registry()


@pytest.fixture
def production(monkeypatch):
    values = {
        "RUNPOD_RUNTIME_KIND": "queue",
        "RUNPOD_BUILD_ID": "build-1",
        "FLASH_APP_NAME": "my-app",
        "FLASH_ENVIRONMENT": "prod",
        "FLASH_RESOURCE_NAME": "work",
        "RUNPOD_ENDPOINT_ID": "ep-1",
        "RUNPOD_POD_ID": "worker-pod",
        "RUNPOD_AI_API_KEY": "endpoint-key",
        "RUNPOD_API_KEY": "must-not-use-account-key",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_endpoint_auth_emits_only_endpoint_source(production):
    headers = _headers()
    assert headers["Authorization"] == "Bearer endpoint-key"
    assert headers["X-RunPod-Endpoint-Id"] == "ep-1"
    assert "X-RunPod-Pod-Id" not in headers


def test_task_auth_emits_only_pod_source(production, monkeypatch):
    monkeypatch.setenv("RUNPOD_RUNTIME_KIND", "task")
    monkeypatch.setenv("RUNPOD_API_KEY", "child-key")
    assert runtime_headers() == {
        "Authorization": "Bearer child-key",
        "X-RunPod-Pod-Id": "worker-pod",
    }


@pytest.mark.parametrize("missing", ["RUNPOD_POD_ID", "RUNPOD_API_KEY"])
def test_task_auth_never_uses_endpoint_or_global_key(production, monkeypatch, missing):
    monkeypatch.setenv("RUNPOD_RUNTIME_KIND", "task")
    monkeypatch.setattr(runpod, "api_key", "account-key")
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError):
        runtime_headers()


def test_endpoint_auth_does_not_fall_back_to_pod_key(production, monkeypatch):
    monkeypatch.delenv("RUNPOD_AI_API_KEY")
    monkeypatch.setattr(runpod, "api_key", "account-key")
    with pytest.raises(RuntimeError, match="credential"):
        runtime_headers()


@pytest.mark.parametrize(
    "missing",
    [
        "RUNPOD_AI_API_KEY",
        "FLASH_APP_NAME",
        "FLASH_ENVIRONMENT",
        "FLASH_RESOURCE_NAME",
        "RUNPOD_BUILD_ID",
    ],
)
def test_missing_production_identity_never_falls_back(production, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError):
        _headers()


def test_dev_auth_retains_account_key(monkeypatch):
    monkeypatch.setenv("RUNPOD_DEV_APP", "my-app")
    monkeypatch.setenv("RUNPOD_AI_API_KEY", "endpoint-key")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-1")
    monkeypatch.setenv("RUNPOD_API_KEY", "developer-key")
    headers = _headers()
    assert headers["Authorization"] == "Bearer developer-key"
    assert "X-RunPod-Endpoint-Id" not in headers


@pytest.mark.parametrize("image", [None, "custom/image:tag"])
def test_dev_task_payload_preserves_source_runtime(image):
    app = App("my-app")

    @app.task(name="work", cpu="cpu3c-1-2", image=image)
    def work():
        pass

    payload = _pod_input(work.spec, "execution-token", "work")
    env = {entry["key"]: entry["value"] for entry in payload["env"]}
    assert "RUNPOD_RUNTIME_KIND" not in env
    assert "RUNPOD_BUILD_ID" not in env
    assert "FLASH_APP_NAME" not in env
    if image:
        assert "runpod_sdk_runtime.task.runner" in payload["dockerArgs"]


async def test_graphql_create_and_terminate_use_exact_contract(production):
    api = FlashTaskApiClient()
    create_input = {
        "flashAppName": "my-app",
        "flashEnvironmentName": "prod",
        "resourceName": "work",
        "executionToken": "execution-token",
    }
    execute = AsyncMock(
        side_effect=[
            {
                "data": {
                    "flashTaskCreate": {
                        "podId": "child",
                        "flashBuildId": "build",
                        "resourceName": "work",
                    }
                }
            },
            {"data": {"flashTaskTerminate": True}},
        ]
    )
    with patch("runpod.apps.api.run_graphql_query_async", execute):
        assert (await api.create(create_input))["podId"] == "child"
        await api.terminate("child", "execution-token")
    create, terminate = execute.await_args_list
    assert "FlashTaskCreateInput!" in create.args[0]
    assert create.kwargs["variables"] == {"input": create_input}
    assert create.kwargs["api_key"] == "endpoint-key"
    assert create.kwargs["headers"]["X-RunPod-Endpoint-Id"] == "ep-1"
    assert terminate.kwargs["variables"] == {
        "input": {"podId": "child", "executionToken": "execution-token"}
    }


async def test_explicit_local_task_uses_account_named_api(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "local-key")
    execute = AsyncMock(return_value={"data": {"flashTaskCreate": {"podId": "child"}}})
    with patch("runpod.apps.api.run_graphql_query_async", execute):
        await FlashTaskApiClient().create({"resourceName": "work"})
    assert execute.await_args.kwargs["api_key"] == "local-key"
    assert execute.await_args.kwargs["headers"] is None


async def test_task_target_selection_is_production_only(production, monkeypatch):
    app = App("my-app")

    @app.task(name="work", cpu="cpu3c-1-2")
    def work(value):
        return value

    target = await app._resolve(work.spec)
    assert isinstance(target, DeployedTaskTarget)
    assert target.env_name == "prod"
    assert target.build_payload(work._fn, work.spec, (1,), {"other": 2}) == {
        "args": [1],
        "kwargs": {"other": 2},
    }
    for name in ("RUNPOD_BUILD_ID", "FLASH_APP_NAME", "FLASH_RESOURCE_NAME"):
        monkeypatch.delenv(name)
    assert isinstance(await app._resolve(work.spec), PodTarget)
    monkeypatch.setenv("RUNPOD_DEV_SESSION", "session")
    assert type(await app._resolve(work.spec)) is PodTarget


async def test_invalid_production_task_identity_cannot_use_source_target(
    production, monkeypatch
):
    app = App("my-app")

    @app.task(name="work", cpu="cpu3c-1-2")
    def work():
        pass

    monkeypatch.delenv("FLASH_APP_NAME")
    with pytest.raises(RuntimeError, match="identity"):
        await app._resolve(work.spec)


@pytest.mark.parametrize("sync", [False, True])
def test_task_self_remote_launches_child(production, sync):
    app = App("my-app")

    @app.task(name="work", cpu="cpu3c-1-2")
    def work(value):
        raise AssertionError("task body must not run inline")

    target = DeployedTaskTarget("my-app", "prod", "work")
    target.invoke = AsyncMock(return_value="child-result")
    with patch.object(app, "_resolve", AsyncMock(return_value=target)):
        result = work.remote(1) if sync else asyncio.run(work.remote.aio(1))
    assert result == "child-result"
    target.invoke.assert_awaited_once_with({"args": [1], "kwargs": {}})


def test_production_queue_self_optimization_checks_app(production):
    other = App("other-app")

    @other.queue(name="work", cpu="cpu3c-1-2")
    def work():
        pass

    assert not work._is_current_worker()
    assert work._app.name != "my-app"


async def test_task_stub_remote_and_spawn_are_json_only():
    stub = Task(app="my-app", env="prod", name="work")
    assert runpod.Task is Task
    target = stub._target()
    target.invoke = AsyncMock(return_value=3)
    target.submit = AsyncMock(return_value="job")
    with patch.object(stub, "_target", return_value=target):
        assert await stub.remote.aio(1, value=2) == 3
        assert await stub.spawn.aio(1, value=2) == "job"
    target.invoke.assert_awaited_once_with({"args": [1], "kwargs": {"value": 2}})
    target.submit.assert_awaited_once_with({"args": [1], "kwargs": {"value": 2}})


@pytest.mark.parametrize(
    "payload",
    [
        {"args": [], "kwargs": {}, "function_code": "bad"},
        {"args": "pickle", "kwargs": {}},
        {"args": [object()], "kwargs": {}},
        {"args": [float("nan")], "kwargs": {}},
        {"args": [], "kwargs": {1: "bad"}},
    ],
)
async def test_deployed_payload_rejects_overrides_and_non_json(payload):
    target = DeployedTaskTarget("my-app", "prod", "work")
    with pytest.raises((ValueError, TypeError)):
        await target.submit(payload)


async def test_cancellation_retains_pending_create_and_cleans_success():
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def create(task_input):
        entered.set()
        await finish.wait()
        return {"podId": "child"}

    api = AsyncMock()
    api.create.side_effect = create
    execution = DeployedTaskExecution("my-app", "prod", "work", api)
    target = DeployedTaskTarget("my-app", "prod", "work")
    with patch.object(target, "_execution", return_value=execution):
        invocation = asyncio.create_task(target.submit({"args": [], "kwargs": {}}))
        await entered.wait()
        invocation.cancel()
        await asyncio.sleep(0)
        assert not invocation.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(invocation, timeout=5)
    api.create.assert_awaited_once()
    api.terminate.assert_awaited_once_with("child", execution.token)
    assert execution.pod_id is None


async def test_create_failure_is_not_retried_or_source_fallback():
    api = AsyncMock()
    api.create.side_effect = RuntimeError("approval missing")
    execution = DeployedTaskExecution("my-app", "prod", "work", api)
    target = DeployedTaskTarget("my-app", "prod", "work")
    with patch.object(target, "_execution", return_value=execution):
        with pytest.raises(RuntimeError, match="approval missing"):
            await target.invoke({"args": [], "kwargs": {}})
    api.create.assert_awaited_once()
    api.terminate.assert_not_awaited()


async def test_execution_creation_contains_only_named_identity_and_timeout():
    api = AsyncMock()
    api.create.return_value = {"podId": "child"}
    execution = DeployedTaskExecution("my-app", "prod", "work", api, timeout=12.5)
    await execution.start()
    api.create.assert_awaited_once_with(
        {
            "flashAppName": "my-app",
            "flashEnvironmentName": "prod",
            "resourceName": "work",
            "executionToken": execution.token,
            "executionTimeoutSeconds": 12.5,
        }
    )
    assert execution._headers == {"Authorization": f"Bearer {execution.token}"}
    await execution.terminate()


def test_manifest_pins_task_image_and_full_storage_references(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNPOD_RUNTIME_TAG", "release")
    app = App("my-app")

    @app.task(
        name="work",
        gpu="4090",
        mounts={
            "/data": NetworkVolume("data", size=75, datacenter="EU-RO-1", create=False),
            "/global": GlobalVolume("shared", create=True),
        },
    )
    def work():
        pass

    resource = build_manifest(app, tmp_path, python_version="3.12")["resources"][0]
    assert resource["imageName"] == "runpod/task-gpu:py3.12-release"
    assert resource["mounts"] == [
        {
            "kind": "network",
            "reference": "data",
            "path": "/data",
            "create": False,
            "size": 75,
            "datacenter": "EU-RO-1",
        },
        {"kind": "global", "reference": "shared", "path": "/global", "create": True},
    ]
    assert resource["module"]
    assert resource["qualname"].endswith("work")


def test_deployed_endpoint_uses_runtime_identity_without_account_key(monkeypatch):
    monkeypatch.setattr(runpod, "api_key", "developer-key")
    app = App("my-app")

    @app.queue(name="work", cpu="cpu3c-1-2")
    def work():
        pass

    payload = _deployed_endpoint_input(app, work.spec, "env", "build", "3.12")
    env = {e["key"]: e["value"] for e in payload["template"]["env"]}
    assert env["FLASH_APP_NAME"] == "my-app"
    assert "RUNPOD_API_KEY" not in env
    assert env["RUNPOD_BUILD_ID"] == "build"
    assert env["FLASH_RESOURCE_NAME"] == "work"
    assert env["FLASH_ENVIRONMENT"] == "default"
    work.spec.env = {"RUNPOD_AI_API_KEY": "override"}
    with pytest.raises(InvalidResourceError, match="reserved"):
        _deployed_endpoint_input(app, work.spec, "env", "build", "3.12")


async def test_task_storage_and_registry_are_resolved_with_deployer_client(tmp_path):
    app = App("my-app")

    @app.task(
        name="work",
        cpu="cpu3c-1-2",
        image="private/image:tag",
        registry_auth="private",
        mounts={"/data": NetworkVolume("data", size=75, create=True)},
    )
    def work():
        pass

    manifest = build_manifest(app, tmp_path)
    api = AsyncMock()
    api.list_registry_auths.return_value = [{"id": "registry-id", "name": "private"}]
    bindings = [
        {
            "id": "volume-id",
            "kind": "network",
            "reference": "data",
            "path": "/data",
            "dataCenterId": "EU-RO-1",
        }
    ]
    with patch(
        "runpod.apps.volume.VolumeResolver.resolve_mounts",
        AsyncMock(return_value=bindings),
    ):
        await _prepare_task_resources(app, manifest, api)
    resource = manifest["resources"][0]
    assert resource["mounts"][0]["id"] == "volume-id"
    assert resource["mounts"][0]["reference"] == "data"
    assert resource["mounts"][0]["size"] == 75
    assert resource["registryAuth"] == "registry-id"
    assert resource["customImage"] is True
    api.list_registry_auths.assert_awaited_once()


@pytest.mark.parametrize(
    "definitions,approved",
    [
        (None, False),
        ({}, False),
        ({"tasks": {}}, False),
        ({"tasks": {"work": None}}, False),
        ({"tasks": {"work": {}}}, False),
        ({"tasks": {"other": {"name": "other"}}}, False),
        ({"tasks": {"work": {"name": "work"}, "extra": {"name": "extra"}}}, False),
        ({"tasks": {"work": {"name": "work"}}}, True),
    ],
)
async def test_task_only_deployment_requires_definitions_without_launching(
    tmp_path, definitions, approved
):
    app = App("my-app")

    @app.task(name="work", cpu="cpu3c-1-2")
    def work():
        pass

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
    api.finalize_artifact_upload.return_value["resourceSpec"] = definitions
    env_dir = tmp_path / "vendor"
    env_dir.mkdir()
    with patch(
        "runpod.apps.deploy.build_environment",
        return_value=BuildResult(env_dir=env_dir, requirements=[]),
    ):
        if approved:
            result = await deploy_app(app, tmp_path, api=api)
            assert result.resources == ["work"]
            assert result.endpoints == {}
            api.deploy_build.assert_awaited_once_with("env", "build")
        else:
            with pytest.raises(RuntimeError, match="acknowledge"):
                await deploy_app(app, tmp_path, api=api)
            api.deploy_build.assert_not_awaited()
    api.save_endpoint.assert_not_awaited()
    api.deploy_task_pod.assert_not_awaited()
