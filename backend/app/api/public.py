"""POST /v1/check — the pluggable public API (§8.1). Same engine as the phone path."""
from __future__ import annotations

import secrets
import time
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.agent import templates
from app.agent.intents import compute_candidates
from app.agent.runtime import get_runtime
from app.config import get_settings
from app.guardrail.engine import run_gates
from app.models import ActionType, CustomerProfile, DecisionKind, GateContext, OrderInfo, ProposedAction, Understanding
from app.store.db import get_session
from app.store.queries import load_sop

router = APIRouter(prefix="/v1")


class Transcript(BaseModel):
    asr_text: str


class ProposedIn(BaseModel):
    type: ActionType | None = None
    amount: float = 0
    currency: str = "INR"
    order_id: str | None = None


class OrderCtx(BaseModel):
    category: str = "general"
    delivered_days_ago: int | None = None
    status: str = "delivered"
    item: str = "item"


class CustomerCtx(BaseModel):
    refunds_in_window: int = 0
    account_age_days: int = 365
    avg_order_inr: float = 1000
    order: OrderCtx | None = None


class CheckRequest(BaseModel):
    tenant_id: str
    call_id: str
    language_hint: str = "en-IN"
    transcript: Transcript
    understanding: Understanding | None = None
    proposed_action: ProposedIn = Field(default_factory=ProposedIn)
    customer_context: CustomerCtx = Field(default_factory=CustomerCtx)


@router.post("/check")
async def check(body: CheckRequest, x_guardrail_key: str | None = Header(default=None)) -> dict:
    settings = get_settings()
    settings.require_public_api()
    if not x_guardrail_key or not secrets.compare_digest(x_guardrail_key, settings.GUARDRAIL_API_KEY or ""):
        raise HTTPException(status_code=401, detail="bad X-GuardRail-Key")
    started = time.monotonic()

    understanding = body.understanding
    if understanding is None:
        understanding = await get_runtime().claude.understand_turn(body.transcript.asr_text, "provided by the caller platform")

    cc = body.customer_context
    order = None
    if cc.order is not None:
        delivered_on = date.today() - timedelta(days=cc.order.delivered_days_ago) if cc.order.delivered_days_ago is not None else None
        order = OrderInfo(id=body.proposed_action.order_id or "ORDER", item=cc.order.item, category=cc.order.category,
                          amount_inr=body.proposed_action.amount, status=cc.order.status, delivered_on=delivered_on)
    with get_session() as s:
        sop = load_sop(s)
    ptype = body.proposed_action.type
    ctx = GateContext(
        call_id=body.call_id,
        customer=CustomerProfile(id=f"{body.tenant_id}:{body.call_id}", name="API caller", phone="-",
                                 language_code=body.language_hint, account_age_days=cc.account_age_days,
                                 avg_order_inr=cc.avg_order_inr),
        order=order, refunds_in_window=cc.refunds_in_window, sop=sop, transcript_used=body.transcript.asr_text,
        understanding=understanding,
        proposed_action=ProposedAction(type=ptype, cash_amount_inr=body.proposed_action.amount if ptype == ActionType.refund else 0,
                                       order_id=body.proposed_action.order_id),
        actions_mentioned=understanding.actions_mentioned,
    )
    result = run_gates(ctx)
    decision = result.decision
    if decision.kind == DecisionKind.CONFIRM_FIRST and not decision.candidate_actions:
        decision.candidate_actions, decision.negated_action = compute_candidates(understanding)
    prompt = templates.confirm_first_prompt(decision) if decision.kind == DecisionKind.CONFIRM_FIRST else None
    reason = next((s.detail for s in result.trace.steps if s.status.value in ("warn", "fail")), decision.reason)
    return {
        "decision_id": f"dec_{uuid.uuid4().hex[:8]}",
        "decision": decision.kind.value,
        "reason": reason,
        "confirmation_prompt": prompt,
        "trace": [s.model_dump(mode="json") for s in result.trace.steps],
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
