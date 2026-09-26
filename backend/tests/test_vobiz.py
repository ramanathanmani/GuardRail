"""Vobiz webhooks and stream auth (§5.3)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.telephony import vobiz_routes


@pytest.fixture
def client(monkeypatch, rt):
    s = get_settings()
    monkeypatch.setattr(s, "VOBIZ_STREAM_SECRET", "sec-test")
    monkeypatch.setattr(s, "PUBLIC_BASE_URL", "https://demo.example")
    app = FastAPI()
    app.include_router(vobiz_routes.router)
    return TestClient(app)


def test_wrong_key_403(client):
    assert client.post("/vobiz/answer?k=nope", data={"CallUUID": "c1"}).status_code == 403
    assert client.post("/vobiz/hangup?k=nope").status_code == 403


def test_answer_xml(client):
    r = client.post("/vobiz/answer?k=sec-test", data={"CallUUID": "c1", "From": "+919999999999", "To": "x"})
    assert r.status_code == 200
    assert 'keepCallAlive="true"' in r.text and 'bidirectional="true"' in r.text
    assert "wss://demo.example/vobiz/stream/sec-test" in r.text


def test_wrong_stream_secret_rejected(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/vobiz/stream/wrong") as ws:
            ws.receive_json()


@pytest.mark.parametrize("event", [
    {"event": "start", "start": {"callId": "abc", "streamId": "s1"}},
    {"event": "start", "start": {"streamId": "abc"}},
    {"event": "start", "callId": "abc"},
    {"event": "start", "streamId": "abc"},
    {"event": "start", "callUUID": "abc"},
    {"event": "start", "CallUUID": "abc"},
])
def test_start_parses_every_variant(event):
    ids = [vobiz_routes._extract_call_id(event), vobiz_routes._extract_stream_id(event)]
    assert "abc" in ids


def test_streamed_tts_is_reframed_to_160_byte_frames(monkeypatch):
    import asyncio
    import base64

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, msg):
            self.sent.append(msg)

    class FakeTts:
        async def stream(self, text):
            for size in (70, 300, 13, 257):  # 640 bytes, deliberately misaligned
                yield b"\x7f" * size

    monkeypatch.setattr(vobiz_routes, "_tts", FakeTts())
    monkeypatch.setattr(vobiz_routes, "PLAYED_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(vobiz_routes, "FRAME_SECONDS", 0.0)
    ws = FakeWS()
    t = vobiz_routes.VobizTransport(ws, "s1", "c1", get_settings())
    asyncio.run(t.say("a line that is not cached"))
    frames = [m for m in ws.sent if m["event"] == "playAudio"]
    assert len(frames) == 4
    assert all(len(base64.b64decode(m["media"]["payload"])) == 160 for m in frames)
    assert ws.sent[-1]["event"] == "checkpoint" and not t.speaking
