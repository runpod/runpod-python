"""Tests for the API wrapper commands."""

from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import unquote
from unittest.mock import patch

import pytest
import requests

from runpod.api import ctl_commands
from runpod.error import QueryError


def test_get_user_uses_graphql():
    with patch(
        "runpod.api.ctl_commands.run_graphql_query",
        return_value={"data": {"myself": {"id": "user"}}},
    ) as request:
        assert ctl_commands.get_user(api_key="key") == {"id": "user"}

    request.assert_called_once_with(ctl_commands.user_queries.QUERY_USER, api_key="key")


def test_update_user_settings_replaces_ssh_keys():
    with (
        patch("runpod.api.ctl_commands.run_rest_request") as request,
        patch(
            "runpod.api.ctl_commands.get_user",
            return_value={"id": "user", "pubKey": "ssh-ed25519 key user"},
        ) as get_user,
    ):
        result = ctl_commands.update_user_settings(
            "\nssh-ed25519 key user\n\n", api_key="key"
        )

    assert result == {"id": "user", "pubKey": "ssh-ed25519 key user"}
    request.assert_called_once_with(
        "PUT",
        "/v2/account/ssh-keys",
        api_key="key",
        json={"keys": ["ssh-ed25519 key user"]},
    )
    get_user.assert_called_once_with(api_key="key")


def test_get_gpus_unwraps_response():
    gpus = [{"id": "NVIDIA A100", "name": "A100", "memory": 80}]
    with patch(
        "runpod.api.ctl_commands.run_rest_request", return_value={"gpus": gpus}
    ) as request:
        assert ctl_commands.get_gpus(api_key="key") == gpus

    request.assert_called_once_with("GET", "/v2/catalog/gpus", api_key="key")


def test_get_gpu_requests_pod_availability():
    gpu = {"id": "NVIDIA A100"}
    with patch("runpod.api.ctl_commands.run_rest_request", return_value=gpu) as request:
        assert ctl_commands.get_gpu("NVIDIA A100", 2, api_key="key") == gpu

    request.assert_called_once_with(
        "GET",
        "/v2/catalog/gpus/NVIDIA%20A100",
        api_key="key",
        params={"include": "AVAILABILITY", "product": "POD", "count": 2},
    )


def test_get_gpu_converts_not_found_to_value_error():
    with (
        patch(
            "runpod.api.ctl_commands.run_rest_request",
            side_effect=QueryError("not found", status_code=404),
        ),
        pytest.raises(ValueError, match="No GPU found"),
    ):
        ctl_commands.get_gpu("missing")


def test_get_gpu_propagates_other_api_errors():
    with (
        patch(
            "runpod.api.ctl_commands.run_rest_request",
            side_effect=QueryError("forbidden", status_code=403),
        ),
        pytest.raises(QueryError, match="forbidden"),
    ):
        ctl_commands.get_gpu("NVIDIA A100")


def test_get_pods_unwraps_response():
    pods = [{"id": "pod"}]
    with patch(
        "runpod.api.ctl_commands.run_rest_request", return_value={"pods": pods}
    ) as request:
        assert ctl_commands.get_pods(api_key="key") == pods

    request.assert_called_once_with("GET", "/v2/pods", api_key="key")


def test_get_pod_escapes_id():
    with patch(
        "runpod.api.ctl_commands.run_rest_request", return_value={"id": "pod/id"}
    ) as request:
        assert ctl_commands.get_pod("pod/id", api_key="key") == {"id": "pod/id"}

    request.assert_called_once_with("GET", "/v2/pods/pod%2Fid", api_key="key")


def test_get_pod_returns_none_when_not_found():
    with patch(
        "runpod.api.ctl_commands.run_rest_request",
        side_effect=QueryError("not found", status_code=404),
    ):
        assert ctl_commands.get_pod("missing") is None


def test_get_pod_propagates_other_api_errors():
    with (
        patch(
            "runpod.api.ctl_commands.run_rest_request",
            side_effect=QueryError("forbidden", status_code=403),
        ),
        pytest.raises(QueryError, match="forbidden"),
    ):
        ctl_commands.get_pod("pod")


