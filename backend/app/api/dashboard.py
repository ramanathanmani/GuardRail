"""Dashboard REST (§8.2) + the live event socket WS /ws/dashboard (§8.3)."""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlmodel import select

from app.agent.runtime import PROFILE_CHOICES, get_runtime
from app.events import bus
from app.guardrail import fault_injection
from app.store import calls_repo
from app.store.db import Call, Customer, Decision, SopRules, get_session

router = APIRouter()

_VALID_MODES = {"off", "flip_negation"}
_LANG = {"ta-IN": "ta", "hi-IN": "hi", "kn-IN": "kn"}


# ---------------------------------------------------------------- demo controls

class FaultInjectionState(BaseModel):
    mode: str


@router.get("/api/demo/fault-injection", response_model=FaultInjectionState)
async def get_fault_injection() -> FaultInjectionState:
    return FaultInjectionState(mode=fault_injection.current_mode())


@router.post("/api/demo/fault-injection", response_model=FaultInjectionState)
async def set_fault_injection(body: FaultInjectionState) -> FaultInjectionState:
    if body.mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {sorted(_VALID_MODES)}")
    fault_injection.set_mode(body.mode)
    bus.publish("demo.fault_injection", None, mode=body.mode)
    return FaultInjectionState(mode=fault_injection.current_mode())


class ProfileState(BaseModel):
    profile: str


@router.get("/api/demo/profile", response_model=ProfileState)
async def get_profile() -> ProfileState:
    return ProfileState(profile=get_runtime().next_profile)


@router.post("/api/demo/profile", response_model=ProfileState)
async def set_profile(body: ProfileState) -> ProfileState:
    profile = body.profile.lower().removeprefix("cust_")
    if profile not in PROFILE_CHOICES:
        raise HTTPException(status_code=400, detail=f"profile must be one of {list(PROFILE_CHOICES)}")
    get_runtime().next_profile = profile
    bus.publish("demo.profile", None, profile=profile)
    return ProfileState(profile=profile)


@router.get("/api/demo/info")
async def demo_info() -> dict:
    rt = get_runtime()
    s = rt.settings
    with get_session() as db:
        profiles = [{"id": c.id.removeprefix("cust_"), "name": c.name, "language_code": c.language_code}
                    for c in db.exec(select(Customer)).all()]
    return {
        "phone_number": s.VOBIZ_PHONE_NUMBER, "payments_provider": s.PAYMENTS, "helpdesk": s.HELPDESK,
        "undo_window_seconds": s.UNDO_WINDOW_SECONDS, "next_profile": rt.next_profile,
        "fault_injection": fault_injection.current_mode(), "profiles": profiles,
        "live_calls": list(rt.sessions),
    }


# ---------------------------------------------------------------- SOP

class SopUpdate(BaseModel):
    return_window_days: int = Field(ge=0, le=365)
    max_refunds: int = Field(ge=0, le=100)
    refund_window_days: int = Field(ge=1, le=365)
    excluded_categories: list[str]
    high_value_hold_inr: float = Field(ge=0)
    require_confirm_on_negation: bool
    undo_window_enabled: bool


def _sop_dict(row: SopRules) -> dict:
    return row.model_dump(exclude={"id"})


@router.get("/api/sop")
async def get_sop() -> dict:
    with get_session() as db:
        row = db.get(SopRules, 1)
        if row is None:
            raise HTTPException(status_code=500, detail="sop_rules row missing")
        return _sop_dict(row)


@router.put("/api/sop")
async def put_sop(body: SopUpdate) -> dict:
    def _save() -> dict:
        with get_session() as db:
            row = db.get(SopRules, 1)
            for k, v in body.model_dump().items():
                setattr(row, k, [c.strip().lower() for c in v if c.strip()] if k == "excluded_categories" else v)
            db.add(row)
            db.commit()
            db.refresh(row)
            return _sop_dict(row)

    result = await calls_repo.run(_save)
    bus.publish("sop.updated", None, **result)
    return result


# ---------------------------------------------------------------- calls / decisions

def _final_decision(decisions: list[Decision]) -> Decision | None:
    return decisions[-1] if decisions else None


@router.get("/api/calls")
async def list_calls(limit: int = 50) -> list[dict]:
    def _q() -> list[dict]:
        with get_session() as db:
            calls = db.exec(select(Call).order_by(Call.started_at.desc()).limit(limit)).all()
            out = []
            for c in calls:
                decs = db.exec(select(Decision).where(Decision.call_id == c.id).order_by(Decision.created_at)).all()
                final = _final_decision(decs)
                ticket = next((d.ticket_url or d.ticket_id for d in decs if d.ticket_id), None)
                refund = next((d.refund_status or d.status for d in decs if d.action_type == "refund"
                               and d.kind in ("EXECUTE", "EXECUTE_WITH_HOLD")), None)
                out.append({
                    "call_id": c.id, "started_at": c.started_at, "ended_at": c.ended_at,
                    "profile": (c.profile_id or "unknown").removeprefix("cust_"), "channel": c.channel,
                    "state": c.state, "final_decision": final.kind if final else None,
                    "action_type": final.action_type if final else None,
                    "ticket_url": ticket, "refund_status": refund, "is_seed": c.is_seed,
                })
            return out

    return await calls_repo.run(_q)


