"""Read helpers the conversation layer uses to build a GateContext from seeded/live data."""
from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from app.models import CustomerProfile, OrderInfo, SopRulesModel
from app.store.db import Customer, Order, RefundHistory, SopRules


def load_customer_profile(session: Session, customer_id: str) -> CustomerProfile | None:
    row = session.get(Customer, customer_id)
    if row is None:
        return None
    return CustomerProfile(
        id=row.id, name=row.name, phone=row.phone, language_code=row.language_code,
        account_age_days=row.account_age_days, avg_order_inr=row.avg_order_inr,
    )


def load_customer_order(session: Session, customer_id: str) -> OrderInfo | None:
    row = session.exec(select(Order).where(Order.customer_id == customer_id)).first()
    if row is None:
        return None
    return OrderInfo(
        id=row.id, item=row.item, category=row.category, amount_inr=row.amount_inr,
        status=row.status, delivered_on=row.delivered_on,
    )


def load_sop(session: Session) -> SopRulesModel:
    row = session.get(SopRules, 1)
    if row is None:
        raise RuntimeError("sop_rules row missing — run `python -m app.store.seed`")
    return SopRulesModel(
        return_window_days=row.return_window_days, max_refunds=row.max_refunds,
        refund_window_days=row.refund_window_days, excluded_categories=row.excluded_categories,
        high_value_hold_inr=row.high_value_hold_inr,
        require_confirm_on_negation=row.require_confirm_on_negation,
        undo_window_enabled=row.undo_window_enabled,
    )


def count_refunds_in_window(session: Session, customer_id: str, sop: SopRulesModel) -> int:
    cutoff = date.today() - timedelta(days=sop.refund_window_days)
    rows = session.exec(
        select(RefundHistory).where(
            RefundHistory.customer_id == customer_id, RefundHistory.refunded_on >= cutoff
        )
    ).all()
    return len(rows)
