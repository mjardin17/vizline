"""Audit logging helper. Every state-changing write goes through here."""
from __future__ import annotations

import json
import sqlite3

from .db import utcnow_iso


def log_audit(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    action: str,
    actor: str,
    details: dict | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO audit_log (entity_type, entity_id, action, actor, details_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            entity_type,
            entity_id,
            action,
            actor,
            json.dumps(details) if details is not None else None,
            utcnow_iso(),
        ),
    )
    return cur.lastrowid
