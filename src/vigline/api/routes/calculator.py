"""Scenario calculator routes (pure math, no persistence)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...promo import math as promo_math
from ...promo.math import (
    ConversionPlan,
    HedgePlan,
    HedgeRefusal,
    ProbabilityAssumptionsMissing,
    bonus_leg_outcomes,
    build_scenario_matrix,
    cash_leg_outcomes,
    estimated_conversion_value,
    peak_cash_commitment,
    plan_equal_return_hedge,
)

router = APIRouter()


class PlanIn(BaseModel):
    bonus_face_cents: int
    bonus_odds: str
    hedge_odds: str
    stake_returned: bool = False
    qualifying_stake_cents: int = 0
    qualifying_odds: Optional[str] = None
    costs_cents: int = 0
    bonus_proceeds_as_cash: bool = True
    hedge_allowed: bool = True
    hedge_restriction_note: str = ""


def to_plan(p: PlanIn) -> ConversionPlan:
    return ConversionPlan(
        bonus_face_cents=p.bonus_face_cents,
        bonus_odds=p.bonus_odds,
        hedge_odds=p.hedge_odds,
        stake_returned=p.stake_returned,
        qualifying_stake_cents=p.qualifying_stake_cents,
        qualifying_odds=p.qualifying_odds,
        costs_cents=p.costs_cents,
        bonus_proceeds_as_cash=p.bonus_proceeds_as_cash,
        hedge_allowed=p.hedge_allowed,
        hedge_restriction_note=p.hedge_restriction_note,
    )


def _row_to_dict(row) -> dict:
    return {
        "key": row.key,
        "label": row.label,
        "cash_out_cents": row.cash_out_cents,
        "cash_in_cents": row.cash_in_cents,
        "net_cents": row.net_cents,
        "restricted_credit_cents": row.restricted_credit_cents,
        "state": row.state,
        "notes": row.notes,
    }


@router.post("/api/calculator/scenarios")
def api_scenarios(plan_in: PlanIn):
    try:
        plan = to_plan(plan_in)
        rows = build_scenario_matrix(plan)
        peak = peak_cash_commitment(plan)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    return {
        "rows": [_row_to_dict(r) for r in rows],
        "peak_cash_commitment_cents": peak,
    }


class HedgeIn(BaseModel):
    face_value_cents: int
    bonus_odds: str
    hedge_odds: str
    hedge_allowed: bool = True
    hedge_restriction_note: str = ""


@router.post("/api/calculator/hedge")
def api_hedge(body: HedgeIn):
    try:
        result = plan_equal_return_hedge(
            body.face_value_cents,
            body.bonus_odds,
            body.hedge_odds,
            hedge_allowed=body.hedge_allowed,
            hedge_restriction_note=body.hedge_restriction_note,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    if isinstance(result, HedgeRefusal):
        return {"refused": True, "reason": result.reason}
    return {
        "refused": False,
        "bonus_face_cents": result.bonus_face_cents,
        "bonus_odds": str(result.bonus_odds),
        "hedge_odds": str(result.hedge_odds),
        "hedge_stake_cents": result.hedge_stake_cents,
        "locked_cents": result.locked_cents,
        "conversion_rate": str(result.conversion_rate),
    }


class EstimateIn(BaseModel):
    plan: PlanIn
    probabilities: Optional[dict] = None


@router.post("/api/calculator/estimate")
def api_estimate(body: EstimateIn):
    try:
        plan = to_plan(body.plan)
        est = estimated_conversion_value(plan, body.probabilities)
    except ProbabilityAssumptionsMissing as exc:
        raise HTTPException(
            422,
            {"error": str(exc), "label": "Unavailable — needs probability assumptions"},
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    return {
        "label": est.label,
        "expected_net_cents": est.expected_net_cents,
        "inputs": est.inputs,
    }


class CashLegIn(BaseModel):
    stake_cents: int
    decimal_odds: str


@router.post("/api/calculator/cash-leg")
def api_cash_leg(body: CashLegIn):
    try:
        out = cash_leg_outcomes(body.stake_cents, body.decimal_odds)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    return {
        "win_net_cents": out.win_net_cents,
        "loss_net_cents": out.loss_net_cents,
        "push_net_cents": out.push_net_cents,
    }


class BonusLegIn(BaseModel):
    face_cents: int
    decimal_odds: str
    stake_returned: bool = False


@router.post("/api/calculator/bonus-leg")
def api_bonus_leg(body: BonusLegIn):
    try:
        out = bonus_leg_outcomes(body.face_cents, body.decimal_odds, body.stake_returned)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    return {
        "win_proceeds_cents": out.win_proceeds_cents,
        "loss_proceeds_cents": out.loss_proceeds_cents,
    }
