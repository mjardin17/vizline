"""Vigline alert scheduler — NEW implementation (October 2026 clean-room rebuild).

This scheduler does NOT preserve any historical job; there was no verified
prior implementation to preserve. Behavioural spec it implements:

- Odds/EV scan every 2 hours at 09, 11, 13, 15, 17, 19, 21, 23 America/New_York.
- Detect fresh arbitrage and new-or-improved +EV at 2%+ (the odds/EV engines
  are milestone 2; the scan step is a defined interface stub that currently
  reports "no data source configured").
- Deduplicate: alert identity = (kind, market_key, book, side, price_bucket);
  suppress repeats unless the opportunity is new or strictly improved.
- Delivery is DISABLED until a destination is configured. Alerts are recorded
  to the DB with delivery_status='delivery_disabled' and logged. It is
  impossible to silently "send" nowhere: deliver() without a destination raises.
- Separate promo-expiry notification worker: checks the wallet for rewards
  expiring within a configurable window (default 72h).

The scheduler is created stopped; the application decides when to start it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from ..config import TIMEZONE
from ..db import get_connection, run_migrations
from .store import DELIVERY_DISABLED, Opportunity, RecordResult, record_opportunity

log = logging.getLogger("vigline.alerts")

SCAN_JOB_ID = "vigline_scan"
EXPIRY_JOB_ID = "promo_expiry_check"
# Cron spec: every 2 hours, 9am through 11pm, America/New_York.
SCAN_CRON_HOUR = "9,11,13,15,17,19,21,23"

IMPLEMENTATION_NOTE = (
    "NEW scheduler implementation (Oct 2026 clean-room rebuild); "
    "does not preserve any historical job."
)


@dataclass
class ScanResult:
    status: str  # "ok" | "no_data_source_configured"
    opportunities: list[Opportunity] = field(default_factory=list)
    message: str = ""


def stub_scan() -> ScanResult:
    """Defined scan interface. Until milestone 2 provides the odds/EV engines,
    there is no data source, so this honestly reports that."""
    return ScanResult(
        status="no_data_source_configured",
        opportunities=[],
        message="no odds data source configured (milestone 2)",
    )


class DeliveryNotConfiguredError(Exception):
    """Raised when code attempts delivery without a configured destination."""


@dataclass(frozen=True)
class AlertDestination:
    kind: str  # e.g. "webhook"
    target: str


def deliver_alert(alert_id: int, destination: Optional[AlertDestination]) -> None:
    """Deliver a recorded alert. Raises DeliveryNotConfiguredError when no
    destination is configured — silent sends are impossible by construction."""
    if destination is None:
        raise DeliveryNotConfiguredError(
            "alert delivery is disabled: no destination configured. "
            f"Alert {alert_id} remains recorded with delivery_status='delivery_disabled'."
        )
    # Future milestone: implement actual delivery per destination.kind here.
    raise NotImplementedError(f"delivery kind {destination.kind!r} not implemented")


def run_scan_job(
    db_path: str | Path,
    scan_fn: Optional[Callable[[], ScanResult]] = None,
) -> dict:
    """Execute one scan cycle: run the scan, dedup, record alerts.

    Returns a summary dict. Alerts are recorded with delivery_status
    'delivery_disabled' (see module docstring)."""
    conn = get_connection(Path(db_path))
    try:
        run_migrations(conn)
        result = (scan_fn or stub_scan)()
        recorded: list[int] = []
        suppressed = 0
        if result.status == "ok":
            for opp in result.opportunities:
                rec: RecordResult = record_opportunity(conn, opp)
                if rec.recorded and rec.alert_id is not None:
                    recorded.append(rec.alert_id)
                    log.info(
                        "alert recorded (delivery disabled): %s %s %s %s",
                        opp.kind, opp.market_key, opp.book, opp.side,
                    )
                else:
                    suppressed += 1
        summary = {
            "implementation": IMPLEMENTATION_NOTE,
            "scan_status": result.status,
            "scan_message": result.message,
            "recorded": recorded,
            "suppressed_duplicates": suppressed,
            "delivery": DELIVERY_DISABLED,
        }
        log.info("scan cycle complete: %s", summary)
        return summary
    finally:
        conn.close()


def run_expiry_check(
    db_path: str | Path,
    window_hours: int = 72,
    now: Optional[datetime] = None,
) -> dict:
    """Promo-expiry worker: alert on issued wallet rewards expiring within the
    window. Alerts are recorded delivery-disabled, like scan alerts."""
    ref = now or datetime.now(timezone.utc)
    window_end = ref + timedelta(hours=window_hours)
    conn = get_connection(Path(db_path))
    try:
        run_migrations(conn)
        rows = conn.execute(
            "SELECT id, platform, reward_type, face_value_cents, expires_at"
            " FROM promo_wallet WHERE state = 'issued' AND expires_at IS NOT NULL"
        ).fetchall()
        recorded: list[int] = []
        for row in rows:
            try:
                expires = datetime.fromisoformat(row["expires_at"])
            except (ValueError, TypeError):
                continue
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if not (ref < expires <= window_end):
                continue
            # The expiry timestamp doubles as the price: identical expiries
            # dedup, changed expiries produce new alerts.
            opp = Opportunity(
                kind="promo_expiry",
                market_key=f"wallet:{row['id']}",
                book=row["platform"],
                side=row["reward_type"],
                price=Decimal(expires.strftime("%Y%m%d%H%M")),
                better_direction="lower",
                details={
                    "wallet_id": row["id"],
                    "face_value_cents": row["face_value_cents"],
                    "expires_at": row["expires_at"],
                    "window_hours": window_hours,
                },
            )
            rec = record_opportunity(conn, opp)
            if rec.recorded and rec.alert_id is not None:
                recorded.append(rec.alert_id)
        summary = {
            "implementation": IMPLEMENTATION_NOTE,
            "checked_at": ref.isoformat(),
            "window_hours": window_hours,
            "recorded": recorded,
            "delivery": DELIVERY_DISABLED,
        }
        log.info("expiry check complete: %s", summary)
        return summary
    finally:
        conn.close()


def next_fire_time_iso(job) -> Optional[str]:
    """Next fire time for a job, computed from its trigger.

    Used for status display on a not-yet-started scheduler: APScheduler's
    Job.next_run_time requires a running scheduler, so we ask the trigger.
    """
    try:
        nxt = job.trigger.get_next_fire_time(None, datetime.now(ZoneInfo(TIMEZONE)))
    except Exception:
        return None
    return nxt.isoformat() if nxt else None


def build_scheduler(
    db_path: str | Path,
    *,
    scan_fn: Optional[Callable[[], ScanResult]] = None,
    expiry_window_hours: int = 72,
) -> BackgroundScheduler:
    """Create (not start) the Vigline scheduler.

    Jobs:
    - vigline_scan: cron every 2h at 9,11,13,15,17,19,21,23 America/New_York.
    - promo_expiry_check: daily 08:00 America/New_York.
    """
    db = str(db_path)
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        lambda: run_scan_job(db, scan_fn),
        trigger="cron",
        hour=SCAN_CRON_HOUR,
        minute=0,
        id=SCAN_JOB_ID,
        name="Vigline scan (NEW Oct 2026 implementation)",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: run_expiry_check(db, window_hours=expiry_window_hours),
        trigger="cron",
        hour=8,
        minute=0,
        id=EXPIRY_JOB_ID,
        name="Promo expiry check (NEW Oct 2026 implementation)",
        replace_existing=True,
    )
    return scheduler
