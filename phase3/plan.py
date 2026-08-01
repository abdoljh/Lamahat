"""
Phase 3 — Shot planner.

Takes:
  - The Arabic script (parsed sections)
  - Word-level timings from align.py
  - Book context (title, character, genre)

Produces:
  - A list of Shot records that fully specify the video, shot by shot.

The compositor (next session's work) executes shots without making any
creative decisions — all decisions live in the plan.

Design principles
-----------------
* ONE Claude call per video.  Sonnet 4.6 is the right model: it has the
  reasoning depth to make varied creative choices across 60–80 shots,
  and at ~$0.10/video the cost is negligible.
* The plan is JSON.  Inspectable, diffable, regeneratable.  When the
  output video is disappointing you debug the plan first, the renderer
  second.
* Word timings are the truth.  Shot boundaries always fall on word
  boundaries — never mid-word — so cuts feel intentional.
* Variety is mandated by the prompt.  We tell Sonnet explicitly that
  shots over 6 s without overlay text are forbidden in long-form, and
  that typography cards must account for ~25–35 % of shots.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

from .align import (STRONG_PUNCT, WEAK_PUNCT, WordTiming,
                    assign_words_to_sections, script_punct_flags,
                    tokenize_script)
from .parser import ScriptSection

log = logging.getLogger(__name__)


# ── Shot type taxonomy ───────────────────────────────────────────────────── #

ShotVisual = Literal[
    "portrait",       # primary subject's face — use static_hold or slow_push
    "location",       # places, landscapes, architecture
    "object",         # documents, weapons, artefacts, books
    "archive",        # period photographs, newspapers, maps
    "broll",          # generic atmospheric footage (Pexels)
    "typography",     # full-screen Arabic text card (no image)
    "title_card",     # opens the video: book title + author
    "section_mark",   # short interstitial naming the section
]

ShotMotion = Literal[
    "static_hold",    # no camera move (best for portraits, typography)
    "slow_push",      # 1.00 → 1.08 over the shot
    "fast_push",      # 1.00 → 1.20 (emphasis, 1.5–3 s shots)
    "slow_pull",      # 1.08 → 1.00 (reveal)
    "pan_left",       # horizontal pan
    "pan_right",
    "ken_burns",      # mixed zoom + pan (only for long landscapes)
]

TypographyTemplate = Literal[
    "pull_quote",     # large centred quote with quotation marks
    "name_reveal",    # "{character_name} —" with a date underneath
    "date_stamp",     # full-screen year/date in massive serif
    "chapter_heading", # section interstitial
]


@dataclass
class Shot:
    """A single shot in the video.  All time is in seconds from t=0."""

    # Timing
    start: float
    end: float

    # Visual selection
    visual: ShotVisual
    search_query: str = ""              # English query for the image source
    source_hint: str = "auto"           # "wikimedia" | "loc" | "pexels" | "auto"
    era: str = ""                       # period the image must depict, e.g.
                                        # "1900s-1910s Ottoman Mesopotamia";
                                        # "timeless" = modern stock acceptable;
                                        # "" = unspecified (legacy plans)

    # Motion
    motion: ShotMotion = "slow_push"
    motion_intensity: float = 1.0        # 0.5 = subtle, 1.5 = exaggerated

    # Typography (only for visual="typography")
    typography_template: TypographyTemplate | None = None
    typography_text: str = ""            # the Arabic text to display

    # Caption overlay (Arabic words spoken during this shot)
    caption_text: str = ""               # auto-filled by build_shot_plan
    show_caption: bool = True            # off for title_card / hero moments
    card_hides_captions: bool = True     # P7.9: False when a typography
                                         # card's quote is NOT what is being
                                         # spoken during its span — the card
                                         # stays a design element, but the
                                         # sentence track keeps running
                                         # underneath instead of going dark

    # Optional creative note from the planner (free-form, for debugging)
    note: str = ""

    # Section this shot belongs to (auto-filled)
    section_id: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class CaptionEvent:
    """One subtitle event, at SENTENCE granularity (P7.2).

    Caption events are independent of the shot grid: a sentence that spans
    a cut keeps one continuous subtitle instead of being chopped into
    per-shot fragments.  Built once from the word timings + script
    punctuation, stored in the v2 plan JSON, consumed by the renderer.

    `words` (P7.7): per-word (word, start, end) triples covering the same
    span as `text`.  When a typography-kind shot hides part of an event's
    time range, the renderer must burn only the words actually spoken
    during the VISIBLE remainder — reusing the full `text` for a clipped
    span was confirmed (screenshot audit, 2026-07-28) to burn a title
    card's own words into the next shot's caption.  Empty on events loaded
    from a plan saved before this field existed; the renderer falls back
    to the full `text` in that case.
    """
    start: float
    end: float
    text: str
    words: list = field(default_factory=list)


# Per-visual hard duration caps (seconds).  Shared by _validate_plan's
# runaway splitter and the clause snapper — a boundary nudge must never
# push a shot past its cap.  Typography needs reading time; portraits
# need contemplation time; archive/B-roll can rotate faster.
HARD_CAPS = {
    "typography":    8.0,
    "portrait":      8.0,
    "archive":       7.0,
    "broll":         7.0,
    "location":      7.0,
    "object":        7.0,
    "section_mark":  4.0,
    "title_card":    5.0,
}


# ── Prompt construction ─────────────────────────────────────────────────── #

_SYSTEM_PROMPT = """\
You are a documentary video editor planning the visual flow of an Arabic \
book-summary video.  You will receive the script, word-by-word audio \
timings, and book context.  Your job is to output a JSON shot list that \
makes the video genuinely cinematic.

Hard rules
----------
1.  Output JSON only.  No markdown, no commentary.
2.  Shot boundaries must fall on word boundaries — start/end times must \
    match one of the word timings provided.  CUT ON SENTENCES: the WORDS \
    lines preserve the script's punctuation — a word ending in . ؟ ! … \
    closes a sentence; a word ending in ، ؛ closes a clause.  Place every \
    shot boundary immediately AFTER a sentence-final word whenever the \
    duration limits allow it, and after a clause-final word otherwise.  \
    Splitting a sentence across two shots when it would fit inside one \
    shot's duration cap is a planning error: the audience hears one \
    thought — the picture must not cut in the middle of it.
3.  Shot duration limits by visual type:
    - title_card:  2.5 – 5.0 s
    - section_mark:  2.0 – 4.0 s  (section_marks are punctuation, not pages — \
      keep them brisk so the new chapter actually hooks the eye)
    - typography, portrait:  2.5 – 7.0 s (need reading / contemplation time)
    - archive, broll, location, object:  2.0 – 6.5 s
    Exceeding these limits is FORBIDDEN.  TARGET RANGE: aim for an \
    overall average of **3.5 – 4.5 s per shot**.  Most shots should sit \
    in the 3–4.5 s band, and you must DELIBERATELY VARY shot length to \
    create rhythm: place brisk 2–3 s beats (b-roll inserts, emphatic \
    lines, the first shots after a section_mark) next to the occasional \
    6 s hold.  Do NOT cluster every shot at the same length — uniform \
    pacing reads as a slideshow.  Only the most resonant typography pull- \
    quotes or portrait holds should reach 6–7 s, and even then sparingly. \
    The opening of each new section in particular must cut fast — \
    section_mark + the next 1–2 shots are the audience hook.
