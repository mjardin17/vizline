"""Alert record store with deduplication.

Alert identity = (kind, market_key, book, side, price_bucket). A repeat of an
identical opportunity is suppressed. A strictly improved price produces a new
row (new price_bucket) and supersedes the older rows.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..db import utcnow_iso

DELIVERY_DISABLED = "delivery_disabled"


@dataclass
class Opportunity:
    kind: str  # 'arbitrage' | 'positive_ev' | 'promo_expiry' | 'terms_changed'
    market_key: str
    book: str
    side: str
    price: Decimal
    better_direction: str = "higher"  # 'higher' | 'lower'
    details: dict = field(default_factory=dict)

    @property
    def price_bucket(self) -> str:
        return f"{self.price:.4f}"


@dataclass
class RecordResult:
    recorded: bool
    alert_id: Optional[int]
    reason: str  # 'new' | 'duplicate' | 'not_improved'


def _price_of(row: sqlite3.Row) -> Decimal:
    return Decimal(row["price_bucket"])


def record_opportunity(conn: sqlite3.Connection, opp: Opportunity) -> RecordResult:
    if opp.better_direction not in ("higher", "lower"):
        raise ValueError(f"invalid better_direction: {opp.better_direction!r}")
    existing = conn.execute(
        "SELECT id, price_bucket FROM alerts"
        " WHERE kind = ? AND market_key = ? AND book = ? AND side = ?",
        (opp.kind, opp.market_key, opp.book, opp.side),
    ).fetchall()

    for row in existing:
        if row["price_bucket"] == opp.price_bucket:
            return RecordResult(recorded=False, alert_id=row["id"], reason="duplicate")

    if existing:
        prices = [_price_of(r) for r in existing]
        if opp.better_direction == "higher" and max(prices) >= opp.price:
            return RecordResult(recorded=False, alert_id=None, reason="not_improved")
        if opp.better_direction == "lower" and min(prices) <= opp.price:
            return RecordResult(recorded=False, alert_id=None, reason="not_improved")

    with conn:
        cur = conn.execute(
            "INSERT INTO alerts (kind, market_key, book, side, price_bucket,"
            " details_json, delivery_status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                opp.kind,
                opp.market_key,
                opp.book,
                opp.side,
                opp.price_bucket,
                json.dumps(opp.details),
                DELIVERY_DISABLED,
                utcnow_iso(),
            ),
        )
        new_id = cur.lastrowid
        for row in existing:
            conn.execute(
                "UPDATE alerts SET superseded_by = ? WHERE id = ?", (new_id, row["id"])
            )
    return RecordResult(recorded=True, alert_id=new_id, reason="new")


def list_alerts(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
