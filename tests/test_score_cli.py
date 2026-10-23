"""Ticket 04: ``cloudpredict score`` writes decisions that the repository can read back."""

from __future__ import annotations

from pathlib import Path

import pytest

from cloudpredict.cli.main import main
from cloudpredict.domain.models import Tier
from cloudpredict.infrastructure.generator import GeneratorParams, generate, write_outputs
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository


@pytest.fixture()
def populated_db(tmp_path: Path) -> Path:
    params = GeneratorParams(n_objects=60, days=120, seed=11)
    synthetic = tmp_path / "synthetic"
    write_outputs(synthetic, *generate(params), params)
    db_path = tmp_path / "db.sqlite"
    main(["ingest", "--source", "file", "--input", str(synthetic), "--db", str(db_path)])
    return db_path


def test_score_persists_decisions(populated_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["score", "--db", str(populated_db)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "scored 60 Objects" in output
    assert "recommendations" in output

    repository = SqliteRepository(populated_db)
    try:
        events = repository.list_access_events()
        assert events
        as_of = max(event.accessed_at for event in events)
        decisions = repository.list_decisions("decay-frequency", as_of)
    finally:
        repository.close()

    assert len(decisions) == 60
    assert all(decision.to_tier in {Tier.WARM, Tier.COLD} for decision in decisions)


def test_score_requires_ingested_data(tmp_path: Path) -> None:
    from cloudpredict.domain.exceptions import CloudPredictError

    with pytest.raises(CloudPredictError, match="ingest"):
        main(["score", "--db", str(tmp_path / "empty.db")])
