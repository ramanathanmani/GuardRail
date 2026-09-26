"""Vobiz webhooks + the bidirectional media stream (§5.3). Confirmed against
vobiz.ai/docs/xml/overview/how-it-works, vobiz.ai/docs/integrations/websockets and
vobiz.ai/docs/applications (Answer/Hangup URLs are POST, form-encoded).

Phase 2 scope only: accept the call, play the pre-rendered greeting, bridge caller audio to
Sarvam, and log final transcripts. No CallSession state machine, no DB rows yet (Phase 3)."""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.audio.codec import reframe
from app.config import Settings, get_settings
from app.logging_utils import mask_phone
from app.stt.sarvam_stream import SarvamStream

logger = logging.getLogger("guardrail.vobiz")

router = APIRouter()

GREETING_TEXT = "Hi, you've reached QuickKart support. I'm an AI assistant. How can I help you today?"

PENDING_CALL_TTL_SECONDS = 60
# Phase 3 will pass the bound profile's language_code instead of this placeholder.
DEFAULT_LANGUAGE_CODE = "hi-IN"

_pending_calls: dict[str, dict[str, Any]] = {}
_greeting_audio: bytes | None = None

FRAME_SECONDS = 0.02  # matches codec.FRAME_BYTES (160 bytes @ 8kHz mu-law = 20ms)


def set_greeting_audio(audio: bytes) -> None:
    global _greeting_audio
    _greeting_audio = audio


def _check_key(settings: Settings, k: str | None) -> bool:
    return bool(k) and secrets.compare_digest(k, settings.VOBIZ_STREAM_SECRET or "")


def _prune_pending_calls(now: float) -> None:
    expired = [cid for cid, c in _pending_calls.items() if now - c["registered_at"] > PENDING_CALL_TTL_SECONDS]
    for cid in expired:
        _pending_calls.pop(cid, None)


def _register_pending_call(call_uuid: str, from_number: str, to_number: str) -> None:
    now = time.monotonic()
    _prune_pending_calls(now)
    _pending_calls[call_uuid] = {"from": from_number, "to": to_number, "registered_at": now}


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


def _claim_pending_call(stream_id: str | None) -> str:
    """Match a WS `start` event to the call registered at /vobiz/answer. Falls back to the
    most recently registered pending call, with a warning, if no id matches (§5.3)."""
    now = time.monotonic()
    _prune_pending_calls(now)
    if not _pending_calls:
        return stream_id or "unknown"
    if stream_id and stream_id in _pending_calls:
        _pending_calls.pop(stream_id)
        return stream_id
    most_recent_id = max(_pending_calls, key=lambda cid: _pending_calls[cid]["registered_at"])
    logger.warning(
        "vobiz start event id %r not among pending calls; using most recent pending call %s",
        stream_id, most_recent_id,
    )
    _pending_calls.pop(most_recent_id)
    return most_recent_id


async def _send_paced_audio(websocket: WebSocket, stream_id: str, audio: bytes, checkpoint_name: str) -> None:
    """Send μ-law frames paced to real time. Vobiz's own guidance is ~20-60ms per playAudio
    message (§5.2) — sending them back-to-back instead makes the whole clip play rushed."""
    started = time.monotonic()
    for i, frame in enumerate(reframe(audio)):
        await websocket.send_json({
            "event": "playAudio",
            "streamId": stream_id,
            "media": {
                "contentType": "audio/x-mulaw",
                "sampleRate": 8000,
                "payload": base64.b64encode(frame).decode("ascii"),
            },
        })
        target = started + (i + 1) * FRAME_SECONDS
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
    await websocket.send_json({"event": "checkpoint", "streamId": stream_id, "name": checkpoint_name})


@router.post("/vobiz/answer")
async def vobiz_answer(request: Request, k: str | None = None) -> Response:
    settings = get_settings()
    if not _check_key(settings, k):
        return Response(status_code=403)

    form = await request.form()
    call_uuid = str(form.get("CallUUID", ""))
    from_number = str(form.get("From", ""))
    to_number = str(form.get("To", ""))
    if call_uuid:
        _register_pending_call(call_uuid, from_number, to_number)
    logger.info("vobiz answer: call=%s from=%s to=%s", call_uuid, mask_phone(from_number), to_number)

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
    logger.info(
        "vobiz hangup: call=%s cause=%s", form.get("CallUUID", ""), form.get("HangupCause", ""),
    )
    return Response(status_code=200)


@router.post("/vobiz/stream-status")
async def vobiz_stream_status(request: Request, k: str | None = None) -> Response:
    settings = get_settings()
    if not _check_key(settings, k):
        return Response(status_code=403)
    form = await request.form()
    logger.info("vobiz stream-status: %s", dict(form))
    return Response(status_code=200)


@router.websocket("/vobiz/stream/{secret}")
async def vobiz_stream(websocket: WebSocket, secret: str) -> None:
    settings = get_settings()
    if not secrets.compare_digest(secret, settings.VOBIZ_STREAM_SECRET or ""):
        await websocket.close(code=4403)
        return
    await websocket.accept()

    call_id = "unknown"
    stream_id = "unknown"
    stt: SarvamStream | None = None
    logged_first_start = False

    async def on_transcript(event: str, text: str) -> None:
        if event == "transcript.final":
            logger.info("call %s: transcript.final: %s", call_id, text)
        else:
            logger.debug("call %s: transcript.partial: %s", call_id, text)

    try:
        while True:
            raw = await websocket.receive_json()
            event = raw.get("event")

            if event == "start":
                if not logged_first_start:
                    logger.info("call %s: raw start event: %s", call_id, raw)
                    logged_first_start = True
                extracted = _extract_stream_id(raw)
                call_id = _claim_pending_call(extracted)
                stream_id = extracted or call_id
                logger.info("call %s: stream started (stream_id=%s)", call_id, stream_id)

                stt = SarvamStream(settings, call_id, DEFAULT_LANGUAGE_CODE, on_transcript)
                await stt.open()

                if _greeting_audio:
                    await _send_paced_audio(websocket, stream_id, _greeting_audio, "greeting")
                else:
                    logger.warning("call %s: no greeting audio cached; skipping playback", call_id)

            elif event == "media":
                payload = raw.get("media", {}).get("payload")
                if payload and stt:
                    await stt.send_audio_b64(payload)

            elif event == "playedStream":
                logger.debug("call %s: playedStream %s", call_id, raw.get("name"))

            elif event == "clearedAudio":
                logger.debug("call %s: clearedAudio", call_id)

            elif event == "stop":
                # Not guaranteed by Vobiz — the WebSocketDisconnect below is the real signal.
                logger.info("call %s: stream stop event received", call_id)
                break

    except WebSocketDisconnect:
        logger.info("call %s: websocket disconnected", call_id)
    finally:
        if stt:
            await stt.close()
