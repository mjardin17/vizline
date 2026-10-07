"""Offer rankings. Pure functions over explicit plan inputs.

Four rankings, per spec:
1. lowest peak cash commitment (ascending)
2. highest estimated net cash value — ONLY with user-supplied probability
   assumptions; otherwise the entry is marked
   "Unavailable — needs probability assumptions"
3. highest minimum modeled net across explicitly covered scenarios (maximin)
4. simplest verified qualification (fewest verified steps)

Every entry cites the offer, the metric value, and its inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .math import (
    ConversionPlan,
    ProbabilityAssumptionsMissing,
    ScenarioRow,
    build_scenario_matrix,
    estimated_conversion_value,
    peak_cash_commitment,
)

UNAVAILABLE_PROBS = "Unavailable — needs probability assumptions"


@dataclass
class OfferPlanInput:
    offer_id: int
    platform: str
    title: str
    plan: ConversionPlan
    verified_step_count: int
    inputs_cited: str = ""


@dataclass
class RankedOffer:
    offer_id: int
    platform: str
    title: str
    metric_name: str
    metric_display: str
    metric_cents: Optional[int]
    inputs_cited: str
    status: str  # "ok" | "unavailable"


def _base(inp: OfferPlanInput, metric_name: str) -> RankedOffer:
    return RankedOffer(
        offer_id=inp.offer_id,
        platform=inp.platform,
        title=inp.title,
        metric_name=metric_name,
        metric_display="",
        metric_cents=None,
        inputs_cited=inp.inputs_cited,
        status="ok",
    )


def _fmt_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}${c // 100}.{c % 100:02d}"


def rank_by_peak_commitment(inputs: list[OfferPlanInput]) -> list[RankedOffer]:
    """Ranking 1: lowest peak cash commitment first."""
    ranked = []
    for inp in inputs:
        peak = peak_cash_commitment(inp.plan)
        entry = _base(inp, "peak_cash_commitment")
        entry.metric_cents = peak
        entry.metric_display = _fmt_cents(peak)
        entry.inputs_cited = (
            inp.inputs_cited
            or f"qualifying_stake={_fmt_cents(inp.plan.qualifying_stake_cents)}, "
            f"hedge_stake=computed from bonus_face={_fmt_cents(inp.plan.bonus_face_cents)} "
            f"at d={inp.plan.bonus_odds} vs h={inp.plan.hedge_odds}; worst-case path"
        )
        ranked.append(entry)
    ranked.sort(key=lambda e: (e.metric_cents is None, e.metric_cents or 0))
    return ranked


def rank_by_estimated_net(
    inputs: list[OfferPlanInput], probabilities: Optional[dict] = None
) -> list[RankedOffer]:
    """Ranking 2: highest estimated net cash value first — only when the user
    supplies probability assumptions. Without them every entry is unavailable."""
    ranked = []
    for inp in inputs:
        entry = _base(inp, "estimated_net_cash_value")
        if not probabilities:
            entry.status = "unavailable"
            entry.metric_display = UNAVAILABLE_PROBS
            entry.inputs_cited = "no probability assumptions supplied"
            ranked.append(entry)
            continue
        try:
            est = estimated_conversion_value(inp.plan, probabilities)
        except ProbabilityAssumptionsMissing as exc:
            entry.status = "unavailable"
            entry.metric_display = UNAVAILABLE_PROBS
            entry.inputs_cited = str(exc)
            ranked.append(entry)
            continue
        entry.metric_cents = est.expected_net_cents
        entry.metric_display = f"{_fmt_cents(est.expected_net_cents)} [{est.label}]"
        entry.inputs_cited = est.label
        ranked.append(entry)
    ranked.sort(
        key=lambda e: (
            e.status != "ok",
            -(e.metric_cents if e.metric_cents is not None else 0),
        )
    )
    return ranked


def rank_by_maximin(inputs: list[OfferPlanInput]) -> list[RankedOffer]:
    """Ranking 3: highest minimum modeled net across explicitly covered
    scenarios (maximin). Uses the scenario matrix; no probabilities invented."""
    ranked = []
    for inp in inputs:
        matrix = build_scenario_matrix(inp.plan)
        worst = min(row.net_cents for row in matrix)
        entry = _base(inp, "maximin_net")
        entry.metric_cents = worst
        entry.metric_display = _fmt_cents(worst)
        worst_rows = [r for r in matrix if r.net_cents == worst]
        entry.inputs_cited = (
            inp.inputs_cited
            or "minimum net across scenario matrix rows: "
            + ", ".join(f"{r.key} ({_fmt_cents(r.net_cents)})" for r in worst_rows)
        )
        ranked.append(entry)
    ranked.sort(key=lambda e: -(e.metric_cents or 0))
    return ranked


def rank_by_simplicity(inputs: list[OfferPlanInput]) -> list[RankedOffer]:
    """Ranking 4: simplest verified qualification first (fewest verified steps)."""
    ranked = []
    for inp in inputs:
        entry = _base(inp, "verified_step_count")
        entry.metric_cents = inp.verified_step_count
        entry.metric_display = f"{inp.verified_step_count} verified step(s)"
        entry.inputs_cited = inp.inputs_cited or "count of verified qualification steps"
        ranked.append(entry)
    ranked.sort(key=lambda e: e.metric_cents or 0)
    return ranked


RANKINGS = {
    "peak_commitment": ("Lowest peak cash commitment", rank_by_peak_commitment),
    "estimated_net": ("Highest estimated net cash value", rank_by_estimated_net),
    "maximin": ("Highest minimum modeled net (maximin)", rank_by_maximin),
    "simplicity": ("Simplest verified qualification", rank_by_simplicity),
}
