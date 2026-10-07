"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request

from .. import __version__
from ..config import load_settings
from ..db import get_connection, run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = get_connection(app.state.db_path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    yield


def db_path_for(request: Request) -> str:
    return request.app.state.db_path


def create_app(db_path: Optional[str | Path] = None) -> FastAPI:
    settings = load_settings()
    resolved = str(db_path or settings.db_path)

    app = FastAPI(title="Vigline", version=__version__, lifespan=lifespan)
    app.state.db_path = resolved

    from .routes import alerts as alerts_routes
    from .routes import calculator as calculator_routes
    from .routes import health as health_routes
    from .routes import offers as offers_routes
    from .routes import rankings as rankings_routes
    from .routes import wallet as wallet_routes

    app.include_router(health_routes.router)
    app.include_router(offers_routes.router)
    app.include_router(wallet_routes.router)
    app.include_router(calculator_routes.router)
    app.include_router(rankings_routes.router)
    app.include_router(alerts_routes.router)

    # Jinja2 web UI (import here to avoid circulars)
    from ..web.routes import router as web_router

    app.include_router(web_router)
    return app


# Default app instance for `uvicorn vigline.api.app:app`.
app = create_app()
