"""Live control-plane dashboard: inline HTML/CSS/JS, no CDN, SSE progress."""

from __future__ import annotations

import json
import queue
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from cloudpredict.cli.seed_minio import run_seed
from cloudpredict.domain.ev import FinOpsEngine
from cloudpredict.domain.models import Tier
from cloudpredict.infrastructure.apply import (
    DEFAULT_DB,
    DEFAULT_MODELS_DIR,
    ApplyProgress,
    resolve_estimator,
    run_apply,
)
from cloudpredict.infrastructure.config import DEFAULT_CONFIG_PATH, Config, load_config
from cloudpredict.infrastructure.seeding import DEFAULT_COUNT, DEFAULT_SIZE_RANGE_MIB
from cloudpredict.infrastructure.silo_storage import SiloStorage
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository

GIB = Decimal(1024**3)
TEMPLATE_TOKENS = ("__HOT__", "__COLD__", "__ENDPOINT__", "__DELTA__")


def _storage(config: Config) -> SiloStorage:
    return SiloStorage(
        endpoint_url=config.backend.endpoint_url,
        region=config.backend.region,
        bucket_tiers={
            config.backend.hot_bucket: Tier.HOT,
            config.backend.cold_bucket: Tier.COLD,
        },
    )


def _savings(config: Config, cold_bytes: int) -> Decimal:
    delta = (
        config.tiers[Tier.HOT].storage_per_gb_month - config.tiers[Tier.COLD].storage_per_gb_month
    )
    return (Decimal(cold_bytes) / GIB) * delta


def render_dashboard(config: Config) -> str:
    delta = (
        config.tiers[Tier.HOT].storage_per_gb_month - config.tiers[Tier.COLD].storage_per_gb_month
    )
    tokens = (config.backend.hot_bucket, config.backend.cold_bucket, config.backend.endpoint_url)
    return (
        DASHBOARD_TEMPLATE.replace("__HOT__", tokens[0])
        .replace("__COLD__", tokens[1])
        .replace("__ENDPOINT__", tokens[2])
        .replace("__DELTA__", str(delta))
    )


