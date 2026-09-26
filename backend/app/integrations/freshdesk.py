"""Freshdesk API v2 (§5.5). Basic auth (api_key, "X"). Retry only on 429, once, after
Retry-After. A ticket create that times out is never retried (it may have gone through)."""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings
from app.integrations.helpdesk import TicketRef, TicketRequest

logger = logging.getLogger("guardrail.freshdesk")

TIMEOUT_SECONDS = 10.0


class FreshdeskClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._domain = settings.FRESHDESK_DOMAIN
        self._base = f"https://{self._domain}.freshdesk.com/api/v2"
        self._auth = httpx.BasicAuth(settings.FRESHDESK_API_KEY or "", "X")
        self._custom_fields = settings.FRESHDESK_CUSTOM_FIELDS
        self._transport = transport

    def ticket_url(self, ticket_id: str) -> str:
        return f"https://{self._domain}.freshdesk.com/a/tickets/{ticket_id}"

    async def _request(self, method: str, path: str, json: dict) -> httpx.Response:
        async with httpx.AsyncClient(auth=self._auth, timeout=TIMEOUT_SECONDS, transport=self._transport) as client:
            resp = await client.request(method, f"{self._base}{path}", json=json)
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", "1") or 1)
                logger.warning("freshdesk 429 on %s %s; retrying once after %.0fs", method, path, wait)
                await asyncio.sleep(min(wait, 30))
                resp = await client.request(method, f"{self._base}{path}", json=json)
            return resp

    async def create_ticket(self, req: TicketRequest) -> TicketRef | None:
        payload: dict = {
            "subject": req.subject,
            "description": req.description_html,
            "name": req.requester_name,
            "phone": req.requester_phone,
            "source": 3,
            "status": 2,
            "priority": 2,
            "tags": req.tags,
        }
        if self._custom_fields and req.custom_fields:
            payload["custom_fields"] = req.custom_fields
        try:
            resp = await self._request("POST", "/tickets", payload)
        except httpx.TimeoutException:
            logger.error("freshdesk ticket create timed out — not retrying (it may have succeeded)")
            return None
        except httpx.HTTPError as exc:
            logger.error("freshdesk ticket create failed: %s", type(exc).__name__)
            return None
        if resp.status_code >= 300:
            logger.error("freshdesk ticket create returned %s: %s", resp.status_code, resp.text[:300])
            return None
        ticket_id = str(resp.json()["id"])
        return TicketRef(ticket_id=ticket_id, url=self.ticket_url(ticket_id))

    async def add_note(self, ticket_id: str, body_html: str) -> None:
        try:
            resp = await self._request("POST", f"/tickets/{ticket_id}/notes", {"body": body_html, "private": True})
            if resp.status_code >= 300:
                logger.error("freshdesk note on %s returned %s", ticket_id, resp.status_code)
        except httpx.HTTPError as exc:
            logger.error("freshdesk note on %s failed: %s", ticket_id, type(exc).__name__)

    async def update_tags(self, ticket_id: str, tags: list[str]) -> None:
        # PUT tags replaces the whole list — callers always pass the full new list.
        try:
            resp = await self._request("PUT", f"/tickets/{ticket_id}", {"tags": tags})
            if resp.status_code >= 300:
                logger.error("freshdesk tag update on %s returned %s", ticket_id, resp.status_code)
        except httpx.HTTPError as exc:
            logger.error("freshdesk tag update on %s failed: %s", ticket_id, type(exc).__name__)
