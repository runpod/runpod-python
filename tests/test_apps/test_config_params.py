"""endpoint configuration parameters across spec, dev, and deploy payloads."""

import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.dev import _endpoint_input
from runpod.apps.errors import InvalidResourceError
from runpod.apps.spec import (
    ResourceKind,
    ResourceSpec,
    normalize_cuda_version,
    normalize_scaler_type,
)


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class TestNormalizers:
    def test_scaler_type_uppercases(self):
        assert normalize_scaler_type("queue_delay") == "QUEUE_DELAY"
        assert normalize_scaler_type("REQUEST_COUNT") == "REQUEST_COUNT"

    def test_scaler_type_none_passes(self):
        assert normalize_scaler_type(None) is None

    def test_scaler_type_rejects_unknown(self):
        with pytest.raises(InvalidResourceError):
            normalize_scaler_type("SOMETHING")

    def test_cuda_version_valid(self):
        assert normalize_cuda_version("12.8") == "12.8"

    def test_cuda_version_none_passes(self):
        assert normalize_cuda_version(None) is None

    def test_cuda_version_rejects_unknown(self):
        with pytest.raises(InvalidResourceError):
            normalize_cuda_version("10.0")


class TestSpecValidation:
    def test_max_concurrency_floor(self):
        with pytest.raises(InvalidResourceError):
            ResourceSpec(
                kind=ResourceKind.QUEUE, name="q", max_concurrency=0
            )

    def test_execution_timeout_floor(self):
        with pytest.raises(InvalidResourceError):
            ResourceSpec(
                kind=ResourceKind.QUEUE, name="q", execution_timeout_ms=-1
            )

    def test_scaler_value_floor(self):
        with pytest.raises(InvalidResourceError):
            ResourceSpec(kind=ResourceKind.QUEUE, name="q", scaler_value=0)

    def test_container_disk_floor(self):
        with pytest.raises(InvalidResourceError):
            ResourceSpec(
                kind=ResourceKind.QUEUE, name="q", container_disk_gb=0
            )

    def test_min_cuda_rejected_on_cpu(self):
        with pytest.raises(InvalidResourceError):
            ResourceSpec(
                kind=ResourceKind.QUEUE,
                name="q",
                cpu=["cpu3c-1-2"],
                min_cuda_version="12.8",
            )

    def test_effective_scaler_type_defaults_by_kind(self):
        queue = ResourceSpec(kind=ResourceKind.QUEUE, name="q")
        api = ResourceSpec(kind=ResourceKind.API, name="api")
        assert queue.effective_scaler_type == "QUEUE_DELAY"
        assert api.effective_scaler_type == "REQUEST_COUNT"

    def test_effective_scaler_type_explicit_wins(self):
        spec = ResourceSpec(
            kind=ResourceKind.QUEUE, name="q", scaler_type="REQUEST_COUNT"
        )
        assert spec.effective_scaler_type == "REQUEST_COUNT"


class TestDecoratorPlumbing:
    def test_queue_accepts_all_params(self):
        app = App("a")

        @app.queue(
            name="q",
            gpu="4090",
            max_concurrency=4,
            execution_timeout_ms=60000,
            flashboot=False,
            scaler_type="request_count",
            scaler_value=8,
            min_cuda_version="12.4",
            accelerate_downloads=False,
            container_disk_gb=50,
        )
        def q():
            pass

        spec = q.spec
        assert spec.max_concurrency == 4
        assert spec.execution_timeout_ms == 60000
        assert spec.flashboot is False
        assert spec.scaler_type == "REQUEST_COUNT"
        assert spec.scaler_value == 8
        assert spec.min_cuda_version == "12.4"
        assert spec.accelerate_downloads is False
        assert spec.container_disk_gb == 50

    def test_api_accepts_params(self):
        app = App("a")

        @app.api(
            name="api",
            cpu="cpu3c-1-2",
            execution_timeout_ms=30000,
            scaler_value=2,
            container_disk_gb=20,
        )
        class Api:
            @runpod.post("/x")
            def x(self, body: dict):
                return body

        assert Api.spec.execution_timeout_ms == 30000
        assert Api.spec.scaler_value == 2
        assert Api.spec.container_disk_gb == 20

    def test_task_accepts_params(self):
        app = App("a")

        @app.task(
            name="t",
            gpu="4090",
            min_cuda_version="12.8",
            accelerate_downloads=False,
            container_disk_gb=100,
        )
        def t():
            pass

        assert t.spec.min_cuda_version == "12.8"
        assert t.spec.accelerate_downloads is False
        assert t.spec.container_disk_gb == 100

    def test_queue_invalid_scaler_fails_at_decoration(self):
        app = App("a")

        with pytest.raises(InvalidResourceError):

            @app.queue(name="q", scaler_type="nope")
            def q():
                pass


