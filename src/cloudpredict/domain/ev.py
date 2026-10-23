"""FinOpsEngine: the ADR 0001 Expected Value decision, in exact Decimal math."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from cloudpredict.domain.models import StorageObject, Tier, TierDecision

ZERO = Decimal("0")
ONE = Decimal("1")
DAYS_PER_MONTH = Decimal(30)


@dataclass(frozen=True, slots=True)
class TierPrice:
    """Backend-neutral price inputs for one Storage Tier, in USD."""

    storage_per_gb_month: Decimal
    retrieval_per_gb: Decimal


def inactivity_probability(access_probability: Decimal) -> Decimal:
    """The complement rule, enforced in exactly one place (glossary: Inactivity Probability)."""
    if not ZERO <= access_probability <= ONE:
        raise ValueError(f"Access Probability must be in [0, 1], got {access_probability}")
    return ONE - access_probability


class FinOpsEngine:
    """Prices a candidate tier move for one Object under ADR 0001."""

    def __init__(
        self,
        prices: Mapping[Tier, TierPrice],
        min_ev_margin: Decimal = ZERO,
        scoring_horizon_days: int = 30,
    ) -> None:
        if min_ev_margin < ZERO:
            raise ValueError(f"min_ev_margin must be >= 0, got {min_ev_margin}")
        if scoring_horizon_days <= 0:
            raise ValueError(f"scoring_horizon_days must be positive, got {scoring_horizon_days}")
        self._prices = dict(prices)
        self._min_ev_margin = min_ev_margin
        self._months = Decimal(scoring_horizon_days) / DAYS_PER_MONTH

    @property
    def min_ev_margin(self) -> Decimal:
        return self._min_ev_margin

    def evaluate(
        self,
        obj: StorageObject,
        to_tier: Tier,
        access_probability: Decimal,
    ) -> TierDecision:
        """EV = P(inactive) * Storage_Savings - P(access) * Egress_Penalty."""
        if to_tier.rank <= obj.tier.rank:
            raise ValueError(f"{to_tier} is not colder than the Object's tier {obj.tier}")

        from_price = self._prices[obj.tier]
        to_price = self._prices[to_tier]
        size_gb = obj.size_gb

        storage_savings = (from_price.storage_per_gb_month - to_price.storage_per_gb_month) * (
            size_gb * self._months
        )
        egress_penalty = to_price.retrieval_per_gb * size_gb
        inactive = inactivity_probability(access_probability)
        expected_value = inactive * storage_savings - access_probability * egress_penalty

        return TierDecision(
            object_id=obj.object_id,
            from_tier=obj.tier,
            to_tier=to_tier,
            access_probability=access_probability,
            storage_savings=storage_savings,
            retrieval_penalty=egress_penalty,
            expected_value=expected_value,
            recommended=expected_value > self._min_ev_margin,
        )

    def best_candidate(
        self, obj: StorageObject, access_probability: Decimal
    ) -> TierDecision | None:
        """The highest-EV colder move, recommended or not; ``None`` with no colder tier."""
        candidates = [
            self.evaluate(obj, tier, access_probability) for tier in obj.tier.colder_tiers()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda decision: decision.expected_value)

    def best_move(self, obj: StorageObject, access_probability: Decimal) -> TierDecision | None:
        """The highest-EV colder move that clears the margin, or ``None``."""
        candidate = self.best_candidate(obj, access_probability)
        if candidate is None or not candidate.recommended:
            return None
        return candidate
