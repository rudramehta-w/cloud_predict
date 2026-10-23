"""Ticket 04: decay-frequency estimator behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from cloudpredict.domain.decay import DecayFrequencyEstimator
from cloudpredict.domain.models import AccessEvent, StorageObject

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _object(key: str) -> StorageObject:
    return StorageObject(
        bucket="b", key=key, size_bytes=1024, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )


def _event(key: str, age_days: float) -> AccessEvent:
    return AccessEvent(bucket="b", key=key, accessed_at=NOW - timedelta(days=age_days))


def test_never_accessed_object_gets_the_smoothed_prior() -> None:
    estimator = DecayFrequencyEstimator()
    probabilities = estimator.estimate([_object("cold")], [], NOW)

    assert Decimal("0.10") < probabilities["b/cold"] < Decimal("0.16")


def test_recent_accesses_raise_the_probability() -> None:
    estimator = DecayFrequencyEstimator()
    objects = [_object("fresh"), _object("stale")]
    events = [_event("fresh", 1), _event("stale", 100)]

    probabilities = estimator.estimate(objects, events, NOW)

    assert probabilities["b/fresh"] > probabilities["b/stale"]


def test_future_events_are_ignored() -> None:
    estimator = DecayFrequencyEstimator()
    future = AccessEvent(bucket="b", key="x", accessed_at=NOW + timedelta(days=5))

    probabilities = estimator.estimate([_object("x")], [future], NOW)

    assert probabilities["b/x"] == estimator.estimate([_object("x")], [], NOW)["b/x"]


def test_probabilities_stay_inside_the_unit_interval() -> None:
    estimator = DecayFrequencyEstimator()
    events = [_event("hot", days) for days in range(0, 60, 1)]

    probability = estimator.estimate([_object("hot")], events, NOW)["b/hot"]

    assert Decimal("0") <= probability <= Decimal("1")


def test_estimator_is_deterministic() -> None:
    estimator = DecayFrequencyEstimator()
    objects = [_object("a"), _object("b")]
    events = [_event("a", 2), _event("b", 40)]

    first = estimator.estimate(objects, events, NOW)
    second = estimator.estimate(objects, events, NOW)

    assert first == second
