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
    stock._cpu = {
        (cpu_id, dc, pods): score
        for (cpu_id, dc), score in (cpu or {}).items()
        for pods in (False, True)
    }
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

    def test_unlisted_pin_stays_candidate_without_stock_data(self):
        # the sdk only queries stock for its built-in list, so an unlisted
        # pin cannot be ruled out client-side; the backend decides
        stock = _stock(gpu={("NVIDIA GeForce RTX 4090", "EU-RO-1"): 3})
        spec = _spec(gpu=["NVIDIA GeForce RTX 4090"], datacenter=["XX-TEST-1"])
        assert candidates(spec, stock) == {"XX-TEST-1"}

    def test_unlisted_pin_alongside_known_pin(self):
        stock = _stock(gpu={("NVIDIA GeForce RTX 4090", "EU-RO-1"): 0})
        spec = _spec(
            gpu=["NVIDIA GeForce RTX 4090"], datacenter=["EU-RO-1", "XX-TEST-1"]
        )
        assert candidates(spec, stock) == {"XX-TEST-1"}

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
            volume_datacenters={dc.value for dc in DataCenter.all()},
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
                volume_datacenters={dc.value for dc in DataCenter.all()},
            )

    def test_existing_dc_is_hard_constraint(self):
        stock = _stock(gpu={("NVIDIA H200", "EU-RO-1"): 3})
        dc = solve_placement(
            [_spec("train", gpu=["NVIDIA H200"])],
            stock,
            volume_name="models",
            existing_dc="EU-RO-1",
            volume_datacenters=set(),
        )
        assert dc == "EU-RO-1"

    def test_existing_unlisted_dc_with_matching_pin(self):
        dc = solve_placement(
            [_spec("train", gpu=["NVIDIA H200"], datacenter="XX-TEST-1")],
            _stock(),
            volume_name="models",
            existing_dc="XX-TEST-1",
            volume_datacenters=set(),
        )
        assert dc == "XX-TEST-1"

    def test_existing_unlisted_dc_without_pin_is_placement_error(self):
        with pytest.raises(PlacementError, match="lives in XX-TEST-1"):
            solve_placement(
                [_spec("train", gpu=["NVIDIA H200"])],
                _stock(gpu={("NVIDIA H200", "EU-RO-1"): 3}),
                volume_name="models",
                existing_dc="XX-TEST-1",
                volume_datacenters=set(),
            )

    def test_new_volume_in_unlisted_dc(self):
        dc = solve_placement(
            [_spec("train", gpu=["NVIDIA H200"], datacenter="XX-TEST-1")],
            _stock(),
            volume_name="models",
            volume_dc="XX-TEST-1",
            volume_datacenters={"EU-RO-1", "XX-TEST-1"},
        )
        assert dc == "XX-TEST-1"

    def test_existing_dc_unschedulable_errors(self):
        stock = _stock(gpu={("NVIDIA H200", "EU-RO-1"): 3})
        with pytest.raises(PlacementError, match="lives in US-KS-2"):
            solve_placement(
                [_spec("train", gpu=["NVIDIA H200"])],
                stock,
                volume_name="models",
                existing_dc="US-KS-2",
                volume_datacenters={dc.value for dc in DataCenter.all()},
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
            volume_datacenters={dc.value for dc in DataCenter.all()},
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
            volume_datacenters={dc.value for dc in DataCenter.all()},
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

        supported = {"EU-RO-1", "US-KS-2"}
        dc = solve_placement(
            [single], stock, volume_name="one", volume_datacenters=supported
        )
        assert dc == "EU-RO-1"
        dc = solve_placement(
            [single, pair], stock, volume_name="shared", volume_datacenters=supported
        )
        assert dc == "US-KS-2"
        with pytest.raises(PlacementError):
            solve_placement(
                [pair],
                stock,
                volume_name="fixed",
                existing_dc="EU-RO-1",
                volume_datacenters=supported,
            )

    async def test_cpu_task_and_endpoint_require_shared_product_stock(self):
        async def cpu_stock(instance_id, dc, *, pods=False):
            if dc == "EU-RO-1":
                return "HIGH" if not pods else "NONE"
            return "MEDIUM" if dc == "US-KS-2" else "NONE"

        api = AsyncMock()
        api.cpu_stock_status.side_effect = cpu_stock
        stock = StockMap(api)
        endpoint = ResourceSpec(kind=ResourceKind.QUEUE, name="serve", cpu="cpu5c-2-4")
        task = ResourceSpec(kind=ResourceKind.TASK, name="task", cpu="cpu5c-2-4")
        await stock.fetch(_hardware_keys(endpoint))
        await stock.fetch(_hardware_keys(task))
        supported = {"EU-RO-1", "US-KS-2"}
        dc = solve_placement(
            [endpoint], stock, volume_name="one", volume_datacenters=supported
        )
        assert dc == "EU-RO-1"
        dc = solve_placement(
            [endpoint, task], stock, volume_name="shared", volume_datacenters=supported
        )
        assert dc == "US-KS-2"
        with pytest.raises(PlacementError):
            solve_placement(
                [task],
                stock,
                volume_name="fixed",
                existing_dc="EU-RO-1",
                volume_datacenters=supported,
            )
