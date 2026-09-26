"""Undo-window timers — money moves last (§7.8). Timers live in memory and survive hang-up.
Refunds happen only at expiry; undo means Dodo is never called; a decision with a refund_id is
never refunded again; nothing is ever auto-retried."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.agent import templates
from app.events import bus
from app.integrations.payments import PaymentsError
from app.store import calls_repo

if TYPE_CHECKING:
    from app.agent.runtime import Runtime

logger = logging.getLogger("guardrail.undo")


class UndoManager:
    def __init__(self, runtime: "Runtime") -> None:
        self._rt = runtime
        self._timers: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, dec_id: str) -> asyncio.Lock:
        return self._locks.setdefault(dec_id, asyncio.Lock())

    async def open_window(self, call_id: str, dec_id: str, seconds: int) -> datetime:
        finalize_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await calls_repo.run(calls_repo.update_decision, dec_id, status="pending_finalize", finalize_at=finalize_at)
        self._schedule(call_id, dec_id, seconds)
        bus.publish("undo.started", call_id, decision_id=dec_id, seconds=seconds, finalize_at=finalize_at.isoformat())
        return finalize_at

    def _schedule(self, call_id: str, dec_id: str, seconds: float) -> None:
        self._timers[dec_id] = asyncio.create_task(self._expire_after(call_id, dec_id, seconds))

    async def _expire_after(self, call_id: str, dec_id: str, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))
        self._timers.pop(dec_id, None)
        bus.publish("undo.expired", call_id, decision_id=dec_id)
        line = await self.finalize(call_id, dec_id)
        if line is not None:
            await self._rt.on_refund_finished(call_id, line)

    async def undo(self, dec_id: str, *, notify_session: bool = True) -> bool:
        """Returns False if the decision isn't pending_finalize (already finalized/undone)."""
        async with self._lock(dec_id):
            row = await calls_repo.run(calls_repo.get_decision, dec_id)
            if row is None or row["status"] != "pending_finalize":
                return False
            task = self._timers.pop(dec_id, None)
            if task:
                task.cancel()
            await calls_repo.run(calls_repo.update_decision, dec_id, status="undone")
        call_id = row["call_id"]
        bus.publish("undo.applied", call_id, decision_id=dec_id)
        await self._ticket_event(row, "undone", "<b>Refund undone</b> within the undo window. Dodo was not called.")
        if notify_session:
            await self._rt.on_refund_finished(call_id, templates.REFUND_UNDONE)
        return True

    async def finalize(self, call_id: str, dec_id: str) -> str | None:
        """Refund now. Returns the line to speak, or None if there was nothing to do."""
        async with self._lock(dec_id):
            row = await calls_repo.run(calls_repo.get_decision, dec_id)
            if row is None or row["status"] != "pending_finalize" or row["refund_id"]:
                return None  # idempotency: undone, already finalized, or already refunded
            order = await calls_repo.run(calls_repo.order_for_call, call_id)
            payment = await calls_repo.run(calls_repo.claim_refundable_payment, order.id) if order else None
            if order is None or (payment is None and self._rt.payments.provider == "dodo"):
                return await self._fail(row, "no refundable test payment left")
            payment_id = payment.payment_id if payment else f"sim_pay_{order.id}"
            try:
                result = await self._rt.payments.refund(
                    payment_id, row["amount_inr"], reason=f"GuardRail {dec_id}",
                    metadata={"decision_id": dec_id, "call_id": call_id},
                )
            except PaymentsError as exc:
                return await self._fail(row, str(exc))
            await calls_repo.run(calls_repo.update_decision, dec_id, status="finalized", refund_id=result.refund_id,
                                 refund_status=result.status, refund_provider=result.provider)
            await calls_repo.run(calls_repo.record_refund, payment_id if payment else "", result.refund_id,
                                 order.customer_id, order.id)
        bus.publish("refund.issued", call_id, decision_id=dec_id, refund_id=result.refund_id, status=result.status,
                    amount=result.amount, currency=result.currency, provider=result.provider)
        label = "Simulated refund" if result.provider == "simulated" else "Dodo refund (test mode)"
        await self._ticket_event(row, "finalized", f"<b>{label} issued</b><br>refund_id: {result.refund_id}<br>"
                                 f"status: {result.status}<br>amount: {result.amount} {result.currency}")
        return templates.REFUND_ISSUED

    async def _fail(self, row: dict, error: str) -> str:
        await calls_repo.run(calls_repo.update_decision, row["id"], status="refund_failed", refund_status="failed")
        bus.publish("refund.failed", row["call_id"], decision_id=row["id"], error=error)
        await self._ticket_event(row, "refund-failed", f"<b>Refund failed</b>: {error}. Not retried; a specialist will follow up.")
        return templates.REFUND_FAILED

    async def _ticket_event(self, row: dict, new_tag: str, note_html: str) -> None:
        ticket_id = row.get("ticket_id")
        if not ticket_id:
            return
        base = self._rt.ticket_tags.get(ticket_id) or ["guardrail", "refund"]
        tags = [t for t in base if t not in ("pending-finalize", "finalized", "undone", "refund-failed")] + [new_tag]
        self._rt.ticket_tags[ticket_id] = tags
        await self._rt.helpdesk.add_note(ticket_id, note_html)
        await self._rt.helpdesk.update_tags(ticket_id, tags)
        bus.publish("ticket.updated", row["call_id"], ticket_id=ticket_id, tags=tags)

    async def recover_on_startup(self) -> None:
        """Overdue pending_finalize → refund_failed (never refunded automatically); not yet due →
        restart the timer for the remaining time (§7.8)."""
        now = datetime.now(timezone.utc)
        for row in await calls_repo.run(calls_repo.pending_finalize_decisions):
            finalize_at = calls_repo.as_utc(row["finalize_at"])
            if finalize_at is None or finalize_at <= now:
                logger.warning("decision %s: undo window overdue at startup → refund_failed", row["id"])
                await self._fail(row, "interrupted by restart")
            else:
                remaining = (finalize_at - now).total_seconds()
                logger.info("decision %s: restarting undo timer (%.0fs left)", row["id"], remaining)
                self._schedule(row["call_id"], row["id"], remaining)

    def pending_ids(self) -> list[str]:
        return list(self._timers)
