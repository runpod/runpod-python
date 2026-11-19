"""storage placement validation before provisioning."""

from unittest.mock import AsyncMock, patch

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.datacenter import DataCenter
from runpod.apps.deploy import deploy_app, reconcile_endpoints
from runpod.apps.dev import DevSession
from runpod.apps.placement import PlacementError
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.volume import (
    GlobalVolume,
    NetworkVolume,
    VolumeError,
    VolumeResolver,
    attach_pod_mounts,
)

VOLUME_DATACENTERS = (
    "AP-IN-1",
    "AP-JP-1",
    "CA-MTL-1",
    "CA-MTL-3",
    "CA-MTL-4",
    "EU-CZ-1",
    "EU-FR-1",
    "EU-NL-1",
    "EU-RO-1",
    "EU-SE-1",
    "EUR-IS-1",
    "EUR-IS-3",
    "EUR-IS-4",
    "EUR-NO-1",
    "EUR-NO-2",
    "US-CA-2",
    "US-CO-1",
    "US-GA-2",
    "US-MO-2",
    "US-NC-1",
    "US-NE-1",
    "US-TX-3",
)


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.fixture
def api():
    client = AsyncMock()
    client.list_network_volumes.return_value = []
    client.list_global_volumes.return_value = []
    client.network_volume_datacenters.return_value = {"EU-RO-1"}
    client.gpu_stock_status.return_value = "High"
    client.cpu_stock_status.return_value = "High"
    client.create_network_volume.return_value = {"id": "nv-new"}
    client.create_global_volume.return_value = {"id": "gv-new"}
    client.list_my_endpoints.return_value = []
    client.save_endpoint.return_value = {"id": "ep-new"}
    return client


def _spec(name, volume, datacenter=None):
    return ResourceSpec(
        kind=ResourceKind.TASK,
        name=name,
        datacenter=datacenter,
        mounts={"/models": volume},
    )


def _queue(app, name, volume):
    @app.queue(name=name, mounts={"/runpod-volume": volume})
    def generate():
        return None

    return generate


@pytest.mark.parametrize("dc", VOLUME_DATACENTERS)
def test_volume_catalog_datacenters_have_enum_members(dc):
    assert DataCenter[dc.replace("-", "_")].value == dc
    assert DataCenter.from_string(dc.lower().replace("-", "_")).value == dc


async def test_preflight_plans_shared_storage_without_creating(api):
    resolver = VolumeResolver(api)
    volume = NetworkVolume("shared", datacenter="EU-RO-1")
    specs = [_spec("a", volume), _spec("b", NetworkVolume("shared"))]

    await resolver.preflight(specs)

    api.create_network_volume.assert_not_awaited()
    api.list_network_volumes.assert_awaited_once()
    api.network_volume_datacenters.assert_awaited_once()
    for spec in specs:
        assert await resolver.resolve(volume, [spec]) == {
            "id": "nv-new",
            "dataCenterId": "EU-RO-1",
        }
    api.list_network_volumes.assert_awaited_once()
    api.create_network_volume.assert_awaited_once_with(
        name="shared", size=50, data_center_id="EU-RO-1"
    )


async def test_global_creation_is_deferred_until_resolution(api):
    resolver = VolumeResolver(api)
    volume = GlobalVolume("global")
    await resolver.preflight([_spec("a", volume)])
    api.create_global_volume.assert_not_awaited()
    assert await resolver.resolve(volume) == {"id": "gv-new"}
    api.create_global_volume.assert_awaited_once_with(name="global")
    api.list_global_volumes.assert_awaited_once()
    api.network_volume_datacenters.assert_not_awaited()


