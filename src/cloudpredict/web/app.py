"""ASGI entry point: ``uvicorn cloudpredict.web.app:app``."""

from __future__ import annotations

from cloudpredict.web.dashboard import create_dashboard_app

app = create_dashboard_app()
