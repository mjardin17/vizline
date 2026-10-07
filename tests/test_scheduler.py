"""Tests for the alert scheduler: dedup, cron timing, delivery-disabled, expiry."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from vigline.alerts import scheduler as sched_mod
from vigline.alerts.scheduler import (
    DeliveryNotConfiguredError,
    ScanResult,
    build_scheduler,
    deliver_alert,
    run_expiry_check,
    run_scan_job,
)
from vigline.alerts.store import Opportunity, list_alerts, record_opportunity
from vigline.promo import service


def _opp(price="2.10", **kw):
    base = dict(
        kind="positive_ev", market_key="nfl:test", book="DraftKings",
        side="home", price=Decimal(price),
    )
    base.update(kw)
    return Opportunity(**base)


def test_dedup_same_opportunity_twice(conn):
    r1 = record_opportunity(conn, _opp())
    r2 = record_opportunity(conn, _opp())
    assert r1.recorded and r1.reason == "new"
    assert not r2.recorded and r2.reason == "duplicate"
    assert r2.alert_id == r1.alert_id
    assert len(list_alerts(conn)) == 1


def test_improved_price_creates_new_alert_and_supersedes(conn):
    r1 = record_opportunity(conn, _opp("2.10"))
    r2 = record_opportunity(conn, _opp("2.20"))  # higher is better
    assert r2.recorded and r2.reason == "new"
    old = conn.execute("SELECT superseded_by FROM alerts WHERE id = ?", (r1.alert_id,)).fetchone()
    assert old["superseded_by"] == r2.alert_id
    assert len(list_alerts(conn)) == 2


def test_worse_price_suppressed(conn):
    record_opportunity(conn, _opp("2.20"))
    r2 = record_opportunity(conn, _opp("2.10"))
    assert not r2.recorded and r2.reason == "not_improved"
    assert len(list_alerts(conn)) == 1


def test_lower_better_direction(conn):
    # e.g. lay-style odds where a lower price is an improvement
    r1 = record_opportunity(conn, _opp("2.20", better_direction="lower"))
    r2 = record_opportunity(conn, _opp("2.10", better_direction="lower"))
    assert r2.recorded and r2.reason == "new"
    r3 = record_opportunity(conn, _opp("2.30", better_direction="lower"))
    assert not r3.recorded and r3.reason == "not_improved"


def test_scan_cycle_stub_reports_no_data_source(tmp_path):
    summary = run_scan_job(tmp_path / "s.db")
    assert summary["scan_status"] == "no_data_source_configured"
    assert summary["recorded"] == []
    assert summary["delivery"] == "delivery_disabled"
    assert "NEW" in summary["implementation"]


def test_scan_cycle_records_and_dedups(tmp_path):
    dbp = tmp_path / "s.db"
    opps = [_opp(), _opp(kind="arbitrage", market_key="nfl:test2", side="away", price="1.95")]
    def fake_scan():
        return ScanResult(status="ok", opportunities=opps)
    first = run_scan_job(dbp, fake_scan)
    second = run_scan_job(dbp, fake_scan)
    assert len(first["recorded"]) == 2
    assert second["recorded"] == []
    assert second["suppressed_duplicates"] == 2
    assert first["delivery"] == "delivery_disabled"


def test_deliver_without_destination_raises():
    with pytest.raises(DeliveryNotConfiguredError):
        sched_mod.deliver_alert(1, None)


def test_scan_cron_fires_at_et_hours():
    tz = ZoneInfo("America/New_York")
    trigger = CronTrigger(hour=sched_mod.SCAN_CRON_HOUR, timezone="America/New_York")
    t = datetime(2026, 10, 7, 0, 0, tzinfo=tz)
    hours = []
    for _ in range(8):
        t = trigger.get_next_fire_time(None, t)
        assert t is not None
        hours.append(t.hour)
        t = t + timedelta(seconds=1)
    assert hours == [9, 11, 13, 15, 17, 19, 21, 23]
    # after 23:30 ET the next fire is 09:00 the next day
    late = datetime(2026, 10, 7, 23, 30, tzinfo=tz)
    nxt = trigger.get_next_fire_time(None, late)
    assert (nxt.day, nxt.hour, nxt.minute) == (8, 9, 0)


def test_build_scheduler_defines_jobs_not_started(tmp_path):
    sched = build_scheduler(tmp_path / "s.db")
    try:
        jobs = {j.id: j for j in sched.get_jobs()}
        assert set(jobs) == {sched_mod.SCAN_JOB_ID, sched_mod.EXPIRY_JOB_ID}
        assert not sched.running
        assert "9,11,13,15,17,19,21,23" in str(jobs[sched_mod.SCAN_JOB_ID].trigger)
        assert "NEW" in jobs[sched_mod.SCAN_JOB_ID].name
    finally:
        if sched.running:
            sched.shutdown(wait=False)


def _issued_wallet_record(conn, expires_at_iso):
    rid = service.create_wallet_record(
        conn, platform="DraftKings", reward_type="bonus_bet",
        face_value_cents=10000, cash_vs_credit="restricted_credit",
        expires_at=expires_at_iso, manual=True,
    )
    service.transition_wallet_record(conn, rid, "issued")
    return rid


def test_expiry_worker_alerts_within_window(tmp_path):
    dbp = tmp_path / "s.db"
    from vigline.db import get_connection, run_migrations
    conn = get_connection(dbp)
    run_migrations(conn)
    ref = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    soon = (ref + timedelta(hours=48)).isoformat()
    far = (ref + timedelta(hours=100)).isoformat()
    past = (ref - timedelta(hours=1)).isoformat()
    _issued_wallet_record(conn, soon)
    _issued_wallet_record(conn, far)
    _issued_wallet_record(conn, past)
    conn.close()

    summary = run_expiry_check(dbp, window_hours=72, now=ref)
    assert len(summary["recorded"]) == 1
    assert summary["delivery"] == "delivery_disabled"

    # second run: duplicate suppressed
    summary2 = run_expiry_check(dbp, window_hours=72, now=ref)
    assert summary2["recorded"] == []


def test_expiry_worker_ignores_non_issued(tmp_path):
    dbp = tmp_path / "s.db"
    from vigline.db import get_connection, run_migrations
    conn = get_connection(dbp)
    run_migrations(conn)
    ref = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    soon = (ref + timedelta(hours=48)).isoformat()
    rid = service.create_wallet_record(
        conn, platform="X", reward_type="bonus_bet", face_value_cents=500,
        cash_vs_credit="restricted_credit", expires_at=soon, manual=True,
    )
    # stays in 'pending' — not issued
    conn.close()
    summary = run_expiry_check(dbp, window_hours=72, now=ref)
    assert summary["recorded"] == []
