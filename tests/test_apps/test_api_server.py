"""tests for the api runtime server."""

import json
import textwrap

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from runpod.apps.app import _clear_registry
from runpod.runtimes.api import server


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def _write_project(tmp_path, monkeypatch, module="main_cls"):
    (tmp_path / f"{module}.py").write_text(
        textwrap.dedent(
            """
            import runpod
            from runpod import App, init, get, post

            app = App("api-test")

            @app.api(name="inference", cpu="cpu3c-1-2")
            class Inference:
                @init
                def setup(self):
                    self.model = "loaded"

                @get("/health")
                def health(self):
                    return {"model": self.model}

                @post("/generate")
                async def generate(self, body: dict):
                    return {"echo": body, "model": self.model}
            """
        )
    )
    manifest = {
        "version": 1,
        "app": "api-test",
        "resources": [
            {
                "kind": "api",
                "name": "inference",
                "module": module,
                "routes": [
                    {"method": "GET", "path": "/health", "handler": "health"},
                    {
                        "method": "POST",
                        "path": "/generate",
                        "handler": "generate",
                    },
                ],
            }
        ],
    }
    (tmp_path / "runpod_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(server, "APP_DIR", str(tmp_path))
    monkeypatch.setenv("FLASH_RESOURCE_NAME", "inference")


class TestDeployedClassApi:
    def test_routes_and_init(self, tmp_path, monkeypatch):
        _write_project(tmp_path, monkeypatch)
        app = server.build_app()
        with TestClient(app) as client:
            # startup ran init before serving
            response = client.get("/ping")
            assert response.status_code == 200
            assert response.json() == {"status": "healthy"}

            response = client.get("/health")
            assert response.json() == {"model": "loaded"}

            response = client.post("/generate", json={"prompt": "hi"})
            assert response.json() == {
                "echo": {"prompt": "hi"},
                "model": "loaded",
            }

    def test_missing_resource_errors(self, tmp_path, monkeypatch):
        _write_project(tmp_path, monkeypatch)
        monkeypatch.setenv("FLASH_RESOURCE_NAME", "nope")
        with pytest.raises(RuntimeError, match="nope"):
            server.build_app()


class TestFactoryApi:
    def test_asgi_factory_served(self, tmp_path, monkeypatch):
        (tmp_path / "main_factory.py").write_text(
            textwrap.dedent(
                """
                from runpod import App

                app = App("api-test")

                @app.api(name="web", cpu="cpu3c-1-2")
                def web():
                    from fastapi import FastAPI

                    server = FastAPI()

                    @server.post("/echo")
                    async def echo(body: dict):
                        return body

                    return server
                """
            )
        )
        manifest = {
            "version": 1,
            "app": "api-test",
            "resources": [
                {"kind": "api", "name": "web", "module": "main_factory"}
            ],
        }
        (tmp_path / "runpod_manifest.json").write_text(json.dumps(manifest))
        monkeypatch.setattr(server, "APP_DIR", str(tmp_path))
        monkeypatch.setenv("FLASH_RESOURCE_NAME", "web")

        app = server.build_app()
        with TestClient(app) as client:
            assert client.get("/ping").json() == {"status": "healthy"}
            assert client.post("/echo", json={"a": 1}).json() == {"a": 1}


class TestLiveApi:
    def test_execute_endpoint(self, monkeypatch):
        monkeypatch.delenv("FLASH_RESOURCE_NAME", raising=False)
        monkeypatch.delenv("RUNPOD_RESOURCE_NAME", raising=False)
        app = server.build_app()
        with TestClient(app) as client:
            assert client.get("/ping").json() == {"status": "healthy"}
            response = client.post(
                "/execute",
                json={
                    "input": {
                        "function_name": "f",
                        "function_code": "def f(a, b):\n    return a * b",
                        "args": [6, 7],
                        "kwargs": {},
                        "serialization_format": "json",
                    }
                },
            )
            data = response.json()
            assert data["success"] is True
            assert data["json_result"] == 42


class TestLiveDispatcher:
    def test_sync_materializes_routes(self):
        from fastapi.testclient import TestClient

        from runpod.runtimes.api.server import _build_live_app

        source = (
            "import runpod\n"
            "from runpod.apps.markers import get, init, post\n"
            "app = runpod.App('live')\n"
            "@app.api(cpu='cpu3c-1-2')\n"
            "class Counter:\n"
            "    @init\n"
            "    def setup(self):\n"
            "        self.count = 0\n"
            "    @post('/bump')\n"
            "    async def bump(self, body: dict):\n"
            "        self.count += body.get('by', 1)\n"
            "        return {'count': self.count}\n"
            "    @get('/value')\n"
            "    async def value(self):\n"
            "        return {'count': self.count}\n"
        )

        client = TestClient(_build_live_app())
        # before sync: core routes only
        assert client.get("/ping").status_code == 200
        assert client.post("/bump", json={}).status_code == 404

        r = client.post(
            "/_runpod/sync", json={"source": source, "resource": "Counter"}
        )
        assert r.status_code == 200

        assert client.post("/bump", json={"by": 3}).json() == {"count": 3}
        assert client.get("/value").json() == {"count": 3}
        # core routes still work after materialization
        assert client.get("/ping").status_code == 200

    def test_sync_installs_dependencies_before_materializing(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from runpod.runtimes.api.server import _build_live_app

        source = (
            "import runpod\n"
            "from runpod.apps.markers import get\n"
            "app = runpod.App('live-deps')\n"
            "@app.api(cpu='cpu3c-1-2', dependencies=['somepkg'])\n"
            "class D:\n"
            "    @get('/ok')\n"
            "    async def ok(self):\n"
            "        return {'ok': True}\n"
        )
        client = TestClient(_build_live_app())
        with (
            patch(
                "runpod.runtimes.executor._install", return_value=None
            ) as install,
            patch(
                "runpod.runtimes.executor._install_system",
                return_value=None,
            ) as install_system,
        ):
            r = client.post(
                "/_runpod/sync",
                json={
                    "source": source,
                    "resource": "D",
                    "dependencies": ["somepkg"],
                    "system_dependencies": ["libfoo"],
                },
            )
        assert r.status_code == 200
        install.assert_called_once_with(["somepkg"], "dependencies")
        install_system.assert_called_once_with(["libfoo"])
        assert client.get("/ok").json() == {"ok": True}

    def test_resync_same_source_keeps_state(self):
        from fastapi.testclient import TestClient

        from runpod.runtimes.api.server import _build_live_app

        source = (
            "import runpod\n"
            "from runpod.apps.markers import post\n"
            "app = runpod.App('live2')\n"
            "@app.api(cpu='cpu3c-1-2')\n"
            "class S:\n"
            "    @post('/add')\n"
            "    async def add(self, body: dict):\n"
            "        self.total = getattr(self, 'total', 0) + body['x']\n"
            "        return {'total': self.total}\n"
        )
        client = TestClient(_build_live_app())
        client.post("/_runpod/sync", json={"source": source, "resource": "S"})
        assert client.post("/add", json={"x": 2}).json() == {"total": 2}
        # identical source: no rebuild, state survives
        client.post("/_runpod/sync", json={"source": source, "resource": "S"})
        assert client.post("/add", json={"x": 3}).json() == {"total": 5}
