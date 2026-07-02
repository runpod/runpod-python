"""tests for runtime image selection."""

import pytest

from runpod.apps.images import (
    SUPPORTED_PYTHON_VERSIONS,
    image_for_spec,
    local_python_version,
    runtime_image,
)
from runpod.apps.spec import ResourceKind, ResourceSpec


class TestRuntimeImage:
    @pytest.mark.parametrize("kind", list(ResourceKind))
    @pytest.mark.parametrize("gpu", [False, True])
    @pytest.mark.parametrize("version", SUPPORTED_PYTHON_VERSIONS)
    def test_full_matrix_resolves(self, kind, gpu, version):
        image = runtime_image(kind, gpu=gpu, python_version=version)
        assert image.startswith("runpod/")
        assert f"py{version}-" in image
        assert ("-gpu:" in image) == gpu

    def test_unsupported_python_rejected(self):
        with pytest.raises(ValueError, match="3.9"):
            runtime_image(ResourceKind.QUEUE, gpu=False, python_version="3.9")

    def test_tag_channel_from_env(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_RUNTIME_TAG", "dev")
        image = runtime_image(ResourceKind.TASK, gpu=True, python_version="3.11")
        assert image == "runpod/task-gpu:py3.11-dev"

    def test_default_channel_is_latest(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_RUNTIME_TAG", raising=False)
        image = runtime_image(ResourceKind.API, gpu=False, python_version="3.12")
        assert image == "runpod/api:py3.12-latest"


class TestImageForSpec:
    def test_custom_image_wins(self):
        spec = ResourceSpec(
            kind=ResourceKind.QUEUE, name="q", image="my/image:1"
        )
        assert image_for_spec(spec) == "my/image:1"

    def test_cpu_spec_gets_cpu_image(self):
        spec = ResourceSpec(kind=ResourceKind.QUEUE, name="q", cpu=["cpu3c-1-2"])
        assert image_for_spec(spec, python_version="3.12") == (
            "runpod/queue:py3.12-latest"
        )

    def test_gpu_spec_gets_gpu_image(self):
        spec = ResourceSpec(kind=ResourceKind.QUEUE, name="q", gpu=["ADA_24"])
        assert image_for_spec(spec, python_version="3.12") == (
            "runpod/queue-gpu:py3.12-latest"
        )


class TestLocalPythonVersion:
    def test_returns_supported_version(self):
        assert local_python_version() in SUPPORTED_PYTHON_VERSIONS

    def test_unsupported_local_python_fails_loudly(self, monkeypatch):
        # cloudpickle payloads are version-bound; a silent fallback
        # would break at deserialization time on the worker
        import runpod.apps.images as images

        class FakeVersion:
            major, minor = 3, 9

        monkeypatch.setattr(images.sys, "version_info", FakeVersion)
        with pytest.raises(RuntimeError, match="3.9"):
            local_python_version()

    def test_supported_range_policy(self):
        # non-EOL cpython with torch wheels; update at EOL boundaries
        assert SUPPORTED_PYTHON_VERSIONS == (
            "3.10",
            "3.11",
            "3.12",
            "3.13",
            "3.14",
        )
