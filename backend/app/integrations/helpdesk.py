"""HelpdeskClient protocol + settings-driven factory. Nothing imports a concrete helpdesk."""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings

logger = logging.getLogger("guardrail.helpdesk")


@dataclass
class TicketRef:
    ticket_id: str
    url: str | None


@dataclass
class TicketRequest:
    subject: str
    description_html: str
    requester_name: str
    requester_phone: str
    tags: list[str]
    custom_fields: dict | None = None


class HelpdeskClient(Protocol):
    async def create_ticket(self, req: TicketRequest) -> TicketRef | None: ...
    async def add_note(self, ticket_id: str, body_html: str) -> None: ...
    async def update_tags(self, ticket_id: str, tags: list[str]) -> None: ...


class LocalHelpdesk:
    """HELPDESK=none: local ticket ids (LOCAL-n), clearly not Freshdesk. For offline dev only."""

    _counter = itertools.count(1)

    async def create_ticket(self, req: TicketRequest) -> TicketRef | None:
        ticket_id = f"LOCAL-{next(self._counter)}"
        logger.info("local helpdesk: created %s (%s) tags=%s", ticket_id, req.subject, req.tags)
        return TicketRef(ticket_id=ticket_id, url=None)

    async def add_note(self, ticket_id: str, body_html: str) -> None:
        logger.info("local helpdesk: note on %s", ticket_id)

    async def update_tags(self, ticket_id: str, tags: list[str]) -> None:
        logger.info("local helpdesk: %s tags=%s", ticket_id, tags)


def get_helpdesk_client(settings: Settings) -> HelpdeskClient:
    settings.require_helpdesk()
    if settings.HELPDESK == "freshdesk":
        from app.integrations.freshdesk import FreshdeskClient

        return FreshdeskClient(settings)
    if settings.HELPDESK == "none":
        return LocalHelpdesk()
    raise ValueError(f"Unsupported HELPDESK: {settings.HELPDESK}")
