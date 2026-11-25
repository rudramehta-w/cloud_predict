"""Tests for enterprise export tools (S3 Lifecycle and Batch Operations)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from cloudpredict.domain.models import Tier, TierDecision
from cloudpredict.infrastructure.enterprise_export import (
    build_s3_lifecycle_rules,
    export_s3_batch_manifest,
    export_s3_lifecycle_json,
    export_s3_lifecycle_terraform,
)


def _sample_decisions() -> list[TierDecision]:
    return [
        TierDecision(
            object_id="my-bucket/backups/db1.tar",
            from_tier=Tier.HOT,
            to_tier=Tier.COLD,
            access_probability=Decimal("0.02"),
            storage_savings=Decimal("15.00"),
            retrieval_penalty=Decimal("1.50"),
            expected_value=Decimal("13.50"),
            recommended=True,
        ),
        TierDecision(
            object_id="my-bucket/backups/db2.tar",
            from_tier=Tier.HOT,
            to_tier=Tier.COLD,
            access_probability=Decimal("0.01"),
            storage_savings=Decimal("20.00"),
            retrieval_penalty=Decimal("1.00"),
            expected_value=Decimal("19.00"),
            recommended=True,
        ),
        TierDecision(
            object_id="my-bucket/config/app.json",
            from_tier=Tier.HOT,
            to_tier=Tier.COLD,
            access_probability=Decimal("0.95"),
            storage_savings=Decimal("0.01"),
            retrieval_penalty=Decimal("0.50"),
            expected_value=Decimal("-0.49"),
            recommended=False,
        ),
    ]


def test_build_s3_lifecycle_rules() -> None:
    decisions = _sample_decisions()
    rules = build_s3_lifecycle_rules(decisions, inactivity_days=45)
    assert len(rules) == 1
    assert rules[0]["Filter"]["Prefix"] == "backups/"
    assert rules[0]["Transitions"][0]["Days"] == 45
    assert rules[0]["Transitions"][0]["StorageClass"] == "GLACIER_IR"


def test_export_s3_lifecycle_json(tmp_path: Path) -> None:
    decisions = _sample_decisions()
    out = tmp_path / "lifecycle.json"
    data = export_s3_lifecycle_json(decisions, out)
    assert "Rules" in data
    assert out.exists()

    loaded = json.loads(out.read_text())
    assert loaded["Rules"][0]["Filter"]["Prefix"] == "backups/"


def test_export_s3_lifecycle_terraform(tmp_path: Path) -> None:
    decisions = _sample_decisions()
    out = tmp_path / "lifecycle.tf"
    hcl = export_s3_lifecycle_terraform(decisions, out)
    assert 'resource "aws_s3_bucket_lifecycle_configuration"' in hcl
    assert 'prefix = "backups/"' in hcl
    assert 'storage_class = "GLACIER_IR"' in hcl


def test_export_s3_batch_manifest(tmp_path: Path) -> None:
    decisions = _sample_decisions()
    out = tmp_path / "batch.csv"
    count = export_s3_batch_manifest(decisions, out)
    assert count == 2  # Only the two recommended moves
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2
    assert "my-bucket,backups/db1.tar" in lines[0]
    assert "my-bucket,backups/db2.tar" in lines[1]
