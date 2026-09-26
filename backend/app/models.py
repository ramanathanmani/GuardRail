"""Pydantic domain models shared by the engine, the agent layer and the API (§7, §8)."""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class Intent(str, Enum):
    refund = "refund"
    replacement = "replacement"
    cancel_order = "cancel_order"
    address_change = "address_change"
    order_status = "order_status"
    complaint = "complaint"
    other = "other"
    unclear = "unclear"


class ActionType(str, Enum):
    refund = "refund"
    replacement = "replacement"
    cancellation = "cancellation"
    address_change = "address_change"


class DecisionKind(str, Enum):
    CONFIRM_FIRST = "CONFIRM_FIRST"
    NEED_INFO = "NEED_INFO"
    OFFER_ALTERNATIVE = "OFFER_ALTERNATIVE"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    EXECUTE = "EXECUTE"
    EXECUTE_WITH_HOLD = "EXECUTE_WITH_HOLD"


class GateStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class NegationTerm(BaseModel):
    term: str
    language: str
    meaning: str
    governs: str | None = None


class NegationAnalysis(BaseModel):
    negation_terms: list[NegationTerm] = Field(default_factory=list)
    contradiction: bool = False
    contradiction_detail: str = ""
    intent_confidence: float = 1.0


class Entities(BaseModel):
    order_id: str | None = None
    product: str | None = None
    issue: str | None = None


class Understanding(BaseModel):
    """The shape of Claude's forced `record_understanding` tool call (§7.1)."""

    english_translation: str
    languages_detected: list[str] = Field(default_factory=list)
    intent: Intent
    actions_mentioned: list[str] = Field(default_factory=list)
    entities: Entities = Field(default_factory=Entities)
    negation_analysis: NegationAnalysis
    reply_to_customer: str = ""


class CustomerProfile(BaseModel):
    id: str
    name: str
    phone: str
    language_code: str
    account_age_days: int
    avg_order_inr: float


class OrderInfo(BaseModel):
    id: str
    item: str
    category: str
    amount_inr: float
    status: str  # processing | delivered
    delivered_on: date | None = None


class SopRulesModel(BaseModel):
    return_window_days: int
    max_refunds: int
    refund_window_days: int
    excluded_categories: list[str] = Field(default_factory=list)
    high_value_hold_inr: float
    require_confirm_on_negation: bool
    undo_window_enabled: bool


class ProposedAction(BaseModel):
    type: ActionType | None = None
    cash_amount_inr: float = 0
    order_id: str | None = None


class GateContext(BaseModel):
    call_id: str
    customer: CustomerProfile
    order: OrderInfo | None
    refunds_in_window: int
    sop: SopRulesModel
    transcript_used: str
    understanding: Understanding
    proposed_action: ProposedAction
    actions_mentioned: list[str] = Field(default_factory=list)
    confirmed_by_customer: bool = False
    alternative_accepted: bool = False
    alternative_declined: bool = False
    is_regate: bool = False  # skips Gate 1 (§7.4)


class GateStep(BaseModel):
    step: int
    gate: str
    status: GateStatus
    detail: str


class GateTrace(BaseModel):
    steps: list[GateStep]


class Decision(BaseModel):
    kind: DecisionKind
    action_type: ActionType | None = None
    amount_inr: float = 0
    reason: str
    candidate_actions: list[ActionType] = Field(default_factory=list)
    negated_action: ActionType | None = None


class EngineResult(BaseModel):
    trace: GateTrace
    decision: Decision
