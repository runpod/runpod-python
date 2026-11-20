import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.datacenter import CPU5_DATACENTERS, DataCenter
from runpod.apps.errors import InvalidResourceError
from runpod.apps.spec import ResourceKind, ResourceSpec


@pytest.mark.parametrize("kind", list(ResourceKind))
@pytest.mark.parametrize("cpu", ["cpu5c-2-4", "cpu5g-2-8", ["cpu5c-2-4", "cpu5g-2-8"]])
@pytest.mark.parametrize("datacenter", ["US-CA-2", ["US-CA-2", "EU-CZ-1"]])
def test_cpu5_rejects_pins_without_a_supported_datacenter(kind, cpu, datacenter):
    with pytest.raises(InvalidResourceError, match="resource 'worker'.*cpu5.*EU-RO-1"):
        ResourceSpec(kind=kind, name="worker", cpu=cpu, datacenter=datacenter)


@pytest.mark.parametrize("kind", list(ResourceKind))
@pytest.mark.parametrize(
    "datacenter",
    [None, [], *CPU5_DATACENTERS, " eu_ro_1 ", ["US-CA-2", "eu_ro_1"]],
)
def test_cpu5_accepts_unpinned_or_schedulable_configuration(kind, datacenter):
    spec = ResourceSpec(
        kind=kind, name="worker", cpu="cpu5c-2-4", datacenter=datacenter
    )
    assert spec.to_manifest()["instanceIds"] == ["cpu5c-2-4"]
    if datacenter:
        assert "EU-RO-1" in spec.datacenter


@pytest.mark.parametrize("kind", list(ResourceKind))
@pytest.mark.parametrize("cpu", ["cpu5c-2-4", "cpu5g-2-8", ["cpu5c-2-4", "cpu5g-2-8"]])
@pytest.mark.parametrize(
    "datacenter,locations",
    [
        ("XX-TEST-1", "XX-TEST-1"),
        (" US-TX-3 ", "US-TX-3"),
        (["US-CA-2", "US-TX-3"], "US-CA-2,US-TX-3"),
        (["US-TX-3", "EU-CZ-1"], "US-TX-3,EU-CZ-1"),
    ],
)
def test_cpu5_passes_unlisted_datacenter_pins_to_manifest(
    kind, cpu, datacenter, locations
):
    spec = ResourceSpec(kind=kind, name="worker", cpu=cpu, datacenter=datacenter)
    assert spec.to_manifest()["locations"] == locations


@pytest.mark.parametrize("kind", list(ResourceKind))
@pytest.mark.parametrize(
    "cpu", [None, "cpu3c-1-2", ["cpu5c-2-4", "cpu3c-1-2"], ["cpu3c-1-2", "cpu5g-2-8"]]
)
def test_other_hardware_options_allow_non_cpu5_datacenter(kind, cpu):
    spec = ResourceSpec(
        kind=kind, name="worker", cpu=cpu, datacenter=DataCenter.US_CA_2
    )
    assert spec.to_manifest()["locations"] == "US-CA-2"


@pytest.mark.parametrize("decorator", ["queue", "api", "task"])
def test_decorators_reject_unschedulable_cpu5_pins(decorator):
    _clear_registry()
    try:
        app = App("cpu-pins")
        with pytest.raises(InvalidResourceError, match="cpu5.*EU-RO-1"):
            getattr(app, decorator)(cpu="cpu5c-2-4", datacenter="US-CA-2")(lambda: None)
        assert app.resources == {}
    finally:
        _clear_registry()


@pytest.mark.parametrize(
    "field,value", [("cpu", "cpu5g-2-8"), ("datacenter", "US-CA-2")]
)
def test_mutable_configuration_revalidates_cpu_datacenter_pair(field, value):
    spec = ResourceSpec(
        kind=ResourceKind.QUEUE,
        name="worker",
        cpu="cpu3c-1-2" if field == "cpu" else "cpu5c-2-4",
        datacenter="US-CA-2" if field == "cpu" else "EU-RO-1",
    )
    setattr(spec, field, value)
    with pytest.raises(InvalidResourceError, match="cpu5.*EU-RO-1"):
        spec.to_manifest()


def test_cpu5_validation_uses_supported_datacenter_constant(monkeypatch):
    monkeypatch.setattr("runpod.apps.spec.CPU5_DATACENTERS", [DataCenter.US_CA_2])
    spec = ResourceSpec(
        kind=ResourceKind.QUEUE,
        name="worker",
        cpu="cpu5c-2-4",
        datacenter="US-CA-2",
    )
    assert spec.to_manifest()["locations"] == "US-CA-2"
    with pytest.raises(InvalidResourceError, match="cpu5.*US-CA-2"):
        ResourceSpec(
            kind=ResourceKind.QUEUE,
            name="worker",
            cpu="cpu5c-2-4",
            datacenter="EU-RO-1",
        )
