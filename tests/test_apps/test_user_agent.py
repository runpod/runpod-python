"""apps attribution on control-plane and data-plane HTTP requests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
import requests

import runpod
from runpod.api.graphql import run_graphql_query, run_graphql_query_async
from runpod.api.rest import run_rest_request, run_rest_request_async
from runpod.apps import gpu, logs, tasks
from runpod.apps.api import AppsApiClient, FlashTaskApiClient
from runpod.apps.auth import agent_login
from runpod.apps.gpu import live_pool_members, live_pool_members_async
from runpod.apps.targets import LiveTarget, SentinelTarget
from runpod.apps.utils.network import get_json
from runpod.user_agent import APPS_USER_AGENT, USER_AGENT


@pytest.fixture
def server(monkeypatch):
    assert "Integration/apps" in APPS_USER_AGENT.split()
    state = SimpleNamespace(requests=[], responses=[{}], user_agent=APPS_USER_AGENT)

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            state.requests.append((self.command, self.path, dict(self.headers)))
            data = state.responses.pop(0)
            status, data = data if isinstance(data, tuple) else (200, data)
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = respond

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    state.url = f"http://127.0.0.1:{httpd.server_port}"
    monkeypatch.setenv("RUNPOD_API_BASE_URL", state.url)
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    monkeypatch.setattr(runpod, "api_key", "test-key")
    monkeypatch.setattr(runpod, "endpoint_url_base", state.url)
    monkeypatch.setattr(logs, "HAPI_BASE", state.url)
    monkeypatch.setattr(tasks, "_proxy_url", lambda pod_id: state.url)
    monkeypatch.setattr("runpod.apps.context.deployed_identity", lambda: None)
    gpu._clear_catalog_cache()
    try:
        yield state
        assert state.requests
        assert not state.responses
        for _, _, headers in state.requests:
            assert headers["User-Agent"] == state.user_agent
    finally:
        gpu._clear_catalog_cache()
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.mark.parametrize("anonymous", [False, True])
@pytest.mark.parametrize("retry", [False, True])
async def test_graphql_lifecycle(server, anonymous, retry):
    server.responses = [{"data": {"ok": True}}]
    client = AppsApiClient(api_key="test-key")
    assert await client._execute("query {}", anonymous=anonymous, retry=retry)
    headers = server.requests[0][2]
    assert ("Authorization" not in headers) == anonymous


async def test_graphql_retry_keeps_attribution(server):
    server.responses = [{"data": {"ok": True}}]
    original = run_graphql_query_async
    attempts = []

    async def query(*args, **kwargs):
        attempts.append(kwargs["headers"])
        if len(attempts) < 4:
            raise aiohttp.ClientConnectionError("reset")
        return await original(*args, **kwargs)

    with (
        patch("runpod.apps.api.run_graphql_query_async", query),
        patch("runpod.apps.api._TRANSPORT_RETRIES", 4),
        patch("asyncio.sleep", AsyncMock()),
    ):
        await AppsApiClient()._execute("query {}", retry=True)
    assert attempts == [{"User-Agent": APPS_USER_AGENT}] * 4


async def test_verify_api_key(server):
    server.responses = [{"data": {"myself": {"id": "user"}}}]
    assert await AppsApiClient().verify_api_key("test-key")


@pytest.mark.parametrize("runtime", [None, "endpoint", "pod"])
async def test_task_lifecycle_preserves_runtime_auth(server, runtime, monkeypatch):
    headers = (
        {
            "Authorization": "Bearer runtime-key",
            f"X-RunPod-{runtime.title()}-Id": "source",
        }
        if runtime
        else None
    )
    monkeypatch.setattr("runpod.apps.utils.network.runtime_headers", lambda: headers)
    server.responses = [{"data": {"ok": True}}]
    await FlashTaskApiClient()._execute("query {}", {})
    sent = server.requests[0][2]
    assert sent["Authorization"] == (
        "Bearer runtime-key" if runtime else "Bearer test-key"
    )
    for key, value in (headers or {}).items():
        assert sent[key] == value


@pytest.mark.parametrize(
    "method,args,response",
    [
        (
            "save_endpoint",
            (
                {
                    "name": "ep",
                    "template": {"imageName": "image"},
                    "scalerType": "QUEUE_DELAY",
                    "scalerValue": 4,
                },
            ),
            {"id": "ep"},
        ),
        ("delete_endpoint", ("ep",), {}),
        ("list_my_endpoints", (), {"endpoints": []}),
        ("endpoint_workers", ("ep",), {"workers": []}),
        ("terminate_pod", ("pod",), {}),
        ("gpu_stock_status", ("gpu", "dc"), {"dataCenters": []}),
        ("cpu_stock_status", ("cpu3g-2-8", "dc"), {"dataCenters": []}),
        ("network_volume_datacenters", (), {"dataCenters": []}),
        ("create_global_volume", ("data",), {"id": "volume"}),
        ("create_network_volume", ("data", 10, "dc"), {"id": "volume"}),
        ("list_registry_auths", (), {"registries": []}),
        ("create_registry_auth", ("registry", "user", "password"), {"id": "registry"}),
        ("delete_registry_auth", ("registry",), {}),
    ],
)
async def test_rest_resource_operations(server, method, args, response):
    server.responses = [response]
    await getattr(AppsApiClient(), method)(*args)


async def test_rest_endpoint_update(server):
    server.responses = [{"id": "ep", "type": "QUEUE"}, {"id": "ep"}]
    await AppsApiClient().save_endpoint({"id": "ep", "workersMin": 1})
    assert [request[0] for request in server.requests] == ["GET", "PATCH"]


@pytest.mark.parametrize(
    "kind,collection", [("global", "globalVolumes"), ("network", "networkVolumes")]
)
async def test_rest_pagination(server, kind, collection):
    server.responses = [
        {
            collection: [{"id": "one"}],
            "pagination": {"hasNextPage": True, "nextCursor": "next"},
        },
        {collection: [{"id": "two"}], "pagination": {"hasNextPage": False}},
    ]
    assert len(await getattr(AppsApiClient(), f"list_{kind}_volumes")()) == 2
    assert server.requests[1][1].endswith("?cursor=next")


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_gpu_catalog(server, asynchronous):
    server.responses = [{"gpus": [{"id": "gpu", "pool": "pool"}]}]
    result = await live_pool_members_async() if asynchronous else live_pool_members()
    assert result == {"pool": {"gpu"}}


async def test_artifact_upload(server, tmp_path):
    tarball = tmp_path / "app.tar.gz"
    tarball.write_bytes(b"archive")
    await AppsApiClient().upload_tarball(server.url, str(tarball))
    assert server.requests[0][2]["Content-Type"] == "application/gzip"


async def test_email_authentication(server):
    server.responses = [
        (201, {"attemptId": "attempt"}),
        {"credential": {"apiKey": "key"}},
    ]
    assert await agent_login("owner@example.com", read_code=lambda: "123456") == "key"
    assert all("Authorization" not in r[2] for r in server.requests)


@pytest.mark.parametrize(
    "target", [LiveTarget("dev"), SentinelTarget("app", "env", "work")]
)
@pytest.mark.parametrize(
    "operation", ["run", "invoke", "status", "cancel", "retry", "stream", "http"]
)
async def test_queue_and_load_balancer(server, target, operation, monkeypatch):
    server.responses = [{"id": "job", "status": "COMPLETED", "output": {}}]
    client = target._client
    if operation == "http":
        original = aiohttp.ClientSession._request

        async def request(session, method, url, **kwargs):
            assert url.startswith("https://")
            return await original(session, method, server.url + "/route", **kwargs)

        monkeypatch.setattr(aiohttp.ClientSession, "_request", request)
        await client.http("POST", "/route", {})
    elif operation == "stream":
        assert [chunk async for chunk in client.stream("job", timeout=10)] == []
    elif operation == "invoke":
        await client.invoke({"input": {}}, timeout=10)
    else:
        await getattr(client, operation)({"input": {}} if operation == "run" else "job")


@pytest.mark.parametrize(
    "operation", ["wait_ready", "submit", "set_timeout", "poll_result"]
)
async def test_task_proxy(server, operation):
    transport = tasks._TaskTransport(None)
    transport.pod_id = "pod"
    if operation == "submit":
        await transport.submit({"args": []})
    elif operation == "set_timeout":
        server.responses = [{"timeout": 5}]
        await transport.set_timeout(5)
    else:
        await getattr(transport, operation)()
    if operation == "wait_ready":
        assert "Authorization" not in server.requests[0][2]
    else:
        assert server.requests[0][2]["Authorization"] == f"Bearer {transport.token}"


@pytest.mark.parametrize(
    "log_type,endpoint", [("all", None), ("container", None), ("system", "ep")]
)
async def test_log_streams(server, log_type, endpoint):
    server.responses = [b'data: {"line":"ready"}\n\n']
    assert [
        event
        async for event in logs.stream_pod_logs(
            "pod", log_type=log_type, endpoint_id=endpoint
        )
    ] == [{"line": "ready"}]


async def test_log_snapshot(server):
    server.responses = [{"container": ["ready"]}]
    assert await logs.pod_logs("pod") == {"container": ["ready"]}


async def test_lightweight_json_client(server):
    await get_json(server.url, {"Authorization": "Bearer test-key"}, 10)
    assert server.requests[0][2]["Authorization"] == "Bearer test-key"


@pytest.mark.parametrize("user_agent", [None, APPS_USER_AGENT])
def test_volume_lookup(server, user_agent):
    from runpod.network_volume_transfer import _locate

    server.user_agent = user_agent or USER_AGENT
    server.responses = [
        {
            "networkVolumes": [{"id": "vol", "name": "data", "dataCenter": "EU-RO-1"}],
            "pagination": {"hasNextPage": False},
        }
    ]
    assert (
        _locate(
            "data", **({"user_agent": user_agent} if user_agent is not None else {})
        )[0]
        == "vol"
    )


@pytest.mark.parametrize("user_agent", [None, APPS_USER_AGENT])
def test_s3_transfer_user_agent(user_agent):
    from runpod.network_volume_transfer import _make_transfer

    transfer = _make_transfer(
        "vol",
        "test-access",
        "test-secret",
        None,
        1,
        None,
        location=("vol", "EU-RO-1", "https://s3.example.test"),
        **({"user_agent": user_agent} if user_agent is not None else {}),
    )
    requests = []

    def capture(request, **kwargs):
        requests.append(request)
        raise RuntimeError("captured request")

    transfer.client.meta.events.register("before-send.s3", capture)
    try:
        with pytest.raises(RuntimeError, match="captured request"):
            transfer.client.head_object(Bucket="vol", Key="file")
        header = requests[0].headers["User-Agent"].decode()
        assert (user_agent or USER_AGENT) in header
        assert ("Integration/apps" in header.split()) == (user_agent is not None)
    finally:
        transfer.client.close()


@pytest.mark.parametrize("caller", ["apps", "cli"])
@pytest.mark.parametrize("operation", ["upload", "download"])
def test_volume_transfer_caller_attribution(
    server, monkeypatch, tmp_path, caller, operation
):
    import boto3.session
    from click.testing import CliRunner

    from runpod.apps import NetworkVolume
    from runpod.rp_cli.main import cli

    server.user_agent = APPS_USER_AGENT if caller == "apps" else USER_AGENT
    server.responses = [
        {
            "networkVolumes": [{"id": "vol", "name": "data", "dataCenter": "EU-RO-1"}],
            "pagination": {"hasNextPage": False},
        }
    ]
    monkeypatch.setattr("runpod.rp_cli.update.start_background_check", lambda: None)
    local = tmp_path / "model.bin"
    local.write_bytes(b"model")
    source, target = (
        (str(local), "model.bin")
        if operation == "upload"
        else ("model.bin", str(local))
    )
    headers = []
    clients = []
    real_client = boto3.session.Session.client

    def capture(request, **kwargs):
        headers.append(request.headers["User-Agent"].decode())
        raise RuntimeError("captured s3 request")

    def client(session, *args, **kwargs):
        result = real_client(session, *args, **kwargs)
        result.meta.events.register("before-send.s3", capture)
        clients.append(result)
        return result

    monkeypatch.setattr(boto3.session.Session, "client", client)
    try:
        if caller == "apps":
            with pytest.raises(RuntimeError, match="captured s3 request"):
                getattr(NetworkVolume("data"), operation)(
                    source, target, access_key="test-access", secret_key="test-secret"
                )
        else:
            result = CliRunner().invoke(
                cli,
                [
                    "volume",
                    operation,
                    "data",
                    source,
                    target,
                    "--s3-access-key",
                    "test-access",
                    "--s3-secret-key",
                    "test-secret",
                ],
            )
            assert result.exit_code == 1
            assert "captured s3 request" in result.output
        assert len(server.requests) == 1
        assert len(headers) == 1
        assert server.user_agent in headers[0]
        assert ("Integration/apps" in headers[0].split()) == (caller == "apps")
    finally:
        for client in clients:
            client.close()


async def test_plain_sdk_transports_keep_default_user_agent(server):
    server.user_agent = USER_AGENT
    server.responses = [{}, {}, {"data": {}}, {"data": {}}]
    run_rest_request("GET", "/v2/pods")
    await run_rest_request_async("GET", "/v2/pods")
    run_graphql_query("query {}")
    await run_graphql_query_async("query {}")
    for _, _, headers in server.requests:
        assert headers["User-Agent"] == USER_AGENT


def test_plain_serverless_calls_keep_requests_user_agent(server):
    server.user_agent = requests.utils.default_user_agent()
    server.responses = [
        {"id": "job"},
        {"id": "job", "status": "COMPLETED", "output": {}},
    ]
    endpoint = runpod.Endpoint("ep")
    try:
        endpoint.run({"value": 1})
        assert endpoint.run_sync({"value": 1}) == {}
    finally:
        endpoint.rp_client.rp_session.close()