def _log_event(event_id, line, source="container"):
    return {
        "id": event_id,
        "data": f'{{"ts": "2026-06-01T12:00:00Z", "source": "{source}", "line": "{line}"}}',
    }


def _entry(event_id, line, source="container"):
    return {"id": event_id, "ts": "2026-06-01T12:00:00Z", "source": source, "line": line}


def _streams(*batches):
    """Return a read_event_stream side effect that serves one batch per call.

    A batch is a list of events, optionally ending in an exception to raise.
    """
    batches = list(batches)

    def stream(*_args, **_kwargs):
        for item in batches.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item

    return stream


def test_get_pod_logs_returns_entries_with_ids():
    events = [_log_event("1", "starting"), _log_event("2", "ready", "system")]
    with patch(
        "runpod.api.ctl_commands.read_event_stream", side_effect=_streams(events)
    ) as stream:
        logs = ctl_commands.get_pod_logs(
            "pod/id",
            tail=50,
            since=datetime(2026, 6, 1, tzinfo=timezone.utc),
            max_wait=2,
            api_key="key",
        )

    assert logs == [_entry("1", "starting"), _entry("2", "ready", "system")]
    stream.assert_called_once()
    assert stream.call_args.args == ("/v2/pods/pod%2Fid/logs",)
    kwargs = stream.call_args.kwargs
    assert kwargs["api_key"] == "key"
    assert kwargs["params"] == {"tail": 50, "since": "2026-06-01T00:00:00+00:00"}
    assert kwargs["last_event_id"] is None
    assert 0 < kwargs["max_wait"] <= 2


def test_get_pod_logs_omits_unset_params():
    with patch(
        "runpod.api.ctl_commands.read_event_stream", side_effect=_streams([])
    ) as stream:
        assert ctl_commands.get_pod_logs("pod", source="system") == []

    assert stream.call_args.kwargs["params"] == {"source": "system"}


def test_get_pod_logs_drops_oldest_lines_past_byte_cap():
    events = [_log_event(str(i), "x" * 10) for i in range(5)]
    with patch("runpod.api.ctl_commands.read_event_stream", side_effect=_streams(events)):
        logs = ctl_commands.get_pod_logs("pod", max_bytes=25)

    assert [entry["id"] for entry in logs] == ["3", "4"]


def test_get_pod_logs_does_not_retry():
    with (
        patch(
            "runpod.api.ctl_commands.read_event_stream",
            side_effect=_streams([QueryError("slow down", status_code=429)]),
        ),
        pytest.raises(QueryError),
    ):
        ctl_commands.get_pod_logs("pod")


def test_get_endpoint_worker_logs_uses_worker_path():
    with patch(
        "runpod.api.ctl_commands.read_event_stream",
        side_effect=_streams([_log_event("1", "Worker ready.")]),
    ) as stream:
        logs = ctl_commands.get_endpoint_worker_logs("ep", "worker/1", tail=10)

    assert logs == [_entry("1", "Worker ready.")]
    assert stream.call_args.args == ("/v2/serverless/ep/workers/worker%2F1/logs",)
    assert stream.call_args.kwargs["params"] == {"tail": 10}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tail": -1},
        {"tail": 5001},
        {"source": "both"},
        {"max_wait": 0},
        {"since": datetime(2026, 6, 1)},
    ],
)
@pytest.mark.parametrize(
    "call",
    [
        lambda **kw: ctl_commands.get_pod_logs("pod", **kw),
        lambda **kw: ctl_commands.iter_pod_logs("pod", **kw),
        lambda **kw: ctl_commands.get_endpoint_worker_logs("ep", "w", **kw),
        lambda **kw: ctl_commands.iter_endpoint_worker_logs("ep", "w", **kw),
    ],
    ids=["get_pod", "iter_pod", "get_worker", "iter_worker"],
)
def test_log_functions_validate_arguments_eagerly(call, kwargs):
    with (
        patch("runpod.api.ctl_commands.read_event_stream") as stream,
        pytest.raises(ValueError),
    ):
        call(**kwargs)

    stream.assert_not_called()


