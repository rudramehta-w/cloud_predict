"""Process downloaded Microsoft Azure Blob Access Trace into CloudPredict format.

Direct provenance:
- Source: Microsoft Azure Public Dataset (Azure/AzurePublicDataset)
- Release: dataset-functions-blob-2020 (ACM SoCC 2021)
- Schema: Timestamp, AnonRegion, AnonUserId, AnonAppName, AnonFunctionInvocationId,
          AnonBlobName, BlobType, AnonBlobETag, BlobBytes, Read, Write
"""

from __future__ import annotations

import bz2
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

INPUT_BZ2 = Path("/tmp/azure_10m.bz2")
OUT_DIR = Path("data/azure_trace")
BUCKET_NAME = "azure-blob-production"


def process_azure_data() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Decompressing downloaded Azure production slice...")
    with INPUT_BZ2.open("rb") as handle:
        compressed = handle.read()

    decompressor = bz2.BZ2Decompressor()
    text = decompressor.decompress(compressed).decode("utf-8", errors="ignore")
    lines = text.splitlines()
    print(f"Loaded {len(lines):,} raw production log records from Microsoft Azure.")

    reader = csv.DictReader(lines)

    blobs: dict[str, dict] = {}
    accesses: list[tuple[str, str, datetime, str, str]] = []

    for row in reader:
        blob_id = row.get("AnonBlobName")
        if not blob_id:
            continue
        app_id = row.get("AnonAppName") or "general"
        key = f"apps/{app_id}/{blob_id}"

        try:
            ts_ms = int(row["Timestamp"])
            accessed_at = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
            size_bytes = int(float(row.get("BlobBytes") or 0))
        except (ValueError, TypeError):
            continue

        etag = row.get("AnonBlobETag") or "0000000000000000"
        blob_type = row.get("BlobType") or "BlockBlob"

        if key not in blobs:
            blobs[key] = {
                "bucket": BUCKET_NAME,
                "key": key,
                "size_bytes": size_bytes,
                "created_at": accessed_at,
                "tier": "HOT",
                "etag": etag,
                "blob_type": blob_type,
            }
        else:
            if accessed_at < blobs[key]["created_at"]:
                blobs[key]["created_at"] = accessed_at
            if size_bytes > blobs[key]["size_bytes"]:
                blobs[key]["size_bytes"] = size_bytes

        if row.get("Read") == "True":
            accesses.append(
                (
                    BUCKET_NAME,
                    key,
                    accessed_at,
                    row.get("AnonUserId", "usr"),
                    row.get("AnonRegion", "reg"),
                )
            )

    accesses.sort(key=lambda x: x[2])
    print(f"Extracted {len(blobs):,} unique Azure blobs and {len(accesses):,} read accesses.")

    # 1. Write AWS S3 Inventory CSV (s3_inventory.csv)
    inv_file = OUT_DIR / "s3_inventory.csv"
    with inv_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "key", "size", "last_modified_date", "storage_class", "etag"])
        for obj in blobs.values():
            writer.writerow(
                [
                    obj["bucket"],
                    obj["key"],
                    obj["size_bytes"],
                    obj["created_at"].strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "STANDARD",
                    obj["etag"],
                ]
            )

    # 2. Write raw S3 Server Access Log format (s3_access.log)
    s3_log_file = OUT_DIR / "s3_access.log"
    with s3_log_file.open("w", encoding="utf-8") as handle:
        for bkt, key, ts, usr, reg in accesses:
            time_str = ts.strftime("%d/%b/%Y:%H:%M:%S +0000")
            line = (
                f"79a59db90f14c0fed3be92e63fb3402d2879c180926f1074e4479e1594d1b099 "
                f"{bkt} [{time_str}] 10.0.{abs(hash(usr)) % 250}.{abs(hash(reg)) % 250} "
                f"{usr} {abs(hash(key)) % 10000000000000000:016X} REST.GET.OBJECT {key} "
                f'"GET /{bkt}/{key} HTTP/1.1" 200 - 45230 45230 38 38 "-" '
                f'"Microsoft Azure Functions SDK" -\n'
            )
            handle.write(line)

    # 3. Write Canonical CSVs (objects.csv & accesses.csv)
    with (OUT_DIR / "objects.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "key", "size_bytes", "created_at", "tier"])
        for obj in blobs.values():
            writer.writerow(
                [
                    obj["bucket"],
                    obj["key"],
                    obj["size_bytes"],
                    obj["created_at"].strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    obj["tier"],
                ]
            )

    with (OUT_DIR / "accesses.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "key", "accessed_at"])
        for bkt, key, ts, _, _ in accesses:
            writer.writerow([bkt, key, ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")])

    total_size = sum(b["size_bytes"] for b in blobs.values())
    manifest = {
        "dataset_name": "Microsoft Azure Functions Blob Storage Access Trace",
        "provenance": "Microsoft Azure Public Dataset (Azure/AzurePublicDataset), ACM SoCC 2021",
        "official_url": (
            "https://github.com/Azure/AzurePublicDataset/blob/master/"
            "AzureFunctionsBlobDataset2020.md"
        ),
        "citation": (
            "Romero et al. 'Faa$T: A Transparent Auto-Scaling Cache for "
            "Serverless Applications', ACM SoCC 2021"
        ),
        "license": "Creative Commons Attribution 4.0 International (CC-BY 4.0)",
        "download_source": (
            "https://github.com/Azure/AzurePublicDataset/releases/download/"
            "dataset-functions-blob-2020/"
        ),
        "objects_count": len(blobs),
        "total_size_bytes": total_size,
        "total_size_gb": round(total_size / 1024**3, 3),
        "access_events_count": len(accesses),
        "provider": "Microsoft Azure",
    }
    with (OUT_DIR / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    print(
        f"Successfully generated {OUT_DIR}: {len(blobs):,} blobs "
        f"({manifest['total_size_gb']} GB), {len(accesses):,} accesses."
    )


if __name__ == "__main__":
    process_azure_data()
