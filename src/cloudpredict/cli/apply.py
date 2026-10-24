"""``cloudpredict apply``: physically move recommended Objects from hot to cold."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cloudpredict.domain.exceptions import UnsupportedSourceError
from cloudpredict.infrastructure.apply import (
    DEFAULT_DB,
    DEFAULT_MODELS_DIR,
    ApplyProgress,
    run_apply,
)
from cloudpredict.infrastructure.config import DEFAULT_CONFIG_PATH


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "apply", help="copy recommended Objects to cold, verify, then delete from hot"
    )
    parser.add_argument("--source", choices=["minio"], default="minio")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without moving")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument(
        "--estimator", choices=["auto", "xgboost", "random-forest", "heuristic"], default="auto"
    )
    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    if args.source != "minio":
        raise UnsupportedSourceError(f"unsupported apply source '{args.source}'")

    def reporter(progress: ApplyProgress) -> None:
        print(progress.message)

    summary = run_apply(
        args.config,
        args.db,
        args.models_dir,
        dry_run=args.dry_run,
        estimator_name=args.estimator,
        on_progress=reporter,
    )
    mode = "dry-run" if summary["dry_run"] else "apply"
    print(
        f"{mode}: scanned {summary['scanned']} objects with {summary['estimator']}, "
        f"{summary['plans']} recommended, {summary['moved']} moved, "
        f"{summary['skipped']} skipped, {summary['failed']} failed, "
        f"{summary['needs_cleanup']} needs-cleanup, "
        f"saved ${summary['savings_usd']}"
    )
    return 0
