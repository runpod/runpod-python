"""gpu, cpu, and region resolution against the rest v2 catalog."""

import re
from collections.abc import Iterable
from typing import Any, Optional

from runpod.api.rest import run_rest_request

AVAILABILITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

REGION_ALIASES = {
    "na": "NORTH_AMERICA",
    "northamerica": "NORTH_AMERICA",
    "sa": "SOUTH_AMERICA",
    "southamerica": "SOUTH_AMERICA",
    "eu": "EUROPE",
    "europe": "EUROPE",
    "asia": "ASIA",
    "apac": "ASIA",
    "me": "MIDDLE_EAST",
    "middleeast": "MIDDLE_EAST",
    "africa": "AFRICA",
    "oceania": "OCEANIA",
}

_NOISE_TOKENS = {"nvidia", "geforce", "amd", "instinct"}


class ResolutionError(ValueError):
    """a user-supplied gpu, cpu, or region that does not resolve."""


def _query_params(**values: Any) -> dict[str, Any]:
    return {
        key: ",".join(value) if isinstance(value, (list, tuple)) else value
        for key, value in values.items()
        if value is not None and value != []
    }


def list_gpus(
    product: Optional[str] = None,
    count: Optional[int] = None,
    cloud: Optional[str] = None,
    cuda_versions: Optional[list[str]] = None,
    min_cuda_version: Optional[str] = None,
) -> list[dict]:
    """gpu types, with availability when a product context is given."""
    params = _query_params(
        include="AVAILABILITY" if product else None,
        product=product,
        count=count if product else None,
        cloud=cloud if product else None,
        cudaVersions=cuda_versions if product else None,
        minCudaVersion=min_cuda_version if product else None,
    )
    return run_rest_request("GET", "/v2/catalog/gpus", params=params)["gpus"]


def list_cpus(
    product: Optional[str] = None, vcpu_count: Optional[int] = None
) -> list[dict]:
    """cpu flavors, with availability when a product context is given."""
    params = _query_params(
        include="AVAILABILITY" if product else None,
        product=product,
        vcpuCount=vcpu_count if product else None,
    )
    return run_rest_request("GET", "/v2/catalog/cpus", params=params)["cpus"]


def list_datacenters(include: Optional[list[str]] = None) -> list[dict]:
    """every data center in the catalog."""
    return run_rest_request(
        "GET", "/v2/catalog/datacenters", params=_query_params(include=include)
    )["dataCenters"]


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[\s_\-]+", text.lower()) if token]


