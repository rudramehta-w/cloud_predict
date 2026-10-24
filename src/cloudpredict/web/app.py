"""Read-only web view: a projection of the report artifact, never a second engine."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

DEFAULT_REPORT_PATH = Path("data/report/report.json")
STYLE = """
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       margin: 0 auto; max-width: 1080px; padding: 28px 20px 60px; color: #1b2430;
       background: #fbfbf9; }
h1 { font-size: 24px; margin: 0 0 4px; }
p.meta { color: #5c6672; margin: 0 0 18px; font-size: 14px; }
input { width: 320px; padding: 8px 10px; border: 1px solid #c9c9c2; border-radius: 6px;
        font-size: 14px; background: #fff; }
table { border-collapse: collapse; width: 100%; font-size: 14px; margin-top: 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #ecece6; }
th { background: #f2f2ec; cursor: pointer; user-select: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.note { color: #5c6672; font-size: 13px; }
"""
SCRIPT = """
const table = document.getElementById("rows");
const search = document.getElementById("search");
let ascending = true;
search.addEventListener("input", () => {
  const needle = search.value.toLowerCase();
  for (const row of table.tBodies[0].rows) {
    row.style.display = row.textContent.toLowerCase().includes(needle) ? "" : "none";
  }
});
for (const [index, header] of Array.from(table.tHead.rows[0].cells).entries()) {
  header.addEventListener("click", () => {
    const rows = Array.from(table.tBodies[0].rows);
    const numeric = header.classList.contains("num");
    rows.sort((a, b) => {
      const left = a.cells[index].textContent.trim();
      const right = b.cells[index].textContent.trim();
      const order = numeric ? parseFloat(left) - parseFloat(right)
                            : left.localeCompare(right);
      return ascending ? order : -order;
    });
    ascending = !ascending;
    for (const row of rows) table.tBodies[0].appendChild(row);
  });
}
"""


def _cell(value: Any) -> str:
    from html import escape

    return escape(str(value))


def render_view(artifact: dict[str, Any], limit: int = 500) -> str:
    run = artifact["run"]
    recommendations = artifact["recommendations"][: max(limit, 1)]
    rows = "".join(
        "<tr>"
        f"<td>{_cell(item['object_id'])}</td>"
        f'<td class="num">{_cell(item["probability"])}</td>'
        f"<td>{_cell(item['from_tier'])}</td>"
        f"<td>{_cell(item['to_tier'])}</td>"
        f'<td class="num">{_cell(item["storage_savings"])}</td>'
        f'<td class="num">{_cell(item["retrieval_penalty"])}</td>'
        f'<td class="num">{_cell(item["expected_value"])}</td>'
        "</tr>"
        for item in recommendations
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cloudPredict — recommendations</title>
<style>{STYLE}</style>
</head>
<body>
<h1>cloudPredict — recommendations</h1>
<p class="meta">
Read-only projection of <code>report.json</code>. Decision point {_cell(run["decision_time"])},
Scoring Horizon {_cell(run["horizon_days"])} days, margin {_cell(run["min_ev_margin"])}.
Nothing on this page recomputes or mutates anything.
</p>
<input id="search" type="search" aria-label="filter"
       placeholder="Filter by object, tier, or value…">
<table id="rows">
<thead><tr>
<th>Object</th><th class="num">P(access)</th><th>From</th><th>To</th>
<th class="num">Savings</th><th class="num">Penalty</th><th class="num">EV</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="note">Showing {len(recommendations)} recommendations from the artifact.</p>
<script>{SCRIPT}</script>
</body>
</html>
"""


def create_app(report_path: Path | None = None) -> FastAPI:
    path = report_path or Path(os.environ.get("CLOUDPREDICT_REPORT", str(DEFAULT_REPORT_PATH)))
    app = FastAPI(title="cloudPredict read-only view")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        if not path.exists():
            raise HTTPException(status_code=503, detail=f"no report at {path}; run report first")
        artifact = json.loads(path.read_text(encoding="utf-8"))
        return HTMLResponse(render_view(artifact))

    return app


app = create_app()
