"""cloudPredict: expected-value driven storage tiering recommendations."""

from __future__ import annotations


def main() -> None:
    from cloudpredict.cli.main import main as cli_main

    raise SystemExit(cli_main())