def _compact(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", text.lower())


def match_gpus(query: str, gpus: Iterable[dict]) -> list[dict]:
    """prefer exact ids, names, and pools, then match hardware-name tokens."""
    gpus = list(gpus)
    text = query.strip().lower()
    exact = [
        gpu
        for gpu in gpus
        if text in (gpu["id"].lower(), (gpu.get("name") or "").lower())
    ]
    if exact:
        return exact
    pool = [gpu for gpu in gpus if (gpu.get("pool") or "").lower() == text]
    if pool:
        return pool
    wanted = [token for token in _tokens(text) if token not in _NOISE_TOKENS]
    if not wanted:
        return []
    hits = [
        gpu
        for gpu in gpus
        if set(wanted) <= set(_tokens(gpu["id"])) | set(_tokens(gpu.get("name") or ""))
    ]
    if hits:
        return hits
    compact = "".join(wanted)
    return [gpu for gpu in gpus if compact in _compact(gpu["id"])]


def resolve_gpus(query: str, gpus: Iterable[dict]) -> list[dict]:
    """like match_gpus, but raise when nothing matches."""
    gpus = list(gpus)
    matches = match_gpus(query, gpus)
    if not matches:
        known = ", ".join(sorted(gpu.get("name") or gpu["id"] for gpu in gpus))
        raise ResolutionError(f"unknown gpu '{query}'. known gpus: {known}")
    return matches


def parse_gpu_spec(value: str) -> tuple[str, Optional[int]]:
    """split "h100:2" into ("h100", 2); a bare name has no count."""
    name, sep, count = value.strip().rpartition(":")
    if not sep:
        return value.strip(), None
    if not name or not count.isdigit() or int(count) < 1:
        raise ResolutionError(
            f"invalid gpu '{value}'. use a name with an optional count, like h100:2"
        )
    return name, int(count)


def parse_cuda(values: Iterable[str]) -> tuple[Optional[str], list[str]]:
    """split cuda filters into (minimum, exact versions).

    ">=12.4" sets a floor; "12.4" or "12.4,12.8" lists exact versions.
    """
    minimum = None
    exact: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            floor = part.startswith(">=")
            version = part[2:].strip() if floor else part
            if not re.fullmatch(r"\d+\.\d+", version):
                raise ResolutionError(
                    f"invalid cuda version '{part}'. use 12.4, 12.4,12.8, or '>=12.4'"
                )
            if floor:
                minimum = version
            else:
                exact.append(version)
    if minimum and exact:
        raise ResolutionError(
            "use either a cuda floor (>=) or exact versions, not both"
        )
    return minimum, exact


def endpoint_gpu_selection(
    matches: Iterable[dict], gpus: Iterable[dict]
) -> tuple[list[str], list[str]]:
    """serverless (pools, excludedTypes) that select exactly the matched types."""
    matches = list(matches)
    pools = list(dict.fromkeys(gpu["pool"] for gpu in matches if gpu.get("pool")))
    if not pools:
        names = ", ".join(gpu.get("name") or gpu["id"] for gpu in matches)
        raise ResolutionError(f"{names} is not offered on serverless")
    chosen = {gpu["id"] for gpu in matches}
    excluded = [
        gpu["id"]
        for gpu in gpus
        if gpu.get("pool") in pools and gpu["id"] not in chosen
    ]
    return pools, excluded


def resolve_regions(values: Iterable[str], datacenters: Iterable[dict]) -> list[str]:
    """data center ids named by ids, id prefixes, or region names.

    "EU-RO-1" is one data center, "us" and "eu-ro" match every id with
    that prefix, and "eu" or "europe" match every data center in the
    region.
    """
    datacenters = list(datacenters)
    resolved: list[str] = []
    for value in values:
        text = value.strip().upper().replace("_", "-")
        region = REGION_ALIASES.get(_compact(value))
        matches = [
            dc["id"]
            for dc in datacenters
            if dc["id"].upper() == text
            or dc["id"].upper().startswith(text + "-")
            or (region and dc.get("region") == region)
        ]
        if not matches:
            ids = ", ".join(sorted(dc["id"] for dc in datacenters))
            names = ", ".join(
                sorted(
                    {
                        dc["region"].lower().replace("_", "-")
                        for dc in datacenters
                        if dc.get("region")
                    }
                )
            )
            raise ResolutionError(
                f"unknown region '{value}'. use a region (eu, na, {names}), "
                f"an id prefix like us or eu-ro, or a data center ({ids})"
            )
        resolved.extend(match for match in matches if match not in resolved)
    return resolved


def match_cpus(query: str, cpus: Iterable[dict]) -> list[dict]:
    """cpu flavors named by id ("cpu3c"), group ("cpu3"), or name."""
    text = query.strip().lower()
    return [
        cpu
        for cpu in cpus
        if text
        in (
            cpu["id"].lower(),
            (cpu.get("group") or "").lower(),
            (cpu.get("name") or "").lower(),
        )
        or _compact(text) == _compact(cpu.get("name") or "")
    ]


def resolve_cpu(query: str, cpus: Iterable[dict]) -> dict:
    """the single cpu flavor a query names."""
    cpus = list(cpus)
    matches = match_cpus(query, cpus)
    if len(matches) != 1:
        known = ", ".join(sorted(cpu["id"] for cpu in cpus))
        problem = "ambiguous" if matches else "unknown"
        raise ResolutionError(f"{problem} cpu '{query}'. cpu flavors: {known}")
    return matches[0]


def availability_rank(level: Optional[str]) -> int:
    return AVAILABILITY_RANK.get(level or "NONE", 0)


def best_availability(levels: Iterable[Optional[str]]) -> str:
    """the highest stock level among levels, NONE when empty."""
    return max(levels, key=availability_rank, default="NONE") or "NONE"
