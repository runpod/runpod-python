"""volume references and provision-time resolution."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from runpod.apps.datacenter import DataCenter
from runpod.apps.placement import PlacementError
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.volume import (
    GlobalVolume,
    NetworkVolume,
    Volume,
    VolumeError,
    VolumeResolver,
    _configure_mounts,
    attach_endpoint_volumes,
    normalize_mounts,
)


def _spec(name="r", gpu=None, cpu=None):
    return ResourceSpec(kind=ResourceKind.TASK, name=name, gpu=gpu, cpu=cpu)


def _api(volumes=None, created=None, global_volumes=None):
    api = AsyncMock()
    api.list_network_volumes.return_value = volumes or []
    api.network_volume_datacenters.return_value = {dc.value for dc in DataCenter.all()}
    api.list_global_volumes.return_value = global_volumes or []
    api.create_global_volume.return_value = {"id": "gv-new", "name": "models"}
    api.create_network_volume.return_value = created or {
        "id": "nv-new",
        "name": "models",
        "size": 50,
        "dataCenter": "EU-RO-1",
    }
    # stock queries: everything available everywhere
    api.gpu_stock_status.return_value = "High"
    api.cpu_stock_status.return_value = "High"
    return api


@pytest.fixture(autouse=True)
def clean_mounts():
    _configure_mounts([])
    yield
    _configure_mounts([])


class TestVolumeRef:
    def test_base_is_abstract(self):
        with pytest.raises(TypeError):
            Volume("models")

    def test_unmounted_volume_has_no_conventional_path(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-1")
        with pytest.raises(VolumeError):
            NetworkVolume("models").path

    def test_kind_and_resolved_id_bind_to_actual_files(self, tmp_path):
        network_path = tmp_path / "network"
        global_path = tmp_path / "global"
        network_path.mkdir()
        global_path.mkdir()
        (network_path / "model").write_text("network weights")
        (global_path / "model").write_text("global weights")
        _configure_mounts(
            [
                {
                    "kind": "network",
                    "reference": "models",
                    "id": "nv-1",
                    "path": str(network_path),
                },
                {
                    "kind": "global",
                    "reference": "models",
                    "id": "models",
                    "path": str(global_path),
                },
            ]
        )
        assert (NetworkVolume("models").path / "model").read_text() == "network weights"
        assert (NetworkVolume("nv-1").path / "model").read_text() == "network weights"
        assert (GlobalVolume("models").path / "model").read_text() == "global weights"

    def test_multiple_bindings_require_explicit_path(self):
        _configure_mounts(
            [
                {
                    "kind": "network",
                    "reference": "models",
                    "id": "nv-1",
                    "path": "/one",
                },
                {
                    "kind": "network",
                    "reference": "models",
                    "id": "nv-1",
                    "path": "/two",
                },
            ]
        )
        with pytest.raises(VolumeError):
            NetworkVolume("models").path
        with pytest.raises(VolumeError):
            NetworkVolume("nv-1").path

    def test_invalid_configuration_does_not_partially_publish(self, tmp_path):
        (tmp_path / "model").write_text("retained")
        binding = {
            "kind": "network",
            "reference": "models",
            "id": "nv-1",
            "path": str(tmp_path),
        }
        _configure_mounts([binding])
        with pytest.raises(VolumeError):
            _configure_mounts([dict(binding, path="/other"), {"kind": "global"}])
        assert (NetworkVolume("models").path / "model").read_text() == "retained"
        _configure_mounts([])
        with pytest.raises(VolumeError):
            NetworkVolume("models").path

    @pytest.mark.parametrize("volume_type", [NetworkVolume, GlobalVolume])
    def test_empty_reference_raises(self, volume_type):
        with pytest.raises(VolumeError):
            volume_type("")

    @pytest.mark.parametrize(
        "mounts",
        [
            {"/models": "models"},
            {"models": NetworkVolume("models")},
            {"/models/../data": NetworkVolume("models")},
            {"/models": NetworkVolume("models"), "/models/": GlobalVolume("global")},
        ],
    )
    def test_invalid_mount_mapping_raises(self, mounts):
        with pytest.raises(VolumeError):
            normalize_mounts(mounts)


class TestVolumeResolver:
    def test_existing_by_name(self):
        api = _api(
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenter": "EU-RO-1"}
            ]
        )
        api.network_volume_datacenters.side_effect = RuntimeError("catalog unavailable")
        resolver = VolumeResolver(api)
        resolved = asyncio.run(
            resolver.resolve(NetworkVolume("models", datacenter="US-IL-1"), [_spec()])
        )
        assert resolved == {"id": "nv-1", "dataCenterId": "EU-RO-1"}
        api.create_network_volume.assert_not_awaited()

    def test_existing_by_id(self):
        api = _api(
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenter": "EU-RO-1"}
            ]
        )
        resolver = VolumeResolver(api)
        resolved = asyncio.run(
            resolver.resolve(NetworkVolume("nv-1"), [_spec(gpu=None)])
        )
        assert resolved["id"] == "nv-1"

    def test_storage_capability_beats_higher_hardware_stock(self):
        api = _api()
        api.network_volume_datacenters.return_value = {"EU-RO-1"}
        api.cpu_stock_status.side_effect = lambda instance, dc, *, pods=False: (
            "HIGH" if dc == "US-IL-1" else "LOW"
        )
        resolver = VolumeResolver(api)
        resolved = asyncio.run(
            resolver.resolve(NetworkVolume("models"), [_spec(cpu="cpu3c-2-4")])
        )
        assert resolved == {"id": "nv-new", "dataCenterId": "EU-RO-1"}

    @pytest.mark.parametrize("volume_type", [NetworkVolume, GlobalVolume])
    def test_missing_no_create_raises(self, volume_type):
        api = _api()
        resolver = VolumeResolver(api)
        with pytest.raises(VolumeError, match="create=False"):
            asyncio.run(
                resolver.resolve(volume_type("models", create=False), [_spec(gpu=None)])
            )

    @pytest.mark.parametrize("volume_type", [NetworkVolume, GlobalVolume])
    def test_duplicate_names_raise(self, volume_type):
        api = _api(
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenter": "EU-RO-1"},
                {"id": "nv-2", "name": "models", "size": 50, "dataCenter": "US-KS-2"},
            ]
        )
        api.list_global_volumes.return_value = api.list_network_volumes.return_value
        resolver = VolumeResolver(api)
        with pytest.raises(VolumeError, match="reference by id"):
            asyncio.run(resolver.resolve(volume_type("models"), [_spec(gpu=None)]))

    @pytest.mark.parametrize("reference", ["models", "gv-1"])
    def test_existing_global_resolves_without_creation_or_placement(self, reference):
        api = _api(global_volumes=[{"id": "gv-1", "name": "models"}])
        resolved = asyncio.run(
            VolumeResolver(api).resolve(GlobalVolume(reference, create=False))
        )
        assert resolved == {"id": "gv-1"}
        api.create_global_volume.assert_not_awaited()
        api.list_network_volumes.assert_not_awaited()

    def test_global_creation_is_cached_and_mounts_resolve_by_name_and_id(
        self, tmp_path
    ):
        (tmp_path / "model").write_text("global weights")
        api = _api()
        resolver = VolumeResolver(api)

        async def run():
            bindings = await resolver.resolve_mounts(
                {str(tmp_path): GlobalVolume("models")}
            )
            assert await resolver.resolve(GlobalVolume("models")) == {"id": "gv-new"}
            return bindings

        _configure_mounts(asyncio.run(run()))
        assert (GlobalVolume("models").path / "model").read_text() == "global weights"
        assert (GlobalVolume("gv-new").path / "model").read_text() == "global weights"
        api.create_global_volume.assert_awaited_once()
        api.list_network_volumes.assert_not_awaited()

    def test_resolution_cached_per_name(self):
        api = _api(
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenter": "EU-RO-1"}
            ]
        )
        resolver = VolumeResolver(api)

        async def run():
            await resolver.resolve(NetworkVolume("models"), [_spec(gpu=None)])
            await resolver.resolve(NetworkVolume("models"), [_spec(gpu=None)])

        asyncio.run(run())
        assert api.list_network_volumes.await_count == 1

    def test_same_reference_in_different_backends_does_not_share_cache(self):
        resolver = VolumeResolver(
            _api(volumes=[{"id": "nv-1", "name": "models", "dataCenter": "EU-RO-1"}])
        )
        assert asyncio.run(resolver.resolve(GlobalVolume("models"))) == {"id": "gv-new"}
        assert asyncio.run(resolver.resolve(NetworkVolume("models"))) == {
            "id": "nv-1",
            "dataCenterId": "EU-RO-1",
        }

    def test_creation_without_app_requires_explicit_placement(self):
        resolver = VolumeResolver(_api())
        with pytest.raises(VolumeError):
            asyncio.run(resolver.resolve(NetworkVolume("models")))
        resolved = asyncio.run(
            resolver.resolve(NetworkVolume("models", datacenter="EU-RO-1"))
        )
        assert resolved == {"id": "nv-new", "dataCenterId": "EU-RO-1"}

    async def test_unsupported_explicit_pin_never_relocates(self):
        api = _api()
        api.network_volume_datacenters.return_value = {"EU-RO-1"}
        with pytest.raises(VolumeError):
            await VolumeResolver(api).resolve(
                NetworkVolume("models", datacenter="US-IL-1"), [_spec()]
            )

    async def test_disjoint_storage_and_hardware_cannot_create(self):
        api = _api()
        api.network_volume_datacenters.return_value = {"EU-RO-1"}
        api.cpu_stock_status.side_effect = lambda instance, dc, *, pods=False: (
            "HIGH" if dc == "US-IL-1" else "NONE"
        )
        with pytest.raises(PlacementError):
            await VolumeResolver(api).resolve(
                NetworkVolume("models"), [_spec(cpu="cpu3c-2-4")]
            )


class TestTaskVolume:
    def test_one_volume_per_backend(self):
        with pytest.raises(VolumeError):
            ResourceSpec(
                kind=ResourceKind.TASK,
                name="t",
                cpu=["cpu3c-1-2"],
                mounts={"/a": NetworkVolume("a"), "/b": NetworkVolume("b")},
            )

    def test_pod_pins_to_volume_dc(self):
        from runpod.apps.tasks import TaskExecution

        api = _api(
            global_volumes=[{"id": "gv-1", "name": "shared-global"}],
            volumes=[
                {"id": "nv-1", "name": "models", "size": 50, "dataCenter": "EU-RO-1"}
            ],
        )
        spec = ResourceSpec(
            kind=ResourceKind.TASK,
            name="t",
            cpu=["cpu3c-1-2"],
            mounts={"/models": NetworkVolume("models"), "/data": GlobalVolume("gv-1")},
        )
        execution = TaskExecution(spec, api=api)
        pod = asyncio.run(execution._attach_mounts({}))
        assert pod["volumeMounts"] == [
            {
                "volumeId": "nv-1",
                "volumeType": "NETWORK_VOLUME",
                "mountPath": "/models",
            },
            {
                "volumeId": "gv-1",
                "volumeType": "OBJECT_STORE_VOLUME",
                "mountPath": "/data",
            },
        ]
        assert pod["dataCenterIds"] == ["EU-RO-1"]
        _configure_mounts(json.loads(pod["env"][0]["value"]))
        assert str(NetworkVolume("models").path) == "/models"
        assert str(GlobalVolume("gv-1").path) == "/data"


class TestEndpointMounts:
    @pytest.mark.parametrize(
        "mounts,cpu",
        [
            ({"/models": NetworkVolume("models")}, None),
            ({"/runpod-volume": GlobalVolume("global")}, "cpu3c-1-2"),
            (
                {
                    "/runpod-volume": NetworkVolume("models"),
                    "/data": GlobalVolume("global"),
                },
                None,
            ),
        ],
    )
    def test_unsupported_attachment_rejected_before_provisioning(self, mounts, cpu):
        with pytest.raises(VolumeError):
            ResourceSpec(kind=ResourceKind.QUEUE, name="queue", cpu=cpu, mounts=mounts)

    def test_global_attachment_does_not_pin_datacenter(self):
        from runpod import App

        app = App("global-model")

        @app.queue(gpu="4090", mounts={"/runpod-volume": GlobalVolume("models")})
        def generate():
            return None

        payload = {"locations": "EU-RO-1,US-KS-2"}
        asyncio.run(
            attach_endpoint_volumes(payload, generate.spec, VolumeResolver(_api()), app)
        )
        assert payload["locations"] == "EU-RO-1,US-KS-2"
        assert payload["networkVolumeIds"] == []
        assert payload["volumes"] == [
            {"volumeId": "gv-new", "volumeType": "OBJECT_STORE_VOLUME"}
        ]

    def test_removing_storage_clears_backend_and_worker_bindings(self):
        from runpod import App

        app = App("no-storage")

        @app.queue()
        def generate():
            return None

        old_binding = {
            "kind": "network",
            "reference": "models",
            "id": "nv-1",
            "path": "/runpod-volume",
        }
        _configure_mounts([old_binding])
        payload = {
            "networkVolumeIds": [{"networkVolumeId": "nv-1"}],
            "volumes": [{"volumeId": "gv-1", "volumeType": "OBJECT_STORE_VOLUME"}],
            "template": {
                "env": [{"key": "RUNPOD_MOUNTS", "value": json.dumps([old_binding])}]
            },
        }
        asyncio.run(
            attach_endpoint_volumes(payload, generate.spec, VolumeResolver(), app)
        )
        assert payload["networkVolumeIds"] == []
        assert payload["volumes"] == []
        env = {entry["key"]: entry["value"] for entry in payload["template"]["env"]}
        _configure_mounts(json.loads(env["RUNPOD_MOUNTS"]))
        with pytest.raises(VolumeError):
            NetworkVolume("models").path
