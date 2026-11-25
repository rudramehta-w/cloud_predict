"""Create a fast 5,000-object demo sample from the Microsoft Azure production dataset."""

from __future__ import annotations

import csv
import json
from pathlib import Path

SRC_DIR = Path("data/azure_trace")
OUT_DIR = Path("data/azure_sample")
SAMPLE_SIZE = 5000


def create_sample() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Read first 5,000 objects
    selected_keys = set()
    sample_objects = []
    with (SRC_DIR / "objects.csv").open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if len(sample_objects) >= SAMPLE_SIZE:
                break
            selected_keys.add(row["key"])
            sample_objects.append(row)

    # Write sample objects
    with (OUT_DIR / "objects.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["bucket", "key", "size_bytes", "created_at", "tier"])
        writer.writeheader()
        writer.writerows(sample_objects)

    # 2. Filter accesses for selected keys
    sample_accesses = []
    with (SRC_DIR / "accesses.csv").open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["key"] in selected_keys:
                sample_accesses.append(row)

    with (OUT_DIR / "accesses.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["bucket", "key", "accessed_at"])
        writer.writeheader()
        writer.writerows(sample_accesses)

    # 3. Filter inventory
    with (
        (SRC_DIR / "s3_inventory.csv").open("r", encoding="utf-8") as f_in,
        (OUT_DIR / "s3_inventory.csv").open("w", newline="", encoding="utf-8") as f_out,
    ):
        reader = csv.DictReader(f_in)
        writer = csv.DictWriter(
            f_out,
            fieldnames=reader.fieldnames
            or ["bucket", "key", "size", "last_modified_date", "storage_class", "etag"],
        )
        writer.writeheader()
        for row in reader:
            if row["key"] in selected_keys:
                writer.writerow(row)

    # 4. Manifest
    total_size = sum(int(o["size_bytes"]) for o in sample_objects)
    manifest = {
        "dataset_name": "Microsoft Azure Functions Blob Access Trace (Demo Sample)",
        "provenance": (
            "Directly sampled from Microsoft Azure Public Dataset (Azure/AzurePublicDataset)"
        ),
        "citation": "Romero et al. (Microsoft Research), ACM SoCC 2021",
        "objects_count": len(sample_objects),
        "total_size_gb": round(total_size / 1024**3, 3),
        "access_events_count": len(sample_accesses),
        "official_repo": "https://github.com/Azure/AzurePublicDataset",
    }
    with (OUT_DIR / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(
        f"Created sample in {OUT_DIR}: {len(sample_objects)} objects "
        f"({manifest['total_size_gb']} GB), {len(sample_accesses)} accesses."
    )


if __name__ == "__main__":
    create_sample()
