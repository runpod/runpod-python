"""tests for rp catalog and rp completion."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from runpod import error
from runpod.rp_cli.main import cli

GPUS = [
    {
        "id": "NVIDIA H100 80GB HBM3",
        "name": "H100 SXM",
        "pool": "ADA_80_PRO",
        "memory": 80,
        "price": {"secure": 3.99, "community": 2.69, "serverless": 4.79},
        "maxCount": {"secure": 8, "community": 1},
        "availability": "LOW",
        "dataCenters": [
            {"id": "US-TX-3", "availability": "LOW"},
            {"id": "EU-RO-1", "availability": "LOW"},
        ],
    },
    {
        "id": "NVIDIA H100 NVL",
        "name": "H100 NVL",
        "pool": "ADA_80_PRO",
        "memory": 94,
        "price": {"secure": 3.19, "community": 0},
        "maxCount": {"secure": 1, "community": 10},
        "availability": "HIGH",
        "dataCenters": [{"id": "US-TX-3", "availability": "HIGH"}],
    },
    {
        "id": "NVIDIA GeForce RTX 3070",
        "name": "RTX 3070",
        "pool": None,
        "memory": 8,
        "price": {"secure": 0, "community": 0.13},
        "maxCount": {"secure": 0, "community": 7},
        "availability": "NONE",
        "dataCenters": [],
    },
]

CPUS = [
    {
        "id": "cpu3c",
        "name": "Compute-Optimized",
        "group": "CPU3",
        "vcpu": {"min": 2, "max": 32},
        "ramGbPerVcpu": 2,
        "price": {"securePerVcpu": 0.03, "serverlessPerVcpu": 0.036},
        "availability": "HIGH",
        "dataCenters": [{"id": "EU-RO-1", "availability": "HIGH"}],
    },
    {
        "id": "cpu5c",
        "name": "Compute-Optimized",
        "group": "CPU5",
        "vcpu": {"min": 2, "max": 32},
        "ramGbPerVcpu": 2,
        "price": {"securePerVcpu": 0.04},
        "availability": "NONE",
        "dataCenters": [],
    },
]

DATACENTERS = [
    {
        "id": "EU-RO-1",
        "region": "EUROPE",
        "networkVolumeTypes": ["STANDARD"],
        "globalNetwork": True,
        "compliance": ["GDPR"],
    },
    {
        "id": "US-TX-3",
        "region": "NORTH_AMERICA",
        "networkVolumeTypes": [],
        "globalNetwork": False,
        "compliance": [],
    },
]


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    from runpod.rp_cli import console as ui

    monkeypatch.setattr(ui.console, "_width", 200)


def _invoke(args, gpus=GPUS, cpus=CPUS, datacenters=DATACENTERS):
    with (
        patch("runpod.rp_cli.compute.list_gpus", return_value=gpus) as list_gpus,
        patch("runpod.rp_cli.compute.list_cpus", return_value=cpus) as list_cpus,
        patch("runpod.rp_cli.compute.list_datacenters", return_value=datacenters),
    ):
        result = CliRunner().invoke(cli, ["catalog", *args])
    return result, list_gpus, list_cpus


class TestGpuCatalog:
    @pytest.mark.parametrize(
        "cloud, expected", [(None, 10), ("secure", 1), ("community", 10)]
    )
    def test_max_count_matches_cloud(self, cloud, expected):
        from runpod.rp_cli.catalog import _gpu_columns

        columns = dict(_gpu_columns("POD", 1, cloud))
        assert columns["MAX"](GPUS[1]) == expected

    def test_cpu_community_rejected_before_fetch(self):
        result, list_gpus, list_cpus = _invoke(["--cpu", "--cloud", "community"])
        assert result.exit_code == 2
        assert "secure cloud" in result.output
        list_gpus.assert_not_called()
        list_cpus.assert_not_called()

    @pytest.mark.parametrize("floors", [("12.4", "12.10"), ("12.10", "12.4")])
    def test_strongest_cuda_floor_is_sent(self, floors):
        result, list_gpus, _ = _invoke(
            ["--cuda", ">=" + floors[0], "--cuda", ">=" + floors[1]]
        )
        assert result.exit_code == 0, result.output
        assert list_gpus.call_args.kwargs["min_cuda_version"] == "12.10"

    def test_unavailable_gpu_omits_stock_fields(self):
        gpu = {
            key: value
            for key, value in GPUS[0].items()
            if key not in ("dataCenters", "availability")
        }
        result, _, _ = _invoke(["--region", "eu", "--available", "--json"], gpus=[gpu])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []

    def test_pod_table_sorted_by_stock_then_price(self):
        result, list_gpus, _ = _invoke(["h100", "--count", "2"])
        assert result.exit_code == 0, result.output
        assert "SECURE $/HR x2" in result.output
        assert result.output.index("H100 NVL") < result.output.index("H100 SXM")
        assert "$7.98" in result.output
        assert "US-TX-3 low, EU-RO-1 low" in result.output
        assert list_gpus.call_args.kwargs == {
            "product": "POD",
            "count": 2,
            "cloud": None,
            "cuda_versions": [],
            "min_cuda_version": None,
        }

    def test_region_narrows_stock(self):
        result, _, _ = _invoke(["h100", "--region", "eu", "--json"])
        rows = json.loads(result.output)
        assert [row["availability"] for row in rows] == ["LOW", "NONE"]
        assert rows[0]["dataCenters"] == [{"id": "EU-RO-1", "availability": "LOW"}]
        assert rows[1]["dataCenters"] == []

    def test_cuda_and_cloud_filters(self):
        result, list_gpus, _ = _invoke(
            ["--cloud", "COMMUNITY", "--cuda", ">=12.4", "--max-price", "1"]
        )
        assert result.exit_code == 0, result.output
        assert list_gpus.call_args.kwargs["cloud"] == "COMMUNITY"
        assert list_gpus.call_args.kwargs["min_cuda_version"] == "12.4"
        assert "COMMUNITY $/HR" in result.output
        assert "SECURE" not in result.output
        assert "RTX 3070" in result.output
        assert "H100" not in result.output

    def test_available_and_vram_filters(self):
        result, _, _ = _invoke(["--available", "--min-vram", "90", "--json"])
        assert [row["id"] for row in json.loads(result.output)] == ["NVIDIA H100 NVL"]

    def test_json_totals(self):
        result, _, _ = _invoke(["h100 sxm", "--count", "2", "--json"])
        (row,) = json.loads(result.output)
        assert row["count"] == 2
        assert row["totalPrice"] == {
            "secure": 7.98,
            "community": 5.38,
            "serverless": 9.58,
        }

    def test_serverless_shows_pools_only(self):
        result, list_gpus, _ = _invoke(["--serverless"])
        assert result.exit_code == 0, result.output
        assert list_gpus.call_args.kwargs["product"] == "SERVERLESS"
        assert "ADA_80_PRO" in result.output
        assert "$4.79" in result.output
        assert "RTX 3070" not in result.output

    def test_unknown_gpu(self):
        result, _, _ = _invoke(["b9000"])
        assert result.exit_code == 1
        assert "unknown gpu 'b9000'" in result.output

    def test_empty(self):
        result, _, _ = _invoke(["--max-price", "0.01"])
        assert "no gpus match" in result.output

    def test_conflicting_flags(self):
        assert _invoke(["--cpu", "--datacenters"])[0].exit_code == 2
        assert _invoke(["--serverless", "--cloud", "secure"])[0].exit_code == 2

    def test_api_error_is_clean(self):
        with patch(
            "runpod.rp_cli.compute.list_gpus",
            side_effect=error.AuthenticationError("Unauthorized request"),
        ):
            result = CliRunner().invoke(cli, ["catalog"])
        assert result.exit_code == 1
        assert "Error: Unauthorized request" in result.output


class TestCpuCatalog:
    def test_table(self):
        result, _, list_cpus = _invoke(["--cpu", "--vcpu", "8", "--serverless"])
        assert result.exit_code == 0, result.output
        assert list_cpus.call_args.kwargs == {"product": "SERVERLESS", "vcpu_count": 8}
        assert result.output.index("cpu3c") < result.output.index("cpu5c")
        assert "$0.04" in result.output
        assert "2-32" in result.output

    def test_query_region_and_available(self):
        result, _, _ = _invoke(
            ["cpu3", "--cpu", "--region", "us", "--available", "--json"]
        )
        assert json.loads(result.output) == []

    def test_unknown(self):
        result, _, _ = _invoke(["cpu9", "--cpu"])
        assert "unknown cpu 'cpu9'" in result.output


class TestDatacenterCatalog:
    def test_all(self):
        result, _, _ = _invoke(["--datacenters"])
        assert result.exit_code == 0, result.output
        assert "north america" in result.output
        assert "standard" in result.output

    def test_query_is_a_region(self):
        result, _, _ = _invoke(["--datacenters", "eu", "--json"])
        assert [row["id"] for row in json.loads(result.output)] == ["EU-RO-1"]


class TestCompletion:
    def test_prints_script(self):
        result = CliRunner().invoke(cli, ["completion", "zsh"])
        assert result.exit_code == 0
        assert "_RP_COMPLETE" in result.output
