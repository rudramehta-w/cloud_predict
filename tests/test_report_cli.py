"""Tickets 06 + 08: the six-rung ladder, EV Capture, and the offline HTML artifact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloudpredict.cli.main import main
from cloudpredict.infrastructure.generator import GeneratorParams, generate, write_outputs


@pytest.fixture(scope="module")
def artifact_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("ladder")
    params = GeneratorParams(n_objects=200, days=180, seed=13)
    write_outputs(base / "synthetic", *generate(params), params)
    db_path = base / "db.sqlite"
    main(
        [
            "ingest",
            "--source",
            "file",
            "--input",
            str(base / "synthetic"),
            "--db",
            str(db_path),
        ]
    )
    main(["train", "--db", str(db_path), "--models-dir", str(base / "models")])
    out = base / "report"
    main(
        [
            "report",
            "--db",
            str(db_path),
            "--models-dir",
            str(base / "models"),
            "--out",
            str(out),
        ]
    )
    return out


def _artifact(artifact_dir: Path) -> dict[str, object]:
    return json.loads((artifact_dir / "report.json").read_text(encoding="utf-8"))


def test_ladder_has_all_six_rungs(artifact_dir: Path) -> None:
    artifact = _artifact(artifact_dir)

    assert [rung["key"] for rung in artifact["ladder"]] == [
        "no-tiering",
        "naive-30d",
        "decay-frequency",
        "xgboost",
        "random-forest",
        "oracle",
    ]


def test_oracle_is_the_ceiling_and_captures_itself(artifact_dir: Path) -> None:
    artifact = _artifact(artifact_dir)
    ladder = artifact["ladder"]
    oracle = next(rung for rung in ladder if rung["key"] == "oracle")

    assert oracle["total_ev"] == max(rung["total_ev"] for rung in ladder)
    assert oracle["false_evictions"] == 0
    assert oracle["ev_capture"] == pytest.approx(1.0)


def test_every_rung_reports_both_metric_families(artifact_dir: Path) -> None:
    artifact = _artifact(artifact_dir)
    metrics = artifact["metrics"]

    for rung in artifact["ladder"]:
        for field in (
            "objects_moved",
            "storage_savings",
            "retrieval_cost",
            "total_ev",
            "false_evictions",
            "false_eviction_cost",
            "ev_capture",
        ):
            assert field in rung
    for key in ("decay-frequency", "xgboost", "random-forest"):
        assert 0.0 <= float(metrics[key]["pr_auc"]) <= 1.0


def test_ev_capture_is_relative_to_the_oracle(artifact_dir: Path) -> None:
    artifact = _artifact(artifact_dir)
    ladder = artifact["ladder"]
    oracle_ev = float(next(rung for rung in ladder if rung["key"] == "oracle")["total_ev"])

    for rung in ladder:
        if rung["ev_capture"] is None:
            continue
        expected = float(rung["total_ev"]) / oracle_ev
        assert rung["ev_capture"] == pytest.approx(expected, abs=2e-2)


def test_no_tiering_rung_moves_nothing(artifact_dir: Path) -> None:
    artifact = _artifact(artifact_dir)
    baseline = artifact["ladder"][0]

    assert baseline["objects_moved"] == 0
    assert baseline["total_ev"] == "0.00"


def test_naive_rule_produces_false_evictions(artifact_dir: Path) -> None:
    artifact = _artifact(artifact_dir)
    naive = artifact["ladder"][1]

    assert naive["objects_moved"] > 0
    assert naive["false_evictions"] > 0


def test_report_is_deterministic(artifact_dir: Path, tmp_path: Path) -> None:
    first = _artifact(artifact_dir)
    db_path = artifact_dir.parent / "db.sqlite"
    repeat = tmp_path / "repeat"
    main(
        [
            "report",
            "--db",
            str(db_path),
            "--models-dir",
            str(artifact_dir.parent / "models"),
            "--out",
            str(repeat),
        ]
    )

    second = json.loads((repeat / "report.json").read_text(encoding="utf-8"))
    assert second["ladder"] == first["ladder"]


def test_html_is_offline_and_self_contained(artifact_dir: Path) -> None:
    html = (artifact_dir / "report.html").read_text(encoding="utf-8")

    for marker in ("run-config", "ladder", "savings", "recommendations"):
        assert f'id="{marker}"' in html
    assert "PR-AUC" in html
    assert "<script" not in html.lower()
    assert "cdn" not in html.lower()
    assert 'src="http' not in html.lower()
    assert 'href="http' not in html.lower()
    assert "<svg" in html