def test_get_pod_logs_requires_max_wait():
    with pytest.raises(ValueError):
        ctl_commands.get_pod_logs("pod", max_wait=None)


def test_iter_pod_logs_resumes_from_last_event_id():
    with (
        patch(
            "runpod.api.ctl_commands.read_event_stream",
            side_effect=_streams(
                [_log_event("1", "a"), _log_event("2", "b")],
                [],
                [_log_event("3", "c")],
            ),
        ) as stream,
        patch("runpod.api.ctl_commands.time.sleep") as sleep,
    ):
        logs = ctl_commands.iter_pod_logs("pod", tail=5, source="container")
        lines = [next(logs)["line"] for _ in range(3)]
        logs.close()

    assert lines == ["a", "b", "c"]
    assert [call.kwargs["last_event_id"] for call in stream.call_args_list] == [
        None,
        "2",
        "2",
    ]
    assert all(call.kwargs["max_wait"] is None for call in stream.call_args_list)
    assert all(
        call.kwargs["params"] == {"tail": 5, "source": "container"}
        for call in stream.call_args_list
    )
    assert [call.args for call in sleep.call_args_list] == [(1,), (1,)]


def test_iter_pod_logs_retries_rate_limit_and_network_errors_on_reconnect():
    with (
        patch(
            "runpod.api.ctl_commands.read_event_stream",
            side_effect=_streams(
                [_log_event("1", "a")],
                [QueryError("slow down", status_code=429, retry_after=12)],
                [requests.exceptions.ConnectionError("reset")],
                [_log_event("2", "b")],
            ),
        ),
        patch("runpod.api.ctl_commands.time.sleep") as sleep,
    ):
        logs = ctl_commands.iter_pod_logs("pod")
        lines = [next(logs)["line"] for _ in range(2)]
        logs.close()

    assert lines == ["a", "b"]
    assert [call.args for call in sleep.call_args_list] == [(1,), (12,), (1,)]


@pytest.mark.parametrize(
    "failure",
    [
        QueryError("pod not found", status_code=404),
        QueryError("slow down", status_code=429, retry_after=3),
        requests.exceptions.ConnectionError("refused"),
    ],
)
def test_iter_pod_logs_raises_on_first_connection_failure(failure):
    with (
        patch(
            "runpod.api.ctl_commands.read_event_stream",
            side_effect=_streams([failure]),
        ),
        pytest.raises(type(failure)),
    ):
        next(ctl_commands.iter_pod_logs("pod"))


def test_iter_pod_logs_raises_non_retryable_error_on_reconnect():
    with (
        patch(
            "runpod.api.ctl_commands.read_event_stream",
            side_effect=_streams(
                [_log_event("1", "a")],
                [QueryError("forbidden", status_code=403)],
            ),
        ),
        patch("runpod.api.ctl_commands.time.sleep"),
        pytest.raises(QueryError, match="forbidden"),
    ):
        list(ctl_commands.iter_pod_logs("pod"))


def test_iter_pod_logs_stops_at_max_wait():
    with (
        patch(
            "runpod.api.ctl_commands.read_event_stream",
            side_effect=_streams([_log_event("1", "a")], [_log_event("2", "b")]),
        ) as stream,
        patch("runpod.api.ctl_commands.time.sleep"),
        patch("runpod.api.ctl_commands.time.monotonic", side_effect=[0, 0, 4, 4, 11, 11]),
    ):
        logs = list(ctl_commands.iter_pod_logs("pod", max_wait=10))

    assert [entry["line"] for entry in logs] == ["a", "b"]
    assert [call.kwargs["max_wait"] for call in stream.call_args_list] == [10, 6]


def test_iter_endpoint_worker_logs_uses_worker_path():
    with patch(
        "runpod.api.ctl_commands.read_event_stream",
        side_effect=_streams([_log_event("1", "a")]),
    ) as stream:
        logs = ctl_commands.iter_endpoint_worker_logs("ep", "w")
        assert next(logs)["line"] == "a"
        logs.close()

    assert stream.call_args.args == ("/v2/serverless/ep/workers/w/logs",)


