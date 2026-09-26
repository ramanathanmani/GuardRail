"""Demo-only simulated ASR negation flip (§7.6). Applied at most once per call, only to the
first customer turn containing a don't-want term. Never touches CONFIRMING / OFFERING_ALT
answers or undo commands — callers are responsible for only invoking this on fresh turns.
"""
from __future__ import annotations

import re

from app.config import get_settings
from app.guardrail.negation import DONT_WANT_LEXICON, tokenize

# Explicit pairs from §7.6; a few more added for full lexicon coverage using the same
# want/don't-want pairing already used for scoring (venda/vendaam pair with vendam's fix).
FLIP_MAP: dict[str, str | None] = {
    "vendam": "venum", "venda": "venum", "vendaam": "venum",
    "வேண்டாம்": "வேணும்", "வேண்டா": "வேணும்",
    "mat": None, "मत": None,
    "nahi": None, "nahin": None, "नहीं": None,
    "beda": "beku", "bedi": "beku", "ಬೇಡ": "ಬೇಕು",
    "vaddu": "kavali", "వద్దు": "కావాలి",
    "വേണ്ട": "വേണം",
}

_NAHI_CHAHIYE_RE = re.compile(r"\bnahi\s+chahiye\b", re.IGNORECASE)

_mode: str | None = None  # lazily initialised from settings; mutable for the dashboard toggle


def current_mode() -> str:
    global _mode
    if _mode is None:
        _mode = get_settings().DEMO_FAULT_INJECTION
    return _mode


def set_mode(mode: str) -> None:
    global _mode
    _mode = mode


def _collapse_spaces(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text.strip()


def _replace_first_token(text: str, token: str, replacement: str | None) -> str:
    match = re.search(re.escape(token), text, re.IGNORECASE)
    if not match:
        return text
    new_text = text[: match.start()] + (replacement or "") + text[match.end() :]
    return _collapse_spaces(new_text)


def maybe_flip_negation(text: str) -> tuple[str, bool]:
    """Returns (text_used, was_flipped). Only ever changes one term: the 'nahi chahiye' phrase
    if present, else the first lone don't-want token, swapped for its want-pair (or dropped)."""
    if current_mode() != "flip_negation":
        return text, False

    match = _NAHI_CHAHIYE_RE.search(text)
    if match:
        flipped = text[: match.start()] + "chahiye" + text[match.end() :]
        return _collapse_spaces(flipped), True

    tokens = tokenize(text)
    target_token = next((t for t in tokens if t in DONT_WANT_LEXICON), None)
    if target_token is None:
        return text, False

    return _replace_first_token(text, target_token, FLIP_MAP.get(target_token)), True
