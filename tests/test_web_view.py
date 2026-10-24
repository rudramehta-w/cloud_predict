"""Ticket 10: the web view is a read-only projection of report.json."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from cloudpredict.web.app import create_app


def _artifact() -> dict[str, object]:
    return {
        "run": {
            "decision_time": "2026-06-01T00:00:00+00:00",
            "horizon_days": 30,
            "min_ev_margin": "0.00",
            "objects": 2,
            "pricing": {"version": "test", "source": "test", "captured_on": "2026-10-24"},
            "seed": 42,
            "estimator": "decay-frequency",
        },
        "ladder": [],
        "metrics": {},
        "recommendations": [
            {
                "object_id": "demo-bucket/models/object-00001.parquet",
                "probability": 0.0543,
                "from_tier": "HOT",
                "to_tier": "COLD",
                "storage_savings": "0.19",
                "retrieval_penalty": "0.03",
                "expected_value": "0.18",
            }
        ],
    }


def test_view_serves_the_artifact_as_a_table(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_artifact()), encoding="utf-8")

    client = TestClient(create_app(report))
    response = client.get("/")

    assert response.status_code == 200
    assert "object-00001" in response.text
    assert "COLD" in response.text
    assert "filter" in response.text.lower()
    assert "<table" in response.text


def test_view_is_self_contained_and_read_only(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    original = json.dumps(_artifact())
    report.write_text(original, encoding="utf-8")

    client = TestClient(create_app(report))
    client.get("/")

    assert "cdn" not in report.read_text(encoding="utf-8").lower()
    assert report.read_text(encoding="utf-8") == original


def test_missing_artifact_is_a_clear_failure(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "absent.json"))

    response = client.get("/")

    assert response.status_code == 503
    assert "report" in response.text
