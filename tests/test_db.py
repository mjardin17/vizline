"""Database migration and constraint tests."""
from __future__ import annotations

import sqlite3

import pytest

from vigline.db import applied_versions, get_connection, run_migrations
from vigline.promo import service


def test_migrations_up_from_zero(tmp_path):
    db_path = tmp_path / "fresh.db"
    conn = get_connection(db_path)
    try:
        assert applied_versions(conn) == set()
        applied = run_migrations(conn)
        assert applied == [1]
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for expected in (
            "schema_migrations", "promo_sources", "promo_offers",
            "promo_terms_versions", "eligibility_rules", "promo_wallet",
            "promo_allocations", "promo_executions", "promo_settlements",
            "alerts", "audit_log",
        ):
            assert expected in tables, f"missing table {expected}"
        # re-running is a no-op
        assert run_migrations(conn) == []
    finally:
        conn.close()


def test_executions_always_unverified_enforced(conn):
    offer_id = service.create_offer(
        conn, platform="X", title="T", offer_type="bonus_bet"
    )
    alloc_id = service.create_allocation(conn, offer_id=offer_id, plan={})
    # direct SQL trying to mark verified must fail at the CHECK constraint
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO promo_executions (allocation_id, report_json, reported_at, verified)"
            " VALUES (?, ?, ?, 1)",
            (alloc_id, "{}", "2026-10-07T00:00:00+00:00"),
        )


def test_wallet_face_value_nonnegative(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO promo_wallet (platform, reward_type, face_value_cents, state,"
            " cash_vs_credit, source, created_at)"
            " VALUES ('X', 'bonus_bet', -5, 'pending', 'restricted_credit',"
            " 'manually_entered_unverified', '2026-10-07T00:00:00+00:00')"
        )


def test_alert_identity_unique(conn):
    conn.execute(
        "INSERT INTO alerts (kind, market_key, book, side, price_bucket, details_json,"
        " delivery_status, created_at)"
        " VALUES ('positive_ev', 'm', 'b', 's', '2.1000', '{}', 'delivery_disabled', 't')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alerts (kind, market_key, book, side, price_bucket, details_json,"
            " delivery_status, created_at)"
            " VALUES ('positive_ev', 'm', 'b', 's', '2.1000', '{}', 'delivery_disabled', 't')"
        )


def test_audit_log_written_on_offer_create(conn):
    offer_id = service.create_offer(
        conn, platform="X", title="T", offer_type="bonus_bet", actor="tester"
    )
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE entity_type = 'promo_offer' AND entity_id = ?",
        (offer_id,),
    ).fetchall()
    assert len(rows) >= 1
    assert rows[0]["actor"] == "tester"
