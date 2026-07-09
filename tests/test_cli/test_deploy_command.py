"""tests for rp deploy and rp dev command wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from runpod.apps.deploy import DeployResult
from runpod.rp_cli.main import cli


@pytest.fixture(autouse=True)
def clean_entrypoints():
    from runpod.apps.entrypoint import _clear_entrypoints

    _clear_entrypoints()
    yield
    _clear_entrypoints()

APP_SOURCE = '''
import runpod

app = runpod.App("demo")


@app.queue(name="chat", gpu="4090")
def chat(prompt: str):
    return prompt
'''

TWO_APP_SOURCE = APP_SOURCE + '''

other = runpod.App("other")


@other.queue(name="embed", gpu="4090")
def embed(text: str):
    return text
'''

ENTRYPOINT_SOURCE = APP_SOURCE + '''

@runpod.local_entrypoint
def main():
    pass
'''


def _runner():
    return CliRunner()


def _result(name="demo", endpoints=None):
    return DeployResult(
        app_name=name,
        build_id="b1",
        environment_id="env1",
        resources=["chat"],
        endpoints=endpoints or {"chat": "ep1"},
    )


class TestDeploy:
    def test_deploys_discovered_app(self, tmp_path, monkeypatch):
        (tmp_path / "main.py").write_text(APP_SOURCE)
        monkeypatch.chdir(tmp_path)
        deploy_app = AsyncMock(return_value=_result())
        with patch("runpod.apps.deploy.deploy_app", deploy_app):
            result = _runner().invoke(cli, ["deploy"])
        assert result.exit_code == 0, result.output
        assert "demo/default" in result.output
        assert "ep1" in result.output
        deploy_app.assert_awaited_once()

    def test_env_flag(self, tmp_path, monkeypatch):
        (tmp_path / "main.py").write_text(APP_SOURCE)
        monkeypatch.chdir(tmp_path)
        deploy_app = AsyncMock(return_value=_result())
        with patch("runpod.apps.deploy.deploy_app", deploy_app):
            result = _runner().invoke(cli, ["deploy", "--env", "prod"])
        assert result.exit_code == 0
        assert deploy_app.call_args[1]["env_name"] == "prod"
        assert "demo/prod" in result.output

    def test_multi_app_plan(self, tmp_path, monkeypatch):
        (tmp_path / "main.py").write_text(TWO_APP_SOURCE)
        monkeypatch.chdir(tmp_path)
        deploy_app = AsyncMock(
            side_effect=[_result("demo"), _result("other", {"embed": "ep2"})]
        )
        with patch("runpod.apps.deploy.deploy_app", deploy_app):
            result = _runner().invoke(cli, ["deploy"])
        assert result.exit_code == 0, result.output
        assert "2 apps" in result.output
        assert deploy_app.await_count == 2

    def test_missing_target(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = _runner().invoke(cli, ["deploy", "missing_dir"])
        assert result.exit_code == 1
        assert "does not exist" in result.output

    def test_no_apps_found(self, tmp_path, monkeypatch):
        (tmp_path / "main.py").write_text("x = 1\n")
        monkeypatch.chdir(tmp_path)
        result = _runner().invoke(cli, ["deploy"])
        assert result.exit_code == 1
        assert "no runpod.App found" in result.output

    def test_engine_error_is_clean(self, tmp_path, monkeypatch):
        (tmp_path / "main.py").write_text(APP_SOURCE)
        monkeypatch.chdir(tmp_path)
        deploy_app = AsyncMock(side_effect=RuntimeError("upload failed"))
        with patch("runpod.apps.deploy.deploy_app", deploy_app):
            result = _runner().invoke(cli, ["deploy"])
        assert result.exit_code == 1
        assert "upload failed" in result.output
        assert "Traceback" not in result.output

    def test_build_only(self, tmp_path, monkeypatch):
        (tmp_path / "main.py").write_text(APP_SOURCE)
        monkeypatch.chdir(tmp_path)
        artifact = tmp_path / "demo-artifact.tar.gz"
        artifact.write_bytes(b"x" * 100)
        build = MagicMock(return_value=artifact)
        with patch("runpod.apps.deploy.build_artifact", build):
            result = _runner().invoke(cli, ["deploy", "--build-only"])
        assert result.exit_code == 0, result.output
        assert "demo-artifact.tar.gz" in result.output
        assert build.call_args[1]["output"] == artifact

    def test_exclude_passthrough(self, tmp_path, monkeypatch):
        (tmp_path / "main.py").write_text(APP_SOURCE)
        monkeypatch.chdir(tmp_path)
        deploy_app = AsyncMock(return_value=_result())
        with patch("runpod.apps.deploy.deploy_app", deploy_app):
            result = _runner().invoke(
                cli, ["deploy", "--exclude", "torch,numpy"]
            )
        assert result.exit_code == 0
        assert deploy_app.call_args[1]["exclude"] == ["torch", "numpy"]

    def test_python_version_fallback_warns(self, tmp_path, monkeypatch):
        (tmp_path / "main.py").write_text(APP_SOURCE)
        monkeypatch.chdir(tmp_path)
        deploy_app = AsyncMock(return_value=_result())
        with (
            patch("runpod.apps.deploy.deploy_app", deploy_app),
            patch(
                "runpod.apps.images.local_python_version",
                side_effect=RuntimeError("3.9 unsupported"),
            ),
        ):
            result = _runner().invoke(cli, ["deploy"])
        assert result.exit_code == 0
        assert "no runtime image" in result.output


class TestDev:
    def test_once_runs_entrypoint_and_stops(self, tmp_path, monkeypatch):
        module = tmp_path / "main.py"
        module.write_text(ENTRYPOINT_SOURCE)
        monkeypatch.chdir(tmp_path)

        session = MagicMock()
        session.start = AsyncMock()
        session.stop = AsyncMock()
        session.refresh = AsyncMock()
        session._endpoints = {"dev-demo-chat": "ep1"}

        def make_session(apps, events=None):
            session.apps = apps
            return session

        with patch("runpod.apps.dev.DevSession", side_effect=make_session):
            result = _runner().invoke(cli, ["dev", str(module), "--once"])
        assert result.exit_code == 0, result.output
        session.start.assert_awaited_once()
        session.stop.assert_awaited_once()

    def test_once_entrypoint_failure_exits_nonzero(self, tmp_path, monkeypatch):
        module = tmp_path / "main.py"
        module.write_text(
            APP_SOURCE
            + "\n@runpod.local_entrypoint\ndef main():\n"
            + "    raise RuntimeError('assertion blew up')\n"
        )
        monkeypatch.chdir(tmp_path)

        session = MagicMock()
        session.start = AsyncMock()
        session.stop = AsyncMock()
        session._endpoints = {}

        def make_session(apps, events=None):
            session.apps = apps
            return session

        with patch("runpod.apps.dev.DevSession", side_effect=make_session):
            result = _runner().invoke(cli, ["dev", str(module), "--once"])
        assert result.exit_code == 1
        session.stop.assert_awaited_once()

    def test_module_without_entrypoint(self, tmp_path, monkeypatch):
        module = tmp_path / "main.py"
        module.write_text(APP_SOURCE)
        monkeypatch.chdir(tmp_path)
        result = _runner().invoke(cli, ["dev", str(module), "--once"])
        assert result.exit_code == 1
        assert "local_entrypoint" in result.output

    def test_module_without_app(self, tmp_path, monkeypatch):
        module = tmp_path / "main.py"
        module.write_text("x = 1\n")
        monkeypatch.chdir(tmp_path)
        result = _runner().invoke(cli, ["dev", str(module), "--once"])
        assert result.exit_code == 1
        assert "no runpod.App" in result.output

    def test_missing_module(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = _runner().invoke(cli, ["dev", "missing.py", "--once"])
        assert result.exit_code == 2
