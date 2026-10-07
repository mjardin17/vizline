"""Offer, terms, review, and eligibility-rule routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...config import load_authorized_sources, load_settings
from ...promo import service, terms
from ..deps import get_conn

router = APIRouter()


class OfferCreate(BaseModel):
    platform: str
    title: str
    offer_type: str
    scope: str = "public"
    source_id: Optional[int] = None


class TermsTextIn(BaseModel):
    raw_text: str
    source_url: Optional[str] = None
    fetched_by: str = "manual"
    actor: str = "user"


class TermsFetchIn(BaseModel):
    url: str
    actor: str = "user"


class ReviewIn(BaseModel):
    review_state: str
    actor: str = "user"


class EligibilityIn(BaseModel):
    state: str
    actor: str = "user"


class RulesIn(BaseModel):
    rules: dict
    verified: bool = False
    actor: str = "user"


@router.get("/api/offers")
def api_list_offers(conn=Depends(get_conn)):
    return {"offers": [dict(r) for r in service.list_offers(conn)]}


@router.post("/api/offers", status_code=201)
def api_create_offer(body: OfferCreate, conn=Depends(get_conn)):
    try:
        offer_id = service.create_offer(
            conn,
            platform=body.platform,
            title=body.title,
            offer_type=body.offer_type,
            scope=body.scope,
            source_id=body.source_id,
            actor="user",
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"id": offer_id}


@router.get("/api/offers/{offer_id}")
def api_get_offer(offer_id: int, conn=Depends(get_conn)):
    try:
        return service.get_offer_detail(conn, offer_id)
    except terms.TermsError as exc:
        raise HTTPException(404, str(exc))


@router.post("/api/offers/{offer_id}/terms/text", status_code=201)
def api_capture_terms_text(offer_id: int, body: TermsTextIn, conn=Depends(get_conn)):
    try:
        result = terms.capture_terms_text(
            conn,
            offer_id=offer_id,
            raw_text=body.raw_text,
            source_url=body.source_url,
            fetched_by=body.fetched_by,
            actor=body.actor,
        )
    except terms.TermsError as exc:
        raise HTTPException(422, str(exc))
    return {
        "offer_id": result.offer_id,
        "version_no": result.version_no,
        "sha256": result.text_hash,
        "changed": result.changed,
        "duplicate": result.duplicate,
    }


@router.post("/api/offers/{offer_id}/terms/fetch", status_code=201)
def api_fetch_terms(offer_id: int, body: TermsFetchIn, conn=Depends(get_conn)):
    settings = load_settings()
    sources = load_authorized_sources(settings)
    if not sources:
        raise HTTPException(409, terms.NO_SOURCE_MESSAGE)
    try:
        text = terms.fetch_authorized_text(body.url, sources)
    except (terms.SourceNotAuthorizedError, terms.RobotsDisallowedError) as exc:
        raise HTTPException(409, str(exc))
    except terms.FetchError as exc:
        raise HTTPException(502, str(exc))
    try:
        result = terms.capture_terms_text(
            conn,
            offer_id=offer_id,
            raw_text=text,
            source_url=body.url,
            fetched_by="authorized_fetch",
            actor=body.actor,
        )
    except terms.TermsError as exc:
        raise HTTPException(422, str(exc))
    return {
        "offer_id": result.offer_id,
        "version_no": result.version_no,
        "sha256": result.text_hash,
        "changed": result.changed,
        "duplicate": result.duplicate,
    }


@router.post("/api/offers/{offer_id}/review")
def api_set_review(offer_id: int, body: ReviewIn, conn=Depends(get_conn)):
    try:
        terms.set_review_state(conn, offer_id, body.review_state, actor=body.actor)
    except terms.TermsError as exc:
        raise HTTPException(422, str(exc))
    return {"offer_id": offer_id, "review_state": body.review_state}


@router.post("/api/offers/{offer_id}/eligibility")
def api_set_eligibility(offer_id: int, body: EligibilityIn, conn=Depends(get_conn)):
    try:
        terms.set_eligibility(conn, offer_id, body.state, actor=body.actor)
    except terms.TermsError as exc:
        raise HTTPException(422, str(exc))
    return {"offer_id": offer_id, "eligibility_state": body.state}


@router.post("/api/offers/{offer_id}/rules")
def api_save_rules(offer_id: int, body: RulesIn, conn=Depends(get_conn)):
    service.save_eligibility_rules(
        conn, offer_id, body.rules, verified=body.verified, actor=body.actor
    )
    return {"offer_id": offer_id, "verified": body.verified}
