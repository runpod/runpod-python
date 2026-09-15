"""gpu and cpu selection enums.

GpuGroup values are backend pool ids; GpuType values are exact device
display names. either (or plain strings) are accepted anywhere a gpu is
configured. CpuInstanceType values are backend cpu flavor ids.
"""

from enum import Enum
from typing import List, Optional, Union


class GpuGroup(Enum):
    ANY = "any"
    """any gpu"""

    ADA_24 = "ADA_24"
    """NVIDIA GeForce RTX 4090"""

    ADA_32_PRO = "ADA_32_PRO"
    """NVIDIA GeForce RTX 5090"""

    ADA_48_PRO = "ADA_48_PRO"
    """NVIDIA RTX 6000 Ada Generation, NVIDIA L40, NVIDIA L40S"""

    ADA_80_PRO = "ADA_80_PRO"
    """NVIDIA H100 PCIe, NVIDIA H100 80GB HBM3, NVIDIA H100 NVL"""

    AMPERE_16 = "AMPERE_16"
    """NVIDIA RTX A4000, NVIDIA RTX A4500, NVIDIA RTX 4000 Ada Generation, NVIDIA RTX 2000 Ada Generation"""

    AMPERE_24 = "AMPERE_24"
    """NVIDIA RTX A5000, NVIDIA L4, NVIDIA GeForce RTX 3090"""

    AMPERE_48 = "AMPERE_48"
    """NVIDIA A40, NVIDIA RTX A6000"""

    AMPERE_80 = "AMPERE_80"
    """NVIDIA A100 80GB PCIe, NVIDIA A100-SXM4-80GB"""

    HOPPER_141 = "HOPPER_141"
    """NVIDIA H200"""

    BLACKWELL_96 = "BLACKWELL_96"
    """NVIDIA RTX PRO 6000 Blackwell Server/Workstation/Max-Q editions"""

    BLACKWELL_180 = "BLACKWELL_180"
    """NVIDIA B200"""

    @classmethod
    def all(cls) -> List["GpuGroup"]:
        """all gpu groups except ANY."""
        return [g for g in cls if g is not cls.ANY]

    def device_names(self) -> List[str]:
        """exact device display names in this pool.

        serverless endpoints take pool ids directly (gpuIds), but pods
        take device names (gpuTypeIdList), so tasks need the expansion.
        """
        return [t.value for t in POOLS_TO_TYPES.get(self, [])]


class GpuType(Enum):
    ANY = "any"
    """any gpu"""

    NVIDIA_GEFORCE_RTX_4090 = "NVIDIA GeForce RTX 4090"
    NVIDIA_GEFORCE_RTX_5090 = "NVIDIA GeForce RTX 5090"
    NVIDIA_RTX_6000_ADA_GENERATION = "NVIDIA RTX 6000 Ada Generation"
    NVIDIA_RTX_PRO_6000_BLACKWELL_SERVER_EDITION = (
        "NVIDIA RTX PRO 6000 Blackwell Server Edition"
    )
    NVIDIA_RTX_PRO_6000_BLACKWELL_WORKSTATION_EDITION = (
        "NVIDIA RTX PRO 6000 Blackwell Workstation Edition"
    )
    NVIDIA_RTX_PRO_6000_BLACKWELL_MAX_Q_WORKSTATION_EDITION = (
        "NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition"
    )
    NVIDIA_H100_80GB_HBM3 = "NVIDIA H100 80GB HBM3"
    NVIDIA_RTX_A4000 = "NVIDIA RTX A4000"
    NVIDIA_RTX_A4500 = "NVIDIA RTX A4500"
    NVIDIA_RTX_4000_ADA_GENERATION = "NVIDIA RTX 4000 Ada Generation"
    NVIDIA_RTX_2000_ADA_GENERATION = "NVIDIA RTX 2000 Ada Generation"
    NVIDIA_RTX_A5000 = "NVIDIA RTX A5000"
    NVIDIA_L4 = "NVIDIA L4"
    NVIDIA_GEFORCE_RTX_3090 = "NVIDIA GeForce RTX 3090"
    NVIDIA_A40 = "NVIDIA A40"
    NVIDIA_RTX_A6000 = "NVIDIA RTX A6000"
    NVIDIA_A100_80GB_PCIe = "NVIDIA A100 80GB PCIe"
    NVIDIA_A100_SXM4_80GB = "NVIDIA A100-SXM4-80GB"
    NVIDIA_H200 = "NVIDIA H200"
    NVIDIA_B200 = "NVIDIA B200"

    @classmethod
    def all(cls) -> List["GpuType"]:
        """all gpu types except ANY."""
        return [g for g in cls if g is not cls.ANY]