@router.get("/api/calls/{call_id}")
async def get_call(call_id: str) -> dict:
    record = await calls_repo.run(calls_repo.call_record, call_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown call")
    return record


@router.get("/api/decisions")
async def list_decisions(kind: str | None = None, limit: int = 200) -> list[dict]:
    def _q() -> list[dict]:
        with get_session() as db:
            q = select(Decision, Call).join(Call, Call.id == Decision.call_id)
            if kind:
                q = q.where(Decision.kind == kind)
            rows = db.exec(q.order_by(Decision.created_at.desc()).limit(limit)).all()
            return [{**d.model_dump(), "profile": (c.profile_id or "unknown").removeprefix("cust_"),
                     "channel": c.channel} for d, c in rows]

    return await calls_repo.run(_q)


def _compute_stats() -> dict:
    with get_session() as db:
        decisions = db.exec(select(Decision).order_by(Decision.created_at)).all()
        calls = {c.id: c for c in db.exec(select(Call)).all()}
        langs = {c.id: c.language_code for c in db.exec(select(Customer)).all()}

    by_call: dict[str, list[Decision]] = defaultdict(list)
    for d in decisions:
        by_call[d.call_id].append(d)

    prevented = 0
    money = 0.0
    seed_prevented = 0
    negation_by_lang: Counter = Counter()
    for call_id, decs in by_call.items():
        call = calls.get(call_id)
        lang = _LANG.get(langs.get(call.profile_id or "", ""), None) if call else None
        if any(any(s.get("gate") == "negation" and s.get("status") == "warn" for s in (d.trace_json or [])) for d in decs):
            if lang:
                negation_by_lang[lang] += 1
        executed = [d for d in decs if d.kind in ("EXECUTE", "EXECUTE_WITH_HOLD")]
        first = next((d.first_proposed_action for d in decs if d.first_proposed_action), None)
        is_prevented = (
            any(d.action_type and d.first_proposed_action and d.action_type != d.first_proposed_action for d in executed)
            or any(d.kind == "OFFER_ALTERNATIVE" for d in decs)
            or any(d.kind == "HUMAN_HANDOFF" and (d.is_seed or any(
                s.get("gate") in ("sop", "risk") and s.get("status") == "fail" for s in (d.trace_json or [])))
                for d in decs)
            or any(d.status == "undone" for d in decs)
        )
        if is_prevented:
            prevented += 1
            if call and call.is_seed:
                seed_prevented += 1
            refund_executed = any(d.action_type == "refund" and d.status in ("finalized", "pending_finalize") for d in executed)
            if first == "refund" and not refund_executed:
                money += max((d.amount_inr for d in decs), default=0)
            elif any(d.status == "undone" for d in decs):
                money += max((d.amount_inr for d in decs if d.status == "undone"), default=0)

    latencies = [d.latency_ms for d in decisions if d.latency_ms]
    per_day: Counter = Counter()
    for d in decisions:
        per_day[(d.created_at.date().isoformat(), d.kind)] += 1
    return {
        "actions_checked": len(decisions),
        "wrong_actions_prevented": prevented,
        "wrong_actions_prevented_sample": seed_prevented,
        "money_protected_inr": money,
        "avg_decision_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "negation_catches_by_language": {k: negation_by_lang.get(k, 0) for k in ("hi", "ta", "kn")},
        "decisions_by_kind": dict(Counter(d.kind for d in decisions)),
        "per_day": [{"date": day, "kind": kind, "count": n} for (day, kind), n in sorted(per_day.items())],
        "sample_rows": sum(1 for d in decisions if d.is_seed),
    }


@router.get("/api/stats")
async def stats() -> dict:
    return await calls_repo.run(_compute_stats)


@router.post("/api/undo/{decision_id}")
async def undo(decision_id: str) -> dict:
    ok = await get_runtime().undo.undo(decision_id)
    if not ok:
        raise HTTPException(status_code=409, detail="decision is not pending_finalize")
    return {"status": "undone", "decision_id": decision_id}


# ---------------------------------------------------------------- live events

@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = bus.subscribe()
    try:
        await websocket.send_json({"type": "hello", "call_id": None, "recent": bus.recent_events()[-50:]})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:  # noqa: BLE001 — a broken dashboard socket must never affect calls
        pass
    finally:
        bus.unsubscribe(queue)
