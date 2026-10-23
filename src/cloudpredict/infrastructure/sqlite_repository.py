"""SQLite adapter for the Repository Port (ADR 0005)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from cloudpredict.domain.models import AccessEvent, StorageObject, Tier

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


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)
