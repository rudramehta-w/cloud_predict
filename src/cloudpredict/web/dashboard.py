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
    return (
        DASHBOARD_TEMPLATE.replace("__HOT__", config.backend.hot_bucket)
        .replace("__COLD__", config.backend.cold_bucket)
        .replace("__ENDPOINT__", config.backend.endpoint_url)
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
<title>cloudPredict — storage thermal ledger</title>
<style>
  :root {
    --paper: #e9edef;
    --surface: #f5f7f8;
    --ink: #0f1518;
    --ink-2: #3f4d55;
    --muted: #55666f;
    --rule: #c6d0d6;
    --hot: #b8431f;
    --hot-tint: #f1dcd2;
    --cold: #0f6f8c;
    --cold-tint: #d8e8ee;
    --good: #2f6b46;
    --serif: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif;
    --sans: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --mono: ui-monospace, "SF Mono", "SFMono-Regular", Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0; background: var(--paper); color: var(--ink);
    font-family: var(--sans); font-size: 15px; line-height: 1.5;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 38px 40px 72px; }

  /* masthead */
  .masthead {
    display: flex; align-items: flex-end; justify-content: space-between;
    gap: 24px; border-bottom: 2px solid var(--ink); padding-bottom: 14px;
  }
  .kicker {
    font-family: var(--mono); font-size: 10px; letter-spacing: 0.18em;
    text-transform: uppercase; color: var(--muted);
  }
  h1 {
    font-family: var(--serif); font-weight: 500; margin: 7px 0 0;
    font-size: clamp(27px, 3vw, 35px); line-height: 1.08; letter-spacing: -0.012em;
  }
  h1 em { font-style: italic; color: var(--cold); }
  .mast-right {
    text-align: right; font-family: var(--mono); font-size: 11px;
    color: var(--ink-2); line-height: 1.8;
  }
  .status { display: inline-flex; align-items: center; gap: 7px; justify-content: flex-end; }
  .dot {
    width: 7px; height: 7px; border-radius: 50%; background: var(--good);
    animation: pulse 2.4s ease-in-out infinite;
  }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }

  /* thermal band signature */
  .band { margin-top: 26px; border: 1px solid var(--ink); background: var(--surface); }
  .band-head {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 9px 14px; border-bottom: 1px solid var(--rule);
    font-family: var(--mono); font-size: 10px; letter-spacing: 0.16em;
    text-transform: uppercase; color: var(--muted);
  }
  .band-head strong { color: var(--cold); font-weight: 600; letter-spacing: 0.08em; }
  .track {
    position: relative; height: 34px; margin: 12px 14px 0;
    background: var(--paper); border: 1px solid var(--rule); overflow: hidden;
  }
  .track::after {
    content: ""; position: absolute; inset: 0; pointer-events: none;
    background-image: repeating-linear-gradient(90deg, transparent 0 24.8%, var(--rule) 24.8% 25%);
    opacity: 0.7;
  }
  .seg {
    position: absolute; top: 0; bottom: 0; width: 100%;
    transition: transform 0.55s cubic-bezier(0.2, 0.7, 0.2, 1);
  }
  .seg.hot { left: 0; transform-origin: left center; background: linear-gradient(90deg, var(--hot), #d4673f); }
  .seg.cold { right: 0; transform-origin: right center; background: linear-gradient(90deg, #16809f, var(--cold)); }
  .band-foot {
    display: flex; justify-content: space-between; gap: 16px;
    padding: 9px 14px 12px; font-family: var(--mono); font-size: 11px; color: var(--muted);
  }
  .band-foot b { color: var(--ink); font-weight: 600; }
  .band-foot .cold-side b { color: var(--cold); }

  /* readings strip */
  .strip {
    display: grid; grid-template-columns: repeat(4, 1fr);
    border: 1px solid var(--ink); border-top: 0; background: var(--surface);
  }
  .kpi { padding: 14px 16px 16px; border-left: 1px solid var(--rule); }
  .kpi:first-child { border-left: 0; }
  .kpi .label {
    font-family: var(--mono); font-size: 9.5px; letter-spacing: 0.16em;
    text-transform: uppercase; color: var(--muted);
  }
  .kpi .value {
    margin-top: 5px; font-family: var(--mono); font-weight: 500;
    font-size: clamp(23px, 2.5vw, 32px); letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
  }
  .kpi .value .unit { font-size: 11px; letter-spacing: 0.05em; color: var(--muted); margin-left: 5px; }
  .kpi .sub { margin-top: 3px; font-family: var(--mono); font-size: 10.5px; color: var(--muted); }

  /* command dock */
  .dock { display: flex; flex-wrap: wrap; align-items: stretch; border: 1px solid var(--ink); border-top: 0; }
  .btn {
    appearance: none; border: 0; border-right: 1px solid var(--rule);
    background: transparent; color: var(--ink); cursor: pointer;
    font-family: var(--mono); font-size: 11px; letter-spacing: 0.13em;
    text-transform: uppercase; padding: 16px 22px; min-height: 46px;
    transition: background 0.16s ease, color 0.16s ease;
  }
  .btn:hover:not(:disabled) { background: var(--ink); color: var(--paper); }
  .btn:focus-visible { outline: 2px solid var(--cold); outline-offset: -4px; }
  .btn:disabled { color: var(--muted); cursor: not-allowed; }
  .btn.primary { background: var(--ink); color: var(--paper); }
  .btn.primary:hover:not(:disabled) { background: var(--cold); color: #ffffff; }
  .dock .spacer {
    margin-left: auto; display: flex; align-items: center; padding: 0 14px;
    font-family: var(--mono); font-size: 10px; color: var(--muted); letter-spacing: 0.06em;
  }

  /* progress */
  .progress { display: none; align-items: center; gap: 12px; margin-top: 14px; }
  .progress.on { display: flex; }
  .progress-track { flex: 1; height: 3px; background: var(--rule); position: relative; overflow: hidden; }
  .progress-fill {
    position: absolute; inset: 0; background: var(--cold);
    transform: scaleX(0); transform-origin: left center;
    transition: transform 0.25s ease;
  }
  .progress-label { font-family: var(--mono); font-size: 10px; color: var(--muted); min-width: 38px; text-align: right; }

  /* journal */
  .journal { margin-top: 16px; border: 1px solid var(--ink); background: var(--surface); }
  .journal-head {
    display: flex; justify-content: space-between; padding: 8px 14px;
    border-bottom: 1px solid var(--rule); font-family: var(--mono);
    font-size: 9.5px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted);
  }
  .journal-body {
    max-height: 136px; overflow: auto; padding: 10px 14px 12px;
    font-family: var(--mono); font-size: 11.5px; line-height: 1.8;
    color: var(--ink-2); white-space: pre-wrap;
  }

  /* columns */
  .columns {
    display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr);
    margin-top: 32px; border-top: 2px solid var(--ink);
  }
  .col { padding: 18px 22px 8px 0; min-width: 0; }
  .col + .col { border-left: 1px solid var(--rule); padding-left: 22px; padding-right: 0; }
  .sec-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
  h2 { font-family: var(--serif); font-weight: 500; font-size: 21px; margin: 0; letter-spacing: -0.01em; }
  .sec-note {
    font-family: var(--mono); font-size: 9.5px; letter-spacing: 0.12em;
    text-transform: uppercase; color: var(--muted); text-align: right;
  }

  /* tables */
  .table-scroll { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th {
    font-family: var(--mono); font-size: 9.5px; letter-spacing: 0.14em;
    text-transform: uppercase; color: var(--muted); font-weight: 500;
    text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--ink);
    white-space: nowrap;
  }
  td { padding: 9px 10px; border-bottom: 1px solid var(--rule); font-size: 13px; vertical-align: top; }
  td.num, th.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
  tbody tr:hover { background: rgba(15, 21, 24, 0.035); }
  tr.rec { background: linear-gradient(90deg, var(--cold-tint), transparent 62%); }
  .key { font-family: var(--mono); font-size: 11.5px; color: var(--ink-2); word-break: break-all; }
  .stamp {
    display: inline-block; padding: 2px 6px; border: 1px solid currentColor;
    font-family: var(--mono); font-size: 9px; letter-spacing: 0.12em; text-transform: uppercase;
  }
  .stamp.move { color: var(--cold); }
  .stamp.keep { color: var(--muted); }
  .stamp.done { color: var(--good); }
  tr.done td { color: var(--muted); }
  tr.done .key { text-decoration: line-through; text-decoration-thickness: 1px; }
  tr.done .stamp { opacity: 0.85; }
  .empty {
    padding: 18px 10px; color: var(--muted); font-style: italic;
    font-family: var(--serif); font-size: 14.5px;
  }

  .foot {
    margin-top: 34px; border-top: 1px solid var(--rule); padding-top: 12px;
    display: flex; justify-content: space-between; gap: 16px;
    font-family: var(--mono); font-size: 10px; color: var(--muted); letter-spacing: 0.05em;
  }

  @media (max-width: 980px) {
    .wrap { padding: 26px 22px 56px; }
    .masthead { flex-direction: column; align-items: flex-start; gap: 10px; }
    .mast-right { text-align: left; }
    .strip { grid-template-columns: repeat(2, 1fr); }
    .kpi { border-top: 1px solid var(--rule); }
    .kpi:nth-child(3) { border-left: 0; }
    .columns { grid-template-columns: 1fr; }
    .col { padding-right: 0; }
    .col + .col { border-left: 0; border-top: 1px solid var(--rule); margin-top: 18px; padding-left: 0; }
  }
  @media (max-width: 620px) {
    .strip { grid-template-columns: 1fr; }
    .kpi { border-left: 0; }
    .dock { flex-direction: column; }
    .btn { width: 100%; border-right: 0; border-bottom: 1px solid var(--rule); text-align: left; }
    .dock .spacer { padding: 10px 14px; }
    .foot { flex-direction: column; gap: 4px; }
    th, td { padding: 8px; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <div>
      <div class="kicker">cloudPredict / storage operations · __HOT__ → __COLD__</div>
      <h1>The <em>thermal</em> ledger</h1>
    </div>
    <div class="mast-right">
      <div class="status"><span class="dot"></span><span id="live">live</span></div>
      <div id="endpoint">__ENDPOINT__</div>
      <div>evaluation horizon 30d · margin $0.00</div>
    </div>
  </header>

  <section class="band" aria-label="Thermal band: share of bytes in cold storage">
    <div class="band-head">
      <span>Thermal band · byte mass</span>
      <strong id="cold-share">cold 0%</strong>
    </div>
    <div class="track">
      <div class="seg hot" id="seg-hot"></div>
      <div class="seg cold" id="seg-cold"></div>
    </div>
    <div class="band-foot">
      <span>HOT <b id="band-hot">0 obj · 0.00 GB</b></span>
      <span class="cold-side">COLD <b id="band-cold">0 obj · 0.00 GB</b></span>
    </div>
  </section>

  <section class="strip" aria-label="Key readings">
    <div class="kpi">
      <div class="label">Hot bucket</div>
      <div class="value"><span id="hot-objects">–</span><span class="unit">obj</span></div>
      <div class="sub"><span id="hot-gb">–</span> GB resident</div>
    </div>
    <div class="kpi">
      <div class="label">Cold bucket</div>
      <div class="value"><span id="cold-objects">–</span><span class="unit">obj</span></div>
      <div class="sub"><span id="cold-gb">–</span> GB archived</div>
    </div>
    <div class="kpi">
      <div class="label">Savings booked</div>
      <div class="value">$<span id="savings">0.0000</span></div>
      <div class="sub">$<span id="savings-year">0.00</span> /yr</div>
    </div>
    <div class="kpi">
      <div class="label">Objects moved</div>
      <div class="value"><span id="moved-count">0</span></div>
      <div class="sub">copy → verify → delete</div>
    </div>
  </section>

  <div class="dock">
    <button id="btn-seed" class="btn" type="button">Seed hot bucket</button>
    <button id="btn-score" class="btn" type="button">Score objects</button>
    <button id="btn-apply" class="btn primary" type="button">Apply migrations</button>
    <span class="spacer">← seed, score, apply</span>
  </div>

  <div class="progress" id="progress-shell" role="status" aria-live="polite">
    <div class="progress-track"><div class="progress-fill" id="progress-bar"></div></div>
    <span class="progress-label" id="progress-label">0%</span>
  </div>

  <section class="journal" aria-label="Run journal">
    <div class="journal-head"><span>Run journal</span><span id="journal-state">ready</span></div>
    <div class="journal-body" id="log" aria-live="polite">Ready. Seed the hot bucket, score, then apply.</div>
  </section>

  <div class="columns">
    <section class="col" aria-labelledby="recs-title">
      <div class="sec-head">
        <h2 id="recs-title">Recommendations</h2>
        <span class="sec-note" id="score-summary">not scored</span>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr>
            <th scope="col">Object</th>
            <th scope="col" class="num">MB</th>
            <th scope="col">Tier</th>
            <th scope="col" class="num">P(access)</th>
            <th scope="col" class="num">EV</th>
            <th scope="col">Action</th>
          </tr></thead>
          <tbody id="recs"><tr><td colspan="6" class="empty">Nothing scored yet. Run “Score objects”.</td></tr></tbody>
        </table>
      </div>
    </section>
    <section class="col" aria-labelledby="ledger-title">
      <div class="sec-head">
        <h2 id="ledger-title">Migration ledger</h2>
        <span class="sec-note" id="ledger-count">0 moved</span>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr>
            <th scope="col">Object</th>
            <th scope="col" class="num">MB</th>
            <th scope="col" class="num">Saved</th>
            <th scope="col">When</th>
          </tr></thead>
          <tbody id="ledger"><tr><td colspan="4" class="empty">No migrations booked.</td></tr></tbody>
        </table>
      </div>
    </section>
  </div>

  <footer class="foot">
    <span>Δ __DELTA__ USD / GB-month · AWS us-east-1 public pricing, captured 2026-10-24</span>
    <span>advise → verify → move</span>
  </footer>
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
  progressLabel: document.getElementById('progress-label'),
  scoreSummary: document.getElementById('score-summary'),
  ledgerCount: document.getElementById('ledger-count'),
  journalState: document.getElementById('journal-state'),
};
let busy = false;
const applied = new Set();

function stamp() {
  return new Date().toLocaleTimeString([], { hour12: false });
}
function log(message) {
  UI.log.textContent += '\\n' + stamp() + '  ' + message;
  UI.log.scrollTop = UI.log.scrollHeight;
}
function setBusy(value, label) {
  busy = value;
  for (const button of [UI.seed, UI.score, UI.apply]) button.disabled = value;
  document.getElementById('live').textContent = label || (value ? 'working' : 'live');
  UI.journalState.textContent = value ? 'running' : 'ready';
}
function progress(percent) {
  UI.progressShell.classList.add('on');
  UI.progressBar.style.transform = 'scaleX(' + Math.min(100, Math.max(0, percent)) / 100 + ')';
  UI.progressLabel.textContent = Math.round(percent) + '%';
  if (percent >= 100) setTimeout(() => UI.progressShell.classList.remove('on'), 1400);
}
function setText(id, value) {
  document.getElementById(id).textContent = value;
}
async function refresh() {
  try {
    const response = await fetch('/api/status');
    const status = await response.json();
    setText('hot-objects', status.hot.objects);
    setText('hot-gb', status.hot.gb.toFixed(2));
    setText('cold-objects', status.cold.objects);
    setText('cold-gb', status.cold.gb.toFixed(2));
    setText('savings', Number(status.savings_monthly_usd).toFixed(4));
    setText('savings-year', Number(status.savings_annual_usd).toFixed(2));
    setText('moved-count', status.moved_total);

    const total = status.hot.gb + status.cold.gb;
    const share = total > 0 ? status.cold.gb / total : 0;
    document.getElementById('seg-hot').style.transform = 'scaleX(' + (1 - share) + ')';
    document.getElementById('seg-cold').style.transform = 'scaleX(' + share + ')';
    setText('cold-share', 'cold ' + Math.round(share * 100) + '%');
    setText('band-hot', status.hot.objects + ' obj · ' + status.hot.gb.toFixed(2) + ' GB');
    setText('band-cold', status.cold.objects + ' obj · ' + status.cold.gb.toFixed(2) + ' GB');
    UI.ledgerCount.textContent = status.moved_total + ' moved';
    renderLedger(status.ledger);
    if (!busy) UI.journalState.textContent = status.busy ? 'running' : 'ready';
  } catch (error) {
    document.getElementById('live').textContent = 'offline';
  }
}
function renderLedger(rows) {
  if (!rows.length) {
    UI.ledger.innerHTML = '<tr><td colspan="4" class="empty">No migrations booked.</td></tr>';
    return;
  }
  UI.ledger.innerHTML = rows.map(row => `<tr>
    <td class="key">${row.key}</td><td class="num">${row.size_mb}</td>
    <td class="num">$${row.savings}</td>
    <td>${new Date(row.moved_at).toLocaleTimeString()}</td></tr>`).join('');
}
async function seed() {
  setBusy(true, 'seeding'); log('Seeding real files into the hot bucket…');
  try {
    const response = await fetch('/api/seed', {method: 'POST'});
    if (!response.ok) { log('Seed failed: ' + (await response.text())); return; }
    const summary = await response.json();
    log(`Seeded ${summary.objects} objects (${summary.gb} GB), ${summary.events} access events.`);
  } finally { setBusy(false); refresh(); }
}
async function score() {
  setBusy(true, 'scoring'); log('Scoring live Objects with the EV model…');
  try {
    const response = await fetch('/api/score', {method: 'POST'});
    const result = await response.json();
    UI.scoreSummary.textContent = `${result.recommended}/${result.scanned} recommended · ${result.estimator}`;
    if (!result.recommendations.length) {
      UI.recs.innerHTML = '<tr><td colspan="6" class="empty">Hot bucket is empty. Seed first.</td></tr>';
    } else {
      UI.recs.innerHTML = result.recommendations.map(row => `<tr class="${row.recommended ? 'rec' : ''}">
        <td class="key">${row.key}</td><td class="num">${row.size_mb}</td>
        <td>${row.tier}→${row.to_tier}</td>
        <td class="num">${row.probability.toFixed(4)}</td><td class="num">${row.ev.toFixed(4)}</td>
        <td><span class="stamp ${row.recommended ? 'move' : 'keep'}">${row.recommended ? 'move' : 'stay'}</span></td>
      </tr>`).join('');
    }
    log(`Scored ${result.scanned} objects with ${result.estimator}: ${result.recommended} recommendations.`);
  } finally { setBusy(false); }
}
async function apply() {
  setBusy(true, 'applying'); log('Applying migrations: copy → verify → delete…');
  applied.clear();
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
    if (payload.status === 'moved') applied.add(payload.key);
    log(payload.message);
    progress(payload.total ? (payload.index / payload.total) * 100 : 0);
  } else if (name === 'done') {
    progress(100);
    markApplied();
    UI.scoreSummary.textContent = `${payload.moved} applied · ${payload.estimator}`;
    log(`Done: ${payload.moved} moved, ${payload.skipped} skipped, ${payload.failed} failed, $${payload.savings_usd} saved.`);
  } else if (name === 'error') {
    log('Error: ' + payload.message);
  }
}
function markApplied() {
  for (const row of UI.recs.querySelectorAll('tr')) {
    const keyCell = row.querySelector('.key');
    if (!keyCell || !applied.has(keyCell.textContent)) continue;
    row.classList.add('done');
    const stampEl = row.querySelector('.stamp');
    if (stampEl) { stampEl.className = 'stamp done'; stampEl.textContent = 'moved'; }
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
