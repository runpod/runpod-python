"""partial deployment diagnostics and preflight validation."""

import io
from unittest.mock import AsyncMock, Mock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.build import BuildResult
from runpod.apps.deploy import deploy_app, reconcile_endpoints
from runpod.apps.errors import EndpointReconciliationError, InvalidResourceError
from runpod.apps.gpu import CpuInstanceType
from runpod.rp_cli import console as ui
from runpod.rp_cli.main import cli


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def make_app():
    app = App("partial")
    for name in ("c_later", "b_bad", "a_ok"):
        app.queue(name=name, cpu="cpu3c-1-2")(lambda x: x)
    return app


def environment(endpoints=None):
    return {"id": "env-1", "name": "prod", "endpoints": endpoints or []}


@pytest.mark.parametrize("updating", [False, True])
@pytest.mark.parametrize(
    "cause", [RuntimeError("quota exceeded"), TimeoutError("timed out")]
)
async def test_reports_confirmed_changes_and_failed_request(updating, cause):
    api = AsyncMock()
    api.save_endpoint.side_effect = [{"id": "ep-ok"}, cause]
    existing = [{"name": "a_ok", "id": "ep-ok"}] if updating else []
    existing.append({"name": "obsolete", "id": "ep-old"})

    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api, make_app(), environment(existing), "build-1", "3.12"
        )

    message = str(caught.value)
    assert caught.value.__cause__ is cause
    assert "partial/prod (environment env-1, build build-1)" in message
    status = (
        "updated endpoints (not rolled back)"
        if updating
        else "rolled back created endpoints"
    )
    assert f"{status}: a_ok (ep-ok)" in message
    assert f"failed to create b_bad: {cause}" in message
    assert "deleted endpoints (not restored)" not in message
    assert "response was lost" in message
    assert "keep billing" in message
    assert "rp flash env get prod --app partial" in message
    assert [call.args[0]["name"] for call in api.save_endpoint.await_args_list] == [
        "a_ok",
        "b_bad",
    ]
    if updating:
        api.delete_endpoint.assert_not_awaited()
    else:
        api.delete_endpoint.assert_awaited_once_with("ep-ok")


async def test_first_update_failure_identifies_existing_endpoint():
    api = AsyncMock()
    api.save_endpoint.side_effect = RuntimeError("rejected")
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api,
            make_app(),
            environment([{"name": "a_ok", "id": "ep-old"}]),
            "build-1",
            "3.12",
        )
    assert "failed to update a_ok (ep-old): rejected" in str(caught.value)
    assert "rolled back created endpoints" not in str(caught.value)
    api.delete_endpoint.assert_not_awaited()
    assert api.save_endpoint.await_count == 1


async def test_preparation_failure_reports_earlier_changes():
    api = AsyncMock()
    api.save_endpoint.return_value = {"id": "ep-ok"}
    with patch(
        "runpod.apps.volume.attach_endpoint_volumes",
        AsyncMock(side_effect=[None, RuntimeError("volume unavailable")]),
    ):
        with pytest.raises(EndpointReconciliationError) as caught:
            await reconcile_endpoints(api, make_app(), environment(), "build-1", "3.12")
    assert "rolled back created endpoints: a_ok (ep-ok)" in str(caught.value)
    assert "failed to prepare b_bad: volume unavailable" in str(caught.value)
    assert "response was lost" not in str(caught.value)
    api.save_endpoint.assert_awaited_once()
    api.delete_endpoint.assert_awaited_once_with("ep-ok")


async def test_deletion_failure_reports_creates_updates_and_deletes():
    api = AsyncMock()
    api.save_endpoint.side_effect = lambda payload: {"id": f"ep-{payload['name']}"}
    api.delete_endpoint.side_effect = [True, RuntimeError("delete denied"), True, True]
    existing = [
        {"name": name, "id": f"ep-{name}"} for name in ("b_bad", "old1", "old2", "old3")
    ]
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api, make_app(), environment(existing), "build-1", "3.12"
        )
    message = str(caught.value)
    assert (
        "rolled back created endpoints: c_later (ep-c_later), a_ok (ep-a_ok)" in message
    )
    assert "updated endpoints (not rolled back): b_bad (ep-b_bad)" in message
    assert "deleted endpoints (not restored): old1 (ep-old1)" in message
    assert "failed to delete old2 (ep-old2): delete denied" in message
    assert [call.args for call in api.delete_endpoint.await_args_list] == [
        ("ep-old1",),
        ("ep-old2",),
        ("ep-c_later",),
        ("ep-a_ok",),
    ]


