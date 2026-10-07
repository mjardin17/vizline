"""Promo wallet routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...promo import service
from ...promo.wallet import IllegalTransitionError
from ..deps import get_conn

router = APIRouter()


class WalletCreate(BaseModel):
    platform: str
    reward_type: str
    face_value_cents: int
    cash_vs_credit: str
    expires_at: Optional[str] = None
    manual: bool = True
    actor: str = "user"


class WalletTransition(BaseModel):
    to_state: str
    actor: str = "user"


@router.get("/api/wallet")
def api_list_wallet(conn=Depends(get_conn)):
    return {"records": [dict(r) for r in service.list_wallet(conn)]}


@router.post("/api/wallet", status_code=201)
def api_create_wallet_record(body: WalletCreate, conn=Depends(get_conn)):
    try:
        record_id = service.create_wallet_record(
            conn,
            platform=body.platform,
            reward_type=body.reward_type,
            face_value_cents=body.face_value_cents,
            cash_vs_credit=body.cash_vs_credit,
            expires_at=body.expires_at,
            manual=body.manual,
            actor=body.actor,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"id": record_id, "source": "manually_entered_unverified" if body.manual else "verified"}


@router.post("/api/wallet/{record_id}/transition")
def api_transition_wallet(record_id: int, body: WalletTransition, conn=Depends(get_conn)):
    try:
        service.transition_wallet_record(conn, record_id, body.to_state, actor=body.actor)
    except IllegalTransitionError as exc:
        raise HTTPException(422, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"id": record_id, "state": body.to_state}
