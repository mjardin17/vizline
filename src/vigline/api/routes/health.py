"""Health endpoint: honest status of every subsystem."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ... import __version__
from ...alerts.scheduler import IMPLEMENTATION_NOTE
from ...config import load_authorized_sources, load_settings
from ...db import get_connection

router = APIRouter()


@router.get("/health")
def health(request: Request):
    settings = load_settings()
    sources = load_authorized_sources(settings)
    try:
        conn = get_connection(request.app.state.db_path)
        conn.execute("SELECT 1")
        conn.close()
        db_status = "ok"
    except Exception:
        db_status = "unavailable"
    return {
        "status": "ok",
        "version": __version__,
        "database": db_status,
        "scheduler": "not_started",
        "scheduler_note": IMPLEMENTATION_NOTE,
        "data_sources": {
            "odds": "unavailable — odds/EV engines are milestone 2",
            "promo_terms": (
                f"{len(sources)} authorized source(s) configured"
                if sources
                else "unavailable — no authorized source configured"
            ),
        },
        "delivery": "disabled — no destination configured",
    }