@pytest.mark.parametrize("cleanup_result", [RuntimeError("cleanup denied"), False])
async def test_rollback_continues_after_cleanup_failure(cleanup_result):
    api = AsyncMock()
    cause = RuntimeError("quota exceeded")
    api.save_endpoint.side_effect = [{"id": "ep-a"}, {"id": "ep-b"}, cause]
    api.delete_endpoint.side_effect = [cleanup_result, True]
    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(api, make_app(), environment(), "build-1", "3.12")
    message = str(caught.value)
    assert caught.value.__cause__ is cause
    assert "failed to create c_later: quota exceeded" in message
    assert "rollback failed for b_bad (ep-b)" in message
    assert "created endpoints still requiring cleanup: b_bad (ep-b)" in message
    assert "rolled back created endpoints: a_ok (ep-a)" in message
    assert "keep billing" in message
    assert [call.args for call in api.delete_endpoint.await_args_list] == [
        ("ep-b",),
        ("ep-a",),
    ]


async def test_success_keeps_created_endpoints():
    api = AsyncMock()
    api.save_endpoint.side_effect = lambda payload: {"id": f"ep-{payload['name']}"}
    result = await reconcile_endpoints(
        api, make_app(), environment(), "build-1", "3.12"
    )
    assert result == {"a_ok": "ep-a_ok", "b_bad": "ep-b_bad", "c_later": "ep-c_later"}
    api.delete_endpoint.assert_not_awaited()


def test_cli_reports_partial_deploy_and_failed_phase(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text(
        'from runpod import App\napp = App("partial")\n'
        '@app.queue(name="a_ok", cpu="cpu3c-1-2")\n'
        "def a_ok(x): return x\n"
        '@app.queue(name="b_bad", cpu="cpu3c-1-2")\n'
        "def b_bad(x): return x\n"
    )
    env_dir = tmp_path / "vendor"
    env_dir.mkdir()
    api = AsyncMock()
    api.get_app_by_name.return_value = {
        "id": "app-1",
        "flashEnvironments": [environment()],
    }
    api.prepare_artifact_upload.return_value = {
        "uploadUrl": "https://upload.invalid",
        "objectKey": "key",
    }
    api.finalize_artifact_upload.return_value = {"id": "build-1"}
    api.save_endpoint.side_effect = [{"id": "ep-ok"}, RuntimeError("quota exceeded")]
    buffer = io.StringIO()
    monkeypatch.setattr(ui, "console", Console(file=buffer, theme=ui.theme, width=120))
    with (
        patch("runpod.apps.deploy.AppsApiClient", return_value=api),
        patch(
            "runpod.apps.deploy.build_environment",
            return_value=BuildResult(env_dir, []),
        ),
    ):
        result = CliRunner().invoke(
            cli, ["flash", "deploy", str(tmp_path), "-e", "prod"]
        )
    assert result.exit_code == 1, result.output
    assert "build build-1 was activated on partial/prod" in result.output
    assert "rolled back created endpoints: a_ok (ep-ok)" in result.output
    assert "failed to create b_bad: quota exceeded" in result.output
    assert "✗ reconciling endpoints" in buffer.getvalue()
    assert "✓ reconciling endpoints" not in buffer.getvalue()
    assert "is live" not in buffer.getvalue()
    api.deploy_build.assert_awaited_once_with("env-1", "build-1")
    api.delete_endpoint.assert_awaited_once_with("ep-ok")


@pytest.mark.parametrize("kind", ["queue", "api"])
@pytest.mark.parametrize("timeout", [0, -1, 3601, 5000, 99999, 1.5, True, "60", None])
def test_idle_timeout_rejected_at_decoration(kind, timeout):
    app = App("invalid")
    with pytest.raises(InvalidResourceError, match="idle_timeout"):
        getattr(app, kind)(name="worker", cpu="cpu3c-1-2", idle_timeout=timeout)(
            lambda: None
        )


@pytest.mark.parametrize("timeout", [1, 3600])
@pytest.mark.parametrize("cpu", list(CpuInstanceType))
def test_accepts_timeout_boundaries_and_supported_cpu_flavors(timeout, cpu):
    app = App("valid")
    handle = app.queue(name="worker", cpu=cpu, idle_timeout=timeout)(lambda: None)
    assert handle.spec.idle_timeout == timeout
    assert handle.spec.cpu == [cpu.value]


@pytest.mark.parametrize("kind", ["queue", "api", "task"])
@pytest.mark.parametrize("cpu", ["cpu9z", ["cpu3c-1-2", "cpu9z"]])
def test_cpu_flavor_rejected_at_decoration(kind, cpu):
    app = App("invalid")
    with pytest.raises(
        InvalidResourceError, match="unsupported cpu instance ids: cpu9z"
    ):
        getattr(app, kind)(name="worker", cpu=cpu)(lambda: None)


@pytest.mark.parametrize("field,value", [("idle_timeout", 5000), ("cpu", ["cpu9z"])])
async def test_mutated_invalid_spec_fails_before_build_and_remote_calls(
    tmp_path, field, value
):
    app = make_app()
    setattr(app.resources["b_bad"].spec, field, value)
    api = AsyncMock()
    build = Mock()
    with patch("runpod.apps.deploy.build_environment", build):
        with pytest.raises(InvalidResourceError):
            await deploy_app(app, tmp_path, api=api)
    build.assert_not_called()
    assert not api.mock_calls
