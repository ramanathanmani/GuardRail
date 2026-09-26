"""In-process pub/sub → dashboard sockets (§8.3). One worker, so a module-level set of queues
is the whole bus. Every event is {"type", "call_id", "ts", ...payload}."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("guardrail.events")

_subscribers: set[asyncio.Queue] = set()
_recent: list[dict] = []  # small replay buffer so a dashboard opened mid-call catches up
RECENT_MAX = 200


def subscribe() -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    _subscribers.discard(queue)


def recent_events() -> list[dict]:
    return list(_recent)


def publish(event_type: str, call_id: str | None, **payload: Any) -> dict:
    event = {"type": event_type, "call_id": call_id, "ts": datetime.now(timezone.utc).isoformat(), **payload}
    _recent.append(event)
    del _recent[:-RECENT_MAX]
    for queue in list(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("dashboard subscriber queue full; dropping event %s", event_type)
    return event
