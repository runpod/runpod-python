"""tests for the queue bootstrap and custom-image support."""

import base64
import json
import textwrap

import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.dev import _endpoint_input
from runpod.runtimes import bootstrap


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class TestLocatePhase:
    def test_missing_artifact_fails_with_phase(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bootstrap, "ARTIFACT_PATH", str(tmp_path / "nope.tar.gz"))
        monkeypatch.setattr(bootstrap, "ARTIFACT_WAIT_SECONDS", 0)
        monkeypatch.setattr(bootstrap, "APP_DIR", str(tmp_path / "app"))
        with pytest.raises(bootstrap.PhaseError) as exc_info:
            bootstrap._locate()
        assert exc_info.value.phase == "locate"

    def test_extracts_artifact(self, tmp_path, monkeypatch):
        import tarfile

        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("x = 1")
        artifact = tmp_path / "artifact.tar.gz"
        with tarfile.open(artifact, "w:gz") as tar:
            tar.add(src / "main.py", arcname="main.py")

        app_dir = tmp_path / "app"
        monkeypatch.setattr(bootstrap, "ARTIFACT_PATH", str(artifact))
        monkeypatch.setattr(bootstrap, "APP_DIR", str(app_dir))
        assert bootstrap._locate() == str(app_dir)
        assert (app_dir / "main.py").read_text() == "x = 1"

    def test_unpack_happens_once(self, tmp_path, monkeypatch):
        import tarfile

        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("x = 1")
        artifact = tmp_path / "artifact.tar.gz"
        with tarfile.open(artifact, "w:gz") as tar:
            tar.add(src / "main.py", arcname="main.py")

        app_dir = tmp_path / "app"
        monkeypatch.setattr(bootstrap, "ARTIFACT_PATH", str(artifact))
        monkeypatch.setattr(bootstrap, "APP_DIR", str(app_dir))
        bootstrap._locate()
        # second boot on a warm container: marker short-circuits
        artifact.unlink()
        assert bootstrap._locate() == str(app_dir)

    def test_prebuilt_dir_skips_unpack(self, tmp_path, monkeypatch):
        prebuilt = tmp_path / "prebuilt"
        prebuilt.mkdir()
        monkeypatch.setattr(bootstrap, "PREBUILT_APP_DIR", str(prebuilt))
        # no artifact anywhere; the host-provided tree wins outright
        monkeypatch.setattr(bootstrap, "ARTIFACT_PATH", str(tmp_path / "no.tar.gz"))
        assert bootstrap._locate() == str(prebuilt)

    def test_rejects_traversal(self, tmp_path, monkeypatch):
        import io
        import tarfile

        artifact = tmp_path / "evil.tar.gz"
        with tarfile.open(artifact, "w:gz") as tar:
            data = b"evil"
            info = tarfile.TarInfo(name="../../etc/evil")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        monkeypatch.setattr(bootstrap, "ARTIFACT_PATH", str(artifact))
        monkeypatch.setattr(bootstrap, "APP_DIR", str(tmp_path / "app"))
        with pytest.raises(bootstrap.PhaseError) as exc_info:
            bootstrap._locate()
        assert exc_info.value.phase == "locate"


class TestAttachPhase:
    def test_env_dir_leads_path_order(self, tmp_path):
        (tmp_path / "env").mkdir()
        paths = bootstrap._attach(str(tmp_path))
        assert paths == [str(tmp_path / "env"), str(tmp_path)]

    def test_no_env_dir_falls_back_to_source_only(self, tmp_path):
        paths = bootstrap._attach(str(tmp_path))
        assert paths == [str(tmp_path)]

    def test_manifest_read(self, tmp_path):
        (tmp_path / "runpod_manifest.json").write_text(
            json.dumps({"resources": [{"name": "q1"}]})
        )
        manifest = bootstrap._manifest(str(tmp_path))
        assert manifest["resources"][0]["name"] == "q1"

    def test_manifest_missing_fails_with_phase(self, tmp_path):
        with pytest.raises(bootstrap.PhaseError) as exc_info:
            bootstrap._manifest(str(tmp_path))
        assert exc_info.value.phase == "attach"


