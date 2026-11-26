"""Ticket 11: live contract tests against the running Silo instance.

These skip cleanly when Silo is unreachable (for example in CI), so the suite
stays green without the service.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from cloudpredict.cli.main import main
from cloudpredict.domain.models import Tier
from cloudpredict.infrastructure.silo_storage import SiloStorage

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def storage() -> Iterator[SiloStorage]:
    candidate = SiloStorage()
    try:
        candidate.ensure_bucket("hot")
        candidate.ensure_bucket("cold")
    except Exception as error:  # noqa: BLE001 - any connection failure means skip
        pytest.skip(f"Silo unreachable at {candidate.endpoint_url}: {error}")
    yield candidate


def _unique_key() -> str:
    return f"__contract__/{uuid.uuid4().hex}.bin"


def test_setup_minio_cli_is_idempotent(
    storage: SiloStorage, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["setup-minio"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "setup-minio:" in output
    assert "verified put/head/delete" in output


def test_bucket_stats_reflect_uploads(storage: SiloStorage) -> None:
    before = storage.bucket_stats("hot").objects
    key = _unique_key()
    try:
        storage.put_bytes("hot", key, b"x" * 2048)
        after = storage.bucket_stats("hot")
        assert after.objects == before + 1
        assert after.size_bytes >= 2048
    finally:
        storage.delete_object("hot", key)


def test_copy_preserves_etag_and_delete_is_clean(storage: SiloStorage) -> None:
    key = _unique_key()
    payload = b"cloudpredict" * 128
    source_etag = storage.put_bytes("hot", key, payload)
    try:
        copied_etag = storage.copy_object("hot", key, "cold")
        assert copied_etag == source_etag

        head = storage.head("cold", key)
        assert head is not None
        assert head.size_bytes == len(payload)
        assert head.etag == source_etag

        storage.delete_object("hot", key)
        assert storage.head("hot", key) is None
    finally:
        for bucket in ("hot", "cold"):
            if storage.head(bucket, key) is not None:
                storage.delete_object(bucket, key)


def test_list_objects_maps_buckets_to_tiers(storage: SiloStorage) -> None:
    key = _unique_key()
    try:
        storage.put_bytes("cold", key, b"tier-check")
        cold_objects = {obj.key: obj for obj in storage.list_objects("cold")}
        assert cold_objects[key].tier is Tier.COLD
    finally:
        storage.delete_object("cold", key)


def test_upload_file_keeps_a_plain_md5_etag(storage: SiloStorage, tmp_path: Path) -> None:
    path = tmp_path / "probe.bin"
    path.write_bytes(b"etag-probe" * 64)
    key = _unique_key()

    etag = storage.upload_file("hot", key, path)
    try:
        assert "-" not in etag
        assert storage.copy_object("hot", key, "cold") == etag
    finally:
        for bucket in ("hot", "cold"):
            if storage.head(bucket, key) is not None:
                storage.delete_object(bucket, key)
