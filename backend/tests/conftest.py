"""Shared fakes: temp SQLite DB (seeded), fixture-driven Claude, recording helpdesk/payments,
local archive. No test ever hits a real API."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine

from app.agent.runtime import Runtime, set_runtime
from app.config import Settings
from app.guardrail import fault_injection
from app.integrations.helpdesk import TicketRef
from app.integrations.payments import RefundResult
from app.models import Understanding
from app.store import db as db_module
from app.store.audit_archive import LocalArchive
from app.store.seed import seed_if_empty

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "understandings.json").read_text())


class FakeClaude:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def understand_turn(self, text: str, order_summary: str) -> Understanding:
        self.calls.append(text)
        t = text.lower()
        if "venum" in t:
            key = "R1"
        elif "vendam" in t:
            key = "R3"
        elif "beku" in t:
            key = "M1"
        elif "address" in t:
            key = "K1"
        else:
            key = "A1"
        return Understanding.model_validate(FIXTURES[key])

    async def summarize_call(self, transcript: str) -> str:
        return "summary"


class FakeHelpdesk:
    def __init__(self) -> None:
        self.created: list = []
        self.notes: list = []
        self.tag_updates: list = []

    async def create_ticket(self, req):
        self.created.append(req)
        return TicketRef(ticket_id=str(1000 + len(self.created)), url=f"https://t/{len(self.created)}")

    async def add_note(self, ticket_id, body_html):
        self.notes.append((ticket_id, body_html))

    async def update_tags(self, ticket_id, tags):
        self.tag_updates.append((ticket_id, list(tags)))


class FakePayments:
    provider = "dodo"

    def __init__(self) -> None:
        self.refunds: list = []
        self.fail = False

    async def refund(self, payment_id, amount_inr, reason, metadata):
        if self.fail:
            from app.integrations.payments import PaymentsError

            raise PaymentsError("boom")
        self.refunds.append(payment_id)
        return RefundResult(refund_id=f"ref_{len(self.refunds)}", status="succeeded", amount=amount_inr,
                            currency="INR", provider="dodo")


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "_engine", engine)
    db_module.init_db()
    with Session(engine) as s:
        seed_if_empty(s)
    yield engine


@pytest.fixture
def rt(db, tmp_path):
    settings = Settings(_env_file=None, UNDO_WINDOW_SECONDS=1, SILENCE_TIMEOUT_SECONDS=8, MAX_CALL_SECONDS=180,
                        HELPDESK="none", PAYMENTS="simulated", AUDIT_ARCHIVE="local")
    runtime = Runtime(settings=settings, claude=FakeClaude(), helpdesk=FakeHelpdesk(), payments=FakePayments(),
                      archive=LocalArchive(tmp_path / "audit"))
    set_runtime(runtime)
    fault_injection.set_mode("off")
    yield runtime
    set_runtime(None)
    fault_injection.set_mode("off")
