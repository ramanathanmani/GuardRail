"""Vobiz webhooks + the bidirectional media stream (§5.3). Confirmed against
vobiz.ai/docs/xml/overview/how-it-works, vobiz.ai/docs/integrations/websockets and
vobiz.ai/docs/applications (Answer/Hangup URLs are POST, form-encoded).

Full phone loop (Phase 5): caller audio → Sarvam → finals → CallSession → template →
ElevenLabs → 160-byte frames → playAudio → checkpoint. Echo guard drops transcripts from the
first playAudio of a reply until its playedStream."""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.agent import templates
from app.agent.runtime import get_runtime
from app.agent.session_factory import build_session
from app.audio.codec import FRAME_BYTES
from app.config import Settings, get_settings
from app.events import bus
from app.logging_utils import mask_phone
from app.stt.sarvam_stream import SarvamStream
from app.telephony.vobiz_client import hangup_call
from app.tts.tts import TtsClient, get_tts_client

logger = logging.getLogger("guardrail.vobiz")

router = APIRouter()

GREETING_TEXT = templates.GREETING
PENDING_CALL_TTL_SECONDS = 60
FRAME_SECONDS = 0.02  # matches codec.FRAME_BYTES (160 bytes @ 8kHz mu-law = 20ms)
PLAYED_WAIT_SECONDS = 3.0
PREBUFFER_FRAMES = 5  # 100 ms of audio sent ahead of real time
# Fixed lines are rendered once and reused, so they play instantly.
CACHEABLE_LINES = {templates.GREETING, templates.GOODBYE, templates.STILL_THERE, templates.CLAUDE_FAILED,
                   templates.REFUND_ISSUED, templates.REFUND_UNDONE, templates.REFUND_FAILED,
                   templates.OFFER_ALTERNATIVE_LINE}

_pending_calls: dict[str, dict[str, Any]] = {}
_audio_cache: dict[str, bytes] = {}
_tts: TtsClient | None = None


def set_greeting_audio(audio: bytes) -> None:
    _audio_cache[GREETING_TEXT] = audio


def _get_tts() -> TtsClient:
    global _tts
    if _tts is None:
        _tts = get_tts_client(get_settings())
    return _tts


async def synthesize_cached(text: str) -> bytes:
    if text in _audio_cache:
        return _audio_cache[text]
    audio = await _get_tts().synthesize(text)
    if text in CACHEABLE_LINES:
        _audio_cache[text] = audio
    return audio


async def prerender_fixed_lines() -> None:
    for line in CACHEABLE_LINES:
        try:
            await synthesize_cached(line)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pre-render of a fixed line failed: %s", type(exc).__name__)


def _check_key(settings: Settings, k: str | None) -> bool:
    return bool(k) and secrets.compare_digest(k, settings.VOBIZ_STREAM_SECRET or "")


def _prune_pending_calls(now: float) -> None:
    expired = [cid for cid, c in _pending_calls.items() if now - c["registered_at"] > PENDING_CALL_TTL_SECONDS]
    for cid in expired:
        _pending_calls.pop(cid, None)


def _register_pending_call(call_uuid: str, from_number: str, to_number: str, profile: str) -> None:
    now = time.monotonic()
    _prune_pending_calls(now)
    _pending_calls[call_uuid] = {"from": from_number, "to": to_number, "profile": profile, "registered_at": now}


def _extract_stream_id(data: dict) -> str | None:
    """Vobiz's own docs disagree on which field carries the call/stream id across examples
    (`streamId` vs `callId` vs `CallUUID`, top-level vs nested under `start`). Try every
    candidate rather than trust one (§5.3)."""
    nested = data.get("start") if isinstance(data.get("start"), dict) else {}
    for source in (nested, data):
        for key in ("streamId", "callId", "callUUID", "CallUUID"):
            value = source.get(key)
            if value:
                return str(value)
    return None


def _extract_call_id(data: dict) -> str | None:
    """The call id (to match /vobiz/answer's CallUUID), preferring call-ish keys over streamId."""
    nested = data.get("start") if isinstance(data.get("start"), dict) else {}
    for source in (nested, data):
        for key in ("callId", "callUUID", "CallUUID", "call_uuid"):
            value = source.get(key)
            if value:
                return str(value)
    return None