@pytest.mark.parametrize("pin_volume", [True, False])
async def test_all_placements_are_checked_before_any_creation(api, pin_volume):
    invalid = NetworkVolume("invalid", datacenter="US-KS-2" if pin_volume else None)
    specs = [
        _spec("a", GlobalVolume("global")),
        _spec("b", NetworkVolume("valid", datacenter="EU-RO-1")),
        _spec("c", invalid, datacenter=None if pin_volume else "US-KS-2"),
    ]
    with pytest.raises((VolumeError, PlacementError), match="invalid"):
        await VolumeResolver(api).preflight(specs)
    api.create_global_volume.assert_not_awaited()
    api.create_network_volume.assert_not_awaited()
    api.network_volume_datacenters.assert_awaited_once()


async def test_shared_volume_pins_are_validated_together(api):
    api.network_volume_datacenters.return_value = {"EU-RO-1", "US-MO-2"}
    specs = [
        _spec("a", NetworkVolume("shared", datacenter="EU-RO-1")),
        _spec("b", NetworkVolume("shared", datacenter="US-MO-2")),
    ]
    with pytest.raises(VolumeError, match="conflicting datacenter pins"):
        await VolumeResolver(api).preflight(specs)
    api.create_network_volume.assert_not_awaited()


async def test_catalog_failure_prevents_global_and_network_creation(api):
    api.network_volume_datacenters.side_effect = RuntimeError("unavailable")
    with pytest.raises(VolumeError, match="catalog lookup failed"):
        await VolumeResolver(api).preflight(
            [
                _spec("a", GlobalVolume("global")),
                _spec("b", NetworkVolume("network", datacenter="EU-RO-1")),
            ]
        )
    api.create_global_volume.assert_not_awaited()
    api.create_network_volume.assert_not_awaited()


@pytest.mark.parametrize("reference", ["existing", "nv-existing"])
async def test_existing_storage_does_not_require_creation_support(api, reference):
    api.list_network_volumes.return_value = [
        {"id": "nv-existing", "name": "existing", "dataCenter": "US-KS-2"}
    ]
    resolver = VolumeResolver(api)
    volume = NetworkVolume(reference, create=False)
    specs = [_spec("a", volume, datacenter="US-KS-2")]
    await resolver.preflight(specs)
    assert await resolver.resolve(volume, specs) == {
        "id": "nv-existing",
        "dataCenterId": "US-KS-2",
    }
    api.network_volume_datacenters.assert_not_awaited()
    api.create_network_volume.assert_not_awaited()


async def test_repeated_preflight_replans_uncreated_volumes(api):
    resolver = VolumeResolver(api)
    await resolver.preflight(
        [_spec("a", NetworkVolume("models", datacenter="EU-RO-1"))]
    )
    with pytest.raises(VolumeError, match="US-KS-2"):
        await resolver.preflight(
            [_spec("a", NetworkVolume("models", datacenter="US-KS-2"))]
        )
    api.create_network_volume.assert_not_awaited()


async def test_reconcile_validates_all_volumes_before_saving_endpoints(api):
    app = App("deploy")
    _queue(app, "a", NetworkVolume("valid", datacenter="EU-RO-1"))
    _queue(app, "b", NetworkVolume("invalid", datacenter="US-KS-2"))
    with pytest.raises(VolumeError, match="invalid"):
        await reconcile_endpoints(
            api, app, {"id": "env", "name": "prod"}, "build", "3.12"
        )
    api.create_network_volume.assert_not_awaited()
    api.save_endpoint.assert_not_awaited()
    api.delete_endpoint.assert_not_awaited()


async def test_deploy_checks_endpoint_volumes_before_creating_task_storage(
    api, tmp_path
):
    app = App("mixed")

    @app.task(mounts={"/models": NetworkVolume("valid", datacenter="EU-RO-1")})
    def task():
        return None

    _queue(app, "queue", NetworkVolume("invalid", datacenter="US-KS-2"))
    with patch("runpod.apps.deploy.build_environment") as build:
        with pytest.raises(VolumeError, match="invalid"):
            await deploy_app(app, tmp_path, api=api)
    build.assert_not_called()
    api.create_network_volume.assert_not_awaited()
    api.create_app.assert_not_awaited()
    api.create_environment.assert_not_awaited()


