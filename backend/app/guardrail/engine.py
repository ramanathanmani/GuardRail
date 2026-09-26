"""GateContext in, GateTrace (exactly 5 steps) + Decision out. Pure — no network, no Claude,
no DB (§7.4). The decision matrix is an ordered list of rules; the first match wins, and every
step after the deciding rule is marked `skipped` rather than computed for show.
"""
from __future__ import annotations

from app.agent.intents import compute_candidates
from app.guardrail.negation import score_negation
from app.guardrail.risk import assess_risk
from app.guardrail.sop import check_sop
from app.models import (
    ActionType,
    Decision,
    DecisionKind,
    EngineResult,
    GateContext,
    GateStatus,
    GateStep,
    GateTrace,
    RiskLevel,
)

_RISK_TO_STATUS = {
    RiskLevel.low: GateStatus.PASS,
    RiskLevel.medium: GateStatus.WARN,
    RiskLevel.high: GateStatus.FAIL,
}


def _risk_step(ctx: GateContext, risk: RiskLevel, amount: float) -> GateStep:
    """Explains *why* the risk level was reached, and what it means for this action."""
    reasons = []
    if ctx.refunds_in_window > ctx.sop.max_refunds or ctx.refunds_in_window >= 2:
        reasons.append(f"{ctx.refunds_in_window} refunds in {ctx.sop.refund_window_days} days")
    if ctx.customer.account_age_days < 7 and amount > ctx.sop.high_value_hold_inr:
        reasons.append(f"account {ctx.customer.account_age_days} days old")
    if amount > 2 * ctx.customer.avg_order_inr:
        reasons.append(f"₹{amount:.0f} > 2× the usual order")
    why = f" ({', '.join(reasons)})" if reasons and risk != RiskLevel.low else ""
    if risk == RiskLevel.high:
        if amount > 0:
            return GateStep(step=4, gate="risk", status=GateStatus.FAIL,
                            detail=f"risk: high{why} — cash is blocked")
        return GateStep(step=4, gate="risk", status=GateStatus.WARN,
                        detail=f"risk: high{why} — no cash moves, so the action can proceed")
    return GateStep(step=4, gate="risk", status=_RISK_TO_STATUS[risk], detail=f"risk: {risk.value}{why}")


def _understanding_step(ctx: GateContext) -> GateStep:
    return GateStep(
        step=1, gate="understanding", status=GateStatus.PASS,
        detail=f"understood as: {ctx.understanding.intent.value}",
    )


