"""Process-wide services a CallSession needs: providers (built by settings-driven factories),
the live-session registry, the undo manager, and the "Next caller" setting (§6.1).
Tests swap it with `set_runtime()`."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Protocol

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from app.agent.conversation import CallSession
    from app.guardrail.undo import UndoManager
    from app.integrations.helpdesk import HelpdeskClient
    from app.integrations.payments import PaymentsClient
    from app.store.audit_archive import AuditArchive

logger = logging.getLogger("guardrail.runtime")

PROFILE_CHOICES = ("riya", "arjun", "meera", "karthik", "auto")


class Transport(Protocol):
    channel: str  # phone | text

    async def say(self, text: str) -> None:
        """Speak `text` and return once it has been played (phone) — no-op for typed calls."""
        ...

    async def hangup(self) -> None: ...


class NullTransport:
    channel = "text"

    async def say(self, text: str) -> None:
        return None

    async def hangup(self) -> None:
        return None


@dataclass
class Runtime:
    settings: Settings
    claude: object  # ClaudeClient-like: understand_turn(), summarize_call()
    helpdesk: "HelpdeskClient"
    payments: "PaymentsClient"
    archive: "AuditArchive"
    undo: "UndoManager" = None  # type: ignore[assignment]
    sessions: dict[str, "CallSession"] = field(default_factory=dict)
    ticket_tags: dict[str, list[str]] = field(default_factory=dict)
    next_profile: str = "riya"
    background: set[asyncio.Task] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.undo is None:
            from app.guardrail.undo import UndoManager

            self.undo = UndoManager(self)

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    async def on_refund_finished(self, call_id: str, spoken_line: str | None) -> None:
        """Called by the undo manager after expiry/undo from outside the call's own turn."""
        session = self.sessions.get(call_id)
        if session is not None and not session.ended:
            await session.announce_and_close(spoken_line)
        else:
            await self.maybe_complete(call_id)

    async def maybe_complete(self, call_id: str) -> None:
        """Complete = call ended AND no decision still pending_finalize → summary note +
        archive (§6.4). Runs in the background; never raises."""
        from app.store import calls_repo
        from app.store.audit_archive import archive_safely

        try:
            if call_id in self.sessions:
                return
            if await calls_repo.run(calls_repo.has_pending_finalize, call_id):
                return
            record = await calls_repo.run(calls_repo.call_record, call_id)
            if record is None or record["archived"] or record["is_seed"]:
                return
            await calls_repo.run(calls_repo.update_call, call_id, archived=True)
            ticket_id = record["ticket"]["ticket_id"]
            if ticket_id:
                self.spawn(self._summary_note(ticket_id, record))
            started = record["started_at"]
            day = started.date().isoformat() if started else date.today().isoformat()
            await archive_safely(self.archive, f"calls/{day}/{call_id}.json", record, call_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("call %s: completion failed: %s", call_id, exc)

    async def _summary_note(self, ticket_id: str, record: dict) -> None:
        try:
            lines = []
            for t in record["turns"]:
                if t["speaker"] == "customer":
                    lines.append(f"Customer: {t['text_used']} (English: {t.get('english') or '-'})")
                else:
                    lines.append(f"Agent: {t['text_used']}")
            summary = await self.claude.summarize_call("\n".join(lines))
            await self.helpdesk.add_note(ticket_id, f"<b>Claude call summary</b><br>{summary}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("summary note for ticket %s failed: %s", ticket_id, type(exc).__name__)


_runtime: Runtime | None = None


def build_runtime(settings: Settings | None = None) -> Runtime:
    from app.agent.claude_client import ClaudeClient
    from app.integrations.helpdesk import get_helpdesk_client
    from app.integrations.payments import get_payments_client
    from app.store.audit_archive import get_audit_archive

    settings = settings or get_settings()
    return Runtime(
        settings=settings,
        claude=ClaudeClient(settings),
        helpdesk=get_helpdesk_client(settings),
        payments=get_payments_client(settings),
        archive=get_audit_archive(settings),
    )


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    _runtime = runtime
