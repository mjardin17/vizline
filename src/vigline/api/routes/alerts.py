"""Alert routes: log inspection, manual scan trigger, scheduler status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...alerts import scheduler as sched_mod
from ...alerts.store import list_alerts
from ..deps import get_conn

router = APIRouter()


@router.get("/api/alerts")
def api_list_alerts(conn=Depends(get_conn)):
    return {"alerts": [dict(r) for r in list_alerts(conn)]}


@router.post("/api/alerts/scan/run")
def api_run_scan(request: Request):
    """Run one scan cycle now (local testing). Delivery stays disabled:
    alerts are recorded with delivery_status='delivery_disabled'."""
    summary = sched_mod.run_scan_job(request.app.state.db_path)
    return summary


@router.post("/api/alerts/expiry/run")
def api_run_expiry(request: Request):
    summary = sched_mod.run_expiry_check(request.app.state.db_path)
    return summary


@router.get("/api/alerts/scheduler")
def api_scheduler_status(request: Request):
    """Describe the scheduler WITHOUT starting it."""
    scheduler = sched_mod.build_scheduler(request.app.state.db_path)
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "next_run_time": sched_mod.next_fire_time_iso(job),
            }
        )
    if scheduler.running:
        scheduler.shutdown(wait=False)
    return {
        "implementation": sched_mod.IMPLEMENTATION_NOTE,
        "running": False,
        "delivery": "disabled — no destination configured",
        "jobs": jobs,
    }
