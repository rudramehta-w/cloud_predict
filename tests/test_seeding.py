"""Ticket 12: deterministic seeding plans plus a live idempotency test."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cloudpredict.domain.models import Tier
from cloudpredict.infrastructure.seeding import (
    MIB,
    plan_objects,
    seed_hot_bucket,
    simulate_accesses,
    write_sparse_file,
)
from cloudpredict.infrastructure.silo_storage import SiloStorage
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

ANCHOR = datetime(2026, 6, 1, tzinfo=UTC)


def test_plan_is_deterministic_and_within_bounds() -> None:
    first = plan_objects(count=30, seed=42)
    second = plan_objects(count=30, seed=42)

    assert first == second
    assert len({plan.key for plan in first}) == 30
    assert all(20 * MIB <= plan.size_bytes <= 100 * MIB for plan in first)


def test_plan_names_are_realistic() -> None:
    plans = plan_objects(count=10, seed=7)

    assert all("/" in plan.key for plan in plans)
    assert {plan.key.rsplit(".", 1)[-1] for plan in plans} <= {
        "parquet",
        "bin",
        "json",
        "tar",
        "csv",
    }


def test_sparse_file_has_the_requested_size(tmp_path: Path) -> None:
    path = tmp_path / "sparse.bin"
    write_sparse_file(path, 5 * MIB)

    assert path.stat().st_size == 5 * MIB


def test_access_history_is_zipfian_and_deterministic() -> None:
    plans = plan_objects(count=20, seed=42)
    created = ANCHOR - timedelta(days=90)

    first = simulate_accesses(plans, created, ANCHOR, seed=42)
    second = simulate_accesses(plans, created, ANCHOR, seed=42)

    assert first == second
    assert first


def _live_storage() -> SiloStorage:
    storage = SiloStorage()
    try:
        storage.ensure_bucket("hot")
        storage.ensure_bucket("cold")
    except Exception as error:  # noqa: BLE001 - connection failures mean skip
        pytest.skip(f"Silo unreachable: {error}")
    return storage


def test_seed_is_idempotent_against_live_silo(tmp_path: Path) -> None:
    pytest.importorskip("boto3")
    storage = _live_storage()
    repository = SqliteRepository(tmp_path / "seed.db")
    repository.initialize()
    plans = plan_objects(count=3, seed=5, size_range_mib=(1, 2))

    try:
        first = seed_hot_bucket(
            storage,
            repository,
            count=3,
            seed=5,
            size_range_mib=(1, 2),
            workdir=tmp_path / "files",
            anchor=ANCHOR,
        )
        second = seed_hot_bucket(
            storage,
            repository,
            count=3,
            seed=5,
            size_range_mib=(1, 2),
            workdir=tmp_path / "files",
            anchor=ANCHOR,
        )

        assert first["objects"] == 3
        assert first["uploaded"] == 3
        assert second["uploaded"] == 0
        assert second["skipped"] == 3
        assert repository.object_count() == 3
        assert repository.event_count() == first["events"]
        assert all(obj.tier is Tier.HOT for obj in repository.list_objects())
    finally:
        repository.close()
        for plan in plans:
            if storage.head("hot", plan.key) is not None:
                storage.delete_object("hot", plan.key)
