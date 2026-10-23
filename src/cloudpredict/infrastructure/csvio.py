"""Canonical CSV reader: the plain-file import path for Objects and accesses."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from cloudpredict.domain.exceptions import DataFormatError
from cloudpredict.domain.models import AccessEvent, StorageObject, Tier

OBJECT_FIELDS = ("bucket", "key", "size_bytes", "created_at", "tier")
ACCESS_FIELDS = ("bucket", "key", "accessed_at")


def parse_timestamp(value: str) -> datetime:
    """Parse ISO-8601, accepting the ``Z`` suffix the generator writes."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_header(
    fieldnames: Sequence[str] | None, expected: tuple[str, ...], path: Path
) -> None:
    if fieldnames is None or tuple(fieldnames) != expected:
        raise DataFormatError(f"{path}: expected header {','.join(expected)}")


def read_objects(path: Path) -> list[StorageObject]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_header(reader.fieldnames, OBJECT_FIELDS, path)
        return [
            StorageObject(
                bucket=row["bucket"],
                key=row["key"],
                size_bytes=int(row["size_bytes"]),
                created_at=parse_timestamp(row["created_at"]),
                tier=Tier(row["tier"]),
            )
            for row in reader
        ]


def read_accesses(path: Path) -> list[AccessEvent]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_header(reader.fieldnames, ACCESS_FIELDS, path)
        return [
            AccessEvent(
                bucket=row["bucket"],
                key=row["key"],
                accessed_at=parse_timestamp(row["accessed_at"]),
            )
            for row in reader
        ]
