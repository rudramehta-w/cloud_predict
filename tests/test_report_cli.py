"""Ticket 06: ladder rungs 0–2 plus the offline HTML artifact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloudpredict.cli.main import main
from cloudpredict.infrastructure.generator import GeneratorParams, generate, write_outputs


@pytest.fixture()
def report_dir(tmp_path: Path) -> Path:
    params = GeneratorParams(n_objects=200, days=120, seed=13)
    write_outputs(tmp_path / "synthetic", *generate(params), params)
    db_path = tmp_path / "db.sqlite"
    main(
        [
            "ingest",
            "--source",
            "file",
            "--input",
            str(tmp_path / "synthetic"),
            "--db",
            str(db_path),
        ]
    )
    main(["score", "--db", str(db_path)])
    out = tmp_path / "report"
    main(["report", "--db", str(db_path), "--out", str(out)])
    return out


def test_report_writes_json_and_html(report_dir: Path) -> None:
    artifact = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))

    assert [rung["key"] for rung in artifact["ladder"]] == [
        "no-tiering",
        "naive-30d",
        "decay-frequency",
    ]
    assert artifact["run"]["horizon_days"] == 30
    assert artifact["recommendations"]
    assert (report_dir / "report.html").stat().st_size > 1000


def test_no_tiering_rung_moves_nothing(report_dir: Path) -> None:
    artifact = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    baseline = artifact["ladder"][0]

    assert baseline["objects_moved"] == 0
    assert baseline["total_ev"] == "0.00"


def test_naive_rule_produces_false_evictions(report_dir: Path) -> None:
    artifact = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    naive = artifact["ladder"][1]

    assert naive["objects_moved"] > 0
    assert naive["false_evictions"] > 0


def test_html_is_offline_and_self_contained(report_dir: Path) -> None:
    html = (report_dir / "report.html").read_text(encoding="utf-8")

    for marker in ("run-config", "ladder", "savings", "recommendations"):
        assert f'id="{marker}"' in html
    assert "<script" not in html.lower()
    assert "cdn" not in html.lower()
    assert 'src="http' not in html.lower()
    assert 'href="http' not in html.lower()
    assert "<svg" in html
