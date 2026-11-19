"""interrupted deployment rollback and recovery diagnostics."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import reconcile_endpoints


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.parametrize("interrupt", [asyncio.CancelledError, KeyboardInterrupt])
async def test_interrupt_restores_build_and_updates_then_propagates(interrupt, caplog):
    app = App("demo")
    for name in ("alpha", "beta"):
        app.queue(name=name, cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    api.snapshot_endpoint.side_effect = [
        {"id": "ep-alpha", "name": "alpha"},
        {"id": "ep-beta", "name": "beta"},
    ]
    api.save_endpoint.side_effect = [{"id": "ep-alpha"}, interrupt(), {}, {}]
    api.get_environment_by_name.return_value = {"activeBuildId": "new-build"}
    environment = {
        "id": "env",
        "name": "prod",
        "activeBuildId": "old-build",
        "endpoints": [{"id": f"ep-{name}", "name": name} for name in ("alpha", "beta")],
    }
    with pytest.raises(interrupt):
        await reconcile_endpoints(
            api, app, environment, "new-build", "3.12", activate_build=True
        )
    assert api.deploy_build.await_args_list[-1].args == ("env", "old-build")
    assert [call.args[0]["id"] for call in api.save_endpoint.await_args_list[-2:]] == [
        "ep-beta",
        "ep-alpha",
    ]
    assert "Changes rolled back." in caplog.text


@pytest.mark.parametrize("delete_succeeds", [True, False])
async def test_repeated_cancellation_waits_for_lost_create_cleanup(
    delete_succeeds, caplog
):
    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    creating = asyncio.Event()
    deleting = asyncio.Event()
    finish_cleanup = asyncio.Event()

    async def save(payload):
        creating.set()
        await asyncio.Future()

    async def delete(endpoint_id):
        deleting.set()
        await finish_cleanup.wait()
        return delete_succeeds

    api.save_endpoint.side_effect = save
    api.delete_endpoint.side_effect = delete
    api.get_environment_by_name.return_value = {
        "activeBuildId": "new-build",
        "endpoints": [
            {"id": "ep-created", "template": {"name": "demo-worker-template-token"}}
        ],
    }
    with patch("runpod.apps.deploy.secrets.token_hex", return_value="token"):
        deployment = asyncio.create_task(
            reconcile_endpoints(
                api,
                app,
                {"id": "env", "name": "prod", "activeBuildId": "old-build"},
                "new-build",
                "3.12",
                activate_build=True,
            )
        )
        await asyncio.wait_for(creating.wait(), timeout=2)
        deployment.cancel()
        await asyncio.wait_for(deleting.wait(), timeout=2)
        deployment.cancel()
        await asyncio.sleep(0)
        assert not deployment.done()
        finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(deployment, timeout=2)
    api.delete_endpoint.assert_awaited_once_with("ep-created")
    api.deploy_build.assert_awaited_with("env", "old-build")
    if delete_succeeds:
        assert "Changes rolled back." in caplog.text
    else:
        assert "Rollback incomplete:" in caplog.text
        assert "rp flash env get prod --app demo" in caplog.text
        assert "Remaining workers may still incur charges." in caplog.text


async def test_cancellation_preserves_other_deployment(caplog):
    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    api.save_endpoint.side_effect = asyncio.CancelledError()
    api.get_environment_by_name.return_value = {"activeBuildId": "other-build"}
    with pytest.raises(asyncio.CancelledError):
        await reconcile_endpoints(
            api,
            app,
            {"id": "env", "name": "prod", "activeBuildId": "old-build"},
            "new-build",
            "3.12",
            activate_build=True,
        )
    api.deploy_build.assert_awaited_once_with("env", "new-build")
    api.delete_endpoint.assert_not_awaited()
    assert "environment changed during deployment" in caplog.text
    assert "rp flash env get prod --app demo" in caplog.text
