"""``cloudpredict report``: evaluate the ladder and render the offline HTML artifact."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from cloudpredict.domain.decay import DecayFrequencyEstimator
from cloudpredict.domain.ev import FinOpsEngine
from cloudpredict.domain.exceptions import CloudPredictError
from cloudpredict.infrastructure.config import DEFAULT_CONFIG_PATH, load_config
from cloudpredict.infrastructure.evaluation import (
    Rung,
    decision_point,
    estimator_moves,
    naive_moves,
    run_ladder,
)
from cloudpredict.infrastructure.report_html import render_report
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

DEFAULT_DB = Path("data/cloudpredict.db")
DEFAULT_OUT = Path("data/report")
HEURISTIC = "decay-frequency"


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("report", help="evaluate the ladder and write the HTML report")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--top", type=int, default=25, help="recommendations shown in the report")
    parser.set_defaults(func=_run)


def build_rungs(
    objects: Any,
    events: Any,
    engine: FinOpsEngine,
    horizon_days: int,
) -> tuple[list[Rung], dict[str, Decimal]]:
    """Rungs 0–2; ticket 08 appends the learned rungs and the Oracle."""
    at = decision_point(events) - timedelta(days=horizon_days)
    heuristic = DecayFrequencyEstimator(horizon_days=horizon_days)
    probabilities = heuristic.estimate(objects, events, at)
    rungs = [
        Rung("no-tiering", "0 · no tiering", {}),
        Rung("naive-30d", "1 · naive 30-day rule", naive_moves(objects, events, at)),
        Rung(
            "decay-frequency",
            "2 · decay-frequency heuristic",
            estimator_moves(objects, probabilities, engine),
        ),
    ]
    return rungs, probabilities


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    horizon_days = config.scoring_horizon_days

    repository = SqliteRepository(args.db)
    try:
        repository.initialize()
        objects = repository.list_objects()
        events = repository.list_access_events()
        if not objects or not events:
            raise CloudPredictError(f"{args.db} has no data; run `generate` and `ingest` first")
    finally:
        repository.close()

    # The evaluation holds out the final Scoring Horizon: the decision point is
    # last_event - horizon, so the label window fits inside the observed data.
    at = decision_point(events) - timedelta(days=horizon_days)
    engine = FinOpsEngine(config.tiers, config.min_ev_margin, horizon_days)
    rungs, probabilities = build_rungs(objects, events, engine, horizon_days)
    results = run_ladder(rungs, objects, events, at, config.tiers, horizon_days)

    candidates = [
        decision
        for obj in objects
        if (decision := engine.best_candidate(obj, probabilities[obj.object_id])) is not None
        and decision.recommended
    ]
    candidates.sort(key=lambda decision: decision.expected_value, reverse=True)
    top = candidates[: max(args.top, 0)]
    artifact: dict[str, Any] = {
        "run": {
            "decision_time": at.isoformat(),
            "horizon_days": horizon_days,
            "min_ev_margin": str(config.min_ev_margin),
            "objects": len(objects),
            "pricing": {
                "version": config.pricing.version,
                "source": config.pricing.source,
                "region": config.pricing.region,
                "captured_on": config.pricing.captured_on,
            },
            "seed": config.generator.seed,
            "estimator": HEURISTIC,
        },
        "ladder": [result.to_dict() for result in results],
        "recommendations": [
            {
                "object_id": decision.object_id,
                "probability": float(decision.access_probability),
                "from_tier": decision.from_tier.value,
                "to_tier": decision.to_tier.value,
                "storage_savings": str(decision.storage_savings.quantize(Decimal("0.01"))),
                "retrieval_penalty": str(decision.retrieval_penalty.quantize(Decimal("0.01"))),
                "expected_value": str(decision.expected_value.quantize(Decimal("0.01"))),
            }
            for decision in top
        ],
        "charts": [],
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    (args.out / "report.html").write_text(render_report(artifact), encoding="utf-8")

    print(
        f"report written to {args.out / 'report.html'}: "
        f"{len(results)} rungs, {len(candidates)} recommendations ({len(top)} shown)"
    )
    return 0
