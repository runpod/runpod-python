"""HTTP transport routing, ownership, and credentials for Apps operations."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

import runpod
from runpod.api.http import HTTPClient
from runpod.apps.api import AppsApiClient
from runpod.apps.logs import pod_logs, stream_pod_logs
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.targets import QueueClient, _wait_terminal
from runpod.apps.tasks import TaskExecution
from runpod.apps.utils.network import get_json
from runpod.user_agent import APPS_USER_AGENT


@pytest.mark.parametrize("operation", ["status", "cancel", "retry", "stream"])
async def test_queue_routes_escape_ids_and_preserve_base_path(monkeypatch, operation):
    monkeypatch.setattr(runpod, "endpoint_url_base", "http://custom.test:8080/api/v2/")
    client = QueueClient("endpoint/id ?#%", lambda: {"Authorization": "Bearer worker"})
    with patch(
        "runpod.apps.targets._request_json",
        AsyncMock(return_value={"status": "COMPLETED"}),
    ) as request:
        if operation == "stream":
            assert [
                chunk async for chunk in client.stream("job/id ?#%", timeout=5)
            ] == []
        else:
            await getattr(client, operation)("job/id ?#%")
    assert request.call_args.args[:3] == (
        "GET" if operation in {"status", "stream"} else "POST",
        f"http://custom.test:8080/api/v2/endpoint%2Fid%20%3F%23%25/{operation}/job%2Fid%20%3F%23%25",
        {"Authorization": "Bearer worker"},
    )
    assert request.call_args.kwargs["job_id"] == (
        "job/id ?#%" if operation == "status" else None
    )


async def test_poll_route_escapes_job_id():
    with (
        patch("runpod.apps.targets.asyncio.sleep", AsyncMock()),
        patch(
            "runpod.apps.targets._get_json",
            AsyncMock(return_value={"status": "COMPLETED"}),
        ) as request,
    ):
        await _wait_terminal(
            "http://custom.test/prefix/endpoint",
            {"id": "job/id?", "status": "IN_QUEUE"},
            {},
            5,
        )
    assert (
        request.call_args.args[0]
        == "http://custom.test/prefix/endpoint/status/job%2Fid%3F"
    )


async def test_lb_transport_preserves_custom_scheme_and_port(monkeypatch):
    monkeypatch.setattr(runpod, "endpoint_url_base", "http://custom.test:8080/api/v2")
    response = MagicMock(status=200)
    response.text = AsyncMock(return_value='{"ok": true}')
    with patch("runpod.apps.targets.HTTPClient") as factory:
        client = factory.return_value.__aenter__.return_value
        client.stream = MagicMock()
        client.stream.return_value.__aenter__.return_value = response
        result = await QueueClient("endpoint", lambda: {}).http(
            "POST", "/work", timeout=5
        )
    assert result == {"ok": True}
    factory.assert_called_once_with(base_url="http://endpoint.custom.test:8080")
    assert client.stream.call_args.args[0].resolved_path == "/work"
    assert client.stream.call_args.args[0].method == "POST"


async def test_host_log_base_is_read_per_operation(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "account-key")
    with patch("runpod.apps.logs.HTTPClient") as factory:
        response = MagicMock(status=200)
        response.json = AsyncMock(return_value={"container": []})
        factory.return_value.stream.return_value.__aenter__.return_value = response
        for base in ("http://one.test/prefix", "http://two.test/other"):
            monkeypatch.setenv("RUNPOD_HAPI_URL", base)
            await pod_logs("pod/id?#")
            factory.assert_called_with(base_url=base)
            route = factory.return_value.stream.call_args.args[0]
            assert route.url(base) == f"{base}/v1/pod/pod%2Fid%3F%23/logs"


async def test_worker_log_route_escapes_both_ids(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "account-key")
    monkeypatch.setenv("RUNPOD_API_BASE_URL", "http://custom.test/prefix")

    async def content():
        yield b'data: {"line": "ready"}\n'
        yield b"\n"

    response = MagicMock(status=200)
    response.content = content()
    with patch("runpod.apps.logs.HTTPClient") as factory:
        client = factory.return_value.__aenter__.return_value
        client.stream = MagicMock()
        client.stream.return_value.__aenter__.return_value = response
        assert [
            event
            async for event in stream_pod_logs(
                "pod/id", endpoint_id="endpoint?", log_type="system"
            )
        ] == [{"line": "ready"}]
    route = client.stream.call_args.args[0]
    assert (
        route.url()
        == "http://custom.test/prefix/v2/serverless/endpoint%3F/workers/pod%2Fid/logs"
    )


@pytest.mark.parametrize("operation", ["submit", "poll_result", "wait_ready"])
async def test_task_retries_share_client_and_use_only_task_credentials(
    monkeypatch, operation
):
    monkeypatch.setenv("RUNPOD_API_KEY", "account-key")
    execution = TaskExecution(
        ResourceSpec(kind=ResourceKind.TASK, name="task"), api=MagicMock()
    )
    execution.pod_id = "pod1"
    responses = [MagicMock(status=404), MagicMock(status=200)]
    responses[-1].json = AsyncMock(
        return_value={"status": "DONE", "response": {"ok": True}}
    )
    with (
        patch("runpod.apps.tasks.HTTPClient") as factory,
        patch("runpod.apps.tasks.asyncio.sleep", AsyncMock()),
    ):
        client = factory.return_value.__aenter__.return_value
        client.stream = MagicMock()
        client.stream.return_value.__aenter__.side_effect = responses
        if operation == "submit":
            await execution.submit({"input": {}})
        elif operation == "poll_result":
            assert await execution.poll_result() == {"ok": True}
        else:
            await execution.wait_ready(timeout=5)
    factory.assert_called_once_with(base_url="https://pod1-8080.proxy.runpod.net")
    assert client.stream.call_count == 2
    for call in client.stream.call_args_list:
        assert call.kwargs.get("headers", {}) == (
            {"User-Agent": APPS_USER_AGENT}
            if operation == "wait_ready"
            else {
                "Authorization": f"Bearer {execution.token}",
                "User-Agent": APPS_USER_AGENT,
            }
        )
    factory.return_value.__aexit__.assert_awaited_once()


async def test_upload_reuses_session_and_preserves_presigned_query(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("RUNPOD_API_KEY", "account-key")
    tar = tmp_path / "app.tar.gz"
    tar.write_bytes(b"payload")
    requests = []
    sessions = []
    original_stream = HTTPClient.stream

    @asynccontextmanager
    async def stream(client, *args, **kwargs):
        sessions.append(client.session)
        async with original_stream(client, *args, **kwargs) as response:
            yield response

    async def upload(request):
        requests.append((request.raw_path, dict(request.headers), await request.read()))
        return web.Response(status=503 if len(requests) == 1 else 200)

    app = web.Application()
    app.router.add_put("/upload", upload)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        with (
            patch.object(HTTPClient, "stream", stream),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await AppsApiClient(api_key="explicit-account-key").upload_tarball(
                f"http://127.0.0.1:{port}/upload?signature=a%2Fb%2Bz", str(tar)
            )
    finally:
        await runner.cleanup()
    assert len(requests) == 2
    for path, headers, payload in requests:
        assert path == "/upload?signature=a%2Fb%2Bz"
        assert "Authorization" not in headers
        assert payload == b"payload"
        assert headers["Content-Type"] == "application/gzip"
    assert len(sessions) == 2
    assert sessions[0] is sessions[1]
    assert sessions[0].closed


async def test_network_get_uses_explicit_credentials():
    with patch("runpod.apps.utils.network.HTTPClient") as factory:
        response = MagicMock(status=200)
        response.json = AsyncMock(return_value={"ok": True})
        factory.return_value.stream.return_value.__aenter__.return_value = response
        assert await get_json(
            "http://custom.test/api?q=x", {"Authorization": "Bearer worker"}, 9
        ) == {"ok": True}
    factory.assert_called_once_with(base_url="http://custom.test/api?q=x")
    route = factory.return_value.stream.call_args.args[0]
    assert route.method == "GET"
    assert route.path == ""
    assert factory.return_value.stream.call_args.kwargs == {
        "headers": {"Authorization": "Bearer worker", "User-Agent": APPS_USER_AGENT},
        "timeout": 9,
    }
