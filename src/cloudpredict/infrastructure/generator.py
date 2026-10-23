"""Synthetic Zipfian access-log generator (ticket 01).

Deterministic under a seed: identical parameters produce byte-identical CSVs,
so every number reported by the benchmark is reproducible.
"""

from __future__ import annotations

import csv
import json
import math
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

MEBIBYTE = 1024 * 1024
GIBIBYTE = 1024 * MEBIBYTE
BUCKET = "demo-bucket"
CALENDAR_START = datetime(2026, 1, 1, tzinfo=UTC)
PREFIXES = ("datasets", "models", "logs", "backups", "exports")
PREFIX_WEIGHTS = (0.40, 0.25, 0.15, 0.12, 0.08)
EXTENSIONS = ("parquet", "csv", "bin", "json")


@dataclass(frozen=True, slots=True)
class GeneratorParams:
    """Knobs for one synthetic run; defaults are the agreed v1 parameters."""

    n_objects: int = 5000
    days: int = 180
    alpha: float = 1.1
    seed: int = 42
    horizon_days: int = 30
    target_positive_fraction: float = 0.20
    max_creation_day: int | None = None


@dataclass(frozen=True, slots=True)
class SyntheticObject:
    bucket: str
    key: str
    size_bytes: int
    created_at: datetime
    tier: str


@dataclass(frozen=True, slots=True)
class SyntheticAccess:
    bucket: str
    key: str
    accessed_at: datetime


def zipf_weights(n_objects: int, alpha: float) -> list[float]:
    """Normalised Zipf popularity weights, hottest Object first."""
    raw = [1.0 / (rank + 1) ** alpha for rank in range(n_objects)]
    total = sum(raw)
    return [value / total for value in raw]


def calibrate_rate(weights: Sequence[float], horizon_days: int, target: float) -> float:
    """Bisect the global rate so the expected hit fraction reaches ``target``."""
    if not 0.0 < target < 1.0:
        raise ValueError(f"target positive fraction must be in (0, 1), got {target}")

    def expected(scale: float) -> float:
        return sum(1.0 - math.exp(-scale * weight * horizon_days) for weight in weights) / len(
            weights
        )

    low, high = 1e-6, 1e4
    for _ in range(200):
        mid = (low + high) / 2
        if expected(mid) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0.0:
        return 0
    if lam > 50.0:
        # Normal approximation keeps the hottest Objects cheap to sample.
        return max(int(round(rng.gauss(lam, math.sqrt(lam)))), 0)
    threshold = math.exp(-lam)
    count = 0
    product = 1.0
    while True:
        product *= rng.random()
        if product <= threshold:
            return count
        count += 1


def generate(params: GeneratorParams) -> tuple[list[SyntheticObject], list[SyntheticAccess]]:
    rng = random.Random(params.seed)
    weights = zipf_weights(params.n_objects, params.alpha)
    rate_scale = calibrate_rate(weights, params.horizon_days, params.target_positive_fraction)

    objects: list[SyntheticObject] = []
    creation_window = (
        params.max_creation_day if params.max_creation_day is not None else int(params.days * 2 / 3)
    )
    for index in range(params.n_objects):
        prefix = rng.choices(PREFIXES, weights=PREFIX_WEIGHTS, k=1)[0]
        extension = rng.choice(EXTENSIONS)
        creation_day = rng.uniform(0.0, creation_window)
        size_bytes = int(math.exp(rng.gauss(math.log(64 * MEBIBYTE), 1.2)))
        size_bytes = min(max(size_bytes, MEBIBYTE), 100 * GIBIBYTE)
        objects.append(
            SyntheticObject(
                bucket=BUCKET,
                key=f"{prefix}/batch-{index // 250:02d}/object-{index:05d}.{extension}",
                size_bytes=size_bytes,
                created_at=CALENDAR_START + timedelta(days=creation_day),
                tier="HOT",
            )
        )

    accesses: list[SyntheticAccess] = []
    for obj, weight in zip(objects, weights, strict=True):
        creation_day = (obj.created_at - CALENDAR_START).total_seconds() / 86400.0
        exposure_days = params.days - creation_day
        lam = rate_scale * weight * exposure_days
        for _ in range(_poisson(rng, lam)):
            offset = rng.uniform(creation_day, params.days)
            accessed_at = CALENDAR_START + timedelta(days=offset)
            accesses.append(SyntheticAccess(obj.bucket, obj.key, accessed_at))

    accesses.sort(key=lambda event: (event.accessed_at, event.key))
    return objects, accesses


def positive_fraction(
    objects: Sequence[SyntheticObject],
    accesses: Sequence[SyntheticAccess],
    params: GeneratorParams,
) -> float:
    """Fraction of Objects read at least once during the final Scoring Horizon."""
    window_start = CALENDAR_START + timedelta(days=params.days - params.horizon_days)
    touched = {event.key for event in accesses if event.accessed_at > window_start}
    return len(touched) / len(objects)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def write_outputs(
    out_dir: Path,
    objects: Sequence[SyntheticObject],
    accesses: Sequence[SyntheticAccess],
    params: GeneratorParams,
) -> dict[str, object]:
    """Write the canonical CSVs plus a manifest; returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / "objects.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "key", "size_bytes", "created_at", "tier"])
        for obj in objects:
            writer.writerow(
                [obj.bucket, obj.key, obj.size_bytes, _format_time(obj.created_at), obj.tier]
            )

    with (out_dir / "accesses.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "key", "accessed_at"])
        for event in accesses:
            writer.writerow([event.bucket, event.key, _format_time(event.accessed_at)])

    manifest: dict[str, object] = {
        "params": asdict(params),
        "objects": len(objects),
        "access_events": len(accesses),
        "positive_fraction": round(positive_fraction(objects, accesses, params), 4),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