4.  The shot list must cover the entire script with no gaps and no \
    overlaps.  shot[i].end == shot[i+1].start.  The first shot must \
    start at 0.0, and the last shot must end at exactly the total \
    audio duration provided — not later.  Do not plan shots beyond \
    the audio.
5.  Each shot has exactly one `visual` type.  Use the full vocabulary — \
    avoid stacking 5 "location" shots in a row.  Aim for 25–35 % \
    typography shots, distributed across the video.
6.  Search queries are in English and source-aware.  The orchestrator \
    sends each query to Wikimedia Commons / Library of Congress (which \
    index named historical content) AND to Pexels (a stock-photo site \
    that keyword-matches user-submitted captions and has zero coverage \
    of named historical figures or specific events).  Write the query \
    so it works on BOTH:
    - Lead with a NAMED entity (person, event, place + year) if one \
      exists in the script — this makes Wikimedia/LoC succeed.
    - Then add VISUAL descriptors so Pexels can find a generic match \
      that's at least era-appropriate when the named query returns \
      nothing.
    Examples — BAD vs GOOD:
      BAD:  "Jafar al-Askari Iraqi officer portrait"
            → Pexels returns Iraqi Airways planes (matches "Iraqi")
      GOOD: "Jafar al-Askari Ottoman military officer mustache sepia \
             portrait early 1900s"
            → Wikimedia finds Jafar; Pexels finds period military portraits
      BAD:  "Sultan Abdulhamid II Ottoman portrait"
      GOOD: "Sultan Abdulhamid II Ottoman ruler bearded fez official \
             portrait sepia"
      BAD:  "Mosul Iraq 1904 historical photograph"
      GOOD: "Mosul Iraq Tigris river city 1900s sepia historical photo \
             stone buildings"
      BAD:  "Arab nationalist movement gathering"
            → Pexels returns Red Square parades
      GOOD: "Arab officers meeting early 1900s Ottoman uniform sepia \
             documentary historical photo"
    The named entity must come FIRST so Wikimedia matches; the visual \
    descriptors come AFTER as a Pexels safety net.
    For people, full names go first (e.g. "Jafar al-Askari" not "Iraqi \
    officer").  For dates, include both decade and year ("1900s 1904").
7.  Typography shots have `typography_text` filled with Arabic text \
    taken VERBATIM from the script — exact wording, including diacritics. \
    Do not summarise, condense, or paraphrase.  If a sentence is too \
    long for typography, pick a shorter contiguous sub-phrase from it. \
    No translation, no invention.  Pull quotes should be the most \
    resonant lines in the script — the lines that would make someone \
    stop scrolling.
8.  Motion choice matters:
    - `static_hold` for portraits and typography (let them land)
    - `slow_push` for most B-roll
    - `fast_push` only on emphasis beats (rare, ≤3 per video)
    - `pan_*` for landscapes and wide compositions
9.  `title_card` and `section_mark` shots are TYPOGRAPHY-ONLY: \
    fill `typography_text` with the relevant Arabic text and leave \
    `search_query` empty.  The renderer composes them from text + \
    designed background, not from an image search.
10. Always open with a `title_card` (3–5 s) showing the book title.
11. Optionally use `section_mark` shots between sections (2.5–5.0 s — brisk).
12. Every image shot (portrait, location, object, archive, broll) must \
    carry an `era` field stating the historical period the image has to \
    depict, derived from the script's chronology — e.g. \
    "1900s-1910s Ottoman Mesopotamia", "1916-1918 Arab Revolt", \
    "1920s-1930s Kingdom of Iraq".  Be specific: decade(s) + polity/region. \
    The image pipeline REJECTS candidates that don't plausibly belong to \
    this period, so an accurate era is what keeps modern stock footage out \
    of the film.  BUT a period label is expensive: archival coverage of \
    narrow periods is thin, and an over-restricted shot can end with no \
    image at all.  Reserve the period era for shots that SHOW the actual \
    person, place, or event ("this is Jafar", "this is 1904 Mosul").  For \
    thematic or emotional beats — determination, exile, study, loss — \
    prefer designing the shot as "timeless" b-roll from the start: desert \
    landscapes, hands writing, calligraphy, worn paper, textures, smoke, \
    abstract skies, silhouettes.  A well-chosen timeless metaphor beats a \
    failed archival search.  A shot naming a person, event, institution, \
    army, or city street is NEVER timeless.  Typography-kind shots leave \
    `era` empty.

Output schema
-------------
{
  "shots": [
    {
      "start": float,
      "end": float,
      "visual": "portrait" | "location" | "object" | "archive" | \
                "broll" | "typography" | "title_card" | "section_mark",
      "search_query": "English search terms, specific and named",
      "era": "period the image must depict, or 'timeless', or empty",
      "motion": "static_hold" | "slow_push" | "fast_push" | "slow_pull" |\
                "pan_left" | "pan_right" | "ken_burns",
      "motion_intensity": 1.0,
      "typography_template": null | "pull_quote" | "name_reveal" | \
                             "date_stamp" | "chapter_heading",
      "typography_text": "Arabic text or empty string",
      "show_caption": true | false,
      "note": "one-line creative reasoning, optional"
    }
  ]
}
"""

_USER_PROMPT_TMPL = """\
Book title: {book_title}
Main character / subject: {character_name}
Genre: {genre}
Total audio duration: {total_duration:.1f} seconds

Script sections (with word-level timings):
{sections_with_timings}

Notes for the planner:
- Open with a 4-second title_card showing the book title.
- TARGET SHOT COUNT: {target_shots} shots (≈ {avg_duration:.1f} s average). \
  This is a documentary, but the rhythm should hook the audience — \
  aim for the target rather than treating it as a ceiling.  Returning \
  fewer than {target_shots} shots IS acceptable only if the pacing \
  genuinely calls for slower holds; otherwise hit the target.  Cut \
  faster around section boundaries — the first 2–3 shots after each \
  section_mark should be on the shorter end of their per-type ranges \
  to re-engage the viewer.
- Typography shots: choose the most emotionally resonant lines from the \
  script — quotes that would stand alone as social-media graphics.
- This is a BIOGRAPHY: the protagonist's face anchors the film.  When a \
  main character is specified, plan 6–10 `portrait` shots of them spread \
  across the video — a curated portrait pool guarantees the imagery \
  exists, so never let generic b-roll displace the protagonist.  The \
  "prefer timeless metaphors" rule is for THEMATIC beats only; it must \
  not reduce the number of shots that show the actual people of the story.
- For portrait searches of "{character_name}", use the name in English \
  (e.g. "Jafar al-Askari portrait") plus any known historical context \
  from the script.
- For location searches, ground them: "Baghdad 1920 historical photo" \
  beats "Iraq".
- Avoid stacking same-visual shots: vary portrait → location → typography\
  → archive → location, etc.