def _claim_pending_call(candidates: list[str | None]) -> tuple[str, dict]:
    """Match a WS `start` event to the call registered at /vobiz/answer. Falls back to the
    most recently registered pending call, with a warning, if no id matches (§5.3)."""
    now = time.monotonic()
    _prune_pending_calls(now)
    for cid in candidates:
        if cid and cid in _pending_calls:
            return cid, _pending_calls.pop(cid)
    fallback_id = next((c for c in candidates if c), None) or f"call_{int(time.time())}"
    if not _pending_calls:
        return fallback_id, {"from": "", "profile": get_runtime().next_profile}
    most_recent_id = max(_pending_calls, key=lambda cid: _pending_calls[cid]["registered_at"])
    logger.warning("vobiz start ids %r not among pending calls; using most recent pending call %s",
                   candidates, most_recent_id)
    return most_recent_id, _pending_calls.pop(most_recent_id)


class VobizTransport:
    """Speaks into the live Vobiz stream. `speaking` is the echo-guard flag."""

    channel = "phone"

    def __init__(self, websocket: WebSocket, stream_id: str, call_uuid: str, settings: Settings) -> None:
        self._ws = websocket
        self.stream_id = stream_id
        self._call_uuid = call_uuid
        self._settings = settings
        self._counter = 0
        self._played: dict[str, asyncio.Event] = {}
        self.speaking = False
        self.closed = False

    def on_played(self, name: str | None) -> None:
        if name and name in self._played:
            self._played[name].set()

    async def _audio_chunks(self, text: str):
        """Cached lines come from memory; everything else streams straight from TTS, so the
        caller hears the first words while the rest is still being generated."""
        if text in _audio_cache:
            yield _audio_cache[text]
            return
        collected = bytearray()
        async for chunk in _get_tts().stream(text):
            collected.extend(chunk)
            yield chunk
        if text in CACHEABLE_LINES:
            _audio_cache[text] = bytes(collected)

    async def say(self, text: str) -> None:
        if self.closed or not text:
            return
        self._counter += 1
        name = f"reply-{self._counter}"
        event = self._played[name] = asyncio.Event()
        self.speaking = True
        try:
            started = None
            sent = 0
            buf = bytearray()
            async for chunk in self._audio_chunks(text):
                buf.extend(chunk)
                while len(buf) >= FRAME_BYTES:  # only whole 160-byte frames (§5.2)
                    if self.closed:
                        return
                    frame = bytes(buf[:FRAME_BYTES])
                    del buf[:FRAME_BYTES]
                    if started is None:
                        started = time.monotonic()
                    await self._ws.send_json({
                        "event": "playAudio", "streamId": self.stream_id,
                        "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000,
                                  "payload": base64.b64encode(frame).decode("ascii")},
                    })
                    sent += 1
                    # Pace to real time, but stay a few frames ahead so the line never starves.
                    delay = started + (sent - PREBUFFER_FRAMES) * FRAME_SECONDS - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
            await self._ws.send_json({"event": "checkpoint", "streamId": self.stream_id, "name": name})
            remaining = (started + sent * FRAME_SECONDS - time.monotonic()) if started else 0
            try:
                await asyncio.wait_for(event.wait(), timeout=max(0.0, remaining) + PLAYED_WAIT_SECONDS)
            except asyncio.TimeoutError:
                pass
        finally:
            self._played.pop(name, None)
            self.speaking = False

    async def hangup(self) -> None:
        if self.closed:
            return
        await hangup_call(self._settings, self._call_uuid)


@router.post("/vobiz/answer")
async def vobiz_answer(request: Request, k: str | None = None) -> Response:
    settings = get_settings()
    if not _check_key(settings, k):
        return Response(status_code=403)

    form = await request.form()
    call_uuid = str(form.get("CallUUID", ""))
    from_number = str(form.get("From", ""))
    to_number = str(form.get("To", ""))
    profile = get_runtime().next_profile
    if call_uuid:
        _register_pending_call(call_uuid, from_number, to_number, profile)
    logger.info("vobiz answer: call=%s from=%s profile=%s", call_uuid, mask_phone(from_number), profile)

    host = settings.public_host
    secret = settings.VOBIZ_STREAM_SECRET
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Stream bidirectional="true" audioTrack="inbound" keepCallAlive="true" '
        'contentType="audio/x-mulaw;rate=8000" '
        f'statusCallbackUrl="{settings.PUBLIC_BASE_URL}/vobiz/stream-status?k={secret}">'
        f"wss://{host}/vobiz/stream/{secret}"
        "</Stream>"
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")


