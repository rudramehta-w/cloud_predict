"""Ticket 07: the feature set matches the spec, and no future data leaks in."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from cloudpredict.domain.models import AccessEvent, StorageObject
from cloudpredict.infrastructure.features import (
    CUTOFF_COLUMN,
    LABEL_COLUMN,
    NUMERIC_FEATURES,
    OBJECT_ID_COLUMN,
    PREFIX_COLUMN,
    build_samples,
    key_prefix,
    temporal_cutoffs,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def _object(key: str = "datasets/a.bin", created_day: float = 0.0) -> StorageObject:
    return StorageObject(
        bucket="b",
        key=key,
        size_bytes=1024,
        created_at=START + timedelta(days=created_day),
    )


def _event(key: str = "datasets/a.bin", day: float = 0.0) -> AccessEvent:
    return AccessEvent(bucket="b", key=key, accessed_at=START + timedelta(days=day))


def test_feature_columns_are_exactly_the_agreed_set() -> None:
    frame = build_samples([_object()], [_event(day=5)], [30], START, horizon_days=30)

    assert set(frame.columns) == {
        OBJECT_ID_COLUMN,
        PREFIX_COLUMN,
        LABEL_COLUMN,
        CUTOFF_COLUMN,
        *NUMERIC_FEATURES,
    }
    # The three exclusions, asserted as absent rather than merely undocumented.
    assert "tier" not in frame.columns
    assert "lifetime_access_count" not in frame.columns
    assert "timestamp" not in frame.columns


def test_label_uses_the_forward_window_only() -> None:
    obj = _object()
    inside = build_samples([obj], [_event(day=45)], [30], START, horizon_days=30)
    outside = build_samples([obj], [_event(day=61)], [30], START, horizon_days=30)

    assert inside[LABEL_COLUMN].iloc[0] == 1
    assert outside[LABEL_COLUMN].iloc[0] == 0


def test_future_events_do_not_change_features() -> None:
    obj = _object()
    before = build_samples([obj], [_event(day=50)], [30], START, horizon_days=30)
    after = build_samples([obj], [_event(day=50), _event(day=35)], [30], START, horizon_days=30)

    pd.testing.assert_frame_equal(
        before.loc[:, list(NUMERIC_FEATURES)], after.loc[:, list(NUMERIC_FEATURES)]
    )


def test_objects_created_after_the_cutoff_are_skipped() -> None:
    frame = build_samples([_object(created_day=40)], [], [30], START, horizon_days=30)

    assert frame.empty


def test_key_prefix_is_the_first_path_segment() -> None:
    assert key_prefix("models/batch-00/x.parquet") == "models"
    assert key_prefix("toplevel.bin") == "toplevel.bin"


def test_cutoffs_leave_room_for_the_label_window() -> None:
    cutoffs = temporal_cutoffs(days=180, horizon_days=30)

    assert cutoffs[0] >= 30
    assert cutoffs[-1] + 30 <= 180
