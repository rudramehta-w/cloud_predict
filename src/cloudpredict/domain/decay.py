"""Decay-weighted frequency estimator: the heuristic baseline (ADR 0004)."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from cloudpredict.domain.models import AccessEvent, StorageObject

SECONDS_PER_DAY = 86400.0
PROBABILITY_QUANTUM = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True)
class DecayFrequencyEstimator:
    """Exponentially decayed access counts, smoothed with a prior rate.

    Each past access contributes ``exp(-age / tau)``; the effective count is
    divided by the kernel's effective exposure (``tau``) plus a prior
    pseudo-count, giving a per-day rate. Access Probability is
    ``1 - exp(-rate * horizon)``.
    """

    tau_days: float = 30.0
    prior_rate_per_day: float = 1.0 / 180.0
    horizon_days: int = 30

    @property
    def name(self) -> str:
        return "decay-frequency"

    def estimate(
        self,
        objects: Sequence[StorageObject],
        events: Sequence[AccessEvent],
        as_of: datetime,
    ) -> dict[str, Decimal]:
        grouped: dict[str, list[float]] = defaultdict(list)
        for event in events:
            age_days = (as_of - event.accessed_at).total_seconds() / SECONDS_PER_DAY
            if age_days >= 0.0:
                grouped[event.object_id].append(age_days)

        prior_pseudo_count = 1.0
        prior_exposure = prior_pseudo_count / self.prior_rate_per_day
        probabilities: dict[str, Decimal] = {}
        for obj in objects:
            ages = grouped.get(obj.object_id, [])
            effective_count = sum(math.exp(-age / self.tau_days) for age in ages)
            rate = (effective_count + prior_pseudo_count) / (self.tau_days + prior_exposure)
            probability = 1.0 - math.exp(-rate * self.horizon_days)
            probabilities[obj.object_id] = Decimal(str(round(probability, 12))).quantize(
                PROBABILITY_QUANTUM
            )
        return probabilities