Return JSON only.
"""


def _format_sections_for_prompt(
    sections: list[ScriptSection],
    section_word_map: dict[str, tuple[float, float, list[WordTiming]]],
) -> str:
    """
    Build a compact textual representation of each section with its
    word-level timings, suitable for inclusion in the planner prompt.

    Each section is rendered as:
        [section_id]  T=12.30–37.85s  (62 words)
        Arabic text body...
        Word timings: word@12.30 word@12.65 word@13.10 ...
    """
    lines: list[str] = []
    for section in sections:
        info = section_word_map.get(section.section_id)
        if not info:
            continue
        sec_start, sec_end, words = info
        lines.append(
            f"\n[{section.section_id}]  T={sec_start:.2f}–{sec_end:.2f}s  "
            f"({len(words)} words)"
        )
        # Cap section text at ~600 chars for prompt size control
        text_excerpt = section.text.strip()
        if len(text_excerpt) > 600:
            text_excerpt = text_excerpt[:600] + "…"
        lines.append(text_excerpt)
        # Word timings: include every word so the planner can pick exact
        # cut points.  Compact format: "word@1.23".  Punctuation is
        # restored onto each token (P7.2) so the planner can see where
        # sentences and clauses end — the Arabic marks ride inside the
        # tokens already; ASCII marks (.!…) come from the section text.
        flags = script_punct_flags(section.text)
        if len(flags) != len(words):
            flags = [""] * len(words)   # drift — ship tokens as-is
        timing_line = " ".join(
            f"{w.word}{f if f and not w.word.endswith(f) else ''}@{w.start:.2f}"
            for w, f in zip(words, flags))
        lines.append(f"WORDS: {timing_line}")

    return "\n".join(lines)


# ── Public API ───────────────────────────────────────────────────────────── #

def _sized_target_shots(total_duration_sec: float,
                        target_shot_duration: float) -> int:
    """
    Pick a reasonable shot count given total duration.

    Documentary pacing on biographical content runs ~3.5–5 s per shot;
    we aim a little brisk to build rhythm.  We scale linearly for short
    videos and sub-linearly past 3 minutes to keep the output token
    budget bounded.  Cap at 110 (≈10 min of content at the target pace).
    """
    if total_duration_sec <= 180:
        target = int(total_duration_sec / target_shot_duration)
    else:
        base = int(180 / target_shot_duration)
        extra = int((total_duration_sec - 180) / 4.0)
        target = base + extra
    return max(8, min(110, target))


def _extract_json_resilient(raw: str) -> dict:
    """
    Parse JSON from a Sonnet response that may be truncated or have
    trailing-comma artefacts.  Strategy:

    1. Try plain json.loads.
    2. If that fails, find the outermost "shots" array and parse shot
       objects one at a time, stopping at the first malformed one.
       This salvages an N-1 shot plan when Sonnet cut off mid-final-shot.
    """
    import re

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Locate "shots": [ … and walk objects manually.
    m = re.search(r'"shots"\s*:\s*\[', raw)
    if not m:
        raise ValueError("No 'shots' array found in response")
    body = raw[m.end():]

    # Walk the body tracking brace depth, extracting top-level shot
    # objects (depth 1 → 0 closes one shot).
    shots: list[dict] = []
    depth = 0
    in_string = False
    escape = False
    obj_start = -1

    for i, ch in enumerate(body):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                obj_text = body[obj_start:i + 1]
                try:
                    shots.append(json.loads(obj_text))
                except json.JSONDecodeError:
                    # First malformed object — stop here, return what we have
                    log.warning(
                        "Salvaged %d shot(s) before encountering "
                        "unparseable object at offset %d", len(shots), i)
                    break
                obj_start = -1
        elif ch == "]" and depth == 0:
            # Clean close of the shots array — done
            break

    if not shots:
        raise ValueError("Could not salvage any shots from response")
    return {"shots": shots}


def build_shot_plan(
    sections: list[ScriptSection],
    word_timings: list[WordTiming],
    *,
    book_title: str,
    character_name: str,
    genre: str,
    total_duration_sec: float,
    anthropic_api_key: str,
    target_shot_duration: float = 3.8,
    model: str = "claude-sonnet-4-6",
    debug_dir: Path | None = None,
) -> list[Shot]:
    """
    Generate a complete shot plan in a single Claude call.

    Parameters
    ----------
    sections             From parser.parse_sections().
    word_timings         From align.align().
    book_title           For prompt context and title card.
    character_name       For portrait queries and name_reveal cards.
    genre                Steers visual choices ("history" prefers archive
                         photographs; "philosophy" prefers typography).
    total_duration_sec   Authoritative total duration.
    anthropic_api_key    Required.
    target_shot_duration Desired average shot length.  Default 3.8 s sits
                         in the 3.5–4.5 s "documentary hook" band — fast
                         enough to keep the eye engaged and create rhythm,
                         while still letting the occasional typography pull-
                         quote or portrait breathe.  Lower → faster cutting.
    model                Sonnet 4.6 by default.  Override for testing.
    debug_dir            If provided, write the raw Sonnet response and
                         (on parse failure) the partial JSON to this
                         directory for inspection.
    """
    from anthropic import Anthropic

    section_word_map = assign_words_to_sections(word_timings, sections)
    target_shots = _sized_target_shots(total_duration_sec, target_shot_duration)

    user_prompt = _USER_PROMPT_TMPL.format(
        book_title=book_title or "unknown",
        character_name=character_name or "not specified",
        genre=genre,
        total_duration=total_duration_sec,
        sections_with_timings=_format_sections_for_prompt(
            sections, section_word_map),
        target_shots=target_shots,
        avg_duration=target_shot_duration,
    )

    log.info("Calling %s for shot plan (target=%d shots, %.1fs avg, %.1fs total)",
             model, target_shots, target_shot_duration, total_duration_sec)

    # Use streaming so very long responses don't timeout client-side and
    # so we get partial output even if the model hits a stop condition
    # before producing the final closing brackets.
    client = Anthropic(api_key=anthropic_api_key)
    raw_parts: list[str] = []
    with client.messages.stream(
        model=model,
        max_tokens=24000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        for text in stream.text_stream:
            raw_parts.append(text)
        final_message = stream.get_final_message()

    raw = "".join(raw_parts).strip()
    raw = (raw
           .removeprefix("```json")
           .removeprefix("```")
           .removesuffix("```")
           .strip())

    # Always dump the raw response if debug_dir is given — useful for
    # iterating on the prompt without re-running the API call.
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "planner_raw_response.txt").write_text(raw, encoding="utf-8")

    stop_reason = getattr(final_message, "stop_reason", None)
    if stop_reason and stop_reason != "end_turn":
        log.warning("Planner stopped early: stop_reason=%s — may need to "
                    "raise max_tokens or lower target_shots", stop_reason)

    try:
        data = _extract_json_resilient(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        # Always dump on failure so the user can inspect what Sonnet said
        fail_dir = debug_dir or Path("output")
        fail_dir.mkdir(parents=True, exist_ok=True)
        fail_path = fail_dir / "planner_raw_response_FAILED.txt"
        fail_path.write_text(raw, encoding="utf-8")
        log.error("Planner returned unparseable JSON (%s). "
                  "Raw response saved to %s", exc, fail_path)
        raise ValueError(
            f"Shot planner returned invalid JSON: {exc}.  "
            f"Raw response written to {fail_path}"
        ) from exc

    shots_data = data.get("shots", [])
    if not shots_data:
        raise ValueError("Shot planner returned no shots")

    shots = [_shot_from_dict(s) for s in shots_data]

    # Post-process: snap to word boundaries, nudge cuts onto sentence /
    # clause ends (P7.2), fill captions, assign sections
    shots = _snap_to_word_boundaries(shots, word_timings)
    shots = _snap_to_clause_boundaries(
        shots, word_timings, _global_punct_flags(sections, word_timings))
    shots = _assign_sections(shots, section_word_map)
    shots = _normalise_fields(shots)
    shots = _validate_plan(shots, total_duration_sec)
    # Put every typography card onto the words it quotes (P7.9).  Runs
    # after _validate_plan so it sees the final shot grid, and before
    # _fill_captions so each shot's caption_text reflects its real span.
    shots, _ = sync_typography_to_speech(shots, word_timings, sections=sections)
    shots = _fill_captions(shots, word_timings)

    log.info("Plan produced: %d shots", len(shots))
    return shots


# ── Shot construction & post-processing ─────────────────────────────────── #

def _shot_from_dict(d: dict) -> Shot:
    """Build a Shot from the planner's JSON dict, with defaulting."""
    return Shot(
        start=float(d.get("start", 0.0)),
        end=float(d.get("end", 0.0)),
        visual=d.get("visual", "broll"),
        search_query=d.get("search_query", "") or "",
        source_hint=d.get("source_hint", "auto") or "auto",
        era=d.get("era", "") or "",
        motion=d.get("motion", "slow_push"),
        motion_intensity=float(d.get("motion_intensity", 1.0)),
        typography_template=d.get("typography_template") or None,
        typography_text=d.get("typography_text", "") or "",
        caption_text=d.get("caption_text", "") or "",
        show_caption=bool(d.get("show_caption", True)),
        card_hides_captions=bool(d.get("card_hides_captions", True)),
        note=d.get("note", "") or "",
        section_id=d.get("section_id", "") or "",
    )


