"""tests for discovery, manifest building, and packaging."""

import json
import tarfile
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.context import Context
from runpod.apps.deploy import (
    DeployResult,
    _deployed_endpoint_input,
    build_manifest,
    deploy_app,
    package_project,
)
from runpod.apps.discovery import DiscoveryError, discover_apps
from runpod.apps.errors import InvalidResourceError, ScheduleNotSupported


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def _write_project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text(textwrap.dedent("""
            import runpod
            from runpod import App

            app = App("demo-app")

            @app.queue(name="que1", cpu="cpu5c-2-4")
            def que1(x: int):
                return x * 2

            if __name__ == "__main__":
                raise SystemExit("main guard must not run during discovery")
            """))
    return tmp_path


class TestDiscovery:
    def test_discovers_app(self, tmp_path):
        _write_project(tmp_path)
        apps = discover_apps(tmp_path)
        assert len(apps) == 1
        assert apps[0].name == "demo-app"
        assert "que1" in apps[0].resources

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

        @app.queue(name="qee", cpu="cpu5c-2-4", dependencies=["numpy"])
        def qee(x):
            return x

        manifest = build_manifest(app, tmp_path)
        assert manifest["app"] == "m-app"
        assert manifest["version"] == 1
        (resource,) = manifest["resources"]
        assert resource["kind"] == "queue"
        assert resource["name"] == "qee"
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


