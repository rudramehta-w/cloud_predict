"""``cloudpredict export-lifecycle``: export recommendations as AWS S3 Lifecycle policies."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cloudpredict.domain.exceptions import CloudPredictError
from cloudpredict.infrastructure.enterprise_export import (
    export_s3_lifecycle_json,
    export_s3_lifecycle_terraform,
)
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

DEFAULT_DB = Path("data/cloudpredict.db")
DEFAULT_OUTPUT = Path("data/s3_lifecycle.json")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "export-lifecycle",
        help="export positive-EV recommendations as AWS S3 Lifecycle JSON or Terraform HCL",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--format",
        choices=["json", "terraform"],
        default="json",
        help="export format (json or terraform)",
    )
    parser.add_argument(
        "--inactivity-days",
        type=int,
        default=30,
        help="inactivity days threshold for S3 transition rule",
    )
    parser.add_argument(
        "--bucket-ref",
        type=str,
        default="aws_s3_bucket.main.id",
        help="Terraform resource reference for bucket (terraform format only)",
    )
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

        recommended = [d for d in decisions if d.recommended]
        if args.format == "json":
            export_s3_lifecycle_json(decisions, args.output, inactivity_days=args.inactivity_days)
            print(
                f"exported AWS S3 Lifecycle Configuration to {args.output} "
                f"({len(recommended)} recommended objects covered)"
            )
        else:
            export_s3_lifecycle_terraform(
                decisions,
                args.output,
                bucket_resource=args.bucket_ref,
                inactivity_days=args.inactivity_days,
            )
            print(
                f"exported Terraform S3 Lifecycle HCL to {args.output} "
                f"({len(recommended)} recommended objects covered)"
            )
        return 0
    finally:
        repository.close()
