"""Every spoken line (§7.7). Chosen *after* the decision — never from Claude's free text,
except `reply_to_customer` for NEED_INFO / order_status (§6.2)."""
from __future__ import annotations

from app.models import ActionType, Decision

GREETING = "Hi, you've reached QuickKart support. I'm an AI assistant. How can I help you today?"
OFFER_ALTERNATIVE_LINE = "I can't process another refund on this order, but I can send an exchange instead. Would that work?"
STILL_THERE = "Are you still there?"
CLAUDE_FAILED = "Sorry, one moment please."
GOODBYE = "Thank you for calling QuickKart. Goodbye!"
REFUND_ISSUED = "Your refund has been processed."
REFUND_UNDONE = "Okay, I've stopped that refund. Nothing has been refunded."
REFUND_FAILED = "I couldn't complete the refund, so a specialist will call you back."

_BARE_NAME: dict[ActionType, str] = {
    ActionType.refund: "refund",
    ActionType.replacement: "replacement",
    ActionType.cancellation: "cancellation",
    ActionType.address_change: "address change",
}

_ARTICLE_PHRASE: dict[ActionType, str] = {
    ActionType.refund: "a refund",
    ActionType.replacement: "a replacement",
    ActionType.cancellation: "a cancellation",
    ActionType.address_change: "an address change",
}


def confirm_first_choice(candidates: list[ActionType]) -> str:
    a, b = _BARE_NAME[candidates[0]], _BARE_NAME[candidates[1]]
    return f"Sorry, I want to get this right. Do you want a {a} or a {b}?"


def confirm_first_readback(action: ActionType, negated: ActionType | None) -> str:
    if negated is not None:
        return f"Just to confirm: you want {_ARTICLE_PHRASE[action]}, not {_ARTICLE_PHRASE[negated]}. Is that right?"
    return f"Just to confirm: you want {_ARTICLE_PHRASE[action]}. Is that right?"


def confirm_first_prompt(decision: Decision) -> str:
    """Choice form for 2+ surviving candidates, else read-back form (§6.2)."""
    if len(decision.candidate_actions) >= 2:
        return confirm_first_choice(decision.candidate_actions)
    action = decision.candidate_actions[0] if decision.candidate_actions else decision.action_type
    return confirm_first_readback(action, decision.negated_action)


def human_handoff(ticket: str) -> str:
    return f"I'm passing this to a specialist who will call you back shortly. Your ticket number is {ticket}."


def execute_line(decision: Decision, item: str, ticket: str) -> str:
    if decision.action_type == ActionType.replacement:
        return f"Done. A replacement for your {item} is on its way. Your ticket number is {ticket}."
    if decision.action_type == ActionType.address_change:
        return "Done. I've noted the address change for your order. Our team will confirm it by SMS."
    if decision.action_type == ActionType.cancellation:
        return f"Done. Your order for the {item} is cancelled. Ticket {ticket}."
    raise ValueError(f"execute_line has no non-refund template for {decision.action_type}")


def refund_window_open(amount_inr: float, seconds: int) -> str:
    return f"Your refund of {amount_inr:.0f} rupees will go through in {seconds} seconds. Say 'cancel' if you want to stop it."
