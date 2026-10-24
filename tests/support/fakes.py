"""Test-only fakes: one per Port, so contract tests run against real and fake alike."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from cloudpredict.domain.models import AccessEvent, StorageObject, TierDecision


class InMemoryRepository:
    """In-memory RepositoryPort implementation for contract tests."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], StorageObject] = {}
        self._events: set[AccessEvent] = set()
        self._decisions: dict[tuple[str, str, str], TierDecision] = {}

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
