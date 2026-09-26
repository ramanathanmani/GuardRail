"""SQLModel tables (§4.1) and the sync engine/session. Callers wrap session work in
asyncio.to_thread from async request/WS paths, same as any other sync SDK call."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator

from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, create_engine

from app.config import Settings, get_settings


class Customer(SQLModel, table=True):
    __tablename__ = "customers"

    id: str = Field(primary_key=True)
    name: str
    phone: str
    language_code: str
    account_age_days: int
    avg_order_inr: float
    is_seed: bool = False


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: str = Field(primary_key=True)
    customer_id: str = Field(foreign_key="customers.id")
    item: str
    category: str
    amount_inr: float
    status: str  # processing | delivered
    delivered_on: date | None = None
    is_seed: bool = False


class RefundHistory(SQLModel, table=True):
    __tablename__ = "refund_history"

    id: str = Field(primary_key=True)
    customer_id: str = Field(foreign_key="customers.id")
    order_id: str = Field(foreign_key="orders.id")
    refunded_on: date
    is_seed: bool = False


class Payment(SQLModel, table=True):
    """Populated only by store/dodo_setup.py. seed --reset never touches this table."""

    __tablename__ = "payments"

    payment_id: str = Field(primary_key=True)
    order_id: str = Field(foreign_key="orders.id")
    amount_inr: float
    refunded: bool = False
    refund_id: str | None = None


class SopRules(SQLModel, table=True):
    """Single-row policy table."""

    __tablename__ = "sop_rules"

    id: int | None = Field(default=1, primary_key=True)
    return_window_days: int
    max_refunds: int
    refund_window_days: int
    excluded_categories: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    high_value_hold_inr: float
    require_confirm_on_negation: bool
    undo_window_enabled: bool


class Call(SQLModel, table=True):
    __tablename__ = "calls"

    id: str = Field(primary_key=True)
    channel: str  # phone | text
    profile_id: str | None = None
    caller_masked: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    state: str
    archived: bool = False
    is_seed: bool = False


class Turn(SQLModel, table=True):
    __tablename__ = "turns"

    id: str = Field(primary_key=True)
    call_id: str = Field(foreign_key="calls.id")
    idx: int
    speaker: str  # customer | agent
    text_asr: str | None = None
    text_used: str | None = None
    fault_injected: bool = False
    english: str | None = None
    understanding_json: dict | None = Field(default=None, sa_column=Column(JSON))
    ts: datetime


class Decision(SQLModel, table=True):
    __tablename__ = "decisions"

    id: str = Field(primary_key=True)  # "dec_…"
    call_id: str = Field(foreign_key="calls.id")
    kind: str
    action_type: str | None = None
    amount_inr: float = 0
    reason: str
    trace_json: list = Field(default_factory=list, sa_column=Column(JSON))
    first_proposed_action: str | None = None
    ticket_id: str | None = None
    ticket_url: str | None = None
    status: str = "open"  # open | pending_finalize | finalized | undone | refund_failed
    finalize_at: datetime | None = None
    refund_id: str | None = None
    refund_status: str | None = None
    refund_provider: str | None = None
    latency_ms: int | None = None
    created_at: datetime
    is_seed: bool = False


_engine = None


def get_engine(settings: Settings | None = None):
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
        _engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
    return _engine


def init_db(settings: Settings | None = None) -> None:
    SQLModel.metadata.create_all(get_engine(settings))


@contextmanager
def get_session(settings: Settings | None = None) -> Iterator[Session]:
    with Session(get_engine(settings)) as session:
        yield session
