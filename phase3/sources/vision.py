"""
Phase 3 sources — Claude vision scorer with three-dimensional rubric.

Each candidate is scored 0-3 on three axes:

  subject:    Does the image show the requested subject or scene?
  quality:    Is it sharp, well-composed, undamaged?
  cinematic:  Does it carry emotional or documentary impact?

Total score: 0-9.  Threshold for "kept" is total >= 4 AND subject >= 1.
Within kept candidates, ranking is by total score (higher = better).

This is qualitatively better than the v1 binary keep/drop because it
gives the orchestrator a ranking signal: when several candidates pass
threshold, we can pick the *best* image, not just any acceptable one.

Cost
----
Scoring is BATCHED per shot (P6.2): all of a shot's candidates go into
ONE Haiku call as numbered images, so the ~500-token rubric is paid
once per shot instead of once per candidate.  Typical video: ~55 image
shots ≈ 55 calls ≈ $0.15–0.25.  The single-image `score()` remains for
callers with one candidate.  Caching makes re-renders free.

Era (P6.1)
----------
When the shot plan carries an explicit `era` (e.g. "1900s-1910s Ottoman
Mesopotamia"), it is passed into the rubric verbatim — the model no
longer has to infer the period from the search query.  For period shots
`passes_threshold(..., era_strict=True)` turns the era axis into a HARD
GATE: era ≤ 1 candidates are rejected outright (the renderer falls back
to an Arabic typography card — a period-false image is worse than no
image).  `era="timeless"` keeps the old soft-demotion behaviour.

Images are always resized to ≤800 px wide before encoding to base64,
as required by the Anthropic API (oversized → 400 error).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .base import ImageCandidate

log = logging.getLogger(__name__)


MIN_KEEP_TOTAL = 4         # minimum total score to keep an image
MIN_KEEP_SUBJECT = 1       # subject must clear this bar (generic shots)
MIN_KEEP_SUBJECT_PORTRAIT = 2   # portrait shots demand a stricter floor:
                                # "vaguely related, wrong era/region" is
                                # not acceptable when the audience sees a
                                # named face on screen.  Bumps subject
                                # bar from 1 → 2 ("right era + region,
                                # wrong specific subject" still passes,
                                # but pure topical drift is rejected).


_SYSTEM = (
    "You score images for a documentary video on their fit, quality, "
    "and cinematic weight.  Return only valid JSON — no markdown, no "
    "explanation."
)

# Shared rubric text — used verbatim by both the single-image and the
# batched per-shot prompt.  {era_clause} is filled by _era_clause().
_RUBRIC = """\
Score on four dimensions, 0-3 each:

1. subject (0-3): does this image show the requested subject or scene?
   0 = completely unrelated
   1 = vaguely related (e.g. wrong era / wrong region)
   2 = related (right era and region, wrong specific subject)
   3 = exact match for what was requested

2. quality (0-3): is the image sharp, well-composed, undamaged?
   0 = unusable (heavily damaged, cropped wrong, illegible, watermarked)
   1 = poor (blurry, low contrast, awkward composition)
   2 = acceptable (sharp enough, reasonable composition)
   3 = excellent (sharp, well-framed, visually rich)

3. cinematic (0-3): does this image carry emotional or documentary impact?
   0 = flat / forgettable
   1 = ordinary
   2 = strong (clear subject, evocative atmosphere)
   3 = striking (the kind of image that makes you pause)

4. era (0-3): {era_clause}
   Judge the CONTENT, not the color treatment — a modern photo graded sepia
   is still modern.  Look at uniforms, weapons, vehicles, clothing,
   architecture, printing/handwriting style.
   IMPORTANT: wrong century in EITHER direction scores 0.  A 17th-century
   Baroque painting is exactly as anachronistic for a 1900s subject as a
   modern photograph; "old-looking" is not "the right period".  Likewise a
   ceremonial guard in archaic-style dress photographed today is modern
   content (score 0), and material from the wrong REGION's army/institutions
   (e.g. a European royal guard for an Ottoman academy) scores at most 1.
   0 = clearly anachronistic (wrong century — older OR newer, modern
       staging/reenactors, obviously AI-generated or contemporary stock)
   1 = doubtful (period ambiguous but details feel wrong)
   2 = plausible for the required period
   3 = clearly authentic period material

