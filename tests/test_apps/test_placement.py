"""placement solve: candidates, intersections, maximin ranking."""

import pytest
from unittest.mock import AsyncMock

from runpod.apps.datacenter import DataCenter

from runpod.apps.placement import (
    PlacementError,
    StockMap,
    _hardware_keys,
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
    # task specs query the pod plane; mirror into both tables so the
    # fixtures stay hardware-shaped rather than plane-shaped
    stock._gpu = {(gpu_id, 1, dc): score for (gpu_id, dc), score in (gpu or {}).items()}
    stock._gpu_pod = dict(stock._gpu)
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

    @pytest.mark.parametrize(
        "pin",
        [DataCenter.EU_RO_1, [DataCenter.EU_RO_1], " eu_ro_1 ", ["eu-ro-1"]],
    )
    def test_datacenter_variants_match_api_ids(self, pin):
        spec = _spec(cpu="cpu5c-2-4", datacenter=pin)
        stock = _stock(cpu={("cpu5c-2-4", "EU-RO-1"): 2})
        assert candidates(spec, stock) == {"EU-RO-1"}
        assert spec.to_manifest()["locations"] == "EU-RO-1"

    def test_mutated_datacenter_pin_is_normalized(self):
        spec = _spec(cpu="cpu5c-2-4")
        spec.datacenter = [DataCenter.EU_RO_1]
        stock = _stock(cpu={("cpu5c-2-4", "EU-RO-1"): 2})
        assert candidates(spec, stock) == {"EU-RO-1"}

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

    @pytest.mark.parametrize("kind", [ResourceKind.QUEUE, ResourceKind.TASK])
    async def test_shared_volume_requires_requested_gpu_count(self, kind):
        async def gpu_stock(gpu_id, dc, gpu_count=1, pods=False):
            if dc == "EU-RO-1" and gpu_count == 1:
                return "HIGH"
            if dc == "US-KS-2":
                return "MEDIUM"
            return None

        api = AsyncMock()
        api.gpu_stock_status.side_effect = gpu_stock
        stock = StockMap(api)
        single = ResourceSpec(kind=kind, name="single", gpu="4090")
        pair = ResourceSpec(kind=kind, name="pair", gpu="4090", gpu_count=2)
        await stock.fetch(_hardware_keys(single))
        await stock.fetch(_hardware_keys(pair))

        assert solve_placement([single], stock, volume_name="one") == "EU-RO-1"
        assert solve_placement([single, pair], stock, volume_name="shared") == "US-KS-2"
        with pytest.raises(PlacementError):
            solve_placement(
                [pair], stock, volume_name="fixed", existing_dc="EU-RO-1"
            )
