"""Server-rendered web UI (Jinja2). No mock data: every empty state says what
is missing and what would provide it."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..alerts import scheduler as sched_mod
from ..alerts.store import list_alerts
from ..api.deps import get_conn
from ..config import load_authorized_sources, load_settings
from ..promo import math as promo_math
from ..promo import service, terms
from ..promo.math import ConversionPlan
from ..promo.wallet import _TRANSITIONS

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _fmt_cents(cents) -> str:
    if cents is None:
        return "—"
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}${c // 100}.{c % 100:02d}"


_templates.env.filters["cents"] = _fmt_cents


def _sources():
    return load_authorized_sources(load_settings())


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn=Depends(get_conn)):
    return _templates.TemplateResponse(
        request, "dashboard.html",
        {
            "request": request,
            "offers": [dict(r) for r in service.list_offers(conn)],
            "authorized_sources": _sources(),
        },
    )


@router.post("/offers/new")
def web_create_offer(
    request: Request,
    platform: str = Form(...),
    title: str = Form(...),
    offer_type: str = Form(...),
    scope: str = Form("public"),
    conn=Depends(get_conn),
):
    offer_id = service.create_offer(
        conn, platform=platform, title=title, offer_type=offer_type,
        scope=scope, actor="web",
    )
    return RedirectResponse(f"/offers/{offer_id}", status_code=303)


@router.get("/offers/{offer_id}", response_class=HTMLResponse)
def offer_detail(request: Request, offer_id: int, conn=Depends(get_conn)):
    try:
        detail = service.get_offer_detail(conn, offer_id)
    except terms.TermsError:
        return HTMLResponse("Offer not found.", status_code=404)
    return _templates.TemplateResponse(
        request, "offer_detail.html",
        {
            "request": request,
            "offer": detail["offer"],
            "versions": detail["terms_versions"],
            "rules": detail["eligibility_rules"],
            "authorized_sources": _sources(),
        },
    )


@router.post("/offers/{offer_id}/terms/text")
def web_capture_text(
    offer_id: int,
    raw_text: str = Form(...),
    source_url: Optional[str] = Form(None),
    conn=Depends(get_conn),
):
    try:
        terms.capture_terms_text(
            conn, offer_id=offer_id, raw_text=raw_text,
            source_url=source_url or None, fetched_by="manual", actor="web",
        )
    except terms.TermsError as exc:
        return HTMLResponse(f"Capture failed: {exc}", status_code=422)
    return RedirectResponse(f"/offers/{offer_id}", status_code=303)


@router.post("/offers/{offer_id}/terms/fetch")
def web_fetch_terms(offer_id: int, url: str = Form(...), conn=Depends(get_conn)):
    sources = _sources()
    if not sources:
        return HTMLResponse(terms.NO_SOURCE_MESSAGE, status_code=409)
    try:
        text = terms.fetch_authorized_text(url, sources)
        terms.capture_terms_text(
            conn, offer_id=offer_id, raw_text=text, source_url=url,
            fetched_by="authorized_fetch", actor="web",
        )
    except terms.TermsError as exc:
        return HTMLResponse(f"Fetch failed: {exc}", status_code=409)
    return RedirectResponse(f"/offers/{offer_id}", status_code=303)


@router.post("/offers/{offer_id}/review")
def web_set_review(offer_id: int, review_state: str = Form(...), conn=Depends(get_conn)):
    try:
        terms.set_review_state(conn, offer_id, review_state, actor="web")
    except terms.TermsError as exc:
        return HTMLResponse(f"Failed: {exc}", status_code=422)
    return RedirectResponse(f"/offers/{offer_id}", status_code=303)


@router.post("/offers/{offer_id}/eligibility")
def web_set_eligibility(offer_id: int, state: str = Form(...), conn=Depends(get_conn)):
    try:
        terms.set_eligibility(conn, offer_id, state, actor="web")
    except terms.TermsError as exc:
        return HTMLResponse(f"Failed: {exc}", status_code=422)
    return RedirectResponse(f"/offers/{offer_id}", status_code=303)


@router.post("/offers/{offer_id}/rules")
def web_save_rules(
    offer_id: int,
    rules_json: str = Form(...),
    verified: Optional[str] = Form(None),
    conn=Depends(get_conn),
):
    try:
        rules = json.loads(rules_json)
    except json.JSONDecodeError as exc:
        return HTMLResponse(f"Invalid JSON: {exc}", status_code=422)
    service.save_eligibility_rules(
        conn, offer_id, rules, verified=bool(verified), actor="web"
    )
    return RedirectResponse(f"/offers/{offer_id}", status_code=303)


@router.get("/wallet", response_class=HTMLResponse)
def wallet_page(request: Request, conn=Depends(get_conn)):
    records = [dict(r) for r in service.list_wallet(conn)]
    return _templates.TemplateResponse(
        request, "wallet.html",
        {"request": request, "records": records, "allowed": _TRANSITIONS},
    )


@router.post("/wallet/new")
def web_wallet_new(
    platform: str = Form(...),
    reward_type: str = Form(...),
    face_value_cents: int = Form(...),
    cash_vs_credit: str = Form(...),
    expires_at: Optional[str] = Form(None),
    conn=Depends(get_conn),
):
    try:
        service.create_wallet_record(
            conn, platform=platform, reward_type=reward_type,
            face_value_cents=int(face_value_cents), cash_vs_credit=cash_vs_credit,
            expires_at=expires_at or None, manual=True, actor="web",
        )
    except ValueError as exc:
        return HTMLResponse(f"Failed: {exc}", status_code=422)
    return RedirectResponse("/wallet", status_code=303)


@router.post("/wallet/{record_id}/transition")
def web_wallet_transition(record_id: int, to_state: str = Form(...), conn=Depends(get_conn)):
    try:
        service.transition_wallet_record(conn, record_id, to_state, actor="web")
    except Exception as exc:
        return HTMLResponse(f"Failed: {exc}", status_code=422)
    return RedirectResponse("/wallet", status_code=303)


@router.get("/calculator", response_class=HTMLResponse)
def calculator_form(request: Request):
    return _templates.TemplateResponse(
        request, "calculator.html", { "form": {}, "rows": None, "hedge": None, "error": None}
    )


@router.post("/calculator", response_class=HTMLResponse)
def calculator_run(
    request: Request,
    bonus_face_cents: int = Form(...),
    bonus_odds: str = Form(...),
    hedge_odds: str = Form(...),
    qualifying_stake_cents: int = Form(0),
    qualifying_odds: Optional[str] = Form(None),
    costs_cents: int = Form(0),
    stake_returned: Optional[str] = Form(None),
    bonus_proceeds_as_cash: Optional[str] = Form(None),
    hedge_allowed: Optional[str] = Form(None),
    hedge_restriction_note: str = Form(""),
):
    form = {
        "bonus_face_cents": bonus_face_cents,
        "bonus_odds": bonus_odds,
        "hedge_odds": hedge_odds,
        "qualifying_stake_cents": qualifying_stake_cents,
        "qualifying_odds": qualifying_odds or "",
        "costs_cents": costs_cents,
        "stake_returned": bool(stake_returned),
        "bonus_proceeds_as_cash": bonus_proceeds_as_cash is None or bool(bonus_proceeds_as_cash),
        "hedge_allowed": hedge_allowed is None or bool(hedge_allowed),
        "hedge_restriction_note": hedge_restriction_note,
    }
    ctx = {"request": request, "form": form, "rows": None, "hedge": None,
           "peak_cents": None, "error": None}
    try:
        plan = ConversionPlan(
            bonus_face_cents=int(bonus_face_cents),
            bonus_odds=bonus_odds,
            hedge_odds=hedge_odds,
            stake_returned=bool(stake_returned),
            qualifying_stake_cents=int(qualifying_stake_cents or 0),
            qualifying_odds=qualifying_odds or None,
            costs_cents=int(costs_cents or 0),
            bonus_proceeds_as_cash=form["bonus_proceeds_as_cash"],
            hedge_allowed=form["hedge_allowed"],
            hedge_restriction_note=hedge_restriction_note or "",
        )
        hedge = promo_math.plan_equal_return_hedge(
            plan.bonus_face_cents, plan.bonus_odds, plan.hedge_odds,
            hedge_allowed=plan.hedge_allowed,
            hedge_restriction_note=plan.hedge_restriction_note,
        )
        if isinstance(hedge, promo_math.HedgeRefusal):
            ctx["hedge"] = {"refused": True, "reason": hedge.reason}
        else:
            ctx["hedge"] = {
                "refused": False,
                "bonus_face_cents": hedge.bonus_face_cents,
                "bonus_odds": str(hedge.bonus_odds),
                "hedge_odds": str(hedge.hedge_odds),
                "hedge_stake_cents": hedge.hedge_stake_cents,
                "locked_cents": hedge.locked_cents,
                "conversion_rate": hedge.conversion_rate,
            }
        rows = promo_math.build_scenario_matrix(plan)
        ctx["rows"] = [
            {
                "key": r.key, "label": r.label, "cash_out_cents": r.cash_out_cents,
                "cash_in_cents": r.cash_in_cents, "net_cents": r.net_cents,
                "restricted_credit_cents": r.restricted_credit_cents,
                "state": r.state, "notes": r.notes,
            }
            for r in rows
        ]
        ctx["peak_cents"] = promo_math.peak_cash_commitment(plan)
    except (ValueError, TypeError) as exc:
        ctx["error"] = str(exc)
    return _templates.TemplateResponse(request, "calculator.html", ctx)


@router.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request, conn=Depends(get_conn)):
    scheduler = sched_mod.build_scheduler(request.app.state.db_path)
    jobs = [
        {"id": j.id, "name": j.name, "trigger": str(j.trigger),
         "next_run_time": sched_mod.next_fire_time_iso(j)}
        for j in scheduler.get_jobs()
    ]
    if scheduler.running:
        scheduler.shutdown(wait=False)
    return _templates.TemplateResponse(
        request, "alerts.html",
        {
            "request": request,
            "alerts": [dict(r) for r in list_alerts(conn)],
            "jobs": jobs,
            "scheduler_note": sched_mod.IMPLEMENTATION_NOTE,
        },
    )


@router.post("/alerts/scan/run")
def web_run_scan(request: Request):
    sched_mod.run_scan_job(request.app.state.db_path)
    return RedirectResponse("/alerts", status_code=303)


@router.post("/alerts/expiry/run")
def web_run_expiry(request: Request):
    sched_mod.run_expiry_check(request.app.state.db_path)
    return RedirectResponse("/alerts", status_code=303)