class TestManifestSerialization:
    def test_non_defaults_serialized(self):
        spec = ResourceSpec(
            kind=ResourceKind.QUEUE,
            name="q",
            max_concurrency=4,
            execution_timeout_ms=1000,
            flashboot=False,
            scaler_type="REQUEST_COUNT",
            scaler_value=8,
            min_cuda_version="12.8",
            accelerate_downloads=False,
            container_disk_gb=42,
        )
        data = spec.to_manifest()
        assert data["maxConcurrency"] == 4
        assert data["executionTimeoutMs"] == 1000
        assert data["flashboot"] is False
        assert data["scalerType"] == "REQUEST_COUNT"
        assert data["scalerValue"] == 8
        assert data["minCudaVersion"] == "12.8"
        assert data["accelerateDownloads"] is False
        assert data["containerDiskGb"] == 42

    def test_defaults_omitted(self):
        spec = ResourceSpec(kind=ResourceKind.QUEUE, name="q")
        data = spec.to_manifest()
        for key in (
            "maxConcurrency",
            "executionTimeoutMs",
            "flashboot",
            "scalerType",
            "scalerValue",
            "minCudaVersion",
            "accelerateDownloads",
            "containerDiskGb",
        ):
            assert key not in data


class TestDevPayload:
    def test_config_params_reach_payload(self):
        app = App("a")

        @app.queue(
            name="q",
            gpu="4090",
            max_concurrency=3,
            execution_timeout_ms=90000,
            scaler_type="request_count",
            scaler_value=7,
            min_cuda_version="12.6",
            container_disk_gb=40,
        )
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        assert payload["scalerType"] == "REQUEST_COUNT"
        assert payload["scalerValue"] == 7
        assert payload["executionTimeoutMs"] == 90000
        assert payload["minCudaVersion"] == "12.6"
        assert payload["template"]["containerDiskInGb"] == 40
        env = {e["key"]: e["value"] for e in payload["template"]["env"]}
        assert env["RUNPOD_MAX_CONCURRENCY"] == "3"

    def test_flashboot_disabled_omits_flag(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2", flashboot=False)
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        assert "flashBootType" not in payload

    def test_defaults_unchanged(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        assert payload["scalerType"] == "QUEUE_DELAY"
        assert payload["scalerValue"] == 4
        assert payload["executionTimeoutMs"] == 0
        assert payload["flashBootType"] == "FLASHBOOT"
        assert payload["template"]["containerDiskInGb"] == 10
        env = {e["key"]: e["value"] for e in payload["template"]["env"]}
        assert "RUNPOD_MAX_CONCURRENCY" not in env


class TestDeployPayload:
    def test_config_params_reach_payload(self):
        from runpod.apps.deploy import _deployed_endpoint_input

        app = App("dep")

        @app.queue(
            name="que",
            gpu="4090",
            max_concurrency=2,
            execution_timeout_ms=5000,
            scaler_value=9,
            min_cuda_version="12.8",
            container_disk_gb=64,
        )
        def que():
            pass

        payload = _deployed_endpoint_input(app, que.spec, "env-1", "b-1", "3.12")
        assert payload["scalerValue"] == 9
        assert payload["executionTimeoutMs"] == 5000
        assert payload["minCudaVersion"] == "12.8"
        assert payload["template"]["containerDiskInGb"] == 64
        env = {e["key"]: e["value"] for e in payload["template"]["env"]}
        assert env["RUNPOD_MAX_CONCURRENCY"] == "2"

    def test_flashboot_default_present(self):
        from runpod.apps.deploy import _deployed_endpoint_input

        app = App("dep")

        @app.queue(name="que", cpu="cpu3c-1-2")
        def que():
            pass

        payload = _deployed_endpoint_input(app, que.spec, "env-1", "b-1", "3.12")
        assert payload["flashBootType"] == "FLASHBOOT"

    def test_flashboot_disabled_omitted(self):
        from runpod.apps.deploy import _deployed_endpoint_input

        app = App("dep")

        @app.queue(name="que", cpu="cpu3c-1-2", flashboot=False)
        def que():
            pass

        payload = _deployed_endpoint_input(app, que.spec, "env-1", "b-1", "3.12")
        assert "flashBootType" not in payload


class TestTaskPayload:
    def test_min_cuda_expands_to_allowed_versions(self):
        from runpod.apps.tasks import _pod_input, cuda_versions_at_least

        app = App("a")

        @app.task(name="t", gpu="4090", min_cuda_version="12.8")
        def t():
            pass

        pod = _pod_input(t.spec, "tok", "t")
        assert pod["allowedCudaVersions"] == ["12.8", "12.9", "13.0"]
        assert cuda_versions_at_least("12.9") == ["12.9", "13.0"]

    def test_container_disk_override(self):
        from runpod.apps.tasks import _pod_input

        app = App("a")

        @app.task(name="t", cpu="cpu3c-1-2", container_disk_gb=25)
        def t():
            pass

        pod = _pod_input(t.spec, "tok", "t")
        assert pod["containerDiskInGb"] == 25


class TestAccelerateDownloads:
    def test_flag_travels_in_function_request(self):
        from runpod.apps.targets import LiveTarget

        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2", accelerate_downloads=False)
        def q(x: int):
            return x

        target = LiveTarget("ep-1", "q")
        payload = target.build_payload(q._fn, q.spec, (1,), {})
        assert payload["input"]["accelerate_downloads"] is False

    def test_default_true(self):
        from runpod.apps.targets import LiveTarget

        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q(x: int):
            return x

        target = LiveTarget("ep-1", "q")
        payload = target.build_payload(q._fn, q.spec, (1,), {})
        assert payload["input"]["accelerate_downloads"] is True
