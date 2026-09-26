"""understand_turn() and summarize_call() (§5.4, §7.1). Forced tool call, 8s timeout, one
retry — confirmed against the installed `anthropic` SDK's tool_choice shape."""
from __future__ import annotations

import logging

import anthropic

from app.agent.prompts import RECORD_UNDERSTANDING_TOOL, build_system_prompt
from app.config import Settings
from app.models import Understanding

logger = logging.getLogger("guardrail.claude")

TIMEOUT_SECONDS = 8.0


class ClaudeUnavailable(Exception):
    pass


class ClaudeClient:
    def __init__(self, settings: Settings) -> None:
        settings.require_llm()
        self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, max_retries=0)
        self._model_fast = settings.ANTHROPIC_MODEL_FAST
        self._model_smart = settings.ANTHROPIC_MODEL_SMART

    async def understand_turn(self, text: str, order_summary: str) -> Understanding:
        last_error: Exception | None = None
        for attempt in range(2):  # one try + one retry
            try:
                message = await self._client.messages.create(
                    model=self._model_fast,
                    max_tokens=1024,
                    system=build_system_prompt(order_summary),
                    messages=[{"role": "user", "content": text}],
                    tools=[RECORD_UNDERSTANDING_TOOL],
                    tool_choice={"type": "tool", "name": "record_understanding"},
                    timeout=TIMEOUT_SECONDS,
                )
                for block in message.content:
                    if block.type == "tool_use" and block.name == "record_understanding":
                        return Understanding.model_validate(block.input)
                last_error = ClaudeUnavailable("no tool_use block in Claude's response")
            except Exception as exc:  # noqa: BLE001 - any SDK/network failure triggers the retry
                last_error = exc
                logger.warning("understand_turn attempt %d failed: %s", attempt + 1, exc)
        raise ClaudeUnavailable(str(last_error)) from last_error

    async def summarize_call(self, transcript: str) -> str:
        message = await self._client.messages.create(
            model=self._model_smart,
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": f"Summarize this support call for a human agent, in under 120 words:\n\n{transcript}",
            }],
        )
        return "".join(block.text for block in message.content if block.type == "text")