def test_get_endpoint_workers_unwraps_response():
    workers = [{"id": "worker", "status": "RUNNING"}]
    with patch(
        "runpod.api.ctl_commands.run_rest_request",
        return_value={"workers": workers, "summary": {"RUNNING": 1}},
    ) as request:
        assert ctl_commands.get_endpoint_workers("ep/1", api_key="key") == workers

    request.assert_called_once_with(
        "GET", "/v2/serverless/ep%2F1/workers", api_key="key"
    )


@pytest.fixture
def template_backend():
    """Model REST template expansion and its persistent-volume size floor."""
    templates = {
        "template/id": {
            "image": "training-image",
            "args": "python train.py",
            "mounts": {"persistent": {"size": 30, "path": "/training"}},
        }
    }

    def request(method, path, *, json=None):
        if method == "GET" and path.startswith("/v2/templates/"):
            return deepcopy(templates[unquote(path.rsplit("/", 1)[1])])
        if method != "POST" or path not in {"/v2/templates", "/v2/pods"}:
            raise AssertionError(f"Unexpected request: {method} {path}")
        persistent = json.get("mounts", {}).get("persistent")
        if persistent is not None and persistent["size"] < 10:
            raise QueryError(
                "Persistent volume must be at least 10 GB", status_code=400
            )
        if path == "/v2/templates":
            templates["created-template"] = deepcopy(json)
            return {"id": "created-template"}

        # REST replaces an explicitly supplied mount object; it does not merge
        # a path-only override with the template's persistent-volume size.
        effective = deepcopy(templates.get(json.get("templateId"), {}))
        effective.update(deepcopy(json))
        effective.setdefault("mounts", {})
        return effective

    with patch("runpod.api.ctl_commands.run_rest_request", side_effect=request):
        yield


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [({}, "python train.py"), ({"docker_args": ""}, "")],
    ids=["inherit-command", "clear-command"],
)
def test_create_pod_template_command_precedence(template_backend, kwargs, expected):
    pod = ctl_commands.create_pod(
        "training", template_id="template/id", gpu_type_id="NVIDIA A100", **kwargs
    )

    assert pod["args"] == expected


@pytest.mark.parametrize(
    ("kwargs", "expected_path"),
    [({}, "/training"), ({"volume_mount_path": "/custom"}, "/custom")],
    ids=["inherit-mount", "override-path-retain-size"],
)
def test_create_pod_template_mount_precedence(template_backend, kwargs, expected_path):
    pod = ctl_commands.create_pod(
        "training", template_id="template/id", gpu_type_id="NVIDIA A100", **kwargs
    )

    assert pod["mounts"] == {"persistent": {"size": 30, "path": expected_path}}


def test_create_cpu_pod_rejects_unrepresentable_template_mount_path(template_backend):
    with pytest.raises(ValueError, match="volume_mount_path"):
        ctl_commands.create_pod(
            "training",
            template_id="template/id",
            instance_id="cpu3c-4-8",
            volume_mount_path="/custom",
        )


def test_create_pod_path_override_without_template_mounts(template_backend):
    template = ctl_commands.create_template("disk-only", "image", volume_in_gb=0)
    pod = ctl_commands.create_pod(
        "training",
        template_id=template["id"],
        gpu_type_id="NVIDIA A100",
        volume_mount_path="/custom",
    )

    assert pod["mounts"] == {}


@pytest.mark.parametrize(
    ("kwargs", "expected_mounts"),
    [
        (
            {"image_name": "image", "volume_in_gb": 20},
            {"persistent": {"size": 20, "path": "/runpod-volume"}},
        ),
        (
            {"template_id": "template/id", "network_volume_id": "volume"},
            {"network": [{"volumeId": "volume", "path": "/runpod-volume"}]},
        ),
    ],
    ids=["fresh-persistent-volume", "network-replaces-template-storage"],
)
def test_create_pod_new_storage_uses_default_mount(
    template_backend, kwargs, expected_mounts
):
    pod = ctl_commands.create_pod("training", gpu_type_id="NVIDIA A100", **kwargs)

    assert pod["mounts"] == expected_mounts


