"""Six-rung evaluation harness: every policy is priced through the same EV lens.

Rungs 0–2 land in ticket 06; the learned rungs and the Oracle are added in
ticket 08 without changing this shape.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from cloudpredict.domain.ev import FinOpsEngine, TierPrice
from cloudpredict.domain.models import AccessEvent, StorageObject, Tier

DAYS_PER_MONTH = Decimal(30)
MONEY_QUANTUM = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Rung:
    key: str
    label: str
    moves: Mapping[str, Tier]


@dataclass(frozen=True, slots=True)
class RungResult:
    key: str
    label: str
    objects_moved: int
    storage_savings: Decimal
    retrieval_cost: Decimal
    total_ev: Decimal
    false_evictions: int
    false_eviction_cost: Decimal
    ev_capture: Decimal | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "objects_moved": self.objects_moved,
            "storage_savings": _money(self.storage_savings),
            "retrieval_cost": _money(self.retrieval_cost),
            "total_ev": _money(self.total_ev),
            "false_evictions": self.false_evictions,
            "false_eviction_cost": _money(self.false_eviction_cost),
            "ev_capture": None if self.ev_capture is None else float(round(self.ev_capture, 4)),
        }


def _money(value: Decimal) -> str:
    return str(value.quantize(MONEY_QUANTUM))


def decision_point(events: Sequence[AccessEvent]) -> datetime:
    """Score at the last observed access; the evaluation window starts there."""
    return max(event.accessed_at for event in events)


def accessed_within(events: Sequence[AccessEvent], start: datetime, horizon_days: int) -> set[str]:
    end = start + timedelta(days=horizon_days)
    return {event.object_id for event in events if start < event.accessed_at <= end}


def last_access_before(events: Sequence[AccessEvent], at: datetime) -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    for event in events:
        if event.accessed_at <= at:
            current = result.get(event.object_id)
            if current is None or event.accessed_at > current:
                result[event.object_id] = event.accessed_at
    return result


def naive_moves(
    objects: Sequence[StorageObject],
    events: Sequence[AccessEvent],
    at: datetime,
    idle_days: int = 30,
) -> dict[str, Tier]:
    """Rung 1: archive anything untouched for ``idle_days``.

    This is the time-based baseline that ADR 0001 argues against.
    """
    last_seen = last_access_before(events, at)
    threshold = at - timedelta(days=idle_days)
    moves: dict[str, Tier] = {}
    for obj in objects:
        last = last_seen.get(obj.object_id, obj.created_at)
        if last <= threshold:
            moves[obj.object_id] = Tier.COLD
    return moves


def oracle_moves(objects: Sequence[StorageObject], accessed: set[str]) -> dict[str, Tier]:
    """Rung 5: perfect foresight — move exactly the Objects that will not be read."""
    return {obj.object_id: Tier.COLD for obj in objects if obj.object_id not in accessed}


def estimator_moves(
    objects: Sequence[StorageObject],
    probabilities: Mapping[str, Decimal],
    engine: FinOpsEngine,
) -> dict[str, Tier]:
    moves: dict[str, Tier] = {}
    for obj in objects:
        decision = engine.best_move(obj, probabilities[obj.object_id])
        if decision is not None:
            moves[obj.object_id] = decision.to_tier
    return moves


def evaluate_moves(
    rung: Rung,
    objects: Sequence[StorageObject],
    accessed: set[str],
    prices: Mapping[Tier, TierPrice],
    horizon_days: int,
) -> RungResult:
    months = Decimal(horizon_days) / DAYS_PER_MONTH
    by_id = {obj.object_id: obj for obj in objects}

    savings = Decimal("0")
    retrieval = Decimal("0")
    false_evictions = 0
    moved = 0
    for object_id, target in rung.moves.items():
        obj = by_id[object_id]
        if target.rank <= obj.tier.rank:
            continue
        savings += (prices[obj.tier].storage_per_gb_month - prices[target].storage_per_gb_month) * (
            obj.size_gb * months
        )
        moved += 1
        if object_id in accessed:
            false_evictions += 1
            retrieval += prices[target].retrieval_per_gb * obj.size_gb

    return RungResult(
        key=rung.key,
        label=rung.label,
        objects_moved=moved,
        storage_savings=savings,
        retrieval_cost=retrieval,
        total_ev=savings - retrieval,
        false_evictions=false_evictions,
        false_eviction_cost=retrieval,
    )


def run_ladder(
    rungs: Sequence[Rung],
    objects: Sequence[StorageObject],
    events: Sequence[AccessEvent],
    at: datetime,
    prices: Mapping[Tier, TierPrice],
    horizon_days: int,
    oracle_key: str = "oracle",
) -> list[RungResult]:
    accessed = accessed_within(events, at, horizon_days)
    results = [evaluate_moves(rung, objects, accessed, prices, horizon_days) for rung in rungs]
    oracle = next((result for result in results if result.key == oracle_key), None)
    if oracle is not None and oracle.total_ev > 0:
        results = [
            replace(result, ev_capture=result.total_ev / oracle.total_ev) for result in results
        ]
    return results