class TestSystemPhase:
    def test_no_system_deps_no_op(self):
        bootstrap._install_system({"resources": [{"name": "q"}]})

    def test_missing_apt_fails_with_phase(self, monkeypatch):
        import shutil

        monkeypatch.setenv("FLASH_RESOURCE_NAME", "q")
        monkeypatch.setattr(shutil, "which", lambda _: None)
        manifest = {
            "resources": [{"name": "q", "systemDependencies": ["ffmpeg"]}]
        }
        with pytest.raises(bootstrap.PhaseError) as exc_info:
            bootstrap._install_system(manifest)
        assert exc_info.value.phase == "system"
        assert "ffmpeg" in str(exc_info.value)

    def test_installs_resource_system_deps(self, monkeypatch):
        import shutil

        monkeypatch.setenv("FLASH_RESOURCE_NAME", "q")
        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/apt-get")
        calls = []

        class R:
            returncode = 0
            stderr = ""

        monkeypatch.setattr(
            bootstrap.subprocess,
            "run",
            lambda cmd, **kw: calls.append(cmd) or R(),
        )
        manifest = {
            "resources": [{"name": "q", "systemDependencies": ["ffmpeg", "sox"]}]
        }
        bootstrap._install_system(manifest)
        assert calls[0][:2] == ["apt-get", "update"]
        assert calls[1][:3] == ["apt-get", "install", "-y"]
        assert "ffmpeg" in calls[1] and "sox" in calls[1]

    def test_other_resources_deps_ignored(self, monkeypatch):
        monkeypatch.setenv("FLASH_RESOURCE_NAME", "q")
        manifest = {
            "resources": [{"name": "other", "systemDependencies": ["ffmpeg"]}]
        }
        # q has no system deps; must not attempt anything
        bootstrap._install_system(manifest)


class TestVerifyPhase:
    def test_no_exclusions_no_op(self):
        bootstrap._verify_excluded({"resources": []})

    def test_present_exclusions_no_install(self, monkeypatch):
        installed = []
        monkeypatch.setattr(
            bootstrap, "_pip_install", lambda pkgs, phase: installed.extend(pkgs)
        )
        # json is definitely importable; stands in for torch-in-image
        bootstrap._verify_excluded({"excludedPackages": ["json"]})
        assert installed == []

    def test_missing_exclusions_installed(self, monkeypatch):
        installed = []
        monkeypatch.setattr(
            bootstrap, "_pip_install", lambda pkgs, phase: installed.extend(pkgs)
        )
        bootstrap._verify_excluded(
            {"excludedPackages": ["definitely-not-installed-xyz"]}
        )
        assert installed == ["definitely-not-installed-xyz"]


class TestCustomImagePayloads:
    def test_queue_custom_image_gets_bootstrap(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2", image="pytorch/pytorch:latest")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        template = payload["template"]
        assert template["imageName"] == "pytorch/pytorch:latest"
        assert "bootstrap.py" in template["dockerArgs"]
        env = {e["key"]: e["value"] for e in template["env"]}
        decoded = base64.b64decode(env["RUNPOD_BOOTSTRAP_B64"]).decode()
        assert "def _locate" in decoded

    def test_queue_default_image_no_bootstrap(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        template = payload["template"]
        from runpod.apps.images import local_python_version

        assert template["imageName"].startswith(
            f"runpod/queue:py{local_python_version()}-"
        )
        assert template["dockerArgs"] == ""
        env = {e["key"]: e["value"] for e in template["env"]}
        assert "RUNPOD_BOOTSTRAP_B64" not in env

    def test_api_custom_image_supported(self):
        app = App("a")

        @app.api(name="api", cpu="cpu3c-1-2", image="my/server:1")
        class Api:
            @runpod.post("/x")
            def x(self, body: dict):
                return body

        payload = _endpoint_input(app, Api.spec)
        assert payload["template"]["imageName"] == "my/server:1"

    def test_image_in_manifest(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2", image="custom:1")
        def q():
            pass

        assert q.spec.to_manifest()["imageName"] == "custom:1"


class TestBootstrapIsStdlibOnly:
    def test_no_third_party_imports(self):
        import ast
        from pathlib import Path

        source = (
            Path(bootstrap.__file__).read_text()
        )
        tree = ast.parse(source)
        # module-level imports only: function-local imports (the runpod
        # availability probe in _ensure_runtime) run after installation
        imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imports.add(node.module.split(".")[0])
        stdlib = {
            "json", "os", "subprocess", "sys", "tarfile", "time",
            "urllib", "io", "importlib", "http",
        }
        assert imports <= stdlib, f"non-stdlib imports: {imports - stdlib}"
