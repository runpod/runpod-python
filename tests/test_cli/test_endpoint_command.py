"""tests for rp endpoint."""

import copy
import json
import threading
import queue
from unittest.mock import MagicMock

import click
import pytest
import requests
from click.testing import CliRunner

from runpod import error
from runpod.endpoint import runner
from runpod.rp_cli import compute, output
from runpod.rp_cli import endpoint as ep
from runpod.rp_cli.main import cli

GPUS = [
    {"id": "NVIDIA H100 80GB HBM3", "name": "H100 SXM", "pool": "ADA_80_PRO"},
    {"id": "NVIDIA H100 NVL", "name": "H100 NVL", "pool": "ADA_80_PRO"},
    {"id": "NVIDIA A100 80GB PCIe", "name": "A100 PCIe", "pool": "ADA_80_PRO"},
    {"id": "NVIDIA GeForce RTX 4090", "name": "RTX 4090", "pool": "ADA_24"},
    {"id": "NVIDIA GeForce RTX 3070", "name": "RTX 3070", "pool": None},
]
CPUS = [
    {"id": "cpu3c", "name": "Compute-Optimized", "group": "CPU3"},
    {"id": "cpu5g", "name": "General Purpose", "group": "CPU5"},
]
DATACENTERS = [
    {"id": "EU-RO-1", "region": "EUROPE"},
    {"id": "EU-CZ-1", "region": "EUROPE"},
    {"id": "US-TX-3", "region": "NORTH_AMERICA"},
]

GPU_ENDPOINT = {
    "id": "ep1",
    "name": "llama-prod",
    "type": "QUEUE",
    "image": "ghcr.io/acme/llama:latest",
    "args": "",
    "env": {"A": "1", "B": "2"},
    "disk": 10,
    "ports": [],
    "gpu": {
        "pools": ["ADA_80_PRO"],
        "excludedTypes": ["NVIDIA A100 80GB PCIe"],
        "count": 1,
        "allowedCudaVersions": [],
        "minCudaVersion": None,
    },
    "workers": {"min": 0, "max": 3, "idleTimeout": 5},
    "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4},
    "timeout": 300000,
    "flashboot": "FLASHBOOT",
    "dataCenterIds": ["EU-RO-1"],
    "networkVolumes": ["vol1"],
    "mounts": {"network": [{"volumeId": "vol1"}], "global": []},
    "requestUrls": {"run": "https://api.runpod.ai/v2/ep1/run"},
    "createdAt": "2026-07-10T22:38:43Z",
}
CPU_ENDPOINT = {
    "id": "ep2",
    "name": "embeddings",
    "type": "QUEUE",
    "image": "ghcr.io/acme/embeddings:latest",
    "env": {},
    "cpu": [{"id": "cpu3c", "vcpuCount": 4, "memory": 8}],
    "workers": {"min": 0, "max": 3},
    "scaling": {"type": "QUEUE_DELAY", "queueDelay": 2},
    "timeout": 0,
    "flashboot": "OFF",
    "dataCenterIds": [],
    "mounts": {"network": [], "global": [{"volumeId": "gv1"}]},
    "createdAt": "2026-07-11T00:00:00Z",
}
LB_ENDPOINT = {
    "id": "lb1",
    "name": "openai-api",
    "type": "LOAD_BALANCER",
    "image": "ghcr.io/acme/openai-server:latest",
    "gpu": {
        "pools": ["ADA_24"],
        "excludedTypes": [],
        "count": 2,
        "allowedCudaVersions": [],
        "minCudaVersion": "12.4",
    },
    "workers": {"min": 1, "max": 10},
    "scaling": {"type": "REQUEST_COUNT", "requestCount": 4},
    "timeout": 0,
    "flashboot": "PRIORITY_FLASHBOOT",
    "dataCenterIds": [],
    "mounts": {"network": [], "global": []},
    "requestUrls": {"base": "https://lb1.api.runpod.ai", "health": "x"},
    "createdAt": "2026-07-12T00:00:00Z",
}
RELEASES = {
    "endpointVersion": 3,
    "rollout": {
        "inProgress": False,
        "workersOnLatest": 2,
        "workersTotal": 2,
        "percentOnLatest": 100,
    },
    "releases": [
        {
            "id": "rel3",
            "version": 3,
            "source": "MANUAL",
            "workerCount": 2,
            "createdAt": "2026-07-12T00:00:00Z",
            "diff": [
                {"field": "template.imageName", "old": "a", "new": "b"},
                {"field": "template.ports", "old": None, "new": ""},
            ],
        },
        {
            "id": "rel2",
            "version": None,
            "source": "GIT_BUILD",
            "buildId": "b1",
            "workerCount": 0,
            "createdAt": "2026-07-11T00:00:00Z",
            "diff": [],
        },
    ],
    "pagination": {"hasNextPage": False, "nextCursor": None},
}
WORKERS = {
    "endpointVersion": 3,
    "summary": {
        "running": 1,
        "idle": 1,
        "initializing": 1,
        "throttled": 0,
        "unhealthy": 0,
        "total": 3,
    },
    "workers": [
        {
            "id": "w1",
            "status": "RUNNING",
            "isStale": False,
            "version": 3,
            "gpuCount": 1,
            "gpuTypeId": "NVIDIA H100 NVL",
            "dataCenterId": "EU-RO-1",
            "uptimeSeconds": 3725,
            "image": "img",
        },
        {
            "id": "w2",
            "status": "IDLE",
            "isStale": True,
            "version": 2,
            "gpuCount": 1,
            "uptimeSeconds": 125,
        },
        {"id": "w3", "status": "INITIALIZING", "isStale": True, "gpuCount": 0},
    ],
}


