"""
Phase 3 — Forced alignment of TTS audio to script text.

Produces a flat list of WordTiming records: every Arabic word in the
script paired with its start/end time in the audio.

Why this exists
---------------
The Phase 2 TTS reads a *known* script.  We don't need ASR to discover
what was said — we already have the text.  What we need is to know
*when* each word is spoken.  This is the "forced alignment" problem
and it's solved by phoneme-level alignment models.

Backends (auto-selected in this order)
--------------------------------------
1. WhisperX with the Arabic wav2vec2 model
   (`jonatasgrosman/wav2vec2-large-xlsr-53-arabic`).
   Sub-100 ms accuracy.  CPU-only is workable for 3–5 min clips.

2. Whisper-only (without WhisperX's phoneme alignment).
   Word timings via Whisper's `word_timestamps=True`.
   Drift of 200–500 ms is typical, still useful.

3. Interpolation fallback.
   When no audio-aligning library is installed, distribute time across
   the script by character count.  Quality is poor but the pipeline
   keeps working.

The WhisperX/Whisper backends transcribe the audio independently and
align *their* transcript to *their* timing.  We then map the script's
known words onto that timeline by CONTENT — matching runs of script
tokens against ASR tokens and anchoring on them, interpolating only
inside the gaps between anchors (P7.15).

This used to be done by token ORDER alone ("order is much more
reliable"), with a proportional stretch whenever the counts differed.
That assumption is wrong in a way that gets worse the longer the film
runs: ASR errors are LOCAL (a dropped word here, two words merged
there) but proportional mapping is GLOBAL, so a single three-word drop
smears its error across the whole timeline — measured on a synthetic
200-token script, one 3-word deletion put every token out by up to 2 s,
including tokens BEFORE the error.  That is the "captions drift out of
sync with the narration partway through" failure.  Anchoring on matched
content confines each ASR error to its own gap.

Output
------
A WordTiming has:
  word    str   — the original script token (with diacritics)
  start   float — seconds from audio start
  end     float — seconds from audio start
  source  str   — "whisperx" | "whisper" | "interpolated"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# Arabic Unicode block matchers — these are what we count as "a word"
# in the script.  Latin tokens (e.g. dates, names quoted in Latin script)
# also count.
_ARABIC_WORD_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\w]+",
    re.UNICODE,
)


@dataclass
class WordTiming:
    word: str
    start: float
    end: float
    source: str = "interpolated"   # "whisperx" | "whisper" | "interpolated"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# ── Sentence / clause punctuation (P7.2) ────────────────────────────────── #
#
# The tokenizer's character class covers the Arabic block, so the Arabic
# marks ، ؛ ؟ ride INSIDE the token they follow ("وطنه،" is one token),
# while ASCII/latin marks (. ! : …) fall in the gap between tokens.
# `script_punct_flags` normalises both cases into one flag per token so
# the planner and the caption builder can see where sentences end.

STRONG_PUNCT = ".؟!…?"     # ends a sentence
WEAK_PUNCT = "،؛:,;"       # ends a clause


def token_trailing_punct(token: str, following: str = "") -> str:
    """The sentence/clause mark this token ends with ('' when none).

    Checks the token's own tail first (Arabic marks are part of the
    token), then the inter-token text `following` (ASCII marks are not).
    """
    tail = ""
    for ch in reversed(token):
        if ch in STRONG_PUNCT or ch in WEAK_PUNCT:
            tail = ch
        else:
            break
    if tail:
        return tail
    for ch in following:
        if ch in STRONG_PUNCT or ch in WEAK_PUNCT:
            return ch
    return ""


def script_punct_flags(script_text: str) -> list[str]:
    """One flag per tokenize_script() token: the punctuation mark that
    closes it ('' = mid-sentence).  Same length as tokenize_script()."""
    matches = list(_ARABIC_WORD_RE.finditer(script_text))
    flags: list[str] = []
    for i, m in enumerate(matches):
        nxt = matches[i + 1].start() if i + 1 < len(matches) else len(script_text)
        flags.append(token_trailing_punct(m.group(0), script_text[m.end():nxt]))
    return flags


# ── Public API ───────────────────────────────────────────────────────────── #

def tokenize_script(script_text: str) -> list[str]:
    """Tokenise the Arabic script into a flat list of word forms."""
    return _ARABIC_WORD_RE.findall(script_text)


def load_word_timings(path) -> list[WordTiming]:
    """Load precomputed word timings from a JSON file.

    Accepts the format `align`/`phase3_run.py --save-alignment` writes:
    a list of `{"word", "start", "end", "source"?}` objects.  Lets accurate
    timings be computed off-Cloud (e.g. WhisperX in Colab) and fed straight
    into planning/rendering without running ASR on Streamlit Cloud.
    """
    import json
    from pathlib import Path
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "words" in data:
        data = data["words"]
    out: list[WordTiming] = []
    for d in data:
        out.append(WordTiming(
            word=str(d.get("word", "")),
            start=float(d.get("start", 0.0)),
            end=float(d.get("end", 0.0)),
            source=str(d.get("source", "imported")),
        ))
    return out


def align(
    script_text: str,
    audio_path: Path,
    total_duration_sec: float,
    *,
    prefer_backend: str = "auto",
) -> list[WordTiming]:
    """
    Return one WordTiming per word in `script_text`.

    Parameters
    ----------
    script_text         The full script (any diacritisation).
    audio_path          MP3/WAV from Phase 2 TTS.  May be None for
                        interpolation-only mode.
    total_duration_sec  Authoritative total audio duration in seconds.
                        Used by the interpolation fallback and as a
                        sanity bound on aligner output.
    prefer_backend      "auto" | "whisperx" | "whisper" | "interpolated".
                        "auto" tries whisperx → whisper → interpolated.
    """
    tokens = tokenize_script(script_text)
    if not tokens:
        return []

    backends_to_try: list[str]
    if prefer_backend == "auto":
        backends_to_try = ["whisperx", "whisper", "interpolated"]
    else:
        backends_to_try = [prefer_backend]

    if audio_path is None:
        log.info("No audio path provided — using interpolation fallback")
        return _interpolate(tokens, total_duration_sec)
    if not Path(audio_path).exists():
        log.warning("Audio file not found at %s (cwd=%s) — using interpolation fallback",
                    audio_path, Path.cwd())
        return _interpolate(tokens, total_duration_sec)

    for backend in backends_to_try:
        try:
            if backend == "whisperx":
                timings = _align_whisperx(tokens, Path(audio_path))
            elif backend == "whisper":
                timings = _align_whisper(tokens, Path(audio_path))
            elif backend == "interpolated":
                timings = _interpolate(tokens, total_duration_sec)
            else:
                continue

            if timings:
                if backend == "interpolated":
                    log.warning(
                        "Using interpolated timings — character-rate "
                        "estimates only. For real word-level accuracy, "
                        "install whisperx (pip install whisperx) or "
                        "whisper (pip install openai-whisper)."
                    )
                else:
                    log.info("Aligned %d words via %s backend",
                             len(timings), backend)
                return timings
        except Exception as exc:
            log.warning("Backend %s failed: %s", backend, exc)
            continue

    # Last resort
    log.warning("All aligners failed — interpolating")
    return _interpolate(tokens, total_duration_sec)


# ── WhisperX backend ─────────────────────────────────────────────────────── #

def _align_whisperx(tokens: list[str], audio_path: Path) -> list[WordTiming]:
    """
    Use WhisperX with the Arabic phoneme alignment model for sub-100 ms
    word-level timestamps.

    Notes
    -----
    - WhisperX needs to load both Whisper itself (for transcription) and
      a phoneme model for alignment.  We pin the Arabic phoneme model
      explicitly because WhisperX's auto-detect picks an English model
      for any non-English language it doesn't know about.
    - Runs CPU-only.  Expect ~30–60 s for a 3-minute audio file.
    - Memory: ~600 MB RSS for the small Whisper model + Arabic w2v.
    """
    import whisperx   # type: ignore

    device = "cpu"
    compute_type = "int8"

    # Step 1 — transcribe (Whisper's own timestamps, segment-level)
    log.debug("WhisperX: loading Whisper small model")
    model = whisperx.load_model("small", device, compute_type=compute_type)
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, language="ar", batch_size=8)

    # Step 2 — phoneme-level forced alignment
    log.debug("WhisperX: loading Arabic alignment model")
    align_model, metadata = whisperx.load_align_model(
        language_code="ar",
        device=device,
        # The default arabic phoneme model on HuggingFace; WhisperX may
        # already know this string, but pinning is safer.
        model_name="jonatasgrosman/wav2vec2-large-xlsr-53-arabic",
    )
    aligned = whisperx.align(
        result["segments"], align_model, metadata, audio, device,
        return_char_alignments=False,
    )

    # Step 3 — flatten to (word, start, end) triples
    asr_words: list[tuple[str, float, float]] = []
    for seg in aligned.get("segments", []):
        for w in seg.get("words", []):
            text = (w.get("word") or "").strip()
            start = w.get("start")
            end = w.get("end")
            if text and start is not None and end is not None:
                asr_words.append((text, float(start), float(end)))

    if not asr_words:
        raise RuntimeError("WhisperX returned no aligned words")

    return _map_tokens_to_asr(tokens, asr_words, source="whisperx")


# ── Whisper-only backend ─────────────────────────────────────────────────── #

def _align_whisper(tokens: list[str], audio_path: Path) -> list[WordTiming]:
    """
    Use openai-whisper directly (no WhisperX).  Less accurate timestamps
    (Whisper interpolates word timings from its decoder), but doesn't
    require the phoneme model download.
    """
    import whisper   # type: ignore

    log.debug("Whisper: loading small model")
    model = whisper.load_model("small")
    result = model.transcribe(
        str(audio_path),
        language="ar",
        word_timestamps=True,
        verbose=False,
    )

    asr_words: list[tuple[str, float, float]] = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            text = (w.get("word") or "").strip()
            start = w.get("start")
            end = w.get("end")
            if text and start is not None and end is not None:
                asr_words.append((text, float(start), float(end)))

    if not asr_words:
        raise RuntimeError("Whisper returned no word timestamps")

    return _map_tokens_to_asr(tokens, asr_words, source="whisper")


# ── Interpolation fallback ──────────────────────────────────────────────── #

def _interpolate(tokens: list[str], total_duration_sec: float) -> list[WordTiming]:
    """
    Distribute total_duration_sec across tokens by character count.
    This is what the v1 pipeline does implicitly — but exposing it here
    lets the v2 planner consume the same WordTiming shape regardless of
    which backend was used.
    """
    if not tokens:
        return []

    lengths = [max(1, len(t)) for t in tokens]
    total_chars = sum(lengths)
    timings: list[WordTiming] = []
    cursor = 0.0
    for tok, n in zip(tokens, lengths):
        dur = total_duration_sec * n / total_chars
        timings.append(WordTiming(word=tok, start=cursor, end=cursor + dur,
                                  source="interpolated"))
        cursor += dur
    return timings


# ── Token → ASR word alignment ───────────────────────────────────────────── #

def _norm_for_match(token: str) -> str:
    """Fold a token to its bare comparison form (P7.15).

    Strips diacritics and punctuation and folds the orthographic
    variants that routinely differ between a written script and an ASR
    transcript, so an anchor isn't lost to a hamza seat or a final
    ta-marbuta.  Kept local to this module: `plan.py` imports `align`,
    so importing its twin from here would be circular.
    """
    import unicodedata
    t = unicodedata.normalize("NFKD", token)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^\w]", "", t, flags=re.UNICODE)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        t = t.replace(a, b)
    return t


def _map_tokens_to_asr(
    tokens: list[str],
    asr_words: list[tuple[str, float, float]],
    *,
    source: str,
) -> list[WordTiming]:
    """
    Pair each script token with its ASR word by CONTENT, not position.

    The script is the authoritative TEXT (it is what the audience must
    read); the ASR supplies only TIMING.  The job is therefore to find,
    for each script token, the moment it is actually spoken.

    Strategy — anchor, then interpolate inside gaps:

    1. Normalise both token streams for comparison (diacritics stripped,
       hamza/alef-maqsura/ta-marbuta folded) so a phonetic or
       orthographic wobble doesn't break a match.
    2. `difflib.SequenceMatcher` gives the matching blocks — long runs
       where script and ASR agree.  These are ANCHORS and take their
       ASR word's timing verbatim.
    3. Script tokens between two anchors are spread evenly across the
       time between those anchors.  An ASR error is therefore contained
       inside its own gap and cannot move anything outside it.
    4. Tokens before the first / after the last anchor extrapolate from
       the audio's own bounds.

    Why not the old proportional mapping: ASR errors are local, that
    mapping was global.  One 3-word deletion in a 200-token script put
    EVERY token out by up to 2 s, including tokens before the deletion.
    Anchoring holds drift to zero wherever script and ASR agree, which
    on TTS narration is the overwhelming majority of the film.
    """
    import difflib

    n_script = len(tokens)
    n_asr = len(asr_words)
    if n_script == 0 or n_asr == 0:
        return []

    a_norm = [_norm_for_match(t) for t in tokens]
    b_norm = [_norm_for_match(w) for w, _, _ in asr_words]

    # Fast path ONLY when the two streams genuinely say the same thing.
    # Equal LENGTH is not the same test and must never be used as one: a
    # transcript that drops four words and invents four others has the
    # identical count while every pairing after the first drop is wrong.
    # (Caught in testing, 2026-07-30 — a 4-drop/4-insert case scored the
    # same 1.28 s drift as the old proportional mapping because it took
    # this branch.)
    if n_script == n_asr and a_norm == b_norm:
        return [
            WordTiming(word=tok, start=s, end=e, source=source)
            for tok, (_, s, e) in zip(tokens, asr_words)
        ]
    sm = difflib.SequenceMatcher(a=a_norm, b=b_norm, autojunk=False)

    starts: list[float | None] = [None] * n_script
    ends: list[float | None] = [None] * n_script
    n_anchor = 0
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            i, j = blk.a + k, blk.b + k
            if i < n_script and j < n_asr:
                starts[i], ends[i] = asr_words[j][1], asr_words[j][2]
                n_anchor += 1

    audio_start = asr_words[0][1]
    audio_end = asr_words[-1][2]

    # Fill the gaps between anchors by even spread.
    anchored = [i for i in range(n_script) if starts[i] is not None]
    if not anchored:
        # Nothing matched at all — fall back to a single even spread over
        # the audio rather than pretending to know more than we do.
        log.warning("Alignment: no content anchors between script (%d) and "
                    "ASR (%d) — spreading evenly over the audio",
                    n_script, n_asr)
        step = (audio_end - audio_start) / max(n_script, 1)
        return [WordTiming(word=tok,
                           start=audio_start + i * step,
                           end=audio_start + (i + 1) * step,
                           source=source)
                for i, tok in enumerate(tokens)]

    def _fill(lo: int, hi: int, t0: float, t1: float) -> None:
        """Spread tokens lo..hi-1 evenly across [t0, t1]."""
        n = hi - lo
        if n <= 0:
            return
        span = max(t1 - t0, 0.0)
        step = span / n if n else 0.0
        for k in range(n):
            starts[lo + k] = t0 + k * step
            ends[lo + k] = t0 + (k + 1) * step

    first, last = anchored[0], anchored[-1]
    _fill(0, first, audio_start, starts[first])
    for x, y in zip(anchored, anchored[1:]):
        if y > x + 1:
            _fill(x + 1, y, ends[x], starts[y])
    _fill(last + 1, n_script, ends[last], audio_end)

    timings = [WordTiming(word=tok, start=float(starts[i]), end=float(ends[i]),
                          source=source)
               for i, tok in enumerate(tokens)]

    # Enforce monotonicity — a pathological anchor pair could otherwise
    # emit a caption that starts before the previous one ended.
    for i in range(1, n_script):
        if timings[i].start < timings[i - 1].start:
            timings[i].start = timings[i - 1].start
        if timings[i].end < timings[i].start:
            timings[i].end = timings[i].start

    pct = 100.0 * n_anchor / n_script
    msg = ("Alignment: %d script tokens vs %d ASR words — %d anchored on "
           "content (%.0f%%), rest interpolated inside gaps")
    if pct < 60.0:
        log.warning(msg + "; LOW anchor rate, captions may drift",
                    n_script, n_asr, n_anchor, pct)
    else:
        log.info(msg, n_script, n_asr, n_anchor, pct)
    return timings


# ── Convenience: bucket words into sections ─────────────────────────────── #

def assign_words_to_sections(
    word_timings: list[WordTiming],
    sections: list,        # list[ScriptSection] from parser.py
) -> dict[str, tuple[float, float, list[WordTiming]]]:
    """
    Group word timings by the script section they belong to.

    Returns
    -------
    dict[section_id, (section_start_sec, section_end_sec, [WordTiming, ...])]

    The section_start / section_end come from the first and last word's
    timestamps respectively — these are *measured* durations, far more
    accurate than the v1 estimate_durations() output.
    """
    # Build a flat list of words *per section* by re-tokenising each
    # section's text in order — the word_timings list mirrors that order.
    result: dict[str, tuple[float, float, list[WordTiming]]] = {}
    cursor = 0
    for section in sections:
        section_tokens = tokenize_script(section.text)
        n = len(section_tokens)
        if n == 0 or cursor >= len(word_timings):
            continue
        slice_ = word_timings[cursor:cursor + n]
        cursor += n
        if slice_:
            result[section.section_id] = (
                slice_[0].start,
                slice_[-1].end,
                slice_,
            )
    return result
