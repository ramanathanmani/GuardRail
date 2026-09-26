"""Gate 2 — SOP checks (§7.4). Pure, deterministic, no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models import ActionType, GateContext


@dataclass
class SopResult:
    passed: bool
    detail: str
    alternative_allowed: bool = False


def _return_window_ok(ctx: GateContext) -> bool:
    order = ctx.order
    if order is None or order.delivered_on is None:
        return True
    days_since = (date.today() - order.delivered_on).days
    return days_since <= ctx.sop.return_window_days


def _excluded_category(ctx: GateContext) -> bool:
    order = ctx.order
    return order is not None and order.category in ctx.sop.excluded_categories


def _refund_limit_ok(ctx: GateContext) -> bool:
    return (ctx.refunds_in_window + 1) <= ctx.sop.max_refunds


def _replacement_would_pass(ctx: GateContext) -> bool:
    """Alternative-allowed (§7.4): a replacement would clear the return-window and
    excluded-category checks (the refund limit doesn't apply to replacements)."""
    return _return_window_ok(ctx) and not _excluded_category(ctx)


def check_sop(ctx: GateContext) -> SopResult:
    action = ctx.proposed_action.type

    if action == ActionType.refund:
        if not _return_window_ok(ctx):
            return SopResult(False, "return window exceeded", alternative_allowed=_replacement_would_pass(ctx))
        if _excluded_category(ctx):
            return SopResult(False, "excluded category", alternative_allowed=_replacement_would_pass(ctx))
        if not _refund_limit_ok(ctx):
            return SopResult(False, "refund limit exceeded", alternative_allowed=_replacement_would_pass(ctx))
        return SopResult(True, "refund SOP checks passed")

    if action == ActionType.replacement:
        if not _return_window_ok(ctx):
            return SopResult(False, "return window exceeded")
        if _excluded_category(ctx):
            return SopResult(False, "excluded category")
        return SopResult(True, "replacement SOP checks passed")

    if action == ActionType.cancellation:
        order = ctx.order
        if order is None or order.status != "processing":
            return SopResult(False, "order already shipped, cannot cancel")
        return SopResult(True, "cancellation SOP checks passed")

    if action == ActionType.address_change:
        return SopResult(True, "no SOP checks for address change")

    return SopResult(True, "no action to check")
