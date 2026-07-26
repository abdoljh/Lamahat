"""
Phase 3 — Shot-level renderer (Stage 1: typography + placeholders).

Consumes a shot plan JSON (from `plan.build_shot_plan`) and produces a
watchable MP4.  Typography shots are rendered via `typography.py`;
image shots get neutral placeholder cards printed with their search
query so the rough cut shows exactly what each shot will become.

Stage 1 deliberately omits image fetching.  The renderer architecture
is identical to Stage 2 — only the asset source changes.  When Stage 2
lands, image shots' placeholder cards get swapped for real fetched
photos and the planned `motion` gets applied via FFmpeg zoompan.

Pipeline overview
-----------------
1. For each Shot in the plan:
   - Build the PNG asset (typography card or placeholder).
   - Wrap it into an MP4 clip of exact duration, with motion if planned.

2. Concat all shot clips with stream-copy (zero-cost cuts).

3. Render an ASS subtitle file from the shots' caption_text fields.

4. Single final FFmpeg pass: burn subtitles, mux audio, hard-trim to
   audio duration.

All FFmpeg work happens in subprocesses to keep Python memory low —
important for Streamlit Cloud's 1 GB ceiling.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .plan import Shot
from .typography import (
    CHARCOAL, CREAM_DEEP, CREAM_LIGHT, CREAM_MEDIUM, FONT_PATHS, GRAPHITE,
    TypographySpec, WARM_GREY,
    _apply_grain, _draw_text_rtl, _font, _measure,
    render as render_typography,
    render_overlay as render_typography_overlay,
    render_overlay_steps as render_typography_overlay_steps,
)
from . import motion_parallax as mp

log = logging.getLogger(__name__)

# ── Render defaults ─────────────────────────────────────────────────── #

DEFAULT_FPS = 25
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080

# ── Issue 1: color grading presets ──────────────────────────────────── #
#
# Each preset is a single FFmpeg video-filter chain applied at the final
# mux stage, BEFORE any subtitle burn-in.  Order matters: grading the
# graphic plate first leaves caption text crisp white (libass overlays
# the captions onto the already-graded frame); grading after subs would
# tint the captions themselves.
#
# Filter design notes:
#   warm    — pronounced cinematic teal-shadows / orange-highlights with
#             an S-curve.  This is the production default.  Built from
#             eq (overall contrast/sat lift) + curves (per-channel
#             control points: R lifted, B dropped → teal-shadow/orange-
#             highlight look without the per-tonal-range colorbalance
#             filter, which more than doubled encode time on Colab).
#   cool    — editorial blue lean for somber / political / war material.
#             Mild contrast lift, slight desaturation, blue shadows-mids,
#             warm pull on highlights.
#   neutral — gentle contrast only; no color cast.  Use when source
#             imagery is already graded or the book is photographic.
#   bw      — full desaturation + 10% contrast bump.  Documentary mono.
#
# Calibration: every preset is `eq=...` based so contrast/saturation are
# multiplicative and round-trip safe.  Tested against testsrc2 in the
# sandbox before shipping.
GRADE_PRESETS: dict[str, str] = {
    "warm": (
        "eq=contrast=1.08:saturation=1.05,"
        "curves=r='0/0.05 0.5/0.55 1/1':b='0/0 0.5/0.45 1/0.95'"
    ),
    "cool": (
        "eq=contrast=1.05:saturation=0.95,"
        "curves=r='0/0 0.5/0.45 1/0.95':b='0/0.05 0.5/0.55 1/1'"
    ),
    "neutral": "eq=contrast=1.03:saturation=1.0",
    "bw": "hue=s=0,eq=contrast=1.10",
}

# Per-section grade presets (P3.4 / grade_map).  Same looks as GRADE_PRESETS
# but built ONLY from filters with long-standing timeline (enable=) support —
# `curves` gained timeline support late, and Colab's system ffmpeg can
# predate it.  Each list entry is one filter stage; the section's
# enable='between(t,…)' window is appended per stage.
GRADE_PRESETS_TIMELINE: dict[str, list[str]] = {
    "warm":    ["colortemperature=temperature=5000",
                "eq=contrast=1.08:saturation=1.05"],
    "cool":    ["colortemperature=temperature=7600",
                "eq=contrast=1.05:saturation=0.95"],
    "neutral": ["eq=contrast=1.03"],
    "bw":      ["hue=s=0", "eq=contrast=1.10"],
}


def _section_ranges(shots: "list[Shot]") -> list[tuple[str, float, float]]:
    """Contiguous (section_id, t0, t1) runs in plan order."""
    ranges: list[tuple[str, float, float]] = []
    for s in shots:
        sid = getattr(s, "section_id", "") or ""
        if ranges and ranges[-1][0] == sid:
            ranges[-1] = (sid, ranges[-1][1], s.end)
        else:
            ranges.append((sid, s.start, s.end))
    return ranges


def _grade_map_vparts(grade_map: dict, shots: "list[Shot]",
                      default_grade: str | None) -> list[str]:
    """Timeline-enabled filter stages for per-section grading.

    Sections named in `grade_map` get their mapped preset; every other
    section falls back to `default_grade` (the global --grade), so the film
    is never partially ungraded just because the map lists two sections.
    Applied at the final mux — clip encoding and the stream-copy concat are
    untouched.
    """
    parts: list[str] = []
    for sid, t0, t1 in _section_ranges(shots):
        g = grade_map.get(sid, default_grade)
        if not g:
            continue
        chain = GRADE_PRESETS_TIMELINE.get(g)
        if chain is None:
            log.warning("grade_map: unknown grade %r for section %r — "
                        "section left at default. Valid: %s",
                        g, sid, sorted(GRADE_PRESETS_TIMELINE))
            continue
        enable = f":enable='between(t,{t0:.3f},{t1:.3f})'"
        parts.extend(f"{stage}{enable}" for stage in chain)
        log.info("Section grade: %-10s → %-7s (%.1f–%.1f s)", sid or "(none)",
                 g, t0, t1)
    return parts
DEFAULT_GRADE = "warm"

# ── Issue 4 Patch B: caption charcoal-bar backplate ─────────────────── #
#
# Layered behind the burned ASS captions to improve legibility against
# bright/varied backgrounds.  Three modes:
#
#   off    — no backplate (the historic baseline; relies on Outline only)
#   subtle — charcoal #1A1A1A at α=0.55, ~12% of frame height (default)
#   solid  — same charcoal at α=0.80 (for very bright source material)
#
# Geometry: y=ih*0.85 + h=ih*0.12 places the band in the bottom 12% of
# the frame, sitting directly behind the ASS Style 'Arabic' (MarginV=40,
# Alignment=2 = bottom-centre).  Full-width (w=iw) so wrapped 2-line
# captions are fully backed.  Runs AFTER grade and BEFORE ass in the
# filter chain.
CAPTION_BACKPLATES: dict[str, str | None] = {
    "off": None,
    "subtle": "drawbox=x=0:y=ih*0.85:w=iw:h=ih*0.09:color=0x1A1A1A@0.55:t=fill",
    "solid":  "drawbox=x=0:y=ih*0.85:w=iw:h=ih*0.09:color=0x1A1A1A@0.80:t=fill",
}
DEFAULT_CAPTION_BACKPLATE = "subtle"

# Shots whose `visual` is in this set are rendered by typography.py;
# all others get a placeholder card in Stage 1.
TYPOGRAPHY_VISUALS = {"title_card", "section_mark", "chapter_heading",
                      "typography"}

# Map the planner's typography_template to renderer template name.
# When a typography shot's template is None, infer from visual type.
_TEMPLATE_DEFAULTS = {
    "title_card":      "title_card",
    "section_mark":    "section_mark",
    "chapter_heading": "chapter_heading",
    "typography":      "pull_quote",
}

# Typography visuals eligible for the typography-over-image overlay path.
# title_card is intentionally excluded — it keeps its dedicated book-cover
# treatment.
_OVERLAY_VISUALS = {"section_mark", "chapter_heading", "typography"}

# Typography-over-image camera CONTINUITY (fixes the "image shrinks when the
# caption appears" pop).  The real-image shot finishes zoomed-in (end of its
# Ken Burns path); the reused background used to restart from wide (t=0), so the
# framing snapped back ~2-3% at the cut.  Instead, the overlay background now
# *continues* from the real shot's end framing and eases gently onward with a
# light dolly so the text still sits on living, not frozen, footage.  Smoothstep
# easing means the continuation resumes from rest → no positional/velocity jump.
_OVERLAY_CONT_MOTION = "dolly_in"     # gentle continued push (cx=cy=0 → no truck drift)
_OVERLAY_CONT_INTENSITY = 0.20        # per-card zoom delta ≈ 0.060 * 0.20 ≈ 1.2%
_OVERLAY_ZOOM_CAP_EXTRA = 0.05        # over a long card run, never creep past real_end + this


def _typography_spec(shot, *, width: int, height: int,
                     typography_family: str,
                     book_cover: Path | None = None,
                     book_cover_fit: str = "fill",
                     book_cover_align: str = "center",
                     title_scale: float = 1.0,
                     title_color: "tuple[int, int, int] | None" = None,
                     title_subtitle: str = "",
                     scrim: "str | None" = None) -> "TypographySpec":
    """Build the TypographySpec for a typography-family shot.

    Shared by the static-card path (_build_shot_asset) and the
    typography-over-image overlay path (render_video), so template resolution
    stays in one place.  Only title_card carries the book cover.

    `title_scale` / `title_color` apply to the main title only — title_color is
    dropped for non-title templates so section/quote accents keep their family
    defaults.  `scrim` selects the over-image background plate (off/soft/band)
    and is ignored by static cards.
    """
    if shot.visual == "typography":
        template = shot.typography_template or _TEMPLATE_DEFAULTS["typography"]
    else:
        template = _TEMPLATE_DEFAULTS[shot.visual]
    cover = book_cover if shot.visual == "title_card" else None
    return TypographySpec(
        template=template,
        text=shot.typography_text,
        subtitle=(title_subtitle if template == "title_card" else ""),
        width=width, height=height,
        family=typography_family,
        cover_image=cover,
        cover_fit=book_cover_fit,
        cover_align=book_cover_align,
        title_scale=title_scale,
        accent_color=(title_color if template == "title_card" else None),
        scrim=scrim,
    )


# ── Placeholder card for Stage 1 ────────────────────────────────────── #

def _placeholder_card(shot: Shot, out_path: Path,
                     width: int, height: int) -> Path:
    """
    Render a neutral placeholder card for a non-typography shot.

    Shows:
      - The shot's `visual` type (small, top-left)  e.g. "portrait"
      - The planned `search_query` (centred, larger)
      - The planned `motion` (small, bottom-right)  e.g. "slow_push"
      - Timing badge (small, top-right)             e.g. "8.2→14.5s"

    Style matches Family A: cream background, charcoal text, hairline
    rule above and below the query.  Looks deliberately like a "TBD"
    card so when reviewing the rough cut you can see exactly which
    image needs sourcing — and the cards visually disappear into the
    final piece's typography rhythm when they're replaced.
    """
    img = Image.new("RGB", (width, height), CREAM_MEDIUM)
    _apply_grain(img)
    draw = ImageDraw.Draw(img)

    # Spec carries the height through to font sizing
    label_size  = max(14, int(height * 0.020))
    query_size  = max(18, int(height * 0.034))
    badge_size  = max(12, int(height * 0.017))

    label_font = _font("italic", label_size)
    query_font = _font("regular", query_size)
    badge_font = _font("regular", badge_size)

    margin = int(width * 0.05)

    # Visual-type tag (top-left, Latin so plain Pillow text is fine)
    draw.text((margin, margin),
              f"[ {shot.visual} ]", font=label_font, fill=GRAPHITE)

    # Timing badge (top-right)
    badge = f"{shot.start:.1f} - {shot.end:.1f}s   ({shot.duration:.1f}s)"
    bw, _ = draw.textbbox((0, 0), badge, font=badge_font)[2:4], 0
    bw = draw.textbbox((0, 0), badge, font=badge_font)[2]
    draw.text((width - margin - bw, margin),
              badge, font=badge_font, fill=GRAPHITE)

    # Centred search query — wrap if too long
    query_text = shot.search_query or "(no query)"
    column_w = int(width * 0.7)
    lines = _wrap_latin(draw, query_text, query_font, column_w)
    line_h = int(query_size * 1.5)
    total_h = line_h * len(lines)
    block_top = (height - total_h) // 2

    for i, line in enumerate(lines):
        lw = draw.textbbox((0, 0), line, font=query_font)[2]
        draw.text(((width - lw) // 2, block_top + i * line_h),
                  line, font=query_font, fill=CHARCOAL)

    # Hairline rules above and below the query block
    rule_gap = int(height * 0.04)
    rule_thick = max(1, int(height * 2 / 1080))
    rule_len = int(width * 0.16)
    rule_x0 = (width - rule_len) // 2
    rule_x1 = rule_x0 + rule_len
    rule_top_y = block_top - rule_gap
    rule_bot_y = block_top + total_h + rule_gap

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    o_draw = ImageDraw.Draw(overlay)
    a = int(255 * 0.45)
    o_draw.rectangle([rule_x0, rule_top_y, rule_x1, rule_top_y + rule_thick],
                     fill=(*WARM_GREY, a))
    o_draw.rectangle([rule_x0, rule_bot_y, rule_x1, rule_bot_y + rule_thick],
                     fill=(*WARM_GREY, a))
    base = img.convert("RGBA")
    base.alpha_composite(overlay)
    img = base.convert("RGB")
    draw = ImageDraw.Draw(img)

    # Motion tag (bottom-right)
    motion_text = f"motion: {shot.motion}"
    mw = draw.textbbox((0, 0), motion_text, font=badge_font)[2]
    mh = badge_size
    draw.text((width - margin - mw, height - margin - mh),
              motion_text, font=badge_font, fill=GRAPHITE)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    return out_path


def _error_card(shot: Shot, shot_index: int, out_path: Path,
                width: int, height: int, error_msg: str) -> Path:
    """
    Render an error-state placeholder.  Used when a single shot fails
    so the timeline doesn't collapse — audio sync depends on every
    shot producing a clip of its planned duration.

    Looks like a placeholder card but with darker tones to make the
    failure visible during review.  Includes the shot index and a
    truncated error message so debugging is direct.
    """
    img = Image.new("RGB", (width, height), CREAM_DEEP)
    _apply_grain(img)
    draw = ImageDraw.Draw(img)

    label_size = max(14, int(height * 0.020))
    title_size = max(18, int(height * 0.030))
    msg_size   = max(12, int(height * 0.018))

    label_font = _font("italic", label_size)
    title_font = _font("bold", title_size)
    msg_font   = _font("regular", msg_size)
    margin = int(width * 0.05)

    # Top-left tag
    draw.text((margin, margin),
              f"[ shot {shot_index} · {shot.visual} · error ]",
              font=label_font, fill=GRAPHITE)

    # Centred shot identity
    title = f"Shot {shot_index} — rendering failed"
    tw = draw.textbbox((0, 0), title, font=title_font)[2]
    draw.text(((width - tw) // 2, height // 2 - title_size),
              title, font=title_font, fill=CHARCOAL)

    # Short query for context (if image-kind shot)
    query = (shot.search_query or shot.typography_text)[:80]
    if query:
        qw = draw.textbbox((0, 0), query, font=msg_font)[2]
        draw.text(((width - qw) // 2, height // 2 + 10),
                  query, font=msg_font, fill=GRAPHITE)

    # Error message at bottom, truncated
    err = error_msg[:140]
    ew = draw.textbbox((0, 0), err, font=msg_font)[2]
    draw.text(((width - ew) // 2, height - margin - msg_size),
              err, font=msg_font, fill=WARM_GREY)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    return out_path


def _wrap_latin(draw: ImageDraw.ImageDraw, text: str,
                font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Simple greedy word-wrap for Latin/Arabic text."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        w = draw.textbbox((0, 0), candidate, font=font)[2]
        if w <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


# ── Per-shot asset builder ──────────────────────────────────────────── #

def _build_shot_asset(shot: Shot, shot_index: int,
                     out_path: Path,
                     width: int, height: int,
                     *,
                     fetcher: "Fetcher | None" = None,
                     book_cover: Path | None = None,
                     book_cover_fit: str = "fill",
                     book_cover_align: str = "center",
                     typography_family: str = "A",
                     parallax: bool = False,
                     parallax_backend: str = "depthanything",
                     title_scale: float = 1.0,
                     title_color: "tuple[int, int, int] | None" = None,
                     title_subtitle: str = "",
                     scrim: "str | None" = None) -> tuple[Path, bool]:
    """
    Render or fetch the asset for a single shot.

    Returns
    -------
    (asset_path, is_real_image)
        is_real_image=True   → planner's motion is applied via zoompan
        is_real_image=False  → static hold (typography or placeholder)

    Asset resolution:
      - Typography visuals → typography.render() PNG
      - Image visuals:
          1. fetcher.fetch_for_shot() if fetcher is supplied
          2. _placeholder_card() if fetcher returns no image

    `book_cover`, when supplied, switches the title_card visual to its
    photo-background variant; `book_cover_fit` selects the layout
    (fill/contain/blur_pad); `book_cover_align` positions the cover
    (center/left/right) when the fit mode leaves spare horizontal
    space.  All three are ignored for non-title-card visuals.
    """
    # Typography always uses the typography renderer
    if shot.visual in TYPOGRAPHY_VISUALS:
        spec = _typography_spec(
            shot, width=width, height=height,
            typography_family=typography_family,
            book_cover=book_cover,
            book_cover_fit=book_cover_fit,
            book_cover_align=book_cover_align,
            title_scale=title_scale,
            title_color=title_color,
            title_subtitle=title_subtitle,
            scrim=scrim,
        )
        return render_typography(spec, out_path), False

    # Image visual: try the fetcher first.  visual_type matters: portrait
    # shots get a stricter vision subject floor (a wrong face is far more
    # jarring than off-topic b-roll).  era makes the period a hard gate
    # for shots the plan marks as historical (P6.1).
    if fetcher is not None:
        try:
            result = fetcher.fetch_for_shot(shot.search_query, shot_index,
                                            visual_type=shot.visual,
                                            era=getattr(shot, "era", ""))
        except Exception as exc:
            log.warning("Fetcher raised on shot %d (%s): %s — using placeholder",
                        shot_index, shot.visual, exc)
            result = None

        if result and result.has_image:
            best = result.best
            # The fetched image becomes the shot's source.  We always
            # copy it to out_path (a PNG slot) — re-encoding via Pillow
            # both normalises format and lets us check the file opens.
            try:
                from PIL import Image
                with Image.open(best.local_path) as im:
                    im = im.convert("RGB")
                    im.save(out_path, format="PNG", optimize=True)
                log.info("Shot %d: using fetched image from %s",
                         shot_index, best.source)
                # Depth for parallax: estimate once and cache it next to the
                # *source* image (persistent), then carry it next to the asset.
                # Because this keys off the resolved source — dossier, portrait
                # pool, chosen-file or live fetch — re-renders reuse it and
                # never re-estimate (kills the "no cached depth" notices).
                if parallax:
                    try:
                        src_depth = mp.ensure_depth_cached(
                            Path(best.local_path), backend=parallax_backend)
                        shutil.copyfile(
                            src_depth,
                            out_path.with_name(out_path.stem + ".depth.png"))
                    except Exception as exc:
                        log.warning("Shot %d: depth caching failed (%s); "
                                    "parallax will estimate at render time",
                                    shot_index, exc)
                return out_path, True
            except Exception as exc:
                log.warning("Shot %d: fetched image %s won't open: %s",
                            shot_index, best.local_path, exc)
                # fall through to placeholder

    # No image survived the waterfall.  A period shot whose candidates all
    # failed the era gate lands here by design — render the gap as an
    # Arabic typography beat (§7.7) instead of the Latin "TBD" card, so
    # the miss reads as an intentional design moment.
    card = _arabic_gap_card(shot, out_path, width, height,
                            typography_family=typography_family,
                            scrim=scrim)
    if card is not None:
        log.info("Shot %d: no era-true image — Arabic typography card",
                 shot_index)
        return card, False

    # Last resort: placeholder card showing the search query
    return _placeholder_card(shot, out_path, width, height), False


def _arabic_gap_card(shot: Shot, out_path: Path,
                     width: int, height: int, *,
                     typography_family: str,
                     scrim: "str | None") -> Path | None:
    """Style an un-sourced image shot as a pull-quote typography card
    using the Arabic being narrated over it (§7.7).  Returns None when
    the shot carries no usable Arabic text (caller falls back to the
    Latin placeholder) or the typography render fails."""
    text = (getattr(shot, "typography_text", "") or "").strip()
    if not text:
        words = ((shot.caption_text or "").strip()).split()
        if len(words) >= 3:
            text = " ".join(words[:12])
    if not text:
        return None
    try:
        from dataclasses import replace as _dc_replace
        quote_shot = _dc_replace(shot, visual="typography",
                                 typography_template="pull_quote",
                                 typography_text=text)
        spec = _typography_spec(
            quote_shot, width=width, height=height,
            typography_family=typography_family,
            scrim=scrim,
        )
        return render_typography(spec, out_path)
    except Exception as exc:  # noqa: BLE001 — placeholder is the safety net
        log.warning("Arabic gap card failed for shot %.1f–%.1fs (%s) — "
                    "using Latin placeholder", shot.start, shot.end, exc)
        return None


# ── Per-shot clip builder (FFmpeg) ──────────────────────────────────── #

def _png_to_clip(png_path: Path, out_path: Path,
                duration: float, *,
                fps: int = DEFAULT_FPS,
                width: int = DEFAULT_WIDTH,
                height: int = DEFAULT_HEIGHT,
                motion: str = "static_hold",
                intensity: float = 1.0) -> Path:
    """
    Encode a single PNG into a video clip of exact duration.

    Motion handling:
      static_hold  → simple loop-encode, no zoompan
      slow_push, slow_pull, fast_push, pan_left, pan_right, ken_burns
                   → reserved for Stage 2 image shots; in Stage 1 we
                     fall through to static_hold (placeholder cards
                     don't benefit from motion)

    Output: h264/yuv420p, identical specs across all shot clips, so
    `concat -c copy` works downstream.
    """
    n_frames = max(1, int(round(duration * fps)))

    # All clips share an identical encoder profile so concat-by-copy works.
    common_encode = [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
    ]

    if motion == "static_hold" or motion not in _MOTION_FILTERS:
        # Loop the PNG for exactly the right number of frames
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", str(fps),
            "-i", str(png_path),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-t", f"{duration:.3f}",
            "-frames:v", str(n_frames),
            *common_encode,
            str(out_path),
        ]
    else:
        # Motion path: source is rendered into a "zoom buffer" sized
        # 1.6× the output, then zoompan crops a window from it.
        # Intensity is the planner's motion_intensity knob (P7.4),
        # clamped so an outlier plan can't produce nauseating moves.
        k = max(0.4, min(1.6, float(intensity or 1.0)))
        zoom_expr = _MOTION_FILTERS[motion](n_frames, k)
        buf_w = int(width * 1.6)
        buf_h = int(height * 1.6)
        zoompan = (
            f"zoompan=z='{zoom_expr['z']}'"
            f":x='{zoom_expr['x']}'"
            f":y='{zoom_expr['y']}'"
            f":d={n_frames}:s={width}x{height}:fps={fps}"
        )

        # Aspect-aware fit.  A landscape source close to 16:9 is cover-filled
        # (≤~12 % crop).  A portrait / odd-aspect source (a standing figure, a
        # tall scan) would lose its head and feet to a cover-crop, so it is
        # *contained* whole over a blurred fill of itself — mirroring the
        # parallax path's _fit_to_frame so character portraits are never cut.
        # The same 0.28 mismatch tolerance keeps the two paths in agreement.
        try:
            with Image.open(png_path) as _im:
                _iw, _ih = _im.size
            _contain = (abs((_iw / _ih) - (width / height))
                        / (width / height) > 0.28)
        except Exception:
            _contain = False

        if _contain:
            # Blurred cover background + centred whole subject (no crop).
            filt = (
                f"split=2[bg][fg];"
                f"[bg]scale={buf_w}:{buf_h}:force_original_aspect_ratio=increase,"
                f"crop={buf_w}:{buf_h},boxblur=24:2[bg];"
                f"[fg]scale={buf_w}:{buf_h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{zoompan},format=yuv420p"
            )
            vflag = ["-filter_complex", filt]
        else:
            filt = (
                f"scale={buf_w}:{buf_h}:force_original_aspect_ratio=increase,"
                f"crop={buf_w}:{buf_h},{zoompan},format=yuv420p"
            )
            vflag = ["-vf", filt]

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", str(fps),
            "-i", str(png_path),
            *vflag,
            "-t", f"{duration:.3f}",
            "-frames:v", str(n_frames),
            *common_encode,
            str(out_path),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(
            f"Shot clip encode failed for {png_path.name}:\n"
            f"{result.stderr[-1200:]}"
        )
    return out_path


# Motion filter expressions — used for image shots.
# All x/y/z expressions use FFmpeg zoompan's own variables:
#   iw, ih       = input (buffer) dimensions
#   on           = output frame index (1-based)
#   pzoom        = previous frame's zoom value
#   zoom         = current frame's zoom value (z expression)
# The pan_step expressions use iw fractions so they work at any
# buffer size — no hardcoded pixel constants.
#
# Every factory takes (n_frames, k) where `k` is the planner's
# motion_intensity (P7.4 — previously silently ignored): 0.5 = subtle,
# 1.0 = default amplitude, 1.5 = exaggerated.  _png_to_clip clamps k.
def _zoom_in(n: int, k: float = 1.0):
    a = 0.08 * k
    return {"z": f"min(pzoom+{a/n:.6f},{1 + a:.4f})",
            "x": "iw/2-(iw/zoom/2)",
            "y": "ih/2-(ih/zoom/2)"}
def _zoom_out(n: int, k: float = 1.0):
    a = 0.08 * k
    return {"z": f"if(lte(on,1),{1 + a:.4f},max(1.0,pzoom-{a/n:.6f}))",
            "x": "iw/2-(iw/zoom/2)",
            "y": "ih/2-(ih/zoom/2)"}
def _fast_push(n: int, k: float = 1.0):
    a = 0.20 * k
    return {"z": f"min(pzoom+{a/n:.6f},{1 + a:.4f})",
            "x": "iw/2-(iw/zoom/2)",
            "y": "ih/2-(ih/zoom/2)"}
def _pan_right(n: int, k: float = 1.0):
    # Travel half the buffer width over the clip (× intensity)
    pan_per_frame = f"(iw*{0.5 * k:.4f})/{n}"
    return {"z": "1.10",
            "x": f"if(lte(on,1),0,min(x+{pan_per_frame},iw-iw/zoom))",
            "y": "ih/2-(ih/zoom/2)"}
def _pan_left(n: int, k: float = 1.0):
    pan_per_frame = f"(iw*{0.5 * k:.4f})/{n}"
    return {"z": "1.10",
            "x": f"if(lte(on,1),iw-iw/zoom,max(0,x-{pan_per_frame}))",
            "y": "ih/2-(ih/zoom/2)"}
def _ken_burns(n: int, k: float = 1.0):
    a = 0.12 * k
    pan_per_frame = f"(iw*{0.3 * k:.4f})/{n}"
    return {"z": f"min(pzoom+{a/n:.6f},{1 + a:.4f})",
            "x": f"if(lte(on,1),0,min(x+{pan_per_frame},iw-iw/zoom))",
            "y": "ih/2-(ih/zoom/2)"}

def _card_drift(n: int, k: float = 1.0):
    """Near-subliminal 1.00 → 1.03 push for typography cards (P7.4).

    A third of the film is text cards; a frozen frame there is the
    single biggest "slide show" tell.  This drift is slow enough that
    the type stays comfortably readable but the frame is alive — the
    same trick every documentary title sequence uses.
    """
    a = 0.03 * k
    return {"z": f"min(pzoom+{a/n:.6f},{1 + a:.4f})",
            "x": "iw/2-(iw/zoom/2)",
            "y": "ih/2-(ih/zoom/2)"}

def _section_accent(n: int, k: float = 1.0):
    """
    0.3 s zoom-in "new chapter" accent for section_mark shots.

    Ramps zoom from 1.00 → 1.05 over the first ~0.3 s, then clamps.
    The rest of the shot is a static hold at 1.05.  Centered crop so
    the typography never drifts off-axis.

    The `n` arg is total frames; we hardcode the accent window to
    ~0.3 s worth of frames assuming the renderer's DEFAULT_FPS=25,
    which gives 8 frames.  Using min() in the z-expression makes the
    accent self-clamping — once `on` exceeds the ramp window, z stays
    at 1.05 for the remainder regardless of total shot length.
    """
    accent_frames = max(2, int(round(0.3 * DEFAULT_FPS)))  # 8 @ 25 fps
    # Linear ramp: z = 1.00 + 0.05 * on / accent_frames, clamped at 1.05
    return {"z": f"min(1.05,1.00+{0.05/accent_frames:.6f}*on)",
            "x": "iw/2-(iw/zoom/2)",
            "y": "ih/2-(ih/zoom/2)"}

_MOTION_FILTERS = {
    "slow_push":      _zoom_in,
    "slow_pull":      _zoom_out,
    "fast_push":      _fast_push,
    "pan_right":      _pan_right,
    "pan_left":       _pan_left,
    "ken_burns":      _ken_burns,
    "card_drift":     _card_drift,
    "section_accent": _section_accent,
}


# ── Caption layer (ASS) ─────────────────────────────────────────────── #

def _ts(sec: float) -> str:
    """Seconds → ASS timestamp H:MM:SS.cc"""
    sec = max(0.0, sec)
    h, m = divmod(int(sec), 3600); m, s = divmod(m, 60)
    cs = min(99, int(round((sec - int(sec)) * 100)))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", r"\{").replace("\n", r"\N")


def _write_captions(shots: list[Shot], dest: Path,
                   width: int, height: int,
                   *,
                   caption_size: float = 1.0,
                   caption_color: "str | None" = None,
                   caption_pos: "float | None" = None,
                   events: "list | None" = None) -> Path | None:
    """
    Generate an ASS subtitle file.

    With `events` (sentence-level CaptionEvents from a v2 plan — P7.2),
    subtitles follow the SPEECH, not the cuts: an event spanning two
    image shots stays one continuous line across the cut.  Events are
    only clipped where a typography-kind shot (which shows its own text)
    or a show_caption=False shot is on screen.

    Without `events`, falls back to the legacy per-shot caption_text
    windows.

    Returns None if there is nothing to show (skips the subtitle pass).
    """
    # Typography shots already show their text on screen as the
    # primary visual element.  Burning a caption layer on top would be
    # redundant and visually cluttered.  Skip them.
    visible = [
        s for s in shots
        if s.show_caption
        and s.caption_text.strip()
        and s.visual not in TYPOGRAPHY_VISUALS
    ]
    if not visible and not events:
        return None

    # Font + position
    # Documentary caption size: 5.0% of frame height — readable from
    # across a room, doesn't crowd the typography.  At 1080p that's
    # ~54 px; at 720p ~36 px.  Combined with two-line wrap (below) this
    # gives a documentary subtitle look rather than a single wall of
    # tiny text.
    font_sz = max(28, int(height * 0.050 * caption_size))
    margin_v = max(20, int(height * (caption_pos if caption_pos is not None else 0.06)))

    # ASS colours: &HAABBGGRR (alpha 00 = opaque, FF = transparent)
    # We use white text with a charcoal outline (BorderStyle 1, no
    # backplate).  libass doesn't blend BackColour alpha against the
    # video for BorderStyle 3 — it renders as fully opaque — so the
    # backplate approach doesn't work.  White-on-outline reads clearly
    # over cream placeholders AND over photo b-roll once Stage 2 lands.
    text_colour    = caption_color if caption_color else "&H00FFFFFF"  # white, opaque
    outline_colour = "&H001F2326"   # charcoal, opaque
    back_colour    = "&H00000000"   # unused

    # Caption width margins (px).  These are tighter than the natural
    # full-width readable region because most of our captions are 12-15
    # Arabic words — long enough to force a wrap into two ~7-word lines
    # that read like documentary subtitles, rather than one wall-of-text
    # line spanning the frame.  Combined with WrapStyle=2 below.
    caption_margin_h = max(240, int(width * 0.18))

    header = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Amiri,{font_sz},{text_colour},&H000000FF,{outline_colour},{back_colour},0,0,0,0,100,100,0,0,1,2,1,2,{caption_margin_h},{caption_margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []

    if events:
        # ── Sentence-level events (P7.2): subtitles follow the SPEECH.
        # An event spanning a cut stays one continuous line; it is only
        # clipped where a typography-kind shot shows its own text (or a
        # shot explicitly hides captions).
        hidden = sorted(
            (s.start, s.end) for s in shots
            if s.visual in TYPOGRAPHY_VISUALS or not s.show_caption)
        MIN_SPAN = 0.4   # a clipped sliver shorter than this just flickers
        for ev in events:
            ev_text = (getattr(ev, "text", "") or "").strip()
            ev_start = float(getattr(ev, "start", 0.0))
            ev_end = float(getattr(ev, "end", 0.0))
            if not ev_text or ev_end <= ev_start:
                continue
            spans = [(ev_start, ev_end)]
            for h0, h1 in hidden:
                nxt: list[tuple[float, float]] = []
                for a, b in spans:
                    if h1 <= a or h0 >= b:
                        nxt.append((a, b))
                        continue
                    if a < h0:
                        nxt.append((a, h0))
                    if h1 < b:
                        nxt.append((h1, b))
                spans = nxt
            body = _wrap_caption(_escape_ass(ev_text), max_words_per_line=8)
            for a, b in spans:
                if b - a < MIN_SPAN:
                    continue
                lines.append(
                    f"Dialogue: 0,{_ts(a)},{_ts(b)},Caption,,0,0,0,,{body}")
        if not lines:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
        return dest

    # ── Legacy per-shot captions (no events in the plan) ─────────────
    # Gap between the visual cut and the caption appearing / disappearing.
    # 0.15s on each side (0.3s total per shot) gives the eye a clear break
    # between consecutive captions, fixing the "merged" appearance where
    # two captions seemed to run into each other.
    GAP = 0.15
    # If a shot is so short that the gap would erase the caption entirely,
    # display it for at least this long.  Captions on 3-4s shots still get
    # visible breathing room.
    MIN_VISIBLE = 0.6

    for shot in visible:
        proposed_start = shot.start + GAP
        proposed_end   = shot.end   - GAP
        if proposed_end - proposed_start < MIN_VISIBLE:
            # Shot is too short for the desired gap.  Centre the caption
            # in the shot and clamp to MIN_VISIBLE (or the whole shot,
            # whichever is shorter) so something still appears on screen.
            duration = min(MIN_VISIBLE, shot.duration)
            mid = (shot.start + shot.end) / 2
            start = mid - duration / 2
            end   = mid + duration / 2
        else:
            start, end = proposed_start, proposed_end

        if end <= start:
            continue
        # Escape user-provided text FIRST (handles literal backslashes,
        # curly braces, and embedded newlines), then insert the ASS
        # line-break sequence on top.  Doing this in the wrong order
        # caused `\N` to be escaped to `\\N`, which libass renders as
        # a literal backslash on screen.
        text = _escape_ass(shot.caption_text.strip())
        text = _wrap_caption(text, max_words_per_line=8)
        lines.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{text}"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return dest


def _wrap_caption(text: str, max_words_per_line: int = 8) -> str:
    """
    Insert a hard ASS line-break (\\N) so long captions render as
    two lines instead of one wall of small text.

    Strategy: count words; if <= max_words_per_line, return unchanged.
    Otherwise split as close to the midpoint as possible at a word
    boundary, preferring breaks after punctuation when nearby.

    Documentary subtitle convention is 6-7 words per line.  We default
    to splitting any caption over 8 words.
    """
    words = text.split()
    if len(words) <= max_words_per_line:
        return text

    mid = len(words) // 2
    # Look ±2 positions around the midpoint for a word ending in
    # punctuation (commas, periods, full stop, semicolons).  Breaking
    # at a comma reads more naturally than mid-clause.
    PUNCT_CHARS = "،,.;؛—"
    best = mid
    for offset in (0, -1, 1, -2, 2):
        idx = mid + offset
        if 0 < idx < len(words) and words[idx - 1] and words[idx - 1][-1] in PUNCT_CHARS:
            best = idx
            break

    line1 = " ".join(words[:best])
    line2 = " ".join(words[best:])
    return f"{line1}\\N{line2}"


# ── Concat + mux ────────────────────────────────────────────────────── #

def _bg_motion_clip(asset_path: Path, out_path: Path, duration: float,
                    config: "RenderConfig", visual: str = "archive",
                    *, zoom_start: float = 1.0,
                    pan_start: tuple[float, float] = (0.0, 0.0),
                    motion: str | None = None,
                    intensity: float | None = None) -> Path:
    """Render a moving background clip from a real-image asset for the
    typography-over-image path: 2.5D parallax when enabled, else a gentle
    zoompan.  Emits the pipeline-standard encoder profile.

    ``zoom_start`` / ``pan_start`` let the clip continue from the preceding real
    shot's end framing (camera continuity), and ``motion`` / ``intensity``
    override the per-visual defaults for the gentle continued drift.  ``visual``
    is carried from the source shot so amp/depth-softness match — making the seam
    frame pixel-identical to the real shot's last frame.
    """
    if config.parallax:
        try:
            overrides: dict = {"zoom_start": zoom_start, "pan_start": pan_start}
            if motion is not None:
                overrides["motion"] = motion
            if intensity is not None:
                overrides["intensity"] = intensity
            mp.render_shot_parallax(
                asset_path, out_path, duration_sec=duration, visual=visual,
                fps=config.fps, out_w=config.width, out_h=config.height,
                backend=config.parallax_backend, warp=config.parallax_warp,
                **overrides,
            )
            return out_path
        except Exception as exc:
            log.warning("typography-over-image bg parallax failed (%s) — zoompan",
                        exc)
    _png_to_clip(asset_path, out_path, duration=duration, fps=config.fps,
                 width=config.width, height=config.height, motion="slow_push")
    return out_path


def _overlay_png_on_clip(bg_clip: Path, overlay_png: Path, out_path: Path, *,
                         fps: int) -> Path:
    """Burn a static RGBA overlay PNG over a moving clip, re-encoding with the
    pipeline-standard profile (libx264/ultrafast/crf22/yuv420p) so
    concat-by-copy stays valid."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(bg_clip),
        "-i", str(overlay_png),
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path


def _rotate_backdrop(fetcher, src_shot_index: int | None,
                     alt_ptrs: dict[int, int], current_asset: Path,
                     assets_dir: Path, i: int) -> Path | None:
    """P1.3 — pick the source shot's next-ranked dossier alternate as a fresh
    backdrop for a long typography-over-image run.

    Returns a render-ready PNG or None (no dossier / no alternates left /
    everything a near-duplicate of what's already on screen).  A None simply
    keeps the current backdrop — the pre-P1.3 behaviour."""
    decisions = getattr(fetcher, "decisions", None) if fetcher else None
    if decisions is None or src_shot_index is None:
        return None
    try:
        resolved = decisions.resolve_detailed(
            src_shot_index, fetcher.config.review_dir)
    except Exception as exc:  # noqa: BLE001 — rotation is best-effort
        log.debug("Backdrop rotation: resolve failed (%s)", exc)
        return None
    alts = resolved.alternates
    if not alts:
        return None

    from .sources.dedupe import dhash, near_duplicate
    cur_hash = dhash(current_asset)
    ptr = alt_ptrs.get(src_shot_index, 0)
    while ptr < len(alts):
        cand = alts[ptr]
        ptr += 1
        if near_duplicate(dhash(cand), cur_hash):
            continue
        try:
            from PIL import Image
            out = assets_dir / f"shot_{i:03d}_bgrot.png"
            with Image.open(cand) as im:
                im.convert("RGB").save(out, format="PNG", optimize=True)
            alt_ptrs[src_shot_index] = ptr
            log.info("Overlay run > rotate: backdrop switched to alternate "
                     "%s (source shot %d)", cand.name, src_shot_index)
            return out
        except Exception as exc:  # noqa: BLE001
            log.debug("Backdrop rotation: %s unusable (%s)", cand, exc)
    alt_ptrs[src_shot_index] = ptr
    return None


def _overlay_steps_on_clip(bg_clip: Path, steps: list[Path], out_path: Path, *,
                           fps: int, duration: float) -> Path:
    """Word-by-word reveal (§7.9): stack CUMULATIVE overlay PNGs with timed
    FFmpeg enables.  Step k appears at its time and stays (each later step
    fully covers the earlier ones, so the topmost active overlay is the most
    complete card).  Same encoder profile as every other clip — concat-by-copy
    stays valid.

    The reveal completes within min(1.6 s, 45% of the shot) so even a 3.5 s
    quote is fully readable for most of its hold.
    """
    if len(steps) == 1:
        return _overlay_png_on_clip(bg_clip, steps[0], out_path, fps=fps)

    reveal_span = min(1.6, duration * 0.45)
    interval = reveal_span / (len(steps) - 1)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(bg_clip)]
    for p in steps:
        cmd += ["-i", str(p)]
    parts = []
    prev = "[0:v]"
    for k in range(len(steps)):
        t_k = k * interval
        out_lbl = f"[v{k+1}]"
        enable = f":enable='gte(t,{t_k:.3f})'" if k else ""
        parts.append(f"{prev}[{k+1}:v]overlay=0:0:format=auto{enable}{out_lbl}")
        prev = out_lbl
    filter_graph = ";".join(parts) + f";{prev}format=yuv420p[vout]"
    cmd += [
        "-filter_complex", filter_graph, "-map", "[vout]",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path


# ── Act-break "breath" (dip through black) ──────────────────────────── #

# Pipeline-standard per-clip encoder profile.  Kept identical to the one
# baked into _png_to_clip / _overlay_png_on_clip so anything re-encoded here
# still concatenates by stream-copy.
_STD_ENCODE = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
               "-pix_fmt", "yuv420p"]


