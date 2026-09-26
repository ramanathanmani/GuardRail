"""Intent → action mapping and pending-action selection (§7.2)."""
from __future__ import annotations

from app.models import ActionType, Intent, NegationTerm, OrderInfo, ProposedAction, Understanding

INTENT_TO_ACTION: dict[Intent, ActionType] = {
    Intent.refund: ActionType.refund,
    Intent.replacement: ActionType.replacement,
    Intent.cancel_order: ActionType.cancellation,
    Intent.address_change: ActionType.address_change,
}

# order_status / complaint / other / unclear never reach the engine (§7.2 "Gates? no") — the
# conversation layer routes them straight to their own responses.
GATED_INTENTS = frozenset(INTENT_TO_ACTION)

_ACTION_WORD_ALIASES: dict[str, ActionType] = {
    "refund": ActionType.refund,
    "replacement": ActionType.replacement,
    "replace": ActionType.replacement,
    "exchange": ActionType.replacement,
    "cancel": ActionType.cancellation,
    "cancellation": ActionType.cancellation,
    "cancel_order": ActionType.cancellation,
    "address": ActionType.address_change,
    "address_change": ActionType.address_change,
}


def normalize_action_word(word: str) -> ActionType | None:
    return _ACTION_WORD_ALIASES.get(word.strip().lower().replace(" ", "_"))


def _negated_action_types(negation_terms: list[NegationTerm]) -> list[ActionType]:
    negated: list[ActionType] = []
    for term in negation_terms:
        if term.governs:
            action = normalize_action_word(term.governs)
            if action and action not in negated:
                negated.append(action)
    return negated


def compute_candidates(understanding: Understanding) -> tuple[list[ActionType], ActionType | None]:
    """Returns (candidates remaining after dropping negated ones, the negated action if any)."""
    mentioned: list[ActionType] = []
    for word in understanding.actions_mentioned:
        action = normalize_action_word(word)
        if action and action not in mentioned:
            mentioned.append(action)

    if not mentioned and understanding.intent in INTENT_TO_ACTION:
        mentioned = [INTENT_TO_ACTION[understanding.intent]]

    negated_types = _negated_action_types(understanding.negation_analysis.negation_terms)
    negated_action = negated_types[0] if negated_types else None
    candidates = [a for a in mentioned if a not in negated_types]

    # "If two or more actions remain, or all were negated, Gate 1 decides" — if filtering
    # dropped everything, fall back to the full mentioned list so there's still something to
    # offer as a choice; Gate 1's conflicting-actions signal already covers this transcript.
    if not candidates:
        candidates = mentioned

    return candidates, negated_action


def pick_pending_action(
    understanding: Understanding, order: OrderInfo | None
) -> tuple[ProposedAction, list[ActionType], ActionType | None]:
    """For a fresh turn (not a re-gate). `proposed_action.type` is None when 0 or 2+ candidates
    remain — Gate 1 will resolve it via CONFIRM_FIRST."""
    candidates, negated_action = compute_candidates(understanding)

    action_type = candidates[0] if len(candidates) == 1 else None
    amount = 0.0
    if action_type == ActionType.refund and order is not None:
        amount = order.amount_inr

    proposed = ProposedAction(type=action_type, cash_amount_inr=amount, order_id=order.id if order else None)
    return proposed, candidates, negated_action
