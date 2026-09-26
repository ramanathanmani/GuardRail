"""Gate 3 — risk assessment (§7.4). Pure, deterministic, no I/O."""
from __future__ import annotations

from app.models import GateContext, RiskLevel


def assess_risk(ctx: GateContext) -> RiskLevel:
    sop = ctx.sop
    customer = ctx.customer
    amount = ctx.proposed_action.cash_amount_inr

    if ctx.refunds_in_window > sop.max_refunds:
        return RiskLevel.high
    if customer.account_age_days < 7 and amount > sop.high_value_hold_inr:
        return RiskLevel.high

    if ctx.refunds_in_window >= 2:
        return RiskLevel.medium
    if amount > 2 * customer.avg_order_inr:
        return RiskLevel.medium

    return RiskLevel.low