def test_create_gpu_pod_enforces_per_gpu_host_minima():
    # Both deficient offers can satisfy total-pod minima for two GPUs, but not
    # the requested per-GPU minima. The exact boundary must remain eligible.
    offers = [
        ("too-little-ram", 16, 8),
        ("too-few-cpus", 32, 4),
        ("at-boundary", 32, 8),
        ("oversized", 64, 64),
    ]

    def allocate(method, path, *, json):
        assert (method, path) == ("POST", "/v2/pods")
        gpu = json["gpu"]
        for machine, ram, vcpus in offers:
            if ram >= gpu.get("minRamPerGpu", 1) and vcpus >= gpu.get(
                "minVcpuCountPerGpu", 1
            ):
                return {"machineId": machine}
        raise QueryError("No matching machine", status_code=400)

    with patch("runpod.api.ctl_commands.run_rest_request", side_effect=allocate):
        pod = ctl_commands.create_pod(
            "training",
            "image",
            gpu_type_id="NVIDIA A100",
            gpu_count=2,
            min_memory_in_gb=32,
            min_vcpu_count=8,
        )

    assert pod["machineId"] == "at-boundary"


def test_create_cpu_pod_requires_instance_id():
    with pytest.raises(ValueError, match="instance_id"):
        ctl_commands.create_pod("cpu-pod", "python:3.11")


@pytest.mark.parametrize(
    "instance_id", ["cpu3c-invalid", "cpu3c", "cpu3c-4-not-a-number"]
)
def test_create_cpu_pod_validates_instance_id(template_backend, instance_id):
    with pytest.raises(ValueError, match="format"):
        ctl_commands.create_pod("cpu-pod", "python:3.11", instance_id=instance_id)


def test_create_pod_validates_image_and_cloud():
    with pytest.raises(ValueError, match="Either image_name or template_id"):
        ctl_commands.create_pod("pod", gpu_type_id="NVIDIA A100")

    with pytest.raises(ValueError, match="cloud_type"):
        ctl_commands.create_pod(
            "pod", "image", gpu_type_id="NVIDIA A100", cloud_type="INVALID"
        )


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"support_public_ip": True}, "support_public_ip"),
        ({"country_code": "US"}, "country_code"),
        ({"min_download": 100}, "min_download"),
        ({"min_upload": 100}, "min_upload"),
    ],
)
def test_create_gpu_pod_rejects_unsupported_constraints(template_backend, kwargs, field):
    with pytest.raises(ValueError, match=field):
        ctl_commands.create_pod("pod", "image", gpu_type_id="NVIDIA A100", **kwargs)


def test_resume_pod_rejects_gpu_count():
    with (
        patch("runpod.api.ctl_commands.run_rest_request", return_value={"id": "pod"}),
        pytest.raises(ValueError, match="gpu_count"),
    ):
        ctl_commands.resume_pod("pod", 8)


def test_terminate_pod_deletes_resource():
    with patch(
        "runpod.api.ctl_commands.run_rest_request", return_value=None
    ) as request:
        assert ctl_commands.terminate_pod("pod/id") is None

    request.assert_called_once_with("DELETE", "/v2/pods/pod%2Fid")


@pytest.mark.parametrize(
    ("volume_in_gb", "expected_mounts"),
    [
        (0, {}),
        (10, {"persistent": {"size": 10, "path": "/data"}}),
    ],
    ids=["no-persistent-storage", "minimum-persistent-storage"],
)
def test_create_template_persistent_storage_boundary(
    template_backend, volume_in_gb, expected_mounts
):
    template = ctl_commands.create_template(
        "template", "image", volume_in_gb=volume_in_gb, volume_mount_path="/data"
    )
    pod = ctl_commands.create_pod(
        "training", template_id=template["id"], gpu_type_id="NVIDIA A100"
    )

    assert pod["mounts"] == expected_mounts