def _normalise_fields(shots: list[Shot]) -> list[Shot]:
    """
    Enforce field exclusivity rules.  Sonnet sometimes sets both
    typography_text and search_query on title cards; the renderer
    needs each shot to have exactly one source of visual content.

    Rules:
    - title_card, section_mark, typography → keep typography_text,
      clear search_query
    - all other visual types → keep search_query, clear typography_text
      and typography_template
    """
    TYPOGRAPHY_KINDS = {"title_card", "section_mark", "typography"}
    cleaned = 0
    for shot in shots:
        if shot.visual in TYPOGRAPHY_KINDS:
            if shot.search_query:
                shot.search_query = ""
                cleaned += 1
            shot.era = ""
        else:
            if shot.typography_text:
                shot.typography_text = ""
                shot.typography_template = None
                cleaned += 1
    if cleaned:
        log.info("Normalised %d shot(s) with conflicting fields", cleaned)
    return shots


def _snap_to_word_boundaries(
    shots: list[Shot],
    word_timings: list[WordTiming],
) -> list[Shot]:
    """
    Snap each shot's start/end to the nearest actual word boundary.
    Prevents cuts mid-syllable when the planner is slightly off.
    """
    if not word_timings:
        return shots

    word_starts = [w.start for w in word_timings]
    word_ends = [w.end for w in word_timings]

    def _snap(t: float, anchors: list[float]) -> float:
        # Linear scan is fine — a few hundred words at most
        return min(anchors, key=lambda a: abs(a - t))

    snapped: list[Shot] = []
    for shot in shots:
        s = _snap(shot.start, word_starts)
        e = _snap(shot.end, word_ends)
        if e <= s:
            e = s + 1.5  # minimum shot duration
        snapped.append(Shot(**{**asdict(shot), "start": s, "end": e}))
    return snapped


def _global_punct_flags(
    sections: list[ScriptSection],
    word_timings: list[WordTiming],
) -> list[str] | None:
    """One punctuation flag per global word timing, or None when the
    section texts don't line up with the timing stream (clause snapping
    and sentence captions then degrade gracefully)."""
    flags: list[str] = []
    for sec in sections:
        flags.extend(script_punct_flags(sec.text))
    if len(flags) != len(word_timings):
        log.warning(
            "Punctuation flags (%d) don't match word timings (%d) — "
            "sentence-aware processing falls back to token tails",
            len(flags), len(word_timings))
        return None
    return flags


def _snap_to_clause_boundaries(
    shots: list[Shot],
    word_timings: list[WordTiming],
    flags: list[str] | None,
) -> list[Shot]:
    """Nudge interior shot boundaries onto sentence/clause ends (P7.2).

    The planner is asked to cut on sentences, but it drifts — badly.
    Measured on real plans: 84 % of cuts landed mid-sentence before the
    prompt fix, and still 67 % after it (the first ±0.9 s version of
    this pass rescued almost nothing).  So this is now a HARD grid, not
    a nudge: every interior boundary is moved to the best sentence end
    within ±2.6 s (clause end within ±1.2 s as fallback), walked left to
    right so cuts stay monotonic, provided neither neighbour drops under
    1.6 s or exceeds its per-visual hard cap.  Sentences in this
    material end every ~5 s, so a qualifying anchor almost always
    exists; a boundary with no anchor keeps its word-end position.
    Cuts land in the natural speech pause after the punctuation — where
    a documentary editor puts them.
    """
    if not shots or not word_timings:
        return shots
    if flags is None:
        # No script text alignment — the Arabic marks (، ؛ ؟) survive
        # inside the tokens themselves, so fall back to token tails.
        from .align import token_trailing_punct
        flags = [token_trailing_punct(w.word) for w in word_timings]

    strong_ends = [w.end for w, f in zip(word_timings, flags)
                   if f and f in STRONG_PUNCT]
    weak_ends = [w.end for w, f in zip(word_timings, flags)
                 if f and f in WEAK_PUNCT]
    all_ends = strong_ends + weak_ends
    if not all_ends:
        return shots

    MIN_SHOT = 1.6
    STRONG_WIN = 2.6
    WEAK_WIN = 1.2
    moved = 0
    for i in range(len(shots) - 1):
        a, b = shots[i], shots[i + 1]
        t = a.end
        if any(abs(t - c) <= 0.05 for c in all_ends):
            continue    # already cutting on a clause end
        best = None
        for anchors, window in ((strong_ends, STRONG_WIN),
                                (weak_ends, WEAK_WIN)):
            cands = [
                c for c in anchors
                if abs(c - t) <= window
                and c - a.start >= MIN_SHOT
                and b.end - c >= MIN_SHOT
                and c - a.start <= HARD_CAPS.get(a.visual, 8.0)
                and b.end - c <= HARD_CAPS.get(b.visual, 8.0)
            ]
            if cands:
                best = min(cands, key=lambda c: abs(c - t))
                break
        if best is not None:
            a.end = best
            b.start = best
            moved += 1
    if moved:
        log.info("Snapped %d shot boundary(ies) onto sentence/clause ends",
                 moved)
    return shots


