"""ElevenLabs streaming TTS (§5.2). Confirmed against elevenlabs.io/docs (the /stream endpoint
reference and the streaming cookbook): `ulaw_8000` is a valid `output_format`, so ElevenLabs'
own bytes go straight to Vobiz with no conversion."""
from __future__ import annotations

from elevenlabs.client import AsyncElevenLabs

from app.config import Settings


class ElevenLabsTts:
    def __init__(self, settings: Settings) -> None:
        self._client = AsyncElevenLabs(api_key=settings.ELEVENLABS_API_KEY)
        self._voice_id = settings.ELEVENLABS_VOICE_ID
        self._model_id = settings.ELEVENLABS_MODEL

    async def synthesize(self, text: str) -> bytes:
        stream = self._client.text_to_speech.stream(
            voice_id=self._voice_id,
            text=text,
            model_id=self._model_id,
            output_format="ulaw_8000",
        )
        chunks = [chunk async for chunk in stream if chunk]
        return b"".join(chunks)
