"""volume references and provision-time resolution."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.volume import (
    Volume,
    VolumeError,
    VolumeResolver,
    volume_list,
)


def _spec(name="r", gpu=None, cpu=None):
    return ResourceSpec(
        kind=ResourceKind.TASK, name=name, gpu=gpu, cpu=cpu
    )


def _api(volumes=None, created=None):
    api = AsyncMock()
    api.list_network_volumes.return_value = volumes or []
    api.create_network_volume.return_value = created or {
        "id": "nv-new",
        "name": "models",
        "size": 50,
        "dataCenterId": "EU-RO-1",
    }
    # stock queries: everything available everywhere
    api.gpu_stock_status.return_value = "High"
    api.cpu_stock_status.return_value = "High"
    return api


class TestVolumeRef:
    def test_path_follows_context(self, monkeypatch):
        from pathlib import Path

        # task pods mount at /workspace
        monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
        assert Volume("models").path == Path("/workspace")
        # endpoint workers mount at /runpod-volume
        monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-1")
        assert Volume("models").path == Path("/runpod-volume")

    def test_empty_name_raises(self):
        with pytest.raises(VolumeError):
            Volume("")

    def test_volume_list_normalizes(self):
        vols = volume_list([Volume("a"), "b"])
        assert [v.name for v in vols] == ["a", "b"]
        assert volume_list(None) == []
        assert [v.name for v in volume_list("single")] == ["single"]


class TestVolumeResolver:
    def test_existing_by_name(self):
        api = _api(
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenterId": "EU-RO-1"}
            ]
        )
        resolver = VolumeResolver(api)
        resolved = asyncio.run(
            resolver.resolve(Volume("models"), [_spec(gpu=None)])
        )
        assert resolved == {"id": "nv-1", "dataCenterId": "EU-RO-1"}
        api.create_network_volume.assert_not_awaited()

    def test_existing_by_id(self):
        api = _api(
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenterId": "EU-RO-1"}
            ]
        )
        resolver = VolumeResolver(api)
        resolved = asyncio.run(
            resolver.resolve(Volume("nv-1"), [_spec(gpu=None)])
        )
        assert resolved["id"] == "nv-1"

    def test_missing_creates_with_placement(self):
        api = _api()
        resolver = VolumeResolver(api)
        resolved = asyncio.run(
            resolver.resolve(Volume("models"), [_spec(gpu=None)])
        )
        assert resolved["id"] == "nv-new"
        api.create_network_volume.assert_awaited_once()

    def test_missing_no_create_raises(self):
        api = _api()
        resolver = VolumeResolver(api)
        with pytest.raises(VolumeError, match="create=False"):
            asyncio.run(
                resolver.resolve(
                    Volume("models", create=False), [_spec(gpu=None)]
                )
            )

    def test_duplicate_names_raise(self):
        api = _api(
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenterId": "EU-RO-1"},
                {"id": "nv-2", "name": "models", "size": 50, "dataCenterId": "US-KS-2"},
            ]
        )
        resolver = VolumeResolver(api)
        with pytest.raises(VolumeError, match="reference by id"):
            asyncio.run(
                resolver.resolve(Volume("models"), [_spec(gpu=None)])
            )

    def test_resolution_cached_per_name(self):
        api = _api(
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenterId": "EU-RO-1"}
            ]
        )
        resolver = VolumeResolver(api)

        async def run():
            await resolver.resolve(Volume("models"), [_spec(gpu=None)])
            await resolver.resolve(Volume("models"), [_spec(gpu=None)])

        asyncio.run(run())
        assert api.list_network_volumes.await_count == 1

    def test_created_event_emitted(self):
        events = []

        class Sink:
            def volume_created(self, name, size, dc):
                events.append((name, size, dc))

        api = _api()
        resolver = VolumeResolver(api, events=Sink())
        asyncio.run(resolver.resolve(Volume("models"), [_spec(gpu=None)]))
        assert len(events) == 1
        name, size, dc = events[0]
        assert (name, size) == ("models", 50)
        assert dc  # placement picks a concrete datacenter


class TestTaskVolume:
    def test_single_volume_only(self):
        from runpod.apps.tasks import TaskExecution

        spec = ResourceSpec(
            kind=ResourceKind.TASK,
            name="t",
            cpu=["cpu3c-1-2"],
            volume=[Volume("a"), Volume("b")],
        )
        execution = TaskExecution(spec, api=_api())
        with pytest.raises(VolumeError, match="exactly one volume"):
            asyncio.run(execution._attach_volume({}))

    def test_pod_pins_to_volume_dc(self):
        from runpod.apps.tasks import TaskExecution

        api = _api(
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenterId": "EU-RO-1"}
            ]
        )
        spec = ResourceSpec(
            kind=ResourceKind.TASK,
            name="t",
            cpu=["cpu3c-1-2"],
            volume=Volume("models"),
        )
        execution = TaskExecution(spec, api=api)
        pod = asyncio.run(execution._attach_volume({}))
        assert pod["networkVolumeId"] == "nv-1"
        assert pod["dataCenterIds"] == ["EU-RO-1"]
