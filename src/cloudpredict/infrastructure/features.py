"""Feature engineering for the learned estimators (spec: Estimator & features).

Excluded by design, and deliberately absent from ``NUMERIC_FEATURES``:

- absolute timestamps — they leak the temporal split boundary;
- lifetime access count — a proxy for age, not recency;
- current Storage Tier — a policy output, so it would leak the target.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

import pandas as pd

from cloudpredict.domain.models import AccessEvent, StorageObject

NUMERIC_FEATURES = (
    "days_since_last_access",
    "accesses_7d",
    "accesses_30d",
    "accesses_90d",
    "lifetime_rate_per_day",
    "age_days",
    "log_size_bytes",
    "trend_ratio",
)
PREFIX_COLUMN = "key_prefix"
LABEL_COLUMN = "accessed_next_horizon"
OBJECT_ID_COLUMN = "object_id"
CUTOFF_COLUMN = "cutoff_day"
TOP_PREFIX_COUNT = 20
SECONDS_PER_DAY = 86400.0


def key_prefix(key: str) -> str:
    return key.split("/", 1)[0]


def temporal_cutoffs(days: int, horizon_days: int, step_days: int = 6) -> list[int]:
    """Cutoff days whose label window still fits inside the simulated span."""
    first = max(horizon_days, days // 10)
    last = days - horizon_days
    if last < first:
        return []
    return list(range(first, last + 1, step_days))


def _days_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / SECONDS_PER_DAY


def _events_by_object(events: Sequence[AccessEvent]) -> dict[str, list[datetime]]:
    grouped: dict[str, list[datetime]] = defaultdict(list)
    for event in events:
        grouped[event.object_id].append(event.accessed_at)
    for times in grouped.values():
        times.sort()
    return grouped


def _row_for(
    obj: StorageObject,
    times: Sequence[datetime],
    cutoff: datetime,
    horizon_days: int,
    include_label: bool,
) -> dict[str, object] | None:
    if obj.created_at > cutoff:
        return None

    index_now = bisect_right(times, cutoff)
    last_access = times[index_now - 1] if index_now else None
    age_days = max(_days_between(cutoff, obj.created_at), 1.0)
    days_since_last = _days_between(cutoff, last_access) if last_access else age_days

    accesses_7d = index_now - bisect_right(times, cutoff - timedelta(days=7))
    accesses_30d = index_now - bisect_right(times, cutoff - timedelta(days=30))
    accesses_90d = index_now - bisect_right(times, cutoff - timedelta(days=90))
    accesses_prev_30d = bisect_right(times, cutoff - timedelta(days=30)) - bisect_right(
        times, cutoff - timedelta(days=60)
    )

    label = 0
    if include_label:
        future_index = bisect_right(times, cutoff + timedelta(days=horizon_days))
        label = int(future_index > index_now)

    row: dict[str, object] = {
        OBJECT_ID_COLUMN: obj.object_id,
        PREFIX_COLUMN: key_prefix(obj.key),
        LABEL_COLUMN: label,
        "days_since_last_access": days_since_last,
        "accesses_7d": float(accesses_7d),
        "accesses_30d": float(accesses_30d),
        "accesses_90d": float(accesses_90d),
        "lifetime_rate_per_day": index_now / age_days,
        "age_days": age_days,
        "log_size_bytes": math.log1p(obj.size_bytes),
        "trend_ratio": (accesses_30d + 1.0) / (accesses_prev_30d + 1.0),
    }
    return row


def build_samples(
    objects: Sequence[StorageObject],
    events: Sequence[AccessEvent],
    cutoffs_days: Sequence[int],
    calendar_start: datetime,
    horizon_days: int,
    include_label: bool = True,
) -> pd.DataFrame:
    """One row per (Object, cutoff); features use only data at or before the cutoff."""
    grouped = _events_by_object(events)
    rows: list[dict[str, object]] = []
    for cutoff_day in cutoffs_days:
        cutoff = calendar_start + timedelta(days=cutoff_day)
        for obj in objects:
            row = _row_for(obj, grouped.get(obj.object_id, []), cutoff, horizon_days, include_label)
            if row is None:
                continue
            row[CUTOFF_COLUMN] = cutoff_day
            rows.append(row)
    return pd.DataFrame(rows)


def top_prefixes(frame: pd.DataFrame, limit: int = TOP_PREFIX_COUNT) -> list[str]:
    counts = frame[PREFIX_COLUMN].value_counts()
    return [str(prefix) for prefix in counts.head(limit).index]


def encode(frame: pd.DataFrame, prefixes: Sequence[str]) -> pd.DataFrame:
    """Numeric features plus one-hot key prefixes; column order is stable."""
    encoded = frame.loc[:, list(NUMERIC_FEATURES)].astype(float).copy()
    for prefix in prefixes:
        encoded[f"prefix_{prefix}"] = (frame[PREFIX_COLUMN] == prefix).astype(float)
    return encoded
