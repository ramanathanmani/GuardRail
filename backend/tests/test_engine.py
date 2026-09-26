"""Every §7.5 row, driven by tests/fixtures/understandings.json. Pure engine tests — no
network, no Claude, no DB."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.guardrail.engine import run_gates
from app.models import (
    ActionType,
    CustomerProfile,
    DecisionKind,
    GateContext,
    OrderInfo,
    ProposedAction,
    SopRulesModel,
    Understanding,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "understandings.json").read_text())


def understanding(row: str) -> Understanding:
    return Understanding.model_validate(FIXTURES[row])


def default_sop(**overrides) -> SopRulesModel:
    base = dict(
        return_window_days=30, max_refunds=3, refund_window_days=15,
        excluded_categories=["innerwear"], high_value_hold_inr=5000,
        require_confirm_on_negation=True, undo_window_enabled=True,
    )
    base.update(overrides)
    return SopRulesModel(**base)


RIYA = CustomerProfile(id="cust_riya", name="Riya", phone="+919000000001", language_code="ta-IN", account_age_days=340, avg_order_inr=2000)
RIYA_ORDER = OrderInfo(id="ORD-88213", item="mixer grinder", category="kitchen", amount_inr=2499, status="delivered", delivered_on=date.today() - timedelta(days=3))

ARJUN = CustomerProfile(id="cust_arjun", name="Arjun", phone="+919000000002", language_code="hi-IN", account_age_days=400, avg_order_inr=600)
ARJUN_ORDER = OrderInfo(id="ORD-77102", item="T-shirt", category="apparel", amount_inr=399, status="delivered", delivered_on=date.today() - timedelta(days=5))

MEERA = CustomerProfile(id="cust_meera", name="Meera", phone="+919000000003", language_code="kn-IN", account_age_days=700, avg_order_inr=4500)
MEERA_ORDER = OrderInfo(id="ORD-66540", item="sneakers", category="footwear", amount_inr=8500, status="delivered", delivered_on=date.today() - timedelta(days=6))

KARTHIK = CustomerProfile(id="cust_karthik", name="Karthik", phone="+919000000004", language_code="hi-IN", account_age_days=200, avg_order_inr=1500)
KARTHIK_ORDER = OrderInfo(id="ORD-55219", item="headphones", category="electronics", amount_inr=1999, status="processing", delivered_on=None)

NEW_CUSTOMER = CustomerProfile(id="cust_x1", name="X1", phone="+910000000000", language_code="en-IN", account_age_days=3, avg_order_inr=1000)
X1_ORDER = OrderInfo(id="ORD-SYNTH", item="gadget", category="electronics", amount_inr=8500, status="delivered", delivered_on=date.today() - timedelta(days=1))


def make_ctx(**kwargs) -> GateContext:
    defaults = dict(
        call_id="test", refunds_in_window=0, sop=default_sop(), actions_mentioned=[],
        confirmed_by_customer=False, alternative_accepted=False, alternative_declined=False,
        is_regate=False,
    )
    defaults.update(kwargs)
    return GateContext(**defaults)


def test_r1_conflicting_actions_warn_even_without_claude_contradiction():
    u = understanding("R1")
    proposed = ProposedAction(type=None, cash_amount_inr=0, order_id=RIYA_ORDER.id)
    ctx = make_ctx(
        customer=RIYA, order=RIYA_ORDER, transcript_used="Jar udanjiruku. Refund venum, replacement anuppunga.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.CONFIRM_FIRST
    assert set(result.decision.candidate_actions) == {ActionType.refund, ActionType.replacement}


def test_r2_confirmed_replacement_executes():
    u = understanding("R1")
    proposed = ProposedAction(type=ActionType.replacement, cash_amount_inr=0, order_id=RIYA_ORDER.id)
    ctx = make_ctx(
        customer=RIYA, order=RIYA_ORDER, transcript_used="Jar udanjiruku. Refund venum, replacement anuppunga.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
        confirmed_by_customer=True, is_regate=True,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.EXECUTE
    assert result.decision.action_type == ActionType.replacement


def test_r3_negated_refund_reads_back_replacement():
    u = understanding("R3")
    proposed = ProposedAction(type=None, cash_amount_inr=0, order_id=RIYA_ORDER.id)
    ctx = make_ctx(
        customer=RIYA, order=RIYA_ORDER, transcript_used="Jar udanjiruku. Refund vendam, replacement anuppunga.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.CONFIRM_FIRST
    assert result.decision.candidate_actions == [ActionType.replacement]
    assert result.decision.negated_action == ActionType.refund


def test_a1_refund_limit_offers_alternative():
    u = understanding("A1")
    proposed = ProposedAction(type=ActionType.refund, cash_amount_inr=ARJUN_ORDER.amount_inr, order_id=ARJUN_ORDER.id)
    ctx = make_ctx(
        customer=ARJUN, order=ARJUN_ORDER, transcript_used="Mujhe refund chahiye, size galat hai.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
        refunds_in_window=4,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.OFFER_ALTERNATIVE


def test_a2_alternative_accepted_executes_replacement():
    u = understanding("A1")
    proposed = ProposedAction(type=ActionType.replacement, cash_amount_inr=0, order_id=ARJUN_ORDER.id)
    ctx = make_ctx(
        customer=ARJUN, order=ARJUN_ORDER, transcript_used="Mujhe refund chahiye, size galat hai.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
        refunds_in_window=4, alternative_accepted=True, is_regate=True,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.EXECUTE
    assert result.decision.action_type == ActionType.replacement


def test_a3_alternative_declined_handoff():
    u = understanding("A1")
    proposed = ProposedAction(type=ActionType.refund, cash_amount_inr=ARJUN_ORDER.amount_inr, order_id=ARJUN_ORDER.id)
    ctx = make_ctx(
        customer=ARJUN, order=ARJUN_ORDER, transcript_used="Mujhe refund chahiye, size galat hai.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
        refunds_in_window=4, alternative_declined=True, is_regate=True,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.HUMAN_HANDOFF


def test_a4_raised_refund_limit_executes_with_undo_window():
    u = understanding("A1")
    proposed = ProposedAction(type=ActionType.refund, cash_amount_inr=ARJUN_ORDER.amount_inr, order_id=ARJUN_ORDER.id)
    ctx = make_ctx(
        customer=ARJUN, order=ARJUN_ORDER, transcript_used="Mujhe refund chahiye, size galat hai.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
        refunds_in_window=4, sop=default_sop(max_refunds=5),
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.EXECUTE
    assert result.decision.action_type == ActionType.refund


def test_m1_high_value_hold():
    u = understanding("M1")
    proposed = ProposedAction(type=ActionType.refund, cash_amount_inr=MEERA_ORDER.amount_inr, order_id=MEERA_ORDER.id)
    ctx = make_ctx(
        customer=MEERA, order=MEERA_ORDER, transcript_used="Sole kithu hogide, refund beku.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.EXECUTE_WITH_HOLD
    assert result.decision.amount_inr == 8500


def test_k1_negated_cancellation_reads_back_address_change():
    u = understanding("K1")
    proposed = ProposedAction(type=None, cash_amount_inr=0, order_id=KARTHIK_ORDER.id)
    ctx = make_ctx(
        customer=KARTHIK, order=KARTHIK_ORDER, transcript_used="Order cancel mat karo, bas address change karo.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.CONFIRM_FIRST
    assert result.decision.candidate_actions == [ActionType.address_change]
    assert result.decision.negated_action == ActionType.cancellation


def test_k2_confirmed_address_change_executes():
    u = understanding("K1")
    proposed = ProposedAction(type=ActionType.address_change, cash_amount_inr=0, order_id=KARTHIK_ORDER.id)
    ctx = make_ctx(
        customer=KARTHIK, order=KARTHIK_ORDER, transcript_used="Order cancel mat karo, bas address change karo.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
        confirmed_by_customer=True, is_regate=True,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.EXECUTE
    assert result.decision.action_type == ActionType.address_change


def test_x1_high_risk_never_moves_cash():
    u = understanding("X1")
    proposed = ProposedAction(type=ActionType.refund, cash_amount_inr=X1_ORDER.amount_inr, order_id=X1_ORDER.id)
    ctx = make_ctx(
        customer=NEW_CUSTOMER, order=X1_ORDER, transcript_used="I want a refund for my order.",
        understanding=u, proposed_action=proposed, actions_mentioned=u.actions_mentioned,
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.HUMAN_HANDOFF
    assert result.decision.amount_inr == 8500  # proposed, but never executed


@pytest.mark.parametrize("account_age_days,amount", [(2, 999999), (0, 5000.01), (6, 100000)])
def test_high_risk_never_moves_cash_generalized(account_age_days, amount):
    """No matter how large the amount, a brand-new account never gets cash moved automatically."""
    customer = CustomerProfile(id="c", name="c", phone="+91", language_code="en-IN", account_age_days=account_age_days, avg_order_inr=1000)
    order = OrderInfo(id="o", item="item", category="misc", amount_inr=amount, status="delivered", delivered_on=date.today())
    u = understanding("X1")
    proposed = ProposedAction(type=ActionType.refund, cash_amount_inr=amount, order_id="o")
    ctx = make_ctx(
        customer=customer, order=order, transcript_used="refund please", understanding=u,
        proposed_action=proposed, actions_mentioned=["refund"],
    )
    result = run_gates(ctx)
    assert result.decision.kind == DecisionKind.HUMAN_HANDOFF
