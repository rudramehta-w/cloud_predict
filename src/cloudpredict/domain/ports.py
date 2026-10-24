"""Ports: the seams between the pure domain and its adapters (ADR 0002)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from cloudpredict.domain.models import (
    AccessEvent,
    MigrationRecord,
    ObjectHead,
    StorageObject,
    TierDecision,
)


class StoragePort(Protocol):
    """Reads Object inventory from an object store; never mutates it."""

    def list_objects(self, bucket: str) -> list[StorageObject]: ...


class StorageExecutionPort(Protocol):
    """The mutating half of a store, used only by the apply engine."""

    def head(self, bucket: str, key: str) -> ObjectHead | None: ...

    def copy_object(self, source_bucket: str, key: str, destination_bucket: str) -> str:
        """Copy and return the destination ETag."""
        ...

    def delete_object(self, bucket: str, key: str) -> None: ...


class RepositoryPort(Protocol):
    """Persists Objects, access events, and (later) scored decisions."""

    def initialize(self) -> None: ...

    def reset(self) -> None: ...

    def add_objects(self, objects: Iterable[StorageObject]) -> int: ...

    def add_access_events(self, events: Iterable[AccessEvent]) -> int: ...

    def delete_access_events(self, bucket: str, keys: Sequence[str]) -> int: ...

    def list_objects(self) -> list[StorageObject]: ...

    def list_access_events(self) -> list[AccessEvent]: ...

    def object_count(self) -> int: ...

    def event_count(self) -> int: ...

    def save_decisions(
        self, estimator: str, as_of: datetime, decisions: Sequence[TierDecision]
    ) -> int: ...

    def list_decisions(self, estimator: str, as_of: datetime) -> list[TierDecision]: ...

    def append_migration(self, record: MigrationRecord) -> None: ...

    def list_migrations(self, limit: int = 50) -> list[MigrationRecord]: ...

    def move_object(self, from_bucket: str, to_bucket: str, key: str, tier: str) -> None: ...

    def delete_objects(self, keys: Sequence[str]) -> int: ...

    def delete_migrations(self, keys: Sequence[str]) -> int: ...

    def count_migrations(self, status: str | None = None) -> int: ...


class AccessEstimatorPort(Protocol):
    """Estimates Access Probability per Object at a point in time."""

    @property
    def name(self) -> str: ...

    def estimate(
        self,
        objects: Sequence[StorageObject],
        events: Sequence[AccessEvent],
        as_of: datetime,
    ) -> dict[str, Decimal]:
        """Return Access Probability keyed by ``object_id``."""
        ...
