"""One Sarvam realtime STT session per call (§5.1). Confirmed against the installed `sarvamai`
SDK (`speech_to_text_realtime_streaming.connect(...)` is an async context manager; the audio
input and end-of-turn messages are typed models, not raw dicts)."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from sarvamai import AsyncSarvamAI, RealtimeAudioInput, RealtimeEnd

from app.config import Settings

logger = logging.getLogger("guardrail.sarvam")

# Biases decoding toward the words the gates care about (applied to final transcripts).
STT_PROMPT = (
    "Customer support call, code-mixed Tamil/Hindi/Kannada and English. Words: refund, replacement, "
    "exchange, cancel, address, order, vendam, venum, venda, beku, beda, chahiye, nahi, mat, karo, "
    "anuppunga, aama, haan, illa, howdu."
)

TranscriptHandler = Callable[[str, str], Awaitable[None]]  # (event, text) -> None


class SarvamStream:
    """Wraps one realtime STT connection for a call. Feed it base64 mu-law frames (passed
    straight through from Vobiz's own `media.payload`, no re-encoding needed); `on_transcript`
    fires for every `transcript.partial` / `transcript.final`."""

    def __init__(
        self, settings: Settings, call_id: str, language_code: str, on_transcript: TranscriptHandler
    ) -> None:
        settings.require_stt()
        self._client = AsyncSarvamAI(api_subscription_key=settings.SARVAM_API_KEY)
        self._model = settings.SARVAM_STT_MODEL
        self._mode = settings.SARVAM_STT_MODE
        self._call_id = call_id
        self._language_code = language_code
        self._on_transcript = on_transcript
        self._ctx = None
        self._ws = None
        self._receive_task: asyncio.Task | None = None

    async def open(self) -> None:
        self._ctx = self._client.speech_to_text_realtime_streaming.connect(
            language_code=self._language_code,
            model=self._model,
            mode=self._mode,
            prompt=STT_PROMPT,
            endpointing="vad",
            stream_type="fast",
            encoding="mulaw",
            sample_rate="8000",
        )
        self._ws = await self._ctx.__aenter__()
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def send_audio_b64(self, payload_b64: str) -> None:
        if self._ws is None:
            return
        await self._ws.send_realtime_audio_input(RealtimeAudioInput(audio=payload_b64))

    async def close(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send_realtime_end(RealtimeEnd())
        except Exception as exc:
            logger.debug("call %s: sarvam send_realtime_end failed: %s", self._call_id, exc)
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ctx:
            await self._ctx.__aexit__(None, None, None)
        self._ws = None

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                event = getattr(message, "event", None)
                if event in ("transcript.partial", "transcript.final"):
                    await self._on_transcript(event, getattr(message, "text", ""))
                elif event == "error":
                    logger.warning(
                        "call %s: sarvam error %s: %s",
                        self._call_id, getattr(message, "code", "?"), getattr(message, "message", ""),
                    )
                    if getattr(message, "is_fatal", False):
                        return
                elif event and str(event).startswith("vad."):
                    logger.debug("call %s: sarvam %s", self._call_id, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("call %s: sarvam receive loop ended: %s", self._call_id, exc)
