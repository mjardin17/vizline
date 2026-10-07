"""DB-backed service layer. All state-changing writes go through here and
produce audit_log rows. Manually reported records are always unverified;
estimated/modelled profit is never written to realized P&L."""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from ..audit import log_audit
from ..db import utcnow_iso
from . import terms as terms_mod
from .wallet import SOURCE_MANUAL, transition as wallet_transition


# ---------- sources & offers ----------

def create_source(
    conn: sqlite3.Connection,
    *,
    platform: str,
    source_url: str,
    source_type: str,
    jurisdiction: Optional[str] = None,
    actor: str = "system",
) -> int:
    if source_type not in ("official_page", "authorized_feed", "manual_import"):
        raise ValueError(f"invalid source_type: {source_type!r}")
    with conn:
        cur = conn.execute(
            "INSERT INTO promo_sources (platform, source_url, source_type, jurisdiction, added_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (platform, source_url, source_type, jurisdiction, utcnow_iso()),
        )
        log_audit(conn, "promo_source", cur.lastrowid, "created", actor,
                  {"platform": platform, "source_url": source_url})
        return cur.lastrowid


def create_offer(
    conn: sqlite3.Connection,
    *,
    platform: str,
    title: str,
    offer_type: str,
    scope: str = "public",
    source_id: Optional[int] = None,
    actor: str = "system",
) -> int:
    if scope not in ("public", "account_specific"):
        raise ValueError(f"invalid scope: {scope!r}")
    now = utcnow_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO promo_offers (source_id, platform, title, offer_type, scope,"
            " status, review_state, eligibility_state, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'needs_review', 'retrieved', 'unknown', ?, ?)",
            (source_id, platform, title, offer_type, scope, now, now),
        )
        log_audit(conn, "promo_offer", cur.lastrowid, "created", actor,
                  {"platform": platform, "title": title})
        return cur.lastrowid


def list_offers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.*, (SELECT COUNT(*) FROM promo_terms_versions v WHERE v.offer_id = o.id)"
        " AS version_count FROM promo_offers o ORDER BY o.updated_at DESC"
    ).fetchall()


