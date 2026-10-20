"""tests for the worker api key scoping analysis."""

import importlib.util
import sys
import textwrap

import pytest

from runpod.apps.app import _clear_registry
from runpod.apps.remote_calls import makes_remote_calls


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.fixture
def load(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))

    def _load(source, name="remote_calls_app"):
        path = tmp_path / f"{name}.py"
        path.write_text(textwrap.dedent(source))
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    return _load


SOURCE = '''
import json

import runpod

app = runpod.App("calls")


def fmt(value):
    return json.dumps({"value": value})


def fan_out(items):
    return [leaf.remote(x=i) for i in items]


@app.queue(name="leaf", cpu="cpu3c-1-2")
def leaf(x: int) -> str:
    return fmt(x)


@app.queue(name="caller", cpu="cpu3c-1-2")
def caller(n: int) -> list:
    return [leaf(x=i) for i in range(n)]


@app.queue(name="indirect", cpu="cpu3c-1-2")
def indirect(n: int) -> list:
    return fan_out(range(n))


@app.queue(name="client", cpu="cpu3c-1-2")
def client(n: int):
    return runpod.Queue(app="other", name="x").remote(n=n)


@app.api(name="service", cpu="cpu3c-1-2")
class Service:
    @runpod.init
    def setup(self):
        self.ready = True

    @runpod.post("/echo")
    def echo(self, body: dict) -> dict:
        return body


@runpod.local_entrypoint
def main():
    print(caller.remote(n=2))
'''


class TestMakesRemoteCalls:
    def test_classification(self, load):
        mod = load(SOURCE)
        app = mod.app
        result = {
            name: makes_remote_calls(app, handle)
            for name, handle in app.resources.items()
        }
        assert result == {
            "leaf": False,  # helper only, no other resource
            "caller": True,  # references another resource
            "indirect": True,  # through a module-level helper
            "client": True,  # runpod client inside the body
            "service": False,  # @runpod.* decorators are not calls
        }

    def test_local_project_import_counts(self, tmp_path, load):
        (tmp_path / "helpers_mod.py").write_text("def run():\n    return 1\n")
        mod = load(
            """
            import runpod
            from helpers_mod import run

            app = runpod.App("local")

            @app.queue(name="uses", cpu="cpu3c-1-2")
            def uses() -> int:
                return run()
            """,
            name="remote_calls_local",
        )
        assert makes_remote_calls(mod.app, mod.app.resources["uses"]) is True

    def test_import_inside_the_body_counts(self, tmp_path, load):
        (tmp_path / "helpers_body.py").write_text("def run():\n    return 1\n")
        mod = load(
            """
            import runpod

            app = runpod.App("bodyimport")

            @app.queue(name="uses", cpu="cpu3c-1-2")
            def uses() -> int:
                from helpers_body import run
                return run()

            @app.queue(name="gpu", gpu="NVIDIA GeForce RTX 4090")
            def gpu() -> str:
                import json
                import not_installed_anywhere
                return json.dumps(not_installed_anywhere.x)
            """,
            name="remote_calls_body",
        )
        assert makes_remote_calls(mod.app, mod.app.resources["uses"]) is True
        # stdlib and packages absent from the client (torch on a gpu
        # worker) are not project code
        assert makes_remote_calls(mod.app, mod.app.resources["gpu"]) is False

    def test_package_import_from_inside_the_package_counts(self, tmp_path, load):
        pkg = tmp_path / "mypkg_rc"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "helpers.py").write_text("def run():\n    return 1\n")
        source = textwrap.dedent(
            """
            import runpod
            from mypkg_rc.helpers import run

            app = runpod.App("pkgimport")

            @app.queue(name="uses", cpu="cpu3c-1-2")
            def uses() -> int:
                return run()
            """
        )
        (pkg / "worker.py").write_text(source)
        import importlib

        mod = importlib.import_module("mypkg_rc.worker")
        assert makes_remote_calls(mod.app, mod.app.resources["uses"]) is True

    def test_init_hook_calls_count(self, load):
        mod = load(
            """
            import runpod

            app = runpod.App("inithook")

            @app.queue(name="sibling", cpu="cpu3c-1-2")
            def sibling() -> int:
                return 1

            @app.queue(name="leaf", cpu="cpu3c-1-2")
            def leaf() -> int:
                return 2

            @leaf.init
            def warm():
                sibling.remote()
            """,
            name="remote_calls_init",
        )
        assert makes_remote_calls(mod.app, mod.app.resources["leaf"]) is True
        assert makes_remote_calls(mod.app, mod.app.resources["sibling"]) is False

    def test_handles_of_other_apps_count(self, load):
        mod = load(
            """
            import runpod

            other = runpod.App("other-app")
            app = runpod.App("main-app")

            @other.api(name="svc", cpu="cpu3c-1-2")
            class Svc:
                @runpod.post("/x")
                def x(self, body: dict) -> dict:
                    return body

            @app.queue(name="calls_api", cpu="cpu3c-1-2")
            def calls_api() -> dict:
                return Svc.post("/x", json={})
            """,
            name="remote_calls_other",
        )
        assert makes_remote_calls(mod.app, mod.app.resources["calls_api"]) is True

    def test_unanalyzable_targets_fail_open(self):
        import runpod

        app = runpod.App("nested")

        @app.queue(name="inner", cpu="cpu3c-1-2")
        def inner() -> int:
            return 1

        # defined inside a function: qualname differs, not analyzed
        assert makes_remote_calls(app, app.resources["inner"]) is True


class TestPayloadsScopeTheKey:
    def test_deploy_gives_key_only_to_callers(self, load, monkeypatch):
        import runpod
        from runpod.apps.deploy import _deployed_endpoint_input

        monkeypatch.setattr(runpod, "api_key", "acct-key")
        mod = load(SOURCE, name="remote_calls_deploy")

        def env_keys(name):
            payload = _deployed_endpoint_input(
                mod.app, mod.app.resources[name].spec, "env-1", "b1", "3.12"
            )
            return {e["key"] for e in payload["template"]["env"]}

        assert "RUNPOD_API_KEY" not in env_keys("leaf")
        assert "RUNPOD_API_KEY" in env_keys("caller")

    def test_dev_gives_key_only_to_callers(self, load, monkeypatch):
        from runpod.apps import dev

        monkeypatch.setattr(dev, "_client_api_key", lambda: "acct-key")
        mod = load(SOURCE, name="remote_calls_dev")

        def env_keys(name):
            payload = dev._endpoint_input(mod.app, mod.app.resources[name].spec)
            return {e["key"] for e in payload["template"]["env"]}

        assert "RUNPOD_API_KEY" not in env_keys("leaf")
        assert "RUNPOD_API_KEY" in env_keys("indirect")
