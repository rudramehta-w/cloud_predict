"""Bridge helpers: synthetic generator output -> domain entities."""

from __future__ import annotations

from collections.abc import Sequence

from cloudpredict.domain.models import AccessEvent, StorageObject, Tier
from cloudpredict.infrastructure.generator import (
    GeneratorParams,
    SyntheticAccess,
    SyntheticObject,
    generate,
)


def to_domain_objects(objects: Sequence[SyntheticObject]) -> list[StorageObject]:
    return [
        StorageObject(
            bucket=obj.bucket,
            key=obj.key,
            size_bytes=obj.size_bytes,
            created_at=obj.created_at,
            tier=Tier(obj.tier),
        )
        for obj in objects
    ]


def to_domain_events(events: Sequence[SyntheticAccess]) -> list[AccessEvent]:
    return [
        AccessEvent(bucket=event.bucket, key=event.key, accessed_at=event.accessed_at)
        for event in events
    ]


def synthetic_domain(
    n_objects: int = 200, days: int = 120, seed: int = 5
) -> tuple[list[StorageObject], list[AccessEvent]]:
    objects, events = generate(GeneratorParams(n_objects=n_objects, days=days, seed=seed))
    return to_domain_objects(objects), to_domain_events(events)
