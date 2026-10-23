"""Ticket 03: end-to-end ingest from the generator's CSVs into SQLite."""

from __future__ import annotations

from pathlib import Path

import pytest

from cloudpredict.cli.main import main
from cloudpredict.infrastructure.generator import GeneratorParams, generate, write_outputs
from cloudpredict.infrastructure.sqlite_repository import SqliteRepository


@pytest.fixture()
def synthetic_dir(tmp_path: Path) -> Path:
    params = GeneratorParams(n_objects=50, days=30, seed=3)
    write_outputs(tmp_path / "synthetic", *generate(params), params)
    return tmp_path / "synthetic"


def test_ingest_file_source_populates_repository(
    synthetic_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "db.sqlite"
    exit_code = main(
        ["ingest", "--source", "file", "--input", str(synthetic_dir), "--db", str(db_path)]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "ingested 50 Objects" in output

    repository = SqliteRepository(db_path)
    try:
        assert repository.object_count() == 50
        assert repository.event_count() >= 0
        assert repository.initialize() is None
    finally:
        repository.close()


def test_minio_source_fails_clearly() -> None:
    from cloudpredict.domain.exceptions import UnsupportedSourceError

    with pytest.raises(UnsupportedSourceError, match="ticket 05"):
        main(["ingest", "--source", "minio"])


def test_bad_header_is_rejected(tmp_path: Path) -> None:
    from cloudpredict.domain.exceptions import DataFormatError
    from cloudpredict.infrastructure.csvio import read_objects

    path = tmp_path / "objects.csv"
    path.write_text("bucket,key\nb,k\n", encoding="utf-8")

    with pytest.raises(DataFormatError):
        read_objects(path)
