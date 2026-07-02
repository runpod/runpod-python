"""tests for the queue bootstrap and custom-image support."""

import base64
import json
import textwrap

import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.dev import _endpoint_input
from runpod.runtimes.queue import bootstrap


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class TestBootstrapPhases:
    def test_unpack_missing_artifact_fails_with_phase(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bootstrap, "ARTIFACT_PATH", str(tmp_path / "nope.tar.gz"))
        monkeypatch.setattr(bootstrap, "ARTIFACT_WAIT_SECONDS", 0)
        with pytest.raises(bootstrap.PhaseError) as exc_info:
            bootstrap._unpack()
        assert exc_info.value.phase == "unpack"

    def test_unpack_extracts_artifact(self, tmp_path, monkeypatch):
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
        bootstrap._unpack()
        assert (app_dir / "main.py").read_text() == "x = 1"

    def test_unpack_rejects_traversal(self, tmp_path, monkeypatch):
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
            bootstrap._unpack()
        assert exc_info.value.phase == "unpack"

    def test_resource_entry_found(self, tmp_path, monkeypatch):
        manifest = {
            "resources": [
                {"name": "q1", "dependencies": ["numpy"]},
                {"name": "q2"},
            ]
        }
        (tmp_path / "runpod_manifest.json").write_text(json.dumps(manifest))
        monkeypatch.setattr(bootstrap, "APP_DIR", str(tmp_path))
        monkeypatch.setenv("FLASH_RESOURCE_NAME", "q1")
        entry = bootstrap._resource_entry()
        assert entry["dependencies"] == ["numpy"]

    def test_resource_entry_missing_names_available(self, tmp_path, monkeypatch):
        (tmp_path / "runpod_manifest.json").write_text(
            json.dumps({"resources": [{"name": "other"}]})
        )
        monkeypatch.setattr(bootstrap, "APP_DIR", str(tmp_path))
        monkeypatch.setenv("FLASH_RESOURCE_NAME", "q1")
        with pytest.raises(bootstrap.PhaseError, match="other"):
            bootstrap._resource_entry()


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
        assert "def _unpack" in decoded

    def test_queue_default_image_no_bootstrap(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        template = payload["template"]
        assert template["imageName"].startswith("runpod/queue:py3.12-")
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
            "urllib", "io",
        }
        assert imports <= stdlib, f"non-stdlib imports: {imports - stdlib}"
