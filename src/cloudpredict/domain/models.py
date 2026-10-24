"""Domain entities: Objects, access events, and tier decisions.

Pure Python, stdlib only (ADR 0002/0003).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

BYTES_PER_GIB = Decimal(1024**3)


class Tier(StrEnum):
    """Abstract Storage Tier; backend mapping lives in configuration."""

    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"

    @property
    def rank(self) -> int:
        return {"HOT": 0, "WARM": 1, "COLD": 2}[self.value]

    def colder_tiers(self) -> tuple[Tier, ...]:
        """Tiers strictly cheaper than this one, coldest last."""
        return tuple(tier for tier in Tier if tier.rank > self.rank)


@dataclass(frozen=True, slots=True)
class StorageObject:
    """One stored Object, addressed by bucket and key."""

    bucket: str
    key: str
    size_bytes: int
    created_at: datetime
    tier: Tier = Tier.HOT

    @property
    def object_id(self) -> str:
        return f"{self.bucket}/{self.key}"

    @property
    def size_gb(self) -> Decimal:
        return Decimal(self.size_bytes) / BYTES_PER_GIB


@dataclass(frozen=True, slots=True)
class ObjectHead:
    """What the store reports about one stored Object without downloading it."""

    bucket: str
    key: str
    size_bytes: int
    etag: str


@dataclass(frozen=True, slots=True)
class AccessEvent:
    """One recorded read of an Object."""

    bucket: str
    key: str
    accessed_at: datetime

    @property
    def object_id(self) -> str:
        return f"{self.bucket}/{self.key}"


@dataclass(frozen=True, slots=True)
class TierDecision:
    """The priced outcome of considering one tier move for one Object."""

    object_id: str
    from_tier: Tier
    to_tier: Tier
    access_probability: Decimal
    storage_savings: Decimal
    retrieval_penalty: Decimal
    expected_value: Decimal
    recommended: bool
