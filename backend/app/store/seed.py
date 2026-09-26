"""Relative-date demo data (§4.2). Dates are always computed from *today*, so the demo
never drifts. Run as `python -m app.store.seed --reset` before rehearsals and before the demo.

seed --reset never touches the `payments` table (§5.6) — only store/dodo_setup.py does.
"""
from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from app.config import get_settings
from app.logging_utils import mask_phone
from app.store.db import Call, Customer, Decision, Order, RefundHistory, SopRules, Turn, get_session, init_db

_RESET_SEED = 1234  # fixed, so historical filler is reproducible across resets


PROFILES = {
    "riya": dict(
        id="cust_riya", name="Riya", phone="+919000000001", language_code="ta-IN",
        account_age_days=340, avg_order_inr=2000,
    ),
    "arjun": dict(
        id="cust_arjun", name="Arjun", phone="+919000000002", language_code="hi-IN",
        account_age_days=400, avg_order_inr=600,
    ),
    "meera": dict(
        id="cust_meera", name="Meera", phone="+919000000003", language_code="kn-IN",
        account_age_days=700, avg_order_inr=4500,
    ),
    "karthik": dict(
        id="cust_karthik", name="Karthik", phone="+919000000004", language_code="hi-IN",
        account_age_days=200, avg_order_inr=1500,
    ),
}

# (profile_key, days_ago, kind, action_type, amount_inr, first_proposed_action)
HISTORICAL_ROWS = [
    ("riya", 13, "EXECUTE", "replacement", 0, "replacement"),
    ("arjun", 13, "EXECUTE", "replacement", 0, "refund"),
    ("meera", 12, "EXECUTE_WITH_HOLD", "refund", 8500, "refund"),
    ("karthik", 12, "EXECUTE", "address_change", 0, "address_change"),
    ("riya", 11, "EXECUTE", "replacement", 0, "replacement"),
    ("arjun", 11, "HUMAN_HANDOFF", None, 399, "refund"),
    ("meera", 10, "EXECUTE", "replacement", 0, "replacement"),
    ("karthik", 10, "EXECUTE", "address_change", 0, "address_change"),
    ("riya", 9, "EXECUTE", "refund", 2499, "refund"),
    ("arjun", 9, "EXECUTE", "replacement", 0, "replacement"),
    ("meera", 8, "HUMAN_HANDOFF", None, 8500, "refund"),
    ("karthik", 8, "EXECUTE", "address_change", 0, "address_change"),
    ("riya", 7, "EXECUTE_WITH_HOLD", "refund", 6200, "refund"),
    ("arjun", 6, "EXECUTE", "replacement", 0, "refund"),
    ("meera", 5, "EXECUTE", "replacement", 0, "replacement"),
    ("karthik", 5, "HUMAN_HANDOFF", None, 0, "address_change"),
    ("riya", 4, "EXECUTE", "replacement", 0, "replacement"),
    ("arjun", 3, "EXECUTE", "refund", 399, "refund"),
    ("meera", 2, "EXECUTE_WITH_HOLD", "refund", 9200, "refund"),
    ("karthik", 1, "EXECUTE", "address_change", 0, "address_change"),
]


def _sample_trace(kind: str) -> list[dict]:
    sop_status = "fail" if kind in ("HUMAN_HANDOFF", "OFFER_ALTERNATIVE") else "pass"
    return [
        {"step": 1, "gate": "understanding", "status": "pass", "detail": "Transcript understood (sample history)"},
        {"step": 2, "gate": "negation", "status": "pass", "detail": "No conflicting actions"},
        {"step": 3, "gate": "sop", "status": sop_status, "detail": "SOP checked"},
        {"step": 4, "gate": "risk", "status": "pass", "detail": "Risk assessed"},
        {"step": 5, "gate": "decision", "status": "pass", "detail": f"Decision: {kind}"},
    ]


