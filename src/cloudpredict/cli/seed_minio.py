"""``cloudpredict seed-minio``: real files in the hot bucket plus access history."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cloudpredict.cli.setup_minio import run_setup
from cloudpredict.domain.models import Tier
from cloudpredict.infrastructure.config import DEFAULT_CONFIG_PATH, load_config
from cloudpredict.infrastructure.seeding import (
    DEFAULT_COUNT,
    DEFAULT_SEED,
    DEFAULT_SIZE_RANGE_MIB,
    seed_hot_bucket,
)
from cloudpredict.infrastructure.silo_storage import SiloStorage
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

DEFAULT_DB = Path("data/cloudpredict.db")
DEFAULT_WORKDIR = Path("data/seed-files")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "seed-minio", help="create buckets, upload demo files, and seed access history"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--objects", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.set_defaults(func=_run)


def run_seed(
    config_path: Path = DEFAULT_CONFIG_PATH,
    db_path: Path = DEFAULT_DB,
    *,
    count: int = DEFAULT_COUNT,
    seed: int = DEFAULT_SEED,
    size_range_mib: tuple[int, int] = DEFAULT_SIZE_RANGE_MIB,
    workdir: Path = DEFAULT_WORKDIR,
) -> dict[str, object]:
    """Shared by the CLI and the dashboard."""
    run_setup(config_path)
    config = load_config(config_path)

    storage = SiloStorage(
        endpoint_url=config.backend.endpoint_url,
        region=config.backend.region,
        bucket_tiers={
            config.backend.hot_bucket: Tier.HOT,
            config.backend.cold_bucket: Tier.COLD,
        },
    )
    repository = SqliteRepository(db_path)
    try:
        repository.initialize()
        summary = seed_hot_bucket(
            storage,
            repository,
            hot_bucket=config.backend.hot_bucket,
            count=count,
            seed=seed,
            size_range_mib=size_range_mib,
            workdir=workdir,
        )
    finally:
        repository.close()

    summary["database"] = str(db_path)
    summary["hot_bucket"] = config.backend.hot_bucket
    return summary


def _run(args: argparse.Namespace) -> int:
    summary = run_seed(args.config, args.db, count=args.objects, seed=args.seed)
    gigabytes = int(summary["bytes"]) / 1024**3  # type: ignore[call-overload]
    print(
        f"Seeded {summary['objects']} objects ({gigabytes:.2f} GB) into hot bucket "
        f"({summary['uploaded']} uploaded, {summary['skipped']} already present, "
        f"{summary['events']} access events in {summary['database']})"
    )
    return 0
