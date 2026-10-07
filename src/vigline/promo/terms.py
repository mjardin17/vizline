"""Promo terms capture and versioning.

- Terms are captured ONLY from operator-configured official URLs (see
  config/sources.json, default empty) or from manual text import.
- Every capture stores raw text + sha256 + fetched_at + source_url as an
  IMMUTABLE version row. promo_terms_versions is append-only: the current
  version is max(version_no); this module never issues UPDATE against it.
- Duplicate captures (same sha256) are idempotent: no new version row.
- Changed text flags the offer ('changed', review_state='needs_review').
- robots.txt is respected for URL fetches; no login automation, no CAPTCHA
  bypass, no scraping of disallowed endpoints.
- With no configured sources, fetching reports
  'Unavailable — no authorized source configured.'
"""
from __future__ import annotations

import hashlib
import sqlite3
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

from ..audit import log_audit
from ..db import utcnow_iso

NO_SOURCE_MESSAGE = "Unavailable — no authorized source configured."


class TermsError(Exception):
    """Base class for terms-capture errors."""


class SourceNotAuthorizedError(TermsError):
    """The URL is not in the operator-configured authorized source list."""


class RobotsDisallowedError(TermsError):
    """robots.txt disallows fetching the URL."""


class FetchError(TermsError):
    """The HTTP fetch failed."""


@dataclass(frozen=True)
class CaptureResult:
    offer_id: int
    version_no: int
    text_hash: str
    changed: bool
    previous_hash: Optional[str]
    duplicate: bool  # True when the capture was idempotent (no new version)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _current_version(conn: sqlite3.Connection, offer_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT version_no, text_hash_sha256, source_url FROM promo_terms_versions"
        " WHERE offer_id = ? ORDER BY version_no DESC LIMIT 1",
        (offer_id,),
    ).fetchone()


def capture_terms_text(
    conn: sqlite3.Connection,
    *,
    offer_id: int,
    raw_text: str,
    source_url: Optional[str],
    fetched_by: str,
    actor: str = "system",
) -> CaptureResult:
    """Store raw terms text as a new immutable version.

    Idempotent: capturing byte-identical text (same sha256) as the current
    version creates no new row and reports duplicate=True.
    """
    if not raw_text or not raw_text.strip():
        raise TermsError("cannot capture empty terms text")
    digest = sha256_text(raw_text)
    current = _current_version(conn, offer_id)
    fetched_at = utcnow_iso()

    if current is not None and current["text_hash_sha256"] == digest:
        return CaptureResult(
            offer_id=offer_id,
            version_no=current["version_no"],
            text_hash=digest,
            changed=False,
            previous_hash=digest,
            duplicate=True,
        )

    previous_hash = current["text_hash_sha256"] if current else None
    version_no = (current["version_no"] + 1) if current else 1
    with conn:
        conn.execute(
            "INSERT INTO promo_terms_versions"
            " (offer_id, version_no, raw_text, text_hash_sha256, source_url, fetched_at, fetched_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (offer_id, version_no, raw_text, digest, source_url, fetched_at, fetched_by),
        )
        if previous_hash is None:
            conn.execute(
                "UPDATE promo_offers SET status = 'needs_review', review_state = 'needs_review',"
                " updated_at = ? WHERE id = ?",
                (fetched_at, offer_id),
            )
        else:
            # Terms changed vs the previous version: flag for re-review.
            conn.execute(
                "UPDATE promo_offers SET status = 'changed', review_state = 'needs_review',"
                " updated_at = ? WHERE id = ?",
                (fetched_at, offer_id),
            )
        log_audit(
            conn,
            "promo_offer",
            offer_id,
            "terms_captured",
            actor,
            {"version_no": version_no, "sha256": digest, "source_url": source_url},
        )
    return CaptureResult(
        offer_id=offer_id,
        version_no=version_no,
        text_hash=digest,
        changed=previous_hash is not None,
        previous_hash=previous_hash,
        duplicate=False,
    )


def _default_robot_check(url: str) -> bool:
    """Return True if robots.txt allows fetching `url`. Network access happens
    only here; tests inject their own checker."""
    parts = urllib.parse.urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(robots_url)
            if resp.status_code != 200:
                return True  # no usable robots.txt -> allow
            rp.parse(resp.text.splitlines())
    except Exception:
        return True  # fail open on robots fetch errors; the page fetch still validates
    return rp.can_fetch("*", url)


def fetch_authorized_text(
    url: str,
    authorized_sources: list[str],
    *,
    robot_check: Optional[Callable[[str], bool]] = None,
    timeout: int = 15,
) -> str:
    """Fetch terms text from an operator-configured official URL.

    Raises SourceNotAuthorizedError if the URL is not configured,
    RobotsDisallowedError if robots.txt disallows it, FetchError on HTTP failure.
    """
    if url not in authorized_sources:
        raise SourceNotAuthorizedError(
            f"refused: {url} is not in the authorized source list. "
            "Configure it in config/sources.json or import terms text manually."
        )
    check = robot_check or _default_robot_check
    if not check(url):
        raise RobotsDisallowedError(f"refused: robots.txt disallows fetching {url}")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "ViglineTermsBot/0.1 (+local research)"})
    except Exception as exc:
        raise FetchError(f"fetch failed for {url}: {exc}") from exc
    if resp.status_code != 200:
        raise FetchError(f"fetch failed for {url}: HTTP {resp.status_code}")
    text = resp.text
    if not text.strip():
        raise FetchError(f"fetch returned empty body for {url}")
    return text


REVIEW_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "retrieved": ("needs_review",),
    "needs_review": ("retrieved", "reviewed"),
    "reviewed": ("needs_review",),
}

ELIGIBILITY_STATES = ("unknown", "eligible", "ineligible", "inapplicable")


class ReviewTransitionError(TermsError):
    pass


def set_review_state(
    conn: sqlite3.Connection, offer_id: int, to_state: str, actor: str = "system"
) -> None:
    row = conn.execute(
        "SELECT review_state FROM promo_offers WHERE id = ?", (offer_id,)
    ).fetchone()
    if row is None:
        raise TermsError(f"offer {offer_id} not found")
    current = row["review_state"]
    if to_state not in REVIEW_TRANSITIONS.get(current, ()):
        raise ReviewTransitionError(
            f"illegal review transition: {current!r} -> {to_state!r}"
        )
    with conn:
        conn.execute(
            "UPDATE promo_offers SET review_state = ?, updated_at = ? WHERE id = ?",
            (to_state, utcnow_iso(), offer_id),
        )
        log_audit(conn, "promo_offer", offer_id, "review_state", actor,
                  {"from": current, "to": to_state})


def set_eligibility(
    conn: sqlite3.Connection, offer_id: int, state: str, actor: str = "system"
) -> None:
    if state not in ELIGIBILITY_STATES:
        raise TermsError(f"invalid eligibility state: {state!r}")
    row = conn.execute(
        "SELECT eligibility_state FROM promo_offers WHERE id = ?", (offer_id,)
    ).fetchone()
    if row is None:
        raise TermsError(f"offer {offer_id} not found")
    with conn:
        conn.execute(
            "UPDATE promo_offers SET eligibility_state = ?, updated_at = ? WHERE id = ?",
            (state, utcnow_iso(), offer_id),
        )
        log_audit(conn, "promo_offer", offer_id, "eligibility_state", actor,
                  {"from": row["eligibility_state"], "to": state})