@router.post("/vobiz/hangup")
async def vobiz_hangup(request: Request, k: str | None = None) -> Response:
    settings = get_settings()
    if not _check_key(settings, k):
        return Response(status_code=403)
    form = await request.form()
    call_uuid = str(form.get("CallUUID", ""))
    logger.info("vobiz hangup: call=%s cause=%s", call_uuid, form.get("HangupCause", ""))
    session = get_runtime().sessions.get(call_uuid)
    if session is not None:
        if isinstance(session.transport, VobizTransport):
            session.transport.closed = True
        get_runtime().spawn(session.hangup_received())
    return Response(status_code=200)


@router.post("/vobiz/stream-status")
async def vobiz_stream_status(request: Request, k: str | None = None) -> Response:
    settings = get_settings()
    if not _check_key(settings, k):
        return Response(status_code=403)
    form = await request.form()
    logger.info("vobiz stream-status: event=%s call=%s", form.get("Event", form.get("event", "")),
                form.get("CallUUID", ""))
    return Response(status_code=200)


@router.websocket("/vobiz/stream/{secret}")
async def vobiz_stream(websocket: WebSocket, secret: str) -> None:
    settings = get_settings()
    if not secrets.compare_digest(secret, settings.VOBIZ_STREAM_SECRET or ""):
        await websocket.close(code=4403)
        return
    await websocket.accept()

    rt = get_runtime()
    call_id = "unknown"
    stt: SarvamStream | None = None
    transport: VobizTransport | None = None
    session = None
    logged_first_start = False
    queued: list[str] = []
    draining = [False]

    async def on_transcript(event: str, text: str) -> None:
        if session is None or not text.strip():
            return
        if event == "transcript.partial":
            bus.publish("transcript.partial", call_id, text=text)
            return
        # Echo guard (§6.3): drop finals while the agent is speaking.
        if transport and transport.speaking:
            logger.info("call %s: dropped final during agent speech (echo guard)", call_id)
            return
        logger.info("call %s: transcript.final: %s", call_id, text)
        # While the agent is thinking, queue what the caller says; it becomes the next turn.
        queued.append(text)
        if not draining[0]:
            draining[0] = True
            rt.spawn(drain())

    async def drain() -> None:
        try:
            while queued and session is not None and not session.ended:
                text = " ".join(queued)
                queued.clear()
                await session.process_turn(text)  # waits for the session lock itself
        finally:
            draining[0] = False

    try:
        while True:
            raw = await websocket.receive_json()
            event = raw.get("event")

            if event == "start":
                if not logged_first_start:
                    logger.info("call %s: raw start event: %s", call_id, raw)
                    logged_first_start = True
                stream_id = _extract_stream_id(raw) or "unknown"
                call_id, pending = _claim_pending_call([_extract_call_id(raw), stream_id])
                logger.info("call %s: stream started (stream_id=%s)", call_id, stream_id)

                transport = VobizTransport(websocket, stream_id, call_id, settings)
                caller = pending.get("from", "")
                session = await build_session(rt, call_id, pending.get("profile", rt.next_profile), transport,
                                              caller_number=caller, caller_masked=mask_phone(caller) if caller else None)
                await session.start()
                stt_lang = session.customer.language_code if settings.SARVAM_STT_LANGUAGE == "profile" else settings.SARVAM_STT_LANGUAGE
                stt = SarvamStream(settings, call_id, stt_lang, on_transcript)
                await stt.open()
                rt.spawn(session.play_greeting())

            elif event == "media":
                payload = raw.get("media", {}).get("payload")
                if payload and stt:
                    await stt.send_audio_b64(payload)

            elif event == "playedStream":
                if transport:
                    transport.on_played(raw.get("name") or raw.get("checkpoint", {}).get("name"))

            elif event == "clearedAudio":
                logger.debug("call %s: clearedAudio", call_id)

            elif event == "stop":
                # Not guaranteed by Vobiz — the WebSocketDisconnect below is the real signal.
                logger.info("call %s: stream stop event received", call_id)
                break

    except WebSocketDisconnect:
        logger.info("call %s: websocket disconnected", call_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("call %s: stream loop error: %s", call_id, exc)
    finally:
        if transport:
            transport.closed = True
        if stt:
            await stt.close()
        if session is not None:
            await session.hangup_received()
