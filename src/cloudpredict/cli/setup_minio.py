"""``cloudpredict setup-minio``: create the hot/cold buckets and prove access."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cloudpredict.domain.exceptions import StorageError
from cloudpredict.domain.models import Tier
from cloudpredict.infrastructure.config import DEFAULT_CONFIG_PATH, load_config
from cloudpredict.infrastructure.silo_storage import SiloStorage

PROBE_KEY = "__cloudpredict_probe__"


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "setup-minio", help="create the hot and cold buckets on Silo and verify access"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.set_defaults(func=_run)


def run_setup(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, object]:
    """Shared by the CLI and the dashboard; returns a summary dictionary."""
    config = load_config(config_path)
    storage = SiloStorage(
        endpoint_url=config.backend.endpoint_url,
        region=config.backend.region,
        bucket_tiers={
            config.backend.hot_bucket: Tier.HOT,
            config.backend.cold_bucket: Tier.COLD,
        },
    )

    created: list[str] = []
    for bucket in (config.backend.hot_bucket, config.backend.cold_bucket):
        if storage.ensure_bucket(bucket):
            created.append(bucket)
        storage.set_public_read(bucket)

    etag = storage.put_bytes(config.backend.hot_bucket, PROBE_KEY, b"cloudpredict probe")
    head = storage.head(config.backend.hot_bucket, PROBE_KEY)
    if head is None or head.etag != etag:
        raise StorageError("connectivity probe failed: put/head ETag mismatch")
    storage.delete_object(config.backend.hot_bucket, PROBE_KEY)

    return {
        "endpoint_url": config.backend.endpoint_url,
        "hot_bucket": config.backend.hot_bucket,
        "cold_bucket": config.backend.cold_bucket,
        "created": created,
        "probe_etag": etag,
    }


def _run(args: argparse.Namespace) -> int:
    summary = run_setup(args.config)
    created_raw = summary["created"]
    created = [str(item) for item in created_raw] if isinstance(created_raw, list) else []
    state = f"created {', '.join(created)}" if created else "already present"
    print(
        f"setup-minio: {summary['hot_bucket']} and {summary['cold_bucket']} ready "
        f"({state}); verified put/head/delete on {summary['hot_bucket']} "
        f"at {summary['endpoint_url']}"
    )
    return 0