def run_gates(ctx: GateContext) -> EngineResult:
    steps = [_understanding_step(ctx)]

    # Gate 1 — negation & contradiction. Skipped entirely on a re-gate (§7.4).
    if ctx.is_regate:
        steps.append(GateStep(
            step=2, gate="negation", status=GateStatus.SKIPPED,
            detail="re-gate reuses the original turn's negation result",
        ))
        gate1_warned = False
    else:
        neg = score_negation(ctx.transcript_used, ctx.understanding)
        status = GateStatus.WARN if neg.score >= 0.4 else GateStatus.PASS
        steps.append(GateStep(step=2, gate="negation", status=status, detail=f"score {neg.score:.1f}: {neg.detail}"))
        gate1_warned = status == GateStatus.WARN

    # Rule 1: Gate 1 warned -> CONFIRM_FIRST. Takes priority over everything else.
    if gate1_warned:
        candidates, negated_action = compute_candidates(ctx.understanding)
        steps.append(GateStep(step=3, gate="sop", status=GateStatus.SKIPPED, detail="not reached — rule 1 already decided"))
        steps.append(GateStep(step=4, gate="risk", status=GateStatus.SKIPPED, detail="not reached — rule 1 already decided"))
        decision = Decision(
            kind=DecisionKind.CONFIRM_FIRST, action_type=ctx.proposed_action.type,
            amount_inr=ctx.proposed_action.cash_amount_inr, reason="possible negation error — confirm with the customer before acting",
            candidate_actions=candidates, negated_action=negated_action,
        )
        steps.append(GateStep(step=5, gate="decision", status=GateStatus.PASS, detail=f"CONFIRM_FIRST: {decision.reason}"))
        return EngineResult(trace=GateTrace(steps=steps), decision=decision)

    # Rule 2: no order on file -> NEED_INFO.
    if ctx.order is None:
        steps.append(GateStep(step=3, gate="sop", status=GateStatus.SKIPPED, detail="no order on file"))
        steps.append(GateStep(step=4, gate="risk", status=GateStatus.SKIPPED, detail="not reached — rule 2 already decided"))
        decision = Decision(kind=DecisionKind.NEED_INFO, action_type=ctx.proposed_action.type, reason="no order on file")
        steps.append(GateStep(step=5, gate="decision", status=GateStatus.PASS, detail=f"NEED_INFO: {decision.reason}"))
        return EngineResult(trace=GateTrace(steps=steps), decision=decision)

    # Gate 2 — SOP.
    sop_result = check_sop(ctx)
    steps.append(GateStep(
        step=3, gate="sop", status=GateStatus.PASS if sop_result.passed else GateStatus.FAIL,
        detail=sop_result.detail,
    ))

    if not sop_result.passed:
        # Rule 3: SOP failed on a refund, an alternative is allowed, and it hasn't been declined.
        if (
            ctx.proposed_action.type == ActionType.refund
            and sop_result.alternative_allowed
            and not ctx.alternative_declined
        ):
            steps.append(GateStep(step=4, gate="risk", status=GateStatus.SKIPPED, detail="not reached — rule 3 already decided"))
            decision = Decision(
                kind=DecisionKind.OFFER_ALTERNATIVE, action_type=ActionType.refund,
                amount_inr=ctx.proposed_action.cash_amount_inr, reason=sop_result.detail,
            )
            steps.append(GateStep(step=5, gate="decision", status=GateStatus.PASS, detail=f"OFFER_ALTERNATIVE: {decision.reason}"))
            return EngineResult(trace=GateTrace(steps=steps), decision=decision)

        # Rule 4: SOP failed (any other case) -> HUMAN_HANDOFF.
        steps.append(GateStep(step=4, gate="risk", status=GateStatus.SKIPPED, detail="not reached — rule 4 already decided"))
        decision = Decision(
            kind=DecisionKind.HUMAN_HANDOFF, action_type=ctx.proposed_action.type,
            amount_inr=ctx.proposed_action.cash_amount_inr, reason=sop_result.detail,
        )
        steps.append(GateStep(step=5, gate="decision", status=GateStatus.PASS, detail=f"HUMAN_HANDOFF: {decision.reason}"))
        return EngineResult(trace=GateTrace(steps=steps), decision=decision)

    # Gate 3 — risk. Only reached once SOP has passed.
    risk = assess_risk(ctx)
    amount = ctx.proposed_action.cash_amount_inr
    steps.append(_risk_step(ctx, risk, amount))

    # Rule 5: never move cash at high risk.
    if risk == RiskLevel.high and amount > 0:
        decision = Decision(
            kind=DecisionKind.HUMAN_HANDOFF, action_type=ctx.proposed_action.type,
            amount_inr=amount, reason="high risk — never move cash at high risk",
        )
        steps.append(GateStep(step=5, gate="decision", status=GateStatus.PASS, detail=f"HUMAN_HANDOFF: {decision.reason}"))
        return EngineResult(trace=GateTrace(steps=steps), decision=decision)

    # Rule 6: high-value hold.
    if amount > ctx.sop.high_value_hold_inr:
        decision = Decision(
            kind=DecisionKind.EXECUTE_WITH_HOLD, action_type=ctx.proposed_action.type,
            amount_inr=amount,
            reason=f"₹{amount:.0f} is above the ₹{ctx.sop.high_value_hold_inr:.0f} hold — refund waits in an undo window",
        )
        steps.append(GateStep(step=5, gate="decision", status=GateStatus.PASS, detail=f"EXECUTE_WITH_HOLD: {decision.reason}"))
        return EngineResult(trace=GateTrace(steps=steps), decision=decision)

    # Rule 7: otherwise, execute.
    decision = Decision(kind=DecisionKind.EXECUTE, action_type=ctx.proposed_action.type, amount_inr=amount, reason="all checks passed")
    steps.append(GateStep(step=5, gate="decision", status=GateStatus.PASS, detail=f"EXECUTE: {decision.reason}"))
    return EngineResult(trace=GateTrace(steps=steps), decision=decision)
