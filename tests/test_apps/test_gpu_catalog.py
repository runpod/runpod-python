"""gpu catalog fetching across async provisioning and synchronous serialization."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

import runpod
from runpod.apps import App, gpu
from runpod.apps.api import AppsApiClient
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import reconcile_endpoints
from runpod.apps.dev import DevSession
from runpod.apps.gpu import live_pool_members_async as _real_live_pool_members_async


MIG = "NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 1g.24gb"
CATALOG = {
    "gpus": [
        {"id": name, "pool": "AMPERE_24"}
        for name in ("NVIDIA L4", "NVIDIA GeForce RTX 3090", "NVIDIA RTX A5000", MIG)
    ]
}


@pytest.fixture(autouse=True)
def clean_catalog(monkeypatch, offline_gpu_catalog):
    _clear_registry()
    gpu._clear_catalog_cache()
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.delenv("RUNPOD_GPU_CATALOG_URL", raising=False)
    monkeypatch.setattr(runpod, "api_key", None)
    monkeypatch.setattr(gpu, "live_pool_members_async", _real_live_pool_members_async)
    monkeypatch.setattr(
        gpu, "live_pool_members", Mock(side_effect=AssertionError("synchronous fetch"))
    )
    yield
    gpu._clear_catalog_cache()
    _clear_registry()


@pytest.fixture
async def catalog(monkeypatch):
    state = SimpleNamespace(
        requests=[],
        started=asyncio.Event(),
        release=asyncio.Event(),
        status=200,
        data=CATALOG,
    )
    state.release.set()

    async def handle(request):
        state.requests.append((request.path, request.headers.get("Authorization")))
        state.started.set()
        await state.release.wait()
        return web.json_response(state.data, status=state.status)

    app = web.Application()
    app.router.add_get("/v2/catalog/gpus", handle)
    app.router.add_get("/custom-catalog", handle)
    runner = web.AppRunner(app, shutdown_timeout=0.1)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        state.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        other = web.TCPSite(runner, "127.0.0.1", 0)
        await other.start()
        other_port = other._server.sockets[0].getsockname()[1]
        state.other_url = f"http://127.0.0.1:{other_port}"
        monkeypatch.setenv("RUNPOD_API_BASE_URL", state.url)
        yield state
    finally:
        state.release.set()
        await runner.cleanup()


async def provision(operation, client, gpu_selection="L4", cpu=None):
    app = App("catalog-test")

    @app.queue(name="work", gpu=gpu_selection, cpu=cpu)
    def work(value):
        return value

    client.save_endpoint = AsyncMock(return_value={"id": "test-endpoint"})
    client.list_my_endpoints = AsyncMock(return_value=[])
    if operation == "reconcile":
        await reconcile_endpoints(
            client, app, {"id": "env", "name": "test"}, "build", "3.12"
        )
    else:
        session = DevSession([app], api=client)
        session._attach_volumes = AsyncMock()
        if operation == "start":
            await session.start()
        else:
            await session.refresh([app])
    return client.save_endpoint.call_args.args[0]


@pytest.mark.parametrize("operation", ["start", "refresh", "reconcile"])
@pytest.mark.parametrize("global_key", [None, "unrelated-global-key"])
async def test_provisioning_uses_explicit_credentials(
    catalog, monkeypatch, operation, global_key
):
    monkeypatch.setattr(runpod, "api_key", global_key)
    if global_key:
        monkeypatch.setenv("RUNPOD_API_KEY", "unrelated-env-key")
    client = AppsApiClient(api_key="explicit-key")
    payload = await provision(operation, client)
    assert f"-{MIG}" in payload["gpuIds"]
    assert catalog.requests == [("/v2/catalog/gpus", "Bearer explicit-key")]


@pytest.mark.parametrize("operation", ["start", "refresh", "reconcile"])
async def test_catalog_wait_keeps_event_loop_responsive(catalog, operation):
    catalog.release.clear()
    client = AppsApiClient(api_key="explicit-key")
    task = asyncio.create_task(provision(operation, client))
    try:
        await asyncio.wait_for(catalog.started.wait(), timeout=1)
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_later(0.01, heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), timeout=1)
        assert not task.done()
        catalog.release.set()
        payload = await asyncio.wait_for(task, timeout=1)
        assert f"-{MIG}" in payload["gpuIds"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("operation", ["start", "refresh", "reconcile"])
async def test_catalog_wait_respects_provisioning_deadline(catalog, operation):
    catalog.release.clear()
    client = AppsApiClient(api_key="explicit-key")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(provision(operation, client), timeout=0.1)
    assert catalog.started.is_set()
    client.save_endpoint.assert_not_called()
    cache = next(iter(gpu._catalog_caches.values()))
    assert cache.failed_at is None
    assert cache.members is None
    catalog.release.set()
    assert MIG in (await client.gpu_pool_members())["AMPERE_24"]


@pytest.mark.parametrize("operation", ["start", "refresh", "reconcile"])
async def test_failed_catalog_uses_static_payload_without_sync_fetch(
    catalog, operation
):
    catalog.status = 500
    payload = await provision(operation, AppsApiClient(api_key="explicit-key"))
    assert payload["gpuIds"] == (
        "AMPERE_24,-NVIDIA GeForce RTX 3090,-NVIDIA RTX A5000"
    )
    assert len(catalog.requests) == 1


@pytest.mark.parametrize("malformed", [False, True])
async def test_failed_catalog_retries_after_cooldown(catalog, malformed, caplog):
    if malformed:
        catalog.data = {"gpus": []}
    else:
        catalog.status = 500
    client = AppsApiClient(api_key="explicit-key")
    assert await client.gpu_pool_members() is None
    assert await client.gpu_pool_members() is None
    assert len(catalog.requests) == 1
    assert "device pins use the static pool table" in caplog.text
    assert "explicit-key" not in caplog.text
    cache = next(iter(gpu._catalog_caches.values()))
    cache.failed_at -= gpu.CATALOG_RETRY_SECONDS
    catalog.status = 200
    catalog.data = CATALOG
    assert MIG in (await client.gpu_pool_members())["AMPERE_24"]
    assert len(catalog.requests) == 2
    another_client = AppsApiClient(api_key="explicit-key")
    assert MIG in (await another_client.gpu_pool_members())["AMPERE_24"]
    assert len(catalog.requests) == 2


async def test_cache_is_scoped_to_credentials_and_catalog_url(catalog, monkeypatch):
    first = AppsApiClient(api_key="first-key")
    assert MIG in (await first.gpu_pool_members())["AMPERE_24"]
    catalog.data = {"gpus": [{"id": "NVIDIA L4", "pool": "AMPERE_24"}]}
    second = AppsApiClient(api_key="second-key")
    assert await second.gpu_pool_members() == {"AMPERE_24": {"NVIDIA L4"}}
    assert MIG in (await first.gpu_pool_members())["AMPERE_24"]
    assert len(catalog.requests) == 2
    monkeypatch.setenv("RUNPOD_API_BASE_URL", catalog.other_url)
    assert await first.gpu_pool_members() == {"AMPERE_24": {"NVIDIA L4"}}
    assert len(catalog.requests) == 3
    monkeypatch.setenv("RUNPOD_GPU_CATALOG_URL", f"{catalog.url}/custom-catalog")
    assert await first.gpu_pool_members() == {"AMPERE_24": {"NVIDIA L4"}}
    assert catalog.requests[-1] == ("/custom-catalog", "Bearer first-key")
    assert len(catalog.requests) == 4


@pytest.mark.parametrize("operation", ["start", "refresh", "reconcile"])
@pytest.mark.parametrize("selection", [None, "any", "AMPERE_24", ["L4", "AMPERE_24"]])
async def test_unpinned_gpus_do_not_fetch_catalog(operation, selection):
    client = AppsApiClient(api_key="explicit-key")
    client.gpu_pool_members = AsyncMock(side_effect=AssertionError("unneeded catalog"))
    await provision(operation, client, gpu_selection=selection)
    client.gpu_pool_members.assert_not_called()


@pytest.mark.parametrize("operation", ["start", "refresh", "reconcile"])
async def test_cpu_resources_do_not_fetch_catalog(operation):
    client = AppsApiClient(api_key="explicit-key")
    client.gpu_pool_members = AsyncMock(side_effect=AssertionError("unneeded catalog"))
    await provision(operation, client, gpu_selection=None, cpu="cpu3c-1-2")
    client.gpu_pool_members.assert_not_called()


def test_supplied_membership_serializes_without_network():
    assert f"-{MIG}" in gpu.gpu_ids_value(
        ["NVIDIA L4"], pool_members=gpu._parse_catalog(CATALOG)
    )
    assert gpu.gpu_ids_value(["NVIDIA L4"], pool_members={}) == (
        "AMPERE_24,-NVIDIA GeForce RTX 3090,-NVIDIA RTX A5000"
    )
