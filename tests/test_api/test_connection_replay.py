"""connection failure types and replay boundaries."""

import socket
import ssl
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.client_reqrep import ConnectionKey

from runpod.api.http import HTTPClient
from runpod.api.rest import AsyncRESTClient
from runpod.api.routes import Route


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize(
    "error_type,cause",
    [
        (aiohttp.ClientConnectorError, OSError("connection failed")),
        (aiohttp.ClientConnectorDNSError, socket.gaierror("name lookup failed")),
        (aiohttp.ClientSSLError, ssl.SSLError("TLS negotiation failed")),
        (
            aiohttp.ClientConnectorCertificateError,
            ssl.CertificateError("invalid certificate"),
        ),
    ],
)
async def test_connector_failures_preserve_type_and_details(
    monkeypatch, method, error_type, cause
):
    key = ConnectionKey("synthetic.invalid", 443, True, True, None, None, None)
    failure = error_type(key, cause)
    async with aiohttp.ClientSession() as session:
        connect = AsyncMock(side_effect=failure)
        monkeypatch.setattr(session.connector, "connect", connect)
        async with AsyncRESTClient(
            "synthetic-key", base_url="https://synthetic.invalid", session=session
        ) as client:
            with pytest.raises(error_type) as caught:
                await client.request(Route(method, "/work"))
        assert caught.value is failure
        assert isinstance(caught.value, OSError)
        assert caught.value.host == "synthetic.invalid"
        assert caught.value.port == 443
        if error_type is aiohttp.ClientConnectorCertificateError:
            assert caught.value.certificate_error is cause
        else:
            assert caught.value.os_error is cause
        connect.assert_awaited_once()
        assert not session.closed


@pytest.fixture
async def disconnect_server():
    requests = []

    async def handler(request):
        await request.read()
        requests.append((request.method, request.transport))
        if len(requests) == 2:
            request.transport.close()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", requests
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
@pytest.mark.parametrize("rest", [True, False])
async def test_only_reads_replay_after_keep_alive_disconnect(
    disconnect_server, method, rest
):
    base, requests = disconnect_server
    calls = []

    async def middleware(request, handler):
        calls.append(request.method)
        return await handler(request)

    async with aiohttp.ClientSession(middlewares=(middleware,)) as session:
        retry_policy = session._retry_connection
        client = (
            AsyncRESTClient("synthetic-key", base_url=base, session=session)
            if rest
            else HTTPClient(base, session=session)
        )
        async with client:
            assert await client.request(Route("GET", "/warm")) == {"ok": True}
            if method == "GET":
                assert await client.request(Route(method, "/work")) == {"ok": True}
            else:
                with pytest.raises(aiohttp.ClientConnectionError):
                    await client.request(Route(method, "/work"))
        assert not session.closed
        assert session._retry_connection is retry_policy
    assert calls == (["GET", "GET", "GET"] if method == "GET" else ["GET", method])
    assert [request[0] for request in requests] == calls
    assert requests[0][1] is requests[1][1]
    if method == "GET":
        assert requests[2][1] is not requests[1][1]
