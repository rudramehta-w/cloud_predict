"""Ports: the seams between the pure domain and its adapters (ADR 0002)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from cloudpredict.domain.models import AccessEvent, StorageObject


class StoragePort(Protocol):
    """Reads Object inventory from an object store; never mutates it."""

    def list_objects(self, bucket: str) -> list[StorageObject]: ...


class RepositoryPort(Protocol):
    """Persists Objects, access events, and (later) scored decisions."""

    def initialize(self) -> None: ...

    def reset(self) -> None: ...

    def add_objects(self, objects: Iterable[StorageObject]) -> int: ...

    def add_access_events(self, events: Iterable[AccessEvent]) -> int: ...

    def list_objects(self) -> list[StorageObject]: ...

    def list_access_events(self) -> list[AccessEvent]: ...

    def object_count(self) -> int: ...

    def event_count(self) -> int: ...


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