def build_caption_events(
    word_timings: list[WordTiming],
    sections: list[ScriptSection] | None = None,
    *,
    max_words: int = 12,
    weak_break_after: int = 6,
    hang: float = 0.35,
) -> list[CaptionEvent]:
    """Build sentence-level subtitle events from the word timings (P7.2).

    Events close at sentence-final punctuation, at clause punctuation
    once the line is ≥ `weak_break_after` words, or hard at `max_words`.
    Each event hangs `hang` seconds past its last word (never into the
    next event) so short lines don't blink.  The result is independent
    of the shot grid: a sentence spanning a cut stays one subtitle.
    """
    if not word_timings:
        return []
    flags = (_global_punct_flags(sections, word_timings)
             if sections is not None else None)
    if flags is None:
        from .align import token_trailing_punct
        flags = [token_trailing_punct(w.word) for w in word_timings]

    events: list[CaptionEvent] = []
    buf: list[WordTiming] = []
    for w, f in zip(word_timings, flags):
        buf.append(w)
        strong = bool(f) and f in STRONG_PUNCT
        weak = bool(f) and not strong
        if (strong
                or (weak and len(buf) >= weak_break_after)
                or len(buf) >= max_words):
            events.append(CaptionEvent(
                start=buf[0].start, end=buf[-1].end,
                text=" ".join(x.word for x in buf),
                words=[(x.word, x.start, x.end) for x in buf]))
            buf = []
    if buf:
        events.append(CaptionEvent(
            start=buf[0].start, end=buf[-1].end,
            text=" ".join(x.word for x in buf),
            words=[(x.word, x.start, x.end) for x in buf]))

    for i, ev in enumerate(events):
        limit = events[i + 1].start if i + 1 < len(events) else ev.end + hang
        ev.end = min(ev.end + hang, max(ev.end, limit))
    return events


def _fill_captions(
    shots: list[Shot],
    word_timings: list[WordTiming],
) -> list[Shot]:
    """Concatenate the Arabic words spoken during each shot into its caption."""
    for shot in shots:
        words_in_shot = [
            w.word for w in word_timings
            if w.start >= shot.start - 0.05 and w.end <= shot.end + 0.05
        ]
        shot.caption_text = " ".join(words_in_shot)
    return shots


def _assign_sections(
    shots: list[Shot],
    section_word_map: dict[str, tuple[float, float, list[WordTiming]]],
) -> list[Shot]:
    """Tag each shot with the section_id whose time range contains it."""
    section_ranges = [(sid, s, e) for sid, (s, e, _) in section_word_map.items()]
    for shot in shots:
        midpoint = (shot.start + shot.end) / 2
        for sid, s, e in section_ranges:
            if s <= midpoint <= e:
                shot.section_id = sid
                break
    return shots


# Opposite camera move for the odd pieces of an auto-split shot (P7.4).
_MOTION_FLIP = {
    "slow_push":   "slow_pull",
    "slow_pull":   "slow_push",
    "fast_push":   "slow_pull",
    "pan_left":    "pan_right",
    "pan_right":   "pan_left",
    "ken_burns":   "slow_push",
    "static_hold": "slow_push",
}


def _validate_plan(shots: list[Shot], total_duration_sec: float) -> list[Shot]:
    """
    Enforce structural invariants on the shot plan.

    - Sort by start time
    - Eliminate overlaps and gaps by snapping consecutive shots together
    - Trim or extend the final shot to cover the full audio
    - Clip any shots that extend beyond the audio
    - Split only shots that exceed 8.0 s (documentary shots routinely
      run 4–6 s; the 8 s ceiling is for true runaways).  Splits are
      annotated but not duplicated — the renderer can vary motion
      across pieces of a split shot to keep the eye engaged.
    """
    if not shots:
        return shots

    shots = sorted(shots, key=lambda s: s.start)

    # 1. Drop or clip shots that start beyond the audio
    in_bounds: list[Shot] = []
    for shot in shots:
        if shot.start >= total_duration_sec:
            log.warning("Dropping shot at %.2fs (audio ends at %.2fs)",
                        shot.start, total_duration_sec)
            continue
        if shot.end > total_duration_sec:
            log.info("Clipping shot %.2f-%.2fs to %.2fs (audio end)",
                     shot.start, shot.end, total_duration_sec)
            shot = Shot(**{**asdict(shot), "end": total_duration_sec})
        in_bounds.append(shot)
    shots = in_bounds
    if not shots:
        return shots

    # 2. Stitch into a contiguous timeline
    fixed: list[Shot] = []
    for shot in shots:
        if fixed:
            prev_end = fixed[-1].end
            if shot.start != prev_end:
                shot = Shot(**{**asdict(shot), "start": prev_end})
            if shot.end <= shot.start:
                shot = Shot(**{**asdict(shot), "end": shot.start + 2.0})
        fixed.append(shot)

    # 3. Pin the last shot to the audio end
    fixed[-1] = Shot(**{**asdict(fixed[-1]), "end": total_duration_sec})

    # 4. Split only TRUE runaways.  Caps are visual-type aware (module-
    #    level HARD_CAPS, shared with the clause snapper).
    TARGET_PIECE = 3.5
    # Floating-point tolerance — shots within 0.1s of the cap are kept
    # intact rather than micro-split.
    TOLERANCE = 0.1
    final: list[Shot] = []
    for shot in fixed:
        cap = HARD_CAPS.get(shot.visual, 8.0)
        if shot.duration <= cap + TOLERANCE:
            final.append(shot)
            continue
        n_pieces = max(2, int(round(shot.duration / TARGET_PIECE)))
        piece_dur = shot.duration / n_pieces
        for k in range(n_pieces):
            piece = Shot(**asdict(shot))
            piece.start = shot.start + k * piece_dur
            piece.end = shot.start + (k + 1) * piece_dur
            # Alternate the camera direction across pieces (P7.4): the
            # same image restarting the same zoom every 3.5 s reads as a
            # stutter; push → pull → push reads as an edited sequence.
            if k % 2 == 1:
                piece.motion = _MOTION_FLIP.get(piece.motion, "slow_pull")
            piece.note = (piece.note +
                          f" [auto-split {k + 1}/{n_pieces}]").strip()
            final.append(piece)

    # 5. Merge adjacent shots with identical content.
    #    Sometimes the splitter, or the planner itself, produces back-
    #    to-back shots that show the same thing — e.g. two 4.1s archive
    #    shots with the same search_query.  Documentary practice says
    #    those should be one 8.2s shot, not a frame-accurate edit
    #    point with no visual change.  Merging here also consolidates
    #    the caption windows so subtitles don't cut in/out at the
    #    invisible boundary.
    merged: list[Shot] = []
    for shot in final:
        if merged and _shots_can_merge(merged[-1], shot):
            prev = merged[-1]
            # Build the merged shot.  Use the earlier shot's metadata;
            # extend its end time; concatenate the caption text.
            combined = Shot(**asdict(prev))
            combined.end = shot.end
            # Merge captions — only when both are non-empty and differ
            if shot.caption_text and shot.caption_text != prev.caption_text:
                combined.caption_text = (
                    f"{prev.caption_text} {shot.caption_text}".strip()
                )
            merged[-1] = combined
            log.debug("Merged adjacent identical shots: %s [%.2fs]",
                      shot.visual, combined.duration)
        else:
            merged.append(shot)

    if len(merged) != len(final):
        log.info("Merged %d adjacent identical shot(s) — final plan %d shots",
                 len(final) - len(merged), len(merged))
    return merged