class TestEndpointInput:
    def test_custom_api_declares_runtime_port(self):
        app = App("api-app")

        @app.api(name="api", cpu="cpu3c-1-2", image="python:3.12-slim")
        class Api:
            @runpod.get("/value")
            def value(self):
                return {"value": 1}

        payload = _deployed_endpoint_input(app, Api.spec, "env-1", "build-1", "3.12")
        assert payload["type"] == "LB"
        assert payload["template"]["ports"] == "80/http"
        env = {entry["key"]: entry["value"] for entry in payload["template"]["env"]}
        assert env["PORT"] == "80"
        assert env["PORT_HEALTH"] == "80"


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

    def test_vendored_env_included_under_env(self, tmp_path):
        _write_project(tmp_path)
        env_dir = tmp_path / "built-env"
        (env_dir / "numpy").mkdir(parents=True)
        (env_dir / "numpy" / "__init__.py").write_text("")

        tar_path = package_project(
            tmp_path, {"version": 1, "resources": []}, env_dir=env_dir
        )
        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
            assert "env/numpy/__init__.py" in names
            # env dir under project root must not be double-added as source
            assert "built-env/numpy/__init__.py" not in names
            assert "main.py" in names

    def test_source_credentials_and_local_files_excluded_by_default(self, tmp_path):
        excluded = {
            ".env",
            ".env.local",
            "settings/dev.env",
            "settings/dev.env.backup",
            "keys/server.pem",
            "keys/server.key",
            ".ssh/id_rsa",
            "keys/id_ed25519",
            ".aws/credentials",
            ".azure/token",
            ".kube/config",
            ".docker/config.json",
            ".netrc",
            ".npmrc",
            ".pypirc",
            ".git-credentials",
            ".boto",
            "credentials.json",
            "secrets.yaml",
            "service-account-prod.json",
            "service_account.json",
            "tests/test_app.py",
            "test/unit.py",
            "test_app.py",
            "app_test.py",
            ".venv/lib/local.py",
            "venv/lib/local.py",
            "env/lib/local.py",
            ".pytest_cache/state",
            "__pycache__/main.pyc",
        }
        for name in excluded | {"main.py", "package/module.py", "data/input.json"}:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)

        with tarfile.open(package_project(tmp_path, {})) as tar:
            assert set(tar.getnames()) == {
                "main.py",
                "package/module.py",
                "data/input.json",
                "runpod_manifest.json",
            }

    def test_broad_negation_cannot_override_protected_paths(self, tmp_path):
        protected = {
            ".env.production",
            "keys/private.pem",
            "keys/private.key",
            ".git/config",
            ".runpod/cache",
            ".flash/cache",
            ".aws/credentials",
            "env/local.py",
            "runpod_manifest.json/forged.json",
        }
        for name in protected | {"tests/fixture.py", ".venv/local.py", "main.py"}:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        (tmp_path / ".runpodignore").write_text("!**\n")

        with tarfile.open(package_project(tmp_path, {"app": "generated"})) as tar:
            assert set(tar.getnames()) == {
                ".runpodignore",
                "tests/fixture.py",
                ".venv/local.py",
                "main.py",
                "runpod_manifest.json",
            }
            assert json.load(tar.extractfile("runpod_manifest.json")) == {
                "app": "generated"
            }

    def test_gitignore_scopes_and_runpod_precedence(self, tmp_path):
        for name in [
            "main.py",
            "root-only.txt",
            "nested/root-only.txt",
            "omit.log",
            "nested/omit.log",
            "nested/keep.log",
            "nested/hidden.txt",
            "nested/override.txt",
            "sibling/hidden.txt",
        ]:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        (tmp_path / ".gitignore").write_text("/root-only.txt\n*.log\n")
        (tmp_path / "nested/.gitignore").write_text(
            "hidden.txt\noverride.txt\n!keep.log\n"
        )
        (tmp_path / ".runpodignore").write_text(
            "!/root-only.txt\n!nested/override.txt\nnested/keep.log\n"
        )

        with tarfile.open(package_project(tmp_path, {})) as tar:
            assert set(tar.getnames()) == {
                ".gitignore",
                ".runpodignore",
                "nested/.gitignore",
                "main.py",
                "root-only.txt",
                "nested/root-only.txt",
                "nested/override.txt",
                "sibling/hidden.txt",
                "runpod_manifest.json",
            }

    def test_directory_patterns_require_parent_reinclusion(self, tmp_path):
        for name in [
            "data/keep.txt",
            "cache/keep.txt",
            "nested/data/drop.txt",
            "tests/fixture.py",
        ]:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        (tmp_path / "ordinary").write_text("not a directory")
        (tmp_path / ".gitignore").write_text("data/\ncache/\nordinary/\n")
        (tmp_path / ".runpodignore").write_text(
            "!data/keep.txt\n!cache/\ncache/*\n!cache/keep.txt\n!tests/\n"
        )

        with tarfile.open(package_project(tmp_path, {})) as tar:
            assert set(tar.getnames()) == {
                ".gitignore",
                ".runpodignore",
                "cache/keep.txt",
                "ordinary",
                "tests/fixture.py",
                "runpod_manifest.json",
            }

    def test_ignore_escaping_and_double_star(self, tmp_path):
        for name in [
            "#notes",
            "!notes",
            "trailing ",
            "assets/a/cache/x",
            "assets/cache/y",
            "assets/a/keep",
        ]:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        (tmp_path / ".runpodignore").write_text(
            "\\#notes\n\\!notes\ntrailing\\ \nassets/**/cache/\n"
        )

        with tarfile.open(package_project(tmp_path, {})) as tar:
            assert set(tar.getnames()) == {
                ".runpodignore",
                "assets/a/keep",
                "runpod_manifest.json",
            }

    def test_generated_entries_and_dependency_certificates_preserved(self, tmp_path):
        (tmp_path / "main.py").write_text("source")
        (tmp_path / "runpod_manifest.json").write_text('{"app": "forged"}')
        (tmp_path / "env").mkdir()
        (tmp_path / "env/local.py").write_text("local environment")
        env_dir = tmp_path / "built-env"
        for name in ["certifi/cacert.pem", "library/public.key", "package.py"]:
            path = env_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        (tmp_path / ".runpodignore").write_text("!**\n*.pem\n*.key\n")
        output = tmp_path / "build.bundle"

        package_project(tmp_path, {"app": "generated"}, output, env_dir)

        with tarfile.open(output) as tar:
            assert set(tar.getnames()) == {
                ".runpodignore",
                "main.py",
                "env/certifi/cacert.pem",
                "env/library/public.key",
                "env/package.py",
                "runpod_manifest.json",
            }
            assert tar.getnames().count("runpod_manifest.json") == 1
            assert json.load(tar.extractfile("runpod_manifest.json")) == {
                "app": "generated"
            }
            assert (
                tar.extractfile("env/certifi/cacert.pem").read()
                == b"certifi/cacert.pem"
            )

    def test_links_are_not_followed_and_hardlinks_are_regular_members(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "main.py").write_text("source")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("private")
        (project / "external.py").symlink_to(outside / "secret.txt")
        (project / "external-dir").symlink_to(outside, target_is_directory=True)
        (project / "internal.py").symlink_to("main.py")
        (project / "broken.py").symlink_to("missing.py")
        (project / "hardlink.py").hardlink_to(project / "main.py")
        (outside / "ignore").write_text("*\n")
        (project / ".gitignore").symlink_to(outside / "ignore")
        (project / ".runpodignore").symlink_to(outside / "ignore")
        (tmp_path / ".gitignore").write_text("*\n")
        env_dir = tmp_path / "built-env"
        env_dir.mkdir()
        (env_dir / "package.py").write_text("dependency")
        (env_dir / "external.py").symlink_to(outside / "secret.txt")
        (env_dir / "external-dir").symlink_to(outside, target_is_directory=True)

        with tarfile.open(package_project(project, {}, env_dir=env_dir)) as tar:
            assert set(tar.getnames()) == {
                "main.py",
                "hardlink.py",
                "env/package.py",
                "runpod_manifest.json",
            }
            assert all(member.isfile() for member in tar.getmembers())
            assert tar.extractfile("hardlink.py").read() == b"source"


