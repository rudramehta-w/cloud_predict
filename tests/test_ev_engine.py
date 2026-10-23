"""Ticket 04: the EV formula pinned to ADR 0001, plus the complement rule and gate."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cloudpredict.domain.ev import FinOpsEngine, TierPrice, inactivity_probability
from cloudpredict.domain.models import StorageObject, Tier
from cloudpredict.infrastructure.config import load_config

GIB = 1024**3
PRICES = {
    Tier.HOT: TierPrice(Decimal("0.023"), Decimal("0.000")),
    Tier.WARM: TierPrice(Decimal("0.0125"), Decimal("0.010")),
    Tier.COLD: TierPrice(Decimal("0.004"), Decimal("0.030")),
}


def make_object(size_gib: int = 100, tier: Tier = Tier.HOT) -> StorageObject:
    return StorageObject(
        bucket="b",
        key="k",
        size_bytes=size_gib * GIB,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        tier=tier,
    )


def test_adr_0001_worked_example_is_exact() -> None:
    """EV = (1 - 0.2) * 1.90 - 0.2 * 3.00 = 0.92 for a 100 GiB Hot -> Cold move."""
    decision = FinOpsEngine(PRICES).evaluate(make_object(), Tier.COLD, Decimal("0.2"))

    assert decision.storage_savings == Decimal("1.9")
    assert decision.retrieval_penalty == Decimal("3.0")
    assert decision.expected_value == Decimal("0.92")
    assert decision.recommended is True


def test_inactivity_is_the_complement_in_one_place() -> None:
    assert inactivity_probability(Decimal("0.2")) == Decimal("0.8")
    with pytest.raises(ValueError):
        inactivity_probability(Decimal("1.5"))


def test_extreme_probabilities_bound_the_ev() -> None:
    engine = FinOpsEngine(PRICES)
    never = engine.evaluate(make_object(), Tier.COLD, Decimal("0"))
    always = engine.evaluate(make_object(), Tier.COLD, Decimal("1"))

    assert never.expected_value == never.storage_savings
    assert always.expected_value == -always.retrieval_penalty


@given(
    lower=st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=4),
    upper=st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=4),
)
def test_ev_decreases_as_access_probability_rises(lower: Decimal, upper: Decimal) -> None:
    if lower > upper:
        lower, upper = upper, lower
    engine = FinOpsEngine(PRICES)

    low_ev = engine.evaluate(make_object(), Tier.COLD, lower).expected_value
    high_ev = engine.evaluate(make_object(), Tier.COLD, upper).expected_value

    assert low_ev >= high_ev


def test_margin_gate_blocks_thin_edges() -> None:
    decision = FinOpsEngine(PRICES, min_ev_margin=Decimal("1.00")).evaluate(
        make_object(), Tier.COLD, Decimal("0.2")
    )

    assert decision.expected_value == Decimal("0.92")
    assert decision.recommended is False


def test_best_candidate_prefers_the_coldest_cheap_move() -> None:
    engine = FinOpsEngine(PRICES)
    candidate = engine.best_candidate(make_object(), Decimal("0.05"))

    assert candidate is not None
    assert candidate.to_tier is Tier.COLD


def test_best_move_returns_none_when_reads_are_likely() -> None:
    engine = FinOpsEngine(PRICES)
    obj = make_object()

    assert engine.best_move(obj, Decimal("0.99")) is None
    candidate = engine.best_candidate(obj, Decimal("0.99"))
    assert candidate is not None and candidate.recommended is False


def test_cannot_move_to_a_warmer_tier() -> None:
    engine = FinOpsEngine(PRICES)
    with pytest.raises(ValueError):
        engine.evaluate(make_object(tier=Tier.WARM), Tier.HOT, Decimal("0.1"))


def test_config_prices_are_citable_and_complete() -> None:
    config = load_config()

    assert set(config.tiers) == {Tier.HOT, Tier.WARM, Tier.COLD}
    assert config.tiers[Tier.HOT].storage_per_gb_month == Decimal("0.023")
    assert config.tiers[Tier.COLD].retrieval_per_gb == Decimal("0.030")
    assert config.min_ev_margin == Decimal("0.00")
    assert config.pricing.source.startswith("https://aws.amazon.com/s3/pricing")
    assert config.pricing.version
