"""Scaffold smoke test: the package must import and expose the CLI entry point."""

import cloudpredict


def test_package_imports() -> None:
    assert callable(cloudpredict.main)
