"""http diagnostics across the apps transports."""

import asyncio
import html
import json
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import aiohttp
import pytest
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL

from runpod.apps import AppError, AppHTTPError, JobStatusError
from runpod.apps.api import AppsApiClient
from runpod.apps.logs import pod_logs, stream_pod_logs
from runpod.apps.targets import QueueClient
from runpod.apps.tasks import _TaskTransport
from runpod.apps.utils.http import BODY_EXCERPT_LIMIT, BODY_READ_LIMIT, raise_for_status
from runpod.apps.utils.network import get_json


@pytest.fixture
async def error_server(monkeypatch):
    state = {"requests": [], "status": 500}

    async def handler(request):
        await request.read()
        state["requests"].append((request.method, request.path))
        body = (
            "GPU capacity exhausted. "
            + request.headers.get("Authorization", "")
            + " "
            + request.query.get("signature", "")
        )
        return web.Response(status=state["status"], text=body)

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    request = aiohttp.ClientSession._request

    async def local_request(self, method, url, **kwargs):
        local_url = URL(url).with_scheme("http").with_host("127.0.0.1").with_port(port)
        return await request(self, method, local_url, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "_request", local_request)
    monkeypatch.setattr("runpod.apps.targets.asyncio.sleep", AsyncMock())
    monkeypatch.setenv("RUNPOD_API_KEY", "synthetic-account-key")
    try:
        yield state
    finally:
        await runner.cleanup()


@pytest.mark.parametrize(
    "operation,method,path,attempts",
    [
        ("queue_submit", "POST", "/v2/ep/run", 1),
        ("queue_status", "GET", "/v2/ep/status/job", 7),
        ("api", "POST", "/predict", 1),
        ("task_submit", "POST", "/submit", 1),
        ("task_result", "GET", "/result", 1),
        ("task_timeout", "POST", "/timeout", 1),
        ("upload", "PUT", "/build", 4),
        ("snapshot", "GET", "/v1/pod/pod/logs", 1),
        ("stream", "GET", "/v1/pod/pod/logs", 1),
        ("json", "GET", "/data", 1),
    ],
)
async def test_transports_report_body_without_credentials(
    error_server, monkeypatch, tmp_path, operation, method, path, attempts
):
    monkeypatch.setattr("runpod.endpoint_url_base", "https://api.runpod.ai/v2")
    monkeypatch.setattr("runpod.apps.logs.HAPI_BASE", "https://logs.example")
    client = QueueClient(
        "ep", lambda: {"Authorization": "Bearer synthetic-account-key"}
    )
    task = _TaskTransport(None)
    task.pod_id = "pod"
    task.token = "synthetic-task-token"
    with pytest.raises(AppHTTPError) as raised:
        if operation == "queue_submit":
            await client.run({"input": {}})
        elif operation == "queue_status":
            await client.status("job")
        elif operation == "api":
            await client.http("POST", "/predict", {})
        elif operation == "task_submit":
            await task.submit({})
        elif operation == "task_result":
            await task.poll_result()
        elif operation == "task_timeout":
            await task.set_timeout(60)
        elif operation == "upload":
            tarball = tmp_path / "build.tar.gz"
            tarball.write_bytes(b"build")
            upload_url = "https://upload.example/build?signature=synthetic-signature"
            await AppsApiClient().upload_tarball(upload_url, str(tarball))
        elif operation == "snapshot":
            await pod_logs("pod")
        elif operation == "stream":
            await anext(stream_pod_logs("pod"))
        else:
            await get_json(
                "https://api.example/data",
                {"Authorization": "Bearer synthetic-account-key"},
                10,
            )

    error = raised.value
    assert isinstance(error, AppError)
    assert error.status == 500
    assert error.method == method
    assert error.url.endswith(path)
    assert "GPU capacity exhausted." in error.body_excerpt
    assert error_server["requests"] == [(method, path)] * attempts
    if operation == "queue_status":
        assert isinstance(error, JobStatusError)
        assert error.job_id == "job"
    diagnostic = (
        str(error)
        + repr(error)
        + repr(vars(error))
        + "".join(traceback.format_exception(type(error), error, error.__traceback__))
    )
    for value in (
        "synthetic-account-key",
        "synthetic-task-token",
        "synthetic-signature",
    ):
        assert value not in diagnostic
    assert not hasattr(error, "request_info")
    assert not hasattr(error, "headers")
    assert error.__cause__ is None
    assert error.__context__ is None


