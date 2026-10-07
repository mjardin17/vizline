"""Honesty guardrails: no guaranteed-profit language in user-facing copy, and
estimated/modelled profit must never land in realized P&L tables."""
from __future__ import annotations

from pathlib import Path

from vigline.promo import service
from vigline.promo.math import ConversionPlan
from vigline.promo.rankings import OfferPlanInput, rank_by_estimated_net

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "vigline" / "web" / "templates"

BANNED_PHRASES = (
    "guaranteed profit",
    "guaranteed win",
    "can't lose",
    "cannot lose",
    "risk-free profit",
    "risk free profit",
    "never lose",
    "sure profit",
)


def test_no_guaranteed_profit_language_in_templates():
    violations = []
    for template in TEMPLATES_DIR.glob("*.html"):
        text = template.read_text(encoding="utf-8").lower()
        for phrase in BANNED_PHRASES:
            if phrase in text:
                violations.append(f"{template.name}: {phrase!r}")
    assert not violations, f"banned phrases in UI copy: {violations}"


def test_estimated_profit_never_enters_realized_pnl(conn):
    offer_id = service.create_offer(
        conn, platform="DraftKings", title="T", offer_type="bonus_bet"
    )
    plan = ConversionPlan(
        bonus_face_cents=10000, bonus_odds="3.0", hedge_odds="2.0",
        qualifying_stake_cents=5000, qualifying_odds="2.0",
    )
    alloc_id = service.create_allocation(conn, offer_id=offer_id, plan={
        "bonus_face_cents": 10000, "bonus_odds": "3.0", "hedge_odds": "2.0",
        "qualifying_stake_cents": 5000, "qualifying_odds": "2.0",
    })
    inputs = [OfferPlanInput(
        offer_id=offer_id, platform="DraftKings", title="T", plan=plan,
        verified_step_count=2, inputs_cited="test",
    )]
    ranked = rank_by_estimated_net(inputs, {"p_bonus_leg_win": "0.5", "p_qualifying_win": "0.5"})
    assert ranked[0].status == "ok"  # an estimate was produced...
    # ...but nothing estimated may appear in realized P&L
    count = conn.execute("SELECT COUNT(*) FROM promo_settlements").fetchone()[0]
    assert count == 0


def test_settlement_only_from_explicit_realized_value(conn):
    offer_id = service.create_offer(
        conn, platform="X", title="T", offer_type="bonus_bet"
    )
    alloc_id = service.create_allocation(conn, offer_id=offer_id, plan={})
    exec_id = service.report_execution(conn, allocation_id=alloc_id, report={"note": "placed"})
    # executions are always unverified
    row = conn.execute(
        "SELECT verified FROM promo_executions WHERE id = ?", (exec_id,)
    ).fetchone()
    assert row["verified"] == 0
    sid = service.record_settlement(
        conn, execution_id=exec_id, realized_net_cents=-2500, note="settled loss"
    )
    stored = conn.execute(
        "SELECT realized_net_cents FROM promo_settlements WHERE id = ?", (sid,)
    ).fetchone()
    assert stored["realized_net_cents"] == -2500
