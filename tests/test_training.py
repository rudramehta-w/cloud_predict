"""Ticket 07: training, metrics, persistence, and the CLI path."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from cloudpredict.cli.main import main
from cloudpredict.cli.train import derive_calendar
from cloudpredict.domain.models import AccessEvent, StorageObject
from cloudpredict.infrastructure.features import (
    LABEL_COLUMN,
    build_samples,
    encode,
    temporal_cutoffs,
)
from cloudpredict.infrastructure.ml_estimator import (
    compute_metrics,
    fit_estimator,
    load_estimator,
    prefix_vocabulary,
    save_estimator,
    split_temporally,
)
from tests.support.synthetic import synthetic_domain

HORIZON = 30


def _samples() -> tuple[
    tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
    pd.DataFrame,
    tuple[list[StorageObject], list[AccessEvent]],
]:
    objects, events = synthetic_domain(n_objects=250, days=120, seed=5)
    start, span = derive_calendar(objects, events)
    cutoffs = temporal_cutoffs(span, HORIZON)
    frame = build_samples(objects, events, cutoffs, start, HORIZON)
    train, validation, test = split_temporally(frame, HORIZON)
    return (train, validation, test), frame, (objects, events)


def test_split_is_temporal_and_purged() -> None:
    (train, validation, test), _, _ = _samples()

    train_cutoffs = set(train["cutoff_day"])
    validation_cutoffs = set(validation["cutoff_day"])
    test_cutoffs = set(test["cutoff_day"])

    assert train_cutoffs and test_cutoffs
    assert max(train_cutoffs) + HORIZON < min(test_cutoffs)
    if validation_cutoffs:
        assert max(train_cutoffs) + HORIZON < min(validation_cutoffs)
        assert max(validation_cutoffs) + HORIZON < min(test_cutoffs)


def test_models_train_and_metrics_are_sane() -> None:
    (train, _, test), _, _ = _samples()
    prefixes = prefix_vocabulary(train)

    for name in ("xgboost", "random-forest"):
        estimator = fit_estimator(name, train, prefixes, horizon_days=HORIZON, seed=42)
        probabilities = estimator.model.predict_proba(encode(test, prefixes))[:, 1]
        metrics = compute_metrics(test[LABEL_COLUMN].to_numpy(), probabilities)

        assert 0.0 <= float(metrics["pr_auc"]) <= 1.0
        assert 0.0 <= float(metrics["brier"]) <= 1.0
        assert metrics["pr_curve"]["precision"]
        assert metrics["pr_curve"]["recall"]
        assert metrics["calibration"]


def test_estimator_round_trips_through_disk(tmp_path: Path) -> None:
    (train, _, _), _, (objects, events) = _samples()
    prefixes = prefix_vocabulary(train)
    estimator = fit_estimator("xgboost", train, prefixes, horizon_days=HORIZON, seed=42)
    save_estimator(estimator, tmp_path)

    loaded = load_estimator("xgboost", tmp_path, horizon_days=HORIZON)
    as_of = max(event.accessed_at for event in events)
    probabilities = loaded.estimate(objects, events, as_of)

    assert len(probabilities) == len(objects)
    assert all(0 <= probability <= 1 for probability in probabilities.values())


def test_train_cli_writes_models_and_metrics(tmp_path: Path) -> None:
    from cloudpredict.infrastructure.generator import GeneratorParams, generate, write_outputs

    params = GeneratorParams(n_objects=150, days=120, seed=9)
    write_outputs(tmp_path / "synthetic", *generate(params), params)
    db_path = tmp_path / "db.sqlite"
    models_dir = tmp_path / "models"
    main(
        [
            "ingest",
            "--source",
            "file",
            "--input",
            str(tmp_path / "synthetic"),
            "--db",
            str(db_path),
        ]
    )

    exit_code = main(["train", "--db", str(db_path), "--models-dir", str(models_dir)])

    assert exit_code == 0
    assert (models_dir / "xgboost.pkl").exists()
    assert (models_dir / "random-forest.pkl").exists()
    assert (models_dir / "metrics.json").exists()


@pytest.mark.parametrize("name", ["xgboost", "random-forest"])
def test_unknown_estimator_is_rejected(tmp_path: Path, name: str) -> None:
    from cloudpredict.domain.exceptions import EstimatorNotTrainedError

    with pytest.raises(EstimatorNotTrainedError):
        load_estimator(name, tmp_path / "missing", horizon_days=HORIZON)
