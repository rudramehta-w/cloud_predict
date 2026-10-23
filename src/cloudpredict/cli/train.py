"""``cloudpredict train``: fit XGBoost and Random Forest on a temporal split."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from cloudpredict.domain.exceptions import CloudPredictError
from cloudpredict.domain.models import AccessEvent, StorageObject
from cloudpredict.infrastructure.config import DEFAULT_CONFIG_PATH, load_config
from cloudpredict.infrastructure.features import (
    LABEL_COLUMN,
    build_samples,
    encode,
    temporal_cutoffs,
)
from cloudpredict.infrastructure.ml_estimator import (
    MODEL_NAMES,
    TrainedEstimator,
    compute_metrics,
    fit_estimator,
    prefix_vocabulary,
    save_estimator,
    split_temporally,
)
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

DEFAULT_DB = Path("data/cloudpredict.db")
DEFAULT_MODELS_DIR = Path("data/models")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("train", help="fit the learned Access Probability estimators")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--seed", type=int, default=42)
    parser.set_defaults(func=_run)


def derive_calendar(
    objects: Sequence[StorageObject], events: Sequence[AccessEvent]
) -> tuple[datetime, int]:
    times = [obj.created_at for obj in objects] + [event.accessed_at for event in events]
    start = min(times).replace(hour=0, minute=0, second=0, microsecond=0)
    span_days = (max(times) - start).days
    return start, span_days


def _predict(estimator: TrainedEstimator, frame: pd.DataFrame) -> np.ndarray:
    encoded = encode(frame, estimator.prefixes)
    probabilities: np.ndarray = estimator.model.predict_proba(encoded)[:, 1]
    return probabilities


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    horizon_days = config.generator.horizon_days

    repository = SqliteRepository(args.db)
    try:
        repository.initialize()
        objects = repository.list_objects()
        events = repository.list_access_events()
    finally:
        repository.close()

    if not objects or not events:
        raise CloudPredictError(
            f"{args.db} has no Objects or access events; run `generate` and `ingest` first"
        )

    calendar_start, span_days = derive_calendar(objects, events)
    cutoffs = temporal_cutoffs(span_days, horizon_days)
    if not cutoffs:
        raise CloudPredictError(
            f"span of {span_days} days cannot host a {horizon_days}-day label window"
        )

    frame = build_samples(objects, events, cutoffs, calendar_start, horizon_days)
    train_frame, validation_frame, test_frame = split_temporally(frame)
    prefixes = prefix_vocabulary(train_frame)

    metrics: dict[str, object] = {
        "run": {
            "seed": args.seed,
            "horizon_days": horizon_days,
            "span_days": span_days,
            "calendar_start": calendar_start.isoformat(),
            "cutoffs": cutoffs,
            "samples": {
                "train": int(len(train_frame)),
                "validation": int(len(validation_frame)),
                "test": int(len(test_frame)),
            },
        },
        "models": {},
    }
    model_metrics: dict[str, object] = {}
    summary: list[str] = []
    for name in MODEL_NAMES:
        estimator = fit_estimator(
            name, train_frame, prefixes, horizon_days=horizon_days, seed=args.seed
        )
        save_estimator(estimator, args.models_dir)

        validation_metrics = compute_metrics(
            validation_frame[LABEL_COLUMN].to_numpy(), _predict(estimator, validation_frame)
        )
        test_metrics = compute_metrics(
            test_frame[LABEL_COLUMN].to_numpy(), _predict(estimator, test_frame)
        )
        model_metrics[name] = {"validation": validation_metrics, "test": test_metrics}
        summary.append(f"{name} (PR-AUC {cast(float, test_metrics['pr_auc']):.3f})")

    metrics["models"] = model_metrics
    args.models_dir.mkdir(parents=True, exist_ok=True)
    (args.models_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"trained {' and '.join(summary)} on {len(frame)} samples "
        f"({len(cutoffs)} cutoffs); models and metrics -> {args.models_dir}"
    )
    return 0
