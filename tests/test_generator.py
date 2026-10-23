"""Ticket 01: the generator is deterministic, skewed, and lands in the agreed band."""

from pathlib import Path

from cloudpredict.infrastructure.generator import (
    GeneratorParams,
    generate,
    positive_fraction,
    write_outputs,
)


def test_generation_is_deterministic(tmp_path: Path) -> None:
    params = GeneratorParams(n_objects=200, days=60, seed=7)
    first = write_outputs(tmp_path / "a", *generate(params), params)
    second = write_outputs(tmp_path / "b", *generate(params), params)

    assert first == second
    for name in ("objects.csv", "accesses.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_positive_fraction_lands_in_agreed_band() -> None:
    params = GeneratorParams()
    objects, accesses = generate(params)

    assert len(objects) == 5000
    assert 0.15 <= positive_fraction(objects, accesses, params) <= 0.25


def test_access_popularity_is_zipfian() -> None:
    params = GeneratorParams(n_objects=400, days=180, seed=42)
    _, accesses = generate(params)

    counts: dict[str, int] = {}
    for event in accesses:
        counts[event.key] = counts.get(event.key, 0) + 1

    ordered = sorted(counts.values())
    median = ordered[len(ordered) // 2]
    assert max(ordered) > 10 * max(median, 1)


def test_csv_schema_is_canonical(tmp_path: Path) -> None:
    params = GeneratorParams(n_objects=20, days=30, seed=1)
    write_outputs(tmp_path, *generate(params), params)

    assert (tmp_path / "objects.csv").read_text(encoding="utf-8").splitlines()[0] == (
        "bucket,key,size_bytes,created_at,tier"
    )
    assert (tmp_path / "accesses.csv").read_text(encoding="utf-8").splitlines()[0] == (
        "bucket,key,accessed_at"
    )
