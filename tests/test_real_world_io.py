"""Tests for real-world S3 access log and inventory ingestion."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cloudpredict.domain.models import Tier
from cloudpredict.infrastructure.real_world_io import (
    _normalize_storage_class,
    parse_s3_access_log_line,
    parse_s3_log_timestamp,
    read_s3_access_logs,
    read_s3_inventory,
)


def test_parse_s3_log_timestamp() -> None:
    dt = parse_s3_log_timestamp("06/Feb/2026:15:04:05 +0000")
    assert dt == datetime(2026, 2, 6, 15, 4, 5, tzinfo=UTC)


def test_parse_s3_access_log_line_get_object() -> None:
    line = (
        "79a59db90f14c0fed3be92e63fb3402d2879c180926f1074e4479e1594d1b099 "
        "my-bucket [06/Feb/2026:00:00:38 +0000] 192.0.2.3 requester-id 3E57427F3EB2099A "
        'REST.GET.OBJECT photos/2026/report.pdf "GET /my-bucket/photos/2026/report.pdf HTTP/1.1" '
        '200 - 45230 45230 38 38 "-" "boto3/1.26.0" -'
    )
    event = parse_s3_access_log_line(line)
    assert event is not None
    assert event.bucket == "my-bucket"
    assert event.key == "photos/2026/report.pdf"
    assert event.accessed_at == datetime(2026, 2, 6, 0, 0, 38, tzinfo=UTC)


def test_parse_s3_access_log_line_ignores_non_reads() -> None:
    line = (
        "79a59db90f14c0fed3be92e63fb3402d2879c180926f1074e4479e1594d1b099 "
        "my-bucket [06/Feb/2026:00:00:38 +0000] 192.0.2.3 requester-id 3E57427F3EB2099A "
        'REST.PUT.OBJECT photos/2026/report.pdf "PUT /my-bucket/photos/2026/report.pdf HTTP/1.1" '
        '200 - 45230 45230 38 38 "-" "boto3/1.26.0" -'
    )
    assert parse_s3_access_log_line(line) is None


def test_parse_s3_access_log_line_ignores_errors() -> None:
    line = (
        "79a59db90f14c0fed3be92e63fb3402d2879c180926f1074e4479e1594d1b099 "
        "my-bucket [06/Feb/2026:00:00:38 +0000] 192.0.2.3 requester-id 3E57427F3EB2099A "
        'REST.GET.OBJECT missing.pdf "GET /my-bucket/missing.pdf HTTP/1.1" '
        '404 NoSuchKey 0 0 10 10 "-" "curl" -'
    )
    assert parse_s3_access_log_line(line) is None


def test_read_s3_access_logs_file(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text(
        "# S3 log header\n"
        "79a59db90f14c0fed3be92e63fb3402d2879c180926f1074e4479e1594d1b099 "
        "demo [01/Jan/2026:10:00:00 +0000] 1.2.3.4 - ID1 REST.GET.OBJECT file1.txt "
        '"GET /demo/file1.txt HTTP/1.1" 200 - 100 100 10 10 "-" "curl" -\n'
        "79a59db90f14c0fed3be92e63fb3402d2879c180926f1074e4479e1594d1b099 "
        "demo [01/Jan/2026:11:00:00 +0000] 1.2.3.4 - ID2 REST.GET.OBJECT file2.txt "
        '"GET /demo/file2.txt HTTP/1.1" 200 - 100 100 10 10 "-" "curl" -\n',
        encoding="utf-8",
    )
    events = read_s3_access_logs(log_file)
    assert len(events) == 2
    assert events[0].key == "file1.txt"
    assert events[1].key == "file2.txt"


def test_normalize_storage_class() -> None:
    assert _normalize_storage_class("STANDARD") == Tier.HOT
    assert _normalize_storage_class("STANDARD_IA") == Tier.WARM
    assert _normalize_storage_class("INTELLIGENT_TIERING") == Tier.WARM
    assert _normalize_storage_class("GLACIER_IR") == Tier.COLD
    assert _normalize_storage_class("GLACIER") == Tier.COLD
    assert _normalize_storage_class("DEEP_ARCHIVE") == Tier.COLD


def test_read_s3_inventory_headered(tmp_path: Path) -> None:
    inv = tmp_path / "inventory.csv"
    inv.write_text(
        "bucket,key,size,last_modified_date,storage_class,etag\n"
        "prod,data/a.csv,1024,2026-01-01T00:00:00Z,STANDARD,abc123etag\n"
        "prod,data/b.parquet,2048,2026-01-02T00:00:00Z,GLACIER_IR,def456etag\n",
        encoding="utf-8",
    )
    objects = read_s3_inventory(inv)
    assert len(objects) == 2
    assert objects[0].key == "data/a.csv"
    assert objects[0].size_bytes == 1024
    assert objects[0].tier == Tier.HOT
    assert objects[1].tier == Tier.COLD
