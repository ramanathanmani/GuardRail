"""POST /api/sim/start|turn|end — typed-text calls (§8.2). Same pipeline as the phone path,
just without audio."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.runtime import get_runtime
from app.agent.session_factory import UnknownProfile, build_session

router = APIRouter(prefix="/api/sim")


class SimStartRequest(BaseModel):
    profile_id: str | None = None  # defaults to the dashboard's "Next caller"


class SimStartResponse(BaseModel):
    call_id: str
    profile: str


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
    rt = get_runtime()
    profile = (body.profile_id or rt.next_profile).removeprefix("cust_")
    if profile == "auto":
        profile = "riya"  # a typed call has no caller number to match
    call_id = f"sim_{uuid.uuid4().hex[:12]}"
    try:
        session = await build_session(rt, call_id, profile, caller_masked="typed")
    except UnknownProfile:
        raise HTTPException(status_code=404, detail=f"unknown profile_id: {body.profile_id}")
    await session.start()
    return SimStartResponse(call_id=call_id, profile=profile)


@router.post("/turn", response_model=SimTurnResponse)
async def sim_turn(body: SimTurnRequest) -> SimTurnResponse:
    session = get_runtime().sessions.get(body.call_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown or ended call_id: {body.call_id}")
    result = await session.process_turn(body.text)
    return SimTurnResponse(**result)


@router.post("/end")
async def sim_end(body: SimEndRequest) -> dict:
    session = get_runtime().sessions.get(body.call_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown or ended call_id: {body.call_id}")
    await session.hangup_received()
    return {"status": "ended", "call_id": body.call_id}
