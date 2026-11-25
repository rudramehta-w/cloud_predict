"""Command-line entry point: one subcommand per pipeline stage."""

from __future__ import annotations

import argparse
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudpredict",
        description="EV-based storage tiering recommendations (advise-only).",
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
    parser = build_parser()
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result
