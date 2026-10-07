"""Promo wallet state machine.

States: pending -> issued -> used | expired
                 issued -> disputed -> used | expired
`used` and `expired` are terminal. Illegal transitions raise.

Manually entered records are forced to source='manually_entered_unverified'
and can never be relabeled verified by code: there is no code path that
writes source='verified' for a manual record.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

PENDING = "pending"
ISSUED = "issued"
USED = "used"
EXPIRED = "expired"
DISPUTED = "disputed"

STATES = (PENDING, ISSUED, USED, EXPIRED, DISPUTED)

_TRANSITIONS: dict[str, tuple[str, ...]] = {
    PENDING: (ISSUED, EXPIRED),
    ISSUED: (USED, EXPIRED, DISPUTED),
    DISPUTED: (USED, EXPIRED),
    USED: (),
    EXPIRED: (),
}

SOURCE_MANUAL = "manually_entered_unverified"
SOURCE_VERIFIED = "verified"


class IllegalTransitionError(Exception):
    def __init__(self, current: str, target: str):
        super().__init__(
            f"illegal wallet transition: {current!r} -> {target!r}; "
            f"allowed from {current!r}: {list(_TRANSITIONS.get(current, ()))}"
        )
        self.current = current
        self.target = target


def allowed_transitions(state: str) -> tuple[str, ...]:
    if state not in _TRANSITIONS:
        raise ValueError(f"unknown wallet state: {state!r}")
    return _TRANSITIONS[state]


def transition(state: str, target: str) -> str:
    """Validate a wallet state transition. Returns the target state, or raises
    IllegalTransitionError."""
    if target not in allowed_transitions(state):
        raise IllegalTransitionError(state, target)
    return target


@dataclass(frozen=True)
class ManualWalletRecord:
    """A manually entered wallet record. Source is forced to unverified and
    the dataclass offers no way to change it."""

    platform: str
    reward_type: str
    face_value_cents: int
    cash_vs_credit: str  # 'restricted_credit' | 'cash'
    expires_at: Optional[str] = None
    source: str = SOURCE_MANUAL  # not settable to 'verified' by callers

    def __post_init__(self):
        if self.source != SOURCE_MANUAL:
            raise ValueError(
                "manually entered records must use source="
                f"{SOURCE_MANUAL!r}; got {self.source!r}"
            )
        if self.cash_vs_credit not in ("restricted_credit", "cash"):
            raise ValueError(f"invalid cash_vs_credit: {self.cash_vs_credit!r}")
        if self.face_value_cents < 0:
            raise ValueError("face value must be >= 0")