def test_get_endpoints_unwraps_response():
    endpoints = [{"id": "endpoint"}]
    with patch(
        "runpod.api.ctl_commands.run_rest_request",
        return_value={"endpoints": endpoints},
    ) as request:
        assert ctl_commands.get_endpoints() == endpoints

    request.assert_called_once_with("GET", "/v2/serverless")


@pytest.fixture
def endpoint_backend():
    data_centers = ["US-KS-2", "US-TX-3", "EU-RO-1", "CA-MTL-1"]
    pools = {
        "AMPERE_16": {"NVIDIA RTX A4000"},
        "ADA_48_PRO": {"NVIDIA L40", "NVIDIA L40S"},
    }

    def request(method, path, *, json):
        if (method, path) != ("POST", "/v2/serverless"):
            raise AssertionError(f"Unexpected request: {method} {path}")
        eligible = set()
        for pool in json["gpu"]["pools"]:
            if pool not in pools:
                raise QueryError("GPU pool is not available", status_code=400)
            eligible.update(pools[pool])
        eligible.difference_update(json["gpu"].get("excludedTypes", []))
        selection = json.get("dataCenterIds") or data_centers
        placements = [center for center in selection if center in data_centers]
        if not placements:
            raise QueryError("No matching data centers", status_code=400)
        return {"eligibleGpuTypes": sorted(eligible), "dataCenterIds": placements}

    with patch("runpod.api.ctl_commands.run_rest_request", side_effect=request):
        yield


def test_create_endpoint_preserves_datacenter_selection(endpoint_backend):
    endpoint = ctl_commands.create_endpoint(
        "endpoint", "template", locations="US-KS-2, EU-RO-1"
    )

    assert set(endpoint["dataCenterIds"]) == {"US-KS-2", "EU-RO-1"}


def test_create_endpoint_preserves_excluded_gpu_types(endpoint_backend):
    endpoint = ctl_commands.create_endpoint(
        "endpoint",
        "template",
        gpu_ids="ADA_48_PRO, AMPERE_16, -NVIDIA L40, - NVIDIA L40",
    )

    assert endpoint["eligibleGpuTypes"] == ["NVIDIA L40S", "NVIDIA RTX A4000"]


@pytest.mark.parametrize("scaler_type", ["INVALID", "WORKER_COUNT"])
def test_create_endpoint_rejects_invalid_scaler(endpoint_backend, scaler_type):
    with pytest.raises(ValueError, match="scaler_type"):
        ctl_commands.create_endpoint("endpoint", "template", scaler_type=scaler_type)


@pytest.mark.parametrize("idle_timeout", [5, 30])
def test_create_endpoint_rejects_idle_timeout_for_request_count(
    endpoint_backend, idle_timeout
):
    with pytest.raises(ValueError, match="idle_timeout"):
        ctl_commands.create_endpoint(
            "endpoint",
            "template",
            scaler_type="REQUEST_COUNT",
            idle_timeout=idle_timeout,
        )


def test_update_endpoint_template_uses_patch():
    with patch(
        "runpod.api.ctl_commands.run_rest_request", return_value={"id": "endpoint"}
    ) as request:
        assert ctl_commands.update_endpoint_template("endpoint/id", "template") == {
            "id": "endpoint"
        }

    request.assert_called_once_with(
        "PATCH",
        "/v2/serverless/endpoint%2Fid",
        json={"templateId": "template"},
    )


def test_create_container_registry_auth_uses_rest():
    with patch(
        "runpod.api.ctl_commands.run_rest_request", return_value={"id": "registry"}
    ) as request:
        result = ctl_commands.create_container_registry_auth(
            "registry", "user", "password"
        )

    assert result == {"id": "registry"}
    request.assert_called_once_with(
        "POST",
        "/v2/registries",
        json={"name": "registry", "username": "user", "password": "password"},
    )


def test_delete_container_registry_auth_uses_rest():
    with patch(
        "runpod.api.ctl_commands.run_rest_request", return_value=None
    ) as request:
        assert ctl_commands.delete_container_registry_auth("registry/id") is True

    request.assert_called_once_with("DELETE", "/v2/registries/registry%2Fid")
