"""Small dashboard-adjacent endpoints that don't need the full dashboard (§8.2). Phase 6 will
add the rest of /api/* here."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.guardrail import fault_injection

router = APIRouter(prefix="/api/demo")

_VALID_MODES = {"off", "flip_negation"}


class FaultInjectionState(BaseModel):
    mode: str


@router.get("/fault-injection", response_model=FaultInjectionState)
async def get_fault_injection() -> FaultInjectionState:
    return FaultInjectionState(mode=fault_injection.current_mode())


@router.post("/fault-injection", response_model=FaultInjectionState)
async def set_fault_injection(body: FaultInjectionState) -> FaultInjectionState:
    if body.mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {sorted(_VALID_MODES)}")
    fault_injection.set_mode(body.mode)
    return FaultInjectionState(mode=fault_injection.current_mode())
