"""tests for context detection and remote dispatch."""

from unittest.mock import AsyncMock, patch

import pytest

import runpod
from runpod.apps import App, Context, current_context, is_local
from runpod.apps.app import _clear_registry
from runpod.apps.errors import (
    EndpointNotFound,
    InvalidResourceError,
    RemoteExecutionError,
)
from runpod.apps.targets import (
    SentinelTarget,
    args_to_input,
    unwrap_job_output,
)


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class TestContextDetection:
    def test_local_by_default(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        assert current_context() is Context.LOCAL
        assert is_local() is True

    def test_worker_via_endpoint_id(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-1")
        assert current_context() is Context.WORKER
        assert is_local() is False

    def test_worker_via_pod_id(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
        monkeypatch.setenv("RUNPOD_POD_ID", "pod-1")
        assert current_context() is Context.WORKER

    def test_dev_session(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("RUNPOD_DEV_SESSION", "1")
        assert current_context() is Context.DEV
        assert is_local() is True


class TestArgsToInput:
    def test_positional_mapped_to_names(self):
        def fn(prompt, temp):
            pass

        assert args_to_input(fn, ("hi", 0.5), {}) == {"prompt": "hi", "temp": 0.5}

    def test_kwargs_merged(self):
        def fn(a, b):
            pass

        assert args_to_input(fn, (1,), {"b": 2}) == {"a": 1, "b": 2}

    def test_empty_gets_placeholder(self):
        def fn():
            pass

        assert args_to_input(fn, (), {}) == {"__empty": True}

    def test_too_many_positional(self):
        def fn(a):
            pass

        with pytest.raises(TypeError):
            args_to_input(fn, (1, 2), {})


class TestUnwrapJobOutput:
    def test_output_extracted(self):
        assert unwrap_job_output({"status": "COMPLETED", "output": {"x": 1}}) == {
            "x": 1
        }

    def test_failed_raises(self):
        with pytest.raises(RemoteExecutionError):
            unwrap_job_output({"status": "FAILED", "error": "boom"})

    def test_error_in_output_raises(self):
        with pytest.raises(RemoteExecutionError):
            unwrap_job_output({"status": "COMPLETED", "output": {"error": "bad"}})


class TestRemoteDispatch:
    def test_remote_sync_via_sentinel(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        app = App("my-app")

        @app.queue(name="q")
        def q(x):
            return x

        with patch.object(
            SentinelTarget, "invoke", new_callable=AsyncMock
        ) as mock_invoke:
            mock_invoke.return_value = {"doubled": 4}
            result = q.remote(2)

        assert result == {"doubled": 4}
        mock_invoke.assert_awaited_once_with({"input": {"x": 2}})

    async def test_remote_aio(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        app = App("my-app")

        @app.queue(name="q")
        async def q(x):
            return x

        with patch.object(
            SentinelTarget, "invoke", new_callable=AsyncMock
        ) as mock_invoke:
            mock_invoke.return_value = {"ok": True}
            result = await q.remote.aio(1)

        assert result == {"ok": True}

    def test_remote_with_options_merges_payload(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        app = App("my-app")

        @app.queue(name="q")
        def q(x):
            return x

        with patch.object(
            SentinelTarget, "invoke", new_callable=AsyncMock
        ) as mock_invoke:
            mock_invoke.return_value = {"ok": 1}
            q.with_options(
                webhook="https://hook", execution_timeout=5000, low_priority=True
            ).remote(2)

        mock_invoke.assert_awaited_once_with(
            {
                "input": {"x": 2},
                "webhook": "https://hook",
                "policy": {"executionTimeout": 5000, "lowPriority": True},
            }
        )

    def test_spawn_with_options_merges_payload(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        app = App("my-app")

        @app.queue(name="q")
        def q(x):
            return x

        with patch.object(
            SentinelTarget, "submit", new_callable=AsyncMock
        ) as mock_submit:
            mock_submit.return_value = {"id": "job-1", "status": "IN_QUEUE"}
            q.with_options(s3_config={"bucketName": "b"}).spawn(1)

        mock_submit.assert_awaited_once_with(
            {"input": {"x": 1}, "s3Config": {"bucketName": "b"}}
        )

    def test_with_options_returns_new_handle(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        app = App("my-app")

        @app.queue(name="q")
        def q(x):
            return x

        bound = q.with_options(webhook="https://hook")
        assert bound is not q
        assert q._job_options == {}
        # policy fields deep-merge across chained calls
        chained = q.with_options(execution_timeout=1).with_options(ttl=2)
        assert chained._job_options == {"policy": {"executionTimeout": 1, "ttl": 2}}

    def test_with_options_rejects_tasks(self):
        app = App("my-app")

        @app.task(name="t")
        def t(x):
            return x

        with pytest.raises(InvalidResourceError, match="only to @app.queue"):
            t.with_options(webhook="https://hook")

    def test_worker_executes_own_body(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-1")
        monkeypatch.setenv("FLASH_RESOURCE_NAME", "q")
        app = App("my-app")

        @app.queue(name="q")
        def q(x):
            return x * 10

        assert q.remote(4) == 40

    def test_worker_calls_sibling_via_sentinel(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-1")
        monkeypatch.setenv("FLASH_RESOURCE_NAME", "other")
        app = App("my-app")

        @app.queue(name="q")
        def q(x):
            return x

        with patch.object(
            SentinelTarget, "invoke", new_callable=AsyncMock
        ) as mock_invoke:
            mock_invoke.return_value = "remote-result"
            result = q.remote(1)

        assert result == "remote-result"

    def test_dev_without_provisioned_target_raises(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("RUNPOD_DEV_SESSION", "1")
        app = App("my-app")

        @app.queue(name="q")
        def q():
            pass

        with pytest.raises(EndpointNotFound):
            q.remote()

    def test_spawn_returns_job(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        app = App("my-app")

        @app.queue(name="q")
        def q(x):
            return x

        with patch.object(
            SentinelTarget, "submit", new_callable=AsyncMock
        ) as mock_submit:
            mock_submit.return_value = {"id": "job-1", "status": "IN_QUEUE"}
            job = q.spawn(1)

        assert isinstance(job, runpod.Job)
        assert job.id == "job-1"

    def test_stream_generator_function(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        app = App("my-app")

        @app.queue(name="gen")
        def gen(n):
            yield from range(n)

        async def fake_stream(self, job_id, *, timeout=300.0):
            for chunk in ("x", "y"):
                yield chunk

        with (
            patch.object(
                SentinelTarget, "submit", new_callable=AsyncMock
            ) as mock_submit,
            patch.object(SentinelTarget, "stream_job", fake_stream),
        ):
            mock_submit.return_value = {"id": "job-1", "status": "IN_QUEUE"}
            chunks = list(gen.stream(2))

        assert chunks == ["x", "y"]

    def test_stream_rejects_non_generator(self, monkeypatch):
        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        app = App("my-app")

        @app.queue(name="plain")
        def plain(x):
            return x

        with pytest.raises(InvalidResourceError, match="not a generator"):
            list(plain.stream(1))


class TestStubs:
    def test_queue_stub_requires_name_or_id(self):
        with pytest.raises(ValueError):
            runpod.Queue(app="a")
        with pytest.raises(ValueError):
            runpod.Queue(app="a", name="x", id="y")

    def test_queue_stub_remote(self):
        stub = runpod.Queue(app="other-app", name="q")
        with patch.object(
            SentinelTarget, "invoke", new_callable=AsyncMock
        ) as mock_invoke:
            mock_invoke.return_value = {"ok": 1}
            result = stub.remote(prompt="hi")

        assert result == {"ok": 1}
        mock_invoke.assert_awaited_once_with({"input": {"prompt": "hi"}})

    def test_queue_stub_with_options(self):
        stub = runpod.Queue(app="other-app", name="q")
        bound = stub.with_options(webhook="https://hook", ttl=1000)
        assert bound is not stub
        assert stub._job_options == {}
        with patch.object(
            SentinelTarget, "invoke", new_callable=AsyncMock
        ) as mock_invoke:
            mock_invoke.return_value = {"ok": 1}
            bound.remote(prompt="hi")

        mock_invoke.assert_awaited_once_with(
            {
                "input": {"prompt": "hi"},
                "webhook": "https://hook",
                "policy": {"ttl": 1000},
            }
        )

    def test_queue_stub_spawn_returns_job(self):
        stub = runpod.Queue(app="other-app", name="q")
        with patch.object(
            SentinelTarget, "submit", new_callable=AsyncMock
        ) as mock_submit:
            mock_submit.return_value = {"id": "job-1", "status": "IN_QUEUE"}
            job = stub.spawn(prompt="hi")

        assert isinstance(job, runpod.Job)
        assert job.id == "job-1"

    def test_queue_stub_stream(self):
        stub = runpod.Queue(app="other-app", name="q")

        async def fake_stream(self, job_id, *, timeout=300.0):
            for chunk in ("x", "y"):
                yield chunk

        with (
            patch.object(
                SentinelTarget, "submit", new_callable=AsyncMock
            ) as mock_submit,
            patch.object(SentinelTarget, "stream_job", fake_stream),
        ):
            mock_submit.return_value = {"id": "job-1", "status": "IN_QUEUE"}
            chunks = list(stub.stream(prompt="hi"))

        assert chunks == ["x", "y"]

    def test_queue_stub_reconnects_to_job(self):
        stub = runpod.Queue(app="other-app", name="q")
        job = stub.job("job-1")

        assert isinstance(job, runpod.Job)
        assert job.id == "job-1"

    def test_api_stub_http(self):
        stub = runpod.Api(app="other-app", name="api")
        with patch.object(
            SentinelTarget, "request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = {"status": "healthy"}
            result = stub.get("/health")

        assert result == {"status": "healthy"}
        mock_request.assert_awaited_once_with("GET", "/health", None)


class TestSyncBridge:
    def test_remote_inside_running_loop(self, monkeypatch):
        """calling sync .remote() from inside an event loop must not raise."""
        import asyncio

        for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
            monkeypatch.delenv(var, raising=False)
        app = App("my-app")

        @app.queue(name="q")
        def q(x):
            return x

        with patch.object(
            SentinelTarget, "invoke", new_callable=AsyncMock
        ) as mock_invoke:
            mock_invoke.return_value = "bridged"

            async def caller():
                return q.remote(1)

            result = asyncio.run(caller())

        assert result == "bridged"


class TestModuleSourceShipping:
    def test_whole_module_ships(self, tmp_path):
        import importlib.util
        import sys

        mod_file = tmp_path / "shipmod.py"
        mod_file.write_text(
            "import asyncio\n"
            "from pathlib import Path\n"
            "\n"
            "GREETING = 'hello'\n"
            "\n"
            "async def waiter():\n"
            "    await asyncio.sleep(0)\n"
            "    return f'{GREETING} {Path(\"x\")}'\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit('main guard must not run')\n"
        )
        spec = importlib.util.spec_from_file_location("shipmod", mod_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["shipmod"] = mod
        try:
            from runpod.apps.serialization import get_function_source

            source = get_function_source(mod.waiter)
            # the entire module ships: imports and globals included
            assert "import asyncio" in source
            assert "GREETING = 'hello'" in source

            # executes like deployed-mode module import: main guard inert
            namespace = {"__name__": "__runpod_live__"}
            exec(source, namespace)
            import asyncio as aio

            assert aio.run(namespace["waiter"]()) == "hello x"
        finally:
            sys.modules.pop("shipmod", None)

    def test_execute_request_unwraps_handles(self):
        # a shipped module defines decorated handles; the runner must
        # call the wrapped function, not the handle
        from runpod.runtimes.executor import execute_request

        code = (
            "import runpod\n"
            "app = runpod.App('t')\n"
            "@app.queue()\n"
            "def double(x):\n"
            "    return x * 2\n"
        )
        response = execute_request(
            {
                "function_name": "double",
                "function_code": code,
                "args": [],
                "kwargs": {"x": "AAA"},
                "serialization_format": "json",
            }
        )
        assert response["success"], response.get("error")
        assert response["json_result"] == "AAAAAA"

    def test_nested_source_extraction_from_shipped_module(self):
        # inside a live worker, a function calling sibling.remote()
        # re-extracts that sibling's source via inspect; the shipped
        # module must be inspectable after exec
        from runpod.runtimes.executor import execute_request

        code = (
            "import runpod\n"
            "app = runpod.App('t2')\n"
            "@app.queue()\n"
            "def sibling(x):\n"
            "    return x\n"
            "@app.queue()\n"
            "def caller():\n"
            "    from runpod.apps.serialization import get_function_source\n"
            "    return get_function_source(sibling._fn)\n"
        )
        response = execute_request(
            {
                "function_name": "caller",
                "function_code": code,
                "args": [],
                "kwargs": {},
                "serialization_format": "json",
            }
        )
        assert response["success"], response.get("error")
        # the whole module re-ships (decorators intact), matching what
        # the first hop sent
        assert "def sibling" in response["json_result"]
        assert "@app.queue()" in response["json_result"]

    def test_missing_module_on_deserialize_is_actionable(self):
        import base64

        import cloudpickle
        import pytest

        from runpod.apps.errors import RemoteExecutionError
        from runpod.apps.serialization import deserialize_result

        class FakePickle:
            def __reduce__(self):
                return (__import__, ("nonexistent_pkg_xyz",))

        payload = base64.b64encode(cloudpickle.dumps(FakePickle())).decode()
        with pytest.raises(RemoteExecutionError, match="nonexistent_pkg_xyz"):
            deserialize_result(payload)
