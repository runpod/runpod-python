import pytest

from runpod.apps import App
from runpod.apps.api import _rest_endpoint_input
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import _deployed_endpoint_input
from runpod.apps.dev import _endpoint_input
from runpod.apps.errors import InvalidResourceError
from runpod.apps.spec import ResourceKind, ResourceSpec
from runpod.apps.tasks import _pod_input


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.parametrize("kind", list(ResourceKind))
@pytest.mark.parametrize("image", [None, "example/custom:latest"])
@pytest.mark.parametrize(
    "config, expected",
    [
        ({}, "12.8"),
        ({"gpu": "4090"}, "12.8"),
        ({"gpu": "4090", "min_cuda_version": None}, "12.8"),
        ({"gpu": "4090", "min_cuda_version": "11.8"}, "11.8"),
        ({"gpu": "4090", "min_cuda_version": "12.6"}, "12.6"),
        ({"gpu": "4090", "min_cuda_version": "12.9"}, "12.9"),
        ({"gpu": "4090", "min_cuda_version": "13.0"}, "13.0"),
        ({"cpu": "cpu3c-1-2"}, None),
        ({"cpu": "cpu3c-2-4"}, None),
    ],
)
def test_cuda_floor_reaches_every_payload(kind, image, config, expected):
    app = App("cuda-defaults")
    declare = getattr(app, kind.value)
    handle = declare(name="resource", image=image, **config)(lambda: None)
    spec = handle.spec

    assert spec.effective_min_cuda_version == expected
    manifest = spec.to_manifest()
    if expected is None:
        assert "minCudaVersion" not in manifest
    else:
        assert manifest["minCudaVersion"] == expected

    if kind is ResourceKind.TASK:
        pod = _pod_input(spec, "synthetic-token", "resource")
        assert "allowedCudaVersions" not in pod
        if expected is None:
            assert "minCudaVersion" not in pod
        else:
            assert pod["minCudaVersion"] == expected
        return

    payloads = [
        _endpoint_input(app, spec),
        _deployed_endpoint_input(app, spec, "env-1", "build-1", "3.12"),
    ]
    for payload in payloads:
        if expected is None:
            assert "minCudaVersion" not in payload
        else:
            assert payload["minCudaVersion"] == expected

    rest = _rest_endpoint_input(payloads[0])
    if config.get("cpu") == "cpu3c-1-2":
        assert rest is None
    elif expected is None:
        assert rest is not None
        assert "gpu" not in rest
    else:
        assert rest is not None
        assert rest["gpu"]["minCudaVersion"] == expected
        assert "allowedCudaVersions" not in rest["gpu"]
    assert _rest_endpoint_input(payloads[1]) is None


@pytest.mark.parametrize("kind", list(ResourceKind))
@pytest.mark.parametrize("image", [None, "example/custom:latest"])
@pytest.mark.parametrize("minimum", ["11.8", "12.8", "13.0"])
def test_cpu_resource_rejects_explicit_cuda_floor(kind, image, minimum):
    app = App("cuda-defaults")
    declare = getattr(app, kind.value)
    with pytest.raises(InvalidResourceError, match="min_cuda_version has no effect"):
        declare(
            name="resource", image=image, cpu="cpu3c-1-2", min_cuda_version=minimum
        )(lambda: None)


def test_cuda_default_follows_mutable_compute_configuration():
    spec = ResourceSpec(kind=ResourceKind.QUEUE, name="resource")
    assert spec.to_manifest()["minCudaVersion"] == "12.8"
    assert spec.min_cuda_version is None

    spec.cpu = "cpu3c-1-2"
    assert "minCudaVersion" not in spec.to_manifest()

    spec.cpu = None
    spec.min_cuda_version = "12.6"
    assert spec.to_manifest()["minCudaVersion"] == "12.6"

    spec.min_cuda_version = None
    assert spec.to_manifest()["minCudaVersion"] == "12.8"
