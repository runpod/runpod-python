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


class TestErrorSurface:
    def test_error_payload_shape(self):
        error = bootstrap.PhaseError("locate", "artifact missing")
        payload = bootstrap._error_payload(error)
        assert payload["error_type"] == "BootstrapError"
        assert "locate" in payload["error_message"]
        assert "artifact missing" in payload["error_message"]

    def test_queue_error_loop_exits_without_webhooks(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_WEBHOOK_GET_JOB", raising=False)
        monkeypatch.delenv("RUNPOD_WEBHOOK_POST_OUTPUT", raising=False)
        with pytest.raises(SystemExit):
            bootstrap._queue_error_loop(bootstrap.PhaseError("locate", "x"))

    def test_queue_error_loop_reports_jobs(self, monkeypatch):
        import contextlib
        import io

        monkeypatch.setenv("RUNPOD_WEBHOOK_GET_JOB", "http://jobs/get")
        monkeypatch.setenv("RUNPOD_WEBHOOK_POST_OUTPUT", "http://jobs/out/$ID")
        monkeypatch.setenv("RUNPOD_AI_API_KEY", "k")

        posted = []

        @contextlib.contextmanager
        def _get_response():
            response = io.BytesIO(json.dumps({"id": "job1"}).encode())
            response.status = 200
            yield response

        def fake_urlopen(req, timeout=None):
            url = req.full_url
            if url.endswith("/get"):
                return _get_response()
            posted.append((url, req.data))
            # first report done: break out of the infinite loop
            raise KeyboardInterrupt

        monkeypatch.setattr(
            bootstrap.urllib.request, "urlopen", fake_urlopen
        )
        error = bootstrap.PhaseError("attach", "manifest gone")
        with pytest.raises(KeyboardInterrupt):
            bootstrap._queue_error_loop(error)

        assert posted
        url, body = posted[0]
        assert url.endswith("/out/job1")
        assert b"BootstrapError" in body

    def test_report_error_routes_by_kind(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            bootstrap, "_api_error_server", lambda e: calls.append("api")
        )
        monkeypatch.setattr(
            bootstrap, "_queue_error_loop", lambda e: calls.append("queue")
        )
        error = bootstrap.PhaseError("locate", "x")

        monkeypatch.setenv("RUNPOD_RUNTIME_KIND", "api")
        bootstrap._report_error(error)
        monkeypatch.setenv("RUNPOD_RUNTIME_KIND", "queue")
        bootstrap._report_error(error)
        assert calls == ["api", "queue"]


class TestLiveRuntimeInstall:
    def test_importable_runtime_no_op(self, monkeypatch):
        monkeypatch.setattr(
            bootstrap, "_worker_importable", lambda paths: (True, "")
        )
        installs = []
        monkeypatch.setattr(
            bootstrap, "_pip_install", lambda pkgs, phase: installs.append(pkgs)
        )
        bootstrap._ensure_runtime_installed()
        assert installs == []

    def test_installs_package_spec(self, monkeypatch):
        probes = iter([(False, "no module"), (True, "")])
        monkeypatch.setattr(
            bootstrap, "_worker_importable", lambda paths: next(probes)
        )
        installs = []
        monkeypatch.setattr(
            bootstrap, "_pip_install", lambda pkgs, phase: installs.append(pkgs)
        )
        monkeypatch.setenv("RUNPOD_PACKAGE_SPEC", "runpod==1.99.0")
        bootstrap._ensure_runtime_installed()
        assert installs == [["runpod==1.99.0", "cloudpickle"]]

    def test_api_kind_adds_uvicorn(self, monkeypatch):
        probes = iter([(False, "no module"), (True, "")])
        monkeypatch.setattr(
            bootstrap, "_worker_importable", lambda paths: next(probes)
        )
        installs = []
        monkeypatch.setattr(
            bootstrap, "_pip_install", lambda pkgs, phase: installs.append(pkgs)
        )
        monkeypatch.delenv("RUNPOD_PACKAGE_SPEC", raising=False)
        monkeypatch.setenv("RUNPOD_RUNTIME_KIND", "api")
        bootstrap._ensure_runtime_installed()
        assert installs == [["runpod", "cloudpickle", "uvicorn>=0.30"]]

    def test_install_failure_raises_phase(self, monkeypatch):
        monkeypatch.setattr(
            bootstrap, "_worker_importable", lambda paths: (False, "still broken")
        )
        monkeypatch.setattr(
            bootstrap, "_pip_install", lambda pkgs, phase: None
        )
        with pytest.raises(bootstrap.PhaseError) as exc_info:
            bootstrap._ensure_runtime_installed()
        assert exc_info.value.phase == "runtime"


class TestServe:
    def test_serve_execs_worker_module(self, monkeypatch):
        execs = []
        monkeypatch.setattr(
            bootstrap.os,
            "execve",
            lambda exe, argv, env: execs.append((exe, argv, env)),
        )
        monkeypatch.setenv("RUNPOD_RUNTIME_KIND", "queue")
        monkeypatch.setenv("PYTHONPATH", "/existing")
        bootstrap._serve(["/env", "/app"])
        exe, argv, env = execs[0]
        assert argv[-1] == bootstrap.WORKER_MODULES["queue"]
        assert env["PYTHONPATH"].split(":")[:2] == ["/env", "/app"]
        assert "/existing" in env["PYTHONPATH"]
        assert env["RUNPOD_APP_DIR"] == "/app"

    def test_serve_live_mode_no_paths(self, monkeypatch):
        execs = []
        monkeypatch.setattr(
            bootstrap.os,
            "execve",
            lambda exe, argv, env: execs.append((exe, argv, env)),
        )
        monkeypatch.delenv("PYTHONPATH", raising=False)
        monkeypatch.setenv("RUNPOD_RUNTIME_KIND", "api")
        bootstrap._serve([])
        _, argv, env = execs[0]
        assert argv[-1] == bootstrap.WORKER_MODULES["api"]
        assert "RUNPOD_APP_DIR" not in env


class TestMain:
    def test_deployed_path_serves(self, tmp_path, monkeypatch):
        app_dir = str(tmp_path)
        (tmp_path / bootstrap.MANIFEST_NAME).write_text(
            json.dumps({"resources": [{"name": "chat"}]})
        )
        monkeypatch.setenv("FLASH_RESOURCE_NAME", "chat")
        monkeypatch.setattr(bootstrap, "_locate", lambda: app_dir)
        monkeypatch.setattr(
            bootstrap, "_worker_importable", lambda paths: (True, "")
        )
        served = []
        monkeypatch.setattr(bootstrap, "_serve", lambda paths: served.append(paths))
        bootstrap.main()
        assert served and served[0][-1] == app_dir

    def test_phase_error_reports(self, monkeypatch):
        monkeypatch.setenv("FLASH_RESOURCE_NAME", "chat")
        monkeypatch.setattr(
            bootstrap,
            "_locate",
            lambda: (_ for _ in ()).throw(
                bootstrap.PhaseError("locate", "gone")
            ),
        )
        reported = []
        monkeypatch.setattr(
            bootstrap, "_report_error", lambda e: reported.append(e)
        )
        bootstrap.main()
        assert reported and reported[0].phase == "locate"

    def test_live_path(self, monkeypatch):
        monkeypatch.delenv("FLASH_RESOURCE_NAME", raising=False)
        monkeypatch.delenv("RUNPOD_RESOURCE_NAME", raising=False)
        monkeypatch.setattr(
            bootstrap, "_ensure_runtime_installed", lambda: None
        )
        served = []
        monkeypatch.setattr(bootstrap, "_serve", lambda paths: served.append(paths))
        bootstrap.main()
        assert served == [[]]
