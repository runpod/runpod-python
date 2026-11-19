"""endpoint redirects fail before job response decoding."""

import asyncio

import aiohttp
import pytest
from aiohttp import web

from runpod.endpoint.asyncio import asyncio_runner
from runpod.endpoint.runner import Endpoint, Job
from runpod.error import QueryError


@pytest.fixture
async def redirect_server(monkeypatch):
    state = {"status": 307, "body": b"", "content_type": "text/html", "requests": []}

    async def handler(request):
        await request.read()
        state["requests"].append(request.path)
        return web.Response(
            status=state["status"],
            body=state["body"],
            content_type=state["content_type"],
            headers={"Location": "/destination?signature=synthetic-secret"},
        )

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}/v2"
    monkeypatch.setenv("RUNPOD_ENDPOINT_BASE_URL", base)
    monkeypatch.setattr("runpod.endpoint_url_base", base)
    try:
        yield state
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("status", [301, 307, 308])
@pytest.mark.parametrize(
    "body,content_type",
    [
        (b"", "text/html"),
        (b"<html>synthetic-secret</html>", "text/html"),
        (
            b'{"id":"job","status":"COMPLETED","output":"synthetic-secret"}',
            "application/json",
        ),
    ],
)
@pytest.mark.parametrize(
    "operation",
    ["run", "health", "purge_queue", "status", "output", "stream", "cancel"],
)
@pytest.mark.parametrize("async_runner", [True, False])
async def test_endpoint_rejects_redirect_before_decoding(
    redirect_server, status, body, content_type, operation, async_runner
):
    redirect_server.update(status=status, body=body, content_type=content_type)
    with pytest.raises(QueryError, match="unexpected redirect") as caught:
        if async_runner:
            async with aiohttp.ClientSession() as session:
                endpoint = asyncio_runner.Endpoint(
                    "endpoint", session, api_key="synthetic-key"
                )
                job = asyncio_runner.Job("endpoint", "job", session, endpoint.headers)
                try:
                    if operation == "run":
                        await endpoint.run({})
                    elif operation in {"health", "purge_queue"}:
                        await getattr(endpoint, operation)()
                    elif operation == "stream":
                        await job._fetch_job("stream")
                    elif operation == "output":
                        await job.output(timeout=3)
                    else:
                        await getattr(job, operation)()
                finally:
                    assert not session.closed
        else:

            def invoke():
                with Endpoint("endpoint", api_key="synthetic-key") as endpoint:
                    job = Job("endpoint", "job", endpoint.rp_client)
                    if operation == "run":
                        endpoint.run({})
                    elif operation in {"health", "purge_queue"}:
                        getattr(endpoint, operation)()
                    elif operation == "stream":
                        job._fetch_job("stream")
                    else:
                        getattr(job, operation)()

            await asyncio.to_thread(invoke)
    assert caught.value.status_code == status
    assert "synthetic" not in str(caught.value) + repr(vars(caught.value))
    assert len(redirect_server["requests"]) == 1
    assert redirect_server["requests"][0].startswith("/v2/endpoint/")
