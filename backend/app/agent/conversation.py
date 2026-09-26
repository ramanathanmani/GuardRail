"""CallSession state machine (§6.2). Drives one call's turns through fault injection ->
understand_turn -> intents -> run_gates -> a spoken template, for both the phone path (later
phases) and typed-text sim calls (§8.2)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.agent import templates
from app.agent.answers import parse_confirmation_answer, parse_offer_answer
from app.agent.claude_client import ClaudeClient, ClaudeUnavailable
from app.agent.intents import pick_pending_action
from app.guardrail import fault_injection
from app.guardrail.engine import run_gates
from app.models import (
    ActionType,
    CustomerProfile,
    Decision,
    DecisionKind,
    GateContext,
    GateTrace,
    Intent,
    OrderInfo,
    ProposedAction,
    SopRulesModel,
    Understanding,
)

logger = logging.getLogger("guardrail.conversation")

MAX_NEED_INFO_TURNS = 2
MAX_CONFIRM_FIRST_PER_CALL = 2
MAX_INCONCLUSIVE_ANSWERS = 2
UNDO_WINDOW_SECONDS = 30


@dataclass
class PendingTurn:
    """The original customer turn a confirmation/offer answer re-gates (§7.4: re-gates reuse
    the original turn and skip Gate 1)."""

    transcript_used: str
    understanding: Understanding


class CallSession:
    def __init__(
        self,
        call_id: str,
        customer: CustomerProfile,
        order: OrderInfo | None,
        sop: SopRulesModel,
        refunds_in_window: int,
        claude: ClaudeClient,
    ) -> None:
        self.call_id = call_id
        self.customer = customer
        self.order = order
        self.sop = sop
        self.refunds_in_window = refunds_in_window
        self.claude = claude

        self.state = "GREETING"
        self.fault_injected_this_call = False
        self.need_info_streak = 0
        self.confirm_first_count = 0
        self.inconclusive_streak = 0
        self.pending: PendingTurn | None = None
        self.last_decision: Decision | None = None
        self.last_trace: GateTrace | None = None
        self.turns: list[dict] = []

    def _ticket_placeholder(self) -> str:
        # No Freshdesk yet (Phase 4) — a clearly-local stand-in, not a real ticket id.
        return f"TCK-{self.call_id[:8]}"

    def _order_summary(self) -> str:
        if self.order is None:
            return "no order on file for this customer"
        return f"{self.order.id}, {self.order.item}, ₹{self.order.amount_inr:.0f}, status {self.order.status}"

    async def handle_turn(self, text_asr: str) -> dict:
        if self.state == "CONFIRMING":
            return await self._handle_confirming_answer(text_asr)
        if self.state == "OFFERING_ALT":
            return await self._handle_offer_answer(text_asr)
        return await self._handle_fresh_turn(text_asr)

    async def _handle_fresh_turn(self, text_asr: str) -> dict:
        self.state = "THINKING"
        self.inconclusive_streak = 0

        if self.fault_injected_this_call:
            text_used, flipped = text_asr, False
        else:
            text_used, flipped = fault_injection.maybe_flip_negation(text_asr)
            if flipped:
                self.fault_injected_this_call = True

        self.turns.append({"speaker": "customer", "text_asr": text_asr, "text_used": text_used, "fault_injected": flipped})

        try:
            understanding = await self.claude.understand_turn(text_used, self._order_summary())
        except ClaudeUnavailable:
            self.state = "CLOSING"
            return self._result(reply=templates.CLAUDE_FAILED, decision=None, terminal=True)

        return await self._route_understanding(text_used, understanding, is_regate=False)

    async def _route_understanding(
        self,
        transcript_used: str,
        understanding: Understanding,
        *,
        is_regate: bool,
        confirmed_by_customer: bool = False,
        alternative_accepted: bool = False,
        alternative_declined: bool = False,
        forced_action: ActionType | None = None,
    ) -> dict:
        intent = understanding.intent

        if intent == Intent.order_status:
            self.state = "LISTENING"
            self.need_info_streak = 0
            return self._result(reply=understanding.reply_to_customer or "Let me check that for you.", decision=None)

        if intent in (Intent.complaint, Intent.other):
            self.state = "CLOSING"
            return self._result(reply=templates.human_handoff(self._ticket_placeholder()), decision=None, terminal=True)

        if intent == Intent.unclear:
            self.need_info_streak += 1
            if self.need_info_streak >= MAX_NEED_INFO_TURNS:
                self.state = "CLOSING"
                return self._result(reply=templates.human_handoff(self._ticket_placeholder()), decision=None, terminal=True)
            self.state = "NEED_INFO"
            return self._result(reply=understanding.reply_to_customer or "Sorry, could you say that again?", decision=None)

        self.need_info_streak = 0

        if forced_action is not None:
            amount = self.order.amount_inr if forced_action == ActionType.refund and self.order else 0.0
            proposed = ProposedAction(type=forced_action, cash_amount_inr=amount, order_id=self.order.id if self.order else None)
        else:
            proposed, _candidates, _negated = pick_pending_action(understanding, self.order)

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

        return self._apply_decision(transcript_used, understanding, decision)

    def _apply_decision(self, transcript_used: str, understanding: Understanding, decision: Decision) -> dict:
        if decision.kind == DecisionKind.CONFIRM_FIRST:
            self.confirm_first_count += 1
            if self.confirm_first_count > MAX_CONFIRM_FIRST_PER_CALL - 1:
                self.state = "CLOSING"
                return self._result(reply=templates.human_handoff(self._ticket_placeholder()), decision=decision, terminal=True)
            self.state = "CONFIRMING"
            self.pending = PendingTurn(transcript_used=transcript_used, understanding=understanding)
            return self._result(reply=templates.confirm_first_prompt(decision), decision=decision)

        if decision.kind == DecisionKind.NEED_INFO:
            self.state = "NEED_INFO"
            return self._result(reply="Sorry, could you tell me your order ID?", decision=decision)

        if decision.kind == DecisionKind.OFFER_ALTERNATIVE:
            self.state = "OFFERING_ALT"
            self.pending = PendingTurn(transcript_used=transcript_used, understanding=understanding)
            return self._result(reply=templates.OFFER_ALTERNATIVE_LINE, decision=decision)

        if decision.kind == DecisionKind.HUMAN_HANDOFF:
            self.state = "CLOSING"
            return self._result(reply=templates.human_handoff(self._ticket_placeholder()), decision=decision, terminal=True)

        if decision.kind == DecisionKind.EXECUTE_WITH_HOLD:
            self.state = "UNDO_WINDOW"  # Phase 7 wires the real timer + Dodo call
            return self._result(reply=templates.refund_window_open(decision.amount_inr, UNDO_WINDOW_SECONDS), decision=decision)

        if decision.kind == DecisionKind.EXECUTE:
            ticket = self._ticket_placeholder()
            if decision.action_type == ActionType.refund:
                self.state = "UNDO_WINDOW"
                reply = templates.refund_window_open(decision.amount_inr, UNDO_WINDOW_SECONDS)
            else:
                self.state = "CLOSING"
                item = self.order.item if self.order else "your order"
                reply = templates.execute_line(decision, item, ticket)
            return self._result(reply=reply, decision=decision)

        raise AssertionError(f"unhandled decision kind {decision.kind}")

    async def _handle_confirming_answer(self, text_asr: str) -> dict:
        assert self.pending is not None
        self.turns.append({"speaker": "customer", "text_asr": text_asr, "text_used": text_asr, "fault_injected": False})
        kind, named_action = parse_confirmation_answer(text_asr)

        if kind == "named":
            self.inconclusive_streak = 0
            return await self._route_understanding(
                self.pending.transcript_used, self.pending.understanding, is_regate=True,
                confirmed_by_customer=True, forced_action=named_action,
            )

        if kind == "yes":
            self.inconclusive_streak = 0
            decision = self.last_decision
            if decision and len(decision.candidate_actions) >= 2:
                # Choice form: plain yes doesn't pick one. Re-ask once; not a new CONFIRM_FIRST.
                return self._result(reply=templates.confirm_first_prompt(decision), decision=decision)
            action = decision.candidate_actions[0] if decision and decision.candidate_actions else (decision.action_type if decision else None)
            return await self._route_understanding(
                self.pending.transcript_used, self.pending.understanding, is_regate=True,
                confirmed_by_customer=True, forced_action=action,
            )

        if kind == "no":
            self.inconclusive_streak = 0
            self.state = "LISTENING"
            self.pending = None
            return self._result(reply="Okay, what would you like me to do?", decision=None)

        self.inconclusive_streak += 1
        if self.inconclusive_streak >= MAX_INCONCLUSIVE_ANSWERS:
            self.state = "CLOSING"
            return self._result(reply=templates.human_handoff(self._ticket_placeholder()), decision=self.last_decision, terminal=True)
        decision = self.last_decision
        reply = templates.confirm_first_prompt(decision) if decision else "Sorry, I didn't catch that."
        return self._result(reply=reply, decision=decision)

    async def _handle_offer_answer(self, text_asr: str) -> dict:
        assert self.pending is not None
        self.turns.append({"speaker": "customer", "text_asr": text_asr, "text_used": text_asr, "fault_injected": False})
        accepted = parse_offer_answer(text_asr) == "accepted"
        return await self._route_understanding(
            self.pending.transcript_used, self.pending.understanding, is_regate=True,
            alternative_accepted=accepted, alternative_declined=not accepted,
            forced_action=ActionType.replacement if accepted else ActionType.refund,
        )

    def _result(self, *, reply: str, decision: Decision | None, terminal: bool = False) -> dict:
        self.turns.append({"speaker": "agent", "text": reply})
        if terminal:
            self.state = "CLOSING"
        return {
            "reply": reply,
            "state": self.state,
            "decision": self._decision_dict(decision) if decision else None,
        }

    @staticmethod
    def _decision_dict(decision: Decision) -> dict:
        return {
            "kind": decision.kind.value,
            "action_type": decision.action_type.value if decision.action_type else None,
            "amount_inr": decision.amount_inr,
            "reason": decision.reason,
        }
