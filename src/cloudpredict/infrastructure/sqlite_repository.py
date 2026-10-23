"""SQLite adapter for the Repository Port (ADR 0005)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from cloudpredict.domain.models import AccessEvent, StorageObject, Tier, TierDecision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    bucket TEXT NOT NULL,
    key TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    tier TEXT NOT NULL,
    PRIMARY KEY (bucket, key)
);
CREATE TABLE IF NOT EXISTS access_events (
    bucket TEXT NOT NULL,
    key TEXT NOT NULL,
    accessed_at TEXT NOT NULL,
    PRIMARY KEY (bucket, key, accessed_at)
);
CREATE TABLE IF NOT EXISTS tier_decisions (
    object_id TEXT NOT NULL,
    estimator TEXT NOT NULL,
    as_of TEXT NOT NULL,
    from_tier TEXT NOT NULL,
    to_tier TEXT NOT NULL,
    access_probability TEXT NOT NULL,
    storage_savings TEXT NOT NULL,
    retrieval_penalty TEXT NOT NULL,
    expected_value TEXT NOT NULL,
    recommended INTEGER NOT NULL,
    PRIMARY KEY (object_id, estimator, as_of)
);
"""


class SqliteRepository:
    """Synchronous, single-file repository behind RepositoryPort."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path))
        self._connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self._connection.close()

    def initialize(self) -> None:
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def reset(self) -> None:
        self._connection.execute("DELETE FROM access_events")
        self._connection.execute("DELETE FROM objects")
        self._connection.commit()

    def add_objects(self, objects: Iterable[StorageObject]) -> int:
        rows = [
            (obj.bucket, obj.key, obj.size_bytes, obj.created_at.isoformat(), obj.tier.value)
            for obj in objects
        ]
        with self._connection:
            self._connection.executemany(
                "INSERT OR REPLACE INTO objects (bucket, key, size_bytes, created_at, tier) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def add_access_events(self, events: Iterable[AccessEvent]) -> int:
        rows = [(event.bucket, event.key, event.accessed_at.isoformat()) for event in events]
        with self._connection:
            self._connection.executemany(
                "INSERT OR IGNORE INTO access_events (bucket, key, accessed_at) VALUES (?, ?, ?)",
                rows,
            )
        return len(rows)

    def list_objects(self) -> list[StorageObject]:
        cursor = self._connection.execute(
            "SELECT bucket, key, size_bytes, created_at, tier FROM objects ORDER BY bucket, key"
        )
        return [
            StorageObject(
                bucket=row[0],
                key=row[1],
                size_bytes=int(row[2]),
                created_at=_parse(row[3]),
                tier=Tier(row[4]),
            )
            for row in cursor.fetchall()
        ]

    def list_access_events(self) -> list[AccessEvent]:
        cursor = self._connection.execute(
            "SELECT bucket, key, accessed_at FROM access_events ORDER BY accessed_at, bucket, key"
        )
        return [
            AccessEvent(bucket=row[0], key=row[1], accessed_at=_parse(row[2]))
            for row in cursor.fetchall()
        ]

    def object_count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM objects").fetchone()
        return int(row[0])

    def event_count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM access_events").fetchone()
        return int(row[0])

    def save_decisions(
        self,
        estimator: str,
        as_of: datetime,
        decisions: Iterable[TierDecision],
    ) -> int:
        rows = [
            (
                decision.object_id,
                estimator,
                as_of.isoformat(),
                decision.from_tier.value,
                decision.to_tier.value,
                str(decision.access_probability),
                str(decision.storage_savings),
                str(decision.retrieval_penalty),
                str(decision.expected_value),
                int(decision.recommended),
            )
            for decision in decisions
        ]
        with self._connection:
            self._connection.executemany(
                "INSERT OR REPLACE INTO tier_decisions "
                "(object_id, estimator, as_of, from_tier, to_tier, access_probability, "
                "storage_savings, retrieval_penalty, expected_value, recommended) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def list_decisions(self, estimator: str, as_of: datetime) -> list[TierDecision]:
        cursor = self._connection.execute(
            "SELECT object_id, from_tier, to_tier, access_probability, storage_savings, "
            "retrieval_penalty, expected_value, recommended FROM tier_decisions "
            "WHERE estimator = ? AND as_of = ? ORDER BY expected_value DESC",
            (estimator, as_of.isoformat()),
        )
        return [
            TierDecision(
                object_id=row[0],
                from_tier=Tier(row[1]),
                to_tier=Tier(row[2]),
                access_probability=Decimal(row[3]),
                storage_savings=Decimal(row[4]),
                retrieval_penalty=Decimal(row[5]),
                expected_value=Decimal(row[6]),
                recommended=bool(row[7]),
            )
            for row in cursor.fetchall()
        ]


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)