def _shots_can_merge(a: Shot, b: Shot) -> bool:
    """
    Return True iff two adjacent shots are identical enough to merge.

    Criteria: same visual type, same source (search_query for image
    shots, typography_text for typography shots).  Also require that
    they're temporally adjacent (b.start == a.end within 0.05s) and that
    the merged shot would respect the visual's hard duration cap —
    without the cap check, the merge pass silently re-joined the pieces
    the runaway splitter had just produced (same visual, same query),
    undoing every auto-split.
    """
    if abs(b.start - a.end) > 0.05:
        return False
    if a.visual != b.visual:
        return False
    if (b.end - a.start) > HARD_CAPS.get(a.visual, 8.0) + 0.1:
        return False
    # Image-kind shots share when their search_query is identical
    if a.search_query or b.search_query:
        if a.search_query != b.search_query:
            return False
    # Typography-kind shots share when their text is identical
    if a.typography_text or b.typography_text:
        if a.typography_text != b.typography_text:
            return False
    return True


# ── Serialisation ────────────────────────────────────────────────────────── #

def shots_to_json(shots: list[Shot]) -> str:
    """Serialise the plan as pretty JSON for inspection or caching."""
    return json.dumps(
        [asdict(s) for s in shots],
        ensure_ascii=False,
        indent=2,
    )


def shots_from_json(text: str) -> list[Shot]:
    """Deserialise a plan from JSON (round-trip safe with shots_to_json).

    Accepts both the legacy bare-list format and the v2 envelope
    ({"version": 2, "shots": [...], "captions": [...]}).
    """
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("shots", [])
    return [_shot_from_dict(d) for d in data]


def save_plan(shots: list[Shot], path: Path,
              caption_events: "list[CaptionEvent] | None" = None) -> Path:
    """Write the plan to disk; returns the path.

    With `caption_events`, the file uses the v2 envelope so the
    sentence-level subtitle track travels with the plan; without, the
    legacy bare-list format is kept (older tooling reads it unchanged).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if caption_events:
        payload = {
            "version": 2,
            "shots": [asdict(s) for s in shots],
            "captions": [asdict(e) for e in caption_events],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    else:
        path.write_text(shots_to_json(shots), encoding="utf-8")
    return path


def load_plan(path: Path | str) -> list[Shot]:
    """Load a previously-saved plan (legacy list or v2 envelope)."""
    return shots_from_json(Path(path).read_text(encoding="utf-8"))


def load_caption_events(path: Path | str) -> list[CaptionEvent]:
    """Sentence-level caption events from a v2 plan JSON ([] for legacy)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    events = []
    for d in data.get("captions", []) or []:
        try:
            words = [(str(w[0]), float(w[1]), float(w[2]))
                     for w in (d.get("words") or [])]
            events.append(CaptionEvent(start=float(d["start"]),
                                       end=float(d["end"]),
                                       text=str(d.get("text", "")),
                                       words=words))
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return events


def _attach_words_from_timings(
    events: list[CaptionEvent],
    word_timings: list[WordTiming],
) -> int:
    """Fill in missing per-word timing on caption events, in place.

    Returns the number of events repaired.  Conservative by design: an
    event only gains `words` when the words falling inside its own time
    range join back to EXACTLY its stored text.  A mismatch (different
    narration, hand-edited caption text) leaves that event untouched, so
    the renderer keeps its old whole-text behaviour rather than burning
    something that was never said.
    """
    if not events or not word_timings:
        return 0
    eps = 0.05
    repaired = 0
    for ev in events:
        if ev.words:
            continue
        inside = [w for w in word_timings
                  if w.start >= ev.start - eps and w.end <= ev.end + eps]
        if not inside:
            continue
        if " ".join(w.word for w in inside).strip() != (ev.text or "").strip():
            log.warning(
                "caption repair: words in %.2f–%.2fs don't reproduce the "
                "stored text — left unrepaired", ev.start, ev.end)
            continue
        ev.words = [(w.word, w.start, w.end) for w in inside]
        repaired += 1
    return repaired


# ── Typography/speech synchronisation (P7.9) ─────────────────────────────── #

# Visual kinds that display their own on-screen text.  Mirrors
# render.TYPOGRAPHY_VISUALS (duplicated here to keep plan.py import-free
# of the renderer).
_TYPO_VISUALS = {"title_card", "section_mark", "chapter_heading", "typography"}

# Minimum on-screen time, per kind, when boundaries are being reflowed.
_MIN_DUR = {"section_mark": 1.8, "title_card": 2.5}
_MIN_DUR_DEFAULT = 1.6


def _norm_tokens(text: str) -> list[str]:
    """Normalise Arabic text to bare comparison tokens.

    Strips diacritics and punctuation and folds the orthographic variants
    that differ between a written card and an ASR transcript (hamza
    carriers, alef maqsura, ta marbuta) so a card can be matched against
    the narration it quotes.
    """
    import unicodedata
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        text = text.replace(a, b)
    return [t for t in text.split() if t]


