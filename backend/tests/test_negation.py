"""Gate 1 lexicons and signal detection (§7.4)."""
from __future__ import annotations

from app.guardrail.negation import DONT_WANT_LEXICON, WANT_LEXICON, find_action_positions, score_negation, tokenize
from app.models import ActionType, NegationAnalysis, Understanding


def _understanding(intent="refund", contradiction=False, confidence=0.9) -> Understanding:
    return Understanding(
        english_translation="x", intent=intent, actions_mentioned=[],
        negation_analysis=NegationAnalysis(contradiction=contradiction, intent_confidence=confidence),
    )


def test_dont_want_latin_and_native_script_both_recognized():
    assert "vendam" in DONT_WANT_LEXICON
    assert "வேண்டாம்" in DONT_WANT_LEXICON
    assert "mat" in DONT_WANT_LEXICON
    assert "मत" in DONT_WANT_LEXICON
    assert "beda" in DONT_WANT_LEXICON
    assert "ಬೇಡ" in DONT_WANT_LEXICON


def test_want_latin_and_native_script_both_recognized():
    assert "venum" in WANT_LEXICON
    assert "வேணும்" in WANT_LEXICON
    assert "chahiye" in WANT_LEXICON
    assert "beku" in WANT_LEXICON
    assert "ಬேಕু" not in WANT_LEXICON  # sanity: a garbled string must never match


def test_lone_hindi_na_excluded():
    assert "na" not in DONT_WANT_LEXICON
    assert "na" not in WANT_LEXICON


def test_dont_want_near_action_word_scores_04():
    result = score_negation("Refund vendam, replacement anuppunga.", _understanding())
    assert result.score >= 0.4


def test_conflicting_actions_detected_via_lexicon_not_claude():
    tokens = tokenize("Refund venum, replacement anuppunga.")
    positions = find_action_positions(tokens)
    assert ActionType.refund in positions
    assert ActionType.replacement in positions
    result = score_negation("Refund venum, replacement anuppunga.", _understanding())
    assert result.score >= 0.4  # conflicting-actions signal fires even with no don't-want term


def test_want_word_alone_never_reaches_threshold():
    result = score_negation("Mujhe refund chahiye, size galat hai.", _understanding())
    assert result.score < 0.4


def test_claude_contradiction_flag_adds_03():
    result = score_negation("refund please", _understanding(contradiction=True))
    assert result.score >= 0.3


def test_low_confidence_adds_02():
    result = score_negation("refund please", _understanding(confidence=0.5))
    assert result.score >= 0.2
