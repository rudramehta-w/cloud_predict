"""Ticket 14: the live dashboard end to end, against isolated buckets."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cloudpredict.infrastructure.silo_storage import SiloStorage
from cloudpredict.web.dashboard import create_dashboard_app

HOT = "test-hot"
COLD = "test-cold"


def _write_config(base: Path) -> Path:
    source = Path("config/cloudpredict.toml").read_text(encoding="utf-8")
    text = source.replace('hot_bucket = "hot"', f'hot_bucket = "{HOT}"').replace(
        'cold_bucket = "cold"', f'cold_bucket = "{COLD}"'
    )
    path = base / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    base = tmp_path_factory.mktemp("dashboard")
    storage = SiloStorage()
    try:
        storage.ensure_bucket(HOT)
        storage.ensure_bucket(COLD)
    except Exception as error:  # noqa: BLE001 - connection failures mean skip
        pytest.skip(f"Silo unreachable: {error}")

    config_path = _write_config(base)
    app = create_dashboard_app(
        config_path=config_path,
        db_path=base / "dashboard.db",
        models_dir=Path("data/models"),
        seed_count=3,
        seed_size_range_mib=(1, 2),
    )
    try:
        yield TestClient(app)
    finally:
        for bucket in (HOT, COLD):
            for obj in storage.list_objects(bucket):
                storage.delete_object(bucket, obj.key)


def test_dashboard_page_is_self_contained(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    for marker in ("btn-seed", "btn-score", "btn-apply", "progress-bar", "recs", "ledger"):
        assert marker in html
    assert "cdn" not in html.lower()
    assert 'src="http' not in html.lower()
    assert 'href="http' not in html.lower()
    assert "@keyframes pulse" in html


def test_full_demo_flow(client: TestClient) -> None:
    status = client.get("/api/status").json()
    assert status["hot"]["objects"] == 0
    assert status["cold"]["objects"] == 0

    seed = client.post("/api/seed").json()
    assert seed["objects"] == 3
    assert seed["events"] > 0

    status = client.get("/api/status").json()
    assert status["hot"]["objects"] == 3

    score = client.post("/api/score").json()
    assert score["scanned"] == 3
    assert score["recommendations"]
    assert score["estimator"] in {"xgboost", "random-forest", "decay-frequency"}

    apply_response = client.post("/api/apply")
    assert apply_response.status_code == 200
    assert "event: progress" in apply_response.text
    assert "event: done" in apply_response.text
    done = [
        json.loads(line.split("data: ", 1)[1])
        for line in apply_response.text.splitlines()
        if line.startswith("data: ")
    ][-1]
    assert done["moved"] == 3

    status = client.get("/api/status").json()
    assert status["hot"]["objects"] == 0
    assert status["cold"]["objects"] == 3
    assert len(status["ledger"]) == 3
    assert float(status["savings_monthly_usd"]) > 0

    second = client.post("/api/apply")
    second_done = [
        json.loads(line.split("data: ", 1)[1])
        for line in second.text.splitlines()
        if line.startswith("data: ")
    ][-1]
    assert second_done["moved"] == 0
    assert second_done["plans"] == 0