If an image is a diagram, chart, anatomical illustration, modern clip
art, stock-photo cliche, or watermarked sample image, score subject=0.
"""

_ERA_EXPLICIT = (
    'the image MUST plausibly depict this period: "{era}".  '
    "Could this image's content be from that period and region?"
)
_ERA_TIMELESS = (
    "this shot is timeless scene-setting b-roll with no period "
    "requirement — score era=2 unless the content would visibly jar "
    "inside a historical documentary (score what you see)."
)
_ERA_INFERRED = (
    "if the query implies a historical period (a year, a decade, or an "
    "era like \"Ottoman\"), could this image plausibly depict that "
    "period?  If the query implies no particular period, score era=2."
)

_USER_TMPL = """\
This image is a candidate visual for a documentary about:
  Book: {book_title}
  Subject: {character_name}
  Specific shot query: "{query}"

{rubric}
Return ONLY this JSON:
{{"subject": N, "quality": N, "cinematic": N, "era": N, "reason": "one-line rationale in English"}}
"""

_BATCH_TMPL = """\
The {n} images above are candidate visuals for ONE shot in a documentary about:
  Book: {book_title}
  Subject: {character_name}
  Specific shot query: "{query}"

Score EVERY image independently against the same shot query.
{rubric}
Return ONLY a JSON array with exactly {n} objects, in the same order as
the images, each shaped:
{{"image": K, "subject": N, "quality": N, "cinematic": N, "era": N, "reason": "one-line rationale in English"}}
where K is the 1-based image number.
"""

# Max images per batched Haiku call.  8 × ~640 image tokens + rubric
# stays comfortably inside limits while cutting per-candidate overhead.
_BATCH_CHUNK = 8


def is_period_era(era: str | None) -> bool:
    """True when a shot's `era` demands a specific historical period.

    "" (legacy plans, typography shots) and "timeless" are not period
    constraints; anything else ("1900s-1910s Ottoman Mesopotamia", …) is.
    """
    e = (era or "").strip().lower()
    return bool(e) and e != "timeless"


def _era_clause(era: str | None) -> str:
    if is_period_era(era):
        return _ERA_EXPLICIT.format(era=era.strip())
    if (era or "").strip().lower() == "timeless":
        return _ERA_TIMELESS
    return _ERA_INFERRED


@dataclass
class VisionScorer:
    """Wraps the Anthropic client with our rubric."""
    api_key: str
    model: str = "claude-haiku-4-5-20251001"

    def _client(self):
        """One Anthropic client per scorer (was one per call)."""
        client = getattr(self, "_client_cached", None)
        if client is None:
            from anthropic import Anthropic
            client = Anthropic(api_key=self.api_key)
            self._client_cached = client
        return client

    def score(self, candidate: ImageCandidate, *,
              book_title: str, character_name: str, query: str,
              era: str = "") -> ImageCandidate:
        """
        Score one candidate in place.  Mutates and returns it.

        Fail-open: on any API/processing error, the candidate is given
        a neutral score (subject=2, quality=2, cinematic=1) so it
        passes threshold rather than being silently dropped.
        """
        if not candidate.local_path or not candidate.local_path.exists():
            log.warning("Vision: skipping %s — no local path", candidate.title[:40])
            return candidate

        try:
            b64 = _encode_image(candidate.local_path)
        except ImportError as exc:
            log.warning("Vision: anthropic/PIL not installed (%s); fail-open", exc)
            _apply_neutral_score(candidate, "vision unavailable")
            return candidate
        except Exception as exc:
            log.warning("Vision: can't read %s: %s — keeping with neutral score",
                        candidate.title[:40], exc)
            _apply_neutral_score(candidate, f"error: {exc!s}"[:80])
            return candidate

        try:
            msg = self._client().messages.create(
                model=self.model,
                max_tokens=200,
                system=_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": _USER_TMPL.format(
                            book_title=book_title or "unknown",
                            character_name=character_name or "not specified",
                            query=query,
                            rubric=_RUBRIC.format(era_clause=_era_clause(era)),
                        )},
                    ],
                }],
            )
            raw = msg.content[0].text.strip()
            scores = _parse_score_json(raw)
            _apply_scores(candidate, scores)

            log.info("Vision %d/%d/%d era=%d  %s  — %s",
                     candidate.score_subject, candidate.score_quality,
                     candidate.score_cinematic, candidate.score_era,
                     candidate.title[:40],
                     candidate.vision_reason[:60])

        except Exception as exc:
            log.warning("Vision: error on %s: %s — keeping with neutral score",
                        candidate.title[:40], exc)
            _apply_neutral_score(candidate, f"error: {exc!s}"[:80])

        return candidate

    def score_batch(self, candidates: list[ImageCandidate], *,
                    book_title: str, character_name: str, query: str,
                    era: str = "") -> list[ImageCandidate]:
        """
        Score all of one shot's candidates in as few Haiku calls as
        possible (chunks of ≤ 8 images per call).  Mutates in place and
        returns the list.  Same fail-open contract as score(): a chunk
        whose call or parse fails gets neutral scores, and an image the
        model skipped in its array gets a neutral score.
        """
        todo = [c for c in candidates
                if c.local_path and Path(c.local_path).exists()]
        if not todo:
            return candidates
        if len(todo) == 1:
            self.score(todo[0], book_title=book_title,
                       character_name=character_name, query=query, era=era)
            return candidates

        for k in range(0, len(todo), _BATCH_CHUNK):
            chunk = todo[k:k + _BATCH_CHUNK]
            self._score_chunk(chunk, book_title=book_title,
                              character_name=character_name,
                              query=query, era=era)
        return candidates

    def _score_chunk(self, chunk: list[ImageCandidate], *,
                     book_title: str, character_name: str, query: str,
                     era: str) -> None:
        # Encode every image first; unreadable ones get neutral scores and
        # drop out of the call.
        encoded: list[tuple[ImageCandidate, str]] = []
        for c in chunk:
            try:
                encoded.append((c, _encode_image(c.local_path)))
            except ImportError as exc:
                log.warning("Vision: anthropic/PIL not installed (%s); fail-open", exc)
                for cc in chunk:
                    _apply_neutral_score(cc, "vision unavailable")
                return
            except Exception as exc:
                log.warning("Vision: can't read %s: %s — neutral score",
                            c.title[:40], exc)
                _apply_neutral_score(c, f"error: {exc!s}"[:80])
        if not encoded:
            return

        content: list[dict] = []
        for i, (_c, b64) in enumerate(encoded, start=1):
            content.append({"type": "text", "text": f"Image {i}:"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": b64},
            })
        content.append({"type": "text", "text": _BATCH_TMPL.format(
            n=len(encoded),
            book_title=book_title or "unknown",
            character_name=character_name or "not specified",
            query=query,
            rubric=_RUBRIC.format(era_clause=_era_clause(era)),
        )})

        try:
            msg = self._client().messages.create(
                model=self.model,
                max_tokens=120 * len(encoded) + 100,
                system=_SYSTEM,
                messages=[{"role": "user", "content": content}],
            )
            raw = msg.content[0].text.strip()
            entries = _parse_batch_json(raw)
        except Exception as exc:
            log.warning("Vision: batch call failed for %r (%d images): %s — "
                        "keeping all with neutral scores",
                        query[:40], len(encoded), exc)
            for c, _b in encoded:
                _apply_neutral_score(c, f"batch error: {exc!s}"[:80])
            return

        # Map entries to candidates by the 1-based "image" index, falling
        # back to positional order when the index is missing/duplicated.
        by_index: dict[int, dict] = {}
        for pos, entry in enumerate(entries, start=1):
            try:
                idx = int(entry.get("image", pos))
            except (TypeError, ValueError):
                idx = pos
            by_index.setdefault(idx, entry)

        for i, (c, _b) in enumerate(encoded, start=1):
            entry = by_index.get(i)
            if entry is None:
                _apply_neutral_score(c, "missing from batch response")
                continue
            _apply_scores(c, entry)
            log.info("Vision %d/%d/%d era=%d  %s  — %s",
                     c.score_subject, c.score_quality, c.score_cinematic,
                     c.score_era, c.title[:40], c.vision_reason[:60])


def _encode_image(path: Path | str) -> str:
    """Read, downscale to ≤800 px wide, and base64-encode one image.

    Raises ImportError when PIL is missing (callers fail open) and
    OSError/ValueError on unreadable files.
    """
    from PIL import Image
    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.width > 800:
            new_h = int(img.height * 800 / img.width)
            img = img.resize((800, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _apply_scores(c: ImageCandidate, scores: dict) -> None:
    """Copy one parsed score dict onto a candidate."""
    c.score_subject   = int(scores.get("subject", 0))
    c.score_quality   = int(scores.get("quality", 0))
    c.score_cinematic = int(scores.get("cinematic", 0))
    # era is newer than some cached responses — absence must not read
    # as "anachronistic", so default to passing 2.
    c.score_era       = int(scores.get("era", 2))
    c.vision_reason   = str(scores.get("reason", ""))[:200]


def _apply_neutral_score(c: ImageCandidate, reason: str) -> None:
    """Set a neutral score that passes threshold (fail-open policy)."""
    c.score_subject = 2
    c.score_quality = 2
    c.score_cinematic = 1
    c.score_era = 2          # unscored era must not demote (fail-open)
    c.vision_reason = f"[fail-open] {reason}"


def _parse_score_json(raw: str) -> dict:
    """Parse the score JSON Claude returns, tolerating common quirks."""
    raw = raw.strip()
    raw = (raw
           .removeprefix("```json")
           .removeprefix("```")
           .removesuffix("```")
           .strip())
    # Find first { and last } in case there's stray text
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object in vision response: {raw[:200]}")
    return json.loads(m.group(0))


def _parse_batch_json(raw: str) -> list[dict]:
    """Parse the score-array JSON of a batched call, tolerating quirks."""
    raw = raw.strip()
    raw = (raw
           .removeprefix("```json")
           .removeprefix("```")
           .removesuffix("```")
           .strip())
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        # A single-object response for a single-image chunk is fine too.
        return [_parse_score_json(raw)]
    data = json.loads(m.group(0))
    if not isinstance(data, list):
        raise ValueError("Batch vision response is not a JSON array")
    return [d for d in data if isinstance(d, dict)]


def passes_threshold(c: ImageCandidate, visual_type: str | None = None,
                     *, era_strict: bool = False) -> bool:
    """Return True if the candidate should be kept.

    `visual_type` (optional): the shot's visual type ("portrait",
    "archive", etc).  When "portrait", the subject bar is raised from
    1 → 2 because a portrait shot showing the wrong person is far more
    jarring than a B-roll shot in roughly the right era.

    `era_strict` (P6.1): the shot's plan carries an explicit historical
    period — an era-failing candidate (era ≤ 1) is REJECTED, not merely
    demoted.  A period-false image in a documentary is worse than no
    image; the renderer's fallback is an Arabic typography card.
    Unscored candidates still pass (vision-down degradation preserved —
    the neutral fail-open score sets era=2).
    """
    if not c.is_scored:
        return True   # unscored candidates pass (vision skipped)
    if era_strict and 0 <= c.score_era <= 1:
        return False
    subject_floor = (MIN_KEEP_SUBJECT_PORTRAIT
                     if visual_type == "portrait"
                     else MIN_KEEP_SUBJECT)
    return (c.total_score >= MIN_KEEP_TOTAL
            and c.score_subject >= subject_floor)


def rank_candidates(candidates: list[ImageCandidate]) -> list[ImageCandidate]:
    """
    Return candidates sorted best-first.

    Era acts as a DEMOTION TIER, not a filter: every era-passing candidate
    (era ≥ 2 or unscored) outranks every era-failing one, regardless of
    total score.  A tricorn-hat reenactor scoring 7/9 must not beat an
    authentic 5/9 period photograph — but if *every* candidate is
    anachronistic, the least-bad one still renders (honest degradation,
    never fail-closed).

    Within a tier: total score, ties broken by subject > quality >
    cinematic > original order.
    """
    return sorted(
        candidates,
        key=lambda c: (
            0 if c.era_pass else 1,
            -c.total_score if c.is_scored else 99,
            -c.score_subject if c.is_scored else 0,
            -c.score_quality if c.is_scored else 0,
            -c.score_cinematic if c.is_scored else 0,
        ),
    )