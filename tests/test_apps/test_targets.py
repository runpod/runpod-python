"""unit tests for invocation targets and their helpers."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, patch

import pytest

from runpod.apps.errors import RemoteExecutionError
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.targets import (
    SENTINEL_ID,
    LiveTarget,
    SentinelTarget,
    _api_key,
    _headers,
    _lb_domain,
    _wait_terminal,
    args_to_input,
    unwrap_job_output,
)

# http.server's per-request sockets are collected lazily; the unraisable
# checker flags them as ResourceWarnings non-deterministically
pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)


class TestApiKey:
    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_API_KEY", "sk-env")
        assert _api_key() == "sk-env"

    def test_module_fallback(self, monkeypatch):
        import runpod

        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        monkeypatch.setattr(runpod, "api_key", "sk-module")
        assert _api_key() == "sk-module"

    def test_missing_raises(self, monkeypatch):
        import runpod

        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        monkeypatch.setattr(runpod, "api_key", None)
        with pytest.raises(RuntimeError, match="rp login"):
            _api_key()


class TestUrlHelpers:
    def test_lb_domain(self, monkeypatch):
        import runpod

        monkeypatch.setattr(
            runpod, "endpoint_url_base", "https://api.runpod.ai/v2"
        )
        assert _lb_domain() == "api.runpod.ai"

    def test_headers(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_API_KEY", "sk-test")
        headers = _headers({"X-Extra": "1"})
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["X-Extra"] == "1"


class TestArgsToInput:
    def test_positional_mapping(self):
        def fn(a, b, c=3):
            pass

        assert args_to_input(fn, (1, 2), {}) == {"a": 1, "b": 2}

    def test_kwargs_merge(self):
        def fn(a, b):
            pass

        assert args_to_input(fn, (1,), {"b": 2}) == {"a": 1, "b": 2}

    def test_too_many_positional(self):
        def fn(a):
            pass

        with pytest.raises(TypeError, match="positional"):
            args_to_input(fn, (1, 2), {})

    def test_empty_marker(self):
        def fn():
            pass

        assert args_to_input(fn, (), {}) == {"__empty": True}


class TestUnwrapJobOutput:
    def test_completed(self):
        assert unwrap_job_output(
            {"status": "COMPLETED", "output": {"x": 1}}
        ) == {"x": 1}

    def test_failed_status(self):
        with pytest.raises(RemoteExecutionError, match="boom"):
            unwrap_job_output({"status": "FAILED", "error": "boom"})

    def test_error_in_output(self):
        with pytest.raises(RemoteExecutionError, match="oops"):
            unwrap_job_output(
                {"status": "COMPLETED", "output": {"error": "oops"}}
            )

    def test_missing_output_returns_data(self):
        data = {"status": "COMPLETED", "value": 7}
        assert unwrap_job_output(data) == data


class TestWaitTerminal:
    async def test_immediate_terminal(self):
        data = {"id": "j1", "status": "COMPLETED", "output": 1}
        result = await _wait_terminal("http://x", data, {}, timeout=5)
        assert result is data

    async def test_polls_to_completion(self):
        polls = [
            {"id": "j1", "status": "IN_PROGRESS"},
            {"id": "j1", "status": "COMPLETED", "output": 2},
        ]
        seen = []
        with (
            patch(
                "runpod.apps.targets._get_json",
                AsyncMock(side_effect=polls),
            ),
            patch("asyncio.sleep", AsyncMock()),
        ):
            result = await _wait_terminal(
                "http://x",
                {"id": "j1", "status": "IN_QUEUE"},
                {},
                timeout=30,
                on_status=seen.append,
            )
        assert result["status"] == "COMPLETED"
        assert len(seen) == 3

    async def test_no_job_id_returns_data(self):
        data = {"status": "IN_QUEUE"}
        result = await _wait_terminal("http://x", data, {}, timeout=5)
        assert result is data

    async def test_timeout(self):
        with (
            patch(
                "runpod.apps.targets._get_json",
                AsyncMock(return_value={"id": "j1", "status": "IN_PROGRESS"}),
            ),
            patch("asyncio.sleep", AsyncMock()),
            patch("time.monotonic", side_effect=[0, 100, 200]),
        ):
            with pytest.raises(TimeoutError, match="did not complete"):
                await _wait_terminal(
                    "http://x",
                    {"id": "j1", "status": "IN_QUEUE"},
                    {},
                    timeout=10,
                )


@pytest.fixture
def local_endpoint(monkeypatch):
    """local http server standing in for the serverless data plane."""
    state = {"requests": [], "responses": {}}

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            state["requests"].append(
                {
                    "path": self.path,
                    "method": self.command,
                    "headers": dict(self.headers),
                    "body": json.loads(body) if body else None,
                }
            )
            reply = state["responses"].get(
                self.path, {"status": "COMPLETED", "output": {"ok": True}}
            )
            payload = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = _respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    import runpod

    monkeypatch.setenv("RUNPOD_API_KEY", "sk-test")
    monkeypatch.setattr(
        runpod,
        "endpoint_url_base",
        f"http://127.0.0.1:{server.server_address[1]}",
    )
    server.state = state
    yield server
    server.shutdown()


class TestSentinelTarget:
    async def test_invoke_routes_through_sentinel(self, local_endpoint):
        target = SentinelTarget("demo", "default", "chat")
        result = await target.invoke({"input": {"prompt": "hi"}}, timeout=10)
        assert result == {"ok": True}

        request = local_endpoint.state["requests"][0]
        assert request["path"] == f"/{SENTINEL_ID}/runsync"
        assert request["headers"]["X-Flash-App"] == "demo"
        assert request["headers"]["X-Flash-Environment"] == "default"
        assert request["headers"]["X-Flash-Endpoint"] == "chat"

    async def test_submit_and_wait(self, local_endpoint):
        local_endpoint.state["responses"][f"/{SENTINEL_ID}/run"] = {
            "id": "j1",
            "status": "IN_QUEUE",
        }
        local_endpoint.state["responses"][f"/{SENTINEL_ID}/status/j1"] = {
            "id": "j1",
            "status": "COMPLETED",
            "output": 42,
        }
        target = SentinelTarget("demo", "default", "chat")
        job = await target.submit({"input": {}})
        assert job["id"] == "j1"
        assert await target.wait(job, timeout=10) == 42

    def test_payload_is_plain_kwargs(self):
        target = SentinelTarget("demo", "default", "chat")

        def fn(prompt):
            pass

        payload = target.build_payload(
            fn, ResourceSpec(kind=ResourceKind.QUEUE, name="chat"), ("hi",), {}
        )
        assert payload == {"input": {"prompt": "hi"}}


class TestLiveTarget:
    def _spec(self):
        return ResourceSpec(kind=ResourceKind.QUEUE, name="chat")

    def test_payload_carries_source(self):
        target = LiveTarget("ep123", "chat")

        def fn(prompt):
            return prompt

        payload = target.build_payload(fn, self._spec(), ("hi",), {})
        body = payload["input"]
        assert body["function_name"] == "fn"
        assert "def fn(" in body["function_code"]

    def test_unwrap_success_response(self):
        target = LiveTarget("ep123", "chat")
        output = {"success": True, "result": None, "json_result": {"x": 1}}
        assert target.unwrap(
            {"status": "COMPLETED", "output": output}
        ) == {"x": 1}

    def test_unwrap_failure_raises(self):
        target = LiveTarget("ep123", "chat")
        output = {"success": False, "error": "worker exploded"}
        with pytest.raises(RemoteExecutionError, match="worker exploded"):
            target.unwrap({"status": "COMPLETED", "output": output})

    def test_unwrap_passthrough(self):
        target = LiveTarget("ep123", "chat")
        assert target.unwrap(
            {"status": "COMPLETED", "output": {"plain": 1}}
        ) == {"plain": 1}

    async def test_invoke(self, local_endpoint):
        target = LiveTarget("ep123", "chat")
        data = await target.invoke({"input": {"x": 1}}, timeout=10)
        assert data == {"ok": True}
        request = local_endpoint.state["requests"][0]
        assert request["path"] == "/ep123/runsync"

    async def test_sync_source_skips_unchanged(self, monkeypatch):
        target = LiveTarget("ep123", "chat")

        def backing():
            return 1

        target.attach_source(backing, "chat", self._spec())
        post = AsyncMock(return_value={})
        with patch("runpod.apps.targets._post_json", post):
            await target._sync_source(timeout=10)
            await target._sync_source(timeout=10)
        assert post.await_count == 1

    async def test_sync_source_noop_without_attachment(self):
        target = LiveTarget("ep123", "chat")
        post = AsyncMock()
        with patch("runpod.apps.targets._post_json", post):
            await target._sync_source(timeout=10)
        post.assert_not_awaited()
