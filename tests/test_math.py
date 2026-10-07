"""Tests for the pure promo math core."""
from __future__ import annotations

from decimal import Decimal

import pytest

from vigline.promo.math import (
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
    to_decimal_odds,
)


def test_cash_leg_exact_formulas():
    out = cash_leg_outcomes(1000, "2.50")
    assert out.win_net_cents == 1500  # S*(d-1)
    assert out.loss_net_cents == -1000  # -S
    assert out.push_net_cents == 0


def test_cash_leg_fractional_odds():
    out = cash_leg_outcomes(1000, "1.95")
    assert out.win_net_cents == 950


def test_bonus_leg_stake_not_returned():
    out = bonus_leg_outcomes(10000, "3.0")
    assert out.win_proceeds_cents == 20000  # F*(d-1)
    assert out.loss_proceeds_cents == 0


def test_bonus_leg_stake_returned():
    out = bonus_leg_outcomes(10000, "3.0", stake_returned=True)
    assert out.win_proceeds_cents == 30000  # F*d


def test_hedge_worked_example():
    # Spec: F=100 bonus at d=3.0 vs h=2.0 -> H=100, locked=100, rate=100%.
    plan = plan_equal_return_hedge(10000, "3.0", "2.0", hedge_allowed=True)
    assert isinstance(plan, HedgePlan)
    assert plan.hedge_stake_cents == 10000
    assert plan.locked_cents == 10000
    assert plan.conversion_rate == Decimal("1.0000")


def test_hedge_rounds_to_whole_cents():
    # F=100, d=2.5 -> proceeds 150.00; H = 150/1.9 = 78.947... -> 7895c
    plan = plan_equal_return_hedge(10000, "2.5", "1.9", hedge_allowed=True)
    assert isinstance(plan, HedgePlan)
    assert plan.hedge_stake_cents == 7895
    assert plan.locked_cents == 15000 - 7895


def test_hedge_refusal_when_terms_forbid():
    result = plan_equal_return_hedge(
        10000, "3.0", "2.0", hedge_allowed=False,
        hedge_restriction_note="no hedging per section 4",
    )
    assert isinstance(result, HedgeRefusal)
    assert "prohibit" in result.reason
    assert "section 4" in result.reason


def test_float_odds_rejected():
    with pytest.raises(TypeError):
        to_decimal_odds(2.5)
    with pytest.raises(ValueError):
        to_decimal_odds("0.99")


def _full_plan(**over):
    kw = dict(
        bonus_face_cents=10000,
        bonus_odds="3.0",
        hedge_odds="2.0",
        qualifying_stake_cents=5000,
        qualifying_odds="2.0",
    )
    kw.update(over)
    return ConversionPlan(**kw)


def test_scenario_matrix_covers_all_cases():
    rows = build_scenario_matrix(_full_plan())
    keys = {r.key for r in rows}
    for expected in (
        "qualifying_win", "qualifying_loss", "qualifying_push_void",
        "bonus_issued", "bonus_expired_unused",
        "conversion_bonus_leg_wins", "conversion_hedge_leg_wins",
        "conversion_push_void_tie", "conversion_leg_rejected_or_partial",
    ):
        assert expected in keys, f"missing scenario row: {expected}"


def test_bonus_face_never_counted_as_cash():
    rows = build_scenario_matrix(_full_plan())
    issued = next(r for r in rows if r.key == "bonus_issued")
    assert issued.cash_in_cents == 0
    assert issued.cash_out_cents == 0
    assert issued.restricted_credit_cents == 10000
    assert issued.net_cents == 0


def test_scenario_matrix_numbers():
    rows = {r.key: r for r in build_scenario_matrix(_full_plan())}
    assert rows["qualifying_win"].net_cents == 5000  # 5000*2.0 - 5000
    assert rows["qualifying_loss"].net_cents == -5000
    assert rows["qualifying_push_void"].net_cents == 0
    assert rows["qualifying_push_void"].state == "void"
    # bonus leg wins: proceeds 20000, hedge stake 10000 -> net 10000
    assert rows["conversion_bonus_leg_wins"].net_cents == 10000
    # hedge leg wins: 10000*2.0 - 10000 -> net 10000 (equal-return)
    assert rows["conversion_hedge_leg_wins"].net_cents == 10000


def test_scenario_matrix_refused_hedge():
    rows = build_scenario_matrix(_full_plan(hedge_allowed=False))
    keys = {r.key for r in rows}
    assert "conversion_hedge_refused" in keys
    assert "conversion_bonus_leg_wins" not in keys


def test_peak_cash_commitment_worst_case():
    # qualifying 5000 (lost) + hedge 10000 -> peak 15000
    assert peak_cash_commitment(_full_plan()) == 15000


def test_peak_cash_commitment_no_qualifier():
    plan = _full_plan(qualifying_stake_cents=0, qualifying_odds=None)
    assert peak_cash_commitment(plan) == 10000


def test_estimated_value_requires_probabilities():
    plan = _full_plan()
    with pytest.raises(ProbabilityAssumptionsMissing):
        estimated_conversion_value(plan, None)
    with pytest.raises(ProbabilityAssumptionsMissing):
        estimated_conversion_value(plan, {})
    with pytest.raises(ProbabilityAssumptionsMissing):
        estimated_conversion_value(plan, {"p_qualifying_win": "0.5"})


def test_estimated_value_label_and_math():
    plan = _full_plan()
    est = estimated_conversion_value(
        plan, {"p_bonus_leg_win": "0.4", "p_qualifying_win": "0.5"}
    )
    assert est.label.startswith("model estimate (inputs:")
    assert "p_bonus_leg_win=0.4" in est.label
    assert "p_qualifying_win=0.5" in est.label
    # equal-return hedge locks 10000 regardless of p; qualifying EV at 50% on fair 2.0 = 0
    assert est.expected_net_cents == 10000


def test_estimated_value_invalid_probability():
    plan = _full_plan(qualifying_stake_cents=0, qualifying_odds=None)
    with pytest.raises(ValueError):
        estimated_conversion_value(plan, {"p_bonus_leg_win": "1.5"})
