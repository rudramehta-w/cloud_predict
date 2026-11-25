"""Unit tests for ``cloudpredict daemon``."""

from __future__ import annotations

from cloudpredict.cli.main import build_parser, main


def test_daemon_parser_registration() -> None:
    parser = build_parser()
    args = parser.parse_args(["daemon", "--interval", "60", "--iterations", "2", "--dry-run"])
    assert args.command == "daemon"
    assert args.interval == 60
    assert args.iterations == 2
    assert args.dry_run is True


def test_daemon_runs_single_iteration_dry_run() -> None:
    exit_code = main(["daemon", "--iterations", "1", "--dry-run", "--estimator", "heuristic"])
    assert exit_code == 0
