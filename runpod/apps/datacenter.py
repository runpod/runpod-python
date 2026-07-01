"""datacenter selection for app resources.

only datacenters with storage support and S3 API support are listed;
resources may attach network volumes at any time, so every deployable
location must be able to host them.
"""

from enum import Enum
from typing import List


class DataCenter(str, Enum):
    """runpod datacenters with storage and S3 API support."""

    # north america
    US_CA_2 = "US-CA-2"
    US_IL_1 = "US-IL-1"
    US_KS_2 = "US-KS-2"
    US_MO_1 = "US-MO-1"
    US_MO_2 = "US-MO-2"
    US_NC_2 = "US-NC-2"
    US_NE_1 = "US-NE-1"
    US_WA_1 = "US-WA-1"

    # europe
    EU_CZ_1 = "EU-CZ-1"
    EU_RO_1 = "EU-RO-1"
    EUR_NO_1 = "EUR-NO-1"

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
            )

    @classmethod
    def all(cls) -> List["DataCenter"]:
        return list(cls)


# datacenters with high cpu serverless stock, restricted to the
# storage+S3 set above. cpu5c/cpu5g are only stocked in EU-RO-1.
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
