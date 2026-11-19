"""datacenter selection for app resources.

network volume creation support is discovered from the datacenter catalog."""

from enum import Enum
from typing import List


class DataCenter(str, Enum):

    # north america
    CA_MTL_1 = "CA-MTL-1"
    CA_MTL_3 = "CA-MTL-3"
    CA_MTL_4 = "CA-MTL-4"
    US_CA_2 = "US-CA-2"
    US_CO_1 = "US-CO-1"
    US_GA_2 = "US-GA-2"
    US_IL_1 = "US-IL-1"
    US_KS_2 = "US-KS-2"
    US_MO_1 = "US-MO-1"
    US_MO_2 = "US-MO-2"
    US_NC_1 = "US-NC-1"
    US_NC_2 = "US-NC-2"
    US_NE_1 = "US-NE-1"
    US_TX_3 = "US-TX-3"
    US_WA_1 = "US-WA-1"

    # europe
    EU_CZ_1 = "EU-CZ-1"
    EU_FR_1 = "EU-FR-1"
    EU_NL_1 = "EU-NL-1"
    EU_RO_1 = "EU-RO-1"
    EU_SE_1 = "EU-SE-1"
    EUR_IS_1 = "EUR-IS-1"
    EUR_IS_3 = "EUR-IS-3"
    EUR_IS_4 = "EUR-IS-4"
    EUR_NO_1 = "EUR-NO-1"
    EUR_NO_2 = "EUR-NO-2"

    AP_IN_1 = "AP-IN-1"
    AP_JP_1 = "AP-JP-1"

    @classmethod
    def from_string(cls, value: str) -> "DataCenter":
        """parse a datacenter id, accepting lowercase and underscores."""
        normalized = value.strip().upper().replace("_", "-")

        try:
            return cls(normalized)
        except ValueError:
            valid = ", ".join(dc.value for dc in cls)
            raise ValueError(
                f"unknown datacenter '{value}'. valid datacenters: {valid}"
            ) from None

    @classmethod
    def all(cls) -> List["DataCenter"]:
        return list(cls)

    @classmethod
    def is_known(cls, datacenter_id: str) -> bool:
        return datacenter_id in cls._value2member_map_


def datacenter_id(value: str) -> str:
    """api id for a datacenter: built-in ids normalize, others pass through.

    the built-in list goes stale as datacenters come online, and the
    backend validates ids anyway, so unrecognized ids are only stripped.
    """
    try:
        return DataCenter.from_string(value).value
    except ValueError:
        return value.strip()


# datacenters with high cpu serverless stock. storage support is checked
# separately; cpu5c/cpu5g are only stocked in EU-RO-1.
CPU3_DATACENTERS: List[DataCenter] = [
    DataCenter.EU_CZ_1,
    DataCenter.EU_RO_1,
    DataCenter.US_CA_2,
    DataCenter.US_IL_1,
    DataCenter.US_KS_2,
    DataCenter.US_MO_1,
    DataCenter.US_MO_2,
    DataCenter.US_NC_2,
    DataCenter.US_NE_1,
    DataCenter.US_WA_1,
]
CPU5_DATACENTERS: List[DataCenter] = [DataCenter.EU_RO_1]
