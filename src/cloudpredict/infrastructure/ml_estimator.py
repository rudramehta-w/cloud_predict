"""Learned estimators: XGBoost (primary) and Random Forest (comparison model)."""

from __future__ import annotations

import pickle
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_recall_curve,
    roc_auc_score,
)
from xgboost import XGBClassifier

from cloudpredict.domain.decay import DecayFrequencyEstimator
from cloudpredict.domain.exceptions import EstimatorNotTrainedError
from cloudpredict.domain.models import AccessEvent, StorageObject
from cloudpredict.infrastructure.features import (
    LABEL_COLUMN,
    OBJECT_ID_COLUMN,
    build_samples,
    encode,
    top_prefixes,
)

PROBABILITY_QUANTUM = Decimal("0.000000000001")
MODEL_NAMES = ("xgboost", "random-forest")


@dataclass(frozen=True, slots=True)
class TrainedEstimator:
    """A fitted model plus the prefix vocabulary it was encoded with."""

    name: str
    model: Any
    prefixes: list[str]
    horizon_days: int

    def estimate(
        self,
        objects: Sequence[StorageObject],
        events: Sequence[AccessEvent],
        as_of: datetime,
    ) -> dict[str, Decimal]:
        frame = build_samples(
            objects,
            events,
            cutoffs_days=[0],
            calendar_start=as_of,
            horizon_days=self.horizon_days,
            include_label=False,
        )
        if frame.empty:
            return {}
        probabilities = self.model.predict_proba(encode(frame, self.prefixes))[:, 1]
        result = {
            str(object_id): Decimal(str(round(float(probability), 12))).quantize(
                PROBABILITY_QUANTUM
            )
            for object_id, probability in zip(
                frame[OBJECT_ID_COLUMN].tolist(), probabilities, strict=True
            )
        }
        # Objects created after ``as_of`` have no feature row; fall back to the
        # heuristic prior rather than dropping them from scoring.
        fallback = DecayFrequencyEstimator(horizon_days=self.horizon_days).estimate(
            objects, events, as_of
        )
        for object_id, probability in fallback.items():
            result.setdefault(object_id, probability)
        return result


def build_classifier(name: str, seed: int = 42) -> Any:
    if name == "xgboost":
        return XGBClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.08,
            subsample=0.9,
            colsample_bytree=0.9,
            min_child_weight=2,
            eval_metric="logloss",
            random_state=seed,
            n_jobs=4,
        )
    if name == "random-forest":
        return RandomForestClassifier(
            n_estimators=300,
            max_depth=10,
            min_samples_leaf=5,
            random_state=seed,
            n_jobs=-1,
        )
    raise ValueError(f"unknown model '{name}'")


def fit_estimator(
    name: str,
    train_frame: pd.DataFrame,
    prefixes: Sequence[str],
    horizon_days: int,
    seed: int = 42,
) -> TrainedEstimator:
    model = build_classifier(name, seed=seed)
    model.fit(encode(train_frame, prefixes), train_frame[LABEL_COLUMN].to_numpy())
    return TrainedEstimator(
        name=name, model=model, prefixes=list(prefixes), horizon_days=horizon_days
    )


def compute_metrics(y_true: np.ndarray, y_score: np.ndarray) -> dict[str, object]:
    """PR-AUC (primary), ROC-AUC, Brier, a downsampled PR curve, and calibration bins."""
    y_true = np.asarray(y_true, dtype=float)
    y_score = np.asarray(y_score, dtype=float)
    metrics: dict[str, object] = {
        "pr_auc": float(average_precision_score(y_true, y_score)),
        "brier": float(brier_score_loss(y_true, y_score)),
        "samples": int(y_true.size),
        "positive_rate": float(y_true.mean()) if y_true.size else 0.0,
    }
    metrics["roc_auc"] = (
        float(roc_auc_score(y_true, y_score)) if len(np.unique(y_true)) > 1 else None
    )

    precision, recall, _ = precision_recall_curve(y_true, y_score)
    indices = np.linspace(0, len(precision) - 1, min(100, len(precision))).astype(int)
    metrics["pr_curve"] = {
        "precision": [round(float(value), 6) for value in precision[indices]],
        "recall": [round(float(value), 6) for value in recall[indices]],
    }
    metrics["calibration"] = calibration_bins(y_true, y_score)
    return metrics


def calibration_bins(
    y_true: np.ndarray, y_score: np.ndarray, bins: int = 10
) -> list[dict[str, float]]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    result: list[dict[str, float]] = []
    for index in range(bins):
        lower, upper = edges[index], edges[index + 1]
        if index == bins - 1:
            mask = (y_score >= lower) & (y_score <= upper)
        else:
            mask = (y_score >= lower) & (y_score < upper)
        count = int(mask.sum())
        if count:
            result.append(
                {
                    "predicted": round(float(y_score[mask].mean()), 4),
                    "observed": round(float(y_true[mask].mean()), 4),
                    "count": float(count),
                }
            )
    return result


def save_estimator(estimator: TrainedEstimator, models_dir: Path) -> Path:
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"{estimator.name}.pkl"
    with path.open("wb") as handle:
        pickle.dump(estimator, handle)
    return path


def load_estimator(name: str, models_dir: Path, horizon_days: int) -> TrainedEstimator:
    path = models_dir / f"{name}.pkl"
    if not path.exists():
        raise EstimatorNotTrainedError(
            f"no persisted model for '{name}' at {path}; run `cloudpredict train` first"
        )
    with path.open("rb") as handle:
        estimator: TrainedEstimator = pickle.load(handle)
    if estimator.horizon_days != horizon_days:
        raise EstimatorNotTrainedError(
            f"model '{name}' was trained for horizon {estimator.horizon_days}, "
            f"config expects {horizon_days}; retrain"
        )
    return estimator


def split_temporally(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """70/15/15 over cutoff days, never over rows (that would leak the future)."""
    cutoffs = sorted(frame["cutoff_day"].unique())
    if len(cutoffs) < 3:
        raise ValueError(f"need at least 3 cutoffs for a temporal split, got {len(cutoffs)}")
    train_end = max(1, int(len(cutoffs) * 0.70))
    validation_end = max(train_end + 1, int(len(cutoffs) * 0.85))

    train = frame[frame["cutoff_day"].isin(cutoffs[:train_end])]
    validation = frame[frame["cutoff_day"].isin(cutoffs[train_end:validation_end])]
    test = frame[frame["cutoff_day"].isin(cutoffs[validation_end:])]
    return train, validation, test


def prefix_vocabulary(train_frame: pd.DataFrame) -> list[str]:
    return top_prefixes(train_frame)
