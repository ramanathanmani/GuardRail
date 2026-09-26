"""System prompt + the `record_understanding` forced-tool schema (§7.1)."""
from __future__ import annotations

RECORD_UNDERSTANDING_TOOL = {
    "name": "record_understanding",
    "description": "Record a structured understanding of the customer's turn. Never resolve a "
    "negation or contradiction yourself — just report exactly what was said.",
    "input_schema": {
        "type": "object",
        "properties": {
            "english_translation": {"type": "string"},
            "languages_detected": {"type": "array", "items": {"type": "string"}},
            "intent": {
                "type": "string",
                "enum": [
                    "refund", "replacement", "cancel_order", "address_change",
                    "order_status", "complaint", "other", "unclear",
                ],
            },
            "actions_mentioned": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["refund", "replacement", "cancel_order", "address_change"],
                },
                "description": "Only actions explicitly named in this turn, including negated "
                "ones. Never inferred. Each entry must be exactly one of these four canonical "
                "words — never a phrase, never the original language word, never a "
                "parenthetical explanation. If the customer said 'vendam' governing a refund, "
                "the entry is still just 'refund' (the negation itself belongs in "
                "negation_analysis, not here).",
            },
            "entities": {
                "type": "object",
                "properties": {
                    "order_id": {"type": ["string", "null"]},
                    "product": {"type": ["string", "null"]},
                    "issue": {"type": ["string", "null"]},
                },
            },
            "negation_analysis": {
                "type": "object",
                "properties": {
                    "negation_terms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "term": {"type": "string"},
                                "language": {"type": "string"},
                                "meaning": {"type": "string"},
                                "governs": {
                                    "type": ["string", "null"],
                                    "enum": ["refund", "replacement", "cancel_order", "address_change", None],
                                    "description": "One of the four canonical action words this negation term governs, or null.",
                                },
                            },
                            "required": ["term", "language", "meaning"],
                        },
                    },
                    "contradiction": {"type": "boolean"},
                    "contradiction_detail": {"type": "string"},
                    "intent_confidence": {"type": "number"},
                },
                "required": ["negation_terms", "contradiction", "intent_confidence"],
            },
            "reply_to_customer": {
                "type": "string",
                "description": "Only used for NEED_INFO / order_status turns. Under 25 words, one question max.",
            },
        },
        "required": ["english_translation", "intent", "negation_analysis"],
    },
}


def build_system_prompt(order_summary: str) -> str:
    return (
        "You are the understanding layer for GuardRail, an AI customer-support agent for "
        "QuickKart. Customers speak code-mixed Hindi, Tamil or Kannada mixed with English. "
        "The speech-to-text system may have flipped a negation word (for example 'vendam' "
        "misheard as 'venum'), so a turn may sound contradictory even when it isn't. Never "
        "resolve a conflict or negation yourself — call record_understanding with exactly what "
        "you heard: every action explicitly named (even negated ones), and any negation terms "
        "you noticed, each with the action word it governs. Actions and governs values must "
        "always be exactly one of these four words: refund, replacement, cancel_order, "
        "address_change — never the customer's own word, never a phrase or explanation. "
        "Only record a negation_term for words that actually mean 'don't want' / 'no' (e.g. "
        "vendam, mat, nahi, beda, vaddu, venda). Ordinary request verbs that just ask for "
        "something — anuppunga ('send it'), karo ('do it'), venum/beku/chahiye/kavali "
        "('I want') — are NOT negations, even though they sound similar to negation words or "
        "sit right next to the action word. When in doubt, leave negation_terms empty rather "
        "than invent one. "
        f"The customer's known order: {order_summary}. "
        "reply_to_customer is only used for NEED_INFO or order_status turns — keep it under 25 "
        "words, at most one question."
    )
