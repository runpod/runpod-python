from unittest.mock import AsyncMock

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import _deployed_endpoint_input
from runpod.apps.placement import StockMap, candidates
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.tasks import TaskExecution, _pod_input
from runpod.apps.volume import NetworkVolume, VolumeResolver
from runpod.error import QueryError

DEFAULT_LOCATIONS = [
    "US-CA-2",
    "US-IL-1",
    "US-KS-2",
    "US-MO-1",
    "US-MO-2",
    "US-NC-2",
    "US-NE-1",
    "US-WA-1",
    "EU-CZ-1",
    "EU-RO-1",
    "EUR-NO-1",
]


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.parametrize("kind", list(ResourceKind))
@pytest.mark.parametrize("datacenter", [None, "AP-JP-1", "EU-SE-1", "US-TX-3"])
def test_gpu_payload_uses_default_pool_or_explicit_pin(kind, datacenter):
    spec = ResourceSpec(kind=kind, name="worker", gpu="4090", datacenter=datacenter)
    expected = [datacenter] if datacenter else DEFAULT_LOCATIONS
    if kind is ResourceKind.TASK:
        payload = _pod_input(spec, "synthetic-token", "worker")
        assert payload["dataCenterIds"] == expected
    else:
        payload = _deployed_endpoint_input(App("pool"), spec, "env", "build", "3.12")
        assert payload["locations"].split(",") == expected


async def test_unpinned_gpu_task_retries_only_default_locations():
    api = AsyncMock()
    api.deploy_task_pod.side_effect = QueryError(
        "This machine does not have the resources to deploy your pod"
    )
    spec = ResourceSpec(kind=ResourceKind.TASK, name="worker", gpu="4090")
    with pytest.raises(QueryError):
        await TaskExecution(spec, api=api).start()
    calls = api.deploy_task_pod.await_args_list
    assert len(calls) == 1 + len(DEFAULT_LOCATIONS)
    assert calls[0].args[0]["dataCenterIds"] == DEFAULT_LOCATIONS
    assert [call.args[0]["dataCenterIds"] for call in calls[1:]] == [
        [dc] for dc in DEFAULT_LOCATIONS
    ]


@pytest.mark.parametrize("kind", ["gpu", "gpu-pod", "cpu", "cpu-pod"])
async def test_stock_fetch_defaults_and_explicit_locations_are_cached_separately(kind):
    api = AsyncMock()
    stock = StockMap(api)
    key = (
        kind,
        "NVIDIA GeForce RTX 4090" if kind.startswith("gpu") else "cpu3c-1-2",
        1,
    )
    query = api.gpu_stock_status if kind.startswith("gpu") else api.cpu_stock_status
    await stock.fetch([key, key])
    assert {call.args[1] for call in query.await_args_list} == set(DEFAULT_LOCATIONS)
    assert query.await_count == len(DEFAULT_LOCATIONS)
    await stock.fetch([key])
    assert query.await_count == len(DEFAULT_LOCATIONS)
    await stock.fetch([key], datacenters=["AP-JP-1", "EU-RO-1"])
    assert query.await_count == len(DEFAULT_LOCATIONS) + 1
    assert query.await_args.args[1] == "AP-JP-1"
    await stock.fetch([key], datacenters=["AP-JP-1"])
    assert query.await_count == len(DEFAULT_LOCATIONS) + 1


@pytest.mark.parametrize("gpu", [None, "4090"])
async def test_candidate_pool_requires_opt_in_for_additional_locations(gpu):
    api = AsyncMock()
    api.gpu_stock_status.return_value = "High"
    stock = StockMap(api)
    await stock.fetch(
        [("gpu-pod", "NVIDIA GeForce RTX 4090", 1)],
        datacenters=["AP-JP-1", *DEFAULT_LOCATIONS],
    )
    spec = ResourceSpec(kind=ResourceKind.TASK, name="worker", gpu=gpu)
    assert candidates(spec, stock) == set(DEFAULT_LOCATIONS)
    spec.datacenter = ["AP-JP-1"]
    assert candidates(spec, stock) == {"AP-JP-1"}


@pytest.mark.parametrize("source", ["resource", "volume", "existing"])
async def test_volume_placement_can_opt_into_additional_locations(source):
    api = AsyncMock()
    api.list_network_volumes.return_value = (
        [{"id": "existing", "name": "models", "dataCenter": "AP-JP-1"}]
        if source == "existing"
        else []
    )
    api.network_volume_datacenters.return_value = {"AP-JP-1"}
    api.gpu_stock_status.return_value = "High"
    api.create_network_volume.return_value = {"id": "new"}
    volume = NetworkVolume(
        "models", datacenter="AP-JP-1" if source == "volume" else None
    )
    spec = ResourceSpec(
        kind=ResourceKind.TASK,
        name="worker",
        gpu="4090",
        datacenter="AP-JP-1" if source == "resource" else None,
        mounts={"/models": volume},
    )
    resolver = VolumeResolver(api)
    await resolver.preflight([spec])
    resolved = await resolver.resolve(volume, [spec])
    assert resolved["dataCenterId"] == "AP-JP-1"
    assert resolved["id"] == ("existing" if source == "existing" else "new")
    api.gpu_stock_status.assert_awaited_once_with(
        "NVIDIA GeForce RTX 4090", "AP-JP-1", gpu_count=1, pods=True
    )
    if source == "existing":
        api.create_network_volume.assert_not_awaited()
    else:
        api.create_network_volume.assert_awaited_once_with(
            name="models", size=50, data_center_id="AP-JP-1"
        )
