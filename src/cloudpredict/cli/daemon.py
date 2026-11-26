"""``cloudpredict daemon``: autonomous background storage reconciliation loop."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cloudpredict.cli.banner import (
    BOLD,
    CYAN,
    GRAY,
    GREEN,
    ORANGE,
    RED,
    RESET,
    YELLOW,
    print_banner,
    print_status_box,
)
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

    print_banner()

    mode_label = (
        f"{YELLOW}● DRY-RUN SIMULATION (Read-Only / Zero Storage Writes){RESET}"
        if args.dry_run
        else f"{GREEN}● ACTIVE STORAGE MIGRATION (Two-Phase Verified Moves){RESET}"
    )

    fields: list[tuple[str, str]] = [
        ("Process State", f"{GREEN}● RUNNING{RESET} (PID {os.getpid()})"),
        ("Reconcile Cadence", f"Every {args.interval}s ({(args.interval / 60):.1f} min)"),
        ("Execution Mode", mode_label),
        ("Inference Model", f"{CYAN}{args.estimator}{RESET}"),
        ("Persistence Store", f"{GRAY}{args.db}{RESET}"),
        ("Configuration", f"{GRAY}{args.config}{RESET}"),
    ]
    if args.export_lifecycle:
        fields.append(("IaC Rule Export", f"{CYAN}{args.export_lifecycle}{RESET}"))

    print_status_box("DAEMON CONTROL PLANE", fields)
    print(
        f"  {GRAY}Autonomous background loop engaged. Press {BOLD}Ctrl+C{RESET}{GRAY} to stop.{RESET}\n"
    )

    cycle = 0
    while running:
        cycle += 1
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(
            f"  {CYAN}❯{RESET} {BOLD}[{now_str}]{RESET} Cycle #{cycle}: Initiating reconciliation..."
        )

        try:
            summary = run_apply(
                args.config,
                args.db,
                args.models_dir,
                dry_run=args.dry_run,
                estimator_name=args.estimator,
            )
            scanned = val if isinstance((val := summary.get("scanned")), int) else 0
            moved = val if isinstance((val := summary.get("moved")), int) else 0
            planned = val if isinstance((val := summary.get("planned")), int) else 0
            skipped = val if isinstance((val := summary.get("skipped")), int) else 0
            failed = val if isinstance((val := summary.get("failed")), int) else 0
            savings = str(summary.get("savings_usd", "0.00"))
            model = str(summary.get("estimator", args.estimator))

            if args.dry_run:
                print(f"    {GREEN}✔{RESET} Inventory Scanned: {BOLD}{scanned:,}{RESET} objects")
                print(
                    f"    {CYAN}✔{RESET} ML Prediction:     Model '{model}' completed temporal inference"
                )
                print(
                    f"    {ORANGE}✔{RESET} Planned Moves:     {BOLD}{planned:,}{RESET} candidates identified (dry-run)"
                )
                print(f"    {GRAY}✔{RESET} Retained in Tier:  {skipped:,} objects")
                print(
                    f"    {GREEN}✔{RESET} Projected Savings: {BOLD}+${savings}/mo{RESET} Net Expected Value"
                )
            else:
                status_icon = f"{RED}✖{RESET}" if failed > 0 else f"{GREEN}✔{RESET}"
                print(f"    {GREEN}✔{RESET} Inventory Scanned: {BOLD}{scanned:,}{RESET} objects")
                print(
                    f"    {CYAN}✔{RESET} ML Prediction:     Model '{model}' evaluated access probability"
                )
                print(
                    f"    {GREEN}✔{RESET} Physical Moves:    {BOLD}{moved:,}{RESET} migrated with 2-phase ETag verification"
                )
                print(f"    {status_icon} Failures / Alerts:  {failed}")
                print(
                    f"    {GREEN}✔{RESET} Realized Savings:  {BOLD}+${savings}/mo{RESET} Net Expected Value"
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
                        f"    {CYAN}✔{RESET} IaC Compilation:   {len(rules)} lifecycle rule(s) written to {args.export_lifecycle}"
                    )
                finally:
                    repo.close()

        except Exception as error:
            print(
                f"    {RED}✖ Reconciliation Error:{RESET} {error}",
                file=sys.stderr,
            )

        if args.iterations > 0 and cycle >= args.iterations:
            print(f"\n  {GRAY}Iteration limit reached ({args.iterations}). Exiting cleanly.{RESET}")
            break

        print(f"  {GRAY}⏳ Next reconciliation cycle in {args.interval}s...{RESET}\n")

        # Sleep in 1-second chunks to handle SIGINT immediately
        for _ in range(args.interval):
            if not running:
                break
            time.sleep(1)

    return 0
