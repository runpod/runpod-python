"""tests for rp pod."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from runpod import error
from runpod.rp_cli import pod as pod_module
from runpod.rp_cli.main import cli

GPUS = [
    {
        "id": "NVIDIA H100 80GB HBM3",
        "name": "H100 SXM",
        "price": {"secure": 3.99, "community": 2.69},
        "maxCount": {"secure": 8, "community": 1},
        "secure": True,
        "community": True,
        "availability": "LOW",
        "dataCenters": [
            {"id": "US-TX-3", "availability": "LOW"},
            {"id": "EU-RO-1", "availability": "LOW"},
        ],
    },
    {
        "id": "NVIDIA H100 NVL",
        "name": "H100 NVL",
        "price": {"secure": 3.19, "community": 2.59},
        "maxCount": {"secure": 1, "community": 10},
        "secure": True,
        "community": True,
        "availability": "HIGH",
        "dataCenters": [{"id": "US-TX-3", "availability": "HIGH"}],
    },
    {
        "id": "NVIDIA H100 PCIe",
        "name": "H100 PCIe",
        "price": {"secure": 2.39, "community": 1.99},
        "maxCount": {"secure": 8, "community": 8},
        "secure": True,
        "community": True,
        "availability": "LOW",
        "dataCenters": [{"id": "US-TX-3", "availability": "LOW"}],
    },
    {
        "id": "NVIDIA GeForce RTX 4090",
        "name": "RTX 4090",
        "price": {"secure": 0.69, "community": 0.34},
        "maxCount": {"secure": 8, "community": 8},
        "secure": True,
        "community": True,
        "availability": "NONE",
    },
]

CPUS = [
    {
        "id": "cpu3c",
        "name": "Compute-Optimized",
        "group": "CPU3",
        "vcpu": {"min": 2, "max": 32},
    },
    {
        "id": "cpu5g",
        "name": "General Purpose",
        "group": "CPU5",
        "vcpu": {"min": 4, "max": 32},
    },
]

DATACENTERS = [
    {"id": "EU-RO-1", "region": "EUROPE"},
    {"id": "EU-CZ-1", "region": "EUROPE"},
    {"id": "US-TX-3", "region": "NORTH_AMERICA"},
]

POD = {
    "id": "pod123",
    "name": "trainer",
    "status": "RUNNING",
    "actions": ["stop", "restart", "terminate"],
    "image": "runpod/pytorch:latest",
    "args": "",
    "disk": 50,
    "ports": ["8888/http", "22/tcp"],
    "env": {"HF_TOKEN": "secret", "MODEL": "llama"},
    "mounts": {"network": [{"volumeId": "vol1", "path": "/data"}]},
    "gpu": {"id": "NVIDIA H100 80GB HBM3", "count": 2, "vcpuCount": 32, "memory": 250},
    "cloud": "SECURE",
    "dataCenterId": "EU-RO-1",
    "cudaVersion": "12.8",
    "cost": 7.98,
    "locked": False,
    "globalNetworking": {"enabled": True, "internalDns": "pod123.runpod.internal"},
    "ssh": {
        "proxy": {"command": "ssh pod123-abc@ssh.runpod.io"},
        "direct": {"command": "ssh root@1.2.3.4 -p 40022"},
    },
    "runtime": {
        "ports": [
            {"private": 22, "public": 40022, "type": "tcp", "ip": "1.2.3.4"},
            {"private": 8888, "public": None, "type": "http", "ip": None},
        ],
        "uptime": 3725,
    },
    "createdAt": "2026-10-19T20:00:00Z",
    "startedAt": "2026-10-19T20:01:00Z",
}

STOPPED = {
    "id": "pod456",
    "name": "box",
    "status": "EXITED",
    "actions": ["start", "terminate"],
    "image": "ubuntu:24.04",
    "cpu": {"id": "cpu3c", "vcpuCount": 4, "memory": 8},
    "cost": 0,
}


class FakeApi:
    """routes rest calls by method and path, recording each one."""

    def __init__(self, routes=None):
        self.calls = []
        self.routes = {
            ("GET", "/v2/pods"): {"pods": [POD, STOPPED]},
            ("GET", "/v2/pods/pod123"): POD,
            ("GET", "/v2/account/ssh-keys"): {"keys": ["ssh-ed25519 AAAA me"]},
            ("GET", "/v2/registries"): {"registries": [{"id": "reg1", "name": "ghcr"}]},
            ("GET", "/v2/network-volumes"): {
                "networkVolumes": [
                    {"id": "vol1", "name": "data", "dataCenter": "EU-RO-1"}
                ]
            },
            ("GET", "/v2/network-volumes/vol1"): {
                "id": "vol1",
                "dataCenter": "EU-RO-1",
            },
            ("POST", "/v2/pods"): {**POD, "status": "PROVISIONING"},
            ("PATCH", "/v2/pods/pod123"): POD,
            ("POST", "/v2/pods/pod123/action"): POD,
            ("POST", "/v2/pods/pod456/action"): {**STOPPED, "status": "STARTING"},
            ("DELETE", "/v2/pods/pod123"): None,
            **(routes or {}),
        }

    def __call__(self, method, path, params=None, json=None):
        self.calls.append((method, path, params, json))
        response = self.routes[(method, path)]
        if isinstance(response, list):
            return response.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, method, path):
        return next(call[3] for call in self.calls if call[:2] == (method, path))

    def made(self, method, path):
        return any(call[:2] == (method, path) for call in self.calls)


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    from runpod.rp_cli import console as ui

    monkeypatch.setattr(ui.console, "_width", 200)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(pod_module.time, "sleep", lambda seconds: None)


def _invoke(args, api=None, gpus=GPUS, interactive=False, input=None):
    api = api or FakeApi()
    with (
        patch("runpod.rp_cli.pod.run_rest_request", api),
        patch("runpod.rp_cli.compute.run_rest_request", api),
        patch("runpod.rp_cli.compute.list_gpus", return_value=gpus) as list_gpus,
        patch("runpod.rp_cli.compute.list_cpus", return_value=CPUS) as list_cpus,
        patch("runpod.rp_cli.compute.list_datacenters", return_value=DATACENTERS),
        patch("runpod.rp_cli.pod.interactive", return_value=interactive),
        patch("runpod.rp_cli.output.interactive", return_value=interactive),
    ):
        result = CliRunner().invoke(cli, ["pod", *args], input=input)
    result.api, result.list_gpus, result.list_cpus = api, list_gpus, list_cpus
    return result


def _create(args, api=None, gpus=GPUS):
    result = _invoke(["create", *args], api=api, gpus=gpus)
    assert result.exit_code == 0, result.output
    return result, result.api.body("POST", "/v2/pods")


class TestList:
    def test_table(self):
        result = _invoke(["list"])
        assert result.exit_code == 0, result.output
        assert "pod123" in result.output and "pod456" in result.output
        assert "2x NVIDIA H100 80GB HBM3" in result.output
        assert "cpu3c 4 vcpu" in result.output
        assert "$7.98/hr" in result.output
        assert result.api.calls[0][2] == {}

    def test_filters(self):
        result = _invoke(["list", "--name", "BO", "--status", "exited", "--json"])
        assert [p["id"] for p in json.loads(result.stdout)] == ["pod456"]
        result = _invoke(["list", "--status", "running", "--json"])
        assert [p["id"] for p in json.loads(result.stdout)] == ["pod123"]

    def test_cluster_pods_and_empty(self):
        api = FakeApi({("GET", "/v2/pods"): {"pods": []}})
        result = _invoke(["list", "--include-cluster-pods"], api=api)
        assert "no pods" in result.output
        assert api.calls[0][2] == {"includeClusterPods": "true"}

    def test_api_error_is_clean(self):
        api = FakeApi({("GET", "/v2/pods"): error.AuthenticationError("Unauthorized")})
        result = _invoke(["list"], api=api)
        assert result.exit_code == 1
        assert "Error: Unauthorized" in result.output


class TestGet:
    def test_fields_and_connection_info(self):
        result = _invoke(["get", "pod123"])
        assert result.exit_code == 0, result.output
        output = result.output
        assert "ssh pod123-abc@ssh.runpod.io" in output
        assert "ssh root@1.2.3.4 -p 40022" in output
        assert "https://pod123-8888.proxy.runpod.net" in output
        assert "1.2.3.4:40022 -> 22" in output
        assert "network vol1 at /data" in output
        assert "pod123.runpod.internal" in output
        assert "32 vcpu, 250GB ram" in output
        assert "HF_TOKEN, MODEL" in output
        assert "secret" not in output
        assert "1h 2m" in output

    @pytest.mark.parametrize(
        "runtime, status, uptime",
        [(None, "starting", None), ({"uptime": 0}, "starting", None)]
        + [({"uptime": 65}, "running", "1m 5s"), ({"uptime": 9}, "running", "9s")],
    )
    def test_status_follows_container(self, runtime, status, uptime):
        api = FakeApi({("GET", "/v2/pods/pod123"): {**POD, "runtime": runtime}})
        result = _invoke(["get", "pod123"], api=api)
        assert f"status          {status}" in result.output
        if uptime:
            assert f"uptime          {uptime}" in result.output

    def test_other_shapes(self):
        pod = {
            **STOPPED,
            "mounts": {
                "persistent": {"size": 20, "path": "/workspace"},
                "global": [{"volumeId": "gv1"}],
            },
            "globalNetworking": {"enabled": False},
        }
        api = FakeApi({("GET", "/v2/pods/pod456"): pod})
        result = _invoke(["get", "pod456"], api=api)
        assert "persistent 20GB at /workspace" in result.output
        assert "global gv1 at -" in result.output
        assert "cpu3c 4 vcpu" in result.output

    def test_json(self):
        result = _invoke(["get", "pod123", "--json"])
        assert json.loads(result.stdout)["env"]["HF_TOKEN"] == "secret"

    def test_missing_id_without_tty(self):
        result = _invoke(["get"])
        assert result.exit_code == 2
        assert "pass a POD_ID" in result.output

    def test_picker(self):
        with patch("InquirerPy.inquirer.fuzzy") as fuzzy:
            fuzzy.return_value.execute.return_value = "pod123"
            result = _invoke(["get"], interactive=True)
        assert result.exit_code == 0, result.output
        choices = fuzzy.call_args.kwargs["choices"]
        assert [choice.value for choice in choices] == ["pod123", "pod456"]
        assert choices[0].name == "trainer  running"


class TestCreateGpu:
    def test_ticket_example(self):
        result, body = _create(
            [
                "trainer",
                "--gpu",
                "h100:2",
                "--image",
                "runpod/pytorch:latest",
                "--region",
                "eu",
                "--ssh",
            ]
        )
        assert body == {
            "name": "trainer",
            "image": "runpod/pytorch:latest",
            "dataCenterIds": ["EU-RO-1", "EU-CZ-1"],
            "gpu": {"id": "NVIDIA H100 80GB HBM3", "count": 2},
            "startSsh": True,
        }
        assert "'h100' matches 3 gpu types; using H100 SXM" in result.output
        assert "low stock, $7.98/hr" in result.output
        assert "no ssh keys" not in result.output
        assert "created pod trainer (pod123)" in result.output
        assert result.list_gpus.call_args.kwargs == {
            "product": "POD",
            "count": 2,
            "cloud": "SECURE",
            "cuda_versions": [],
            "min_cuda_version": None,
        }

    def test_prefers_stock_then_price(self):
        _, body = _create(["a", "--gpu", "h100", "--image", "x"])
        assert body["gpu"]["id"] == "NVIDIA H100 NVL"
        _, body = _create(["a", "--gpu", "h100", "--gpu-count", "2", "--image", "x"])
        assert body["gpu"]["id"] == "NVIDIA H100 PCIe"

    def test_stock_in_requested_data_centers(self):
        _, body = _create(
            ["a", "--gpu", "h100", "--data-center", "EU-RO-1", "--image", "x"]
        )
        assert body["gpu"]["id"] == "NVIDIA H100 80GB HBM3"
        assert body["dataCenterIds"] == ["EU-RO-1"]

    def test_unique_match_used_directly(self):
        result, body = _create(["a", "--gpu", "4090", "--image", "x", "--json"])
        assert body["gpu"] == {"id": "NVIDIA GeForce RTX 4090", "count": 1}
        assert "matches" not in result.output
        assert "no secure stock for 1x RTX 4090" in result.stderr
        assert json.loads(result.stdout)["id"] == "pod123"

    def test_gpu_settings(self):
        _, body = _create(
            [
                "a",
                "--gpu",
                "h100 nvl",
                "--cloud",
                "COMMUNITY",
                "--gpu-count",
                "4",
                "--cuda",
                ">=12.4",
                "--min-ram-per-gpu",
                "32",
                "--min-vcpu-per-gpu",
                "8",
                "--image",
                "x",
            ]
        )
        assert body["cloud"] == "COMMUNITY"
        assert body["gpu"] == {
            "id": "NVIDIA H100 NVL",
            "count": 4,
            "minCudaVersion": "12.4",
            "minRamPerGpu": 32,
            "minVcpuCountPerGpu": 8,
        }

    def test_exact_cuda(self):
        result, body = _create(
            ["a", "--gpu", "4090", "--cuda", "12.4,12.8", "--image", "x"]
        )
        assert body["gpu"]["allowedCudaVersions"] == ["12.4", "12.8"]
        assert result.list_gpus.call_args.kwargs["cuda_versions"] == ["12.4", "12.8"]

    def test_no_type_offers_count(self):
        result = _invoke(["create", "a", "--gpu", "h100:9", "--image", "x"])
        assert result.exit_code == 1
        assert "offers 9 per pod on secure cloud" in result.output

    def test_count_conflict(self):
        result = _invoke(
            ["create", "a", "--gpu", "h100:2", "--gpu-count", "3", "--image", "x"]
        )
        assert result.exit_code == 2
        assert "conflicts" in result.output

    def test_unknown_gpu(self):
        result = _invoke(["create", "a", "--gpu", "b9000", "--image", "x"])
        assert result.exit_code == 1
        assert "unknown gpu 'b9000'" in result.output

    def test_ssh_without_keys_warns(self):
        api = FakeApi({("GET", "/v2/account/ssh-keys"): {"keys": []}})
        result, body = _create(["a", "--gpu", "4090", "--image", "x", "--ssh"], api=api)
        assert body["startSsh"] is True
        assert "no ssh keys" in result.output


class TestCreateCpu:
    def test_default_vcpu(self):
        result, body = _create(["box", "--cpu", "cpu5g", "--image", "ubuntu"])
        assert body["cpu"] == {"id": "cpu5g", "vcpuCount": 4}
        assert result.list_cpus.call_args.kwargs == {
            "product": "POD",
            "vcpu_count": None,
        }

    def test_explicit_vcpu(self):
        _, body = _create(["box", "--cpu", "cpu3c", "--vcpu", "8", "--image", "ubuntu"])
        assert body["cpu"] == {"id": "cpu3c", "vcpuCount": 8}


class TestCreateContainer:
    def test_every_container_field(self):
        _, body = _create(
            [
                "a",
                "--gpu",
                "4090",
                "--template",
                "tpl1",
                "--image",
                "img",
                "-e",
                "A=1",
                "--env",
                "B=x=y",
                "-p",
                "8888/http",
                "--port",
                "22/tcp",
                "--disk",
                "40",
                "--args",
                "bash start.sh",
                "--cmd",
                "python -m train --lr '1e-4'",
                "--entrypoint",
                "/bin/sh -c",
                "--registry",
                "ghcr",
                "--global-networking",
                "--jupyter",
                "--persistent-volume",
                "100",
            ]
        )
        assert body == {
            "name": "a",
            "image": "img",
            "templateId": "tpl1",
            "args": "bash start.sh",
            "cmd": ["python", "-m", "train", "--lr", "1e-4"],
            "entrypoint": ["/bin/sh", "-c"],
            "disk": 40,
            "ports": ["8888/http", "22/tcp"],
            "registry": "reg1",
            "globalNetworking": True,
            "env": {"A": "1", "B": "x=y"},
            "mounts": {"persistent": {"size": 100, "path": "/workspace"}},
            "gpu": {"id": "NVIDIA GeForce RTX 4090", "count": 1},
            "startJupyter": True,
        }

    def test_network_volume_sets_its_data_center(self):
        _, body = _create(
            [
                "a",
                "--gpu",
                "h100",
                "--image",
                "x",
                "--network-volume",
                "vol1",
                "--global-volume",
                "gv1:/g",
            ]
        )
        assert body["mounts"] == {
            "network": [{"volumeId": "vol1", "path": "/workspace"}],
            "global": [{"volumeId": "gv1", "path": "/g"}],
        }
        assert body["dataCenterIds"] == ["EU-RO-1"]
        assert body["gpu"]["id"] == "NVIDIA H100 80GB HBM3"

    def test_regions_and_data_centers_merge(self):
        _, body = _create(
            [
                "a",
                "--gpu",
                "4090",
                "--image",
                "x",
                "--region",
                "eu-ro",
                "--data-center",
                "EU-RO-1",
                "--data-center",
                "US-TX-3",
                "--network-volume",
                "vol1:/data",
            ]
        )
        assert body["dataCenterIds"] == ["EU-RO-1", "US-TX-3"]
        assert body["mounts"]["network"] == [{"volumeId": "vol1", "path": "/data"}]

    def test_template_without_image(self):
        _, body = _create(["a", "--gpu", "4090", "--template", "tpl1"])
        assert body["templateId"] == "tpl1"
        assert "image" not in body

    @pytest.mark.parametrize(
        "args, message",
        [
            (["--env", "NOPE"], "is not KEY=VALUE"),
            (["--persistent-volume", "big"], "size is in GB"),
            (["--network-volume", "vol1:"], "VALUE[:PATH]"),
        ],
    )
    def test_bad_values(self, args, message):
        result = _invoke(["create", "a", "--gpu", "4090", "--image", "x", *args])
        assert result.exit_code == 2
        assert message in result.output

    def test_unknown_registry(self):
        result = _invoke(
            ["create", "a", "--gpu", "4090", "--image", "x", "--registry", "nope"]
        )
        assert result.exit_code == 1
        assert "unknown registry credential 'nope'" in result.output


class TestCreateValidation:
    @pytest.mark.parametrize(
        "args, message",
        [
            (
                ["a", "--gpu", "4090", "--cpu", "cpu3c", "--image", "x"],
                "either --gpu or --cpu",
            ),
            (["a", "--image", "x"], "pass --gpu"),
            (
                ["a", "--cpu", "cpu3c", "--cuda", "12.4", "--image", "x"],
                "apply to gpu pods",
            ),
            (["a", "--gpu", "4090", "--vcpu", "4", "--image", "x"], "--vcpu applies"),
            (["a", "--gpu", "4090"], "pass --image or --template"),
            (["--gpu", "4090", "--image", "x"], "pass a NAME"),
        ],
    )
    def test_usage_errors(self, args, message):
        result = _invoke(["create", *args])
        assert result.exit_code == 2
        assert message in result.output
        assert not result.api.made("POST", "/v2/pods")

    def test_prompts_for_name(self):
        result = _invoke(
            ["create", "--gpu", "4090", "--image", "x"],
            interactive=True,
            input="mine\n",
        )
        assert result.exit_code == 0, result.output
        assert result.api.body("POST", "/v2/pods")["name"] == "mine"


class TestWait:
    def test_create_waits_until_running(self):
        api = FakeApi(
            {
                ("GET", "/v2/pods/pod123"): [
                    {**POD, "status": "PROVISIONING"},
                    {**POD, "status": "STARTING"},
                    {**POD, "runtime": None},
                    {**POD, "runtime": {"uptime": 0}},
                    {**POD, "runtime": {"uptime": 1}},
                ]
            }
        )
        result = _invoke(
            ["create", "a", "--gpu", "4090", "--image", "x", "--wait", "--json"],
            api=api,
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["status"] == "RUNNING"
        assert sum(call[:2] == ("GET", "/v2/pods/pod123") for call in api.calls) == 5

    def test_error_status_fails(self):
        api = FakeApi({("GET", "/v2/pods/pod456"): {**STOPPED, "status": "ERROR"}})
        result = _invoke(["start", "pod456", "--wait"], api=api)
        assert result.exit_code == 1
        assert "pod pod456 is error" in result.output

    def test_timeout(self, monkeypatch):
        monkeypatch.setattr(pod_module, "WAIT_TIMEOUT", -1)
        api = FakeApi({("GET", "/v2/pods/pod456"): STOPPED})
        result = _invoke(["start", "pod456", "--wait"], api=api)
        assert result.exit_code == 1
        assert "still exited" in result.output


class TestUpdate:
    def test_nothing_to_update(self):
        result = _invoke(["update", "pod123"])
        assert result.exit_code == 2
        assert "nothing to update" in result.output

    def test_simple_fields_skip_the_read(self):
        result = _invoke(
            [
                "update",
                "pod123",
                "--name",
                "renamed",
                "--lock",
                "--no-global-networking",
                "--image",
                "img:2",
                "--registry",
                "",
                "--port",
                "8000/http",
                "--disk",
                "80",
                "--cmd",
                "",
            ]
        )
        assert result.exit_code == 0, result.output
        assert result.api.body("PATCH", "/v2/pods/pod123") == {
            "image": "img:2",
            "cmd": [],
            "disk": 80,
            "ports": ["8000/http"],
            "registry": None,
            "globalNetworking": False,
            "name": "renamed",
            "locked": True,
        }
        assert not result.api.made("GET", "/v2/pods/pod123")
        assert "updated pod pod123" in result.output

    def test_env_edits_existing(self):
        result = _invoke(
            [
                "update",
                "pod123",
                "-e",
                "MODEL=qwen",
                "-e",
                "NEW=1",
                "--unset-env",
                "HF_TOKEN",
                "--unlock",
            ]
        )
        assert result.exit_code == 0, result.output
        assert result.api.body("PATCH", "/v2/pods/pod123") == {
            "locked": False,
            "env": {"MODEL": "qwen", "NEW": "1"},
        }

    def test_mount_path_defaults_to_current(self):
        result = _invoke(["update", "pod123", "--network-volume", "vol1", "--json"])
        assert result.exit_code == 0, result.output
        assert result.api.body("PATCH", "/v2/pods/pod123") == {
            "mounts": {"network": [{"volumeId": "vol1", "path": "/data"}]}
        }
        assert json.loads(result.stdout)["id"] == "pod123"


class TestActions:
    @pytest.mark.parametrize("action", ["stop", "restart"])
    def test_action(self, action):
        result = _invoke([action, "pod123"])
        assert result.exit_code == 0, result.output
        assert result.api.body("POST", "/v2/pods/pod123/action") == {"action": action}

    def test_start_wait(self):
        api = FakeApi(
            {
                ("GET", "/v2/pods/pod456"): [
                    {**STOPPED, "status": "STARTING"},
                    {**STOPPED, "status": "RUNNING", "runtime": {"uptime": 46}},
                    {**STOPPED, "status": "RUNNING", "runtime": {"uptime": 1}},
                ]
            }
        )
        result = _invoke(["start", "pod456", "--wait", "--json"], api=api)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["runtime"] == {"uptime": 1}
        assert "started pod pod456" in result.stderr

    def test_picker_offers_pods_that_allow_the_action(self):
        with patch("InquirerPy.inquirer.fuzzy") as fuzzy:
            fuzzy.return_value.execute.return_value = "pod456"
            result = _invoke(["start"], interactive=True)
        assert result.exit_code == 0, result.output
        assert [c.value for c in fuzzy.call_args.kwargs["choices"]] == ["pod456"]

    def test_api_error(self):
        api = FakeApi(
            {
                ("POST", "/v2/pods/pod123/action"): error.QueryError(
                    "pod is locked", "POST"
                )
            }
        )
        result = _invoke(["stop", "pod123"], api=api)
        assert result.exit_code == 1
        assert "pod is locked" in result.output


class TestTerminate:
    @pytest.mark.parametrize("command", ["terminate", "delete"])
    def test_with_yes(self, command):
        result = _invoke([command, "pod123", "--yes"])
        assert result.exit_code == 0, result.output
        assert result.api.made("DELETE", "/v2/pods/pod123")
        assert "terminated pod pod123" in result.output

    def test_needs_confirmation(self):
        result = _invoke(["terminate", "pod123"])
        assert result.exit_code == 2
        assert "--yes" in result.output
        assert not result.api.made("DELETE", "/v2/pods/pod123")

    def test_interactive_confirm(self):
        result = _invoke(["terminate", "pod123"], interactive=True, input="n\n")
        assert result.exit_code == 1
        assert not result.api.made("DELETE", "/v2/pods/pod123")


class TestConnect:
    def test_launches_terminal(self):
        ssh = MagicMock()
        with patch(
            "runpod.cli.utils.ssh_cmd.SSHConnection", return_value=ssh
        ) as connection:
            result = _invoke(["connect", "pod123"])
        assert result.exit_code == 0, result.output
        connection.assert_called_once_with("pod123")
        ssh.launch_terminal.assert_called_once()

    def test_failure_is_clean(self):
        with patch(
            "runpod.cli.utils.ssh_cmd.SSHConnection", side_effect=TimeoutError("no ssh")
        ):
            result = _invoke(["connect", "pod123"])
        assert result.exit_code == 1
        assert "Error: no ssh" in result.output

    def test_sync_is_mounted(self):
        result = _invoke(["sync", "--help"])
        assert result.exit_code == 0


class TestCompletion:
    def test_completers(self):
        api = FakeApi()
        with (
            patch("runpod.rp_cli.pod.run_rest_request", api),
            patch("runpod.rp_cli.compute.run_rest_request", api),
            patch("runpod.rp_cli.compute.list_datacenters", return_value=DATACENTERS),
        ):
            pods = pod_module._complete_pods(None, None, "pod1")
            datacenters = pod_module._complete_datacenters(None, None, "EU")
            volumes = pod_module._complete_network_volumes(None, None, "")
            registries = pod_module._complete_registries(None, None, "g")
        assert [(item.value, item.help) for item in pods] == [
            ("pod123", "trainer  running")
        ]
        assert [item.value for item in datacenters] == ["EU-RO-1", "EU-CZ-1"]
        assert [(item.value, item.help) for item in volumes] == [
            ("vol1", "data EU-RO-1")
        ]
        assert [item.value for item in registries] == ["ghcr"]
