"""CallSession state machine (§6.2). Drives one call's turns through fault injection ->
understand_turn -> intents -> run_gates -> a spoken template, for both the phone path and
typed-text sim calls (§8.2). Side effects (DB rows, dashboard events, the one Freshdesk ticket,
undo windows) happen here; audio lives behind the Transport."""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass

from app.agent import templates
from app.agent.answers import is_cancel_word, parse_confirmation_answer, parse_offer_answer
from app.agent.claude_client import ClaudeUnavailable
from app.agent.intents import INTENT_TO_ACTION, normalize_action_word, pick_pending_action
from app.agent.runtime import NullTransport, Runtime, Transport
from app.events import bus
from app.guardrail import fault_injection
from app.guardrail.engine import run_gates
from app.integrations.helpdesk import TicketRequest
from app.models import (
    ActionType,
    CustomerProfile,
    Decision,
    DecisionKind,
    GateContext,
    GateStatus,
    GateTrace,
    Intent,
    OrderInfo,
    ProposedAction,
    SopRulesModel,
    Understanding,
)
from app.store import calls_repo

logger = logging.getLogger("guardrail.conversation")

MAX_NEED_INFO_TURNS = 2
MAX_CONFIRM_FIRST_PER_CALL = 2
MAX_INCONCLUSIVE_ANSWERS = 2
LISTEN_STATES = {"LISTENING", "CONFIRMING", "OFFERING_ALT", "NEED_INFO"}
_ORDER_ID_RE = re.compile(r"\bORD[\s-]?(\d{5})\b", re.IGNORECASE)

_ACTION_TITLE = {
    "refund": "Refund", "replacement": "Replacement", "cancellation": "Cancellation",
    "address_change": "Address change",
}


@dataclass
class PendingTurn:
    """The original customer turn a confirmation/offer answer re-gates (§7.4: re-gates reuse
    the original turn and skip Gate 1)."""

    transcript_used: str
    understanding: Understanding
    first_proposed_action: str | None


