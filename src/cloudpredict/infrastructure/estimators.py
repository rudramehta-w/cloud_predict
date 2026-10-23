"""Estimator factory: maps the CLI choice to an AccessEstimatorPort implementation."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from cloudpredict.domain.decay import DecayFrequencyEstimator
from cloudpredict.domain.exceptions import EstimatorNotTrainedError
from cloudpredict.domain.ports import AccessEstimatorPort
from cloudpredict.infrastructure.config import Config

HEURISTIC = "heuristic"
XGBOOST = "xgboost"
RANDOM_FOREST = "random-forest"
MODELS_DIR = Path("data/models")


def build_estimator(
    name: str, config: Config, models_dir: Path = MODELS_DIR
) -> AccessEstimatorPort:
    if name == HEURISTIC:
        return DecayFrequencyEstimator(horizon_days=config.scoring_horizon_days)
    if name in (XGBOOST, RANDOM_FOREST):
        from cloudpredict.infrastructure.ml_estimator import load_estimator

        return cast(
            AccessEstimatorPort,
            load_estimator(name, models_dir, horizon_days=config.scoring_horizon_days),
        )
    raise EstimatorNotTrainedError(f"unknown estimator '{name}'")