def get_offer(conn: sqlite3.Connection, offer_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM promo_offers WHERE id = ?", (offer_id,)).fetchone()


def get_offer_detail(conn: sqlite3.Connection, offer_id: int) -> dict:
    offer = get_offer(conn, offer_id)
    if offer is None:
        raise terms_mod.TermsError(f"offer {offer_id} not found")
    versions = conn.execute(
        "SELECT id, version_no, text_hash_sha256, source_url, fetched_at, fetched_by,"
        " length(raw_text) AS text_len FROM promo_terms_versions"
        " WHERE offer_id = ? ORDER BY version_no DESC",
        (offer_id,),
    ).fetchall()
    rules = conn.execute(
        "SELECT rules_json, verified, updated_at FROM eligibility_rules WHERE offer_id = ?",
        (offer_id,),
    ).fetchone()
    return {
        "offer": dict(offer),
        "terms_versions": [dict(v) for v in versions],
        "eligibility_rules": dict(rules) if rules else None,
    }


def save_eligibility_rules(
    conn: sqlite3.Connection,
    offer_id: int,
    rules: dict,
    *,
    verified: bool = False,
    actor: str = "system",
) -> None:
    """Store structured eligibility/qualification rules as JSON. Decimal odds
    must be strings inside the JSON (never floats)."""
    payload = json.dumps(rules)
    now = utcnow_iso()
    with conn:
        conn.execute(
            "INSERT INTO eligibility_rules (offer_id, rules_json, verified, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(offer_id) DO UPDATE SET rules_json = excluded.rules_json,"
            " verified = excluded.verified, updated_at = excluded.updated_at",
            (offer_id, payload, 1 if verified else 0, now),
        )
        log_audit(conn, "promo_offer", offer_id, "eligibility_rules_saved", actor,
                  {"verified": verified})


# ---------- wallet ----------

def create_wallet_record(
    conn: sqlite3.Connection,
    *,
    platform: str,
    reward_type: str,
    face_value_cents: int,
    cash_vs_credit: str,
    expires_at: Optional[str] = None,
    manual: bool = True,
    actor: str = "system",
) -> int:
    """Create a wallet record. Manual entries are forced to
    source='manually_entered_unverified' and can never be relabeled."""
    if cash_vs_credit not in ("restricted_credit", "cash"):
        raise ValueError(f"invalid cash_vs_credit: {cash_vs_credit!r}")
    if face_value_cents < 0:
        raise ValueError("face value must be >= 0")
    source = SOURCE_MANUAL if manual else "verified"
    with conn:
        cur = conn.execute(
            "INSERT INTO promo_wallet (platform, reward_type, face_value_cents, state,"
            " cash_vs_credit, expires_at, source, created_at)"
            " VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
            (platform, reward_type, face_value_cents, cash_vs_credit,
             expires_at, source, utcnow_iso()),
        )
        log_audit(conn, "promo_wallet", cur.lastrowid, "created", actor,
                  {"source": source, "face_value_cents": face_value_cents})
        return cur.lastrowid


def transition_wallet_record(
    conn: sqlite3.Connection, record_id: int, to_state: str, actor: str = "system"
) -> None:
    row = conn.execute("SELECT state FROM promo_wallet WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        raise ValueError(f"wallet record {record_id} not found")
    new_state = wallet_transition(row["state"], to_state)  # raises on illegal
    with conn:
        conn.execute("UPDATE promo_wallet SET state = ? WHERE id = ?", (new_state, record_id))
        log_audit(conn, "promo_wallet", record_id, "state_transition", actor,
                  {"from": row["state"], "to": new_state})


def list_wallet(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM promo_wallet ORDER BY created_at DESC").fetchall()


# ---------- allocations / executions / settlements ----------

def create_allocation(
    conn: sqlite3.Connection,
    *,
    offer_id: int,
    plan: dict,
    peak_cash_commitment_cents: Optional[int] = None,
    actor: str = "system",
) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO promo_allocations (offer_id, plan_json, peak_cash_commitment_cents,"
            " status, created_at) VALUES (?, ?, ?, 'planned', ?)",
            (offer_id, json.dumps(plan), peak_cash_commitment_cents, utcnow_iso()),
        )
        log_audit(conn, "promo_allocation", cur.lastrowid, "created", actor,
                  {"offer_id": offer_id})
        return cur.lastrowid


def report_execution(
    conn: sqlite3.Connection,
    *,
    allocation_id: int,
    report: dict,
    actor: str = "user",
) -> int:
    """Record a manually reported execution. ALWAYS unverified (DB CHECK
    enforces verified = 0)."""
    with conn:
        cur = conn.execute(
            "INSERT INTO promo_executions (allocation_id, report_json, reported_at, verified)"
            " VALUES (?, ?, ?, 0)",
            (allocation_id, json.dumps(report), utcnow_iso()),
        )
        log_audit(conn, "promo_execution", cur.lastrowid, "reported", actor,
                  {"allocation_id": allocation_id, "verified": False})
        return cur.lastrowid


def record_settlement(
    conn: sqlite3.Connection,
    *,
    execution_id: int,
    realized_net_cents: int,
    note: str = "",
    actor: str = "system",
) -> int:
    """Record realized P&L from a settled execution ONLY. Estimated or modelled
    profit must never be passed here."""
    with conn:
        cur = conn.execute(
            "INSERT INTO promo_settlements (execution_id, realized_net_cents, settled_at, note)"
            " VALUES (?, ?, ?, ?)",
            (execution_id, realized_net_cents, utcnow_iso(), note),
        )
        log_audit(conn, "promo_settlement", cur.lastrowid, "recorded", actor,
                  {"execution_id": execution_id, "realized_net_cents": realized_net_cents})
        return cur.lastrowid
