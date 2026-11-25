"""``cloudpredict web``: launch the interactive FinOps control-plane dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cloudpredict.infrastructure.apply import DEFAULT_DB, DEFAULT_MODELS_DIR
from cloudpredict.infrastructure.config import DEFAULT_CONFIG_PATH
from cloudpredict.web.dashboard import create_dashboard_app


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("web", help="start the interactive web control-plane dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="bind port (default: 8000)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    import uvicorn

    app = create_dashboard_app(
        config_path=args.config,
        db_path=args.db,
        models_dir=args.models_dir,
    )
    print("=" * 64)
    print("  cloudPredict — Autonomous Cloud Storage Tiering & FinOps")
    print(f"  Live Control Plane: http://{args.host}:{args.port}")
    print(f"  Database:           {args.db}")
    print(f"  Config:             {args.config}")
    print("=" * 64)
    print("  Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