class FakeApi:
    """routes rest calls by (method, path) and records them."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, path, params=None, json=None, **_):
        self.calls.append((method, path, params, json))
        value = self.routes.get((method, path))
        if callable(value):
            value = value(params, json)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def bodies(self, method):
        return [body for m, _, _, body in self.calls if m == method]


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    from runpod.rp_cli import console as ui

    monkeypatch.setattr(ui.console, "_width", 200)


@pytest.fixture
def api(monkeypatch):
    routes = {
        ("GET", "/v2/catalog/gpus"): {"gpus": GPUS},
        ("GET", "/v2/catalog/cpus"): {"cpus": CPUS},
        ("GET", "/v2/catalog/datacenters"): {"dataCenters": DATACENTERS},
        ("GET", "/v2/serverless"): {
            "endpoints": [GPU_ENDPOINT, CPU_ENDPOINT, LB_ENDPOINT],
            "pagination": {"hasNextPage": False, "nextCursor": None},
        },
        ("GET", "/v2/serverless/ep1"): GPU_ENDPOINT,
        ("GET", "/v2/serverless/ep2"): CPU_ENDPOINT,
        ("GET", "/v2/serverless/lb1"): LB_ENDPOINT,
        ("GET", "/v2/serverless/ep1/workers"): WORKERS,
        ("GET", "/v2/serverless/ep1/releases"): RELEASES,
        ("POST", "/v2/serverless"): lambda params, body: dict(body, id="new1"),
        ("PATCH", "/v2/serverless/ep1"): lambda params, body: dict(
            GPU_ENDPOINT, **body
        ),
        ("PATCH", "/v2/serverless/ep2"): lambda params, body: dict(
            CPU_ENDPOINT, **body
        ),
        ("PATCH", "/v2/serverless/lb1"): lambda params, body: dict(LB_ENDPOINT, **body),
        ("DELETE", "/v2/serverless/ep1"): None,
    }
    fake = FakeApi(routes)
    monkeypatch.setattr(ep, "run_rest_request", fake)
    monkeypatch.setattr(compute, "run_rest_request", fake)
    monkeypatch.setattr(ep.time, "sleep", lambda seconds: None)
    return fake


@pytest.fixture
def client(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(runner, "RunPodClient", lambda: fake)
    return fake


def invoke(*args, input=None):
    return CliRunner().invoke(cli, ["endpoint", *args], input=input)


def ok(*args, input=None):
    result = invoke(*args, input=input)
    assert result.exit_code == 0, result.output
    return result.output


def fails(*args, input=None):
    result = invoke(*args, input=input)
    assert result.exit_code != 0, result.output
    return result.output


# ---------------------------------------------------------------- list/get


def test_list_shows_ids_and_summaries(api):
    out = ok("list")
    for text in ("ep1", "ep2", "lb1", "llama-prod", "load-balancer", "ADA_80_PRO x1"):
        assert text in out
    assert "4s delay" in out and "4/worker" in out and "excluding" not in out


def test_list_filters_by_name_glob_and_type(api):
    rows = json.loads(ok("list", "--name", "LLAMA*", "--json"))
    assert [row["id"] for row in rows] == ["ep1"]
    rows = json.loads(ok("list", "--type", "load-balancer", "--json"))
    assert [row["id"] for row in rows] == ["lb1"]
    assert "no endpoints match" in ok("list", "--name", "nope")


def test_get_shows_fields(api):
    out = ok("get", "ep1")
    assert "llama-prod" in out
    assert "ADA_80_PRO x1 (excluding NVIDIA A100 80GB PCIe)" in out
    assert "queue delay 4s" in out
    assert "vol1 (network)" in out
    assert "300s" in out
    out = ok("get", "lb1")
    assert ">=12.4" in out and "4 requests per worker" in out and "priority" in out
    out = ok("get", "ep2")
    assert "cpu3c 4 vcpu" in out and "gv1 (global)" in out
    assert json.loads(ok("get", "ep1", "--json"))["id"] == "ep1"


def test_get_without_id_needs_a_terminal(api):
    assert "pass an ENDPOINT_ID" in fails("get")


def test_get_without_id_picks_interactively(api, monkeypatch):
    seen = {}

    def fake_pick(message, choices, missing):
        seen["choices"] = choices
        return "ep2"

    monkeypatch.setattr(ep, "interactive", lambda: True)
    monkeypatch.setattr(ep, "pick", fake_pick)
    assert "embeddings" in ok("get")
    assert seen["choices"][0][0] == "ep1"
    assert "llama-prod" in seen["choices"][0][1]


def test_api_errors_are_clean(api):
    api.routes[("GET", "/v2/serverless/ep1")] = error.QueryError("endpoint not found")
    assert "endpoint not found" in fails("get", "ep1")


# ------------------------------------------------------------------ create


def test_create_queue_endpoint_from_ticket(api):
    ok(
        "create",
        "--name", "llama-prod",
        "--type", "queue",
        "--image", "ghcr.io/acme/llama:latest",
        "--gpu", "H100",
        "--gpu-count", "1",
        "--min-workers", "0",
        "--max-workers", "4",
        "--queue-delay-seconds", "4",
        "--idle-timeout-seconds", "10",
        "--request-timeout-seconds", "300",
        "--data-center", "EU-RO-1",
        "--network-volume", "vol1",
        "--env", "MODEL_ID=meta-llama/Llama-3.1-8B",
        "--flashboot", "standard",
    )  # fmt: skip
    (body,) = api.bodies("POST")
    assert body == {
        "type": "QUEUE",
        "name": "llama-prod",
        "image": "ghcr.io/acme/llama:latest",
        "flashboot": "FLASHBOOT",
        "timeout": 300000,
        "env": {"MODEL_ID": "meta-llama/Llama-3.1-8B"},
        "gpu": {
            "pools": ["ADA_80_PRO"],
            "excludedTypes": ["NVIDIA A100 80GB PCIe"],
            "count": 1,
        },
        "workers": {"min": 0, "max": 4, "idleTimeout": 10},
        "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4.0},
        "dataCenterIds": ["EU-RO-1"],
        "mounts": {"network": [{"volumeId": "vol1"}]},
    }


def test_create_load_balancer_endpoint_from_ticket(api):
    out = ok(
        "create",
        "--name", "openai-api",
        "--type", "load-balancer",
        "--image", "ghcr.io/acme/openai-server:latest",
        "--gpu", "h100",
        "--gpu", "4090",
        "--min-workers", "1",
        "--max-workers", "10",
        "--requests-per-worker", "4",
        "--env", "PORT=8000",
        "--env", "PORT_HEALTH=8000",
        "--env", "HEALTH_CHECK_PATH=/health",
    )  # fmt: skip
    assert "new1" in out
    (body,) = api.bodies("POST")
    assert body["type"] == "LOAD_BALANCER"
    assert body["scaling"] == {"type": "REQUEST_COUNT", "requestCount": 4}
    assert body["gpu"]["pools"] == ["ADA_80_PRO", "ADA_24"]
    assert body["env"] == {
        "PORT": "8000",
        "PORT_HEALTH": "8000",
        "HEALTH_CHECK_PATH": "/health",
    }


def test_create_cpu_endpoint_from_ticket(api):
    ok(
        "create",
        "--name", "embeddings",
        "--type", "queue",
        "--image", "ghcr.io/acme/embeddings:latest",
        "--cpu", "cpu3c",
        "--vcpu-count", "8",
        "--min-workers", "0",
        "--max-workers", "3",
        "--queue-delay-seconds", "2",
    )  # fmt: skip
    (body,) = api.bodies("POST")
    assert body["cpu"] == [{"id": "cpu3c", "vcpuCount": 8}]
    assert "gpu" not in body


def test_create_maps_container_and_placement_flags(api):
    ok(
        "create",
        "--name", "x",
        "--type", "queue",
        "--template", "tpl1",
        "--gpu", "h100:2",
        "--cuda", ">=12.4",
        "--region", "eu",
        "--global-volume", "gv1",
        "--port", "8000/http",
        "--disk", "20",
        "--args", "python main.py",
        "--entrypoint", "python -m 'my app'",
        "--cmd", "--port 1",
        "--registry", "reg1",
        "--json",
    )  # fmt: skip
    (body,) = api.bodies("POST")
    assert body["templateId"] == "tpl1"
    assert body["gpu"] == {
        "pools": ["ADA_80_PRO"],
        "excludedTypes": ["NVIDIA A100 80GB PCIe"],
        "count": 2,
        "minCudaVersion": "12.4",
    }
    assert body["scaling"] == {"type": "QUEUE_DELAY", "queueDelay": 4}
    assert body["dataCenterIds"] == ["EU-RO-1", "EU-CZ-1"]
    assert body["mounts"] == {"global": [{"volumeId": "gv1"}]}
    assert body["ports"] == ["8000/http"]
    assert body["entrypoint"] == ["python", "-m", "my app"]
    assert body["cmd"] == ["--port", "1"]
    assert body["registry"] == "reg1" and body["disk"] == 20


def test_create_exact_cuda_and_cpu_vcpu_suffix(api):
    ok("create", "--name", "x", "--type", "queue", "--image", "i", "--gpu", "4090",
       "--cuda", "12.4,12.8")  # fmt: skip
    ok("create", "--name", "y", "--type", "queue", "--image", "i", "--cpu", "cpu5g:4")
    gpu_body, cpu_body = api.bodies("POST")
    assert gpu_body["gpu"] == {
        "pools": ["ADA_24"],
        "allowedCudaVersions": ["12.4", "12.8"],
    }
    assert cpu_body["cpu"] == [{"id": "cpu5g", "vcpuCount": 4}]


@pytest.mark.parametrize(
    "args, message",
    [
        (["--name", "x", "--type", "queue"], "pass --image or --template"),
        (["--name", "x", "--type", "queue", "--image", "i", "--gpu", "h100", "--cpu", "cpu3c"],
         "either --gpu or --cpu"),
        (["--name", "x", "--type", "queue", "--image", "i", "--gpu", "h100:2", "--gpu-count", "1"],
         "conflicting gpu counts"),
        (["--name", "x", "--type", "load-balancer", "--image", "i"], "--requests-per-worker"),
        (["--name", "x", "--type", "load-balancer", "--image", "i", "--queue-delay-seconds", "2"],
         "scale on --requests-per-worker"),
        (["--name", "x", "--type", "queue", "--image", "i", "--queue-delay-seconds", "2",
          "--requests-per-worker", "2"], "either --queue-delay-seconds"),
        (["--name", "x", "--type", "queue", "--image", "i", "--env", "NOVALUE"], "KEY=VALUE"),
        (["--name", "x", "--type", "queue", "--image", "i", "--cpu", "cpu3c"], "--vcpu-count"),
        (["--name", "x", "--type", "queue", "--image", "i", "--cpu", "cpu3c:x"], "invalid --cpu"),
        (["--name", "x", "--type", "queue", "--image", "i", "--vcpu-count", "4"], "pass --cpu"),
        (["--name", "x", "--type", "queue", "--image", "i", "--cpu", "cpu3c:4", "--cuda", "12.4"],
         "--cuda applies to GPU"),
        (["--name", "x", "--type", "queue", "--image", "i", "--gpu", "b200"], "unknown gpu"),
        (["--name", "x", "--type", "queue", "--image", "i", "--region", "mars"], "unknown region"),
    ],
)  # fmt: skip
def test_create_rejects_bad_flags(api, args, message):
    assert message in fails("create", *args)
    assert not api.bodies("POST")


# ------------------------------------------------------------------ update


def test_update_merges_env_and_workers(api):
    ok("update", "ep1", "--env", "C=3", "--unset-env", "A", "--max-workers", "8")
    (patch,) = api.bodies("PATCH")
    assert patch == {
        "env": {"B": "2", "C": "3"},
        "workers": {"min": 0, "max": 8, "idleTimeout": 5},
    }


def test_update_gpu_replaces_pools_and_exclusions_together(api):
    ok("update", "ep1", "--gpu", "4090", "--cuda", "any")
    ok("update", "ep1", "--cuda", ">=12.8")
    ok("update", "ep1", "--cuda", "12.8")
    first, second, third = api.bodies("PATCH")
    assert first["gpu"] == {
        "pools": ["ADA_24"],
        "excludedTypes": [],
        "allowedCudaVersions": [],
        "minCudaVersion": "",
    }
    assert second["gpu"] == {"minCudaVersion": "12.8", "allowedCudaVersions": []}
    assert third["gpu"] == {"allowedCudaVersions": ["12.8"], "minCudaVersion": ""}


def test_update_cpu_vcpus_and_mounts(api):
    ok("update", "ep2", "--vcpu-count", "8", "--network-volume", "vol9")
    (patch,) = api.bodies("PATCH")
    assert patch["cpu"] == [{"id": "cpu3c", "vcpuCount": 8}]
    assert patch["mounts"] == {
        "network": [{"volumeId": "vol9"}],
        "global": [{"volumeId": "gv1"}],
    }


@pytest.mark.parametrize(
    "args, message",
    [
        (["ep1"], "nothing to update"),
        (["ep1", "--unset-env", "MISSING"], "no such variable"),
        (["ep1", "--cpu", "cpu3c:4"], "cannot switch to CPUs"),
        (["ep2", "--gpu", "h100"], "cannot switch to GPUs"),
        (["ep2", "--cuda", "12.4"], "--cuda applies to GPU"),
        (["lb1", "--queue-delay-seconds", "3"], "scale on --requests-per-worker"),
    ],
)
def test_update_rejects_bad_flags(api, args, message):
    assert message in fails("update", *args)
    assert not api.bodies("PATCH")


def test_update_json_and_wait_follows_rollout(api):
    states = iter(
        [
            {"endpointVersion": 3},
            {"endpointVersion": 4, "rollout": {"inProgress": True, "workersOnLatest": 1,
                                               "workersTotal": 2}},
            {"endpointVersion": 4, "rollout": {"inProgress": False, "workersOnLatest": 2,
                                               "workersTotal": 2}},
        ]
    )  # fmt: skip
    api.routes[("GET", "/v2/serverless/ep1/releases")] = lambda params, body: next(
        states
    )
    out = ok("update", "ep1", "--image", "img:2", "--wait")
    assert "v4 rolling out, 1/2 workers on latest" in out
    assert "release v4 is live" in out
    out = ok("update", "ep1", "--name", "renamed", "--json")
    assert json.loads(out)["name"] == "renamed"


# -------------------------------------------------------------------- edit


def _doc(**changes):
    doc = ep._mutable(GPU_ENDPOINT)
    doc.update(changes)
    return json.dumps(doc)


def test_mutable_document_holds_update_fields_only():
    doc = ep._mutable(GPU_ENDPOINT)
    assert set(doc) <= set(ep.MUTABLE_FIELDS)
    assert "networkVolumes" not in doc and "id" not in doc and "requestUrls" not in doc
    assert ep._mutable(CPU_ENDPOINT)["cpu"] == [{"id": "cpu3c", "vcpuCount": 4}]


def test_edit_file_shows_patch_and_applies(api, tmp_path):
    path = tmp_path / "doc.json"
    gpu = dict(ep._mutable(GPU_ENDPOINT)["gpu"], excludedTypes=[], minCudaVersion=None)
    path.write_text(
        _doc(image="img:2", workers={"min": 1, "max": 3, "idleTimeout": 5}, gpu=gpu)
    )
    out = ok("edit", "ep1", "--file", str(path), "--yes", "--no-follow")
    (patch,) = api.bodies("PATCH")
    assert patch == {
        "image": "img:2",
        "workers": {"min": 1, "max": 3, "idleTimeout": 5},
        "gpu": {"pools": ["ADA_80_PRO"], "excludedTypes": []},
    }
    assert '"image": "img:2"' in out
    assert "updated" in out


def test_edit_clears_cuda_floor_with_empty_string():
    before = ep._mutable(LB_ENDPOINT)
    after = copy.deepcopy(before)
    after["gpu"]["minCudaVersion"] = None
    after["gpu"]["count"] = 1
    assert ep._patch(before, after) == {"gpu": {"count": 1, "minCudaVersion": ""}}


def test_edit_without_changes(api, tmp_path):
    path = tmp_path / "doc.json"
    path.write_text(_doc())
    assert "no changes" in ok("edit", "ep1", "--file", str(path))
    assert json.loads(ok("edit", "ep1", "--file", str(path), "--json")) == {}


def test_edit_requires_yes_without_a_terminal(api, tmp_path):
    path = tmp_path / "doc.json"
    path.write_text(_doc(name="renamed"))
    assert "pass --yes" in fails("edit", "ep1", "--file", str(path))
    assert not api.bodies("PATCH")


def test_edit_needs_terminal_or_file(api):
    assert "needs an interactive terminal" in fails("edit", "ep1")


@pytest.mark.parametrize(
    "text, message",
    [
        ("{not json", "invalid JSON"),
        ("[]", "must be a JSON object"),
        (json.dumps({"bogus": 1}), "unknown field 'bogus'"),
        (json.dumps({"disk": "big"}), "'disk' must be an integer"),
        (json.dumps({"disk": True}), "'disk' must be an integer"),
        (json.dumps({"registry": 1}), "'registry' must be a string or null"),
        (json.dumps({"workers": {"maximum": 1}}), "unknown field 'workers.maximum'"),
        (json.dumps({"env": {"A": 1}}), "env values must be strings"),
        (json.dumps({"flashboot": "ON"}), "'flashboot' must be one of"),
        (
            json.dumps({"cpu": [{"id": "cpu3c", "vcpuCount": 2}]}),
            "cannot switch to CPUs",
        ),
    ],
)
def test_edit_validation(text, message):
    with pytest.raises(click.ClickException, match=message):
        ep._validate(text, ep._mutable(GPU_ENDPOINT))
    with pytest.raises(click.ClickException, match="cannot switch to GPUs"):
        ep._validate(json.dumps({"gpu": {}}), ep._mutable(CPU_ENDPOINT))


def test_edit_in_editor_reopens_on_error_then_follows(api, monkeypatch):
    monkeypatch.setattr(ep, "interactive", lambda: True)
    monkeypatch.setattr(output, "interactive", lambda: True)
    edits = iter(['{"bogus": 1}', _doc(name="renamed")])
    seen = []

    def fake_edit(text, extension):
        seen.append(text)
        return next(edits)

    monkeypatch.setattr(ep.click, "edit", fake_edit)
    states = iter([{"endpointVersion": 3}, {"endpointVersion": 4, "rollout": {}}])
    api.routes[("GET", "/v2/serverless/ep1/releases")] = lambda params, body: next(
        states
    )
    out = ok("edit", "ep1", input="y\ny\n")
    assert "unknown field 'bogus'" in out
    assert seen[1] == '{"bogus": 1}'
    assert api.bodies("PATCH") == [{"name": "renamed"}]
    assert "release v4 is live" in out


def test_edit_in_editor_gives_up_or_saves_nothing(api, monkeypatch):
    monkeypatch.setattr(ep, "interactive", lambda: True)
    monkeypatch.setattr(ep.click, "edit", lambda text, extension: "{oops")
    assert "invalid JSON" in fails("edit", "ep1", input="n\n")
    monkeypatch.setattr(ep.click, "edit", lambda text, extension: None)
    assert "no changes" in ok("edit", "ep1")


def test_follow_rollout_without_new_release(api):
    path_state = {"endpointVersion": 3, "rollout": {}}
    api.routes[("GET", "/v2/serverless/ep1/releases")] = path_state
    ep._follow_rollout("ep1", 3, timeout=0)


def test_follow_rollout_times_out_and_detaches(api, monkeypatch, capsys):
    state = {"endpointVersion": 4, "rollout": {"inProgress": True}}
    api.routes[("GET", "/v2/serverless/ep1/releases")] = state
    ep._follow_rollout("ep1", 3, timeout=0)
    assert "still in progress" in capsys.readouterr().out

    def interrupt(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(ep.time, "sleep", interrupt)
    ep._follow_rollout("ep1", 3, timeout=60)
    assert "detached" in capsys.readouterr().out


# ------------------------------------------------------------ scale/delete


def test_scale_sends_workers_and_scaler(api):
    out = ok("scale", "ep1", "--min-workers", "1", "--requests-per-worker", "3")
    (patch,) = api.bodies("PATCH")
    assert patch == {
        "workers": {"min": 1, "max": 3, "idleTimeout": 5},
        "scaling": {"type": "REQUEST_COUNT", "requestCount": 3},
    }
    assert "1-3" in out and "3 requests per worker" in out
    assert (
        json.loads(ok("scale", "ep1", "--max-workers", "9", "--json"))["workers"]["max"]
        == 9
    )


def test_scale_rejects_nothing_and_lb_queue_delay(api):
    assert "pass --min-workers" in fails("scale", "ep1")
    assert "requests-per-worker" in fails("scale", "lb1", "--queue-delay-seconds", "2")


def test_delete_confirms(api, monkeypatch):
    assert "pass --yes" in fails("delete", "ep1")
    monkeypatch.setattr(output, "interactive", lambda: True)
    fails("delete", "ep1", input="n\n")
    assert not [c for c in api.calls if c[0] == "DELETE"]
    out = ok("delete", "ep1", input="y\n")
    assert "deleted" in out and "llama-prod" in out
    ok("delete", "ep1", "--yes")
    assert len([c for c in api.calls if c[0] == "DELETE"]) == 2


# ------------------------------------------------------- workers/releases


def test_workers_table_and_json(api):
    out = ok("workers", "ep1")
    assert "3 workers: 1 running, 1 idle, 1 initializing" in out
    for text in ("w1", "w2", "w3", "1h02m", "2m05s", "v2 (stale)", "stale"):
        assert text in out
    assert json.loads(ok("workers", "ep1", "--json"))["summary"]["total"] == 3


def test_workers_empty_and_uptime():
    assert ep._uptime(None) is None
    assert ep._uptime(42) == "42s"
    assert ep._worker_version({"version": None}) is None
    assert "0 workers" in str(ep._workers_view({"summary": {}}))


def test_workers_watch(api, monkeypatch):
    calls = {"n": 0}

    def tick(seconds):
        calls["n"] += 1
        if calls["n"] > 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(ep.time, "sleep", tick)
    out = ok("workers", "ep1", "--watch", "--json")
    lines = [json.loads(line) for line in out.splitlines()]
    assert len(lines) == 2 and lines[0]["endpointVersion"] == 3
    calls["n"] = 0
    out = ok("workers", "ep1", "--watch")
    assert "w1" in out


def test_releases(api):
    out = ok("releases", "ep1")
    assert "v3 live, 2/2 workers on latest" in out
    for text in (
        "rel3",
        "rel2",
        "manual",
        "git build",
        "imageName",
        "2026-07-12 00:00",
    ):
        assert text in out
    assert "template." not in out and "ports" not in out
    data = json.loads(ok("releases", "ep1", "--json"))
    assert data["endpointVersion"] == 3 and len(data["releases"]) == 2


# -------------------------------------------------------------------- logs


class FakeStream:
    def __init__(self, lines, status=200, text=""):
        self.lines = lines
        self.status_code = status
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self, decode_unicode=True):
        for line in self.lines:
            if isinstance(line, Exception):
                raise line
            yield line


def _event(ts, line, source="container"):
    return [
        f"id: {ts}",
        "data: " + json.dumps({"source": source, "line": line, "ts": ts}),
        "",
    ]


@pytest.fixture
def streams(monkeypatch):
    calls = []
    by_worker = {}

    def fake_get(url, params=None, headers=None, stream=None, timeout=None):
        worker = url.split("/workers/")[1].split("/")[0]
        calls.append({"worker": worker, "params": params, "headers": headers})
        return by_worker[worker]

    monkeypatch.setattr(ep.requests, "get", fake_get)
    monkeypatch.setattr(ep, "_resolve_api_key", lambda key: "key")
    by_worker["calls"] = calls
    return by_worker


def test_logs_snapshot_multiplexes_sorted_and_stops_at_live_lines(api, streams):
    streams["w1"] = FakeStream(
        _event("2026-01-01T00:00:02Z", "w1 second")
        + ["data: {broken", ": heartbeat"]
        + _event("2999-01-01T00:00:00Z", "live line")
    )
    streams["w2"] = FakeStream(_event("2026-01-01T00:00:01Z", "w2 first", "system"))
    streams["w3"] = FakeStream([], status=404, text='{"title": "worker gone"}')
    out = ok("logs", "ep1", "--source", "system", "--tail", "200")
    lines = out.splitlines()
    assert lines == ["w2 [system] w2 first", "w1 [container] w1 second"]
    params = streams["calls"][0]["params"]
    assert params == {"tail": 200, "source": "system"}


def test_logs_filters_workers_and_prints_json(api, streams):
    streams["w2"] = FakeStream(_event("2026-01-01T00:00:01Z", "hello"))
    out = ok(
        "logs", "ep1", "--worker", "w2", "--since", "2026-01-01T00:00:00Z", "--json"
    )
    assert json.loads(out) == {
        "worker": "w2",
        "source": "container",
        "line": "hello",
        "ts": "2026-01-01T00:00:01Z",
    }
    assert [call["worker"] for call in streams["calls"]] == ["w2"]
    assert streams["calls"][0]["params"]["since"] == "2026-01-01T00:00:00Z"


def test_logs_without_workers(api):
    api.routes[("GET", "/v2/serverless/ep1/workers")] = {"workers": []}
    assert "no workers" in ok("logs", "ep1")


def test_logs_auth_error_fails(api, streams):
    for worker in ("w1", "w2", "w3"):
        streams[worker] = FakeStream([], status=401)
    assert "Unauthorized" in fails("logs", "ep1")


def test_logs_follow_attaches_new_workers_and_resumes(api, streams, monkeypatch):
    monkeypatch.setattr(ep, "WORKER_POLL_SECONDS", 0)
    listings = iter(
        [
            {"workers": [{"id": "w1"}]},
            {"workers": [{"id": "w1"}, {"id": "w2"}]},
        ]
    )

    def list_workers(params, body):
        try:
            return next(listings)
        except StopIteration:
            raise KeyboardInterrupt from None

    api.routes[("GET", "/v2/serverless/ep1/workers")] = list_workers
    streams["w1"] = FakeStream(_event("2026-01-01T00:00:01Z", "one"))
    streams["w2"] = FakeStream(
        _event("2026-01-01T00:00:02Z", "two") + [requests.exceptions.ReadTimeout()]
    )
    out = invoke("logs", "ep1", "--follow").output
    assert "w1 [container] one" in out


def test_logs_follow_waits_for_workers(api, monkeypatch):
    monkeypatch.setattr(ep, "WORKER_POLL_SECONDS", 0)
    listings = iter([{"workers": []}])

    def list_workers(params, body):
        try:
            return next(listings)
        except StopIteration:
            raise KeyboardInterrupt from None

    api.routes[("GET", "/v2/serverless/ep1/workers")] = list_workers
    assert "waiting for workers" in ok("logs", "ep1", "-f")


def test_log_stream_sends_resume_cursor(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, stream=None, timeout=None):
        seen["headers"] = headers
        return FakeStream(_event("2026-01-01T00:00:01Z", "x"))

    monkeypatch.setattr(ep.requests, "get", fake_get)
    monkeypatch.setattr(ep, "_resolve_api_key", lambda key: "key")
    events = queue.Queue()
    stop = threading.Event()
    ep._log_stream("ep1", "w1", {}, "2026-01-01T00:00:00Z", events, stop, 1)
    assert seen["headers"]["Last-Event-ID"] == "2026-01-01T00:00:00Z"
    assert events.get()[0] == "line" and events.get()[0] == "done"
    stop.set()
    ep._log_stream("ep1", "w1", {}, None, events, stop, 1)
    assert events.get()[0] == "done"


def test_is_live_handles_odd_timestamps():
    from datetime import datetime, timezone

    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert not ep._is_live({"ts": "garbage"}, started)
    assert ep._is_live({"ts": "2026-02-01T00:00:00"}, started)


# --------------------------------------------------------------------- run


def test_run_waits_for_output(api, client):
    client.post.return_value = {"id": "job1", "status": "IN_QUEUE"}
    client.get.side_effect = [
        {"id": "job1", "status": "IN_PROGRESS"},
        {"id": "job1", "status": "COMPLETED", "output": {"text": "hi"}},
    ]
    out = ok("run", "ep1", "--data", '{"prompt": "hello"}', "--webhook", "https://hook",
             "--execution-timeout", "60", "--ttl", "120", "--low-priority")  # fmt: skip
    assert json.loads(out) == {"text": "hi"}
    path, body = client.post.call_args.args
    assert path == "ep1/run"
    assert body == {
        "input": {"prompt": "hello"},
        "webhook": "https://hook",
        "policy": {"executionTimeout": 60000, "ttl": 120000, "lowPriority": True},
    }
    client.get.assert_called_with("ep1/status/job1")


def test_run_reads_input_file_and_stdin(api, client, tmp_path):
    client.post.return_value = {"id": "job1"}
    client.get.return_value = {"id": "job1", "status": "COMPLETED", "output": "plain"}
    path = tmp_path / "request.json"
    path.write_text('{"input": {"a": 1}, "policy": {"ttl": 5}}')
    assert ok("run", "ep1", "--input", str(path)).strip() == "plain"
    assert client.post.call_args.args[1] == {"input": {"a": 1}, "policy": {"ttl": 5}}
    full = json.loads(ok("run", "ep1", "--input", "-", "--json", input="[1, 2]"))
    assert full["output"] == "plain"
    assert client.post.call_args.args[1] == {"input": [1, 2]}


def test_run_async(api, client):
    client.post.return_value = {"id": "job1", "status": "IN_QUEUE"}
    out = ok("run", "ep1", "--data", "{}", "--async")
    assert "job1" in out and "rp endpoint job wait ep1 job1" in out
    assert (
        json.loads(ok("run", "ep1", "--data", "{}", "--async", "--json"))["id"]
        == "job1"
    )
    client.get.assert_not_called()


def test_run_failed_job_exits_nonzero(api, client):
    client.post.return_value = {"id": "job1"}
    client.get.return_value = {"id": "job1", "status": "FAILED", "error": "boom"}
    assert "job job1 failed: boom" in fails("run", "ep1", "--data", "{}")


def test_run_timeout_and_interrupt(api, client, monkeypatch):
    client.post.return_value = {"id": "job1"}
    client.get.return_value = {"id": "job1", "status": "IN_QUEUE"}
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(ep.time, "monotonic", lambda: next(clock))
    assert "resume with: rp endpoint job wait ep1 job1" in fails(
        "run", "ep1", "--data", "{}", "--timeout", "5"
    )
    client.get.side_effect = KeyboardInterrupt
    result = invoke("run", "ep1", "--data", "{}")
    assert result.exit_code != 0


def test_run_stream(api, client):
    client.post.return_value = {"id": "job1"}
    client.get.side_effect = [
        {"status": "IN_PROGRESS", "stream": []},
        {"status": "IN_PROGRESS", "stream": [{"output": "hel"}, {"output": "lo"}]},
        {"status": "COMPLETED", "stream": [{"output": {"done": True}}]},
        {"status": "COMPLETED", "stream": []},
    ]
    out = ok("run", "ep1", "--input", "-", "--stream", input='{"p": 1}')
    assert 'hello\n{"done": true}' in out


def test_run_stream_prints_output_of_non_streaming_handler(api, client):
    client.post.return_value = {"id": "job1"}
    client.get.side_effect = [
        requests.exceptions.ReadTimeout(),
        {"status": "IN_QUEUE", "stream": []},
        {"status": "COMPLETED", "stream": []},
        {"id": "job1", "status": "COMPLETED", "output": "streamed"},
    ]
    assert ok("run", "ep1", "--data", "{}", "--stream").splitlines()[-1] == "streamed"
    client.get.assert_called_with("ep1/status/job1")


def test_run_stream_failure_and_json(api, client):
    client.post.return_value = {"id": "job1"}
    client.get.side_effect = [
        {"status": "FAILED", "stream": [{"output": "partial"}]},
        {"status": "FAILED", "stream": []},
        {"id": "job1", "status": "FAILED", "error": "oom"},
    ]
    assert "failed: oom" in fails("run", "ep1", "--data", "{}", "--stream", "--json")


@pytest.mark.parametrize(
    "args, message",
    [
        (["ep1", "--data", "{}", "--async", "--stream"], "either --async or --stream"),
        (["ep1"], "pass --input FILE or --data JSON"),
        (["ep1", "--data", "{bad"], "not valid JSON"),
        (["ep1", "--data", "{}", "--input", "-"], "either --input or --data"),
        (["ep1", "--data", "{}", "--path", "/x"], "apply to load-balancer"),
        (["lb1", "--data", "{}", "--async"], "job options apply to queue"),
    ],
)
def test_run_rejects_bad_flags(api, client, args, message):
    assert message in fails("run", *args)
    client.post.assert_not_called()


def test_run_load_balancer(api, monkeypatch):
    calls = []

    class Response:
        def __init__(self, status, payload=None, text=""):
            self.status_code = status
            self._payload = payload
            self.text = text

        def json(self):
            if self._payload is None:
                raise ValueError
            return self._payload

    responses = iter(
        [
            Response(200, {"data": [1]}),
            Response(200, text="pong"),
            Response(200, "a string"),
            Response(500, text="kaput"),
        ]
    )

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls.append((method, url, json, timeout))
        return next(responses)

    monkeypatch.setattr(ep.requests, "request", fake_request)
    monkeypatch.setattr(ep, "_resolve_api_key", lambda key: "key")
    assert json.loads(ok("run", "lb1", "--path", "/v1/chat", "--data", '{"m": 1}')) == {
        "data": [1]
    }
    assert ok("run", "lb1", "--path", "ping").strip() == "pong"
    assert ok("run", "lb1", "--method", "put", "--timeout", "5").strip() == "a string"
    assert "returned 500: kaput" in fails("run", "lb1")
    assert calls[0] == ("POST", "https://lb1.api.runpod.ai/v1/chat", {"m": 1}, 600)
    assert calls[1][:2] == ("GET", "https://lb1.api.runpod.ai/ping")
    assert calls[2][0] == "PUT" and calls[2][3] == 5.0


# -------------------------------------------------------------------- jobs


def test_job_get(api, client):
    client.get.return_value = {
        "id": "job1",
        "status": "COMPLETED",
        "delayTime": 1500,
        "executionTime": 2000,
        "workerId": "w1",
        "output": {"a": 1},
    }
    out = ok("job", "get", "ep1", "job1")
    assert "completed" in out and "1.5s" in out and '{"a": 1}' in out
    client.get.assert_called_with("ep1/status/job1")
    assert json.loads(ok("job", "get", "ep1", "job1", "--json"))["workerId"] == "w1"


def test_job_wait_and_stream(api, client):
    client.get.return_value = {"id": "job1", "status": "COMPLETED", "output": "done"}
    assert ok("job", "wait", "ep1", "job1", "--timeout", "30").strip() == "done"
    client.get.return_value = None
    client.get.side_effect = [
        {"status": "COMPLETED", "stream": [{"output": "chunk"}]},
        {"status": "COMPLETED", "stream": []},
    ]
    assert ok("job", "stream", "ep1", "job1").strip() == "chunk"
    client.get.assert_called_with("ep1/stream/job1", timeout=ep.STREAM_POLL_SECONDS)


def test_job_stream_interrupt(api, client):
    client.get.side_effect = KeyboardInterrupt
    result = invoke("job", "stream", "ep1", "job1")
    assert result.exit_code != 0


def test_job_cancel_and_retry(api, client):
    client.post.return_value = {"id": "job1", "status": "CANCELLED"}
    assert "CANCELLED" in ok("job", "cancel", "ep1", "job1")
    client.post.assert_called_with("ep1/cancel/job1", None)
    assert (
        json.loads(ok("job", "cancel", "ep1", "job1", "--json"))["status"]
        == "CANCELLED"
    )
    client.post.return_value = {"id": "job1", "status": "IN_QUEUE"}
    assert "IN_QUEUE" in ok("job", "retry", "ep1", "job1")
    client.post.assert_called_with("ep1/retry/job1", None)
    assert json.loads(ok("job", "retry", "ep1", "job1", "--json"))["id"] == "job1"


def test_purge(api, client):
    client.post.return_value = {"removed": 3, "status": "completed"}
    assert "pass --yes" in fails("purge", "ep1")
    assert "purged 3 queued jobs" in ok("purge", "ep1", "--yes")
    client.post.assert_called_with("ep1/purge-queue", None)
    assert json.loads(ok("purge", "ep1", "--yes", "--json"))["removed"] == 3


def test_data_plane_errors(api, client, monkeypatch):
    response = requests.Response()
    response.status_code = 404
    response._content = b"job not found"
    client.get.side_effect = requests.HTTPError(response=response)
    assert "404 job not found" in fails("job", "get", "ep1", "job1")
    client.get.side_effect = requests.HTTPError("no response")
    assert "no response" in fails("job", "get", "ep1", "job1")

    def no_key():
        raise RuntimeError("api key not set")

    monkeypatch.setattr(runner, "RunPodClient", no_key)
    assert "api key not set" in fails("job", "get", "ep1", "job1")


# -------------------------------------------------------------- completion


def test_completion_inserts_ids(api):
    ctx = click.Context(ep.list_workers, info_name="workers")
    items = ep.complete_endpoint(ctx, None, "ep")
    assert [item.value for item in items] == ["ep1", "ep2"]
    assert items[0].help == "llama-prod"
    ctx.params["endpoint_id"] = "ep1"
    assert [item.value for item in ep.complete_worker(ctx, None, "w")] == [
        "w1",
        "w2",
        "w3",
    ]
    assert ep.complete_worker(click.Context(ep.list_workers), None, "") == []
    assert [item.value for item in ep.complete_datacenter(ctx, None, "EU")] == [
        "EU-RO-1",
        "EU-CZ-1",
    ]
