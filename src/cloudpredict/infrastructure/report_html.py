"""Self-contained HTML rendering: inline CSS and SVG, no CDN, no JavaScript."""

from __future__ import annotations

from html import escape
from typing import Any

STYLE = """
:root { color-scheme: light; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       margin: 0 auto; max-width: 1040px; padding: 32px 24px 64px; color: #1b2430;
       background: #fbfbf9; line-height: 1.5; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 40px 0 12px; border-bottom: 1px solid #e3e3dd; padding-bottom: 6px; }
p.meta { color: #5c6672; margin: 0 0 8px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #ecece6; }
th { background: #f2f2ec; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.recommended td { font-weight: 600; }
.note { color: #5c6672; font-size: 13px; margin-top: 6px; }
.figure { margin: 8px 0 4px; }
"""


def _table(
    headers: list[tuple[str, bool]],
    rows: list[list[str]],
    row_classes: list[str] | None = None,
) -> str:
    head = "".join(
        f'<th class="{"num" if numeric else ""}">{escape(label)}</th>' for label, numeric in headers
    )
    body_parts: list[str] = []
    for index, row in enumerate(rows):
        css = f' class="{row_classes[index]}"' if row_classes and row_classes[index] else ""
        cells = "".join(
            f'<td class="{"num" if headers[position][1] else ""}">{cell}</td>'
            for position, cell in enumerate(row)
        )
        body_parts.append(f"<tr{css}>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_parts)}</tbody></table>"


def _bar_chart_svg(rows: list[tuple[str, float]]) -> str:
    """Horizontal bars scaled to the largest absolute value; pure SVG, no library."""
    if not rows:
        return ""
    width, label_width, bar_height, gap = 720, 190, 26, 12
    max_value = max(abs(value) for _, value in rows) or 1.0
    chart_width = width - label_width - 70
    height = len(rows) * (bar_height + gap) + 10
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="savings by rung">'
    ]
    for index, (label, value) in enumerate(rows):
        y = index * (bar_height + gap) + 5
        bar_width = max(int(chart_width * abs(value) / max_value), 2)
        colour = "#2f7d4f" if value >= 0 else "#a33b3b"
        parts.append(
            f'<text x="0" y="{y + 18}" font-size="13" fill="#1b2430">{escape(label)}</text>'
        )
        parts.append(
            f'<rect x="{label_width}" y="{y}" width="{bar_width}" height="{bar_height}" '
            f'rx="3" fill="{colour}"/>'
        )
        parts.append(
            f'<text x="{label_width + bar_width + 8}" y="{y + 18}" font-size="12" '
            f'fill="#1b2430">{value:,.2f}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _pr_auc(metrics: dict[str, Any], key: str) -> str:
    payload = metrics.get(key) or {}
    value = payload.get("pr_auc")
    return "n/a" if value is None else f"{float(value):.3f}"


def render_report(artifact: dict[str, Any]) -> str:
    run = artifact["run"]
    ladder = artifact["ladder"]
    recommendations = artifact["recommendations"]
    metrics = artifact.get("metrics") or {}

    header_rows = [
        ["Decision point", escape(str(run["decision_time"]))],
        ["Scoring Horizon (days)", str(run["horizon_days"])],
        ["min_ev_margin", escape(str(run["min_ev_margin"]))],
        ["Objects scored", str(run["objects"])],
        ["Pricing version", escape(str(run["pricing"]["version"]))],
        ["Pricing source", escape(str(run["pricing"]["source"]))],
        ["Price capture date", escape(str(run["pricing"]["captured_on"]))],
        ["Seed", str(run.get("seed", "n/a"))],
    ]
    header_table = _table(
        [("Parameter", False), ("Value", False)],
        [[escape(str(left)), right] for left, right in header_rows],
    )

    ladder_rows = [
        [
            escape(str(rung["label"])),
            str(rung["objects_moved"]),
            str(rung["storage_savings"]),
            str(rung["retrieval_cost"]),
            str(rung["total_ev"]),
            str(rung["false_evictions"]),
            str(rung["false_eviction_cost"]),
            "n/a" if rung["ev_capture"] is None else f"{rung['ev_capture']:.1%}",
            _pr_auc(metrics, str(rung["key"])),
        ]
        for rung in ladder
    ]
    ladder_table = _table(
        [
            ("Rung", False),
            ("Objects moved", True),
            ("Storage savings", True),
            ("Retrieval cost", True),
            ("Total EV", True),
            ("False evictions", True),
            ("False-eviction cost", True),
            ("EV Capture", True),
            ("PR-AUC (test)", True),
        ],
        ladder_rows,
    )

    savings_chart = _bar_chart_svg(
        [(str(rung["label"]), float(rung["total_ev"])) for rung in ladder]
    )

    recommendation_rows = [
        [
            escape(str(item["object_id"])),
            f"{item['probability']:.4f}",
            escape(str(item["from_tier"])),
            escape(str(item["to_tier"])),
            str(item["storage_savings"]),
            str(item["retrieval_penalty"]),
            str(item["expected_value"]),
        ]
        for item in recommendations
    ]
    recommendation_table = _table(
        [
            ("Object", False),
            ("P(access)", True),
            ("From tier", False),
            ("To tier", False),
            ("Storage savings", True),
            ("Egress penalty", True),
            ("EV", True),
        ],
        recommendation_rows,
    )

    charts = artifact.get("charts") or []
    charts_html = "".join(charts)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cloudPredict report</title>
<style>{STYLE}</style>
</head>
<body>
<h1>cloudPredict — v1 tiering report</h1>
<p class="meta">
Advise-only recommendations priced with
EV = P(inactive) × Storage_Savings − P(access) × Egress_Penalty (ADR 0001).
</p>

<h2 id="run-config">Run configuration</h2>
{header_table}

<h2 id="ladder">Estimator ladder</h2>
{ladder_table}
<p class="note">EV Capture is each rung's total EV as a fraction of the Oracle's ceiling.</p>

<h2 id="savings">Realised savings by rung</h2>
<div class="figure">{savings_chart}</div>

<h2 id="recommendations">Ranked recommendations</h2>
{recommendation_table}
<p class="note">
Top Objects by Expected Value at the evaluation decision point. Moving an Object
is a recommendation only; cloudPredict never mutates storage.
</p>
{charts_html}
</body>
</html>
"""
