"""Pure promo/bonus math. No I/O, no network, no database access.

Conventions:
- Money is integer cents everywhere. Floats are never used for money.
- Decimal odds are ``decimal.Decimal`` built from strings (never floats).
- Payouts round half-up to whole cents at payout boundaries.
- Nothing here invents probabilities. Expected-value outputs require explicit
  user-supplied probability inputs and are labelled as model estimates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Union

OddsLike = Union[str, int, Decimal]

_CENT = Decimal("1")
_FOUR_DP = Decimal("0.0001")


class ProbabilityAssumptionsMissing(Exception):
    """Raised when an expected-value computation is requested without explicit
    user-supplied probability inputs."""


def to_decimal_odds(value: OddsLike) -> Decimal:
    """Parse decimal odds from str/int/Decimal. Floats are rejected outright."""
    if isinstance(value, float):
        raise TypeError("decimal odds must not be constructed from float; use str")
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal odds: {value!r}") from exc
    if d < 1:
        raise ValueError(f"decimal odds must be >= 1, got {d}")
    return d


def _to_cents(amount: Decimal) -> int:
    return int(amount.quantize(_CENT, rounding=ROUND_HALF_UP))


def _to_probability(value: OddsLike, name: str) -> Decimal:
    if isinstance(value, float):
        raise TypeError("probabilities must not be constructed from float; use str")
    try:
        p = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid probability for {name}: {value!r}") from exc
    if not (Decimal(0) <= p <= Decimal(1)):
        raise ValueError(f"probability {name} must be in [0, 1], got {p}")
    return p


@dataclass(frozen=True)
class CashLegOutcomes:
    win_net_cents: int
    loss_net_cents: int
    push_net_cents: int = 0


def cash_leg_outcomes(stake_cents: int, decimal_odds: OddsLike) -> CashLegOutcomes:
    """Cash stake S at decimal odds d, before costs.

    win_net = S*(d-1); loss_net = -S; push_net = 0.
    """
    if stake_cents < 0:
        raise ValueError("stake must be >= 0")
    d = to_decimal_odds(decimal_odds)
    win_net = _to_cents(Decimal(stake_cents) * (d - 1))
    return CashLegOutcomes(
        win_net_cents=win_net, loss_net_cents=-stake_cents, push_net_cents=0
    )


@dataclass(frozen=True)
class BonusLegOutcomes:
    win_proceeds_cents: int
    loss_proceeds_cents: int = 0


def bonus_leg_outcomes(
    face_cents: int, decimal_odds: OddsLike, stake_returned: bool = False
) -> BonusLegOutcomes:
    """Bonus bet of face value F at decimal odds d.

    Stake not returned (default): win proceeds = F*(d-1), loss = 0.
    Stake returned: win proceeds = F*d, loss = 0.
    """
    if face_cents < 0:
        raise ValueError("face value must be >= 0")
    d = to_decimal_odds(decimal_odds)
    proceeds = _to_cents(Decimal(face_cents) * (d if stake_returned else d - 1))
    return BonusLegOutcomes(win_proceeds_cents=proceeds, loss_proceeds_cents=0)


@dataclass(frozen=True)
class HedgePlan:
    bonus_face_cents: int
    bonus_odds: Decimal
    hedge_odds: Decimal
    hedge_stake_cents: int  # H, rounded to whole cents
    locked_cents: int  # converted cash before costs
    conversion_rate: Decimal  # locked / face, 4dp


@dataclass(frozen=True)
class HedgeRefusal:
    reason: str


def plan_equal_return_hedge(
    face_cents: int,
    bonus_odds: OddsLike,
    hedge_odds: OddsLike,
    *,
    hedge_allowed: bool,
    hedge_restriction_note: str = "",
) -> Union[HedgePlan, HedgeRefusal]:
    """Equal-return hedge of a bonus bet against an opposing cash price.

    H = F*(d-1) / h  (rounded to whole cents; books require whole-cent stakes)
    locked converted cash = F*(d-1) - H   (before costs)
    conversion_rate = locked / F

    When the offer terms forbid hedging, returns a HedgeRefusal instead of a
    plan. It never silently complies.
    """
    if not hedge_allowed:
        reason = "hedge plan refused: the offer terms prohibit hedging"
        if hedge_restriction_note:
            reason += f" (terms note: {hedge_restriction_note})"
        return HedgeRefusal(reason=reason)
    if face_cents < 0:
        raise ValueError("face value must be >= 0")
    d = to_decimal_odds(bonus_odds)
    h = to_decimal_odds(hedge_odds)
    proceeds = Decimal(face_cents) * (d - 1)
    stake = _to_cents(proceeds / h)
    locked = _to_cents(proceeds) - stake
    rate = (
        (Decimal(locked) / Decimal(face_cents)).quantize(_FOUR_DP, rounding=ROUND_HALF_UP)
        if face_cents
        else Decimal("0")
    )
    return HedgePlan(
        bonus_face_cents=face_cents,
        bonus_odds=d,
        hedge_odds=h,
        hedge_stake_cents=stake,
        locked_cents=locked,
        conversion_rate=rate,
    )


@dataclass
class ConversionPlan:
    """A full qualification + conversion plan for one promo offer."""

    bonus_face_cents: int
    bonus_odds: OddsLike
    hedge_odds: OddsLike
    stake_returned: bool = False
    qualifying_stake_cents: int = 0
    qualifying_odds: Optional[OddsLike] = None
    costs_cents: int = 0
    bonus_proceeds_as_cash: bool = True  # if False, win proceeds stay restricted credit
    hedge_allowed: bool = True
    hedge_restriction_note: str = ""


@dataclass
class ScenarioRow:
    key: str
    label: str
    cash_out_cents: int
    cash_in_cents: int
    restricted_credit_cents: int = 0
    state: str = "settled"  # settled | void | expired | rejected | refused | costs | restricted_credit
    notes: str = ""

    @property
    def net_cents(self) -> int:
        return self.cash_in_cents - self.cash_out_cents


def build_scenario_matrix(plan: ConversionPlan) -> list[ScenarioRow]:
    """Explicit scenario matrix. No probabilities are invented; every row is a
    concrete, named outcome with cash flows. Bonus face value is tracked as
    restricted credit, never as cash."""
    rows: list[ScenarioRow] = []
    d = to_decimal_odds(plan.bonus_odds)
    h = to_decimal_odds(plan.hedge_odds)

    # --- qualification phase ---
    if plan.qualifying_stake_cents:
        if plan.qualifying_odds is None:
            raise ValueError("qualifying_odds is required when qualifying_stake_cents > 0")
        q = to_decimal_odds(plan.qualifying_odds)
        s = plan.qualifying_stake_cents
        rows.append(
            ScenarioRow(
                key="qualifying_win",
                label="Qualifying bet wins",
                cash_out_cents=s,
                cash_in_cents=_to_cents(Decimal(s) * q),
                state="settled",
            )
        )
        rows.append(
            ScenarioRow(
                key="qualifying_loss",
                label="Qualifying bet loses",
                cash_out_cents=s,
                cash_in_cents=0,
                state="settled",
            )
        )
        rows.append(
            ScenarioRow(
                key="qualifying_push_void",
                label="Qualifying bet pushes / is voided",
                cash_out_cents=s,
                cash_in_cents=s,
                state="void",
                notes="Stake returned. Check terms: a void qualifier may not trigger the reward.",
            )
        )

    # --- reward issuance ---
    rows.append(
        ScenarioRow(
            key="bonus_issued",
            label="Bonus issued",
            cash_out_cents=0,
            cash_in_cents=0,
            restricted_credit_cents=plan.bonus_face_cents,
            state="restricted_credit",
            notes="Bonus face value is restricted credit, not withdrawable cash.",
        )
    )
    rows.append(
        ScenarioRow(
            key="bonus_expired_unused",
            label="Bonus expires unused",
            cash_out_cents=0,
            cash_in_cents=0,
            state="expired",
            notes="No cash changes hands. Track expiry dates in the wallet.",
        )
    )

    # --- conversion phase ---
    hedge = plan_equal_return_hedge(
        plan.bonus_face_cents,
        d,
        h,
        hedge_allowed=plan.hedge_allowed,
        hedge_restriction_note=plan.hedge_restriction_note,
    )
    if isinstance(hedge, HedgeRefusal):
        rows.append(
            ScenarioRow(
                key="conversion_hedge_refused",
                label="Conversion hedge refused",
                cash_out_cents=0,
                cash_in_cents=0,
                state="refused",
                notes=hedge.reason,
            )
        )
    else:
        proceeds = bonus_leg_outcomes(
            plan.bonus_face_cents, d, stake_returned=plan.stake_returned
        ).win_proceeds_cents
        if plan.bonus_proceeds_as_cash:
            rows.append(
                ScenarioRow(
                    key="conversion_bonus_leg_wins",
                    label="Conversion: bonus leg wins",
                    cash_out_cents=hedge.hedge_stake_cents,
                    cash_in_cents=proceeds,
                    state="settled",
                    notes="Assumes terms pay bonus win proceeds as cash. If terms pay site credit, treat as restricted credit instead.",
                )
            )
        else:
            rows.append(
                ScenarioRow(
                    key="conversion_bonus_leg_wins",
                    label="Conversion: bonus leg wins",
                    cash_out_cents=hedge.hedge_stake_cents,
                    cash_in_cents=0,
                    restricted_credit_cents=proceeds,
                    state="settled",
                    notes="Per terms, win proceeds are paid as restricted credit, not cash.",
                )
            )
        rows.append(
            ScenarioRow(
                key="conversion_hedge_leg_wins",
                label="Conversion: hedge (cash) leg wins",
                cash_out_cents=hedge.hedge_stake_cents,
                cash_in_cents=_to_cents(Decimal(hedge.hedge_stake_cents) * h),
                state="settled",
                notes="Bonus leg loses; the bonus itself is forfeited (no cash loss beyond the hedge stake).",
            )
        )
        rows.append(
            ScenarioRow(
                key="conversion_push_void_tie",
                label="Conversion leg pushes / voids / ties",
                cash_out_cents=hedge.hedge_stake_cents,
                cash_in_cents=hedge.hedge_stake_cents,
                state="void",
                notes="Terms-dependent: the bonus leg may be returned as bonus, voided, or forfeited. Verify per offer.",
            )
        )
        rows.append(
            ScenarioRow(
                key="conversion_leg_rejected_or_partial",
                label="Hedge leg rejected or partially accepted",
                cash_out_cents=hedge.hedge_stake_cents,
                cash_in_cents=0,
                state="rejected",
                notes="Exposure remains unhedged. Re-plan before proceeding; do not assume the full stake was accepted.",
            )
        )

    # --- costs ---
    if plan.costs_cents:
        rows.append(
            ScenarioRow(
                key="costs_applied",
                label="Costs applied",
                cash_out_cents=plan.costs_cents,
                cash_in_cents=0,
                state="costs",
                notes="Deduct from any net above. Costs are never netted silently.",
            )
        )
    return rows


def peak_cash_commitment(plan: ConversionPlan) -> int:
    """Maximum cumulative cash outflow on the worst-case path (qualifying bet
    loses, then conversion legs are placed). A refundable deposit counts as
    cash committed while it is held, not as a loss."""
    hedge = plan_equal_return_hedge(
        plan.bonus_face_cents,
        plan.bonus_odds,
        plan.hedge_odds,
        hedge_allowed=plan.hedge_allowed,
        hedge_restriction_note=plan.hedge_restriction_note,
    )
    h_stake = hedge.hedge_stake_cents if isinstance(hedge, HedgePlan) else 0
    events: list[tuple[int, int]] = []
    if plan.qualifying_stake_cents:
        events.append((plan.qualifying_stake_cents, 0))  # stake placed
        events.append((0, 0))  # settles as a loss on the worst-case path
    if h_stake:
        events.append((h_stake, 0))  # hedge placed
        events.append((0, 0))  # conversion legs settle
    running = 0
    peak = 0
    for out_c, in_c in events:
        running += out_c - in_c
        peak = max(peak, running)
    return peak


@dataclass(frozen=True)
class EstimatedValue:
    label: str  # always "model estimate (inputs: ...)"
    expected_net_cents: int
    inputs: dict


def estimated_conversion_value(
    plan: ConversionPlan, probabilities: Optional[dict] = None
) -> EstimatedValue:
    """Expected net of the full plan. Requires explicit user-supplied
    probabilities; never invents them.

    probabilities keys: 'p_qualifying_win' (required if a qualifying stake
    exists), 'p_bonus_leg_win' (required). Push/void outcomes are excluded
    from the expectation and noted as such.
    """
    if not probabilities or "p_bonus_leg_win" not in probabilities:
        raise ProbabilityAssumptionsMissing(
            "estimated value requires explicit user-supplied probabilities "
            "('p_bonus_leg_win' and, with a qualifying stake, 'p_qualifying_win')"
        )
    p_bonus = _to_probability(probabilities["p_bonus_leg_win"], "p_bonus_leg_win")

    hedge = plan_equal_return_hedge(
        plan.bonus_face_cents,
        plan.bonus_odds,
        plan.hedge_odds,
        hedge_allowed=plan.hedge_allowed,
        hedge_restriction_note=plan.hedge_restriction_note,
    )
    if isinstance(hedge, HedgeRefusal):
        raise ValueError(f"cannot estimate value: {hedge.reason}")

    # Equal-return hedge: both conversion outcomes lock the same cash (before
    # costs), so conversion EV is independent of p_bonus_leg_win. The input is
    # still required by policy and recorded in the label.
    d = to_decimal_odds(plan.bonus_odds)
    h = to_decimal_odds(plan.hedge_odds)
    proceeds = bonus_leg_outcomes(
        plan.bonus_face_cents, d, stake_returned=plan.stake_returned
    ).win_proceeds_cents
    if not plan.bonus_proceeds_as_cash:
        conv_ev = _to_cents(Decimal(hedge.hedge_stake_cents) * (h - 1)) - hedge.hedge_stake_cents
        # bonus-win proceeds are restricted credit: cash EV comes only from the
        # hedge leg winning; bonus leg winning yields no cash.
        conv_ev = int(
            (Decimal(1) - p_bonus) * Decimal(conv_ev)
        )
        inputs = {"p_bonus_leg_win": str(p_bonus)}
    else:
        conv_ev = hedge.locked_cents
        inputs = {"p_bonus_leg_win": str(p_bonus)}

    total = conv_ev - plan.costs_cents
    if plan.qualifying_stake_cents:
        if "p_qualifying_win" not in probabilities:
            raise ProbabilityAssumptionsMissing(
                "estimated value requires 'p_qualifying_win' when the plan has a qualifying stake"
            )
        p_qual = _to_probability(probabilities["p_qualifying_win"], "p_qualifying_win")
        outcomes = cash_leg_outcomes(plan.qualifying_stake_cents, plan.qualifying_odds)
        qual_ev = int(
            p_qual * Decimal(outcomes.win_net_cents)
            + (Decimal(1) - p_qual) * Decimal(outcomes.loss_net_cents)
        )
        total += qual_ev
        inputs["p_qualifying_win"] = str(p_qual)

    label = "model estimate (inputs: " + ", ".join(f"{k}={v}" for k, v in inputs.items()) + ")"
    return EstimatedValue(label=label, expected_net_cents=total, inputs=inputs)
