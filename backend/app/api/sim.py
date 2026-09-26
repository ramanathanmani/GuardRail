"""POST /api/sim/start|turn|end — typed-text calls (§8.2). Same engine as the phone path,
just without audio."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.claude_client import ClaudeClient
from app.agent.conversation import CallSession
from app.config import get_settings
from app.store import queries
from app.store.db import get_session

router = APIRouter(prefix="/api/sim")

_sessions: dict[str, CallSession] = {}
_claude_client: ClaudeClient | None = None


def _get_claude_client() -> ClaudeClient:
    global _claude_client
    if _claude_client is None:
        _claude_client = ClaudeClient(get_settings())
    return _claude_client


class SimStartRequest(BaseModel):
    profile_id: str


class SimStartResponse(BaseModel):
    call_id: str


class SimTurnRequest(BaseModel):
    call_id: str
    text: str


class SimTurnResponse(BaseModel):
    reply: str
    state: str
    decision: dict | None = None


class SimEndRequest(BaseModel):
    call_id: str


@router.post("/start", response_model=SimStartResponse)
async def sim_start(body: SimStartRequest) -> SimStartResponse:
    customer_id = body.profile_id if body.profile_id.startswith("cust_") else f"cust_{body.profile_id}"
    settings = get_settings()
    with get_session(settings) as session:
        customer = queries.load_customer_profile(session, customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail=f"unknown profile_id: {body.profile_id}")
        order = queries.load_customer_order(session, customer_id)
        sop = queries.load_sop(session)
        refunds_in_window = queries.count_refunds_in_window(session, customer_id, sop)

    call_id = f"sim_{uuid.uuid4().hex[:12]}"
    _sessions[call_id] = CallSession(
        call_id=call_id, customer=customer, order=order, sop=sop,
        refunds_in_window=refunds_in_window, claude=_get_claude_client(),
    )
    return SimStartResponse(call_id=call_id)


@router.post("/turn", response_model=SimTurnResponse)
async def sim_turn(body: SimTurnRequest) -> SimTurnResponse:
    session = _sessions.get(body.call_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown call_id: {body.call_id}")
    result = await session.handle_turn(body.text)
    return SimTurnResponse(**result)


@router.post("/end")
async def sim_end(body: SimEndRequest) -> dict:
    session = _sessions.pop(body.call_id, None)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown call_id: {body.call_id}")
    session.state = "ENDED"
    return {"status": "ended", "call_id": body.call_id}
