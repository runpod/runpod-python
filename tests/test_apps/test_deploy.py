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


    @pytest.mark.parametrize(
        "hardware", [{"gpu": "ADA_24"}, {"cpu": "cpu3c-1-2"}, {"cpu": "cpu5c-2-4"}]
    )
    def test_unlisted_datacenter_reaches_locations(self, hardware):
        app = App("dc-app")

        @app.queue(name="q", datacenter="XX-TEST-1", **hardware)
        def q():
            pass

        payload = _deployed_endpoint_input(app, q.spec, "env-1", "build-1", "3.12")
        assert payload["locations"] == "XX-TEST-1"


class TestPackaging:
    def test_tarball_contains_source_and_manifest(self, tmp_path):
        _write_project(tmp_path)
        (tmp_path / "runpod_manifest.json").write_text('{"app": "forged"}')
        manifest = {"version": 1, "app": "demo-app", "resources": []}
        tar_path = package_project(tmp_path, manifest)

        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
            assert "main.py" in names
            assert "runpod_manifest.json" in names
            extracted = json.load(tar.extractfile("runpod_manifest.json"))
            assert extracted["app"] == "demo-app"

    def test_ignores_and_non_overridable_credentials(self, tmp_path):
        _write_project(tmp_path)
        for name in ("secret.env", ".env.production", "private.pem"):
            (tmp_path / name).write_text("private")
        (tmp_path / "local.txt").write_text("local")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__/main.pyc").write_text("cache")
        (tmp_path / ".runpodignore").write_text(
            "!*.env\n!.env.production\n!private.pem\nlocal.txt\n"
        )

        with tarfile.open(package_project(tmp_path, {})) as tar:
            assert set(tar.getnames()) == {
                "main.py",
                ".runpodignore",
                "runpod_manifest.json",
            }

    def test_credential_like_source_paths_are_included(self, tmp_path):
        names = (
            "credentials.py",
            "secrets.py",
            "credentials",
            "secrets",
            "nested/secrets/config.py",
            "nested/credentials/config.py",
        )
        for name in names:
            path = tmp_path / "src" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("source")
        with tarfile.open(package_project(tmp_path, {})) as tar:
            for name in names:
                assert f"src/{name}" in tar.getnames()

    def test_excluded_paths_are_logged_without_contents(self, tmp_path, caplog):
        for name in (".env", "private.key", "private.pem", "local.txt", ".aws/config"):
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("sensitive-file-contents")
        (tmp_path / ".runpodignore").write_text("!*.key\n!.aws/\nlocal.txt\n")

        with tarfile.open(package_project(tmp_path, {})) as tar:
            assert set(tar.getnames()) == {".runpodignore", "runpod_manifest.json"}
        for name in (".env", "private.key", "private.pem", "local.txt", ".aws/"):
            assert (
                f"excluded source path {name!r} from deployment artifact" in caplog.text
            )
        assert "sensitive-file-contents" not in caplog.text

    def test_vendored_env_included_under_env(self, tmp_path):
        _write_project(tmp_path)
        env_dir = tmp_path / "built-env"
        (env_dir / "numpy").mkdir(parents=True)
        (env_dir / "numpy" / "__init__.py").write_text("")
        (env_dir / "numpy" / "cacert.pem").write_text("dependency CA")
        (tmp_path / ".runpodignore").write_text("*.pem\n")

        tar_path = package_project(
            tmp_path, {"version": 1, "resources": []}, env_dir=env_dir
        )
        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
            assert "env/numpy/__init__.py" in names
            # env dir under project root must not be double-added as source
            assert "built-env/numpy/__init__.py" not in names
            assert "main.py" in names
            assert tar.extractfile("env/numpy/cacert.pem").read() == b"dependency CA"

    def test_project_ignore_precedence(self, tmp_path):
        for name in (
            "root.log",
            "nested/keep.log",
            "nested/drop.log",
            "other/keep.log",
        ):
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        (tmp_path / ".gitignore").write_text("*.log\n")
        (tmp_path / "nested/.gitignore").write_text("!*.log\n")
        (tmp_path / ".runpodignore").write_text("!root.log\nnested/drop.log\n")

        with tarfile.open(package_project(tmp_path, {})) as tar:
            assert set(tar.getnames()) == {
                ".gitignore",
                ".runpodignore",
                "nested/.gitignore",
                "root.log",
                "nested/keep.log",
                "runpod_manifest.json",
            }

    def test_symlinks_cannot_escape_source_or_dependencies(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "main.py").write_text("source")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("private")
        env_dir = tmp_path / "built-env"
        env_dir.mkdir()
        for root in (project, env_dir):
            (root / "linked.py").symlink_to(outside / "secret.txt")
            (root / "linked-dir").symlink_to(outside, target_is_directory=True)

        with tarfile.open(package_project(project, {}, env_dir=env_dir)) as tar:
            assert set(tar.getnames()) == {"main.py", "runpod_manifest.json"}


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
        api.finalize_artifact_upload.return_value = {
            "id": "build-1",
            "resourceSpec": {"tasks": {}},
        }
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
        api.finalize_artifact_upload.return_value = {
            "id": "build-2",
            "resourceSpec": {"tasks": {}},
        }

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
        api.finalize_artifact_upload.return_value = {
            "id": "build-1",
            "resourceSpec": {"tasks": {}},
        }
        api.save_endpoint.return_value = {"id": "ep-1"}
        with _stub_build(tmp_path):
            with pytest.raises(InvalidResourceError, match="FLASH_ENVIRONMENT"):
                await deploy_app(app, tmp_path, env_name="prod", api=api)
            api.get_app_by_name.assert_not_awaited()
            api.deploy_build.assert_not_awaited()
            api.save_endpoint.assert_not_awaited()
            handle.spec.env = {}
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