def _seed_customers_and_orders(session: Session, today: date) -> dict[str, Customer]:
    customers: dict[str, Customer] = {}
    for key, attrs in PROFILES.items():
        customer = Customer(is_seed=True, **attrs)
        session.add(customer)
        customers[key] = customer

    orders = [
        Order(id="ORD-88213", customer_id="cust_riya", item="mixer grinder", category="kitchen",
              amount_inr=2499, status="delivered", delivered_on=today - timedelta(days=3), is_seed=True),
        Order(id="ORD-77102", customer_id="cust_arjun", item="T-shirt", category="apparel",
              amount_inr=399, status="delivered", delivered_on=today - timedelta(days=5), is_seed=True),
        Order(id="ORD-66540", customer_id="cust_meera", item="sneakers", category="footwear",
              amount_inr=8500, status="delivered", delivered_on=today - timedelta(days=6), is_seed=True),
        Order(id="ORD-55219", customer_id="cust_karthik", item="headphones", category="electronics",
              amount_inr=1999, status="processing", delivered_on=None, is_seed=True),
    ]
    for order in orders:
        session.add(order)

    for i, days_ago in enumerate((1, 3, 6, 9), start=1):
        session.add(RefundHistory(
            id=f"rh_arjun_{i}", customer_id="cust_arjun", order_id="ORD-77102",
            refunded_on=today - timedelta(days=days_ago), is_seed=True,
        ))

    return customers


def _seed_sop_defaults(session: Session) -> None:
    session.add(SopRules(
        id=1, return_window_days=30, max_refunds=3, refund_window_days=15,
        excluded_categories=["innerwear"], high_value_hold_inr=5000,
        require_confirm_on_negation=True, undo_window_enabled=True,
    ))


def _seed_historical_calls(session: Session, now: datetime) -> None:
    rng = random.Random(_RESET_SEED)
    for i, (profile_key, days_ago, kind, action_type, amount, first_proposed) in enumerate(HISTORICAL_ROWS, start=1):
        profile = PROFILES[profile_key]
        call_id = f"call_seed_{i:02d}"
        dec_id = f"dec_seed_{i:02d}"
        started_at = now - timedelta(days=days_ago, hours=rng.randint(1, 20), minutes=rng.randint(0, 59))
        ended_at = started_at + timedelta(minutes=rng.randint(1, 4))
        channel = "phone" if i % 2 == 0 else "text"
        is_refund_execute = action_type == "refund" and kind in ("EXECUTE", "EXECUTE_WITH_HOLD")
        status = "finalized" if is_refund_execute else "open"

        session.add(Call(
            id=call_id, channel=channel, profile_id=profile["id"],
            caller_masked=mask_phone(profile["phone"]), started_at=started_at, ended_at=ended_at,
            state="ENDED", archived=True, is_seed=True,
        ))
        session.add(Turn(
            id=f"{call_id}_t1", call_id=call_id, idx=0, speaker="customer",
            text_asr="(sample history — no transcript stored)", text_used="(sample history — no transcript stored)",
            fault_injected=False, ts=started_at,
        ))
        session.add(Turn(
            id=f"{call_id}_t2", call_id=call_id, idx=1, speaker="agent",
            text_asr=None, text_used=None, fault_injected=False, ts=ended_at,
        ))
        session.add(Decision(
            id=dec_id, call_id=call_id, kind=kind, action_type=action_type, amount_inr=amount,
            reason=f"Sample history — {kind}", trace_json=_sample_trace(kind),
            first_proposed_action=first_proposed, status=status,
            refund_id=f"sample_refund_{i:02d}" if is_refund_execute else None,
            refund_status="succeeded" if is_refund_execute else None,
            refund_provider="simulated" if is_refund_execute else None,
            latency_ms=rng.randint(400, 1400), created_at=started_at, is_seed=True,
        ))


def _seed_core(session: Session) -> None:
    today = date.today()
    now = datetime.now(timezone.utc)
    _seed_customers_and_orders(session, today)
    _seed_sop_defaults(session)
    _seed_historical_calls(session, now)
    session.commit()


def reset_all(session: Session) -> None:
    """Wipe everything this module owns (never `payments`), then reseed fresh."""
    for model in (Turn, Decision, Call, RefundHistory, Order, Customer, SopRules):
        for row in session.exec(select(model)).all():
            session.delete(row)
    session.commit()
    _seed_core(session)


def seed_if_empty(session: Session) -> bool:
    if session.exec(select(Customer)).first() is not None:
        return False
    _seed_core(session)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="wipe and reseed with fresh relative dates")
    args = parser.parse_args()

    settings = get_settings()
    init_db(settings)
    with get_session(settings) as session:
        if args.reset:
            reset_all(session)
            print("Reset and reseeded demo data (customers, orders, SOP defaults, sample history).")
        elif seed_if_empty(session):
            print("Database was empty — seeded demo data.")
        else:
            print("Demo data already present; nothing to do (use --reset to refresh dates).")


if __name__ == "__main__":
    main()
