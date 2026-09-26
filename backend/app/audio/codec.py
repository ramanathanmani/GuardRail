"""μ-law audio framing for Vobiz `playAudio` (§5.2/§5.3). Vobiz's own docs recommend
~20-60ms per playAudio message; at 8kHz μ-law that's 160-480 bytes. We use fixed 20ms frames."""
from __future__ import annotations

from typing import Iterator

FRAME_BYTES = 160  # 20ms @ 8kHz mu-law, 1 byte/sample


def reframe(audio: bytes, frame_size: int = FRAME_BYTES) -> Iterator[bytes]:
    """Yield fixed-size frames, dropping a trailing partial frame rather than sending it short."""
    whole_frames = len(audio) // frame_size
    for i in range(whole_frames):
        offset = i * frame_size
        yield audio[offset : offset + frame_size]
