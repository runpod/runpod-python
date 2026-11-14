"""cpu compatibility and deployment rollback safety."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import reconcile_endpoints
from runpod.apps.errors import EndpointReconciliationError, InvalidResourceError


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.parametrize("kind", ["queue", "api", "task"])
@pytest.mark.parametrize("cpu", ["cpu5g-4-16", "cpu3c-16-32", "cpu9z-32-64"])
def test_cpu_instance_shapes_are_not_limited_to_enum_members(kind, cpu):
    app = App("demo")
    handle = getattr(app, kind)(name="worker", cpu=cpu)(lambda: None)
    assert handle.spec.cpu == [cpu]


@pytest.mark.parametrize(
    "cpu",
    ["cpu5g", "cpu5g-0-16", "cpu5g-4-0", "cpu5g-4", "cpu5g-4-16-extra", " cpu5g-4-16"],
)
def test_malformed_cpu_instance_ids_fail_before_deployment(cpu):
    app = App("demo")
    with pytest.raises(InvalidResourceError, match="unknown cpu"):
        app.queue(name="worker", cpu=cpu)(lambda: None)


async def test_asyncio_timeout_recovers_created_endpoint():
    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    error = asyncio.TimeoutError("response lost")
    api.save_endpoint.side_effect = error
    api.get_environment_by_name.return_value = {
        "endpoints": [
            {
                "id": "ep-created",
                "name": "worker",
                "template": {"name": "demo-worker-template-token"},
            }
        ]
    }
    with patch("runpod.apps.deploy.secrets.token_hex", return_value="token"):
        with pytest.raises(EndpointReconciliationError) as caught:
            await reconcile_endpoints(
                api, app, {"id": "env", "name": "prod"}, "new-build", "3.12"
            )
    assert caught.value.__cause__ is error
    assert (
        str(caught.value)
        == "Couldn't deploy 'worker': response lost\nChanges rolled back."
    )
    api.delete_endpoint.assert_awaited_once_with("ep-created")


@pytest.mark.parametrize("environment_created", [False, True])
@pytest.mark.parametrize("current", [{"activeBuildId": "other-build"}, {}, None])
async def test_unconfirmed_ownership_prevents_rollback_writes(
    current, environment_created
):
    app = App("demo")
    for name in ("alpha", "beta", "gamma"):
        app.queue(name=name, cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    api.snapshot_endpoint.return_value = {"id": "ep-alpha", "name": "alpha"}
    api.save_endpoint.side_effect = [
        {"id": "ep-alpha"},
        {"id": "ep-beta"},
        RuntimeError("quota exceeded"),
    ]
    api.get_environment_by_name.return_value = current
    environment = {
        "id": "env",
        "name": "prod",
        "activeBuildId": None if environment_created else "old-build",
        "endpoints": (
            [] if environment_created else [{"id": "ep-alpha", "name": "alpha"}]
        ),
    }
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api,
            app,
            environment,
            "new-build",
            "3.12",
            activate_build=True,
            environment_created=environment_created,
        )
    message = str(caught.value)
    assert "Rollback incomplete:" in message
    assert "Changes rolled back." not in message
    assert "rp flash env get prod --app demo" in message
    assert "may still incur charges" in message
    if current:
        assert "environment changed during deployment" in message
    else:
        assert "activation status unknown" in message
    api.deploy_build.assert_awaited_once_with("env", "new-build")
    assert api.save_endpoint.await_count == 3
    api.delete_endpoint.assert_not_awaited()
    api.delete_environment.assert_not_awaited()
