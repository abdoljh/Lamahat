"""
Phase 2 — Text-to-Speech module.

Backends
--------
gtts        Google TTS (gTTS). Free, no API key required.
            Arabic support via lang='ar'. Output: MP3.
            Use for development and cost minimisation during testing.

elevenlabs  ElevenLabs API. Premium Arabic voices (e.g. Chaouki).
            Requires API key + voice ID.
"""

from __future__ import annotations

import io
from typing import Literal

TtsBackend = Literal["gtts", "elevenlabs"]


def synthesize_gtts(text: str) -> bytes:
    """Synthesize Arabic text to MP3 via Google TTS. Returns MP3 bytes."""
    if not text.strip():
        raise ValueError("Text is empty — nothing to synthesize.")
    from gtts import gTTS  # lazy import — keeps app startup fast when unused

    buf = io.BytesIO()
    gTTS(text=text, lang="ar", slow=False).write_to_fp(buf)
    return buf.getvalue()


def synthesize(
    text: str,
    backend: TtsBackend = "gtts",
    *,
    elevenlabs_api_key: str = "",
    elevenlabs_voice_id: str = "",
) -> bytes:
    """
    Synthesize Arabic script text to MP3.

    Parameters
    ----------
    text                  Arabic script (plain or diacritized).
    backend               'gtts' or 'elevenlabs'.
    elevenlabs_api_key    Required for 'elevenlabs' backend.
    elevenlabs_voice_id   Required for 'elevenlabs' backend.

    Returns
    -------
    bytes — MP3 audio.
    """
    if not text.strip():
        raise ValueError("Text is empty — nothing to synthesize.")

    if backend == "gtts":
        return synthesize_gtts(text)

    if backend == "elevenlabs":
        if not elevenlabs_api_key:
            raise ValueError("elevenlabs_api_key is required for the ElevenLabs backend.")
        if not elevenlabs_voice_id:
            raise ValueError("elevenlabs_voice_id is required for the ElevenLabs backend.")
        import requests  # noqa: PLC0415
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{elevenlabs_voice_id}",
            headers={
                "xi-api-key": elevenlabs_api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.content

    raise ValueError(f"Unknown TTS backend: {backend!r}")