def response_for(body, *, status=500, url="https://api.example/work", headers=None):
    url = URL(url)
    content = AsyncMock()
    if len(body) > BODY_READ_LIMIT:
        content.readexactly.return_value = body[: BODY_READ_LIMIT + 1]
    else:
        content.readexactly.side_effect = asyncio.IncompleteReadError(
            body, BODY_READ_LIMIT + 1
        )
    return SimpleNamespace(
        status=status,
        method="POST",
        url=url,
        request_info=SimpleNamespace(real_url=url, headers=CIMultiDict(headers or {})),
        history=(),
        content=content,
    )


@pytest.mark.parametrize(
    "body,excerpt",
    [
        (b'{"error":"quota exceeded"}', '{"error":"quota exceeded"}'),
        (b"<html>upstream unavailable</html>", "<html>upstream unavailable</html>"),
        (b"", ""),
        (b"invalid: \xff", "invalid: \ufffd"),
        (b"x" * (BODY_READ_LIMIT * 2), "x" * (BODY_EXCERPT_LIMIT - 3) + "..."),
    ],
)
async def test_body_excerpt_is_bounded_and_tolerates_non_json(body, excerpt):
    response = response_for(body)
    with pytest.raises(AppHTTPError) as raised:
        await raise_for_status(response)
    assert raised.value.body_excerpt == excerpt
    response.content.readexactly.assert_awaited_once_with(BODY_READ_LIMIT + 1)


@pytest.mark.parametrize("status", [200, 204, 302])
async def test_success_does_not_consume_body(status):
    response = response_for(b"success", status=status)
    await raise_for_status(response)
    response.content.readexactly.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [aiohttp.ClientPayloadError("secret"), asyncio.TimeoutError()]
)
async def test_unreadable_error_body_preserves_status(failure):
    response = response_for(b"")
    response.content.readexactly.side_effect = failure
    with pytest.raises(AppHTTPError) as raised:
        await raise_for_status(response)
    assert raised.value.status == 500
    assert raised.value.body_excerpt == "[response body unavailable]"
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None


async def test_cancellation_during_body_read_propagates():
    response = response_for(b"")
    response.content.readexactly.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await raise_for_status(response)


async def test_redacts_reflected_credentials_and_redirect_history():
    token = 'synthetic-token<&"'
    values = [token, quote(token, safe=""), html.escape(token), json.dumps(token)[1:-1]]
    response = response_for(
        (
            "rejected "
            + " ".join(values)
            + " query-secret user-secret password-secret cookie-secret old-token"
        ).encode(),
        url="https://user-secret:password-secret@api.example/work?key=query-secret#fragment-secret",
        headers={"Authorization": f"Bearer {token}", "Cookie": "session=cookie-secret"},
    )
    response.history = (
        response_for(b"", headers={"X-Runpod-Task-Token": "old-token"}),
    )
    with pytest.raises(AppHTTPError) as raised:
        await raise_for_status(response)
    error = raised.value
    assert error.url == "https://api.example/work"
    assert error.body_excerpt.startswith("rejected [redacted]")
    for value in (
        *values,
        "query-secret",
        "user-secret",
        "password-secret",
        "cookie-secret",
        "old-token",
        "fragment-secret",
    ):
        assert value not in str(error) + repr(error) + repr(vars(error))


async def test_redacts_credentials_crossing_excerpt_boundary():
    token = "synthetic-account-key"
    body = ("x" * (BODY_EXCERPT_LIMIT - 10) + token).encode()
    response = response_for(body, headers={"Authorization": f"Bearer {token}"})
    with pytest.raises(AppHTTPError) as raised:
        await raise_for_status(response)
    assert "synthetic" not in raised.value.body_excerpt
    assert len(raised.value.body_excerpt) <= BODY_EXCERPT_LIMIT


async def test_redacts_complete_repeating_credentials_at_excerpt_boundary():
    token = "synthetic-synthetic-"
    body = ("x" * (BODY_EXCERPT_LIMIT - len(token)) + token + "overflow").encode()
    response = response_for(body, headers={"Authorization": f"Bearer {token}"})
    with pytest.raises(AppHTTPError) as raised:
        await raise_for_status(response)
    assert "synthetic" not in raised.value.body_excerpt
