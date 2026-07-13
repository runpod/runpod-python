"""tests for transient-failure retry in the http layer."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import aiohttp
import pytest

from runpod.apps.errors import EndpointNotFound
from runpod.apps.targets import (
    RETRY_ATTEMPTS,
    RETRYABLE_STATUSES,
    _post_json,
)


@pytest.fixture
def flaky_server():
    """local server whose first responses are scripted status codes."""
    state = {"script": [], "hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            state["hits"] += 1
            if state["hits"] <= len(state["script"]):
                status = state["script"][state["hits"] - 1]
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b"scripted")
                return
            body = json.dumps({"ok": True, "hits": state["hits"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.state = state
    server.url = f"http://127.0.0.1:{server.server_address[1]}/run"
    yield server
    server.shutdown()


async def test_retries_transient_5xx_then_succeeds(flaky_server):
    flaky_server.state["script"] = [520, 502]
    with patch("runpod.apps.targets.RETRY_BASE_DELAY", 0.01):
        data = await _post_json(flaky_server.url, {"x": 1}, {}, timeout=10)
    assert data["ok"] is True
    assert data["hits"] == 3


async def test_exhausted_retries_raise_last_error(flaky_server):
    flaky_server.state["script"] = [503] * (RETRY_ATTEMPTS + 2)
    with patch("runpod.apps.targets.RETRY_BASE_DELAY", 0.01):
        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await _post_json(flaky_server.url, {"x": 1}, {}, timeout=10)
    assert exc_info.value.status == 503
    assert flaky_server.state["hits"] == RETRY_ATTEMPTS


async def test_404_not_retried_maps_to_endpoint_not_found(flaky_server):
    flaky_server.state["script"] = [404]
    with pytest.raises(EndpointNotFound):
        await _post_json(
            flaky_server.url,
            {"x": 1},
            {},
            timeout=10,
            app_name="a",
            resource_name="r",
        )
    assert flaky_server.state["hits"] == 1


async def test_client_4xx_not_retried(flaky_server):
    flaky_server.state["script"] = [400]
    with pytest.raises(aiohttp.ClientResponseError) as exc_info:
        await _post_json(flaky_server.url, {"x": 1}, {}, timeout=10)
    assert exc_info.value.status == 400
    assert flaky_server.state["hits"] == 1


def test_429_is_retryable():
    assert 429 in RETRYABLE_STATUSES


async def test_connection_error_retried(unused_tcp_port):
    # nothing listening: pure connection failures, all attempts consumed
    url = f"http://127.0.0.1:{unused_tcp_port}/run"
    with patch("runpod.apps.targets.RETRY_BASE_DELAY", 0.01):
        with pytest.raises(aiohttp.ClientConnectionError):
            await _post_json(url, {"x": 1}, {}, timeout=5)
