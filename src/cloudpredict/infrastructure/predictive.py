"""Predictive metrics for the ladder.

Learned models reuse the metrics computed during ``train`` (stored in
``metrics.json``); the heuristic is recomputed on the exact same test cutoffs so
the comparison is like-for-like.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import cast

import numpy as np

from cloudpredict.domain.decay import DecayFrequencyEstimator
from cloudpredict.domain.exceptions import EstimatorNotTrainedError
from cloudpredict.domain.models import AccessEvent, StorageObject
from cloudpredict.infrastructure.features import (
    CUTOFF_COLUMN,
    LABEL_COLUMN,
    OBJECT_ID_COLUMN,
    build_samples,
    derive_calendar,
    temporal_cutoffs,
)
from cloudpredict.infrastructure.ml_estimator import compute_metrics, split_temporally

HEURISTIC_KEY = "decay-frequency"


def predictive_metrics(
    objects: Sequence[StorageObject],
    events: Sequence[AccessEvent],
    horizon_days: int,
    models_dir: Path,
) -> dict[str, dict[str, object]]:
    metrics_path = models_dir / "metrics.json"
    if not metrics_path.exists():
        raise EstimatorNotTrainedError(
            f"no metrics at {metrics_path}; run `cloudpredict train` before `report`"
        )
    trained = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics: dict[str, dict[str, object]] = {
        name: dict(payload["test"]) for name, payload in trained["models"].items()
    }

    start, span = derive_calendar(objects, events)
    cutoffs = temporal_cutoffs(span, horizon_days)
    frame = build_samples(objects, events, cutoffs, start, horizon_days)
    test_frame = split_temporally(frame)[2]

    estimator = DecayFrequencyEstimator(horizon_days=horizon_days)
    scores: list[float] = []
    for cutoff_day, group in test_frame.groupby(CUTOFF_COLUMN, sort=True):
        cutoff = start + timedelta(days=cast(int, cutoff_day))
        probabilities = estimator.estimate(objects, events, cutoff)
        scores.extend(
            float(probabilities[object_id]) for object_id in group[OBJECT_ID_COLUMN].tolist()
        )

    metrics[HEURISTIC_KEY] = compute_metrics(
        test_frame[LABEL_COLUMN].to_numpy(), np.asarray(scores, dtype=float)
    )
    return metrics