def _apply_edge_fades(in_clip: Path, out_clip: Path, *, fps: int,
                      head: float = 0.30, tail: float = 0.30) -> Path:
    """Re-encode a clip so it fades IN from black and OUT to black.

    Used on section-break interstitials.  Because the clip now starts and
    ends on a full-black frame, the surrounding hard cuts land on black and
    become invisible — i.e. a clean dip-through-black act break with zero
    concat surgery on the neighbouring shots.  Encoder profile is identical
    to every other clip, so concat-by-copy stays valid.
    """
    dur = _probe_seconds(in_clip)
    # Guard tiny clips: never let the two fades overlap or exceed the clip.
    head = max(0.0, min(head, dur * 0.45))
    tail = max(0.0, min(tail, dur * 0.45))
    fades = []
    if head > 0:
        fades.append(f"fade=t=in:st=0:d={head:.3f}")
    if tail > 0:
        fades.append(f"fade=t=out:st={max(0.0, dur - tail):.3f}:d={tail:.3f}")
    if not fades:
        shutil.copy(in_clip, out_clip)
        return out_clip
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(in_clip),
           "-vf", ",".join(fades + ["format=yuv420p"]),
           *_STD_ENCODE, "-r", str(fps), str(out_clip)]
    subprocess.run(cmd, check=True)
    return out_clip


