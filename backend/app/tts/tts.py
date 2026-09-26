"""TtsClient protocol + settings-driven factory (§5.2). Nothing outside this package should
import a concrete provider directly."""
from __future__ import annotations

from typing import Protocol

from app.config import Settings


class TtsClient(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """Return raw mu-law 8kHz audio bytes for `text`, ready to reframe for Vobiz."""
        ...


def get_tts_client(settings: Settings) -> TtsClient:
    settings.require_tts()
    if settings.TTS_PROVIDER == "elevenlabs":
        from app.tts.elevenlabs_tts import ElevenLabsTts

        return ElevenLabsTts(settings)
    raise ValueError(f"Unsupported TTS_PROVIDER: {settings.TTS_PROVIDER}")