def _locate_card(card: list[str], narration: list[str],
                 min_cover: float = 0.6) -> tuple[int, int] | None:
    """Find where a card's text is spoken; (first, last) word index or None.

    Fuzzy on purpose: a pull quote is usually a lightly-edited excerpt of
    the narration (dropped connective, added full stop), so an exact
    subsequence test finds far too few.  Requires `min_cover` of the
    card's tokens to match before it will claim a location.
    """
    import difflib
    if not card or not narration:
        return None
    sm = difflib.SequenceMatcher(a=narration, b=card, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return None
    if sum(b.size for b in blocks) < min_cover * len(card):
        return None
    lo = min(b.a for b in blocks)
    hi = max(b.a + b.size for b in blocks) - 1
    # Reject a DIFFUSE match.  A section heading like "الصراع الأيديولوجي
    # والسياسي — بين الولاء والحلم" joins two phrases the narration keeps
    # seconds apart; matching the first and last of them spans everything
    # in between and would stretch the card across half a minute.  A real
    # quote occupies roughly its own length in the narration.
    if (hi - lo + 1) > 1.5 * len(card) + 2:
        return None
    return lo, hi


def sync_typography_to_speech(
    shots: list[Shot],
    word_timings: list[WordTiming],
    *,
    sections: "list[ScriptSection] | None" = None,
    tolerance: float = 0.35,
) -> tuple[list[Shot], dict]:
    """Make every typography card agree with the voice under it (P7.9).

    The planner picks a resonant line and places a card NEAR that beat,
    not ON it.  Measured on a real 84-shot plan, only 4 of 33 cards sat
    within 0.5 s of the words they quote (median drift 3.1 s).  Because a
    card also suppresses the subtitle track, a drifted card shows
    sentence A in silence while the narrator speaks sentence B and the
    audience gets neither.  That is the root cause behind the
    duplicated-title and repeated-phrase reports — clipping captions
    only treated the symptom.

    One bounded repair, plus a fallback that changes no text:

    * **trim** — the card's words fall inside its own shot, so the shot
      is tightened onto them and the freed time is handed to the
      IMMEDIATE neighbour (never a global reflow: stretching the whole
      timeline pushed 13 image shots past `HARD_CAPS` and reintroduced
      the static-hold problem).
    * **uncover** — the card's words are somewhere else entirely (or the
      match is too diffuse to trust — this includes a card split across
      two auto-split shots, whose combined text never fits inside either
      piece).  An earlier version of this pass REWROTE the card to the
      words spoken under it; screened against a real cut, that read as
      a regression — a bold pull quote ("انقلابات ومكائد تحاك في كل
      زاوية.") shrank to a thin, syntactically broken line ("ومصالح
      شخصية تعترض طريق الإصلاح الحقيقي كانت هذه فترة من") because the
      auto-fit shrinks longer text, so the typographic punctuation the
      card exists for was lost at exactly the beats meant to carry the
      most weight.  A split card's two pieces were also retexted
      independently, turning one heading into two unrelated fragments
      ("الصراع" / "والسياسي بين").  Now the card's text is left
      completely untouched and `card_hides_captions` is set False
      instead: the design stays on screen and the sentence track keeps
      running underneath it, so the audience gets both instead of a
      damaged version of one.  Only fires when the card's words are
      genuinely NOT what's being said during its span.  Two screenings
      found two different ways a card's own quote can still end up
      duplicated by the caption track it no longer suppresses:
      (i) a card whose located match merely overran its shot by a
      second or two (the sentence continues into a neighbour) — first
      caught 2026-07-29, checked by comparing the located match's own
      timing against the shot's bounds; (ii) a card that fully or
      mostly transcribes ONE sentence-level `CaptionEvent` whose own
      time range only starts (or only ends) inside the card's shot —
      that event is never CLIPPED against this shot (clipping only
      happens at a HIDDEN typography boundary, and this shot no longer
      counts as one), so it burns in full wherever it overlaps, which
      is the card's own words repeated verbatim underneath it — caught
      2026-07-30 from a real render ("بين دفتي هذا الكتاب ستجد نقاشا
      صريحا..." as both the card and, seconds later inside the same
      shot, the caption).  A located-word check like (i) cannot see
      (ii): the event in that case starts, correctly, in the words
      right after where the card's fuzzy match is anchored, so the
      simple "is the match inside the shot" test scored it as safely
      displaced.  `_max_event_overlap` below checks the thing that
      actually renders — the sentence events overlapping the shot — not
      an approximate located span, so it catches both.  A card is left
      hidden whenever ANY overlapping event shares at least half of the
      SMALLER of {card, event} in tokens, on the reasoning that a
      slightly-early or slightly-late caption is a smaller sin than
      showing the audience the same words twice at once.

    A card split across two auto-split shots (same text, adjacent, for
    motion variety — see `_shots_can_merge`) is judged and decided as
    ONE card over its combined span, not per piece: judging halves
    independently could hide one piece's captions and uncover the
    other's for what the audience reads as a single heading.

    Shot count and order are preserved, so a review dossier keyed by shot
    index stays valid — safe to run before a render-only pass.
    Returns `(shots, report)`.
    """
    if not shots or not word_timings:
        return shots, {"trimmed": 0, "uncovered": 0, "n_cards": 0}

    narration = [(_norm_tokens(w.word) or [""])[0] for w in word_timings]
    # Recompute `card_hides_captions` from scratch every run.  This pass
    # only ever CLEARS the flag, so without resetting it first a plan
    # carried across code revisions keeps a stale False forever and the
    # re-run silently cannot repair it — confirmed against a real
    # dossier (2026-07-30): two cards marked uncovered by the P7.9-era
    # logic still had their line captioned right after the card, at
    # 139.43 s and 365.61 s, on a plan whose sync reported "0 uncovered".
    out = [Shot(**{**asdict(s), "card_hides_captions": True}) for s in shots]
    n = len(out)
    trimmed: list[int] = []
    uncovered: list[int] = []
    n_cards = 0

    # Group split-card runs so they are judged (and decided) as one card.
    split_group: dict[int, list[int]] = {}
    seen: set[int] = set()
    for i, s in enumerate(out):
        if i in seen or s.visual not in _TYPO_VISUALS or not (s.typography_text or "").strip():
            continue
        run = [i]
        j = i + 1
        while (j < n and out[j].visual == s.visual
               and (out[j].typography_text or "") == (s.typography_text or "")):
            run.append(j)
            j += 1
        for k in run:
            split_group[k] = run
        seen.update(run)

    def _min_dur(k: int) -> float:
        return _MIN_DUR.get(out[k].visual, _MIN_DUR_DEFAULT)

    # The actual sentence-caption track the renderer will burn — matches
    # render-time segmentation exactly when `sections` is available.
    events = build_caption_events(word_timings, sections)

    def _max_event_overlap(card: list[str], span: tuple[float, float]) -> float:
        """Largest share of {card, event} (whichever is smaller) shared
        with any CaptionEvent whose time range overlaps `span` — i.e. any
        event the renderer will actually show while this card is on
        screen, clipped or not."""
        if not card:
            return 0.0
        s0, s1 = span
        card_set = set(card)
        best = 0.0
        for ev in events:
            if ev.end <= s0 or ev.start >= s1:
                continue
            ev_set = set(_norm_tokens(ev.text))
            if not ev_set:
                continue
            shared = len(card_set & ev_set)
            denom = min(len(card_set), len(ev_set))
            if denom:
                best = max(best, shared / denom)
        return best

    for i, s in enumerate(out):
        if s.visual not in _TYPO_VISUALS:
            continue
        card = _norm_tokens(s.typography_text or "")
        if not card:
            continue
        n_cards += 1
        hit = _locate_card(card, narration)
        want = (None if hit is None
                else (word_timings[hit[0]].start,
                      word_timings[min(hit[1], len(word_timings) - 1)].end))

        # Case 1 — the card's line overlaps this shot: tighten the shot
        # onto the overlap.  Because the renderer silences the caption
        # track for a card's WHOLE on-screen span (P7.10 — anything less
        # puts a second block of text under a full-screen typography
        # card, which screening flagged on 15 separate shots), every
        # second a card holds beyond its own line is a second of
        # narration the audience can hear but not read.  Shrinking the
        # card to its line hands that time back to the neighbouring
        # image shots, where captions run normally.  A neighbour is
        # therefore allowed to absorb the time past its `HARD_CAPS`
        # ceiling — a slightly long b-roll beat costs far less than a
        # deleted clause.
        overlap0 = max(want[0], s.start) if want else 0.0
        overlap1 = min(want[1], s.end) if want else 0.0
        if want and overlap1 - overlap0 >= 1.5:
            new_start, new_end = overlap0, overlap1
            head = new_start - s.start          # time handed back
            tail = s.end - new_end              # time handed forward
            CAP_SLACK = 1.5
            # Absorb as much as each neighbour has room for.  All-or-
            # nothing here was the single biggest remaining source of
            # unreadable narration: shot 9 needed to hand over 2.12 s to
            # a neighbour with 2.10 s of room and so handed over nothing,
            # keeping its whole tail clause off screen.
            if head > 0.05:
                take = 0.0
                if i > 0:
                    prev = out[i - 1]
                    room = (HARD_CAPS.get(prev.visual, 8.0) + CAP_SLACK
                            - prev.duration)
                    take = min(head, max(0.0, room))
                    if take > 0.05:
                        out[i - 1] = Shot(**{**asdict(prev),
                                             "end": s.start + take})
                new_start = s.start + take
            if tail > 0.05:
                take = 0.0
                if i + 1 < n:
                    nxt = out[i + 1]
                    room = (HARD_CAPS.get(nxt.visual, 8.0) + CAP_SLACK
                            - nxt.duration)
                    take = min(tail, max(0.0, room))
                    if take > 0.05:
                        out[i + 1] = Shot(**{**asdict(nxt),
                                             "start": s.end - take})
                new_end = s.end - take
            if abs(new_start - s.start) > 0.05 or abs(new_end - s.end) > 0.05:
                out[i] = Shot(**{**asdict(s), "start": new_start, "end": new_end})
                trimmed.append(i)
            continue

        # Case 1b — the card is DISPLACED: its line is spoken outside
        # this shot entirely, so every second it holds is narration the
        # audience hears with nothing readable on screen (the card owns
        # the frame, P7.10).  We cannot move it onto its line without
        # re-cutting the film, but we can stop it squatting: shrink it
        # to the minimum readable hold and hand the rest to a
        # neighbouring image shot, where captions run normally.
        if want and len(split_group.get(i, [i])) == 1:
            floor = _MIN_DUR.get(s.visual, _MIN_DUR_DEFAULT)
            excess = s.duration - floor
            if excess > 0.4:
                CAP_SLACK = 1.5
                # The card's line lies after this shot → hold the card as
                # LATE as possible (shrink its head, the previous image
                # shot absorbs).  Line lies before → hold it as EARLY as
                # possible (shrink its tail, the next shot absorbs).
                # Either way the card ends up nearer its own words and
                # covers less unrelated narration.
                # Absorb as much as the neighbour has room for rather
                # than refusing outright — a partial shrink still hands
                # real seconds back to a captioned shot, and all-or-
                # nothing was rejecting fixes that missed the ceiling by
                # fractions (shot 51 needed 9.69 s against a 9.5 s
                # limit, and its whole clause stayed unreadable).
                # Compare CENTRES, not edges.  `want[0] >= s.end` was too
                # strict: a line that starts just inside the shot and runs
                # well past it is centred after the card, but failed the
                # test and got shrunk from the tail — moving the card
                # AWAY from its own words instead of toward them.
                line_is_after = ((want[0] + want[1]) / 2.0
                                 >= (s.start + s.end) / 2.0)
                if line_is_after and i > 0:
                    prev = out[i - 1]
                    room = (HARD_CAPS.get(prev.visual, 8.0) + CAP_SLACK
                            - prev.duration)
                    take = min(excess, max(0.0, room))
                    if take > 0.4:
                        cut = s.start + take
                        out[i - 1] = Shot(**{**asdict(prev), "end": cut})
                        out[i] = Shot(**{**asdict(s), "start": cut})
                        trimmed.append(i)
                        continue
                elif not line_is_after and i + 1 < n:
                    nxt = out[i + 1]
                    room = (HARD_CAPS.get(nxt.visual, 8.0) + CAP_SLACK
                            - nxt.duration)
                    take = min(excess, max(0.0, room))
                    if take > 0.4:
                        cut = s.end - take
                        out[i] = Shot(**{**asdict(s), "end": cut})
                        out[i + 1] = Shot(**{**asdict(nxt), "start": cut})
                        trimmed.append(i)
                        continue

        # Case 2 — the card missed the trim window.  Judge it (and, if
        # split, its whole run) against every sentence event overlapping
        # its span — not just the located match — since an overlapping
        # event burns in full once this shot stops hiding captions,
        # regardless of how the located-match test alone would have
        # scored it.  >=50% shared with any one event means uncovering
        # would put a near-duplicate of the card's own words on screen —
        # worse than leaving it hidden.
        run = split_group.get(i, [i])
        run_span = (out[run[0]].start, out[run[-1]].end)
        if _max_event_overlap(card, run_span) >= 0.5:
            continue
        if s.card_hides_captions:
            out[i] = Shot(**{**asdict(s), "card_hides_captions": False})
            uncovered.append(i)

    # Re-stitch: only neighbours were touched, so this is a no-op check.
    for k in range(1, n):
        if abs(out[k].start - out[k - 1].end) > 1e-6:
            out[k] = Shot(**{**asdict(out[k]), "start": out[k - 1].end})

    log.info("typography sync: %d card(s) trimmed onto their words, "
             "%d left as designed but uncovering the caption track "
             "beneath them (%d cards total)",
             len(trimmed), len(uncovered), n_cards)
    return out, {"trimmed": len(trimmed), "uncovered": len(uncovered),
                 "n_cards": n_cards,
                 "trimmed_idx": trimmed, "uncovered_idx": uncovered}


def repair_caption_events(
    events: list[CaptionEvent],
    plan_path: Path | str,
    review_dir: Path | str | None = None,
) -> list[CaptionEvent]:
    """Self-heal caption events saved before `CaptionEvent.words` existed.

    Plans written before P7.7 (commit 873f9d8) carry aggregate
    start/end/text only.  Without per-word timing the renderer cannot
    reconstruct the text of a span that a hidden typography card clips,
    so it falls back to burning the WHOLE sentence — which is how a
    title card's own words end up glued onto the next shot's caption.

    Rather than require the curator to regenerate the plan (and to guess
    WHICH of the dossier's several `shot_plan.json` copies the renderer
    will actually read), repair it here at load time from whichever
    `word_timings.json` sits beside the plan or inside the dossier.
    Returns the same list, repaired in place where possible.
    """
    if not events or all(ev.words for ev in events):
        return events

    plan_path = Path(plan_path)
    candidates = [plan_path.parent / "word_timings.json",
                  plan_path.parent / "review" / "word_timings.json"]
    if review_dir:
        candidates.insert(0, Path(review_dir) / "word_timings.json")

    for cand in candidates:
        if not cand.exists():
            continue
        try:
            from .align import load_word_timings
            timings = load_word_timings(cand)
        except Exception as exc:  # noqa: BLE001
            log.warning("caption repair: could not read %s (%s)", cand, exc)
            continue
        n = _attach_words_from_timings(events, timings)
        if n:
            log.info("caption repair: attached word timing to %d/%d event(s) "
                     "from %s (plan predates P7.7)", n, len(events), cand)
            return events
    log.warning(
        "caption repair: %d caption event(s) carry no word timing and no "
        "usable word_timings.json was found next to %s — captions spanning "
        "a typography card will show the full sentence",
        sum(1 for ev in events if not ev.words), plan_path)
    return events


# ── Plan summary printer ─────────────────────────────────────────────────── #

def summarise_plan(shots: list[Shot]) -> str:
    """Human-readable one-line-per-shot summary, for the CLI."""
    if not shots:
        return "(empty plan)"

    lines: list[str] = []
    lines.append(f"Plan: {len(shots)} shots, "
                 f"{sum(s.duration for s in shots):.1f}s total")
    lines.append("─" * 92)

    # Histogram of visual types
    from collections import Counter
    visual_counts = Counter(s.visual for s in shots)
    histogram = "  ".join(f"{v}:{n}" for v, n in visual_counts.most_common())
    lines.append(f"Visuals: {histogram}")
    lines.append("─" * 92)

    for i, shot in enumerate(shots):
        section_tag = f"[{shot.section_id:>9}]" if shot.section_id else "[         ]"
        timing = f"{shot.start:6.2f}-{shot.end:6.2f}s"
        dur = f"({shot.duration:4.1f}s)"
        kind = f"{shot.visual:<14}"
        motion = f"{shot.motion:<12}"

        if shot.visual == "typography":
            extra = f'"{shot.typography_text[:50]}"'
        else:
            extra = shot.search_query[:60]

        lines.append(
            f"  {i+1:>3}. {section_tag} {timing} {dur} {kind} {motion}  {extra}"
        )
    return "\n".join(lines)