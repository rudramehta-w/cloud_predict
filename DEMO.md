# cloudPredict — live demo runbook

A five-minute, click-by-click demo. Expected results come from a real run on this machine.

## 0. Prerequisites

- Silo running locally: API `http://localhost:9000`, console `http://localhost:9001`
- `uv` installed, repository checked out
- Port 8000 free

Verify Silo first:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9000/minio/health/live
# expect: 200
```

## 1. Install dependencies

```bash
uv sync --group dev
```

Expect: resolution completes, no errors.

## 2. Start the control plane

```bash
uv run uvicorn cloudpredict.web.app:app --host 127.0.0.1 --port 8000
```

Expect: `Uvicorn running on http://127.0.0.1:8000`.

## 3. Open the dashboard

Browse to <http://localhost:8000>.

Expect: dark control panel, live pill (green), four stat cards. Fresh state shows
**Hot 0 objects / Cold 0 objects / $0.0000/mo / 0 moved**.

## 4. Click "Seed hot bucket"

Expect, after ~10 seconds:

- terminal line: `Seeded 30 objects (1.68 GB), 34 access events.`
- Hot bucket card: **30 objects, 1.68 GB**
- Cold bucket card: **0 objects, 0.00 GB**
- files are real: 30 sparse files of 20–100 MB with names like
  `datasets/customer-orders-001.parquet`

## 5. Click "Score objects"

Expect, after ~2 seconds:

- summary chip: `30/30 recommended · xgboost`
- recommendation table fills with 30 green rows: object, MB, `HOT→COLD`, P(access),
  EV, `move`
- P(access) ranges from the smoothed prior (~0.13) up to ~0.31 for recently read Objects

## 6. Click "Apply migrations"

Expect:

- progress bar animates as each Object is copied
- terminal streams one line per Object:
  `Moving datasets/customer-orders-001.parquet (33MB) from hot to cold... done`
- final line: `Done: 30 moved, 0 skipped, 0 failed, $0.03 saved.`
- Hot bucket card: **0 objects, 0.00 GB**
- Cold bucket card: **30 objects, 1.68 GB**
- Savings card: **$0.0319/mo**, **$0.38/yr**, Δ **$0.019/GB-mo**
- Migrations card: **30 moved**
- ledger table shows the most recent moves with timestamps and per-Object savings

Click "Apply migrations" again: `0 moved` — the run is idempotent because the hot bucket is empty
and any Object already in cold is skipped.

## 7. Verify in the Silo console

Open <http://localhost:9001>, sign in with `minioadmin` / `minioadmin`.

Expect:

- `hot` bucket: 0 objects
- `cold` bucket: 30 objects, ~1.68 GB
- the keys match the ones shown in the dashboard ledger

## 8. Show the savings calculation

The Savings card multiplies moved bytes by the price differential from
`config/cloudpredict.toml` (AWS us-east-1 public pricing, captured 2026-10-24):

```
1.68 GB × ($0.023 − $0.004)/GB-month ≈ $0.0319/month
annualised                             ≈ $0.38/year
```

Thirty Objects are a small workload; the same model on a terabyte bucket scales linearly.

## CLI-only path (no dashboard)

```bash
uv run cloudpredict setup-minio
uv run cloudpredict seed-minio
uv run cloudpredict apply --dry-run     # plan only, nothing moves
uv run cloudpredict apply               # copy → verify → delete
```

Expected `apply` summary line:

```
apply: scanned 30 objects with xgboost, 30 recommended, 30 moved, 0 skipped,
0 failed, 0 needs-cleanup, saved $0.03
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Dashboard pill shows `offline` | Silo not reachable | start Silo, check `/minio/health/live` |
| `Score objects` returns `decay-frequency` | no trained model in `data/models` | run `uv run cloudpredict train` |
| Seeds report `0 uploaded, 30 already present` | hot bucket already holds the demo set | expected on re-runs; the seed resets cold copies and ledger rows |
| Port 8000 already in use | another server running | `--port 8001` |
| Apply reports `needs-cleanup` | copy verified but source delete failed | re-run `apply`; it completes the cleanup without copying twice |