POOLS_TO_TYPES = {
    GpuGroup.ADA_24: [GpuType.NVIDIA_GEFORCE_RTX_4090],
    GpuGroup.ADA_32_PRO: [GpuType.NVIDIA_GEFORCE_RTX_5090],
    GpuGroup.ADA_48_PRO: [GpuType.NVIDIA_RTX_6000_ADA_GENERATION],
    GpuGroup.ADA_80_PRO: [GpuType.NVIDIA_H100_80GB_HBM3],
    GpuGroup.AMPERE_16: [
        GpuType.NVIDIA_RTX_A4000,
        GpuType.NVIDIA_RTX_A4500,
        GpuType.NVIDIA_RTX_4000_ADA_GENERATION,
        GpuType.NVIDIA_RTX_2000_ADA_GENERATION,
    ],
    GpuGroup.AMPERE_24: [
        GpuType.NVIDIA_RTX_A5000,
        GpuType.NVIDIA_L4,
        GpuType.NVIDIA_GEFORCE_RTX_3090,
    ],
    GpuGroup.AMPERE_48: [GpuType.NVIDIA_A40, GpuType.NVIDIA_RTX_A6000],
    GpuGroup.AMPERE_80: [
        GpuType.NVIDIA_A100_80GB_PCIe,
        GpuType.NVIDIA_A100_SXM4_80GB,
    ],
    GpuGroup.HOPPER_141: [GpuType.NVIDIA_H200],
    GpuGroup.BLACKWELL_96: [
        GpuType.NVIDIA_RTX_PRO_6000_BLACKWELL_SERVER_EDITION,
        GpuType.NVIDIA_RTX_PRO_6000_BLACKWELL_WORKSTATION_EDITION,
        GpuType.NVIDIA_RTX_PRO_6000_BLACKWELL_MAX_Q_WORKSTATION_EDITION,
    ],
    GpuGroup.BLACKWELL_180: [GpuType.NVIDIA_B200],
}


def pool_for_gpu_type(gpu_type: GpuType) -> Optional[GpuGroup]:
    """the pool a specific gpu type belongs to, if any."""
    for group, types in POOLS_TO_TYPES.items():
        if gpu_type in types:
            return group
    return None


class CpuInstanceType(str, Enum):
    """cpu instance flavors: {generation}{type}-{vcpu}-{memory_gb}."""

    CPU3G_1_4 = "cpu3g-1-4"
    CPU3G_2_8 = "cpu3g-2-8"
    CPU3G_4_16 = "cpu3g-4-16"
    CPU3G_8_32 = "cpu3g-8-32"
    CPU3C_1_2 = "cpu3c-1-2"
    CPU3C_2_4 = "cpu3c-2-4"
    CPU3C_4_8 = "cpu3c-4-8"
    CPU3C_8_16 = "cpu3c-8-16"
    CPU5C_1_2 = "cpu5c-1-2"
    CPU5C_2_4 = "cpu5c-2-4"
    CPU5C_4_8 = "cpu5c-4-8"
    CPU5C_8_16 = "cpu5c-8-16"


GpuLike = Union[GpuGroup, GpuType, str]


def resolve_gpu_string(value: str) -> List[str]:
    """resolve a user-supplied gpu string to api-facing values.

    accepted forms, in match order:
      - "any"
      - pool ids ("ADA_24", case-insensitive)
      - exact device names ("NVIDIA B200")
      - enum-style names ("NVIDIA_B200")
      - shorthand device fragments ("B200", "4090", "h100"): every
        device whose name contains the fragment matches, so "H100"
        selects all H100 variants

    unknown strings raise with the full list of valid options.
    """
    text = value.strip()
    if text.lower() == "any":
        return [GpuGroup.ANY.value]

    upper = text.upper()
    for group in GpuGroup.all():
        if group.value.upper() == upper:
            return [group.value]

    for gpu_type in GpuType.all():
        if gpu_type.value.upper() == upper or gpu_type.name == upper:
            return [gpu_type.value]

    fragment = upper.replace("_", " ")
    matches = [
        gpu_type.value
        for gpu_type in GpuType.all()
        if fragment in gpu_type.value.upper()
    ]
    if matches:
        return matches

    from .errors import InvalidResourceError

    pools = ", ".join(g.value for g in GpuGroup.all())
    devices = ", ".join(t.value for t in GpuType.all())
    raise InvalidResourceError(
        f"unknown gpu '{value}'. use a pool id ({pools}), a device "
        f"name ({devices}), or a fragment like '4090' or 'H100'"
    )


def gpu_ids_value(gpu: Optional[List[str]]) -> str:
    """the gpuIds string for an endpoint payload.

    "any gpu" (no selection, or the ANY sentinel) means every pool id:
    the api has no wildcard and rejects "any". device names map back
    to their pool (endpoints select by pool, not device).
    """
    if not gpu or any(str(g).lower() == "any" for g in gpu):
        return ",".join(g.value for g in GpuGroup.all())
    pools: List[str] = []
    for entry in gpu:
        value = str(entry)
        try:
            pool = pool_for_gpu_type(GpuType(value))
            value = pool.value if pool is not None else value
        except ValueError:
            # not a known GpuType: pass the raw pool/id string through
            pass
        if value not in pools:
            pools.append(value)
    return ",".join(pools)
