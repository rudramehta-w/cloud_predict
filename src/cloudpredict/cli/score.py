"""``cloudpredict score``: estimate Access Probability and price every candidate move."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from cloudpredict.domain.ev import FinOpsEngine
from cloudpredict.domain.exceptions import CloudPredictError
from cloudpredict.domain.models import AccessEvent, StorageObject
from cloudpredict.infrastructure.config import DEFAULT_CONFIG_PATH, load_config
from cloudpredict.infrastructure.csvio import parse_timestamp
from cloudpredict.infrastructure.estimators import (
    HEURISTIC,
    RANDOM_FOREST,
    XGBOOST,
    build_estimator,
)
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

DEFAULT_DB = Path("data/cloudpredict.db")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("score", help="score Objects and emit tier recommendations")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--estimator", choices=[HEURISTIC, XGBOOST, RANDOM_FOREST], default=HEURISTIC
    )
    parser.add_argument("--as-of", type=str, default=None, help="ISO-8601 decision point")
    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    repository = SqliteRepository(args.db)
    try:
        repository.initialize()
        objects = repository.list_objects()
        if not objects:
            raise CloudPredictError(f"no Objects in {args.db}; run `cloudpredict ingest` first")
        events = repository.list_access_events()

        as_of = _resolve_as_of(args.as_of, objects, events)
        estimator = build_estimator(args.estimator, config)
        probabilities = estimator.estimate(objects, events, as_of)
        engine = FinOpsEngine(
            config.tiers,
            min_ev_margin=config.min_ev_margin,
            scoring_horizon_days=config.scoring_horizon_days,
        )

        decisions = []
        for obj in objects:
            decision = engine.best_candidate(obj, probabilities[obj.object_id])
            if decision is not None:
                decisions.append(decision)
        repository.save_decisions(estimator.name, as_of, decisions)

        recommended = [decision for decision in decisions if decision.recommended]
        total_ev = sum((decision.expected_value for decision in recommended), Decimal("0"))
    finally:
        repository.close()

    print(
        f"scored {len(objects)} Objects as of {as_of.isoformat()} with {estimator.name}: "
        f"{len(recommended)} recommendations, total EV {total_ev:.2f} USD"
    )
    return 0


def _resolve_as_of(
    raw: str | None,
    objects: Sequence[StorageObject],
    events: Sequence[AccessEvent],
) -> datetime:
    if raw:
        return parse_timestamp(raw)
    if events:
        return max(event.accessed_at for event in events)
    return max(obj.created_at for obj in objects)
