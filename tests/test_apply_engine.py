"""Ticket 13: the execution engine never loses data and never copies twice."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from cloudpredict.domain.ev import TierPrice
from cloudpredict.domain.models import StorageObject, Tier
from cloudpredict.infrastructure.apply import (
    FAILED,
    MOVED,
    NEEDS_CLEANUP,
    PLANNED,
    SKIPPED,
    ApplyEngine,
    MovePlan,
)
from tests.support.fakes import FakeExecutionStorage, InMemoryRepository

PRICES = {
    Tier.HOT: TierPrice(Decimal("0.023"), Decimal("0.000")),
    Tier.WARM: TierPrice(Decimal("0.0125"), Decimal("0.010")),
    Tier.COLD: TierPrice(Decimal("0.004"), Decimal("0.030")),
}


def _object(key: str = "datasets/orders-001.parquet", size: int = 10 * 1024**2) -> StorageObject:
    return StorageObject(
        bucket="hot",
        key=key,
        size_bytes=size,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        tier=Tier.HOT,
    )


def _engine(storage: FakeExecutionStorage) -> tuple[ApplyEngine, InMemoryRepository]:
    repository = InMemoryRepository()
    repository.initialize()
    engine = ApplyEngine(storage, repository, PRICES, horizon_days=30)
    return engine, repository


def _plan(engine: ApplyEngine, obj: StorageObject, probability: str = "0.05") -> list[MovePlan]:
    return engine.plan_moves([obj], {obj.object_id: Decimal(probability)})


def test_happy_path_copies_verifies_then_deletes() -> None:
    storage = FakeExecutionStorage()
    engine, repository = _engine(storage)
    obj = _object()
    repository.add_objects([obj])
    storage.put("hot", obj.key, b"payload")

    results = engine.execute(_plan(engine, obj))

    assert [result.status for result in results] == [MOVED]
    assert storage.calls == [("copy", obj.key), ("delete", obj.key)]
    assert storage.head("hot", obj.key) is None
    assert storage.head("cold", obj.key) is not None
    assert [record.status for record in repository.list_migrations()] == [MOVED]
    assert repository.list_objects()[0].tier is Tier.COLD
    assert repository.list_objects()[0].bucket == "cold"


def test_failed_copy_leaves_the_source_untouched() -> None:
    storage = FakeExecutionStorage()
    engine, repository = _engine(storage)
    obj = _object()
    repository.add_objects([obj])
    storage.put("hot", obj.key, b"payload")
    storage.fail_copy = True

    results = engine.execute(_plan(engine, obj))

    assert results[0].status == FAILED
    assert storage.head("hot", obj.key) is not None
    assert storage.head("cold", obj.key) is None
    assert [record.status for record in repository.list_migrations()] == [FAILED]
    assert repository.list_objects()[0].bucket == "hot"


def test_etag_mismatch_discards_the_copy_and_keeps_the_source() -> None:
    storage = FakeExecutionStorage()
    engine, repository = _engine(storage)
    obj = _object()
    repository.add_objects([obj])
    storage.put("hot", obj.key, b"payload")
    storage.corrupt_copy = True

    results = engine.execute(_plan(engine, obj))

    assert results[0].status == FAILED
    assert "ETag mismatch" in results[0].detail
    assert storage.head("hot", obj.key) is not None
    assert storage.head("cold", obj.key) is None


def test_failed_delete_is_flagged_but_does_not_raise() -> None:
    storage = FakeExecutionStorage()
    engine, repository = _engine(storage)
    obj = _object()
    repository.add_objects([obj])
    storage.put("hot", obj.key, b"payload")
    storage.fail_delete = True

    results = engine.execute(_plan(engine, obj))

    assert results[0].status == NEEDS_CLEANUP
    assert storage.head("cold", obj.key) is not None
    assert [record.status for record in repository.list_migrations()] == [NEEDS_CLEANUP]


def test_second_run_skips_without_duplicating_the_ledger() -> None:
    storage = FakeExecutionStorage()
    engine, repository = _engine(storage)
    obj = _object()
    repository.add_objects([obj])
    storage.put("hot", obj.key, b"payload")

    engine.execute(_plan(engine, obj))
    ledger_after_first = len(repository.list_migrations())
    second = engine.execute(_plan(engine, obj))

    assert second[0].status == SKIPPED
    assert len(repository.list_migrations()) == ledger_after_first


def test_dry_run_touches_nothing() -> None:
    storage = FakeExecutionStorage()
    engine, repository = _engine(storage)
    obj = _object()
    repository.add_objects([obj])
    storage.put("hot", obj.key, b"payload")

    results = engine.execute(_plan(engine, obj), dry_run=True)

    assert results[0].status == PLANNED
    assert storage.calls == []
    assert storage.head("hot", obj.key) is not None
    assert repository.list_migrations() == []


def test_interrupted_migration_is_repaired() -> None:
    """Both buckets hold the Object (delete failed earlier); the re-run cleans the source."""
    storage = FakeExecutionStorage()
    engine, repository = _engine(storage)
    obj = _object()
    repository.add_objects([obj])
    storage.put("hot", obj.key, b"payload")
    storage.put("cold", obj.key, b"payload")

    results = engine.execute(_plan(engine, obj))

    assert results[0].status == MOVED
    assert "interrupted migration" in results[0].detail
    assert storage.head("hot", obj.key) is None
    assert storage.head("cold", obj.key) is not None


def test_plan_is_sorted_by_expected_value() -> None:
    storage = FakeExecutionStorage()
    engine, _ = _engine(storage)
    small = _object(key="a/small.bin", size=1024**2)
    large = _object(key="a/large.bin", size=50 * 1024**2)

    plans = engine.plan_moves(
        [small, large],
        {small.object_id: Decimal("0.05"), large.object_id: Decimal("0.05")},
    )

    assert [plan.key for plan in plans] == [large.key, small.key]
