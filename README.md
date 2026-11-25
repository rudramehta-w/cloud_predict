# cloudPredict: Autonomous Cloud Storage Tiering & FinOps Platform

[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-84%20passed%20(100%25)-brightgreen.svg)](tests/)
[![Architecture](https://img.shields.io/badge/architecture-hexagonal%20%2F%20ports%20%26%20adapters-orange.svg)](docs/adr/)
[![Dataset](https://img.shields.io/badge/telemetry-Microsoft%20Azure%20(ACM%20SoCC%20'21)-blueviolet.svg)](data/azure_trace/)
[![Compliance](https://img.shields.io/badge/math-Decimal%20fixed--point-green.svg)](src/cloudpredict/domain/ev.py)

**Expected-Value Driven Autonomous Storage Tiering.** cloudPredict predicts the access probability of every cloud object, prices lifecycle moves with a formal Expected Value (EV) mathematical engine, and either physically migrates objects across storage classes with two-phase ETag verification or exports cloud-native AWS S3 Lifecycle policies and Batch Operations manifests.

![cloudPredict control-plane dashboard](dashboard-demo.png)

---

## 1. The Core Problem & Mathematical Engine

Object storage providers (AWS S3, Azure Blob, Google Cloud Storage) bill by tier. Standard hot storage is costly per GB-month; cold archive tiers (Glacier Instant Retrieval, Coldline) offer massive discounts on storage but penalize egress and retrieval requests.

Traditional cloud policies rely on static rules (*"archive after 30 days of inactivity"*). These rules fail because they cannot predict whether an object is **about to be read**; a single false eviction of an active file can cause retrieval penalties that completely wipe out months of storage savings.

cloudPredict solves this with an **Expected Value (EV)** decision model evaluated over a configurable horizon $H$ (default 30 days):

$$\text{EV} = P(\text{inactive}) \times \text{Storage Savings} - P(\text{access}) \times \text{Retrieval Penalty}$$

Where:
- $\text{Storage Savings} = \text{size\_GB} \times (\text{Price}_{\text{hot}} - \text{Price}_{\text{cold}}) \times \frac{H}{30}$
- $\text{Retrieval Penalty} = \text{size\_GB} \times \text{Egress}_{\text{cold}}$
- $P(\text{inactive}) = 1.0 - P(\text{access})$

An object is recommended for tiering **if and only if $\text{EV} > \text{min\_ev\_margin}$** (default $\$0.00$). All currency calculations are executed using Python's `Decimal` fixed-point arithmetic to guarantee zero floating-point drift.

---

## 2. Real-World Cloud Ingestion (No Toy Data)

In real-world enterprise infrastructure, storage tiering engines **never transfer or download petabytes of binary payloads**; they operate strictly on cloud metadata and access logs. cloudPredict natively ingests:

1. **Microsoft Azure Production Trace (ACM SoCC 2021)**: Real-world access telemetry published by Microsoft Research from Azure Functions Blob Storage. Includes 146,609 unique blobs (362.7 GB metadata) and 509,566 read accesses.
2. **AWS S3 Server Access Logs**: Regex-parsed W3C log format tracking `REST.GET.OBJECT` access frequencies, byte counts, and timestamps.
3. **AWS S3 Inventory CSV Reports**: Standard AWS manifests specifying bucket, key, byte size, storage class, last modified timestamp, and ETag hashes.
4. **Live S3 / MinIO Metadata Scanner**: Queries `ListObjectsV2` without downloading object bodies, calculating zero-overhead inventory profiles.

---

## 3. Architecture

Built on hexagonal architecture (clean ports and adapters, ADR 0002/0003). The domain logic relies strictly on Python standard library modules with zero third-party dependencies.

```
                    ┌────────────────────────────┐   ┌───────────────────────────┐
                    │ CLI (argparse)             │   │ Control Plane (FastAPI)   │
                    │ ingest · train · score ·   │   │ live status · auto-mode · │
                    │ apply · export-lifecycle · │   │ score · SSE apply stream  │
                    │ export-batch · web         │   └─────────────┬─────────────┘
                    └─────────────┬──────────────┘                 │
                                  │                                │
        ┌─────────────────────────▼────────────────────────────────▼─────────────┐
        │ domain/  (stdlib only, ADR 0002/0003)                                  │
        │   FinOpsEngine · tier decisions · ports (Protocol)                     │
        │   StorageObject · AccessEvent · MigrationRecord · TierPrice            │
        └─────────────────────────┬────────────────────────────────┬─────────────┘
                                  │                                │
        ┌─────────────────────────▼───────┐        ┌───────────────▼─────────────┐
        │ infrastructure/                 │        │ infrastructure/             │
        │ SQLite Repository (ADR 0005)    │        │ Silo / S3 Adapter (boto3)   │
        │ Real-World IO (S3 logs & trace) │        │ copy · verify ETag · delete │
        │ XGBoost & RF Estimators         │        │ Enterprise IaC Exporters    │
        └─────────────────────────────────┘        └─────────────────────────────┘
```

---

## 4. Quick Start: 3 Ways to Run

### Option A: One-Command Docker Compose (Production Deployment)
Launch the entire stack (CloudPredict App + MinIO S3 Backend) in isolated, production-hardened containers:

```bash
docker compose up -d
```
- Web Control Plane: <http://localhost:8000>
- MinIO S3 Console: <http://localhost:9001> (Credentials: `minioadmin` / `minioadmin`)

---

### Option B: Local Interactive Control Plane
Run the web dashboard natively with Python 3.12+ and `uv`:

```bash
# 1. Install dependencies
uv sync --group dev

# 2. Launch the interactive web control plane
uv run cloudpredict web
```
Open <http://localhost:8000>. From the command dock:
- **`[1] Scan Live Storage`**: Discovers live objects in MinIO/S3.
- **`[★] Load Azure Trace`**: Loads 5,000 real-world Microsoft Azure blobs (18.26 GB) with 301,985 real access requests.
- **`[2] Price & Optimize (EV)`**: Evaluates candidate tiering moves using the trained XGBoost model.
- **`[3] Execute Migrations`**: Runs two-phase atomic moves on live storage or dry-run simulations on telemetry traces.
- **`[Export S3 Lifecycle]`** & **`[Export Batch Manifest]`**: Direct downloads for AWS cloud execution.

---

### Option C: Complete CLI Pipeline
Execute every stage of the FinOps pipeline programmatically:

```bash
# 1. Ingest real Microsoft Azure dataset
uv run cloudpredict ingest --source azure

# 2. Train access estimators (temporal walk-forward split)
uv run cloudpredict train

# 3. Score all objects with EV lens
uv run cloudpredict score

# 4. Export AWS S3 Lifecycle Configuration (JSON)
uv run cloudpredict export-lifecycle --output s3_lifecycle.json

# 5. Export AWS S3 Batch Operations Manifest (CSV)
uv run cloudpredict export-batch-manifest --output s3_batch_manifest.csv

# 6. Simulate migrations without side-effects (Dry-Run)
uv run cloudpredict apply --dry-run
```

---

## 5. Enterprise Infrastructure-as-Code (IaC) Exports

For petabyte-scale cloud environments, organizations do not route storage through an intermediate application server. cloudPredict exports cloud-native configurations:

1. **AWS S3 Lifecycle Configuration (`s3_lifecycle.json`)**:
   Standard S3 lifecycle transition rules grouping positive-EV prefixes into `GLACIER_IR` transitions after optimal holding periods.
2. **Terraform HCL (`s3_lifecycle.tf`)**:
   Ready-to-apply `aws_s3_bucket_lifecycle_configuration` resource block for automated DevOps pipelines.
3. **AWS S3 Batch Operations Manifest (`s3_batch_manifest.csv`)**:
   Two-column CSV (`bucket,key`) compatible with Amazon S3 Batch Operations for asynchronous execution across millions of objects.

---

## 6. Safety & Durability Invariants

The execution engine (`src/cloudpredict/infrastructure/apply.py`) strictly enforces data durability:
- **Two-Phase Hash Verification**: The source object in `hot` is **only deleted** after the destination object in `cold` exists and its MD5/ETag matches the source byte-for-byte.
- **Zero Data Loss on Failure**: If copying fails, the source is left untouched. If source deletion fails after a verified copy, the incident is flagged as `needs-cleanup` in the SQLite ledger and execution proceeds.
- **Idempotency**: Objects already present in cold storage are automatically skipped; re-running never duplicates operations.
- **Dry-Run Mode**: Simulates the exact financial impact with zero storage side-effects.

---

## 7. Verification & Testing Suite

Every commit is validated against contract suites, property-based tests, and live storage integration tests:

```bash
# Run linters, type checks, and complete test suite
uv run ruff check src tests
uv run mypy
uv run pytest
```

- **84 automated tests passing** (100% pass rate).
- **Strict typing**: Zero MyPy errors across 39 source files.
- **Hypothesis property tests**: Invariant testing of EV calculations and boundary conditions.
- **Port contracts**: Swap SQLite or MinIO with any backend without breaking domain invariants.

---

## 8. Project Artifacts & Evaluation

- **Evaluation Presentation Deck**: [Cloud_Predict_Evaluation_III.pptx](docs/presentation/Cloud_Predict_Evaluation_III.pptx) (15 slides, includes speaker notes)
- **Click-by-Click Demo Runbook**: [DEMO.md](DEMO.md)
- **Architecture Decision Records**: [docs/adr/](docs/adr/)
- **Domain Modeling & Terms**: [GLOSSARY.md](GLOSSARY.md)
