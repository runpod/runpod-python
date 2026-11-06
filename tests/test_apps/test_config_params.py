"""endpoint configuration parameters across spec, dev, and deploy payloads."""

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.datacenter import DataCenter
from runpod.apps.dev import _endpoint_input
from runpod.apps.errors import InvalidResourceError
from runpod.apps.spec import (
    DATACENTER_IDS_DOCS_URL,
    ResourceKind,
    ResourceSpec,
    normalize_cuda_version,
    normalize_datacenter,
    normalize_scaler_type,
)

UNLISTED_DC = "XX-TEST-1"


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


class TestDatacenterNormalization:
    @pytest.fixture(autouse=True)
    def _fresh_warnings(self):
        from runpod.apps.spec import _warn_unlisted_datacenter

        _warn_unlisted_datacenter.cache_clear()
        yield
        _warn_unlisted_datacenter.cache_clear()

    @pytest.mark.parametrize(
        "value", [DataCenter.EU_RO_1, "EU-RO-1", "eu_ro_1", " eu-ro-1 ", ["eu_ro_1"]]
    )
    def test_known_values_normalize_without_warning(self, value, caplog):
        assert normalize_datacenter(value) == ["EU-RO-1"]
        assert "valid datacenter id" not in caplog.text

    def test_none_passes(self):
        assert normalize_datacenter(None) is None

    def test_unlisted_id_passes_through_with_warning(self, caplog):
        assert normalize_datacenter(UNLISTED_DC) == [UNLISTED_DC]
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert UNLISTED_DC in warnings[0].getMessage()
        message = warnings[0].getMessage()
        assert "doesn't look like a valid datacenter id" in message
        assert "will be used anyway" in message
        assert DATACENTER_IDS_DOCS_URL in message

    def test_mixed_list_keeps_order(self):
        assert normalize_datacenter(["us_ks_2", UNLISTED_DC]) == [
            "US-KS-2",
            UNLISTED_DC,
        ]

    def test_unlisted_id_warns_once_across_revalidation(self, caplog):
        spec = ResourceSpec(kind=ResourceKind.QUEUE, name="q", datacenter=UNLISTED_DC)
        spec.validate()
        spec.to_manifest()
        warnings = [r for r in caplog.records if UNLISTED_DC in r.getMessage()]
        assert len(warnings) == 1
        assert spec.datacenter == [UNLISTED_DC]

    @pytest.mark.parametrize("value", ["", "   ", [""], ["EU-RO-1", " "], [None], 42])
    def test_blank_or_non_string_rejected(self, value):
        with pytest.raises(InvalidResourceError, match="invalid datacenter"):
            normalize_datacenter(value)


class TestSpecValidation:
    @pytest.mark.parametrize(
        "config",
        [
            {"gpu_count": 0},
            {"gpu_count": -2},
            {"workers": (9, 1)},
            {"scaler_type": "BOGUS"},
            {"min_cuda_version": "99"},
        ],
    )
    def test_direct_construction_rejects_invalid_configuration(self, config):
        with pytest.raises(InvalidResourceError):
            ResourceSpec(kind=ResourceKind.QUEUE, name="q", **config)

    def test_mutable_spec_revalidates_before_serialization(self):
        spec = ResourceSpec(kind=ResourceKind.QUEUE, name="q")
        spec.gpu_count = 0
        with pytest.raises(InvalidResourceError):
            spec.to_manifest()
        spec.gpu_count = 2
        spec.workers = 4
        spec.scaler_type = "request_count"
        spec.gpu = "4090"
        manifest = spec.to_manifest()
        assert manifest["gpuCount"] == 2
        assert manifest["workersMax"] == 4
        assert manifest["scalerType"] == "REQUEST_COUNT"
        assert manifest["gpus"] == ["NVIDIA GeForce RTX 4090"]

    def test_mutated_routes_reject_reserved_runtime_path(self):
        from runpod.apps.spec import RouteSpec

        spec = ResourceSpec(kind=ResourceKind.API, name="web")
        spec.routes.append(RouteSpec("POST", "/_runpod/sync", "sync"))
        with pytest.raises(InvalidResourceError):
            spec.to_manifest()

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


class TestDecoratorValidation:

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
