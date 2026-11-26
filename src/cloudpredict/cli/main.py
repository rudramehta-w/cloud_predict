"""Command-line entry point: one subcommand per pipeline stage."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from cloudpredict.cli import (
    apply,
    daemon,
    export_batch_manifest,
    export_lifecycle,
    generate,
    ingest,
    report,
    score,
    seed_minio,
    setup_minio,
    train,
    web,
)
from cloudpredict.cli.banner import print_banner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudpredict",
        description="Autonomous cloud storage tiering & Expected-Value FinOps platform.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate.register(subparsers)
    ingest.register(subparsers)
    train.register(subparsers)
    score.register(subparsers)
    report.register(subparsers)
    setup_minio.register(subparsers)
    seed_minio.register(subparsers)
    apply.register(subparsers)
    export_lifecycle.register(subparsers)
    export_batch_manifest.register(subparsers)
    web.register(subparsers)
    daemon.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if not effective_argv or effective_argv[0] in ("-h", "--help"):
        print_banner()
        parser = build_parser()
        parser.print_help()
        return 0 if effective_argv else 2
    parser = build_parser()
    args = parser.parse_args(effective_argv)
    result: int = args.func(args)
    return result
