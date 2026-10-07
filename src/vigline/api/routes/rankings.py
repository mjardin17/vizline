"""Offer ranking routes. Rankings are computed from saved allocations' plans."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ...promo.math import ConversionPlan
from ...promo.rankings import RANKINGS, OfferPlanInput, rank_by_estimated_net
from ..deps import get_conn

router = APIRouter()


def _plan_from_dict(d: dict) -> ConversionPlan:
    return ConversionPlan(
        bonus_face_cents=d["bonus_face_cents"],
        bonus_odds=d["bonus_odds"],
        hedge_odds=d["hedge_odds"],
        stake_returned=d.get("stake_returned", False),
        qualifying_stake_cents=d.get("qualifying_stake_cents", 0),
        qualifying_odds=d.get("qualifying_odds"),
        costs_cents=d.get("costs_cents", 0),
        bonus_proceeds_as_cash=d.get("bonus_proceeds_as_cash", True),
        hedge_allowed=d.get("hedge_allowed", True),
        hedge_restriction_note=d.get("hedge_restriction_note", ""),
    )


def _load_inputs(conn) -> list[OfferPlanInput]:
    rows = conn.execute(
        "SELECT a.id AS alloc_id, a.plan_json, o.id AS offer_id, o.platform, o.title"
        " FROM promo_allocations a JOIN promo_offers o ON o.id = a.offer_id"
        " ORDER BY a.id"
    ).fetchall()
    inputs: list[OfferPlanInput] = []
    for r in rows:
        plan = _plan_from_dict(json.loads(r["plan_json"]))
        rules = conn.execute(
            "SELECT rules_json, verified FROM eligibility_rules WHERE offer_id = ?",
            (r["offer_id"],),
        ).fetchone()
        if rules and rules["verified"]:
            try:
                steps = json.loads(rules["rules_json"]).get("qualification_steps", [])
                step_count = len(steps) if isinstance(steps, list) else 0
            except (json.JSONDecodeError, AttributeError):
                step_count = 0
        else:
            step_count = 0
        inputs.append(
            OfferPlanInput(
                offer_id=r["offer_id"],
                platform=r["platform"],
                title=r["title"],
                plan=plan,
                verified_step_count=step_count,
                inputs_cited=f"allocation {r['alloc_id']} plan; "
                + ("verified eligibility rules on file" if step_count else "no verified eligibility rules on file"),
            )
        )
    return inputs


@router.get("/api/rankings")
def api_rankings(
    metric: str,
    p_bonus_leg_win: Optional[str] = None,
    p_qualifying_win: Optional[str] = None,
    conn=Depends(get_conn),
):
    if metric not in RANKINGS:
        raise HTTPException(400, f"unknown metric {metric!r}; choose from {sorted(RANKINGS)}")
    label, fn = RANKINGS[metric]
    inputs = _load_inputs(conn)
    if metric == "estimated_net":
        probabilities = {}
        if p_bonus_leg_win is not None:
            probabilities["p_bonus_leg_win"] = p_bonus_leg_win
        if p_qualifying_win is not None:
            probabilities["p_qualifying_win"] = p_qualifying_win
        ranked = rank_by_estimated_net(inputs, probabilities or None)
    else:
        ranked = fn(inputs)
    return {
        "metric": metric,
        "label": label,
        "rankings": [
            {
                "offer_id": e.offer_id,
                "platform": e.platform,
                "title": e.title,
                "metric_display": e.metric_display,
                "metric_cents": e.metric_cents,
                "inputs_cited": e.inputs_cited,
                "status": e.status,
            }
            for e in ranked
        ],
    }


@router.get("/api/rankings/metrics")
def api_ranking_metrics():
    return {
        name: label for name, (label, _fn) in RANKINGS.items()
    }
