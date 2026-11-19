"""shared transport ownership, route resolution, and credential boundaries."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import aiohttp
import pytest
import requests

import runpod
from runpod.api.http import HTTPClient, SyncHTTPClient
from runpod.api.rest import AsyncRESTClient, RESTClient
from runpod.api.routes import (
    Route,
    endpoint_base_url,
    host_api_base_url,
    load_balancer_base_url,
    pod_proxy_base_url,
)


@pytest.fixture
def http_server():
    state = {"requests": [], "status": 200, "body": b'{"ok":true}'}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            state["requests"].append(
                (self.command, self.path, dict(self.headers), body, self.client_address)
            )
            payload = state["body"]
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Set-Cookie", "session=should-not-be-replayed")
            self.send_header("Location", "/redirect-target")
            self.end_headers()
            self.wfile.write(payload)

        do_POST = do_PUT = do_GET

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/prefix/", state
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_route_encodes_parameters_and_preserves_service_prefix():
    route = Route("get", "/v2/pods/{pod_id}", pod_id="id/with ?#%")
    assert route.method == "GET"
    assert route.url("https://api.example/base/") == (
        "https://api.example/base/v2/pods/id%2Fwith%20%3F%23%25"
    )


@pytest.mark.parametrize(
    "path", ["https://other.example/x", "//other.example/x", "../x", "/x#secret"]
)
def test_route_cannot_override_service_origin(path):
    with pytest.raises(ValueError):
        Route("GET", path).url("https://api.example/prefix")


@pytest.mark.parametrize(
    "base", ["file:///tmp/data", "https://user:password@example.com", "relative"]
)
def test_route_requires_http_origin_without_credentials(base):
    with pytest.raises(ValueError):
        Route("GET", "/v2/pods").url(base)


def test_signed_upload_url_is_preserved_exactly():
    url = "https://storage.example/artifact?signature=a%2Fb&expires=123"
    assert Route("PUT", "").url(url) == url
    with pytest.raises(ValueError):
        Route("PUT", "/another").url(url)


def test_route_encodes_literal_spaces_without_requoting_parameters():
    assert (
        Route("GET", "/hello world/{value}?q=a b", value="a/b").url(
            "https://api.example/base path/"
        )
        == "https://api.example/base%20path/hello%20world/a%2Fb?q=a%20b"
    )


def test_service_bases_respect_configuration(monkeypatch):
    monkeypatch.delenv("RUNPOD_ENDPOINT_BASE_URL", raising=False)
    monkeypatch.delenv("RUNPOD_PROXY_DOMAIN", raising=False)
    monkeypatch.setenv("RUNPOD_API_BASE_URL", "http://control.example/prefix/")
    monkeypatch.setenv("RUNPOD_HAPI_URL", "http://logs.example/prefix/")
    monkeypatch.setattr(runpod, "endpoint_url_base", "http://worker.example:8080/v2/")
    assert Route("POST", "/graphql").url() == "http://control.example/prefix/graphql"
    assert host_api_base_url() == "http://logs.example/prefix"
    assert (
        endpoint_base_url("endpoint/id")
        == "http://worker.example:8080/v2/endpoint%2Fid"
    )
    assert (
        load_balancer_base_url("endpoint-id")
        == "http://endpoint-id.worker.example:8080"
    )
    assert pod_proxy_base_url("pod-id", 8080) == "https://pod-id-8080.proxy.runpod.net"


@pytest.mark.parametrize(
    "resource_id", ["other.example/", "id@evil.example", "id:123", ""]
)
def test_resource_hostnames_reject_url_components(resource_id):
    with pytest.raises(ValueError):
        load_balancer_base_url(resource_id)
    with pytest.raises(ValueError):
        pod_proxy_base_url(resource_id, 8080)


async def test_async_client_reuses_and_closes_owned_session(http_server):
    base, state = http_server
    client = HTTPClient(base)
    async with client:
        session = client.session
        assert await client.request(Route("GET", "/first")) == {"ok": True}
        assert await client.request(Route("GET", "/second")) == {"ok": True}
        assert client.session is session
        assert not session.closed
    assert session.closed
    assert client.session is None
    assert state["requests"][0][4] == state["requests"][1][4]
    assert all("Cookie" not in request[2] for request in state["requests"])


async def test_borrowed_async_session_is_not_closed(http_server):
    base, state = http_server
    async with aiohttp.ClientSession(headers={"User-Agent": "caller-agent"}) as session:
        async with HTTPClient(base, session=session) as client:
            assert await client.request(Route("GET", "/pods")) == {"ok": True}
        assert not session.closed
    assert state["requests"][0][2]["User-Agent"] == "caller-agent"


async def test_concurrent_one_shot_calls_have_independent_lifetimes(http_server):
    base, state = http_server
    client = HTTPClient(base)
    results = await asyncio.gather(
        *(client.request(Route("GET", "/{index}", index=index)) for index in range(4))
    )
    assert results == [{"ok": True}] * 4
    assert len(state["requests"]) == 4
    assert client.session is None


async def test_raw_transport_never_reads_account_credentials(http_server, monkeypatch):
    base, state = http_server
    monkeypatch.setattr(runpod, "api_key", "account-secret")
    monkeypatch.setenv("RUNPOD_API_KEY", "environment-secret")
    client = HTTPClient(base)
    await client.request(Route("GET", "/anonymous"))
    await client.request(
        Route("GET", "/worker"), headers={"Authorization": "Bearer task-token"}
    )
    assert "Authorization" not in state["requests"][0][2]
    assert state["requests"][1][2]["Authorization"] == "Bearer task-token"


async def test_redirect_is_not_followed_with_credentials(http_server):
    base, state = http_server
    state["status"] = 302
    with pytest.raises(aiohttp.ClientResponseError) as caught:
        await HTTPClient(base).request(
            Route("GET", "/start"), headers={"Authorization": "Bearer private-key"}
        )
    assert caught.value.status == 302
    assert len(state["requests"]) == 1


async def test_response_decoding_and_streaming_errors(http_server):
    base, state = http_server
    async with HTTPClient(base) as client:
        state.update(status=204, body=b"")
        assert await client.request(Route("GET", "/empty")) is None
        state.update(status=200, body=b"plain text")
        assert (
            await client.request(Route("GET", "/text"), response_type="text")
            == "plain text"
        )
        state.update(status=403, body=b'{"detail":"denied"}')
        async with client.stream(Route("GET", "/error")) as response:
            assert response.status == 403
            assert await response.json() == {"detail": "denied"}
        with pytest.raises(aiohttp.ClientResponseError):
            await client.request(Route("GET", "/error"))


async def test_async_rest_client_reuses_pool_and_explicit_key(http_server, monkeypatch):
    base, state = http_server
    monkeypatch.setattr(runpod, "api_key", "wrong-key")
    async with AsyncRESTClient("right-key", base_url=base) as client:
        session = client.session
        await client.request(Route("GET", "/v2/pods"))
        await client.request(Route("POST", "/v2/pods"), json={"name": "pod"})
        assert client.session is session
    assert session.closed
    assert all(
        request[2]["Authorization"] == "Bearer right-key"
        for request in state["requests"]
    )
    assert json.loads(state["requests"][1][3]) == {"name": "pod"}


def test_sync_client_reuses_pool_and_borrows_sessions(http_server):
    base, state = http_server
    with requests.Session() as session:
        session.headers["User-Agent"] = "caller-agent"
        with SyncHTTPClient(base, session=session) as client:
            for path in ("/first", "/second"):
                with client.stream(Route("GET", path)) as response:
                    assert response.json() == {"ok": True}
        assert session.get(f"{base}third").status_code == 200
    assert state["requests"][0][4] == state["requests"][1][4]
    assert state["requests"][0][2]["User-Agent"] == "caller-agent"


def test_sync_rest_client_reuses_pool_and_closes_responses(http_server):
    base, state = http_server
    with RESTClient("key", base_url=base) as client:
        session = client.session
        assert client.request(Route("GET", "/v2/pods")) == {"ok": True}
        assert client.request(Route("GET", "/v2/pods")) == {"ok": True}
        assert client.session is session
    assert client.session is None
    assert state["requests"][0][4] == state["requests"][1][4]


async def test_mutation_preserves_borrowed_session_middleware(http_server):
    base, state = http_server
    calls = []

    async def middleware(request, handler):
        calls.append(request.method)
        request.headers["X-Caller"] = "preserved"
        return await handler(request)

    async with aiohttp.ClientSession(middlewares=(middleware,)) as session:
        async with HTTPClient(base, session=session) as client:
            await client.request(Route("PUT", "/first"))
        async with session.get(f"{base}second") as response:
            await response.read()
        assert session._retry_connection is True
    assert calls == ["PUT", "GET"]
    assert all(request[2]["X-Caller"] == "preserved" for request in state["requests"])


@pytest.mark.parametrize("status", [302, 307, 308])
def test_graphql_rejects_redirect_before_decoding(http_server, monkeypatch, status):
    from runpod.api.graphql import run_graphql_query
    from runpod.error import QueryError

    base, state = http_server
    state.update(status=status, body=b"")
    monkeypatch.setenv("RUNPOD_API_BASE_URL", base)
    with pytest.raises(QueryError, match="unexpected redirect") as caught:
        run_graphql_query("query { myself { id } }", api_key="synthetic-key")
    assert caught.value.status_code == status
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("status", [302, 307, 308])
async def test_async_graphql_rejects_redirect_before_decoding(
    http_server, monkeypatch, status
):
    from runpod.api.graphql import run_graphql_query_async
    from runpod.error import QueryError

    base, state = http_server
    state.update(status=status, body=b"")
    monkeypatch.setenv("RUNPOD_API_BASE_URL", base)
    with pytest.raises(QueryError, match="unexpected redirect") as caught:
        await run_graphql_query_async(
            "query { myself { id } }", api_key="synthetic-key"
        )
    assert caught.value.status_code == status
    assert len(state["requests"]) == 1