def _stub_build(tmp_path):
    """patch environment vendoring with a tiny fake env tree."""
    from runpod.apps.build import BuildResult

    env_dir = tmp_path / "fake-env"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "vendored_pkg.py").write_text("x = 1")
    return patch(
        "runpod.apps.deploy.build_environment",
        return_value=BuildResult(env_dir=env_dir, requirements=["runpod"]),
    )


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

        with _stub_build(tmp_path):
            result = await deploy_app(app, tmp_path, api=api)

        assert isinstance(result, DeployResult)
        assert result.build_id == "build-1"
        assert result.resources == ["que1"]
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

        with _stub_build(tmp_path):
            await deploy_app(app, tmp_path, api=api)

        api.create_app.assert_not_awaited()
        api.create_environment.assert_not_awaited()

    async def test_deploy_environment_is_used_for_nested_calls(
        self, tmp_path, monkeypatch
    ):
        _write_project(tmp_path)
        (app,) = discover_apps(tmp_path)
        handle = next(iter(app.resources.values()))
        handle.spec.env = {"FLASH_ENVIRONMENT": "stale"}
        api = AsyncMock()
        api.get_app_by_name.return_value = {
            "id": "app-1",
            "flashEnvironments": [{"id": "env-prod", "name": "prod"}],
        }
        api.prepare_artifact_upload.return_value = {
            "uploadUrl": "https://upload",
            "objectKey": "key-1",
        }
        api.finalize_artifact_upload.return_value = {"id": "build-1"}
        api.save_endpoint.return_value = {"id": "ep-1"}
        with _stub_build(tmp_path):
            await deploy_app(app, tmp_path, env_name="prod", api=api)
        payload = api.save_endpoint.await_args.args[0]
        worker_env = {
            entry["key"]: entry["value"] for entry in payload["template"]["env"]
        }
        monkeypatch.setenv("FLASH_ENVIRONMENT", worker_env["FLASH_ENVIRONMENT"])
        monkeypatch.setattr(runpod, "api_key", "test-key")
        monkeypatch.delenv("RUNPOD_DEV_APP", raising=False)
        with patch("runpod.apps.app.current_context", return_value=Context.WORKER):
            target = await app._resolve(handle.spec)
        assert target._sentinel_headers()["X-Flash-Environment"] == "prod"

    async def test_invalid_mutated_spec_fails_before_remote_deployment(self, tmp_path):
        _write_project(tmp_path)
        (app,) = discover_apps(tmp_path)
        next(iter(app.resources.values())).spec.workers = (5, 1)
        api = AsyncMock()
        with pytest.raises(InvalidResourceError):
            await deploy_app(app, tmp_path, api=api)
        api.create_app.assert_not_awaited()
        api.save_endpoint.assert_not_awaited()


class TestTolerantDiscovery:
    def test_broken_bystander_warns_but_discovers(self, tmp_path):
        _write_project(tmp_path)
        (tmp_path / "scratch.py").write_text("this is not python !!!")
        apps = discover_apps(tmp_path)
        assert len(apps) == 1

    def test_single_file_target_stays_strict(self, tmp_path):
        broken = tmp_path / "broken.py"
        broken.write_text("import nonexistent_module_xyz")
        with pytest.raises(DiscoveryError, match="broken.py"):
            discover_apps(broken)

    def test_no_apps_and_failures_raises_with_causes(self, tmp_path):
        (tmp_path / "broken.py").write_text("import nonexistent_module_xyz")
        with pytest.raises(DiscoveryError, match="broken.py"):
            discover_apps(tmp_path)

    def test_import_time_invocation_diagnosed(self, tmp_path):
        _write_project(tmp_path)
        (tmp_path / "client.py").write_text("from main import que1\nque1.remote(1)\n")
        # directory walk: the client file fails with the precise
        # diagnosis but the app still discovers
        apps = discover_apps(tmp_path)
        assert len(apps) == 1

    def test_import_time_invocation_message(self, tmp_path):
        _write_project(tmp_path)
        client = tmp_path / "client.py"
        client.write_text("from main import que1\nque1.remote(1)\n")
        with pytest.raises(DiscoveryError, match="invoked at import time"):
            discover_apps(client)
