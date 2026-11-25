"""Real-world cloud storage I/O adapters.

Supports:
1. AWS S3 Server Access Logs (standard W3C-extended log format).
2. AWS S3 Inventory CSV reports.
3. Zero-payload live bucket metadata inspection (ListObjectsV2).
4. Enterprise storage traces (e.g., Azure Blob / SNIA traces).
"""

from __future__ import annotations

import csv
import gzip
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cloudpredict.domain.exceptions import DataFormatError, StorageError
from cloudpredict.domain.models import AccessEvent, StorageObject, Tier

# Standard AWS S3 Server Access Log regex:
# [BucketOwner] [Bucket] [Time] [RemoteIP] [Requester] [RequestID] [Operation] [Key]
# "[Request-URI]" [HTTPstatus] [ErrorCode] [BytesSent] [ObjectSize] [TotalTime] [TurnAroundTime]
# "[Referer]" "[User-Agent]" [VersionId]
S3_ACCESS_LOG_REGEX = re.compile(
    r"^(?P<owner>\S+)\s+"
    r"(?P<bucket>\S+)\s+"
    r"\[(?P<time>[^\]]+)\]\s+"
    r"(?P<remote_ip>\S+)\s+"
    r"(?P<requester>\S+)\s+"
    r"(?P<request_id>\S+)\s+"
    r"(?P<operation>\S+)\s+"
    r"(?P<key>\S+)\s+"
    r'"(?P<request_uri>[^"]*)"\s+'
    r"(?P<status>\S+)\s+"
    r"(?P<error_code>\S+)\s+"
    r"(?P<bytes_sent>\S+)\s+"
    r"(?P<object_size>\S+)\s+"
    r"(?P<total_time>\S+)\s+"
    r"(?P<turn_around_time>\S+)\s+"
    r'"(?P<referer>[^"]*)"\s+'
    r'"(?P<user_agent>[^"]*)'
)

# Operations that constitute a read access event on stored data
READ_OPERATIONS = {
    "REST.GET.OBJECT",
    "REST.HEAD.OBJECT",
    "WEBSITE.GET.OBJECT",
    "REST.COPY.OBJECT_GET",
    "BATCH.GET.OBJECT",
}

# HTTP status codes indicating successful or partial read
SUCCESS_STATUS_CODES = {"200", "206", "304"}


def parse_s3_log_timestamp(raw_time: str) -> datetime:
    """Parse standard AWS S3 log timestamp: '06/Feb/2026:15:04:05 +0000'."""
    try:
        dt = datetime.strptime(raw_time, "%d/%b/%Y:%H:%M:%S %z")
        return dt.astimezone(UTC)
    except ValueError as exc:
        raise DataFormatError(f"unrecognized S3 log timestamp: {raw_time}") from exc


