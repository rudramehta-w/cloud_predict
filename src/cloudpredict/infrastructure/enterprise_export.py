"""Enterprise export tools for AWS S3 and cloud automation.

Enables CloudPredict recommendations to be directly applied in production:
1. AWS S3 Lifecycle Configuration JSON (for aws s3api put-bucket-lifecycle-configuration).
2. Terraform HCL resource definition (aws_s3_bucket_lifecycle_configuration).
3. AWS S3 Batch Operations Manifest CSV (for native server-side S3 batch transitions).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cloudpredict.domain.models import Tier, TierDecision

TIER_TO_AWS_STORAGE_CLASS = {
    Tier.HOT: "STANDARD",
    Tier.WARM: "STANDARD_IA",
    Tier.COLD: "GLACIER_IR",
}


def _extract_prefix(key: str) -> str:
    """Extract first- or second-level directory prefix from an object key."""
    parts = key.split("/")
    if len(parts) > 1:
        return f"{parts[0]}/"
    return ""


def build_s3_lifecycle_rules(
    decisions: Sequence[TierDecision],
    inactivity_days: int = 30,
) -> list[dict[str, Any]]:
    """Synthesize S3 lifecycle rules from positive-EV tier recommendations.

    Groups objects by top-level prefix. If a prefix is predominantly recommended
    for cold tiering, a declarative prefix rule is emitted.
    """
    prefix_decisions: dict[str, list[TierDecision]] = defaultdict(list)
    for dec in decisions:
        obj_key = dec.object_id.split("/", 1)[-1] if "/" in dec.object_id else dec.object_id
        prefix = _extract_prefix(obj_key)
        prefix_decisions[prefix].append(dec)

    rules: list[dict[str, Any]] = []
    rule_idx = 1

    for prefix, dec_list in sorted(prefix_decisions.items()):
        recommended_moves = [d for d in dec_list if d.recommended]
        if not recommended_moves:
            continue

        # Target tier from recommendations (prefer COLD / GLACIER_IR if recommended)
        is_cold = any(d.to_tier == Tier.COLD for d in recommended_moves)
        target_tier = Tier.COLD if is_cold else Tier.WARM
        aws_storage_class = TIER_TO_AWS_STORAGE_CLASS.get(target_tier, "GLACIER_IR")

        rule_id = f"CloudPredict-Rule-{rule_idx}-{prefix.rstrip('/') or 'root'}"
        rule_def: dict[str, Any] = {
            "ID": rule_id,
            "Status": "Enabled",
            "Filter": {"Prefix": prefix} if prefix else {},
            "Transitions": [
                {
                    "Days": inactivity_days,
                    "StorageClass": aws_storage_class,
                }
            ],
        }
        rules.append(rule_def)
        rule_idx += 1

    return rules


def export_s3_lifecycle_json(
    decisions: Sequence[TierDecision],
    output_path: Path,
    inactivity_days: int = 30,
) -> dict[str, Any]:
    """Write AWS S3 Lifecycle Configuration JSON for aws s3api CLI."""
    rules = build_s3_lifecycle_rules(decisions, inactivity_days=inactivity_days)
    payload = {"Rules": rules}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return payload


def export_s3_lifecycle_terraform(
    decisions: Sequence[TierDecision],
    output_path: Path,
    bucket_resource: str = "aws_s3_bucket.main.id",
    inactivity_days: int = 30,
) -> str:
    """Generate Terraform HCL for aws_s3_bucket_lifecycle_configuration."""
    rules = build_s3_lifecycle_rules(decisions, inactivity_days=inactivity_days)
    tf_blocks: list[str] = [
        "# Generated automatically by CloudPredict FinOps Engine",
        'resource "aws_s3_bucket_lifecycle_configuration" "cloudpredict_lifecycle" {',
        f"  bucket = {bucket_resource}\n",
    ]

    for rule in rules:
        tf_blocks.append("  rule {")
        tf_blocks.append(f'    id     = "{rule["ID"]}"')
        tf_blocks.append('    status = "Enabled"\n')
        prefix = rule.get("Filter", {}).get("Prefix", "")
        if prefix:
            tf_blocks.append("    filter {")
            tf_blocks.append(f'      prefix = "{prefix}"')
            tf_blocks.append("    }\n")
        else:
            tf_blocks.append("    filter {}\n")

        for trans in rule.get("Transitions", []):
            tf_blocks.append("    transition {")
            tf_blocks.append(f"      days          = {trans['Days']}")
            tf_blocks.append(f'      storage_class = "{trans["StorageClass"]}"')
            tf_blocks.append("    }")
        tf_blocks.append("  }\n")

    tf_blocks.append("}\n")
    hcl = "\n".join(tf_blocks)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(hcl)
    return hcl


def export_s3_batch_manifest(
    decisions: Sequence[TierDecision],
    output_path: Path,
) -> int:
    """Generate an AWS S3 Batch Operations CSV manifest.

    Format expected by S3 Batch Operations:
    Bucket,Key
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for dec in decisions:
            if not dec.recommended:
                continue
            parts = dec.object_id.split("/", 1)
            bucket = parts[0] if len(parts) > 1 else "bucket"
            key = parts[1] if len(parts) > 1 else parts[0]
            writer.writerow([bucket, key])
            count += 1
    return count