async def test_dev_start_checks_every_app_before_creating(api):
    first = App("first")
    second = App("second")
    _queue(first, "queue", NetworkVolume("valid", datacenter="EU-RO-1"))
    _queue(second, "queue", NetworkVolume("invalid", datacenter="US-KS-2"))
    with pytest.raises(VolumeError, match="invalid"):
        await DevSession([first, second], api=api).start()
    api.create_network_volume.assert_not_awaited()
    api.save_endpoint.assert_not_awaited()


async def test_dev_refresh_checks_volumes_before_creating_or_deleting(api):
    original = App("original")

    @original.queue()
    def queue():
        return None

    session = DevSession([original], api=api)
    await session.start()
    api.save_endpoint.reset_mock()
    replacement = App("replacement")
    _queue(replacement, "a", NetworkVolume("valid", datacenter="EU-RO-1"))
    _queue(replacement, "b", NetworkVolume("invalid", datacenter="US-KS-2"))
    with pytest.raises(VolumeError, match="invalid"):
        await session.refresh([replacement])
    api.create_network_volume.assert_not_awaited()
    api.save_endpoint.assert_not_awaited()
    api.delete_endpoint.assert_not_awaited()
    assert session.apps == [original]
    assert session.generation == 1


async def test_task_mounts_validate_network_before_creating_global(api):
    spec = ResourceSpec(
        kind=ResourceKind.TASK,
        name="task",
        mounts={
            "/global": GlobalVolume("global"),
            "/network": NetworkVolume("invalid", datacenter="US-KS-2"),
        },
    )
    with pytest.raises(VolumeError, match="invalid"):
        await attach_pod_mounts({}, spec, VolumeResolver(api), [spec])
    api.create_global_volume.assert_not_awaited()
    api.create_network_volume.assert_not_awaited()


async def test_creation_support_comes_from_catalog_not_enum(api):
    api.network_volume_datacenters.return_value = {"US-MO-2"}
    volume = NetworkVolume("models", datacenter=DataCenter.US_MO_2)
    resolver = VolumeResolver(api)
    await resolver.preflight([_spec("task", volume)])
    await resolver.resolve(volume)
    api.create_network_volume.assert_awaited_once_with(
        name="models", size=50, data_center_id="US-MO-2"
    )


async def test_volumeless_resources_need_no_storage_catalog(api):
    spec = ResourceSpec(
        kind=ResourceKind.QUEUE, name="queue", datacenter=DataCenter.US_KS_2
    )
    await VolumeResolver(api).preflight([spec])
    api.network_volume_datacenters.assert_not_awaited()
    api.list_network_volumes.assert_not_awaited()
    api.list_global_volumes.assert_not_awaited()


async def test_shared_volume_checks_same_named_resources_across_apps(api):
    api.network_volume_datacenters.return_value = {"EU-RO-1", "US-MO-2"}
    volume = NetworkVolume("shared")
    first = App("first")
    second = App("second")

    @first.queue(name="queue", datacenter="EU-RO-1", mounts={"/runpod-volume": volume})
    def first_queue():
        return None

    @second.queue(name="queue", datacenter="US-MO-2", mounts={"/runpod-volume": volume})
    def second_queue():
        return None

    with pytest.raises(PlacementError, match="no datacenter"):
        await DevSession([first, second], api=api).start()
    api.create_network_volume.assert_not_awaited()
    api.save_endpoint.assert_not_awaited()


async def test_preflight_validates_cached_placement_against_current_specs(api):
    resolver = VolumeResolver(api)
    volume = NetworkVolume("shared", datacenter="EU-RO-1")
    await resolver.preflight([_spec("a", volume)])
    await resolver.resolve(volume)
    api.create_network_volume.reset_mock()
    with pytest.raises(PlacementError, match="cannot schedule"):
        await resolver.preflight(
            [
                _spec("b", NetworkVolume("new", datacenter="EU-RO-1")),
                _spec("a", volume, datacenter="US-MO-2"),
            ]
        )
    api.create_network_volume.assert_not_awaited()
