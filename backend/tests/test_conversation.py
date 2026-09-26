"""Typed-turn flows through the real CallSession with fixture understandings (§6.2, §7.5)."""
from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session, select

from app.agent.session_factory import build_session
from app.guardrail import fault_injection
from app.store.db import Decision, Payment, SopRules

pytestmark = pytest.mark.asyncio


async def start(rt, profile):
    s = await build_session(rt, f"sim_{profile}", profile)
    await s.start()
    return s


async def test_r1_choice_then_named_answer_executes_replacement(rt):
    fault_injection.set_mode("flip_negation")
    s = await start(rt, "riya")
    r = await s.process_turn("Jar udanjiruku. Refund vendam, replacement anuppunga.")
    assert r["decision"]["kind"] == "CONFIRM_FIRST"
    assert "refund or a replacement" in r["reply"]
    assert s.turns[0]["fault_injected"] and s.turns[0]["text_used"].lower().count("venum") == 1
    r = await s.process_turn("aama, replacement")
    assert r["decision"]["kind"] == "EXECUTE" and r["decision"]["action_type"] == "replacement"
    assert s.ended
    assert len(rt.helpdesk.created) == 1
    assert "negation-flag" in rt.helpdesk.created[0].tags
    assert rt.helpdesk.created[0].requester_name == "Riya"


async def test_fault_injection_once_and_never_on_answers(rt):
    fault_injection.set_mode("flip_negation")
    s = await start(rt, "riya")
    await s.process_turn("Jar udanjiruku. Refund vendam, replacement anuppunga.")
    await s.process_turn("vendam, replacement")  # answer containing a don't-want term: never flipped
    assert [t.get("fault_injected") for t in s.turns if t["speaker"] == "customer"] == [True, False]


async def test_r3_readback_plain_yes(rt):
    s = await start(rt, "riya")
    r = await s.process_turn("Jar udanjiruku. Refund vendam, replacement anuppunga.")
    assert "a replacement, not a refund" in r["reply"]
    r = await s.process_turn("aama")
    assert r["decision"]["kind"] == "EXECUTE" and r["decision"]["action_type"] == "replacement"


async def test_choice_plain_yes_reasks_then_handoff(rt):
    fault_injection.set_mode("flip_negation")
    s = await start(rt, "riya")
    await s.process_turn("Jar udanjiruku. Refund vendam, replacement anuppunga.")
    r = await s.process_turn("haan")
    assert "refund or a replacement" in r["reply"] and s.state == "CONFIRMING"
    r = await s.process_turn("haan")
    assert "specialist" in r["reply"] and s.ended


async def test_a1_offer_accept_and_decline(rt):
    s = await start(rt, "arjun")
    r = await s.process_turn("Mujhe refund chahiye, size galat hai.")
    assert r["decision"]["kind"] == "OFFER_ALTERNATIVE"
    r = await s.process_turn("haan")
    assert r["decision"]["kind"] == "EXECUTE" and r["decision"]["action_type"] == "replacement"

    s2 = await build_session(rt, "sim_arjun2", "arjun")
    await s2.start()
    await s2.process_turn("Mujhe refund chahiye, size galat hai.")
    r = await s2.process_turn("nahi")
    assert r["decision"]["kind"] == "HUMAN_HANDOFF"


async def test_k1_readback_then_yes(rt):
    s = await start(rt, "karthik")
    r = await s.process_turn("Order cancel mat karo, bas address change karo.")
    assert "an address change, not a cancellation" in r["reply"]
    r = await s.process_turn("haan")
    assert r["decision"]["action_type"] == "address_change"


async def test_m1_hold_then_voice_undo_never_refunds(rt, db):
    with Session(db) as ses:
        ses.add(Payment(payment_id="pay_1", order_id="ORD-66540", amount_inr=8500))
        ses.commit()
    s = await start(rt, "meera")
    r = await s.process_turn("Sole kithu hogide, refund beku.")
    assert r["decision"]["kind"] == "EXECUTE_WITH_HOLD" and s.state == "UNDO_WINDOW"
    r = await s.process_turn("nahi")  # not an undo word
    assert r["reply"] == "" and s.state == "UNDO_WINDOW"
    r = await s.process_turn("cancel")
    assert "stopped that refund" in r["reply"]
    await asyncio.sleep(1.3)
    assert rt.payments.refunds == []
    with Session(db) as ses:
        assert ses.exec(select(Decision).where(Decision.kind == "EXECUTE_WITH_HOLD", Decision.is_seed == False)).one().status == "undone"  # noqa: E712


async def test_a4_after_sop_edit_refund_executes_at_expiry(rt, db):
    with Session(db) as ses:
        sop = ses.get(SopRules, 1)
        sop.max_refunds = 5
        ses.add(sop)
        ses.add(Payment(payment_id="pay_a", order_id="ORD-77102", amount_inr=399))
        ses.commit()
    s = await start(rt, "arjun")
    r = await s.process_turn("Mujhe refund chahiye, size galat hai.")
    assert r["decision"]["kind"] == "EXECUTE" and r["decision"]["action_type"] == "refund"
    assert rt.payments.refunds == []  # nothing before expiry
    await asyncio.sleep(1.4)
    assert rt.payments.refunds == ["pay_a"]
    assert s.ended
    assert len(rt.helpdesk.created) == 1
    assert rt.helpdesk.tag_updates[-1][1].count("finalized") == 1


async def test_unclear_intent_that_names_actions_still_goes_through_gates(rt):
    from tests.conftest import FIXTURES
    from app.models import Understanding

    async def unclear(text, summary):
        return Understanding.model_validate({**FIXTURES["R1"], "intent": "unclear"})

    rt.claude.understand_turn = unclear
    s = await start(rt, "riya")
    r = await s.process_turn("Jar udanjiruku. Refund venum, replacement anuppunga.")
    assert r["decision"]["kind"] == "CONFIRM_FIRST"


async def test_complaint_without_action_asks_once_then_hands_off(rt):
    from tests.conftest import FIXTURES
    from app.models import Understanding

    async def complaint(text, summary):
        return Understanding.model_validate({**FIXTURES["A1"], "intent": "complaint", "actions_mentioned": []})

    rt.claude.understand_turn = complaint
    s = await start(rt, "riya")
    r = await s.process_turn("I ordered it and it came broken.")
    assert "replacement or a refund" in r["reply"] and not s.ended
    r = await s.process_turn("it is just broken")
    assert "specialist" in r["reply"] and s.ended


async def test_stats_count_live_sop_handoff_as_prevented(rt):
    from app.api.dashboard import _compute_stats

    before = _compute_stats()
    s = await start(rt, "arjun")
    await s.process_turn("Mujhe refund chahiye, size galat hai.")
    await s.process_turn("nahi")  # declines the exchange → SOP-driven HUMAN_HANDOFF
    after = _compute_stats()
    assert after["wrong_actions_prevented"] == before["wrong_actions_prevented"] + 1
    assert after["money_protected_inr"] == before["money_protected_inr"] + 399
