"""
Phase 2 — Text-to-Speech module.

Backends
--------
gtts        Google TTS (gTTS). Free, no API key required.
            Arabic support via lang='ar'. Output: MP3.
            Use for development and cost minimisation during testing.

elevenlabs  ElevenLabs API. Premium Arabic voices (e.g. Chaouki).
            Requires API key + voice ID.  Long scripts are split at
            sentence boundaries into ≤4500-char requests and the MP3
            parts joined (ffmpeg concat when available, MPEG-frame
            byte-join fallback).  Cleaner narration also improves
            WhisperX alignment downstream (PHASE3.md §7.5/§7.10).
"""

from __future__ import annotations

import io
import logging
import re
from typing import Literal

log = logging.getLogger(__name__)

TtsBackend = Literal["gtts", "elevenlabs"]

# ElevenLabs request character budget.  eleven_multilingual_v2 accepts up
# to 10k chars on most paid tiers and 5k on others; 4500 keeps a healthy
# margin on every plan while splitting only genuinely long scripts (the
# typical 625-850-word Lamahat script is a single request).
_EL_CHUNK_CHARS = 4500

# Sentence-final punctuation for Arabic + Latin (chunk split points).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?؟…؛])\s+")

_EL_API = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
_EL_MODEL = "eleven_multilingual_v2"


def synthesize_gtts(text: str) -> bytes:
    """Synthesize Arabic text to MP3 via Google TTS. Returns MP3 bytes."""
    if not text.strip():
        raise ValueError("Text is empty — nothing to synthesize.")
    from gtts import gTTS  # lazy import — keeps app startup fast when unused

    buf = io.BytesIO()
    gTTS(text=text, lang="ar", slow=False).write_to_fp(buf)
    return buf.getvalue()


def _split_for_elevenlabs(text: str,
                          max_chars: int = _EL_CHUNK_CHARS) -> list[str]:
    """Split a script into ≤max_chars chunks at sentence boundaries.

    A single sentence longer than max_chars (pathological) is split at
    whitespace as a last resort — never mid-word.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks: list[str] = []
    cur = ""
    for sent in sentences:
        while len(sent) > max_chars:            # pathological single sentence
            cut = sent.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            piece, sent = sent[:cut], sent[cut:].lstrip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(piece)
        candidate = f"{cur} {sent}".strip() if cur else sent
        if len(candidate) > max_chars:
            chunks.append(cur)
            cur = sent
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def _join_mp3_parts(parts: list[bytes]) -> bytes:
    """Join MP3 parts into one stream.

    Preferred: ffmpeg concat demuxer with stream copy (correct headers,
    no re-encode).  Fallback: raw byte concatenation — MP3 is a frame
    stream, so decoders play joined frames correctly; only some seek
    bars misreport duration.
    """
    if len(parts) == 1:
        return parts[0]
    import shutil as _shutil
    import subprocess
    import tempfile
    from pathlib import Path

    if _shutil.which("ffmpeg"):
        try:
            with tempfile.TemporaryDirectory(prefix="lamahat_tts_") as td:
                tdir = Path(td)
                listing = []
                for i, blob in enumerate(parts):
                    p = tdir / f"part_{i:02d}.mp3"
                    p.write_bytes(blob)
                    listing.append(f"file '{p.as_posix()}'")
                (tdir / "list.txt").write_text("\n".join(listing),
                                               encoding="utf-8")
                out = tdir / "joined.mp3"
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error",
                     "-f", "concat", "-safe", "0",
                     "-i", str(tdir / "list.txt"), "-c", "copy", str(out)],
                    check=True)
                return out.read_bytes()
        except Exception as exc:  # noqa: BLE001 — fall through to byte join
            log.warning("ffmpeg concat of TTS parts failed (%s) — using "
                        "byte-join fallback", exc)
    return b"".join(parts)


def synthesize_elevenlabs(
    text: str,
    *,
    api_key: str,
    voice_id: str,
    model_id: str = _EL_MODEL,
    stability: float = 0.5,
    similarity_boost: float = 0.75,
) -> bytes:
    """Synthesize Arabic text to MP3 via the ElevenLabs REST API.

    Long scripts are split at sentence boundaries into ≤4500-char
    requests (`previous_text`/`next_text` keep the prosody continuous
    across chunk edges) and the parts are joined.
    """
    if not text.strip():
        raise ValueError("Text is empty — nothing to synthesize.")
    if not api_key:
        raise ValueError("elevenlabs_api_key is required for the ElevenLabs backend.")
    if not voice_id:
        raise ValueError("elevenlabs_voice_id is required for the ElevenLabs backend.")
    import requests  # noqa: PLC0415 — lazy; only the EL path needs it

    chunks = _split_for_elevenlabs(text)
    log.info("ElevenLabs: synthesizing %d chunk(s), voice=%s, model=%s",
             len(chunks), voice_id, model_id)

    parts: list[bytes] = []
    for i, chunk in enumerate(chunks):
        payload: dict = {
            "text": chunk,
            "model_id": model_id,
            "voice_settings": {
                "stability": stability,
                "similarity_boost": similarity_boost,
            },
        }
        # Prosody continuity across chunk boundaries (harmless when absent).
        if i > 0:
            payload["previous_text"] = chunks[i - 1][-300:]
        if i + 1 < len(chunks):
            payload["next_text"] = chunks[i + 1][:300]

        resp = requests.post(
            _EL_API.format(voice_id=voice_id),
            headers={"xi-api-key": api_key,
                     "Content-Type": "application/json"},
            json=payload,
            timeout=300,
        )
        if resp.status_code != 200:
            # Surface ElevenLabs' own error message — it names the real
            # problem (bad voice id, quota, text too long for the tier).
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:  # noqa: BLE001
                detail = resp.text[:300]
            raise RuntimeError(
                f"ElevenLabs API error {resp.status_code} on chunk "
                f"{i + 1}/{len(chunks)}: {detail}")
        parts.append(resp.content)

    return _join_mp3_parts(parts)


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
        return synthesize_elevenlabs(
            text,
            api_key=elevenlabs_api_key,
            voice_id=elevenlabs_voice_id,
        )

    raise ValueError(f"Unknown TTS backend: {backend!r}")