class TestManifestKeepsEnvValuesOut:
    PLAIN_VALUE = "hf_plain_value_from_os_environ"

    def _app_with_env(self):
        app = App("env-app")

        @app.queue(name="qee", cpu="cpu5c-2-4", env={"HF_TOKEN": self.PLAIN_VALUE})
        def qee(x):
            return x

        @app.api(name="web", cpu="cpu5c-2-4", env={"API_SECRET": self.PLAIN_VALUE})
        def web():
            return {}

        @app.task(name="job", cpu="cpu3c-1-2", env={"MODE": "fast"})
        def job():
            pass

        return app

    def test_endpoint_env_is_left_out_of_the_manifest(self, tmp_path):
        manifest = build_manifest(self._app_with_env(), tmp_path)

        endpoints = [r for r in manifest["resources"] if r["kind"] != "task"]
        assert len(endpoints) == 2
        assert all("env" not in r for r in endpoints)
        assert self.PLAIN_VALUE not in json.dumps(manifest)

    def test_task_env_stays_in_the_manifest_for_the_backend(self, tmp_path):
        manifest = build_manifest(self._app_with_env(), tmp_path)

        (task,) = [r for r in manifest["resources"] if r["kind"] == "task"]
        assert task["env"] == {"MODE": "fast"}

    def test_reserved_endpoint_env_is_still_rejected(self, tmp_path):
        app = App("env-app")

        @app.queue(name="qee", cpu="cpu5c-2-4", env={"RUNPOD_ENDPOINT_ID": "x"})
        def qee(x):
            return x

        with pytest.raises(InvalidResourceError, match="RUNPOD_ENDPOINT_ID"):
            build_manifest(app, tmp_path)

    def test_packaged_manifest_has_no_env_values(self, tmp_path):
        manifest = {
            "version": 1,
            "app": "env-app",
            "resources": [
                {"kind": "task", "name": "job", "env": {"MODE": self.PLAIN_VALUE}}
            ],
        }

        with tarfile.open(package_project(tmp_path, manifest)) as tar:
            packaged = json.load(tar.extractfile("runpod_manifest.json"))

        assert packaged["resources"] == [{"kind": "task", "name": "job"}]
        assert manifest["resources"][0]["env"] == {"MODE": self.PLAIN_VALUE}

    @pytest.mark.parametrize(
        "name", ["HF_TOKEN", "OPENAI_API_KEY", "DB_PASSWORD", "app_secret"]
    )
    def test_secret_like_plain_value_warns(self, tmp_path, caplog, name):
        app = App("env-app")

        @app.queue(name="qee", cpu="cpu5c-2-4", env={name: self.PLAIN_VALUE})
        def qee(x):
            return x

        build_manifest(app, tmp_path)

        assert name in caplog.text
        assert "runpod.Secret" in caplog.text
        assert self.PLAIN_VALUE not in caplog.text

    @pytest.mark.parametrize(
        "env",
        [
            {"HF_TOKEN": runpod.Secret("hf-token")},
            {"MODE": "fast", "KEYBOARD": "us"},
        ],
    )
    def test_secret_references_and_ordinary_names_do_not_warn(
        self, tmp_path, caplog, env
    ):
        app = App("env-app")

        @app.queue(name="qee", cpu="cpu5c-2-4", env=env)
        def qee(x):
            return x

        build_manifest(app, tmp_path)

        assert "runpod.Secret" not in caplog.text


