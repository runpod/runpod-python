"""placement solve: candidates, intersections, maximin ranking."""

import pytest

from runpod.apps.placement import (
    PlacementError,
    StockMap,
    candidates,
    solve_placement,
)
from runpod.apps.spec import ResourceKind, ResourceSpec


def _spec(name="r", gpu=None, cpu=None, datacenter=None):
    return ResourceSpec(
        kind=ResourceKind.TASK,
        name=name,
        gpu=gpu,
        cpu=cpu,
        datacenter=datacenter,
    )


def _stock(gpu=None, cpu=None):
    stock = StockMap(api=object())
    stock._gpu = gpu or {}
    stock._cpu = cpu or {}
    return stock


class TestCandidates:
    def test_gpu_stock_filters_dcs(self):
        stock = _stock(
            gpu={
                ("NVIDIA GeForce RTX 4090", "EU-RO-1"): 3,
                ("NVIDIA GeForce RTX 4090", "US-KS-2"): 0,
            }
        )
        dcs = candidates(_spec(gpu=["NVIDIA GeForce RTX 4090"]), stock)
        assert "EU-RO-1" in dcs
        assert "US-KS-2" not in dcs

    def test_pool_id_expands_to_devices(self):
        stock = _stock(gpu={("NVIDIA GeForce RTX 4090", "EU-RO-1"): 2})
        dcs = candidates(_spec(gpu=["ADA_24"]), stock)
        assert "EU-RO-1" in dcs

    def test_datacenter_pin_intersects(self):
        stock = _stock(
            gpu={
                ("NVIDIA GeForce RTX 4090", "EU-RO-1"): 3,
                ("NVIDIA GeForce RTX 4090", "US-KS-2"): 3,
            }
        )
        dcs = candidates(
            _spec(gpu=["NVIDIA GeForce RTX 4090"], datacenter=["US-KS-2"]),
            stock,
        )
        assert dcs == {"US-KS-2"}

    def test_cpu_stock(self):
        stock = _stock(cpu={("cpu5c-2-4", "EU-RO-1"): 2})
        dcs = candidates(_spec(cpu=["cpu5c-2-4"]), stock)
        assert dcs == {"EU-RO-1"}

    def test_any_gpu_allows_everywhere(self):
        stock = _stock(gpu={("NVIDIA GeForce RTX 4090", "EU-RO-1"): 1})
        dcs = candidates(_spec(gpu=None), stock)
        assert "EU-RO-1" in dcs


class TestSolvePlacement:
    def test_intersection_picks_shared_dc(self):
        stock = _stock(
            gpu={
                ("NVIDIA GeForce RTX 4090", "EU-RO-1"): 3,
                ("NVIDIA GeForce RTX 4090", "US-KS-2"): 3,
                ("NVIDIA H200", "EU-RO-1"): 2,
            }
        )
        dc = solve_placement(
            [
                _spec("train", gpu=["NVIDIA H200"]),
                _spec("infer", gpu=["NVIDIA GeForCE RTX 4090".replace("CE", "ce")]),
            ],
            stock,
            volume_name="models",
        )
        assert dc == "EU-RO-1"

    def test_disjoint_hardware_errors_with_details(self):
        stock = _stock(
            gpu={
                ("NVIDIA H200", "EU-RO-1"): 3,
                ("NVIDIA B200", "US-KS-2"): 3,
            }
        )
        with pytest.raises(PlacementError, match="no datacenter can host"):
            solve_placement(
                [
                    _spec("train", gpu=["NVIDIA H200"]),
                    _spec("eval", gpu=["NVIDIA B200"]),
                ],
                stock,
                volume_name="models",
            )

    def test_existing_dc_is_hard_constraint(self):
        stock = _stock(gpu={("NVIDIA H200", "EU-RO-1"): 3})
        dc = solve_placement(
            [_spec("train", gpu=["NVIDIA H200"])],
            stock,
            volume_name="models",
            existing_dc="EU-RO-1",
        )
        assert dc == "EU-RO-1"

    def test_existing_dc_unschedulable_errors(self):
        stock = _stock(gpu={("NVIDIA H200", "EU-RO-1"): 3})
        with pytest.raises(PlacementError, match="lives in US-KS-2"):
            solve_placement(
                [_spec("train", gpu=["NVIDIA H200"])],
                stock,
                volume_name="models",
                existing_dc="US-KS-2",
            )

    def test_maximin_prefers_worst_case_stock(self):
        # both DCs host both resources; EU is (3, 1), US is (2, 2):
        # maximin picks US because its worst resource is better off
        stock = _stock(
            gpu={
                ("NVIDIA H200", "EU-RO-1"): 3,
                ("NVIDIA H200", "US-KS-2"): 2,
                ("NVIDIA B200", "EU-RO-1"): 1,
                ("NVIDIA B200", "US-KS-2"): 2,
            }
        )
        dc = solve_placement(
            [
                _spec("a", gpu=["NVIDIA H200"]),
                _spec("b", gpu=["NVIDIA B200"]),
            ],
            stock,
            volume_name="v",
        )
        assert dc == "US-KS-2"

    def test_mixed_cpu_gpu_sharing(self):
        stock = _stock(
            gpu={
                ("NVIDIA GeForce RTX 4090", "EU-RO-1"): 3,
                ("NVIDIA GeForce RTX 4090", "US-KS-2"): 3,
            },
            cpu={("cpu5c-2-4", "EU-RO-1"): 2},
        )
        dc = solve_placement(
            [
                _spec("gpures", gpu=["NVIDIA GeForce RTX 4090"]),
                _spec("cpures", cpu=["cpu5c-2-4"]),
            ],
            stock,
            volume_name="shared",
        )
        assert dc == "EU-RO-1"
