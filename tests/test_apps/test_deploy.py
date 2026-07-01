"""tests for discovery, manifest building, and packaging."""

import io
import json
import tarfile
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import (
    DeployResult,
    build_manifest,
    deploy_app,
    package_project,
)
from runpod.apps.discovery import DiscoveryError, discover_apps
from runpod.apps.errors import ScheduleNotSupported


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def _write_project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text(
        textwrap.dedent(
            """
            import runpod
            from runpod import App

            app = App("demo-app")

            @app.queue(name="q1", cpu="cpu5c-2-4")
            def q1(x: int):
                return x * 2

            if __name__ == "__main__":
                raise SystemExit("main guard must not run during discovery")
            """
        )
    )
    return tmp_path


class TestDiscovery:
    def test_discovers_app(self, tmp_path):
        _write_project(tmp_path)
        apps = discover_apps(tmp_path)
        assert len(apps) == 1
        assert apps[0].name == "demo-app"
        assert "q1" in apps[0].resources

    def test_single_file_target(self, tmp_path):
        _write_project(tmp_path)
        apps = discover_apps(tmp_path / "main.py")
        assert len(apps) == 1

    def test_main_guard_not_executed(self, tmp_path):
        _write_project(tmp_path)
        # would raise SystemExit if __main__ ran
        discover_apps(tmp_path)

    def test_import_error_reported(self, tmp_path):
        (tmp_path / "broken.py").write_text("import nonexistent_module_xyz")
        with pytest.raises(DiscoveryError, match="broken.py"):
            discover_apps(tmp_path)

    def test_skips_venv_dirs(self, tmp_path):
        _write_project(tmp_path)
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "bad.py").write_text("import nonexistent_module_xyz")
        apps = discover_apps(tmp_path)
        assert len(apps) == 1

    def test_non_python_file_rejected(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("hi")
        with pytest.raises(DiscoveryError):
            discover_apps(f)


class TestManifest:
    def test_manifest_shape(self, tmp_path):
        app = App("m-app")

        @app.queue(name="q", cpu="cpu5c-2-4", dependencies=["numpy"])
        def q(x):
            return x

        manifest = build_manifest(app, tmp_path)
        assert manifest["app"] == "m-app"
        assert manifest["version"] == 1
        (resource,) = manifest["resources"]
        assert resource["kind"] == "queue"
        assert resource["name"] == "q"
        assert resource["dependencies"] == ["numpy"]
        assert resource["qualname"]

    def test_schedule_blocked_until_backend_support(self, tmp_path):
        app = App("s-app")

        @app.task(name="t")
        @runpod.schedule(cron="0 * * * *")
        def t():
            pass

        with pytest.raises(ScheduleNotSupported):
            build_manifest(app, tmp_path)


class TestPackaging:
    def test_tarball_contains_source_and_manifest(self, tmp_path):
        _write_project(tmp_path)
        manifest = {"version": 1, "app": "demo-app", "resources": []}
        tar_path = package_project(tmp_path, manifest)

        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
            assert "main.py" in names
            assert "runpod_manifest.json" in names
            extracted = json.load(tar.extractfile("runpod_manifest.json"))
            assert extracted["app"] == "demo-app"

    def test_ignores_applied(self, tmp_path):
        _write_project(tmp_path)
        (tmp_path / "secret.env").write_text("KEY=1")
        (tmp_path / ".runpodignore").write_text("secret.env\n")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "x.pyc").write_text("junk")

        tar_path = package_project(tmp_path, {"version": 1, "resources": []})
        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
            assert "secret.env" not in names
            assert not any("__pycache__" in n for n in names)


class TestDeployPipeline:
    async def test_deploy_app_full_flow(self, tmp_path):
        _write_project(tmp_path)
        (app,) = discover_apps(tmp_path)

        api = AsyncMock()
        api.get_app_by_name.return_value = None
        api.create_app.return_value = {"id": "app-1", "flashEnvironments": []}
        api.create_environment.return_value = {"id": "env-1", "name": "default"}
        api.prepare_artifact_upload.return_value = {
            "uploadUrl": "https://upload",
            "objectKey": "key-1",
        }
        api.finalize_artifact_upload.return_value = {"id": "build-1"}
        api.deploy_build.return_value = {"id": "env-1"}

        result = await deploy_app(app, tmp_path, api=api)

        assert isinstance(result, DeployResult)
        assert result.build_id == "build-1"
        assert result.resources == ["q1"]
        api.upload_tarball.assert_awaited_once()
        api.deploy_build.assert_awaited_once_with("env-1", "build-1")

    async def test_deploy_reuses_existing_app_and_env(self, tmp_path):
        _write_project(tmp_path)
        (app,) = discover_apps(tmp_path)

        api = AsyncMock()
        api.get_app_by_name.return_value = {
            "id": "app-1",
            "flashEnvironments": [{"id": "env-1", "name": "default"}],
        }
        api.prepare_artifact_upload.return_value = {
            "uploadUrl": "https://upload",
            "objectKey": "key-1",
        }
        api.finalize_artifact_upload.return_value = {"id": "build-2"}

        await deploy_app(app, tmp_path, api=api)

        api.create_app.assert_not_awaited()
        api.create_environment.assert_not_awaited()
