"""Gate 1 — negation & contradiction (§7.4). Pure, deterministic, no I/O.

Lexicons are exactly as specified. The lone Hindi "na" is deliberately excluded — it's mostly
a tag word. This module never sees Claude's `actions_mentioned`; the conflicting-actions signal
comes only from the action-word lexicon below, matched against the (possibly fault-injected)
transcript text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import ActionType, Understanding

# term (lowercased) -> language code. A few Latin spellings are shared across languages
# (e.g. Tamil/Malayalam "venda"); the language tag is informational only and doesn't affect
# scoring, so each string appears once.
DONT_WANT_LEXICON: dict[str, str] = {
    "vendam": "ta", "venda": "ta", "vendaam": "ta",
    "வேண்டாம்": "ta", "வேண்டா": "ta",
    "mat": "hi", "nahi": "hi", "nahin": "hi",
    "मत": "hi", "नहीं": "hi",
    "beda": "kn", "bedi": "kn", "ಬೇಡ": "kn",
    "vaddu": "te", "వద్దు": "te",
    # Malayalam "venda" (Latin) is spelled identically to Tamil's "venda" above — one entry
    # covers both; only its native script needs a separate key.
    "വേണ്ട": "ml",
}

WANT_LEXICON: dict[str, str] = {
    "venum": "ta", "venam": "ta", "vendum": "ta",
    "வேணும்": "ta", "வேண்டும்": "ta",
    "chahiye": "hi", "karo": "hi",
    "चाहिए": "hi", "करो": "hi",
    "beku": "kn", "ಬೇಕು": "kn",
    "kavali": "te", "కావాలి": "te",
    "വേണം": "ml",
}

# §7.4: "A want term whose don't-want pair differs by one syllable (venum/vendam, beku/beda)".
# Taken literally as these two exact pairs — see ARCHITECTURE.md §15 for the open decision.
WANT_BONUS_TERMS: frozenset[str] = frozenset({"venum", "வேணும்", "beku", "ಬೇಕು"})

ACTION_LEXICON: dict[ActionType, list[str]] = {
    ActionType.refund: [
        "refund", "money back", "paisa wapas", "paise wapas",
        "ரீஃபண்ட்", "रिफंड", "ರೀಫಂಡ್",
    ],
    ActionType.replacement: [
        "replacement", "replace", "exchange", "badal", "new one",
        "ரீப்ளேஸ்மென்ட்", "रिप्लेसमेंट", "एक्सचेंज",
    ],
    ActionType.cancellation: ["cancel", "cancellation", "कैंसल"],
    ActionType.address_change: ["address", "pata", "एड्रेस"],
}

_TOKEN_RE = re.compile(r"[^\s,.!?;:।]+")

NEAR_TOKEN_WINDOW = 4


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _find_phrase_positions(tokens: list[str], phrase: str) -> list[int]:
    phrase_tokens = tokenize(phrase)
    n = len(phrase_tokens)
    if n == 0 or n > len(tokens):
        return []
    return [i for i in range(len(tokens) - n + 1) if tokens[i : i + n] == phrase_tokens]


def find_action_positions(tokens: list[str]) -> dict[ActionType, list[int]]:
    result: dict[ActionType, list[int]] = {}
    for action_type, phrases in ACTION_LEXICON.items():
        positions: list[int] = []
        for phrase in phrases:
            positions.extend(_find_phrase_positions(tokens, phrase))
        if positions:
            result[action_type] = sorted(positions)
    return result


def find_dont_want_hits(tokens: list[str]) -> list[tuple[int, str, str]]:
    return [(i, tok, DONT_WANT_LEXICON[tok]) for i, tok in enumerate(tokens) if tok in DONT_WANT_LEXICON]


def find_want_hits(tokens: list[str]) -> list[tuple[int, str, str]]:
    return [(i, tok, WANT_LEXICON[tok]) for i, tok in enumerate(tokens) if tok in WANT_LEXICON]


@dataclass
class NegationScore:
    score: float
    detail: str
    dont_want_hits: list[tuple[int, str, str]] = field(default_factory=list)
    action_positions: dict[ActionType, list[int]] = field(default_factory=dict)


def score_negation(transcript: str, understanding: Understanding) -> NegationScore:
    tokens = tokenize(transcript)
    action_positions = find_action_positions(tokens)
    dont_want_hits = find_dont_want_hits(tokens)
    want_hits = find_want_hits(tokens)

    score = 0.0
    details: list[str] = []

    dont_want_near_action = any(
        abs(idx - p) <= NEAR_TOKEN_WINDOW
        for idx, _term, _lang in dont_want_hits
        for positions in action_positions.values()
        for p in positions
    )
    if dont_want_near_action:
        score += 0.4
        details.append("don't-want term near an action word")

    if len(action_positions) >= 2:
        score += 0.4
        names = "+".join(a.value for a in action_positions)
        details.append(f"conflicting actions: {names}")

    if understanding.negation_analysis.contradiction:
        score += 0.3
        details.append("Claude flagged a contradiction")

    if understanding.negation_analysis.intent_confidence < 0.75:
        score += 0.2
        details.append("low intent confidence")

    if any(term in WANT_BONUS_TERMS for _, term, _ in want_hits):
        score += 0.1
        details.append("want-term with a near-homophone don't-want pair")

    score = min(score, 1.0)
    detail = "; ".join(details) if details else "no negation signals"
    return NegationScore(score=score, detail=detail, dont_want_hits=dont_want_hits, action_positions=action_positions)
