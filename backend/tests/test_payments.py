"""Undo window + refunds (§7.8): only at expiry, undo = no Dodo call, idempotent, restart-safe."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.store import calls_repo
from app.store.db import Decision, Payment, RefundHistory, get_session
from app.store.seed import reset_all

pytestmark = pytest.mark.asyncio


def _decision(call_profile="cust_meera", amount=8500, status="open", finalize_at=None):
    call_id = f"c_{datetime.now().timestamp()}"
    calls_repo.create_call(call_id, "text", call_profile, None)
    dec_id = calls_repo.add_decision(call_id, kind="EXECUTE_WITH_HOLD", action_type="refund", amount_inr=amount,
                                     reason="t", trace=[], first_proposed_action="refund", latency_ms=1)
    calls_repo.update_decision(dec_id, status=status, finalize_at=finalize_at)
    return call_id, dec_id


def _pay(db, pid="pay_1", order="ORD-66540"):
    with Session(db) as s:
        s.add(Payment(payment_id=pid, order_id=order, amount_inr=8500))
        s.commit()


async def test_refund_only_at_expiry_and_history_row(rt, db):
    _pay(db)
    call_id, dec_id = _decision()
    await rt.undo.open_window(call_id, dec_id, 1)
    await asyncio.sleep(0.3)
    assert rt.payments.refunds == []
    await asyncio.sleep(1.1)
    assert rt.payments.refunds == ["pay_1"]
    row = calls_repo.get_decision(dec_id)
    assert row["status"] == "finalized" and row["refund_id"] == "ref_1"
    with Session(db) as s:
        assert s.get(Payment, "pay_1").refunded
        assert len(s.exec(select(RefundHistory).where(RefundHistory.is_seed == False)).all()) == 1  # noqa: E712


async def test_undo_means_no_dodo_call(rt, db):
    _pay(db)
    call_id, dec_id = _decision()
    await rt.undo.open_window(call_id, dec_id, 1)
    assert await rt.undo.undo(dec_id)
    await asyncio.sleep(1.2)
    assert rt.payments.refunds == []
    assert not await rt.undo.undo(dec_id)


async def test_idempotent_never_refunds_twice(rt, db):
    _pay(db)
    call_id, dec_id = _decision(status="pending_finalize")
    calls_repo.update_decision(dec_id, refund_id="already")
    assert await rt.undo.finalize(call_id, dec_id) is None
    assert rt.payments.refunds == []


async def test_no_payment_left_fails(rt, db):
    call_id, dec_id = _decision(status="pending_finalize")
    line = await rt.undo.finalize(call_id, dec_id)
    assert "couldn't complete" in line
    assert calls_repo.get_decision(dec_id)["status"] == "refund_failed"


async def test_dodo_error_fails_without_retry(rt, db):
    _pay(db)
    rt.payments.fail = True
    call_id, dec_id = _decision(status="pending_finalize")
    await rt.undo.finalize(call_id, dec_id)
    assert calls_repo.get_decision(dec_id)["status"] == "refund_failed"


async def test_restart_overdue_failed_and_due_restarted(rt, db):
    _pay(db)
    now = datetime.now(timezone.utc)
    _, overdue = _decision(status="pending_finalize", finalize_at=now - timedelta(seconds=5))
    _, due = _decision(status="pending_finalize", finalize_at=now + timedelta(seconds=1))
    await rt.undo.recover_on_startup()
    assert calls_repo.get_decision(overdue)["status"] == "refund_failed"
    assert due in rt.undo.pending_ids()
    await asyncio.sleep(1.3)
    assert calls_repo.get_decision(due)["status"] == "finalized"
    assert rt.payments.refunds == ["pay_1"]


async def test_seed_reset_leaves_payments(rt, db):
    _pay(db)
    with get_session() as s:
        reset_all(s)
    with Session(db) as s:
        assert s.get(Payment, "pay_1") is not None
