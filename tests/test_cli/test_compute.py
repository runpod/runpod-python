"""tests for gpu, cpu, and region resolution."""

from unittest.mock import patch

import pytest

from runpod.rp_cli import compute

GPUS = [
    {"id": "NVIDIA GeForce RTX 4090", "name": "RTX 4090", "pool": "ADA_24"},
    {"id": "NVIDIA H100 80GB HBM3", "name": "H100 SXM", "pool": "ADA_80_PRO"},
    {"id": "NVIDIA H100 NVL", "name": "H100 NVL", "pool": "ADA_80_PRO"},
    {"id": "NVIDIA H100 PCIe", "name": "H100 PCIe", "pool": "ADA_80_PRO"},
    {"id": "NVIDIA L4", "name": "L4", "pool": "AMPERE_24"},
    {"id": "NVIDIA L40", "name": "L40", "pool": "ADA_48_PRO"},
    {"id": "NVIDIA A100-SXM4-40GB", "name": "A100 SXM 40GB", "pool": None},
]

DATACENTERS = [
    {"id": "EU-RO-1", "region": "EUROPE"},
    {"id": "EUR-IS-1", "region": "EUROPE"},
    {"id": "US-TX-3", "region": "NORTH_AMERICA"},
    {"id": "US-TX-4", "region": "NORTH_AMERICA"},
    {"id": "CA-MTL-1", "region": "NORTH_AMERICA"},
    {"id": "AP-JP-1", "region": "ASIA"},
]


def _ids(gpus):
    return [gpu["id"] for gpu in gpus]


class TestMatchGpus:
    @pytest.mark.parametrize(
        "query, expected",
        [
            ("4090", ["NVIDIA GeForce RTX 4090"]),
            ("rtx 4090", ["NVIDIA GeForce RTX 4090"]),
            ("rtx4090", ["NVIDIA GeForce RTX 4090"]),
            ("NVIDIA L4", ["NVIDIA L4"]),
            ("l4", ["NVIDIA L4"]),
            ("h100 sxm", ["NVIDIA H100 80GB HBM3"]),
            (
                "ADA_80_PRO",
                ["NVIDIA H100 80GB HBM3", "NVIDIA H100 NVL", "NVIDIA H100 PCIe"],
            ),
            ("a100-sxm4", ["NVIDIA A100-SXM4-40GB"]),
            ("h100", ["NVIDIA H100 80GB HBM3", "NVIDIA H100 NVL", "NVIDIA H100 PCIe"]),
            ("nvidia", []),
            ("mi300x", []),
        ],
    )
    def test_queries(self, query, expected):
        assert _ids(compute.match_gpus(query, GPUS)) == expected

    def test_resolve_raises_with_known_names(self):
        with pytest.raises(
            compute.ResolutionError, match="unknown gpu 'b9000'.*RTX 4090"
        ):
            compute.resolve_gpus("b9000", GPUS)


class TestParsing:
    def test_gpu_spec(self):
        assert compute.parse_gpu_spec("h100:2") == ("h100", 2)
        assert compute.parse_gpu_spec(" 4090 ") == ("4090", None)

    @pytest.mark.parametrize("value", ["h100:0", "h100:x", ":2"])
    def test_invalid_gpu_spec(self, value):
        with pytest.raises(compute.ResolutionError, match="invalid gpu"):
            compute.parse_gpu_spec(value)

    def test_cuda(self):
        assert compute.parse_cuda([">=12.4"]) == ("12.4", [])
        assert compute.parse_cuda(["12.4,12.8", "13.0", ""]) == (
            None,
            ["12.4", "12.8", "13.0"],
        )

    def test_invalid_cuda(self):
        with pytest.raises(compute.ResolutionError, match="invalid cuda"):
            compute.parse_cuda(["twelve"])
        with pytest.raises(compute.ResolutionError, match="not both"):
            compute.parse_cuda([">=12.4", "12.8"])


class TestEndpointSelection:
    def test_excludes_unselected_pool_members(self):
        matches = compute.match_gpus("h100 nvl", GPUS)
        assert compute.endpoint_gpu_selection(matches, GPUS) == (
            ["ADA_80_PRO"],
            ["NVIDIA H100 80GB HBM3", "NVIDIA H100 PCIe"],
        )

    def test_whole_pool(self):
        matches = compute.match_gpus("h100", GPUS)
        assert compute.endpoint_gpu_selection(matches, GPUS) == (["ADA_80_PRO"], [])

    def test_not_on_serverless(self):
        matches = compute.match_gpus("a100-sxm4", GPUS)
        with pytest.raises(compute.ResolutionError, match="not offered on serverless"):
            compute.endpoint_gpu_selection(matches, GPUS)


