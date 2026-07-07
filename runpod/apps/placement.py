"""datacenter placement: solving where volumes and resources can live.

a network volume pins everything attached to it to one datacenter, so
placement is a constraint solve over the whole app: each resource's
schedulable datacenters (hardware stock ∩ user pins ∩ storage support)
intersected per volume, ranked maximin by stock so the chosen DC is
the one where the most-constrained resource has the best availability.
"""

import asyncio
import logging
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .datacenter import DataCenter
from .errors import AppError
from .gpu import GpuGroup

log = logging.getLogger(__name__)

# stock signal ranking; unknown/none scores zero
_STOCK_SCORE = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


class PlacementError(AppError):
    pass


def _score(status: Optional[str]) -> int:
    if not isinstance(status, str):
        return 0
    return _STOCK_SCORE.get(status.strip().upper(), 0)


def _hardware_keys(spec) -> List[Tuple[str, str]]:
    """(kind, id) stock lookup keys for a resource's hardware.

    gpu entries may be pool ids or device names; pools expand to their
    device names since the stock api takes devices.
    """
    if spec.is_cpu:
        return [("cpu", c) for c in spec.cpu or []]
    gpu = spec.gpu
    if not gpu or any(str(g).lower() == "any" for g in gpu):
        return [("gpu", "*")]
    keys: List[Tuple[str, str]] = []
    for entry in gpu:
        try:
            for device in GpuGroup(str(entry)).device_names():
                keys.append(("gpu", device))
        except ValueError:
            keys.append(("gpu", str(entry)))
    return keys


class StockMap:
    """per-(hardware, datacenter) stock signals, fetched lazily in bulk."""

    def __init__(self, api=None):
        self._api = api
        self._gpu: Dict[Tuple[str, str], int] = {}
        self._cpu: Dict[Tuple[str, str], int] = {}
        self._fetched_gpu: Set[str] = set()
        self._fetched_cpu: Set[str] = set()

    async def _client(self):
        if self._api is None:
            from .api import AppsApiClient

            self._api = AppsApiClient()
        return self._api

    async def fetch(self, keys: Iterable[Tuple[str, str]]) -> None:
        """populate stock for the given hardware keys across all DCs."""
        gpu_ids = {
            k[1]
            for k in keys
            if k[0] == "gpu" and k[1] != "*" and k[1] not in self._fetched_gpu
        }
        cpu_ids = {
            k[1] for k in keys if k[0] == "cpu" and k[1] not in self._fetched_cpu
        }
        client = await self._client()
        jobs = []
        for gpu_id in gpu_ids:
            self._fetched_gpu.add(gpu_id)
            for dc in DataCenter.all():
                jobs.append(self._fetch_gpu(client, gpu_id, dc.value))
        for cpu_id in cpu_ids:
            self._fetched_cpu.add(cpu_id)
            for dc in DataCenter.all():
                jobs.append(self._fetch_cpu(client, cpu_id, dc.value))
        if jobs:
            await asyncio.gather(*jobs)

    async def _fetch_gpu(self, client, gpu_id: str, dc: str) -> None:
        try:
            status = await client.gpu_stock_status(gpu_id, dc)
        except Exception:  # noqa: BLE001 - stock is advisory
            log.debug("gpu stock query failed for %s@%s", gpu_id, dc, exc_info=True)
            status = None
        self._gpu[(gpu_id, dc)] = _score(status)

    async def _fetch_cpu(self, client, instance_id: str, dc: str) -> None:
        try:
            status = await client.cpu_stock_status(instance_id, dc)
        except Exception:  # noqa: BLE001 - stock is advisory
            log.debug("cpu stock query failed for %s@%s", instance_id, dc, exc_info=True)
            status = None
        self._cpu[(instance_id, dc)] = _score(status)

    def score(self, key: Tuple[str, str], dc: str) -> int:
        kind, hw = key
        if kind == "gpu":
            if hw == "*":
                # any gpu: best signal among fetched devices, else assume ok
                scores = [
                    s for (g, d), s in self._gpu.items() if d == dc
                ]
                return max(scores, default=1)
            return self._gpu.get((hw, dc), 0)
        return self._cpu.get((hw, dc), 0)


def candidates(spec, stock: StockMap) -> Set[str]:
    """datacenters where a resource is schedulable.

    hardware needs stock in the DC (any of the resource's acceptable
    devices/flavors), intersected with an explicit datacenter pin.
    """
    allowed = {dc.value for dc in DataCenter.all()}
    if spec.datacenter:
        allowed &= {str(d) for d in spec.datacenter}

    keys = _hardware_keys(spec)
    if not keys:
        return allowed
    viable = set()
    for dc in allowed:
        if any(stock.score(key, dc) > 0 for key in keys):
            viable.add(dc)
    return viable


def _resource_best_score(spec, stock: StockMap, dc: str) -> int:
    keys = _hardware_keys(spec)
    if not keys:
        return 1
    return max(stock.score(key, dc) for key in keys)


def solve_placement(
    specs: List,
    stock: StockMap,
    *,
    volume_name: str,
    existing_dc: Optional[str] = None,
) -> str:
    """pick the datacenter for one volume given every resource using it.

    an existing volume's DC is a hard constraint (verified schedulable);
    a new volume lands in the intersection of every resource's candidate
    set, ranked maximin: the DC where the most-constrained resource has
    the best stock.
    """
    per_resource = {spec.name: candidates(spec, stock) for spec in specs}

    if existing_dc is not None:
        blocked = [
            name for name, dcs in per_resource.items() if existing_dc not in dcs
        ]
        if blocked:
            raise PlacementError(
                f"volume '{volume_name}' lives in {existing_dc}, but "
                f"{', '.join(blocked)} cannot schedule there "
                f"(no hardware stock or conflicting datacenter pin)"
            )
        return existing_dc

    shared = set.intersection(*per_resource.values()) if per_resource else set()
    if not shared:
        lines = [
            f"  {name:<12} schedulable in: {', '.join(sorted(dcs)) or '(nowhere)'}"
            for name, dcs in per_resource.items()
        ]
        raise PlacementError(
            f"cannot place volume '{volume_name}': no datacenter can host "
            f"every resource using it\n" + "\n".join(lines) + "\n"
            f"use separate volumes or compatible hardware"
        )

    # maximin: rank each DC by the worst resource's stock there,
    # tiebreak by the aggregate
    def rank(dc: str) -> Tuple[int, int]:
        scores = [_resource_best_score(spec, stock, dc) for spec in specs]
        return (min(scores), sum(scores))

    return max(sorted(shared), key=rank)