def _probe_seconds(path: Path) -> float:
    """Duration in seconds via ffprobe (0.0 on failure)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _concat_clips(clips: list[Path], out_path: Path) -> Path:
    """Stream-copy concat — zero re-encode cost when all clips share specs."""
    if len(clips) == 1:
        shutil.copy(clips[0], out_path)
        return out_path

    list_file = out_path.parent / f"_concat_{out_path.stem}.txt"
    list_file.write_text(
        "\n".join(f"file '{c.resolve()}'" for c in clips),
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    list_file.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Concat failed:\n{result.stderr[-1200:]}"
        )
    return out_path


def _mux_final(background: Path, out_path: Path,
              audio_path: Path | None,
              subtitle_path: Path | None,
              max_duration: float,
              grade: str | None = None,
              grade_map_vparts: "list[str] | None" = None,
              caption_backplate: str | None = None,
              music_path: Path | None = None,
              music_gain_db: float = -18.0,
              duck: bool = True,
              fade_open: float = 0.8,
              fade_close: float = 1.0) -> Path:
    """
    Final pass: color grade, caption backplate, burn captions, mux audio,
    open/close fades, optional ducked music bed, hard-trim.

    Video chain order: grade → backplate → ass → fade-in/out.
    - Grade first so subtitles and backplate sit on the graded plate.
    - Backplate before ass so captions render ON TOP of the charcoal bar.
    - Fades last so they dip the *finished* frame (captions included) to black.

    Audio:
    - Narration alone when no `music_path` (with a short fade-in/out so it
      never clicks on/off).
    - With `music_path`: the bed is looped to length, attenuated to
      `music_gain_db`, optionally side-chain *ducked* under the narration
      (so dialogue always wins), faded in at the head and out at the tail,
      and mixed under the voice.  The swell lives in the gaps (intro, section
      breaths, outro) where there's no narration to duck it.

    `caption_backplate`: one of CAPTION_BACKPLATES keys, or None.
    `music_gain_db`: bed level relative to full scale (negative dB).
    `duck`: side-chain compress the bed under the narration.
    `fade_open` / `fade_close`: black fade durations (s); 0 disables.
    """
    has_audio = bool(audio_path and audio_path.exists())
    has_music = bool(music_path and music_path.exists())

    # End time used to place the fade-out: the hard-trim length when known,
    # else the background's own duration.
    end = max_duration if max_duration > 0 else _probe_seconds(background)

    # ── Inputs ───────────────────────────────────────────────────────── #
    inputs = ["-i", str(background)]
    narr_idx = music_idx = None
    if has_audio:
        narr_idx = len(inputs) // 2
        inputs += ["-i", str(audio_path)]
    if has_music:
        music_idx = len(inputs) // 2
        # -stream_loop -1 repeats a short bed to cover the whole film.
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]

    # ── Video filter chain (label [v]) ──────────────────────────────── #
    vparts: list[str] = []
    if grade_map_vparts:
        # Per-section grading (P3.4) — timeline-enabled stages replace the
        # single global preset entirely (unmapped sections already fall back
        # to the global grade inside _grade_map_vparts).
        vparts.extend(grade_map_vparts)
        log.info("Per-section grade map applied (%d filter stages)",
                 len(grade_map_vparts))
    elif grade:
        preset = GRADE_PRESETS.get(grade)
        if preset:
            vparts.append(preset)
            log.info("Color grade applied: %s", grade)
        else:
            log.warning("Unknown --grade '%s'; falling back to ungraded. "
                        "Valid presets: %s", grade, sorted(GRADE_PRESETS))

    has_subs = bool(subtitle_path and subtitle_path.exists())
    if caption_backplate and caption_backplate != "off" and has_subs:
        bp = CAPTION_BACKPLATES.get(caption_backplate)
        if bp is not None:
            vparts.append(bp)
            log.info("Caption backplate applied: %s", caption_backplate)
        else:
            log.warning("Unknown --caption-backplate '%s'; skipping. "
                        "Valid: %s", caption_backplate, sorted(CAPTION_BACKPLATES))

    if has_subs:
        safe = str(subtitle_path).replace("\\", "/").replace(":", "\\:")
        ass_args = [f"ass={safe}"]
        try:
            regular = FONT_PATHS.get("regular")
            if regular:
                font_dir = str(Path(regular).parent).replace("\\", "/").replace(":", "\\:")
                ass_args.append(f"fontsdir={font_dir}")
        except Exception:
            pass
        vparts.append(":".join(ass_args))

    if fade_open and fade_open > 0:
        vparts.append(f"fade=t=in:st=0:d={fade_open:.3f}")
    if fade_close and fade_close > 0 and end > fade_close:
        vparts.append(f"fade=t=out:st={end - fade_close:.3f}:d={fade_close:.3f}")
    vparts.append("format=yuv420p")

    fc = [f"[0:v]{','.join(vparts)}[v]"]

    # ── Audio graph (label [a]) ─────────────────────────────────────── #
    amap = None
    if has_audio or has_music:
        # narration → [narr] (gentle de-click fades; never touches sync)
        a_fclose = min(0.4, end) if end > 0 else 0.4
        if has_audio:
            nf = ["aresample=async=1"]
            nf.append("afade=t=in:st=0:d=0.05")
            if end > a_fclose:
                nf.append(f"afade=t=out:st={end - a_fclose:.3f}:d={a_fclose:.3f}")
            fc.append(f"[{narr_idx}:a]{','.join(nf)}[narr]")

        if has_music:
            gain = 10 ** (music_gain_db / 20.0)        # dB → linear
            mf = [f"volume={gain:.4f}"]
            # Swell the bed up where there's no narration (head/tail/breaths).
            mf.append("afade=t=in:st=0:d=2.0")
            if end > 3.0:
                mf.append(f"afade=t=out:st={end - 3.0:.3f}:d=3.0")
            fc.append(f"[{music_idx}:a]{','.join(mf)}[bed0]")

            # A filtergraph label is single-use; when the narration must feed
            # BOTH the mixer and the ducking sidechain, split it first.
            narr_mix = "[narr]"
            if has_audio and duck:
                fc.append("[narr]asplit=2[narrmix][narrsc]")
                narr_mix = "[narrmix]"
                # Bed (main) compressed by the narration (sidechain key) →
                # dialogue ducks the music automatically.
                fc.append(
                    "[bed0][narrsc]sidechaincompress="
                    "threshold=0.03:ratio=6:attack=5:release=350:makeup=1[bed]")
            else:
                fc.append("[bed0]anull[bed]")

            if has_audio:
                # normalize=0 keeps levels as authored (no amix halving).
                fc.append(f"{narr_mix}[bed]amix=inputs=2:duration=first:"
                          "dropout_transition=0:normalize=0[a]")
            else:
                fc.append("[bed]anull[a]")
            amap = "[a]"
            log.info("Music bed: %.0f dB%s", music_gain_db,
                     ", ducked under narration" if (has_audio and duck) else "")
        else:
            amap = "[narr]"

    # ── Assemble command ────────────────────────────────────────────── #
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(fc), "-map", "[v]"]
    if amap:
        cmd += ["-map", amap]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "192k"] if amap else ["-an"]
    if max_duration > 0:
        cmd += ["-t", f"{max_duration:.3f}"]
    else:
        # No explicit trim: still bound the (looped) music to the video.
        cmd += ["-shortest"]
    cmd.append(str(out_path))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"Final mux failed:\n{result.stderr[-1600:]}")
    return out_path


# ── Top-level orchestrator ──────────────────────────────────────────── #

@dataclass
class RenderConfig:
    """All renderer configuration in one place."""
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: int = DEFAULT_FPS
    add_captions: bool = True
    # Stage 2: optional image fetcher.  When None, all image-kind
    # shots get placeholder cards (Stage 1 behaviour).
    fetcher: object = None   # phase3.sources.Fetcher (avoid import cycle)
    # Optional path to a book-cover image.  When set, title_card shots
    # are rendered with the cover as full-frame background + gold title
    # rather than the default cream card.  See typography._render_title_card.
    book_cover: Path | None = None
    # How the book cover is fitted into the 16:9 frame.  "fill" (default)
    # scales-and-crops, "contain" letterboxes with cream bars, "blur_pad"
    # letterboxes with a blurred-cover background.  Only meaningful when
    # book_cover is set.
    book_cover_fit: str = "fill"
    # Horizontal alignment of the book cover within the frame:
    # "center" (default), "left", or "right".  Only meaningful with
    # contain / blur_pad — fill leaves no spare horizontal space.
    # The gold title overlay automatically shifts to the opposite side.
    book_cover_align: str = "center"
    # Apply a 0.3 s zoom-in accent (1.00 → 1.05) at the start of every
    # section_mark shot to signal "new chapter".  All other typography
    # visuals (title_card, typography, chapter_heading) remain static.
    # Set False to restore the pre-issue-3 behaviour (all typography
    # static_hold).
    section_mark_accent: bool = True
    # Typography family — "A" (Aljazeera-editorial, cream/charcoal,
    # default) or "B" (Netflix-doc cinematic, dark gradient).  Reserved:
    # "C" (manuscript, sepia + ornament) lands in a follow-up patch.
    # Unknown values fall back to Family A.  Selectable via the
    # --typography-family CLI flag in phase3_run.py.
    typography_family: str = "A"
    # Color grade preset applied at the final mux stage.  One of
    # GRADE_PRESETS keys ("warm", "cool", "neutral", "bw") or None for
    # ungraded.  Default "warm" matches the cinematic-warm look agreed
    # for issue 1.  Grading runs BEFORE caption burn-in so subtitles
    # remain crisp white regardless of preset.  Selectable via --grade
    # in render_plan.py.
    grade: str | None = DEFAULT_GRADE
    # Per-section grading (P3.4): {section_id: grade_name} applied at the
    # final mux via timeline-enabled filters (GRADE_PRESETS_TIMELINE).
    # Sections not in the map fall back to `grade`.  None → single global
    # grade (previous behaviour).  --grade-map FILE.json in render_plan.py.
    grade_map: dict | None = None
    # Caption charcoal-bar backplate (issue 4 patch B).  One of
    # CAPTION_BACKPLATES keys ("off", "subtle", "solid").  Applied as a
    # `drawbox` filter after grade and before ass burn-in, so it sits
    # behind the captions but on top of the graded plate.  "subtle" is
    # the recommended default for varied source material.  No-ops when
    # add_captions=False (no captions to back).
    caption_backplate: str = DEFAULT_CAPTION_BACKPLATE
    # ── Notebook-controllable text styling ──────────────────────────── #
    # Main title (title_card): a size multiplier on the default title_main/
    # title_sub sizes, and an RGB colour override.  None colour → family
    # default (Family A: aged gold).  --title-size / --title-color.
    title_scale: float = 1.0
    title_color: tuple[int, int, int] | None = None
    # Optional sub-line under the main title (author / date range / tagline,
    # e.g. "١٨٨٥ – ١٩٣٦").  Empty → title only; the accent rule still draws
    # so the block reads anchored.  --title-subtitle.
    title_subtitle: str = ""
    # Over-image typography scrim plate: "auto" | "off" | "soft" | "band".
    # None → the typography_common default ("auto": sample the backdrop under
    # the text block, escalate off→soft→band only for bright/busy frames).
    # Only affects the --typography-over-image path.  --text-scrim.
    text_scrim: str | None = None
    # Over-image text vertical anchor: "auto" (lower third for quotes/names/
    # dates, centre for section marks) | "center" | "lower".  --overlay-anchor.
    overlay_anchor: str = "auto"
    # Word-by-word reveal on over-image pull quotes / name reveals (§7.9).
    # Default ON since P7.4 (screened opt-in through P5/P6): the quote
    # extends word-group by word-group over the first ~1.6 s instead of
    # appearing whole.  --no-word-reveal to disable.
    word_reveal: bool = True
    # Near-subliminal card_drift push on typography cards (P7.4) so text
    # beats are never frozen frames.  section_mark keeps its accent;
    # False restores the pre-P7.4 static holds.
    card_motion: bool = True
    # P1.3: when a continuous typography-over-image run keeps one backdrop on
    # screen longer than this many seconds, the NEXT overlay card switches to
    # the source shot's next-ranked dossier alternate (near-duplicates
    # skipped), so a long quote run reads as an edited sequence instead of a
    # frozen frame.  0 disables.  --backdrop-rotate.
    overlay_backdrop_rotate_sec: float = 10.0
    # Narration captions (only used when add_captions is True, i.e. NOT
    # --no-captions): size multiplier on the 5%-of-height default, an ASS
    # colour string (&HAABBGGRR) override, and vertical position as a fraction
    # of height from the bottom (default 0.06).  --caption-size /
    # --caption-color / --caption-pos.
    caption_size: float = 1.0
    caption_color: str | None = None
    caption_pos: float | None = None
    # Sentence-level caption events (P7.2) from the v2 plan JSON (list of
    # plan.CaptionEvent or anything with .start/.end/.text).  When set,
    # subtitles follow the narration's sentences and survive shot cuts
    # instead of being chopped into per-shot fragments.  None → legacy
    # per-shot captions.
    caption_events: list | None = None
    # 2.5D depth parallax (opt-in).  When True, real-image shots whose
    # visual is not in motion_parallax.PARALLAX_SKIP_VISUALS are rendered
    # as depth-parallax clips (foreground moves more than background)
    # instead of the flat zoompan motion — emitted with the identical
    # encoder profile so concat-by-copy stays valid.  Depth is estimated
    # once per image (cached as a <stem>.depth.png sidecar, optionally
    # pre-warmed by prebuild_assets.py --parallax) via `parallax_backend`.
    # Off by default — same opt-in rollout as section_mark_accent.
    # Selectable via --parallax in render_plan.py.
    parallax: bool = False
    parallax_backend: str = "depthanything"   # or "classical" (CPU fallback)
    parallax_warp: str = "auto"               # auto | backward | inpaint
    # Typography-over-image (opt-in).  When True, typography / section_mark
    # shots are composited as a text overlay on top of the most recent
    # real-image shot's footage (parallaxing if `parallax` is on) instead of
    # a flat static card — collapsing the "static text card" share that makes
    # a documentary feel like a slideshow.  title_card is excluded (it keeps
    # its book-cover treatment).  Falls back to a static card when no recent
    # real image is available (e.g. before the first photo).  Default ON
    # since P7.4 (matches the Streamlit default); --no-typography-over-image
    # in render_plan.py to disable.
    typography_over_image: bool = True
    # ── Cinematic final-assembly (audio score + fades) ──────────────── #
    # Optional music bed mixed UNDER the narration at the final mux.  When
    # set, the bed is looped to length, attenuated to `music_gain_db`,
    # side-chain ducked under the voice (so dialogue always wins), and faded
    # in/out — the swell lives in the gaps (intro, section breaths, outro).
    # None → narration only (previous behaviour).  --music in render_plan.py.
    music_path: Path | None = None
    music_gain_db: float = -12.0      # bed level vs full scale (negative dB)
    music_duck: bool = True           # side-chain compress bed under narration
    # Fades.  `fades` gates both the whole-film black open/close AND the
    # dip-through-black "breath" on section-break interstitials.  The black
    # bookends make the film start/end from darkness; the section breaths
    # make each act land cleanly (the surrounding hard cuts hit black frames
    # and vanish).  Set False to restore straight cuts everywhere.
    fades: bool = True
    fade_open: float = 0.8            # fade-from-black at film start (s)
    fade_close: float = 1.0           # fade-to-black at film end (s)
    section_fade: float = 0.30        # per-interstitial head/tail dip (s)


def render_video(shots: list[Shot], out_path: Path, *,
                audio_path: Path | None = None,
                audio_duration_sec: float | None = None,
                config: RenderConfig | None = None,
                on_progress: Callable[[str, float], None] | None = None) -> Path:
    """
    Render a complete video from a shot plan.

    Parameters
    ----------
    shots                The plan (from plan.build_shot_plan or plan.load_plan).
    out_path             Where to write the final MP4.
    audio_path           Phase 2 TTS output.  Optional; without it the
                         video is silent.
    audio_duration_sec   Hard duration cap.  Defaults to the plan's last
                         shot end time.
    config               RenderConfig (defaults: 1920x1080 @ 25 fps).
                         Set config.fetcher to a phase3.sources.Fetcher
                         to enable real image fetching.
    on_progress          Callback(label, fraction).

    Returns the path to the finished MP4.
    """
    config = config or RenderConfig()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not shots:
        raise ValueError("Cannot render an empty shot plan")

    if audio_duration_sec is None:
        audio_duration_sec = shots[-1].end

    def _prog(label: str, frac: float) -> None:
        log.info("[render %.0f%%] %s", frac * 100, label)
        if on_progress:
            on_progress(label, frac)

    with tempfile.TemporaryDirectory(prefix="bk2v_render_") as tmp:
        work = Path(tmp)
        assets_dir = work / "assets"
        clips_dir = work / "clips"
        assets_dir.mkdir()
        clips_dir.mkdir()

        # ── Per-shot: PNG asset → MP4 clip ────────────────────────── #
        # Each shot is rendered defensively — if any single shot fails
        # (FFmpeg error, fetched image won't open, etc), we emit a
        # neutral error card and continue rather than killing the whole
        # render.  The audio stays in sync because each error card
        # has the exact duration of the failed shot.
        shot_clips: list[Path] = []
        n = len(shots)
        # Most recent real-image asset (+ its persisted depth sidecar), reused
        # as the moving background for typography-over-image overlay shots.
        last_real_asset: Path | None = None
        # Camera-continuity cursor for typography-over-image backgrounds.
        #   _ovl_cam_cursor : (cx, cy, zoom) the NEXT overlay bg should start from
        #   _ovl_cam_base   : real shot's end zoom (so a long card run is capped)
        # Both refresh to the real shot's end framing on every real-image shot;
        # each overlay then continues from the cursor and advances it, so a run of
        # consecutive cards is one continuous slow push instead of a sawtooth.
        _ovl_cam_cursor: tuple[float, float, float] | None = None
        _ovl_cam_base: float | None = None
        _last_real_visual: str | None = None
        # P1.3 backdrop-rotation state: when did the current continuous
        # footage run start, which shot supplied the backdrop, and how many
        # of its alternates have been consumed already.
        _footage_run_start: float | None = None
        _backdrop_src_index: int | None = None
        _backdrop_alt_ptr: dict[int, int] = {}
        for i, shot in enumerate(shots):
            asset_path = assets_dir / f"shot_{i:03d}.png"
            clip_path  = clips_dir  / f"shot_{i:03d}.mp4"

            try:
                rendered = False

                # (A) Typography-over-image (opt-in): composite the text as an
                # overlay on the most recent real image's footage instead of a
                # flat static card.  Needs a prior real image to sit over;
                # otherwise we fall through to the normal static-card path.
                if (config.typography_over_image
                        and shot.visual in _OVERLAY_VISUALS
                        and last_real_asset is not None):
                    # P1.3: a long run of overlays on one backdrop reads as a
                    # frozen frame — past the threshold, switch to the source
                    # shot's next-ranked alternate and restart the camera.
                    if (config.overlay_backdrop_rotate_sec > 0
                            and _footage_run_start is not None
                            and (shot.end - _footage_run_start
                                 > config.overlay_backdrop_rotate_sec)):
                        rotated = _rotate_backdrop(
                            config.fetcher, _backdrop_src_index,
                            _backdrop_alt_ptr, last_real_asset,
                            assets_dir, i)
                        if rotated is not None:
                            last_real_asset = rotated
                            _ovl_cam_cursor = None
                            _ovl_cam_base = None
                            _footage_run_start = shot.start
                    try:
                        spec = _typography_spec(
                            shot, width=config.width, height=config.height,
                            typography_family=config.typography_family,
                            title_scale=config.title_scale,
                            title_color=config.title_color,
                            scrim=config.text_scrim)
                        # Over-image extras: anchor policy + the backdrop the
                        # text will sit on (consulted by scrim="auto" to pick
                        # a plate for bright/busy frames).
                        spec.overlay_anchor = config.overlay_anchor
                        spec.backdrop_path = last_real_asset
                        # Word-by-word reveal (opt-in): quotes and name
                        # reveals build up word-group by word-group; other
                        # templates (and reveal off) stay single-card.
                        if (config.word_reveal
                                and shot.visual == "typography"
                                and spec.template in ("pull_quote",
                                                      "name_reveal")):
                            overlay_steps = render_typography_overlay_steps(
                                spec, assets_dir, f"shot_{i:03d}_ovl")
                        else:
                            overlay_png = assets_dir / f"shot_{i:03d}_ovl.png"
                            render_typography_overlay(spec, overlay_png)
                            overlay_steps = [overlay_png]
                        bg_clip = clips_dir / f"shot_{i:03d}_bg.mp4"
                        # Continue the camera from the prior real shot's end
                        # framing (no shrink-pop at the cut).  When parallax is
                        # off, or there's no cursor yet, this is the wide default.
                        if config.parallax and _ovl_cam_cursor is not None:
                            cx0, cy0, z0 = _ovl_cam_cursor
                            _bg_motion_clip(
                                last_real_asset, bg_clip, shot.duration, config,
                                visual=(_last_real_visual or "archive"),
                                zoom_start=z0, pan_start=(cx0, cy0),
                                motion=_OVERLAY_CONT_MOTION,
                                intensity=_OVERLAY_CONT_INTENSITY)
                            # Advance the cursor by this card's own drift so the
                            # next consecutive card picks up seamlessly; cap creep.
                            drift = mp.camera_path(
                                _OVERLAY_CONT_MOTION, 1.0,
                                _OVERLAY_CONT_INTENSITY)[2] - 1.0
                            z_next = z0 + drift
                            if _ovl_cam_base is not None:
                                z_next = min(z_next,
                                             _ovl_cam_base + _OVERLAY_ZOOM_CAP_EXTRA)
                            _ovl_cam_cursor = (cx0, cy0, z_next)
                        else:
                            _bg_motion_clip(last_real_asset, bg_clip,
                                            shot.duration, config)
                        _overlay_steps_on_clip(bg_clip, overlay_steps,
                                               clip_path, fps=config.fps,
                                               duration=shot.duration)
                        rendered = True
                    except Exception as exc:
                        log.warning("Shot %d typography-over-image failed (%s) "
                                    "— static card", i + 1, exc)

                # (B) Normal path: build the PNG asset, then motion-encode.
                if not rendered:
                    _, is_real_image = _build_shot_asset(
                        shot, i + 1, asset_path,
                        width=config.width, height=config.height,
                        fetcher=config.fetcher,
                        book_cover=config.book_cover,
                        book_cover_fit=config.book_cover_fit,
                        book_cover_align=config.book_cover_align,
                        typography_family=config.typography_family,
                        parallax=config.parallax,
                        parallax_backend=config.parallax_backend,
                        title_scale=config.title_scale,
                        title_color=config.title_color,
                        title_subtitle=config.title_subtitle,
                        scrim=config.text_scrim,
                    )
                    if is_real_image:
                        last_real_asset = asset_path
                        # Refresh the camera-continuity cursor to this shot's end
                        # framing so any following typography-over-image cards
                        # continue from here instead of snapping back to wide.
                        # (Computed from the per-visual parallax path; harmless
                        # when parallax is off — it just won't be consumed.)
                        _last_real_visual = shot.visual
                        _footage_run_start = shot.start
                        _backdrop_src_index = i + 1   # dossier is 1-indexed
                        if config.parallax:
                            end_cam = mp.shot_end_camera(shot.visual)
                            _ovl_cam_cursor = end_cam
                            _ovl_cam_base = end_cam[2]

                    # 2.5D parallax (opt-in) replaces the flat zoompan encode for
                    # real-image shots.  It emits the clip directly with the same
                    # encoder profile, so concat-by-copy stays valid.  Any failure
                    # falls back to the normal motion path below.
                    if (config.parallax and is_real_image
                            and shot.visual not in mp.PARALLAX_SKIP_VISUALS):
                        try:
                            mp.render_shot_parallax(
                                asset_path, clip_path,
                                duration_sec=shot.duration, visual=shot.visual,
                                fps=config.fps,
                                out_w=config.width, out_h=config.height,
                                backend=config.parallax_backend,
                                warp=config.parallax_warp,
                            )
                            rendered = True
                        except Exception as exc:
                            log.warning("Shot %d parallax failed (%s) — falling "
                                        "back to %s", i + 1, exc, shot.motion)

                if not rendered:
                    # Real images take the planner's motion.  Typography
                    # cards are no longer frozen frames (P7.4): they get a
                    # near-subliminal card_drift push — a static text card
                    # is the single biggest "slide show" tell.  section_mark
                    # keeps its 0.3 s new-chapter accent, and card motion as
                    # a whole can be disabled via RenderConfig.card_motion.
                    if is_real_image:
                        shot_motion = shot.motion
                    elif (shot.visual == "section_mark"
                          and config.section_mark_accent):
                        shot_motion = "section_accent"
                    elif config.card_motion:
                        shot_motion = "card_drift"
                    else:
                        shot_motion = "static_hold"
                    _png_to_clip(asset_path, clip_path,
                                 duration=shot.duration,
                                 fps=config.fps,
                                 width=config.width, height=config.height,
                                 motion=shot_motion,
                                 intensity=(shot.motion_intensity
                                            if is_real_image else 1.0))
            except Exception as exc:
                log.error("Shot %d (%s) failed: %s — emitting error card",
                          i + 1, shot.visual, exc)
                # Emit an error card so the timeline doesn't collapse
                _error_card(shot, i + 1, asset_path,
                            config.width, config.height, str(exc))
                _png_to_clip(asset_path, clip_path,
                             duration=shot.duration,
                             fps=config.fps,
                             width=config.width, height=config.height,
                             motion="static_hold")

            # Act-break "breath": fade section interstitials in from / out to
            # black so the surrounding hard cuts land on black and disappear.
            if (config.fades and config.section_fade > 0
                    and shot.visual in {"section_mark", "chapter_heading"}):
                try:
                    faded = clip_path.with_name(clip_path.stem + "_fade.mp4")
                    _apply_edge_fades(clip_path, faded, fps=config.fps,
                                      head=config.section_fade,
                                      tail=config.section_fade)
                    clip_path = faded
                except Exception as exc:
                    log.warning("Shot %d: act-break fade failed (%s) — straight cut",
                                i + 1, exc)

            shot_clips.append(clip_path)

            _prog(f"shot {i+1}/{n}: {shot.visual}", 0.05 + 0.70 * (i+1) / n)

        # ── Concat all clips ──────────────────────────────────────── #
        _prog("concat all shots", 0.80)
        bg_path = work / "background.mp4"
        _concat_clips(shot_clips, bg_path)

        # ── Caption layer ────────────────────────────────────────── #
        ass_path: Path | None = None
        if config.add_captions:
            _prog("generating captions", 0.86)
            ass_path = _write_captions(shots, work / "captions.ass",
                                       width=config.width, height=config.height,
                                       caption_size=config.caption_size,
                                       caption_color=config.caption_color,
                                       caption_pos=config.caption_pos,
                                       events=config.caption_events)

        # ── Final mux: burn captions + mux audio + hard-trim ──────── #
        _prog("mux audio and captions", 0.92)
        _mux_final(bg_path, out_path,
                  audio_path=audio_path,
                  subtitle_path=ass_path,
                  max_duration=audio_duration_sec,
                  grade=config.grade,
                  grade_map_vparts=(
                      _grade_map_vparts(config.grade_map, shots, config.grade)
                      if config.grade_map else None),
                  caption_backplate=config.caption_backplate,
                  music_path=config.music_path,
                  music_gain_db=config.music_gain_db,
                  duck=config.music_duck,
                  fade_open=config.fade_open if config.fades else 0.0,
                  fade_close=config.fade_close if config.fades else 0.0)

    _prog("done", 1.0)
    log.info("Rendered video → %s", out_path)
    return out_path
