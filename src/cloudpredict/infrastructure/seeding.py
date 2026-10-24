"""Seed the hot bucket with real files and a deterministic access history."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cloudpredict.domain.models import AccessEvent, StorageObject, Tier
from cloudpredict.infrastructure.generator import (
    PREFIX_WEIGHTS,
    PREFIXES,
    calibrate_rate,
    sample_poisson,
    zipf_weights,
)
from cloudpredict.infrastructure.silo_storage import SiloStorage
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

DEFAULT_COUNT = 30
DEFAULT_SIZE_RANGE_MIB = (20, 100)
DEFAULT_HISTORY_DAYS = 90
DEFAULT_SEED = 42
POSITIVE_FRACTION = 0.20
HORIZON_DAYS = 30
MIB = 1024**2

NAME_STEMS: dict[str, tuple[str, ...]] = {
    "datasets": ("customer-orders", "clickstream", "sensor-telemetry"),
    "models": ("recommender-weights", "churn-classifier", "forecast-checkpoint"),
    "logs": ("api-access", "worker-trace", "audit-events"),
    "backups": ("nightly-snapshot", "postgres-dump", "config-archive"),
    "exports": ("finance-export", "crm-export", "usage-report"),
}
EXTENSIONS = {
    "datasets": "parquet",
    "models": "bin",
    "logs": "json",
    "backups": "tar",
    "exports": "csv",
}


@dataclass(frozen=True, slots=True)
class SeedPlan:
    key: str
    size_bytes: int


def plan_objects(
    count: int = DEFAULT_COUNT,
    seed: int = DEFAULT_SEED,
    size_range_mib: tuple[int, int] = DEFAULT_SIZE_RANGE_MIB,
) -> list[SeedPlan]:
    """Deterministic names and sizes; no randomness outside the seeded RNG."""
    rng = random.Random(seed)
    plans: list[SeedPlan] = []
    for index in range(count):
        prefix = rng.choices(PREFIXES, weights=PREFIX_WEIGHTS, k=1)[0]
        stem = rng.choice(NAME_STEMS[prefix])
        size_bytes = rng.randint(*size_range_mib) * MIB
        plans.append(
            SeedPlan(
                key=f"{prefix}/{stem}-{index:03d}.{EXTENSIONS[prefix]}",
                size_bytes=size_bytes,
            )
        )
    return plans


def write_sparse_file(path: Path, size_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size_bytes)


def simulate_accesses(
    plans: list[SeedPlan],
    created_at: datetime,
    anchor: datetime,
    seed: int = DEFAULT_SEED,
    horizon_days: int = HORIZON_DAYS,
) -> list[AccessEvent]:
    """Zipfian popularity, calibrated to the agreed positive band over the horizon."""
    rng = random.Random(seed + 1)
    weights = zipf_weights(len(plans), 1.1)
    scale = calibrate_rate(weights, horizon_days, POSITIVE_FRACTION)
    span_days = max((anchor - created_at).total_seconds() / 86400.0, 1.0)

    events: list[AccessEvent] = []
    for plan, weight in zip(plans, weights, strict=True):
        for _ in range(sample_poisson(rng, scale * weight * span_days)):
            offset_days = rng.uniform(0.0, span_days)
            events.append(
                AccessEvent(
                    bucket="hot",
                    key=plan.key,
                    accessed_at=created_at + timedelta(days=offset_days),
                )
            )
    events.sort(key=lambda event: (event.accessed_at, event.key))
    return events


def seed_hot_bucket(
    storage: SiloStorage,
    repository: SqliteRepository,
    *,
    hot_bucket: str = "hot",
    cold_bucket: str = "cold",
    count: int = DEFAULT_COUNT,
    seed: int = DEFAULT_SEED,
    size_range_mib: tuple[int, int] = DEFAULT_SIZE_RANGE_MIB,
    history_days: int = DEFAULT_HISTORY_DAYS,
    workdir: Path = Path("data/seed-files"),
    anchor: datetime | None = None,
) -> dict[str, object]:
    """Upload real files (idempotently) and replace their access history."""
    anchor = anchor or datetime.now(UTC)
    created_at = anchor - timedelta(days=history_days)
    plans = plan_objects(count=count, seed=seed, size_range_mib=size_range_mib)
    keys = [plan.key for plan in plans]

    # A re-seed resets the demo: stale cold copies and rows are cleared first.
    repository.delete_objects(keys)
    repository.delete_migrations(keys)

    uploaded = 0
    skipped = 0
    objects: list[StorageObject] = []
    for plan in plans:
        if storage.head(cold_bucket, plan.key) is not None:
            storage.delete_object(cold_bucket, plan.key)
        path = workdir / Path(plan.key).name
        if not path.exists() or path.stat().st_size != plan.size_bytes:
            write_sparse_file(path, plan.size_bytes)
        if storage.head(hot_bucket, plan.key) is None:
            storage.upload_file(hot_bucket, plan.key, path)
            uploaded += 1
        else:
            skipped += 1
        objects.append(
            StorageObject(
                bucket=hot_bucket,
                key=plan.key,
                size_bytes=plan.size_bytes,
                created_at=created_at,
                tier=Tier.HOT,
            )
        )

    repository.add_objects(objects)
    repository.delete_access_events(hot_bucket, keys)
    events = simulate_accesses(plans, created_at, anchor, seed=seed)
    repository.add_access_events(events)

    return {
        "objects": len(plans),
        "bytes": sum(plan.size_bytes for plan in plans),
        "uploaded": uploaded,
        "skipped": skipped,
        "events": len(events),
        "anchor": anchor.isoformat(),
    }