class TestRegions:
    @pytest.mark.parametrize(
        "values, expected",
        [
            (["eu"], ["EU-RO-1", "EUR-IS-1"]),
            (["Europe"], ["EU-RO-1", "EUR-IS-1"]),
            (["north-america"], ["US-TX-3", "US-TX-4", "CA-MTL-1"]),
            (["us"], ["US-TX-3", "US-TX-4"]),
            (["us-tx"], ["US-TX-3", "US-TX-4"]),
            (["eu_ro_1"], ["EU-RO-1"]),
            (["ap", "EU-RO-1", "eu-ro"], ["AP-JP-1", "EU-RO-1"]),
        ],
    )
    def test_resolution(self, values, expected):
        assert compute.resolve_regions(values, DATACENTERS) == expected

    def test_unknown_region_lists_options(self):
        with pytest.raises(compute.ResolutionError) as exc:
            compute.resolve_regions(["mars"], DATACENTERS)
        assert "north-america" in str(exc.value)
        assert "AP-JP-1" in str(exc.value)


class TestCpus:
    CPUS = [
        {"id": "cpu3c", "name": "Compute-Optimized", "group": "CPU3"},
        {"id": "cpu3g", "name": "General Purpose", "group": "CPU3"},
        {"id": "cpu5c", "name": "Compute-Optimized", "group": "CPU5"},
    ]

    def test_match(self):
        assert [cpu["id"] for cpu in compute.match_cpus("CPU3", self.CPUS)] == [
            "cpu3c",
            "cpu3g",
        ]
        assert [
            cpu["id"] for cpu in compute.match_cpus("general purpose", self.CPUS)
        ] == ["cpu3g"]

    def test_resolve(self):
        assert compute.resolve_cpu("cpu5c", self.CPUS)["id"] == "cpu5c"
        with pytest.raises(compute.ResolutionError, match="ambiguous cpu"):
            compute.resolve_cpu("cpu3", self.CPUS)
        with pytest.raises(compute.ResolutionError, match="unknown cpu"):
            compute.resolve_cpu("cpu9", self.CPUS)


class TestAvailability:
    def test_best(self):
        assert compute.best_availability(["LOW", None, "HIGH", "MEDIUM"]) == "HIGH"
        assert compute.best_availability([]) == "NONE"
        assert compute.availability_rank(None) == 0


class TestCatalogRequests:
    def test_list_all_follows_cursors(self):
        pages = [
            {
                "pods": [{"id": "a"}],
                "pagination": {"hasNextPage": True, "nextCursor": "c1"},
            },
            {
                "pods": [{"id": "b"}],
                "pagination": {"hasNextPage": False, "nextCursor": None},
            },
        ]
        with patch.object(compute, "run_rest_request", side_effect=pages) as request:
            assert compute.list_all("/v2/pods", "pods", {"limit": 1}) == [
                {"id": "a"},
                {"id": "b"},
            ]
        assert request.call_args_list[1].kwargs["params"] == {
            "limit": 1,
            "cursor": "c1",
        }

    def test_list_gpus_with_availability(self):
        with patch.object(
            compute, "run_rest_request", return_value={"gpus": GPUS}
        ) as request:
            assert (
                compute.list_gpus(
                    product="POD",
                    count=2,
                    cloud="SECURE",
                    cuda_versions=["12.4", "12.8"],
                )
                == GPUS
            )
        assert request.call_args.kwargs["params"] == {
            "include": "AVAILABILITY",
            "product": "POD",
            "count": 2,
            "cloud": "SECURE",
            "cudaVersions": "12.4,12.8",
        }

    def test_list_gpus_without_availability(self):
        with patch.object(compute, "run_rest_request", return_value=None) as request:
            assert compute.list_gpus(count=2, min_cuda_version="12.4") == []
        assert request.call_args.kwargs["params"] == {}

    def test_list_cpus_and_datacenters(self):
        with patch.object(
            compute,
            "run_rest_request",
            side_effect=[{"cpus": [{"id": "cpu3c"}]}, {"dataCenters": DATACENTERS}],
        ) as request:
            assert compute.list_cpus(product="SERVERLESS", vcpu_count=4) == [
                {"id": "cpu3c"}
            ]
            assert compute.list_datacenters() == DATACENTERS
        assert request.call_args_list[0].kwargs["params"] == {
            "include": "AVAILABILITY",
            "product": "SERVERLESS",
            "vcpuCount": 4,
        }
        assert request.call_args_list[1].args[1] == "/v2/catalog/datacenters"
