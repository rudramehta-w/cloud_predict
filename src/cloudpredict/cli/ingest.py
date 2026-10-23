"""``cloudpredict ingest``: load Objects and access events into the repository."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cloudpredict.domain.exceptions import UnsupportedSourceError
from cloudpredict.infrastructure.csvio import read_accesses, read_objects
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

DEFAULT_INPUT = Path("data/synthetic")
DEFAULT_DB = Path("data/cloudpredict.db")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("ingest", help="load Objects and access events")
    parser.add_argument("--source", choices=["file", "minio"], default="file")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    if args.source == "minio":
        # Ticket 05 is blocked on unavailable MinIO infrastructure; see the
        # ticket's Comments section and RUNLOG.md.
        raise UnsupportedSourceError(
            "the MinIO adapter is not available (ticket 05 is ready-for-human); "
            "use --source file with a synthetic or exported CSV instead"
        )

    objects = read_objects(args.input / "objects.csv")
    events = read_accesses(args.input / "accesses.csv")

    repository = SqliteRepository(args.db)
    try:
        repository.initialize()
        repository.reset()
        repository.add_objects(objects)
        repository.add_access_events(events)
        object_count = repository.object_count()
        event_count = repository.event_count()
    finally:
        repository.close()

    print(f"ingested {object_count} Objects and {event_count} access events into {args.db}")
    return 0
