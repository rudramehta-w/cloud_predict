"""RepositoryPort contract: the fake and the SQLite adapter must behave identically."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cloudpredict.domain.models import AccessEvent, StorageObject, Tier
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository
from tests.support.fakes import InMemoryRepository


@pytest.fixture(params=["fake", "sqlite"])
def repository(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[object]:
    if request.param == "sqlite":
        sqlite_repository = SqliteRepository(tmp_path / "contract.db")
        sqlite_repository.initialize()
        try:
            yield sqlite_repository
        finally:
            sqlite_repository.close()
    else:
        fake = InMemoryRepository()
        fake.initialize()
        yield fake


def _sample_objects() -> list[StorageObject]:
    return [
        StorageObject(
            bucket="b",
            key="one",
            size_bytes=1024,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            tier=Tier.HOT,
        ),
        StorageObject(
            bucket="b",
            key="two",
            size_bytes=2048,
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
            tier=Tier.WARM,
        ),
    ]


def _sample_events() -> list[AccessEvent]:
    return [
        AccessEvent(bucket="b", key="one", accessed_at=datetime(2026, 2, 1, tzinfo=UTC)),
        AccessEvent(bucket="b", key="one", accessed_at=datetime(2026, 2, 1, tzinfo=UTC)),
        AccessEvent(bucket="b", key="two", accessed_at=datetime(2026, 2, 3, tzinfo=UTC)),
    ]


def test_add_and_list_objects(repository: object) -> None:
    added = repository.add_objects(_sample_objects())  # type: ignore[attr-defined]
    assert added == 2
    assert repository.object_count() == 2  # type: ignore[attr-defined]

    listed = repository.list_objects()  # type: ignore[attr-defined]
    assert [obj.key for obj in listed] == ["one", "two"]
    assert listed[1].tier is Tier.WARM


def test_objects_are_upserted(repository: object) -> None:
    repository.add_objects(_sample_objects())  # type: ignore[attr-defined]
    updated = StorageObject(
        bucket="b", key="one", size_bytes=4096, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    repository.add_objects([updated])  # type: ignore[attr-defined]

    assert repository.object_count() == 2  # type: ignore[attr-defined]
    assert repository.list_objects()[0].size_bytes == 4096  # type: ignore[attr-defined]


def test_access_events_are_deduplicated(repository: object) -> None:
    repository.add_access_events(_sample_events())  # type: ignore[attr-defined]
    assert repository.event_count() == 2  # type: ignore[attr-defined]


def test_reset_clears_everything(repository: object) -> None:
    repository.add_objects(_sample_objects())  # type: ignore[attr-defined]
    repository.add_access_events(_sample_events())  # type: ignore[attr-defined]
    repository.reset()  # type: ignore[attr-defined]

    assert repository.object_count() == 0  # type: ignore[attr-defined]
    assert repository.event_count() == 0  # type: ignore[attr-defined]
