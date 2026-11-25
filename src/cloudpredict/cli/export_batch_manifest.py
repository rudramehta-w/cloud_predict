"""``cloudpredict export-batch-manifest``: export AWS S3 Batch Operations CSV manifest."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cloudpredict.domain.exceptions import CloudPredictError
from cloudpredict.infrastructure.enterprise_export import export_s3_batch_manifest
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

DEFAULT_DB = Path("data/cloudpredict.db")
DEFAULT_OUTPUT = Path("data/s3_batch_manifest.csv")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "export-batch-manifest",
        help="export positive-EV recommendations as AWS S3 Batch Operations CSV manifest",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    repository = SqliteRepository(args.db)
    try:
        repository.initialize()
        decisions = repository.list_latest_decisions()
        if not decisions:
            raise CloudPredictError(
                f"no scored decisions found in {args.db}; run `cloudpredict score` first"
            )

        count = export_s3_batch_manifest(decisions, args.output)
        print(f"exported AWS S3 Batch Operations manifest ({count} objects) to {args.output}")
        return 0
    finally:
        repository.close()
