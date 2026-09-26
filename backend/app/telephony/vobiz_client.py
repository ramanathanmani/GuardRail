"""Hang up via Vobiz REST — confirmed at docs.vobiz.ai/call/hangup-call (§5.3):
DELETE https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/{CallUUID}/ → 204."""
from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger("guardrail.vobiz")


async def hangup_call(settings: Settings, call_uuid: str) -> bool:
    url = f"https://api.vobiz.ai/api/v1/Account/{settings.VOBIZ_AUTH_ID}/Call/{call_uuid}/"
    headers = {"X-Auth-ID": settings.VOBIZ_AUTH_ID or "", "X-Auth-Token": settings.VOBIZ_AUTH_TOKEN or ""}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.delete(url, headers=headers)
        if resp.status_code in (200, 204):
            return True
        logger.warning("call %s: vobiz hangup returned %s", call_uuid, resp.status_code)
    except httpx.HTTPError as exc:
        logger.warning("call %s: vobiz hangup failed: %s", call_uuid, type(exc).__name__)
    return False
