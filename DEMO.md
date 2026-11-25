# cloudPredict — Enterprise Live Demo Runbook

A complete click-by-click evaluation script for teachers, external examiners, and engineering colleagues.

---

## 0. Prerequisites & Startup

You can run CloudPredict either via Docker Compose (1 command) or locally with `uv`:

### Option A: Via Docker Compose (Recommended for Evaluators)
```bash
docker compose up -d
```
Open <http://localhost:8000> in your browser.

### Option B: Local Development
```bash
# Verify MinIO / Silo is running
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9000/minio/health/live
# expect: 200

# Start web control plane
uv run cloudpredict web
```
Open <http://localhost:8000> in your browser.

---

## Part 1: Live S3 Storage Migration (Physical Invariants)

This demonstrates real physical object movement with two-phase verification across S3-compatible buckets.

### Step 1: Discover Storage
1. Click **`[1] Scan Live Storage`**.
2. **What to observe:**
   - Masthead badge displays: `LIVE S3 STORAGE (MINIO)`
   - Hot bucket card: **30 objects, 1.68 GB**
   - Cold bucket card: **0 objects, 0.00 GB**
   - Thermal band: 100% ember (Hot)
   - Journal logs: `Live scan discovered 30 objects (1.68 GB) in bucket 'hot'.`

### Step 2: Price Candidates with the EV Engine
1. Click **`[2] Price & Optimize (EV)`**.
2. **What to observe:**
   - Summary note updates to: `30/30 recommended · xgboost`
   - Recommendations table populates with 30 candidate moves.
   - For each object, inspect:
     - Byte size (MB)
     - Target tier: `HOT→COLD`
     - $P(\text{access})$ calculated from access history
     - Expected Value ($\text{EV}$) in USD
     - Action stamp: `MOVE`

### Step 3: Execute Two-Phase Verified Migrations
1. Click **`[3] Execute Migrations`** (keep "Dry-Run Simulation" unchecked).
2. **What to observe:**
   - Progress bar smoothly streams from 0% to 100% via Server-Sent Events (SSE).
   - Journal streams real-time atomic steps:
     `Moving datasets/customer-orders-001.parquet (33MB) from hot to cold... done`
   - Completion summary: `Done: 30 moved, 0 skipped, 0 failed, $0.03 saved.`
   - Hot bucket card: **0 objects, 0.00 GB**
   - Cold bucket card: **30 objects, 1.68 GB**
   - Thermal band flips to 100% glacier: **COLD 100%**
   - Savings booked: **$0.0319 / mo**, **$0.38 / yr**
   - Recommendations table rows are struck through with `MOVED` stamps.
   - Migration ledger lists verified moves with ETags and timestamps.

### Step 4: Verify in the S3 Storage Console
1. Open the MinIO Console: <http://localhost:9001> (Credentials: `minioadmin` / `minioadmin`).
2. Inspect `hot`: **0 objects** (safely deleted after verification).
3. Inspect `cold`: **30 objects** (byte-for-byte verified via ETag).

---

## Part 2: Real-World Enterprise Telemetry (Microsoft Azure ACM SoCC 2021)

This demonstrates why CloudPredict is applicable to real production enterprises without requiring petabytes of binary downloads.

### Step 5: Ingest Microsoft Azure Production Dataset
1. Return to <http://localhost:8000>.
2. Click **`[★] Load Azure Trace`**.
3. **What to observe:**
   - Masthead badge updates to: `AZURE PRODUCTION TRACE (ACM SOCC 2021)`
   - Hot bucket card updates to: **5,000 objects, 18.26 GB**
   - Journal confirms:
     `Loaded 5000 objects (18.26 GB) with 301985 real accesses from Microsoft Azure Functions Blob Storage Dataset (ACM SoCC 2021).`

### Step 6: Price the Real-World Workload
1. Click **`[2] Price & Optimize (EV)`**.
2. **What to observe:**
   - Summary note updates to: `4376/5000 recommended · xgboost`
   - The EV engine automatically isolates the long tail of cold blobs while keeping frequently invoked application blobs in Hot!
   - Recommendations table lists real enterprise blob paths:
     `apps/7c51my6n/j34jv5m80nb1c2jd` (43.1 MB, P(access)=0.2009, EV=+$0.00038)

### Step 7: Simulate Migrations (Dry-Run Mode)
1. Check the **`Dry-Run Simulation`** checkbox.
2. Click **`[3] Execute Migrations`**.
3. **What to observe:**
   - Journal logs: `Simulating migrations (Dry-Run mode: 0 storage side-effects)...`
   - Progress bar streams simulated transitions.
   - Realized savings and projected cost reduction are displayed without touching production storage!

---

## Part 3: Enterprise Cloud Export (IaC & Batch Ops)

For enterprise petabyte-scale deployment, point the examiner to the export buttons:

1. Click **`[Export S3 Lifecycle]`**:
   - Downloads `s3_lifecycle.json`.
   - Inspect the file: a valid AWS S3 Lifecycle Configuration with `Rules` arrays, prefix filters, and 30-day transitions to `GLACIER_IR`.
2. Click **`[Export Batch Manifest]`**:
   - Downloads `s3_batch_manifest.csv`.
   - Inspect the file: a standard Amazon S3 Batch Operations CSV manifest (`bucket,key`) ready to submit to AWS S3 Batch Operations.

---

## Key Talking Points for the External Invigilator

1. **Why not download petabytes of files?**
   *"In production cloud architectures (like AWS Intelligent-Tiering, Granica, or Vantage), tiering engines evaluate access logs and object manifests. Transferring 500 TB of binary payloads just to classify them would incur tens of thousands of dollars in unnecessary cross-AZ network egress."*
2. **Where does the data come from?**
   *"We evaluate against published Microsoft Research telemetry from Azure Functions Blob Storage (ACM SoCC 2021) comprising 146,609 unique blobs and 509,566 real read events."*
3. **Why EV instead of just idle days?**
   *"A 30-day idle rule has zero awareness of upcoming read probability. If an object is read once every 35 days, a time-based rule repeatedly archives and retrieves it, costing far more in retrieval penalties than it saves in storage. The EV engine mathematically guarantees positive net yield."*
4. **Is it safe?**
   *"Our execution layer enforces a two-phase commit: the hot object is only deleted after the cold replica exists and its ETag matches byte-for-byte. The entire pipeline is covered by 84 automated tests."*
