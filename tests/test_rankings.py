"""Tests for the four offer rankings."""
from __future__ import annotations

from vigline.promo.math import ConversionPlan
from vigline.promo.rankings import (
    UNAVAILABLE_PROBS,
    OfferPlanInput,
    rank_by_estimated_net,
    rank_by_maximin,
    rank_by_peak_commitment,
    rank_by_simplicity,
)


def _input(offer_id, platform, title, plan, steps):
    return OfferPlanInput(
        offer_id=offer_id, platform=platform, title=title, plan=plan,
        verified_step_count=steps, inputs_cited="test",
    )


def _plans():
    # A: $100 bonus @3.0 vs 2.0, $50 qualifier @2.0  -> peak 15000
    # B: $50 bonus @2.5 vs 2.0, no qualifier         -> peak 7500... check: proceeds=7500? F=5000,d=2.5 -> proceeds=7500, H=7500/2=3750, peak=3750
    a = _input(1, "DK", "Offer A",
               ConversionPlan(bonus_face_cents=10000, bonus_odds="3.0", hedge_odds="2.0",
                              qualifying_stake_cents=5000, qualifying_odds="2.0"), 3)
    b = _input(2, "FD", "Offer B",
               ConversionPlan(bonus_face_cents=5000, bonus_odds="2.5", hedge_odds="2.0"), 1)
    return a, b


def test_rank_by_peak_commitment_ascending():
    a, b = _plans()
    ranked = rank_by_peak_commitment([a, b])
    assert [r.offer_id for r in ranked] == [2, 1]
    assert ranked[0].metric_cents == 3750
    assert ranked[1].metric_cents == 15000
    assert all(r.status == "ok" for r in ranked)


def test_rank_by_estimated_net_unavailable_without_probabilities():
    a, b = _plans()
    ranked = rank_by_estimated_net([a, b], None)
    assert all(r.status == "unavailable" for r in ranked)
    assert all(r.metric_display == UNAVAILABLE_PROBS for r in ranked)


def test_rank_by_estimated_net_with_probabilities():
    a, b = _plans()
    probs = {"p_bonus_leg_win": "0.5", "p_qualifying_win": "0.5"}
    ranked = rank_by_estimated_net([a, b], probs)
    assert all(r.status == "ok" for r in ranked)
    assert all("model estimate (inputs:" in r.inputs_cited for r in ranked)
    # A: locked 10000 + qual EV 0 = 10000; B: locked 3750 -> A first
    assert [r.offer_id for r in ranked] == [1, 2]


def test_rank_by_maximin():
    a, b = _plans()
    ranked = rank_by_maximin([a, b])
    # A worst row: rejected hedge leg -> -10000; B worst row: rejected -> -3750
    assert ranked[0].offer_id == 2  # maximin -3750 beats -10000
    assert ranked[0].metric_cents == -3750
    assert ranked[1].metric_cents == -10000


def test_rank_by_simplicity():
    a, b = _plans()
    ranked = rank_by_simplicity([a, b])
    assert [r.offer_id for r in ranked] == [2, 1]  # 1 step vs 3 steps
