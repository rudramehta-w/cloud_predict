"""``cloudpredict ingest``: load Objects and access events into the repository."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cloudpredict.domain.exceptions import UnsupportedSourceError
from cloudpredict.infrastructure.csvio import read_accesses, read_objects
from cloudpredict.infrastructure.real_world_io import (
    read_s3_access_logs,
    read_s3_inventory,
    scan_live_bucket_metadata,
)
from cloudpredict.infrastructure.silo_storage import SiloStorage
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

DEFAULT_INPUT = Path("data/synthetic")
DEFAULT_DB = Path("data/cloudpredict.db")
DEFAULT_TRACE_DIR = Path("data/real_trace")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("ingest", help="load Objects and access events")
    parser.add_argument(
        "--source",
        choices=["file", "minio", "s3-logs", "s3-metadata", "real-trace", "azure", "azure-full"],
        default="file",
        help="source of metadata and access events",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="directory containing canonical CSVs",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="target SQLite repository")
    parser.add_argument(
        "--inventory",
        type=Path,
        default=None,
        help="path to AWS S3 Inventory CSV (for s3-logs source)",
    )
    parser.add_argument(
        "--access-logs",
        type=Path,
        default=None,
        help="path to AWS S3 Server Access Log file or directory (for s3-logs source)",
    )
    parser.add_argument(
        "--bucket",
        type=str,
        default="hot",
        help="bucket name for live s3/minio metadata scan (for s3-metadata source)",
    )
    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    if args.source == "minio":
        # Ticket 05 is blocked on unavailable MinIO infrastructure; see the
        # ticket's Comments section and RUNLOG.md.
        raise UnsupportedSourceError(
            "the MinIO adapter is not available (ticket 05 is ready-for-human); "
            "use --source file with a synthetic or exported CSV instead"
        )

    if args.source == "file":
        objects = read_objects(args.input / "objects.csv")
        events = read_accesses(args.input / "accesses.csv")
    elif args.source == "azure":
        azure_dir = args.input if args.input != DEFAULT_INPUT else Path("data/azure_sample")
        objects = read_objects(azure_dir / "objects.csv")
        events = read_accesses(azure_dir / "accesses.csv")
    elif args.source == "azure-full":
        azure_dir = args.input if args.input != DEFAULT_INPUT else Path("data/azure_trace")
        objects = read_objects(azure_dir / "objects.csv")
        events = read_accesses(azure_dir / "accesses.csv")
    elif args.source == "real-trace":
        trace_dir = args.input if args.input != DEFAULT_INPUT else DEFAULT_TRACE_DIR
        objects = read_s3_inventory(trace_dir / "s3_inventory.csv")
        events = read_s3_access_logs(trace_dir / "s3_access.log")
    elif args.source == "s3-logs":
        inv_path = args.inventory or (args.input / "s3_inventory.csv")
        log_path = args.access_logs or (args.input / "s3_access.log")
        objects = read_s3_inventory(inv_path)
        events = read_s3_access_logs(log_path)
    elif args.source == "s3-metadata":
        storage = SiloStorage()
        objects = scan_live_bucket_metadata(storage, args.bucket)
        events = read_s3_access_logs(args.access_logs) if args.access_logs else []
    else:
        raise ValueError(f"unknown source: {args.source}")

    repository = SqliteRepository(args.db)
    try:
        repository.initialize()
        repository.reset()
        repository.add_objects(objects)
        if events:
            repository.add_access_events(events)
        object_count = repository.object_count()
        event_count = repository.event_count()
    finally:
        repository.close()

    source_desc = f" ({args.source})" if args.source != "file" else ""
    msg = (
        f"ingested {object_count} Objects and {event_count} access events "
        f"into {args.db}{source_desc}"
    )
    print(msg)
    return 0
