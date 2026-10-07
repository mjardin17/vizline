"""Tests for the promo wallet state machine."""
from __future__ import annotations

import pytest

from vigline.promo.wallet import (
    IllegalTransitionError,
    ManualWalletRecord,
    allowed_transitions,
    transition,
)


def test_legal_transitions():
    assert transition("pending", "issued") == "issued"
    assert transition("pending", "expired") == "expired"
    assert transition("issued", "used") == "used"
    assert transition("issued", "expired") == "expired"
    assert transition("issued", "disputed") == "disputed"
    assert transition("disputed", "used") == "used"
    assert transition("disputed", "expired") == "expired"


def test_illegal_transitions_raise():
    for current, target in [
        ("pending", "used"),
        ("pending", "disputed"),
        ("issued", "pending"),
        ("used", "issued"),
        ("used", "expired"),
        ("expired", "used"),
        ("disputed", "pending"),
        ("disputed", "issued"),
    ]:
        with pytest.raises(IllegalTransitionError):
            transition(current, target)


def test_terminal_states_have_no_transitions():
    assert allowed_transitions("used") == ()
    assert allowed_transitions("expired") == ()


def test_unknown_state_rejected():
    with pytest.raises(ValueError):
        transition("nope", "issued")


def test_manual_record_forced_unverified():
    rec = ManualWalletRecord(
        platform="DraftKings",
        reward_type="bonus_bet",
        face_value_cents=1000,
        cash_vs_credit="restricted_credit",
    )
    assert rec.source == "manually_entered_unverified"


def test_manual_record_cannot_be_verified():
    with pytest.raises(ValueError):
        ManualWalletRecord(
            platform="DraftKings",
            reward_type="bonus_bet",
            face_value_cents=1000,
            cash_vs_credit="restricted_credit",
            source="verified",
        )
