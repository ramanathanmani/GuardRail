"""Sync DB writes/reads for live calls, decisions, payments and refunds. Async callers wrap
these in asyncio.to_thread (via `run`)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlmodel import select

from app.store.db import Call, Customer, Decision, Order, Payment, RefundHistory, Turn, get_session


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    # SQLite drops tzinfo; everything we store is UTC.
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def run(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


def create_call(call_id: str, channel: str, profile_id: str | None, caller_masked: str | None) -> None:
    with get_session() as s:
        s.add(Call(id=call_id, channel=channel, profile_id=profile_id, caller_masked=caller_masked,
                   started_at=now_utc(), state="GREETING"))
        s.commit()


def update_call(call_id: str, **fields: Any) -> None:
    with get_session() as s:
        row = s.get(Call, call_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        s.add(row)
        s.commit()


def add_turn(call_id: str, idx: int, speaker: str, *, text_asr: str | None = None, text_used: str | None = None,
             fault_injected: bool = False, english: str | None = None, understanding: dict | None = None) -> None:
    with get_session() as s:
        s.add(Turn(id=f"{call_id}_t{idx}", call_id=call_id, idx=idx, speaker=speaker, text_asr=text_asr,
                   text_used=text_used, fault_injected=fault_injected, english=english,
                   understanding_json=understanding, ts=now_utc()))
        s.commit()


def add_decision(call_id: str, *, kind: str, action_type: str | None, amount_inr: float, reason: str,
                 trace: list[dict], first_proposed_action: str | None, latency_ms: int | None) -> str:
    dec_id = f"dec_{uuid.uuid4().hex[:10]}"
    with get_session() as s:
        s.add(Decision(id=dec_id, call_id=call_id, kind=kind, action_type=action_type, amount_inr=amount_inr,
                       reason=reason, trace_json=trace, first_proposed_action=first_proposed_action,
                       latency_ms=latency_ms, created_at=now_utc()))
        s.commit()
    return dec_id


def update_decision(dec_id: str, **fields: Any) -> None:
    with get_session() as s:
        row = s.get(Decision, dec_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        s.add(row)
        s.commit()


def get_decision(dec_id: str) -> dict | None:
    with get_session() as s:
        row = s.get(Decision, dec_id)
        return row.model_dump() if row else None


def set_ticket_on_call_decisions(call_id: str, ticket_id: str, ticket_url: str | None) -> None:
    with get_session() as s:
        for row in s.exec(select(Decision).where(Decision.call_id == call_id)).all():
            row.ticket_id, row.ticket_url = ticket_id, ticket_url
            s.add(row)
        s.commit()


def has_pending_finalize(call_id: str) -> bool:
    with get_session() as s:
        return s.exec(select(Decision).where(Decision.call_id == call_id,
                                             Decision.status == "pending_finalize")).first() is not None


def pending_finalize_decisions() -> list[dict]:
    with get_session() as s:
        return [r.model_dump() for r in s.exec(select(Decision).where(Decision.status == "pending_finalize")).all()]


def order_for_call(call_id: str) -> Order | None:
    with get_session() as s:
        call = s.get(Call, call_id)
        if call is None or call.profile_id is None:
            return None
        return s.exec(select(Order).where(Order.customer_id == call.profile_id)).first()


def claim_refundable_payment(order_id: str) -> Payment | None:
    with get_session() as s:
        return s.exec(select(Payment).where(Payment.order_id == order_id, Payment.refunded == False)  # noqa: E712
                      .order_by(Payment.payment_id)).first()


def record_refund(payment_id: str, refund_id: str, customer_id: str | None, order_id: str) -> None:
    with get_session() as s:
        payment = s.get(Payment, payment_id)
        if payment is not None:
            payment.refunded, payment.refund_id = True, refund_id
            s.add(payment)
        if customer_id:
            s.add(RefundHistory(id=f"rh_{uuid.uuid4().hex[:10]}", customer_id=customer_id, order_id=order_id,
                                refunded_on=date.today(), is_seed=False))
        s.commit()


def find_customer_by_phone(phone: str) -> str | None:
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    if not digits:
        return None
    with get_session() as s:
        for c in s.exec(select(Customer)).all():
            if "".join(ch for ch in c.phone if ch.isdigit()).endswith(digits):
                return c.id
    return None


def find_order(order_id: str) -> Order | None:
    with get_session() as s:
        return s.get(Order, order_id.upper())


def call_record(call_id: str) -> dict | None:
    """Full call for /api/calls/{id} and the S3 archive. Never includes the raw phone number."""
    with get_session() as s:
        call = s.get(Call, call_id)
        if call is None:
            return None
        turns = s.exec(select(Turn).where(Turn.call_id == call_id).order_by(Turn.idx)).all()
        decisions = s.exec(select(Decision).where(Decision.call_id == call_id).order_by(Decision.created_at)).all()
        ticket = next(((d.ticket_id, d.ticket_url) for d in decisions if d.ticket_id), (None, None))
        refund = next(({"refund_id": d.refund_id, "status": d.refund_status, "provider": d.refund_provider,
                        "decision_status": d.status} for d in decisions if d.action_type == "refund"
                       and d.kind in ("EXECUTE", "EXECUTE_WITH_HOLD")), None)
        return {
            "call_id": call.id, "channel": call.channel, "profile_id": call.profile_id,
            "caller_masked": call.caller_masked, "started_at": call.started_at, "ended_at": call.ended_at,
            "state": call.state, "archived": call.archived, "is_seed": call.is_seed,
            "turns": [t.model_dump() for t in turns],
            "decisions": [d.model_dump() for d in decisions],
            "ticket": {"ticket_id": ticket[0], "url": ticket[1]},
            "refund": refund,
        }