class TestDeployedTemplateNames:
    """Template names are unique per owner, and a deleted endpoint holds its
    template for a while, so new endpoints must not reuse a fixed name."""

    @staticmethod
    def _app():
        app = App("demo-app")

        @app.queue(name="que1", cpu="cpu5c-2-4")
        def que1(x: int):
            return x

        return app

    @staticmethod
    def _client():
        client = AsyncMock()
        client.save_endpoint.side_effect = lambda payload: {
            "id": payload.get("id", "ep-new")
        }
        return client

    @staticmethod
    def _saved_template_name(client) -> str:
        (payload,), _ = client.save_endpoint.await_args
        return payload["template"]["name"]

    async def _reconcile(self, environment):
        from runpod.apps.deploy import reconcile_endpoints

        client = self._client()
        await reconcile_endpoints(client, self._app(), environment, "b-1", "3.12")
        return client

    async def test_new_endpoint_template_name_has_unique_suffix(self):
        environment = {"id": "env-1", "name": "default", "endpoints": []}

        first = self._saved_template_name(await self._reconcile(environment))
        second = self._saved_template_name(await self._reconcile(environment))

        assert first.startswith("demo-app-que1-template-")
        assert second.startswith("demo-app-que1-template-")
        assert first != second

    async def test_update_keeps_the_existing_template_name(self):
        environment = {
            "id": "env-1",
            "name": "default",
            "endpoints": [
                {"id": "ep-1", "name": "que1", "template": {"name": "kept-name"}}
            ],
        }

        client = await self._reconcile(environment)

        (payload,), _ = client.save_endpoint.await_args
        assert payload["id"] == "ep-1"
        assert payload["template"]["name"] == "kept-name"

    async def test_update_without_template_info_uses_the_legacy_name(self):
        environment = {
            "id": "env-1",
            "name": "default",
            "endpoints": [{"id": "ep-1", "name": "que1", "template": None}],
        }

        client = await self._reconcile(environment)

        assert self._saved_template_name(client) == "demo-app-que1-template"

    @pytest.mark.parametrize(
        "query_name", ["QUERY_FLASH_APP_BY_NAME", "QUERY_FLASH_ENVIRONMENT_BY_NAME"]
    )
    def test_environment_queries_select_the_bound_template_name(self, query_name):
        from runpod.api.queries import apps as app_queries

        query = " ".join(getattr(app_queries, query_name).split())

        assert "endpoints { id name template { name } }" in query