def create_dashboard_app(
    config_path: Path = DEFAULT_CONFIG_PATH,
    db_path: Path = DEFAULT_DB,
    models_dir: Path = DEFAULT_MODELS_DIR,
    *,
    seed_count: int = DEFAULT_COUNT,
    seed_size_range_mib: tuple[int, int] = DEFAULT_SIZE_RANGE_MIB,
) -> FastAPI:
    config = load_config(config_path)
    storage = _storage(config)
    lock = threading.Lock()
    app = FastAPI(title="cloudPredict control plane")

    def busy() -> bool:
        return lock.locked()

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(render_dashboard(config))

    @app.get("/api/status")
    def status() -> dict[str, object]:
        hot = storage.bucket_stats(config.backend.hot_bucket)
        cold = storage.bucket_stats(config.backend.cold_bucket)
        monthly = _savings(config, cold.size_bytes)
        repository = SqliteRepository(db_path)
        try:
            repository.initialize()
            ledger = repository.list_migrations(8)
            moved_total = repository.count_migrations("moved")
        finally:
            repository.close()
        return {
            "hot": {"objects": hot.objects, "gb": round(hot.size_gb, 2)},
            "cold": {"objects": cold.objects, "gb": round(cold.size_gb, 2)},
            "savings_monthly_usd": str(monthly.quantize(Decimal("0.0001"))),
            "savings_annual_usd": str((monthly * 12).quantize(Decimal("0.01"))),
            "price_delta_per_gb_month": str(
                config.tiers[Tier.HOT].storage_per_gb_month
                - config.tiers[Tier.COLD].storage_per_gb_month
            ),
            "busy": busy(),
            "moved_total": moved_total,
            "ledger": [
                {
                    "key": record.object_key,
                    "size_mb": round(record.size_bytes / 1024**2, 1),
                    "savings": str(record.savings_usd.quantize(Decimal("0.0001"))),
                    "status": record.status,
                    "moved_at": record.moved_at.isoformat(),
                }
                for record in ledger
            ],
        }

    @app.post("/api/seed")
    def seed() -> dict[str, object]:
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="another operation is running")
        try:
            summary = run_seed(
                config_path,
                db_path,
                count=seed_count,
                size_range_mib=seed_size_range_mib,
            )
        finally:
            lock.release()
        return {
            "objects": summary["objects"],
            "gb": round(int(summary["bytes"]) / 1024**3, 2),  # type: ignore[call-overload]
            "events": summary["events"],
            "uploaded": summary["uploaded"],
            "skipped": summary["skipped"],
        }

    @app.post("/api/score")
    def score() -> dict[str, object]:
        objects = storage.list_objects(config.backend.hot_bucket)
        repository = SqliteRepository(db_path)
        try:
            repository.initialize()
            events = repository.list_access_events()
        finally:
            repository.close()
        if not objects:
            return {"scanned": 0, "recommended": 0, "estimator": "n/a", "recommendations": []}

        from datetime import UTC, datetime

        as_of = max((event.accessed_at for event in events), default=datetime.now(UTC))
        estimator = resolve_estimator("auto", config, models_dir)
        probabilities = estimator.estimate(objects, events, as_of)
        finops = FinOpsEngine(config.tiers, config.min_ev_margin, config.scoring_horizon_days)

        rows: list[dict[str, Any]] = []
        for obj in objects:
            decision = finops.best_candidate(obj, probabilities[obj.object_id])
            if decision is None:
                continue
            rows.append(
                {
                    "key": obj.key,
                    "size_mb": round(obj.size_bytes / 1024**2, 1),
                    "tier": obj.tier.value,
                    "to_tier": decision.to_tier.value,
                    "probability": float(decision.access_probability),
                    "ev": float(decision.expected_value),
                    "recommended": decision.recommended,
                }
            )
        rows.sort(key=lambda row: float(row["ev"]), reverse=True)
        return {
            "scanned": len(objects),
            "recommended": sum(1 for row in rows if row["recommended"]),
            "estimator": estimator.name,
            "recommendations": rows,
        }

    @app.post("/api/apply")
    def apply(dry_run: bool = False) -> StreamingResponse:
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="another operation is running")

        def worker(events: queue.Queue[tuple[str, dict[str, object]]]) -> None:
            def on_progress(progress: ApplyProgress) -> None:
                events.put(
                    (
                        "progress",
                        {
                            "index": progress.index,
                            "total": progress.total,
                            "key": progress.key,
                            "size_bytes": progress.size_bytes,
                            "status": progress.status,
                            "message": progress.message,
                        },
                    )
                )

            try:
                summary = run_apply(
                    config_path,
                    db_path,
                    models_dir,
                    dry_run=dry_run,
                    estimator_name="auto",
                    on_progress=on_progress,
                )
                events.put(("done", summary))
            except Exception as error:  # noqa: BLE001 - surfaced to the browser
                events.put(("error", {"message": str(error)}))
            finally:
                lock.release()

        def stream() -> Any:
            events: queue.Queue[tuple[str, dict[str, object]]] = queue.Queue()
            threading.Thread(target=worker, args=(events,), daemon=True).start()
            while True:
                kind, payload = events.get()
                if kind == "progress":
                    yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                elif kind == "done":
                    yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                    break
                else:
                    yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                    break

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cloudPredict — control plane</title>
<style>
  :root {
    --bg: #080b14;
    --panel: rgba(255, 255, 255, 0.055);
    --line: rgba(255, 255, 255, 0.09);
    --text: #e8ecf4;
    --muted: #8b94a7;
    --accent: #6ee7ff;
    --accent2: #8b5cf6;
    --good: #3ddc97;
    --warn: #ffb454;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; color: var(--text);
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background:
      radial-gradient(1100px 600px at 12% -8%, rgba(110, 231, 255, 0.16), transparent 60%),
      radial-gradient(900px 520px at 92% 4%, rgba(139, 92, 246, 0.18), transparent 55%),
      var(--bg);
    padding: 28px 32px 64px;
  }
  .wrap { max-width: 1240px; margin: 0 auto; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .brand { display: flex; align-items: center; gap: 12px; }
  .logo {
    width: 38px; height: 38px; border-radius: 11px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    box-shadow: 0 8px 24px rgba(110, 231, 255, 0.28);
  }
  h1 { font-size: 21px; margin: 0; letter-spacing: 0.2px; }
  .sub { color: var(--muted); font-size: 13px; margin-top: 3px; }
  .pill {
    display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted);
    border: 1px solid var(--line); padding: 6px 12px; border-radius: 999px;
    background: rgba(255, 255, 255, 0.03);
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--good);
    box-shadow: 0 0 0 0 rgba(61, 220, 151, 0.5); animation: pulse 2.2s infinite; }
  @keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(61, 220, 151, 0.45); }
    70% { box-shadow: 0 0 0 9px rgba(61, 220, 151, 0); }
    100% { box-shadow: 0 0 0 0 rgba(61, 220, 151, 0); }
  }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 26px; }
  .card {
    background: var(--panel); border: 1px solid var(--line); border-radius: 16px;
    padding: 18px 20px; backdrop-filter: blur(10px);
  }
  .card .label { color: var(--muted); font-size: 12px; letter-spacing: 0.6px; text-transform: uppercase; }
  .card .value { font-size: 30px; font-weight: 650; margin-top: 8px; font-variant-numeric: tabular-nums; }
  .card .unit { font-size: 14px; color: var(--muted); margin-left: 4px; }
  .card .delta { color: var(--muted); font-size: 12px; margin-top: 6px; }
  .actions { display: flex; gap: 14px; margin-top: 22px; flex-wrap: wrap; }
  button {
    appearance: none; border: 0; cursor: pointer; color: #071018; font-weight: 650;
    font-size: 14px; padding: 13px 22px; border-radius: 12px; letter-spacing: 0.2px;
    background: linear-gradient(135deg, var(--accent), #a5f3fc);
    transition: transform 0.12s ease, box-shadow 0.12s ease, opacity 0.2s ease;
    box-shadow: 0 10px 26px rgba(110, 231, 255, 0.2);
  }
  button.secondary { background: linear-gradient(135deg, #a78bfa, var(--accent2)); color: #f5f3ff; }
  button.primary { background: linear-gradient(135deg, #34d399, #10b981); color: #04150e; }
  button:hover:not(:disabled) { transform: translateY(-1px); }
  button:disabled { opacity: 0.42; cursor: not-allowed; }
  .progress-shell {
    margin-top: 18px; height: 10px; border-radius: 999px; overflow: hidden;
    background: rgba(255, 255, 255, 0.07); border: 1px solid var(--line); display: none;
  }
  .progress-shell.on { display: block; }
  .progress-bar {
    height: 100%; width: 0%; border-radius: 999px;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
    transition: width 0.25s ease;
  }
  .cols { display: grid; grid-template-columns: 1.35fr 1fr; gap: 18px; margin-top: 24px; }
  section h2 { font-size: 15px; margin: 0 0 10px; letter-spacing: 0.3px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-weight: 550; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tbody tr.rec { background: rgba(61, 220, 151, 0.07); }
  tbody tr.stay { color: #93a0b5; }
  .tag { font-size: 11px; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--line); }
  .tag.move { color: var(--good); border-color: rgba(61, 220, 151, 0.4); }
  .tag.keep { color: var(--muted); }
  .terminal {
    margin-top: 18px; border: 1px solid var(--line); border-radius: 14px;
    background: rgba(2, 6, 12, 0.6); padding: 14px 16px; font-family: "SFMono-Regular", ui-monospace, monospace;
    font-size: 12px; color: #b9c4d6; height: 130px; overflow: auto; white-space: pre-wrap;
  }
  .empty { color: var(--muted); font-size: 13px; padding: 12px 0; }
  .note { color: var(--muted); font-size: 12px; margin-top: 8px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="logo"></div>
      <div>
        <h1>cloudPredict</h1>
        <div class="sub">EV-driven storage tiering · <span id="endpoint">__ENDPOINT__</span></div>
      </div>
    </div>
    <span class="pill"><span class="dot"></span><span id="live">live</span></span>
  </header>

  <div class="grid">
    <div class="card"><div class="label">Hot bucket</div>
      <div class="value"><span id="hot-objects">–</span><span class="unit">objects</span></div>
      <div class="delta"><span id="hot-gb">–</span> GB stored</div></div>
    <div class="card"><div class="label">Cold bucket</div>
      <div class="value"><span id="cold-objects">–</span><span class="unit">objects</span></div>
      <div class="delta"><span id="cold-gb">–</span> GB stored</div></div>
    <div class="card"><div class="label">Savings</div>
      <div class="value">$<span id="savings">–</span><span class="unit">/mo</span></div>
      <div class="delta">$<span id="savings-year">–</span>/yr · Δ $<span id="delta">__DELTA__</span>/GB-mo</div></div>
    <div class="card"><div class="label">Migrations</div>
      <div class="value"><span id="moved-count">–</span><span class="unit">moved</span></div>
      <div class="delta">copy → verify → delete</div></div>
  </div>

  <div class="actions">
    <button id="btn-seed" class="secondary">Seed hot bucket</button>
    <button id="btn-score">Score objects</button>
    <button id="btn-apply" class="primary">Apply migrations</button>
  </div>
  <div class="progress-shell" id="progress-shell"><div class="progress-bar" id="progress-bar"></div></div>
  <div class="terminal" id="log">Ready. Seed the hot bucket, score, then apply.</div>

  <div class="cols">
    <section class="card">
      <h2>Recommendations <span class="note" id="score-summary"></span></h2>
      <table>
        <thead><tr>
          <th>Object</th><th class="num">MB</th><th>Tier</th><th class="num">P(access)</th>
          <th class="num">EV</th><th>Action</th>
        </tr></thead>
        <tbody id="recs"><tr><td colspan="6" class="empty">Score to see recommendations.</td></tr></tbody>
      </table>
    </section>
    <section class="card">
      <h2>Migration ledger</h2>
      <table>
        <thead><tr><th>Object</th><th class="num">MB</th><th class="num">Saved</th><th>When</th></tr></thead>
        <tbody id="ledger"><tr><td colspan="4" class="empty">No migrations yet.</td></tr></tbody>
      </table>
    </section>
  </div>
</div>

<script>
const UI = {
  seed: document.getElementById('btn-seed'),
  score: document.getElementById('btn-score'),
  apply: document.getElementById('btn-apply'),
  log: document.getElementById('log'),
  recs: document.getElementById('recs'),
  ledger: document.getElementById('ledger'),
  progressShell: document.getElementById('progress-shell'),
  progressBar: document.getElementById('progress-bar'),
  scoreSummary: document.getElementById('score-summary'),
};
let busy = false;

function log(message) {
  UI.log.textContent += '\\n' + message;
  UI.log.scrollTop = UI.log.scrollHeight;
}
function setBusy(value, label) {
  busy = value;
  for (const button of [UI.seed, UI.score, UI.apply]) button.disabled = value;
  document.getElementById('live').textContent = label || (value ? 'working…' : 'live');
}
function progress(percent) {
  UI.progressShell.classList.add('on');
  UI.progressBar.style.width = Math.min(100, Math.max(0, percent)) + '%';
  if (percent >= 100) setTimeout(() => UI.progressShell.classList.remove('on'), 1200);
}
async function refresh() {
  try {
    const response = await fetch('/api/status');
    const status = await response.json();
    document.getElementById('hot-objects').textContent = status.hot.objects;
    document.getElementById('hot-gb').textContent = status.hot.gb.toFixed(2);
    document.getElementById('cold-objects').textContent = status.cold.objects;
    document.getElementById('cold-gb').textContent = status.cold.gb.toFixed(2);
    document.getElementById('savings').textContent = Number(status.savings_monthly_usd).toFixed(4);
    document.getElementById('savings-year').textContent = Number(status.savings_annual_usd).toFixed(2);
    document.getElementById('moved-count').textContent = status.moved_total;
    renderLedger(status.ledger);
  } catch (error) { document.getElementById('live').textContent = 'offline'; }
}
function renderLedger(rows) {
  if (!rows.length) {
    UI.ledger.innerHTML = '<tr><td colspan="4" class="empty">No migrations yet.</td></tr>';
    return;
  }
  UI.ledger.innerHTML = rows.map(row => `<tr>
    <td>${row.key}</td><td class="num">${row.size_mb}</td>
    <td class="num">$${row.savings}</td>
    <td>${new Date(row.moved_at).toLocaleTimeString()}</td></tr>`).join('');
}
async function seed() {
  setBusy(true, 'seeding…'); log('Seeding real files into the hot bucket…');
  try {
    const response = await fetch('/api/seed', {method: 'POST'});
    if (!response.ok) { log('Seed failed: ' + (await response.text())); return; }
    const summary = await response.json();
    log(`Seeded ${summary.objects} objects (${summary.gb} GB), ${summary.events} access events.`);
  } finally { setBusy(false); refresh(); }
}
async function score() {
  setBusy(true, 'scoring…'); log('Scoring live Objects with the EV model…');
  try {
    const response = await fetch('/api/score', {method: 'POST'});
    const result = await response.json();
    UI.scoreSummary.textContent = `${result.recommended}/${result.scanned} recommended · ${result.estimator}`;
    if (!result.recommendations.length) {
      UI.recs.innerHTML = '<tr><td colspan="6" class="empty">Hot bucket is empty. Seed first.</td></tr>';
    } else {
      UI.recs.innerHTML = result.recommendations.map(row => `<tr class="${row.recommended ? 'rec' : 'stay'}">
        <td>${row.key}</td><td class="num">${row.size_mb}</td><td>${row.tier}→${row.to_tier}</td>
        <td class="num">${row.probability.toFixed(4)}</td><td class="num">${row.ev.toFixed(4)}</td>
        <td><span class="tag ${row.recommended ? 'move' : 'keep'}">${row.recommended ? 'move' : 'stay'}</span></td>
      </tr>`).join('');
    }
    log(`Scored ${result.scanned} objects with ${result.estimator}: ${result.recommended} recommendations.`);
  } finally { setBusy(false); }
}
async function apply() {
  setBusy(true, 'applying…'); log('Applying migrations: copy → verify → delete…');
  progress(0);
  try {
    const response = await fetch('/api/apply', {method: 'POST'});
    if (!response.ok) { log('Apply rejected: ' + (await response.text())); return; }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const chunks = buffer.split('\\n\\n');
      buffer = chunks.pop();
      for (const chunk of chunks) handleEvent(chunk);
    }
  } finally { setBusy(false); refresh(); }
}
function handleEvent(chunk) {
  let name = 'message';
  let data = '';
  for (const line of chunk.split('\\n')) {
    if (line.startsWith('event:')) name = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (!data) return;
  const payload = JSON.parse(data);
  if (name === 'progress') {
    log(payload.message);
    progress(payload.total ? (payload.index / payload.total) * 100 : 0);
  } else if (name === 'done') {
    progress(100);
    log(`Done: ${payload.moved} moved, ${payload.skipped} skipped, ${payload.failed} failed, $${payload.savings_usd} saved.`);
  } else if (name === 'error') {
    log('Error: ' + payload.message);
  }
}
UI.seed.addEventListener('click', seed);
UI.score.addEventListener('click', score);
UI.apply.addEventListener('click', apply);
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""
