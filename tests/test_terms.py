"""Tests for terms capture, versioning, idempotency, and review workflow."""
from __future__ import annotations

import pytest

from vigline.promo import service, terms
from vigline.promo.terms import (
    NO_SOURCE_MESSAGE,
    ReviewTransitionError,
    RobotsDisallowedError,
    SourceNotAuthorizedError,
    TermsError,
    capture_terms_text,
    fetch_authorized_text,
    set_eligibility,
    set_review_state,
    sha256_text,
)


@pytest.fixture
def offer_id(conn):
    return service.create_offer(
        conn, platform="DraftKings", title="Bet 5 get 150", offer_type="bonus_bet"
    )


def test_sha256_known_value():
    assert sha256_text("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_capture_first_version(conn, offer_id):
    result = capture_terms_text(
        conn, offer_id=offer_id, raw_text="official terms v1",
        source_url=None, fetched_by="manual",
    )
    assert result.version_no == 1
    assert not result.changed
    assert not result.duplicate
    assert result.previous_hash is None
    row = conn.execute(
        "SELECT status, review_state FROM promo_offers WHERE id = ?", (offer_id,)
    ).fetchone()
    assert row["status"] == "needs_review"


def test_capture_duplicate_is_idempotent(conn, offer_id):
    r1 = capture_terms_text(conn, offer_id=offer_id, raw_text="same text",
                            source_url=None, fetched_by="manual")
    r2 = capture_terms_text(conn, offer_id=offer_id, raw_text="same text",
                            source_url=None, fetched_by="manual")
    assert r2.duplicate
    assert r2.version_no == r1.version_no == 1
    count = conn.execute(
        "SELECT COUNT(*) FROM promo_terms_versions WHERE offer_id = ?", (offer_id,)
    ).fetchone()[0]
    assert count == 1


def test_capture_changed_text_flags_offer(conn, offer_id):
    r1 = capture_terms_text(conn, offer_id=offer_id, raw_text="terms v1",
                            source_url=None, fetched_by="manual")
    r2 = capture_terms_text(conn, offer_id=offer_id, raw_text="terms v2 (updated)",
                            source_url=None, fetched_by="manual")
    assert r2.version_no == 2
    assert r2.changed
    assert r2.previous_hash == r1.text_hash
    row = conn.execute(
        "SELECT status, review_state FROM promo_offers WHERE id = ?", (offer_id,)
    ).fetchone()
    assert row["status"] == "changed"
    assert row["review_state"] == "needs_review"


def test_terms_versions_are_append_only(conn, offer_id):
    capture_terms_text(conn, offer_id=offer_id, raw_text="v1",
                       source_url=None, fetched_by="manual")
    before = conn.execute(
        "SELECT id, version_no, raw_text FROM promo_terms_versions WHERE offer_id = ?",
        (offer_id,),
    ).fetchall()
    capture_terms_text(conn, offer_id=offer_id, raw_text="v2",
                       source_url=None, fetched_by="manual")
    after = conn.execute(
        "SELECT id, version_no, raw_text FROM promo_terms_versions WHERE offer_id = ?",
        (offer_id,),
    ).fetchall()
    # original row untouched; new row appended
    assert len(after) == len(before) + 1
    assert [dict(r) for r in before] == [dict(r) for r in after if r["version_no"] == 1]


def test_capture_empty_text_rejected(conn, offer_id):
    with pytest.raises(TermsError):
        capture_terms_text(conn, offer_id=offer_id, raw_text="   ",
                           source_url=None, fetched_by="manual")


def test_review_workflow_transitions(conn, offer_id):
    set_review_state(conn, offer_id, "needs_review")
    set_review_state(conn, offer_id, "reviewed")
    set_review_state(conn, offer_id, "needs_review")  # back for re-review
    row = conn.execute(
        "SELECT review_state FROM promo_offers WHERE id = ?", (offer_id,)
    ).fetchone()
    assert row["review_state"] == "needs_review"


def test_review_illegal_transition(conn, offer_id):
    with pytest.raises(ReviewTransitionError):
        set_review_state(conn, offer_id, "reviewed")  # retrieved -> reviewed illegal


def test_eligibility_states(conn, offer_id):
    for state in ("eligible", "ineligible", "inapplicable", "unknown"):
        set_eligibility(conn, offer_id, state)
    with pytest.raises(TermsError):
        set_eligibility(conn, offer_id, "maybe")


def test_fetch_requires_authorized_source():
    with pytest.raises(SourceNotAuthorizedError):
        fetch_authorized_text("https://example.com/terms", [])


def test_fetch_respects_robots():
    with pytest.raises(RobotsDisallowedError):
        fetch_authorized_text(
            "https://example.com/terms",
            ["https://example.com/terms"],
            robot_check=lambda url: False,
        )


def test_no_source_message_constant():
    assert NO_SOURCE_MESSAGE.startswith("Unavailable")