class CallSession:
    def __init__(
        self,
        call_id: str,
        customer: CustomerProfile,
        order: OrderInfo | None,
        sop: SopRulesModel,
        refunds_in_window: int,
        runtime: Runtime,
        transport: Transport | None = None,
        caller_masked: str | None = None,
        profile_known: bool = True,
    ) -> None:
        self.call_id = call_id
        self.customer = customer
        self.order = order
        self.sop = sop
        self.refunds_in_window = refunds_in_window
        self.rt = runtime
        self.claude = runtime.claude
        self.transport: Transport = transport or NullTransport()
        self.caller_masked = caller_masked
        self.profile_known = profile_known

        self.state = "GREETING"
        self.ended = False
        self.started_monotonic = time.monotonic()
        self.lock = asyncio.Lock()
        self.fault_injected_this_call = False
        self.need_info_streak = 0
        self.confirm_first_count = 0
        self.inconclusive_streak = 0
        self.silence_prompts = 0
        self.complaint_streak = 0
        self.negation_flagged = False
        self.pending: PendingTurn | None = None
        self.last_decision: Decision | None = None
        self.last_trace: GateTrace | None = None
        self.undo_decision_id: str | None = None
        self.ticket_id: str | None = None
        self.ticket_url: str | None = None
        self.turns: list[dict] = []
        self._turn_idx = 0
        self._turn_started = 0.0
        self._writes: list[asyncio.Task] = []
        self._timer: asyncio.Task | None = None
        self._max_timer: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    @property
    def channel(self) -> str:
        return self.transport.channel

    async def start(self) -> None:
        await calls_repo.run(calls_repo.create_call, self.call_id, self.channel,
                             self.customer.id if self.profile_known else None, self.caller_masked)
        self.rt.sessions[self.call_id] = self
        bus.publish("call.started", self.call_id, profile=self.customer.id.removeprefix("cust_") if self.profile_known else "unknown",
                    channel=self.channel, caller_masked=self.caller_masked)
        self._set_state("GREETING")
        if self.channel == "phone":
            self._max_timer = asyncio.create_task(self._max_call_guard())
        else:
            self._set_state("LISTENING")
            self._arm_timer()

    async def play_greeting(self) -> None:
        """Phone only: speak the cached greeting, then start listening."""
        async with self.lock:
            await self._say(templates.GREETING)
            if self.state == "GREETING":
                self._set_state("LISTENING")
            self._arm_timer()

    async def process_turn(self, text_asr: str) -> dict:
        """One customer turn, end to end: decide, speak, and close if terminal."""
        async with self.lock:
            if self.ended or self.state in ("CLOSING", "ENDED"):
                return {"reply": "", "state": self.state, "decision": None}
            self._cancel_timer()
            self.silence_prompts = 0
            self._turn_started = time.monotonic()
            bus.publish("transcript.final", self.call_id, text=text_asr)
            result = await self.handle_turn(text_asr)
            if result["reply"]:
                await self._say(result["reply"])
            if self.state == "CLOSING":
                await self._close_locked()
            else:
                self._arm_timer()
            result["state"] = self.state
            return result

    async def announce_and_close(self, line: str | None) -> None:
        """Refund finalized/undone from outside a turn (timer expiry, dashboard Undo)."""
        async with self.lock:
            if self.ended:
                return
            if line:
                self._save_agent_turn(line)
                await self._say(line)
            await self._close_locked()

    async def hangup_received(self) -> None:
        """The caller hung up (WS close / Vobiz hangup / sim end). Open undo windows keep running."""
        await self.end()

    async def _close_locked(self, *, goodbye: bool = True) -> None:
        self._set_state("CLOSING")
        if goodbye:
            self._save_agent_turn(templates.GOODBYE)
            await self._say(templates.GOODBYE)
        try:
            await self.transport.hangup()
        except Exception as exc:  # noqa: BLE001
            logger.warning("call %s: hangup failed: %s", self.call_id, exc)
        await self.end()

    async def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        self._cancel_timer()
        if self._max_timer and self._max_timer is not asyncio.current_task():
            self._max_timer.cancel()
        if self.ticket_id is None:
            extra = ["no-response"] if self.silence_prompts >= 2 else ["call-dropped"]
            await self._ensure_ticket(None, extra_tags=extra)
        if self._writes:
            await asyncio.gather(*self._writes, return_exceptions=True)
        duration = round(time.monotonic() - self.started_monotonic, 1)
        await calls_repo.run(calls_repo.update_call, self.call_id, ended_at=calls_repo.now_utc(), state="ENDED")
        self.state = "ENDED"
        bus.publish("state", self.call_id, state="ENDED")
        bus.publish("call.ended", self.call_id, duration_s=duration)
        self.rt.sessions.pop(self.call_id, None)
        self.rt.spawn(self.rt.maybe_complete(self.call_id))

    # ------------------------------------------------------------------ timers

    def _cancel_timer(self) -> None:
        if self._timer and self._timer is not asyncio.current_task():
            self._timer.cancel()
        self._timer = None

    def _arm_timer(self) -> None:
        self._cancel_timer()
        if self.ended:
            return
        if self.channel == "phone":
            if self.state in LISTEN_STATES:
                self._timer = asyncio.create_task(self._silence_guard())
        else:
            # Typed calls idle for MAX_CALL_SECONDS are ended so they still complete + archive.
            self._timer = asyncio.create_task(self._sim_idle_guard())

    async def _silence_guard(self) -> None:
        await asyncio.sleep(self.rt.settings.SILENCE_TIMEOUT_SECONDS)
        async with self.lock:
            if self.ended or self.state not in LISTEN_STATES:
                return
            self.silence_prompts += 1
            if self.silence_prompts == 1:
                self._save_agent_turn(templates.STILL_THERE)
                await self._say(templates.STILL_THERE)
                self._timer = asyncio.create_task(self._silence_guard())
            else:
                await self._close_locked()

    async def _sim_idle_guard(self) -> None:
        await asyncio.sleep(self.rt.settings.MAX_CALL_SECONDS)
        if self.state == "UNDO_WINDOW":
            return  # the refund result will close it
        async with self.lock:
            await self._close_locked(goodbye=False)

    async def _max_call_guard(self) -> None:
        await asyncio.sleep(self.rt.settings.MAX_CALL_SECONDS)
        async with self.lock:
            if self.ended or self.state == "UNDO_WINDOW":
                return
            ticket = await self._ensure_ticket(self.last_decision, extra_tags=["max-length"])
            line = templates.human_handoff(ticket)
            self._save_agent_turn(line)
            await self._say(line)
            await self._close_locked()

    # ------------------------------------------------------------------ speaking / events

    def _set_state(self, state: str) -> None:
        self.state = state
        bus.publish("state", self.call_id, state=state)

    async def _say(self, text: str) -> None:
        bus.publish("agent.reply", self.call_id, text=text)
        if self.channel != "phone":
            return
        bus.publish("agent.audio", self.call_id, speaking=True)
        try:
            await self.transport.say(text)
        except Exception as exc:  # noqa: BLE001 — a TTS hiccup must not kill the call
            logger.warning("call %s: say failed: %s", self.call_id, exc)
        finally:
            bus.publish("agent.audio", self.call_id, speaking=False)

    def _next_idx(self) -> int:
        idx = self._turn_idx
        self._turn_idx += 1
        return idx

    def _save_customer_turn(self, text_asr: str, text_used: str, fault: bool, understanding: Understanding | None = None) -> None:
        self.turns.append({"speaker": "customer", "text_asr": text_asr, "text_used": text_used, "fault_injected": fault})
        self._writes.append(self.rt.spawn(calls_repo.run(
            calls_repo.add_turn, self.call_id, self._next_idx(), "customer", text_asr=text_asr, text_used=text_used,
            fault_injected=fault, english=understanding.english_translation if understanding else None,
            understanding=understanding.model_dump(mode="json") if understanding else None,
        )))

    def _save_agent_turn(self, text: str) -> None:
        self.turns.append({"speaker": "agent", "text": text})
        self._writes.append(self.rt.spawn(calls_repo.run(calls_repo.add_turn, self.call_id, self._next_idx(), "agent",
                                                         text_asr=None, text_used=text)))

    # ------------------------------------------------------------------ tickets

    def _order_summary(self) -> str:
        if self.order is None:
            return "no order on file for this customer"
        return f"{self.order.id}, {self.order.item}, ₹{self.order.amount_inr:.0f}, status {self.order.status}"

    def _base_tags(self, decision: Decision | None) -> list[str]:
        tags = ["guardrail"]
        if decision is not None:
            tags.append(decision.kind.value.lower().replace("_", "-"))
            if decision.action_type:
                tags.append(decision.action_type.value.replace("_", "-"))
            if decision.kind == DecisionKind.EXECUTE_WITH_HOLD:
                tags.append("high-value-hold")
        else:
            tags.append("human-handoff")
        tags.append(self.customer.language_code)
        if self.negation_flagged:
            tags.append("negation-flag")
        if self.fault_injected_this_call:
            tags.append("simulated-asr-error")
        return tags

    def _ticket_html(self, decision: Decision | None) -> str:
        e = html.escape
        rows = ""
        if self.last_trace:
            rows = "".join(f"<tr><td>{s.step}</td><td>{e(s.gate)}</td><td>{s.status.value}</td><td>{e(s.detail)}</td></tr>"
                           for s in self.last_trace.steps)
        transcript = "".join(
            f"<li><b>Customer:</b> {e(t['text_used'])}"
            + (f" <i>(simulated ASR error; real transcript: {e(t['text_asr'])})</i>" if t.get("fault_injected") else "")
            + "</li>" if t["speaker"] == "customer" else f"<li><b>Agent:</b> {e(t['text'])}</li>"
            for t in self.turns
        )
        dec = (f"<p><b>Decision:</b> {decision.kind.value} {e(decision.action_type.value if decision.action_type else '')}"
               f" — {e(decision.reason)}</p>") if decision else "<p><b>Decision:</b> handed to a human</p>"
        return (
            f"<p><b>Customer:</b> {e(self.customer.name)} ({self.customer.language_code}) · channel {self.channel}</p>"
            f"<p><b>Order:</b> {e(self._order_summary())}</p>{dec}"
            f"<p><b>GuardRail gate trace</b></p><table border='1'><tr><th>#</th><th>Gate</th><th>Status</th><th>Detail</th></tr>{rows}</table>"
            f"<p><b>Transcript so far</b></p><ul>{transcript}</ul><p><i>Call id: {e(self.call_id)}</i></p>"
        )

    async def _ensure_ticket(self, decision: Decision | None, extra_tags: list[str] | None = None) -> str:
        """One ticket per call, created from a template (no LLM) at the first terminal decision.
        Returns the ticket number to speak."""
        if self.ticket_id is not None:
            return self.ticket_id
        tags = self._base_tags(decision) + (extra_tags or [])
        action = _ACTION_TITLE.get(decision.action_type.value, "Support") if decision and decision.action_type else "Support"
        item = f"{self.order.item.capitalize()} ({self.order.id})" if self.order else "no order on file"
        req = TicketRequest(
            subject=f"[GuardRail] {action} — {item}",
            description_html=self._ticket_html(decision),
            requester_name=self.customer.name, requester_phone=self.customer.phone,  # seeded profile, never the caller
            tags=tags,
            custom_fields={"cf_guardrail_decision": decision.kind.value} if decision else None,
        )
        ref = await self.rt.helpdesk.create_ticket(req)
        if ref is None:
            self.ticket_id = ""  # tried once; never create a second
            return "being sent to you by SMS"
        self.ticket_id, self.ticket_url = ref.ticket_id, ref.url
        self.rt.ticket_tags[ref.ticket_id] = tags
        await calls_repo.run(calls_repo.set_ticket_on_call_decisions, self.call_id, ref.ticket_id, ref.url)
        bus.publish("ticket.created", self.call_id, decision_id=getattr(self, "_last_dec_id", None),
                    ticket_id=ref.ticket_id, url=ref.url, tags=tags)
        return ref.ticket_id

    async def _handoff(self, decision: Decision | None, extra_tags: list[str] | None = None) -> dict:
        ticket = await self._ensure_ticket(decision, extra_tags)
        self._set_state("CLOSING")
        return self._result(reply=templates.human_handoff(ticket), decision=decision)

    # ------------------------------------------------------------------ turn handling

    async def handle_turn(self, text_asr: str) -> dict:
        if self.state == "CONFIRMING":
            return await self._handle_confirming_answer(text_asr)
        if self.state == "OFFERING_ALT":
            return await self._handle_offer_answer(text_asr)
        if self.state == "UNDO_WINDOW":
            return await self._handle_undo_window(text_asr)
        return await self._handle_fresh_turn(text_asr)

    async def _handle_undo_window(self, text_asr: str) -> dict:
        # Undo commands are never fault-injected, and only short explicit words count.
        self._save_customer_turn(text_asr, text_asr, False)
        if self.undo_decision_id and is_cancel_word(text_asr):
            if await self.rt.undo.undo(self.undo_decision_id, notify_session=False):
                self._set_state("CLOSING")
                return self._result(reply=templates.REFUND_UNDONE, decision=None)
        return {"reply": "", "state": self.state, "decision": None}

    async def _try_bind_order(self, text: str, understanding: Understanding | None) -> None:
        """Auto profile with no match: look the order up from what the caller says (§6.1)."""
        if self.order is not None:
            return
        candidate = None
        m = _ORDER_ID_RE.search(text)
        if m:
            candidate = f"ORD-{m.group(1)}"
        elif understanding and understanding.entities.order_id:
            m = _ORDER_ID_RE.search(understanding.entities.order_id) or re.search(r"(\d{5})", understanding.entities.order_id)
            if m:
                candidate = f"ORD-{m.group(1)}"
        if not candidate:
            return
        from app.store import queries
        from app.store.db import get_session

        def _load():
            order_row = calls_repo.find_order(candidate)
            if order_row is None:
                return None
            with get_session() as s:
                cust = queries.load_customer_profile(s, order_row.customer_id)
                order = queries.load_customer_order(s, order_row.customer_id)
                refunds = queries.count_refunds_in_window(s, order_row.customer_id, self.sop)
            return cust, order, refunds

        loaded = await calls_repo.run(_load)
        if loaded:
            self.customer, self.order, self.refunds_in_window = loaded
            self.profile_known = True
            await calls_repo.run(calls_repo.update_call, self.call_id, profile_id=self.customer.id)
            logger.info("call %s: bound to %s via order id", self.call_id, self.customer.id)

    async def _handle_fresh_turn(self, text_asr: str) -> dict:
        self._set_state("THINKING")
        self.inconclusive_streak = 0

        if self.fault_injected_this_call:
            text_used, flipped = text_asr, False
        else:
            text_used, flipped = fault_injection.maybe_flip_negation(text_asr)
            if flipped:
                self.fault_injected_this_call = True
                bus.publish("fault.injected", self.call_id, original=text_asr, flipped=text_used)

        await self._try_bind_order(text_used, None)
        try:
            understanding = await self.claude.understand_turn(text_used, self._order_summary())
        except ClaudeUnavailable:
            self._save_customer_turn(text_asr, text_used, flipped)
            self._save_agent_turn(templates.CLAUDE_FAILED)
            await self._say(templates.CLAUDE_FAILED)
            return await self._handoff(None, extra_tags=["claude-unavailable"])

        self._save_customer_turn(text_asr, text_used, flipped, understanding)
        bus.publish("understanding", self.call_id, **understanding.model_dump(mode="json"))
        await self._try_bind_order(text_used, understanding)

        first = INTENT_TO_ACTION.get(understanding.intent)
        return await self._route_understanding(text_used, understanding, is_regate=False,
                                               first_proposed=first.value if first else None)

    async def _route_understanding(
        self,
        transcript_used: str,
        understanding: Understanding,
        *,
        is_regate: bool,
        first_proposed: str | None,
        confirmed_by_customer: bool = False,
        alternative_accepted: bool = False,
        alternative_declined: bool = False,
        forced_action: ActionType | None = None,
    ) -> dict:
        intent = understanding.intent
        # Claude sometimes labels a contradictory turn "unclear"/"other" while still naming the
        # actions. Any explicitly named action means the gates decide (Gate 1 catches the conflict).
        names_action = any(normalize_action_word(a) for a in understanding.actions_mentioned)
        if names_action and intent in (Intent.unclear, Intent.other, Intent.complaint):
            is_ungated = False
        else:
            is_ungated = not is_regate

        if intent == Intent.order_status and not is_regate:
            self._set_state("LISTENING")
            self.need_info_streak = 0
            return self._result(reply=understanding.reply_to_customer or "Let me check that for you.", decision=None)

        if intent in (Intent.complaint, Intent.other) and is_ungated:
            # A problem report with no action named: ask once what they want, then hand off.
            self.complaint_streak += 1
            if intent == Intent.complaint and self.order is not None and self.complaint_streak < 2:
                self._set_state("LISTENING")
                return self._result(reply=templates.COMPLAINT_ASK, decision=None)
            return await self._handoff(None, extra_tags=[intent.value])

        if intent == Intent.unclear and is_ungated:
            self.need_info_streak += 1
            if self.need_info_streak >= MAX_NEED_INFO_TURNS:
                return await self._handoff(None, extra_tags=["unclear"])
            self._set_state("NEED_INFO")
            return self._result(reply=understanding.reply_to_customer or "Sorry, could you say that again?", decision=None)

        if forced_action is not None:
            amount = self.order.amount_inr if forced_action == ActionType.refund and self.order else 0.0
            proposed = ProposedAction(type=forced_action, cash_amount_inr=amount, order_id=self.order.id if self.order else None)
        else:
            proposed, candidates, _negated = pick_pending_action(understanding, self.order)
            if first_proposed is None and candidates:
                first_proposed = candidates[0].value

        ctx = GateContext(
            call_id=self.call_id, customer=self.customer, order=self.order,
            refunds_in_window=self.refunds_in_window, sop=self.sop,
            transcript_used=transcript_used, understanding=understanding,
            proposed_action=proposed, actions_mentioned=understanding.actions_mentioned,
            confirmed_by_customer=confirmed_by_customer, alternative_accepted=alternative_accepted,
            alternative_declined=alternative_declined, is_regate=is_regate,
        )
        result = run_gates(ctx)
        decision = result.decision
        self.last_decision = decision
        self.last_trace = result.trace
        if any(s.gate == "negation" and s.status == GateStatus.WARN for s in result.trace.steps):
            self.negation_flagged = True

        latency = int((time.monotonic() - self._turn_started) * 1000) if self._turn_started else None
        trace = [s.model_dump(mode="json") for s in result.trace.steps]
        dec_id = await calls_repo.run(
            calls_repo.add_decision, self.call_id, kind=decision.kind.value,
            action_type=decision.action_type.value if decision.action_type else None,
            amount_inr=decision.amount_inr, reason=decision.reason, trace=trace,
            first_proposed_action=first_proposed, latency_ms=latency,
        )
        self._last_dec_id = dec_id
        for step in trace:
            bus.publish("gate", self.call_id, **step)
        bus.publish("decision", self.call_id, decision_id=dec_id, kind=decision.kind.value,
                    action_type=decision.action_type.value if decision.action_type else None,
                    amount_inr=decision.amount_inr, reason=decision.reason)

        return await self._apply_decision(transcript_used, understanding, decision, dec_id, first_proposed)

    async def _apply_decision(self, transcript_used: str, understanding: Understanding, decision: Decision,
                              dec_id: str, first_proposed: str | None) -> dict:
        if decision.kind == DecisionKind.CONFIRM_FIRST:
            self.confirm_first_count += 1
            if self.confirm_first_count >= MAX_CONFIRM_FIRST_PER_CALL:
                return await self._handoff(decision, extra_tags=["unresolved-confirmation"])
            self._set_state("CONFIRMING")
            self.pending = PendingTurn(transcript_used, understanding, first_proposed)
            return self._result(reply=templates.confirm_first_prompt(decision), decision=decision, dec_id=dec_id)

        if decision.kind == DecisionKind.NEED_INFO:
            self.need_info_streak += 1
            if self.need_info_streak > MAX_NEED_INFO_TURNS:
                return await self._handoff(decision)
            self._set_state("NEED_INFO")
            return self._result(reply="Sorry, could you tell me your order ID?", decision=decision, dec_id=dec_id)

        self.need_info_streak = 0

        if decision.kind == DecisionKind.OFFER_ALTERNATIVE:
            self._set_state("OFFERING_ALT")
            self.pending = PendingTurn(transcript_used, understanding, first_proposed)
            return self._result(reply=templates.OFFER_ALTERNATIVE_LINE, decision=decision, dec_id=dec_id)

        if decision.kind == DecisionKind.HUMAN_HANDOFF:
            r = await self._handoff(decision)
            r["decision"] = self._decision_dict(decision, dec_id)
            return r

        # EXECUTE / EXECUTE_WITH_HOLD
        self._set_state("EXECUTING")
        is_refund = decision.action_type == ActionType.refund
        opens_window = is_refund and (decision.kind == DecisionKind.EXECUTE_WITH_HOLD or self.sop.undo_window_enabled)
        ticket = await self._ensure_ticket(decision, extra_tags=["pending-finalize"] if opens_window else None)
        if self.ticket_id:
            await calls_repo.run(calls_repo.update_decision, dec_id, ticket_id=self.ticket_id, ticket_url=self.ticket_url)

        if is_refund:
            seconds = self.rt.settings.UNDO_WINDOW_SECONDS
            self.undo_decision_id = dec_id
            if opens_window:
                await self.rt.undo.open_window(self.call_id, dec_id, seconds)
                self._set_state("UNDO_WINDOW")
                return self._result(reply=templates.refund_window_open(decision.amount_inr, seconds),
                                    decision=decision, dec_id=dec_id)
            # Undo window switched off in the SOP: refund immediately.
            await calls_repo.run(calls_repo.update_decision, dec_id, status="pending_finalize")
            line = await self.rt.undo.finalize(self.call_id, dec_id) or templates.REFUND_FAILED
            self._set_state("CLOSING")
            return self._result(reply=line, decision=decision, dec_id=dec_id)

        self._set_state("CLOSING")
        item = self.order.item if self.order else "your order"
        return self._result(reply=templates.execute_line(decision, item, ticket), decision=decision, dec_id=dec_id)

    async def _handle_confirming_answer(self, text_asr: str) -> dict:
        assert self.pending is not None
        self._save_customer_turn(text_asr, text_asr, False)  # answers are never fault-injected
        kind, named_action = parse_confirmation_answer(text_asr)
        pending = self.pending

        if kind == "named":
            self.inconclusive_streak = 0
            return await self._route_understanding(
                pending.transcript_used, pending.understanding, is_regate=True,
                first_proposed=pending.first_proposed_action, confirmed_by_customer=True, forced_action=named_action,
            )

        if kind == "yes":
            decision = self.last_decision
            if decision and len(decision.candidate_actions) >= 2:
                # Choice form: plain yes doesn't pick one. Re-ask once; not a new CONFIRM_FIRST.
                self.inconclusive_streak += 1
                if self.inconclusive_streak >= MAX_INCONCLUSIVE_ANSWERS:
                    return await self._handoff(decision, extra_tags=["unresolved-confirmation"])
                return self._result(reply=templates.confirm_first_prompt(decision), decision=decision)
            self.inconclusive_streak = 0
            action = decision.candidate_actions[0] if decision and decision.candidate_actions else (decision.action_type if decision else None)
            return await self._route_understanding(
                pending.transcript_used, pending.understanding, is_regate=True,
                first_proposed=pending.first_proposed_action, confirmed_by_customer=True, forced_action=action,
            )

        if kind == "no":
            self.inconclusive_streak = 0
            self._set_state("LISTENING")
            self.pending = None
            return self._result(reply="Okay, what would you like me to do?", decision=None)

        self.inconclusive_streak += 1
        if self.inconclusive_streak >= MAX_INCONCLUSIVE_ANSWERS:
            return await self._handoff(self.last_decision, extra_tags=["unresolved-confirmation"])
        decision = self.last_decision
        reply = templates.confirm_first_prompt(decision) if decision else "Sorry, I didn't catch that."
        return self._result(reply=reply, decision=decision)

    async def _handle_offer_answer(self, text_asr: str) -> dict:
        assert self.pending is not None
        self._save_customer_turn(text_asr, text_asr, False)
        accepted = parse_offer_answer(text_asr) == "accepted"
        return await self._route_understanding(
            self.pending.transcript_used, self.pending.understanding, is_regate=True,
            first_proposed=self.pending.first_proposed_action,
            alternative_accepted=accepted, alternative_declined=not accepted,
            forced_action=ActionType.replacement if accepted else ActionType.refund,
        )

    def _result(self, *, reply: str, decision: Decision | None, dec_id: str | None = None) -> dict:
        if reply:
            self._save_agent_turn(reply)
        return {
            "reply": reply,
            "state": self.state,
            "decision": self._decision_dict(decision, dec_id) if decision else None,
        }

    @staticmethod
    def _decision_dict(decision: Decision, dec_id: str | None = None) -> dict:
        return {
            "decision_id": dec_id,
            "kind": decision.kind.value,
            "action_type": decision.action_type.value if decision.action_type else None,
            "amount_inr": decision.amount_inr,
            "reason": decision.reason,
        }
