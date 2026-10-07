"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Request


def get_conn(request: Request):
    from ..db import get_connection

    conn = get_connection(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()
