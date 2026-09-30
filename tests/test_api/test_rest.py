"""Network-level tests for the synchronous and asynchronous REST transports."""

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import aiohttp
import pytest
import requests

import runpod
from runpod.api.rest import run_rest_request, run_rest_request_async
from runpod.error import AuthenticationError, QueryError


@pytest.fixture
def rest_server(monkeypatch):
    state = {
        "hits": 0,
        "status": 200,
        "body": b'{"id":"pod-1"}',
        "content_type": "application/json",
        "started": threading.Event(),
        "disconnected": threading.Event(),
    }

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            state["hits"] += 1
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            state["started"].set()
            if state.get("stall"):
                self.connection.settimeout(3)
                if self.connection.recv(1) == b"":
                    state["disconnected"].set()
                return
            if state.get("disconnect"):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            status, response_body = state["status"], state["body"]
            if self.headers.get("Authorization") != "Bearer key":
                status, response_body = 401, b"invalid key"
            elif self.path != state.get("path", "/prefix/v2/pods"):
                status, response_body = 404, b"wrong path"
            elif "expected_body" in state and json.loads(body) != state["expected_body"]:
                status, response_body = 400, b"wrong body"
            self.send_response(status)
            self.send_header("Content-Type", state["content_type"])
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        do_GET = do_POST = do_PUT = do_DELETE = _respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "RUNPOD_API_BASE_URL", f"http://127.0.0.1:{server.server_port}/prefix/"
    )
    monkeypatch.setattr(runpod, "api_key", None)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(params=["sync", "async"])
def rest_request(request):
    if request.param == "sync":
        return run_rest_request

    def request_async(*args, **kwargs):
        return asyncio.run(run_rest_request_async(*args, **kwargs))

    return request_async


@pytest.mark.parametrize("explicit_key", ["key", None])
def test_authenticated_mutation_at_custom_base_url(
    rest_server, rest_request, monkeypatch, explicit_key
):
    monkeypatch.setattr(runpod, "api_key", "wrong-key" if explicit_key else "key")
    rest_server.update(
        path="/prefix/v2/pods?include=all", expected_body={"name": "pod"}
    )
    result = rest_request(
        "POST",
        "/v2/pods",
        api_key=explicit_key,
        params={"include": "all"},
        json={"name": "pod"},
    )
    assert result == {"id": "pod-1"}


def test_missing_key_fails_before_network_request(rest_server, rest_request):
    with pytest.raises(AuthenticationError):
        rest_request("POST", "/v2/pods", json={"name": "pod"})
    assert rest_server["hits"] == 0


def test_invalid_key_raises_authentication_error(rest_server, rest_request):
    with pytest.raises(AuthenticationError):
        rest_request("GET", "/v2/pods", api_key="invalid-key")


@pytest.mark.parametrize("status", [204, 200])
def test_empty_response_returns_none(rest_server, rest_request, status):
    rest_server.update(status=status, body=b"")
    assert rest_request("DELETE", "/v2/pods", api_key="key") is None


def test_problem_json_preserves_validation_error(rest_server, rest_request):
    rest_server.update(
        status=422,
        content_type="application/problem+json",
        body=json.dumps(
            {
                "title": "Unprocessable Entity",
                "detail": "request validation failed",
                "errors": ["$.name is required"],
            }
        ).encode(),
    )
    with pytest.raises(QueryError) as raised:
        rest_request("post", "/v2/pods", api_key="key", json={})
    assert str(raised.value) == "request validation failed"
    assert raised.value.query == "POST /v2/pods"
    assert raised.value.status_code == 422
    assert raised.value.errors == ["$.name is required"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b'{"title":"service unavailable"}', "service unavailable"),
        (b"upstream failure", "upstream failure"),
        (b'["unexpected array"]', '["unexpected array"]'),
        (b"\xff", "\ufffd"),
        (b"", "Request failed with status 503"),
    ],
)
def test_error_body_fallbacks_do_not_mask_http_failure(
    rest_server, rest_request, body, message
):
    rest_server.update(
        status=503, content_type="application/problem+json; charset=utf-8", body=body
    )
    with pytest.raises(QueryError) as raised:
        rest_request("POST", "/v2/pods", api_key="key", json={"name": "pod"})
    assert str(raised.value) == message
    assert raised.value.status_code == 503
    assert raised.value.errors == []
    assert rest_server["hits"] == 1


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_mutation_is_not_repeated_after_disconnect(rest_server, rest_request, method):
    rest_server["disconnect"] = True
    with pytest.raises((requests.ConnectionError, aiohttp.ClientConnectionError)):
        rest_request(method, "/v2/pods", api_key="key", json={"name": "pod"})
    assert rest_server["hits"] == 1


async def _wait_for_event(event):
    async def poll():
        while not event.is_set():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=2)


@pytest.mark.asyncio
async def test_cancellation_closes_pending_request_without_repeating_mutation(rest_server):
    rest_server["stall"] = True
    pending = asyncio.create_task(
        run_rest_request_async(
            "POST", "/v2/pods", api_key="key", json={"name": "pod"}, timeout=5
        )
    )
    try:
        await _wait_for_event(rest_server["started"])
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=1)
        await _wait_for_event(rest_server["disconnected"])
        assert rest_server["hits"] == 1
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_timeout_closes_pending_request_without_repeating_mutation(rest_server):
    rest_server["stall"] = True
    with pytest.raises(asyncio.TimeoutError):
        await run_rest_request_async(
            "POST", "/v2/pods", api_key="key", json={"name": "pod"}, timeout=0.5
        )
    await _wait_for_event(rest_server["disconnected"])
    assert rest_server["hits"] == 1
