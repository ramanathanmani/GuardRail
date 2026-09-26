"""Freshdesk adapter (§5.5) against a mocked transport."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.config import Settings
from app.integrations.freshdesk import FreshdeskClient
from app.integrations.helpdesk import TicketRequest

pytestmark = pytest.mark.asyncio


def settings(**kw):
    return Settings(_env_file=None, FRESHDESK_DOMAIN="https://acme.freshdesk.com", FRESHDESK_API_KEY="k-test", **kw)


def req():
    return TicketRequest(subject="s", description_html="d", requester_name="Riya", requester_phone="+919000000001",
                         tags=["guardrail"], custom_fields={"cf_x": "y"})


async def test_auth_payload_and_domain():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(201, json={"id": 42})

    client = FreshdeskClient(settings(), transport=httpx.MockTransport(handler))
    ref = await client.create_ticket(req())
    assert ref.ticket_id == "42" and ref.url == "https://acme.freshdesk.com/a/tickets/42"
    r = seen[0]
    assert str(r.url) == "https://acme.freshdesk.com/api/v2/tickets"
    assert r.headers["authorization"] == "Basic " + base64.b64encode(b"k-test:X").decode()
    body = json.loads(r.content)
    assert body["name"] == "Riya" and body["phone"] == "+919000000001" and body["source"] == 3
    assert "custom_fields" not in body


async def test_custom_fields_only_when_flag_on():
    seen = []
    client = FreshdeskClient(settings(FRESHDESK_CUSTOM_FIELDS=True),
                             transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(201, json={"id": 1})))
    await client.create_ticket(req())
    assert json.loads(seen[0].content)["custom_fields"] == {"cf_x": "y"}


async def test_create_timeout_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow")

    client = FreshdeskClient(settings(), transport=httpx.MockTransport(handler))
    assert await client.create_ticket(req()) is None
    assert len(calls) == 1


async def test_429_retried_once_and_tags_full_list():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={})

    client = FreshdeskClient(settings(), transport=httpx.MockTransport(handler))
    await client.update_tags("7", ["guardrail", "ta-IN", "finalized"])
    assert len(calls) == 2
    assert json.loads(calls[1].content) == {"tags": ["guardrail", "ta-IN", "finalized"]}
