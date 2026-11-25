"""Unit tests for ``cloudpredict web`` CLI parser."""

from __future__ import annotations

from cloudpredict.cli.main import build_parser


def test_web_parser_registration() -> None:
    parser = build_parser()
    args = parser.parse_args(["web", "--host", "0.0.0.0", "--port", "9999"])
    assert args.command == "web"
    assert args.host == "0.0.0.0"
    assert args.port == 9999
