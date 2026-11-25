"""``cloudpredict daemon``: autonomous background storage reconciliation loop."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cloudpredict.infrastructure.apply import DEFAULT_DB, DEFAULT_MODELS_DIR, run_apply
from cloudpredict.infrastructure.config import DEFAULT_CONFIG_PATH
from cloudpredict.infrastructure.enterprise_export import build_s3_lifecycle_rules
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "daemon", help="run autonomous background storage reconciliation loop"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="seconds between reconciliation cycles (default: 3600)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="number of cycles before exit (default: 0 = run indefinitely)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="simulate transitions without modifying storage",
    )
    parser.add_argument(
        "--estimator",
        choices=["auto", "heuristic", "xgboost", "random-forest"],
        default="auto",
        help="access estimator to use (default: auto)",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument(
        "--export-lifecycle",
        type=Path,
        default=None,
        help="file path to write AWS S3 Lifecycle Configuration JSON at each cycle",
    )
    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    running = True

    def _handle_exit(sig: int, frame: Any) -> None:
        nonlocal running
        print("\n[daemon] Shutdown signal received. Exiting gracefully...", file=sys.stderr)
        running = False

    signal.signal(signal.SIGINT, _handle_exit)
    signal.signal(signal.SIGTERM, _handle_exit)

    mode_label = "DRY-RUN SIMULATION" if args.dry_run else "ACTIVE STORAGE MIGRATION"
    print("=" * 70)
    print("  cloudPredict Autonomous Reconciliation Daemon")
    print(f"  Mode:        {mode_label}")
    print(f"  Interval:    Every {args.interval}s")
    print(f"  Estimator:   {args.estimator}")
    print(f"  Database:    {args.db}")
    print(f"  Config:      {args.config}")
    if args.export_lifecycle:
        print(f"  Export IaC:  {args.export_lifecycle}")
    print("=" * 70)
    print("  Daemon active. Press Ctrl+C to terminate.\n")

    cycle = 0
    while running:
        cycle += 1
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"[{now_str}] [Cycle #{cycle}] Starting storage reconciliation...")

        try:
            summary = run_apply(
                args.config,
                args.db,
                args.models_dir,
                dry_run=args.dry_run,
                estimator_name=args.estimator,
            )
            scanned = summary.get("scanned", 0)
            moved = summary.get("moved", 0)
            planned = summary.get("planned", 0)
            skipped = summary.get("skipped", 0)
            failed = summary.get("failed", 0)
            savings = summary.get("savings_usd", "0.00")
            model = summary.get("estimator", args.estimator)

            if args.dry_run:
                print(
                    f"[{now_str}] [Cycle #{cycle}] Completed (dry-run): "
                    f"scanned={scanned}, planned={planned}, skipped={skipped}, "
                    f"potential_savings=${savings}/mo (model: {model})"
                )
            else:
                print(
                    f"[{now_str}] [Cycle #{cycle}] Completed: "
                    f"scanned={scanned}, moved={moved}, skipped={skipped}, "
                    f"failed={failed}, savings=${savings}/mo (model: {model})"
                )

            if args.export_lifecycle:
                repo = SqliteRepository(args.db)
                try:
                    repo.initialize()
                    decisions = repo.list_latest_decisions()
                    rules = build_s3_lifecycle_rules(decisions)
                    args.export_lifecycle.parent.mkdir(parents=True, exist_ok=True)
                    args.export_lifecycle.write_text(
                        json.dumps({"Rules": rules}, indent=2), encoding="utf-8"
                    )
                    print(
                        f"[{now_str}] [Cycle #{cycle}] Updated S3 Lifecycle rules at "
                        f"{args.export_lifecycle}"
                    )
                finally:
                    repo.close()

        except Exception as error:
            print(
                f"[{now_str}] [Cycle #{cycle}] ERROR during reconciliation: {error}",
                file=sys.stderr,
            )

        if args.iterations > 0 and cycle >= args.iterations:
            print(f"[daemon] Reached iteration limit ({args.iterations}). Exiting.")
            break

        # Sleep in 1-second chunks to handle SIGINT immediately
        for _ in range(args.interval):
            if not running:
                break
            time.sleep(1)

    return 0