def parse_s3_access_log_line(line: str) -> AccessEvent | None:
    """Parse a single line of an AWS S3 Server Access Log.

    Returns an AccessEvent if the line represents a successful object read,
    or None if it is a write, delete, error, or unparseable line.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    match = S3_ACCESS_LOG_REGEX.match(line)
    if not match:
        return None

    operation = match.group("operation")
    if operation not in READ_OPERATIONS:
        return None

    status = match.group("status")
    if status not in SUCCESS_STATUS_CODES:
        return None

    key = match.group("key")
    if not key or key == "-":
        return None

    bucket = match.group("bucket")
    raw_time = match.group("time")
    accessed_at = parse_s3_log_timestamp(raw_time)

    return AccessEvent(bucket=bucket, key=key, accessed_at=accessed_at)


def read_s3_access_logs(source: Path) -> list[AccessEvent]:
    """Read AWS S3 Server Access Logs from a single file or a directory.

    Transparently handles plain text and .gz compressed files.
    """
    if not source.exists():
        raise FileNotFoundError(f"S3 access log source does not exist: {source}")

    files: list[Path] = []
    if source.is_dir():
        for item in sorted(source.rglob("*")):
            if item.is_file() and not item.name.startswith("."):
                files.append(item)
    else:
        files.append(source)

    events: list[AccessEvent] = []
    for file_path in files:
        if file_path.suffix == ".gz":
            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    ev = parse_s3_access_log_line(line)
                    if ev is not None:
                        events.append(ev)
        else:
            with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    ev = parse_s3_access_log_line(line)
                    if ev is not None:
                        events.append(ev)

    return events


def _normalize_storage_class(raw_class: str) -> Tier:
    """Map AWS S3 StorageClass string to Tier."""
    cleaned = raw_class.strip().upper().replace(" ", "_")
    if cleaned in {"STANDARD", "HOT"}:
        return Tier.HOT
    if cleaned in {
        "STANDARD_IA",
        "ONEZONE_IA",
        "INTELLIGENT_TIERING",
        "WARM",
    }:
        return Tier.WARM
    if cleaned in {
        "GLACIER",
        "GLACIER_IR",
        "DEEP_ARCHIVE",
        "COLD",
    }:
        return Tier.COLD
    return Tier.HOT


def parse_inventory_timestamp(raw_time: str) -> datetime:
    """Parse inventory timestamps (ISO 8601 or common date formats)."""
    cleaned = raw_time.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    raise DataFormatError(f"unrecognized inventory timestamp format: {raw_time}")


def read_s3_inventory(path: Path, bucket_override: str | None = None) -> list[StorageObject]:
    """Parse an AWS S3 Inventory CSV report.

    Accepts both headered CSVs and standard AWS S3 inventory column layouts:
    [bucket, key, version_id, is_latest, is_delete_marker, size,
     last_modified_date, etag, storage_class, ...]
    """
    if not path.exists():
        raise FileNotFoundError(f"inventory file not found: {path}")

    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        first_row = next(reader, None)
        if not first_row:
            return []

        # Check if first row is a header
        header_map: dict[str, int] = {}
        row_sample = [col.strip().lower() for col in first_row]
        if any(h in row_sample for h in ("key", "bucket", "size", "size_bytes")):
            header_map = {name: idx for idx, name in enumerate(row_sample)}
            data_rows: Iterable[Sequence[str]] = reader
        else:
            # Positional AWS standard:
            # 0: bucket, 1: key, 2: version_id, 3: is_latest, 4: is_delete_marker,
            # 5: size, 6: last_modified_date, 7: etag, 8: storage_class
            if len(first_row) >= 7:
                header_map = {
                    "bucket": 0,
                    "key": 1,
                    "size": 5,
                    "last_modified_date": 6,
                }
                if len(first_row) > 8:
                    header_map["storage_class"] = 8
            else:
                # Fallback minimal: bucket, key, size, last_modified, storage_class
                header_map = {
                    "bucket": 0,
                    "key": 1,
                    "size": 2,
                    "last_modified_date": 3,
                }
                if len(first_row) > 4:
                    header_map["storage_class"] = 4
            data_rows = [first_row, *reader]

        key_idx = header_map.get("key", 1)
        size_idx = header_map.get("size", header_map.get("size_bytes", 2))
        date_idx = header_map.get(
            "last_modified_date", header_map.get("created_at", header_map.get("last_modified", 3))
        )
        bucket_idx = header_map.get("bucket", 0)
        tier_idx = header_map.get("storage_class", header_map.get("tier", -1))

        objects: list[StorageObject] = []
        for row in data_rows:
            if not row or len(row) <= max(key_idx, size_idx, date_idx):
                continue
            default_bkt = "default-bucket"
            raw_bkt = row[bucket_idx].strip() if bucket_idx < len(row) else default_bkt
            bucket = bucket_override or raw_bkt
            key = row[key_idx].strip()
            if not key:
                continue

            try:
                size_bytes = int(row[size_idx].strip())
            except ValueError:
                continue

            created_at = parse_inventory_timestamp(row[date_idx].strip())
            tier = Tier.HOT
            if tier_idx != -1 and tier_idx < len(row):
                tier = _normalize_storage_class(row[tier_idx])

            objects.append(
                StorageObject(
                    bucket=bucket,
                    key=key,
                    size_bytes=size_bytes,
                    created_at=created_at,
                    tier=tier,
                )
            )

        return objects


def scan_live_bucket_metadata(storage_client: Any, bucket: str) -> list[StorageObject]:
    """Zero-payload live bucket metadata inspection.

    Uses S3 ListObjectsV2 to retrieve object keys, sizes, timestamps, and storage
    classes directly from the storage provider WITHOUT downloading any file content.
    """
    tier = getattr(storage_client, "_bucket_tiers", {}).get(bucket, Tier.HOT)
    client = getattr(storage_client, "_client", storage_client)

    objects: list[StorageObject] = []
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                storage_class = item.get("StorageClass", "STANDARD")
                obj_tier = _normalize_storage_class(storage_class) if storage_class else tier
                objects.append(
                    StorageObject(
                        bucket=bucket,
                        key=str(item["Key"]),
                        size_bytes=int(item["Size"]),
                        created_at=item["LastModified"].astimezone(UTC),
                        tier=obj_tier,
                    )
                )
    except Exception as exc:
        raise StorageError(f"failed to scan metadata for bucket '{bucket}': {exc}") from exc

    return objects
