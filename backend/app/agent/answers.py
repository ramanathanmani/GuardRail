"""Yes/no/action-naming/cancel-word parsing for CONFIRMING, OFFERING_ALT, UNDO_WINDOW
(§6.2, §7.8). Checked before falling back to Claude."""
from __future__ import annotations

from app.guardrail.negation import find_action_positions, tokenize
from app.models import ActionType

YES_WORDS = {"yes", "haan", "aama", "sari", "howdu", "correct"}
NO_WORDS = {"no", "nahi", "illa", "beda"}
# Short, explicit only — "nahi" / "mat karo" must NOT stop a refund; people say those in passing.
CANCEL_WORDS = {"cancel", "stop", "vendam", "beda", "ruko"}


def find_named_action(text: str) -> ActionType | None:
    """If the answer explicitly names exactly one action ("replacement", "aama, replacement"),
    return it. Ambiguous (names 2+) or names none -> None, so the caller falls back to yes/no."""
    positions = find_action_positions(tokenize(text))
    if len(positions) == 1:
        return next(iter(positions))
    return None


def is_yes(text: str) -> bool:
    return bool(set(tokenize(text)) & YES_WORDS)


def is_no(text: str) -> bool:
    return bool(set(tokenize(text)) & NO_WORDS)


def is_cancel_word(text: str) -> bool:
    return bool(set(tokenize(text)) & CANCEL_WORDS)


def parse_confirmation_answer(text: str) -> tuple[str, ActionType | None]:
    """Returns (kind, action): kind is 'named' | 'yes' | 'no' | 'inconclusive'."""
    named = find_named_action(text)
    if named is not None:
        return "named", named
    if is_yes(text):
        return "yes", None
    if is_no(text):
        return "no", None
    return "inconclusive", None


def parse_offer_answer(text: str) -> str:
    """'accepted' on yes, else 'declined' — anything that isn't yes counts (§6.2)."""
    return "accepted" if is_yes(text) else "declined"
