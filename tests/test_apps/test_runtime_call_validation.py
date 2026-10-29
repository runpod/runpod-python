"""runtime scope enforcement and task argument compatibility."""

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

import runpod
from runpod.apps import App
from runpod.apps.api import FlashTaskApiClient
from runpod.apps.app import _clear_registry
from runpod.apps.targets import DeployedTaskTarget, PodTarget, SentinelTarget


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    _clear_registry()
    for key in (
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
        monkeypatch.delenv(key, raising=False)
    yield
    _clear_registry()


@pytest.fixture(params=["queue", "task"])
def production(request, monkeypatch):
    for key, value in {
        "RUNPOD_RUNTIME_KIND": request.param,
        "RUNPOD_BUILD_ID": "build",
        "RUNPOD_ENDPOINT_ID": "endpoint",
        "RUNPOD_POD_ID": "pod",
        "RUNPOD_AI_API_KEY": "endpoint-key",
        "RUNPOD_API_KEY": "pod-key",
        "FLASH_APP_NAME": "my-app",
        "FLASH_ENVIRONMENT": "prod",
        "FLASH_RESOURCE_NAME": "caller",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(runpod, "api_key", "account-key")
    return request.param


@pytest.mark.parametrize("kind", ["queue", "queue-spawn", "api", "task", "task-spawn"])
@pytest.mark.parametrize("app,env", [("other-app", "prod"), ("my-app", "staging")])
async def test_cross_scope_stubs_fail_before_network_request(
    production, kind, app, env
):
    with (
        patch("runpod.apps.targets._request_json", AsyncMock()) as sentinel,
        patch("runpod.apps.api.run_graphql_query_async", AsyncMock()) as graphql,
    ):
        with pytest.raises(ValueError, match="same app and environment"):
            if kind == "api":
                await runpod.Api(app=app, env=env, name="work").post.aio("/work", {})
            elif kind.startswith("queue"):
                stub = runpod.Queue(app=app, env=env, name="work")
                await (stub.spawn if kind.endswith("spawn") else stub.remote).aio()
            else:
                stub = runpod.Task(app=app, env=env, name="work")
                await (stub.spawn if kind.endswith("spawn") else stub.remote).aio()
        sentinel.assert_not_awaited()
        graphql.assert_not_awaited()


async def test_same_scope_targets_use_runtime_credentials(production):
    key = "endpoint-key" if production == "queue" else "pod-key"
    headers = SentinelTarget("my-app", "prod", "work")._sentinel_headers()
    assert headers["Authorization"] == f"Bearer {key}"
    execute = AsyncMock(return_value={"data": {"flashTaskCreate": {"podId": "child"}}})
    with patch("runpod.apps.api.run_graphql_query_async", execute):
        await FlashTaskApiClient().create(
            {
                "flashAppName": "my-app",
                "flashEnvironmentName": "prod",
                "resourceName": "work",
            }
        )
    assert execute.await_args.kwargs["api_key"] == key


@pytest.mark.parametrize("dev", [False, True])
async def test_account_clients_can_target_other_apps_and_environments(monkeypatch, dev):
    monkeypatch.setenv("RUNPOD_API_KEY", "account-key")
    if dev:
        monkeypatch.setenv("RUNPOD_DEV_SESSION", "dev-session")
        monkeypatch.setenv("RUNPOD_DEV_APP", "my-app")
    headers = SentinelTarget("other-app", "staging", "work")._sentinel_headers()
    assert headers["Authorization"] == "Bearer account-key"
    assert "X-RunPod-Endpoint-Id" not in headers
    assert "X-RunPod-Pod-Id" not in headers
    execute = AsyncMock(return_value={"data": {"flashTaskCreate": {"podId": "child"}}})
    with patch("runpod.apps.api.run_graphql_query_async", execute):
        await FlashTaskApiClient().create(
            {
                "flashAppName": "other-app",
                "flashEnvironmentName": "staging",
                "resourceName": "work",
            }
        )
    assert execute.await_args.kwargs["api_key"] == "account-key"
    assert execute.await_args.kwargs["headers"] is None


@dataclass
class Argument:
    value: int


@pytest.mark.parametrize(
    "value", [b"payload", Argument(1), datetime(2026, 1, 1), {1, 2}, float("nan")]
)
@pytest.mark.parametrize("method", ["invoke", "submit"])
async def test_deployed_task_reports_consistent_json_error_before_creation(
    value, method
):
    target = DeployedTaskTarget("app", "env", "work")
    with patch("runpod.apps.api.run_graphql_query_async", AsyncMock()) as execute:
        with pytest.raises(
            ValueError, match="^tasks accept only JSON args and kwargs$"
        ):
            await getattr(target, method)({"args": [value], "kwargs": {}})
        execute.assert_not_awaited()


@pytest.mark.parametrize("dev", [False, True])
@pytest.mark.parametrize(
    "value", [b"private-payload", Argument(1), datetime(2026, 1, 1)]
)
async def test_source_tasks_reject_non_json_arguments(monkeypatch, dev, value):
    if dev:
        monkeypatch.setenv("RUNPOD_DEV_SESSION", "dev-session")
    app = App("app")

    @app.task(name="work", cpu="cpu3c-1-2")
    def work(value):
        return value

    target = await app._resolve(work.spec)
    assert isinstance(target, PodTarget)
    with pytest.raises(
        ValueError, match="^tasks accept only JSON args and kwargs$"
    ) as exc:
        target.build_payload(work._fn, work.spec, (value,), {})
    assert "private-payload" not in str(exc.value)
    assert work.local(value) == value


def test_source_tasks_use_json_arguments_and_results():
    app = App("app")

    @app.task(name="work", cpu="cpu3c-1-2")
    def work(value):
        return value

    value = {"items": [1, "two", True, None]}
    payload = PodTarget(work.spec, work._fn).build_payload(
        work._fn, work.spec, (value,), {}
    )
    assert payload["args"] == [value]
    assert payload["serialization_format"] == "json"
