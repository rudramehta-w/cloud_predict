"""Test-only fakes: one per Port, so contract tests run against real and fake alike."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from hashlib import md5

from cloudpredict.domain.exceptions import StorageError
from cloudpredict.domain.models import (
    AccessEvent,
    MigrationRecord,
    ObjectHead,
    StorageObject,
    Tier,
    TierDecision,
)


class InMemoryRepository:
    """In-memory RepositoryPort implementation for contract tests."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], StorageObject] = {}
        self._events: set[AccessEvent] = set()
        self._decisions: dict[tuple[str, str, str], TierDecision] = {}
        self._migrations: list[MigrationRecord] = []

    def initialize(self) -> None:
        return None

    def reset(self) -> None:
        self._objects.clear()
        self._events.clear()

    def add_objects(self, objects: Iterable[StorageObject]) -> int:
        count = 0
        for obj in objects:
            self._objects[(obj.bucket, obj.key)] = obj
            count += 1
        return count

    def add_access_events(self, events: Iterable[AccessEvent]) -> int:
        count = 0
        for event in events:
            self._events.add(event)
            count += 1
        return count

    def delete_access_events(self, bucket: str, keys: Sequence[str]) -> int:
        wanted = set(keys)
        doomed = {event for event in self._events if event.bucket == bucket and event.key in wanted}
        self._events -= doomed
        return len(doomed)

    def list_objects(self) -> list[StorageObject]:
        return sorted(self._objects.values(), key=lambda obj: (obj.bucket, obj.key))

    def list_access_events(self) -> list[AccessEvent]:
        return sorted(self._events, key=lambda event: (event.accessed_at, event.bucket, event.key))

    def object_count(self) -> int:
        return len(self._objects)

    def event_count(self) -> int:
        return len(self._events)

    def save_decisions(
        self, estimator: str, as_of: datetime, decisions: Sequence[TierDecision]
    ) -> int:
        for decision in decisions:
            self._decisions[(decision.object_id, estimator, as_of.isoformat())] = decision
        return len(decisions)

    def list_decisions(self, estimator: str, as_of: datetime) -> list[TierDecision]:
        matching = [
            decision
            for (object_id, decision_estimator, decision_as_of), decision in self._decisions.items()
            if decision_estimator == estimator and decision_as_of == as_of.isoformat()
        ]
        return sorted(matching, key=lambda decision: decision.expected_value, reverse=True)

    def list_latest_decisions(self) -> list[TierDecision]:
        if not self._decisions:
            return []
        latest_as_of = max(as_of for _, _, as_of in self._decisions)
        matching = [
            decision
            for (object_id, decision_estimator, decision_as_of), decision in self._decisions.items()
            if decision_as_of == latest_as_of
        ]
        return sorted(matching, key=lambda decision: decision.expected_value, reverse=True)

    def append_migration(self, record: MigrationRecord) -> None:
        self._migrations.append(record)

    def list_migrations(self, limit: int = 50) -> list[MigrationRecord]:
        return list(reversed(self._migrations))[:limit]

    def move_object(self, from_bucket: str, to_bucket: str, key: str, tier: str) -> None:
        existing = self._objects.pop((from_bucket, key), None)
        if existing is not None:
            self._objects[(to_bucket, key)] = replace(existing, bucket=to_bucket, tier=Tier(tier))
        moved = {
            replace(event, bucket=to_bucket)
            for event in self._events
            if event.bucket == from_bucket and event.key == key
        }
        for event in moved:
            self._events.discard(replace(event, bucket=from_bucket))
        self._events |= moved

    def delete_objects(self, keys: Sequence[str]) -> int:
        wanted = set(keys)
        removed = [pair for pair in self._objects if pair[1] in wanted]
        for pair in removed:
            del self._objects[pair]
        self._events = {event for event in self._events if event.key not in wanted}
        return len(removed)

    def delete_migrations(self, keys: Sequence[str]) -> int:
        wanted = set(keys)
        before = len(self._migrations)
        self._migrations = [row for row in self._migrations if row.object_key not in wanted]
        return before - len(self._migrations)

    def count_migrations(self, status: str | None = None) -> int:
        if status is None:
            return len(self._migrations)
        return sum(1 for row in self._migrations if row.status == status)


class FakeExecutionStorage:
    """In-memory StorageExecutionPort with injectable failures and call order."""

    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, tuple[bytes, str]]] = {"hot": {}, "cold": {}}
        self.calls: list[tuple[str, str]] = []
        self.fail_copy = False
        self.fail_delete = False
        self.corrupt_copy = False

    def put(self, bucket: str, key: str, payload: bytes) -> str:
        etag = md5(payload).hexdigest()
        self.buckets[bucket][key] = (payload, etag)
        return etag

    def head(self, bucket: str, key: str) -> ObjectHead | None:
        entry = self.buckets[bucket].get(key)
        if entry is None:
            return None
        payload, etag = entry
        return ObjectHead(bucket=bucket, key=key, size_bytes=len(payload), etag=etag)

    def copy_object(self, source_bucket: str, key: str, destination_bucket: str) -> str:
        self.calls.append(("copy", key))
        if self.fail_copy:
            raise StorageError("injected copy failure")
        payload, etag = self.buckets[source_bucket][key]
        if self.corrupt_copy:
            etag = "corrupted"
        self.buckets[destination_bucket][key] = (payload, etag)
        return etag

    def delete_object(self, bucket: str, key: str) -> None:
        self.calls.append(("delete", key))
        if self.fail_delete:
            raise StorageError("injected delete failure")
        del self.buckets[bucket][key]
