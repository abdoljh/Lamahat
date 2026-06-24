"""
Phase 3 — Typography Family C (manuscript, sepia + ornament).

Aged-paper palette with sepia ink, faded burgundy accents, and decorative
flourishes drawn directly from Arabic manuscript margins.  Where Family A
is editorial restraint and Family B is cinematic minimalism, Family C
leans into ornament — manuscript brackets, double-rules, visible
decorative quote marks.

Templates implement the same 5-renderer contract:
    (spec: TypographySpec) -> PIL.Image.Image

Design philosophy
-----------------
Classical Quranic and historical-Arabic manuscripts use a small but
recognisable visual vocabulary: warm aged-paper backgrounds (often with
a slight vignette where the page edges have darkened), sepia or
burgundy ink for body text, gold or burgundy ornaments framing
headings, and double-rules separating sections.  Family C distils those
moves into the same 5-template renderer contract as Families A and B.

Differences from A and B
------------------------
- Background: vignetted aged paper (slight darkening at corners),
  heavier grain than A/B to mimic manuscript paper texture
- Primary text: deep sepia (#3D2F1F), not charcoal or off-white
- Accent: faded burgundy (#7A2E2A) for rules, brackets, decorative marks
- Section_mark gets bracket ornaments framing the text + a double-rule
  below.  No diamond.
- Pull_quote draws visible «» decorative marks at burgundy/medium
  opacity — these are a design feature here, not a hidden trick.
- Title card (no cover): aged paper with bracket ornaments framing
  the title and double-rules above/below
- Title card (with cover): same composite as A/B but with a burgundy-
  tinted gradient instead of charcoal/dark, and the title in burgundy
  rather than gold/off-white

History: new in §15.2 part 2, following Family B (typography_b.py).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .typography_common import (
    # shared design tokens (not family-specific)
    LINE_HEIGHT_MULT, MARGINS, PULL_QUOTE_THRESHOLDS,
    RULE_THICKNESS_PX_1080,
    # font availability
    FONT_PATHS,
    # shared spec + helpers
    TypographySpec,
    _font, _size, _measure, _draw_text_rtl, _fit_text_to_width, _apply_grain,
    _wrap_by_width, _draw_centred_lines,
    _resize_cover_to_fill, _resize_cover_to_contain,
    _make_cover_contain, _make_cover_blur_pad,
    # for quote-mark constants
    QUOTE_OPEN, QUOTE_CLOSE,
)


# ── Family C font selection ────────────────────────────────────────────── #
# Headlines use a calligraphic Quran face when available; falls back to
# Amiri Bold if neither Quran weight is present.  Body text (pull_quote
# lines, all subtitles, attribution) always uses the standard weights —
# Quran's metrics make multi-line wrapping unreliable.

def _headline_font(size_px: int):
    """Calligraphic font for Family C headlines.

    Preference order: quran_colored → quran → bold.  Quran weights are
    optional in the font discovery (only regular + bold are required),
    so this falls back gracefully on systems with a minimal Amiri
    install.
    """
    for slot in ("quran_colored", "quran"):
        if slot in FONT_PATHS:
            return _font(slot, size_px)
    return _font("bold", size_px)


# Quran glyphs have generous ascender/descender clearance for diacritics
# but Pillow's textbbox() returns only the tight bbox of rendered ink.
# When centering, that mismatch causes the headline + subtitle stack to
# overlap.  Add this fraction of the headline size as vertical padding
# whenever Family C uses a calligraphic headline.
HEADLINE_VPAD_FRAC = 0.35


def _headline_metrics(draw, text, font, fallback_h):
    """Measure a headline string + return (width, padded_height).

    The returned height includes Quran's required vertical breathing
    room.  `fallback_h` is used if measurement fails.
    """
    w, h = _measure(draw, text, font)
    if h <= 0:
        h = fallback_h
    return w, int(h * (1 + HEADLINE_VPAD_FRAC))


# ── Family C palette ───────────────────────────────────────────────────── #
# Aged-paper backgrounds and ink colours sampled from Ottoman-era
# manuscript photography.  The paper is warmer and slightly more
# saturated than Family A's cream because manuscript pages have absorbed
# centuries of warmth from the binding oils.

PAPER_LIGHT  = (232, 220, 192)   # #E8DCC0 — base aged paper
PAPER_DEEP   = (217, 200, 160)   # #D9C8A0 — corner vignette
PAPER_EDGE   = (192, 170, 130)   # #C0AA82 — extreme corner darkening

SEPIA_INK    = (61,  47,  31)    # #3D2F1F — primary body text
SEPIA_DIM    = (108, 90,  64)    # #6C5A40 — subtitles, attribution
BURGUNDY     = (122, 46,  42)    # #7A2E2A — rules, brackets, ornaments
BURGUNDY_DIM = (148, 94,  86)    # #945E56 — secondary ornament use


# ── Family C background helpers ────────────────────────────────────────── #

def _make_aged_paper_canvas(width: int, height: int) -> Image.Image:
    """
    Aged-paper canvas: warm base + radial vignette darkening at corners
    + heavier grain than Families A/B.

    The vignette uses a radial gradient computed analytically (not via
    PIL.ImageDraw.ellipse, which would alias).  The math: for each
    pixel, distance from centre normalised to half-diagonal, then
    eased with gamma 1.8 so the vignette is gentle near the centre
    and ramps up toward the corners.
    """
    # Base solid colour
    img = Image.new("RGB", (width, height), PAPER_LIGHT)

    # Radial vignette via a precomputed alpha-mask blend.
    # We sample sparsely (every 4 px) for speed, then blur — the result
    # is visually identical to per-pixel computation but ~16× faster.
    cx, cy = width / 2, height / 2
    half_diag = (cx ** 2 + cy ** 2) ** 0.5
    sample_step = 4
    sw, sh = width // sample_step, height // sample_step
    mask = Image.new("L", (sw, sh), 0)
    mpx = mask.load()
    for y in range(sh):
        for x in range(sw):
            px = x * sample_step
            py = y * sample_step
            dist = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
            frac = (dist / half_diag) ** 1.8
            # 0 at centre → 1 at corners; scale to 0–180 alpha range
            mpx[x, y] = min(180, int(180 * frac))
    mask = mask.resize((width, height), Image.LANCZOS)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=8))

    # Composite the deeper paper colour through the vignette mask.
    overlay = Image.new("RGB", (width, height), PAPER_EDGE)
    img = Image.composite(overlay, img, mask)

    # Slightly heavier grain than the default to mimic paper texture.
    # We use the shared _apply_grain twice — once at default, once with
    # a different seed offset would be cleaner, but applying twice is
    # the cheap path and gives the heavier-grain feel we want.
    _apply_grain(img)
    _apply_grain(img)
    return img


# ── Family C ornament helpers ──────────────────────────────────────────── #

def _draw_double_rule(draw: ImageDraw.ImageDraw, y: int,
                      width: int, height: int,
                      length_pct: float = 0.20,
                      gap_pct: float = 0.010,
                      colour: tuple = BURGUNDY) -> int:
    """
    Two parallel rules with a small gap between them — manuscript margins
    style.  Returns the y coordinate immediately below the second rule.
    """
    rule_len = int(width * length_pct)
    thickness = max(1, int(height * RULE_THICKNESS_PX_1080 / 1080))
    gap = max(2, int(height * gap_pct))
    x0 = (width - rule_len) // 2
    x1 = x0 + rule_len
    draw.rectangle([x0, y, x1, y + thickness], fill=colour)
    y2 = y + thickness + gap
    draw.rectangle([x0, y2, x1, y2 + thickness], fill=colour)
    return y2 + thickness


def _draw_bracket(draw: ImageDraw.ImageDraw,
                  cx: int, cy: int,
                  size: int,
                  facing: str,
                  colour: tuple = BURGUNDY,
                  thickness: int = 3) -> None:
    """
    Manuscript corner-bracket ornament.

    For ``facing="right"`` (opens rightward; sits to the LEFT of text):

           ┌───
           │
           │
           └───

    For ``facing="left"`` (mirrored, sits to the RIGHT of text):

        ───┐
           │
           │
        ───┘

    ``cx, cy`` is the **inner** anchor — i.e. the cy is the text mid-line
    and the vertical bar passes through cx.  Arms extend horizontally
    *toward* the text (no longer; just short stubs).  The bracket spans
    `size` vertically and `size * 0.45` horizontally.

    The earlier version added a small "terminus dot" on the outside of
    each arm.  At realistic title sizes this read as noise rather than
    ornament, so the dots are removed.  The earlier version also sized
    arms to 0.7 × size; that proved too long, so arms now extend only
    far enough to read as a corner (0.45 × size).
    """
    half = size // 2
    arm = int(size * 0.45)
    t = thickness

    if facing == "right":
        # Vertical bar (left of text)
        draw.rectangle([cx, cy - half, cx + t, cy + half], fill=colour)
        # Top arm extending rightward (toward text)
        draw.rectangle([cx, cy - half, cx + arm, cy - half + t], fill=colour)
        # Bottom arm extending rightward
        draw.rectangle([cx, cy + half - t, cx + arm, cy + half], fill=colour)
    else:  # "left" — mirror of above
        draw.rectangle([cx - t, cy - half, cx, cy + half], fill=colour)
        draw.rectangle([cx - arm, cy - half, cx, cy - half + t], fill=colour)
        draw.rectangle([cx - arm, cy + half - t, cx, cy + half], fill=colour)


# ── Template: title_card ───────────────────────────────────────────────── #

def _render_title_card(spec: TypographySpec) -> Image.Image:
    """Two modes: cover-image (burgundy-tinted) or aged-paper."""
    if spec.cover_image is not None and Path(spec.cover_image).exists():
        return _render_title_card_with_cover(spec)
    return _render_title_card_paper(spec)


def _render_title_card_with_cover(spec: TypographySpec) -> Image.Image:
    """
    Cover-image variant: composite the cover full-frame, apply a warm
    burgundy-tinted vignette (instead of A's charcoal or B's near-black),
    draw the title in burgundy.
    """
    fit = (spec.cover_fit or "fill").lower()
    align = (spec.cover_align or "center").lower()
    if align not in ("center", "left", "right"):
        align = "center"

    if fit == "contain":
        bg = _make_cover_contain(spec.cover_image, spec.width, spec.height,
                                 align, pad_colour=PAPER_DEEP)
    elif fit in ("blur_pad", "blur-pad", "blurpad"):
        bg = _make_cover_blur_pad(spec.cover_image, spec.width, spec.height,
                                  align=align)
    else:
        cover = Image.open(spec.cover_image).convert("RGB")
        bg = _resize_cover_to_fill(cover, spec.width, spec.height)

    # Warm burgundy-tinted bottom gradient.  Lighter than B's pure dark
    # because the title is burgundy (mid-tone) not off-white (high-key).
    grad = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    for y in range(spec.height):
        frac = y / max(1, spec.height - 1)
        alpha = int(200 * (frac ** 1.3))
        ImageDraw.Draw(grad).line(
            [(0, y), (spec.width, y)],
            fill=(40, 30, 25, alpha),     # warm near-black, not blue-black
        )
    cover_rgba = bg.convert("RGBA")
    cover_rgba.alpha_composite(grad)
    img = cover_rgba.convert("RGB")

    draw = ImageDraw.Draw(img)
    accent = spec.accent_color or BURGUNDY

    main_size = _size(spec, "title_main")
    sub_size  = _size(spec, "title_sub")
    sub_font  = _font("italic", sub_size)

    has_spare_h = (fit in ("contain", "blur_pad", "blur-pad", "blurpad")
                   and align in ("left", "right"))
    if has_spare_h:
        cover_img = Image.open(spec.cover_image)
        fg_tmp = _resize_cover_to_contain(cover_img, spec.width, spec.height)
        spare_w = spec.width - fg_tmp.width
        if align == "left":
            text_region_x0 = fg_tmp.width
            text_region_w  = spare_w
        else:
            text_region_x0 = 0
            text_region_w  = spare_w
        inner_margin = int(text_region_w * MARGINS["horizontal_pct"])
        title_budget = max(64, text_region_w - 2 * inner_margin)
    else:
        text_region_x0 = 0
        text_region_w  = spec.width
        title_budget = int(spec.width * (1 - 2 * MARGINS["horizontal_pct"]))

    _hf_loader = lambda _w, sz: _headline_font(sz)
    main_font, _ = _fit_text_to_width(
        draw, spec.text, "headline", main_size, title_budget,
        _font_loader=_hf_loader,
    )

    mw, mh_padded = _headline_metrics(draw, spec.text, main_font,
                                      fallback_h=main_size)
    _, mh = _measure(draw, spec.text, main_font)

    sub_lines: list[str] = []
    sub_line_height = int(sub_size * LINE_HEIGHT_MULT)
    sub_block_h = 0
    if spec.subtitle:
        sub_lines = _wrap_by_width(draw, spec.subtitle, sub_font, title_budget)
        if not sub_lines:
            sub_lines = [spec.subtitle]
        sub_block_h = sub_line_height * len(sub_lines)

    sub_gap = int(spec.height * 0.025)
    total_block_h = mh_padded + (sub_gap + sub_block_h if sub_lines else 0)

    block_top = int(spec.height * 0.55) - (total_block_h // 2)

    if has_spare_h:
        title_x = text_region_x0 + (text_region_w - mw) // 2
        sub_x_anchor = text_region_x0 + text_region_w // 2
    else:
        title_x = (spec.width - mw) // 2
        sub_x_anchor = spec.width // 2

    _draw_text_rtl(draw, (title_x, block_top),
                   spec.text, font=main_font, fill=accent,
                   embedded_color=True)

    if sub_lines:
        y = block_top + mh_padded + sub_gap
        for line in sub_lines:
            sw, _ = _measure(draw, line, sub_font)
            sub_x = sub_x_anchor - sw // 2
            _draw_text_rtl(draw, (sub_x, y),
                           line, font=sub_font, fill=PAPER_LIGHT)
            y += sub_line_height

    return img


def _render_title_card_paper(spec: TypographySpec) -> Image.Image:
    """
    Aged-paper title card — no cover image.

    Composition:
        ═══════
            [bracket]  TITLE  [bracket]
                       subtitle
        ═══════
    """
    img = _make_aged_paper_canvas(spec.width, spec.height)
    draw = ImageDraw.Draw(img)

    main_size = _size(spec, "title_main")
    sub_size  = _size(spec, "title_sub")
    sub_font  = _font("italic", sub_size)

    # Family C uses _headline_font (Quran-colored/Quran/Bold fallback).
    # Adapt the loader so _fit_text_to_width can drive it.
    _hf_loader = lambda _weight, sz: _headline_font(sz)
    headline_budget = int(spec.width * (1 - 2 * MARGINS["horizontal_pct"]))
    main_font, _ = _fit_text_to_width(
        draw, spec.text, "headline", main_size, headline_budget,
        _font_loader=_hf_loader,
    )

    mw, mh_padded = _headline_metrics(draw, spec.text, main_font,
                                      fallback_h=main_size)
    _, mh = _measure(draw, spec.text, main_font)

    sub_h = 0
    if spec.subtitle:
        _, sub_h = _measure(draw, spec.subtitle, sub_font)

    rule_gap = int(spec.height * 0.06)
    sub_gap  = int(spec.height * 0.03)
    rule_thick = max(1, int(spec.height * RULE_THICKNESS_PX_1080 / 1080))
    double_rule_h = rule_thick * 2 + max(2, int(spec.height * 0.010))

    total_block_h = mh_padded + (sub_gap + sub_h if spec.subtitle else 0)
    block_top = (spec.height - total_block_h) // 2

    # Double rule above
    _draw_double_rule(draw, block_top - rule_gap - double_rule_h,
                      spec.width, spec.height, length_pct=0.30)
    # Double rule below
    _draw_double_rule(draw, block_top + total_block_h + rule_gap,
                      spec.width, spec.height, length_pct=0.30)

    # Bracket ornaments flanking the title — sized to actual ink height,
    # not the padded height (otherwise brackets would extend past the
    # rules above/below).
    bracket_size = int(mh * 0.85)
    bracket_gap = int(spec.width * 0.020)
    title_cx = spec.width // 2
    bracket_cy = block_top + mh // 2
    _draw_bracket(draw,
                  cx=title_cx - mw // 2 - bracket_gap,
                  cy=bracket_cy,
                  size=bracket_size, facing="right")
    _draw_bracket(draw,
                  cx=title_cx + mw // 2 + bracket_gap,
                  cy=bracket_cy,
                  size=bracket_size, facing="left")

    # Title text
    _draw_text_rtl(draw, ((spec.width - mw) // 2, block_top),
                   spec.text, font=main_font, fill=SEPIA_INK,
                   embedded_color=True)

    if spec.subtitle:
        sub_y = block_top + mh_padded + sub_gap
        sw, _ = _measure(draw, spec.subtitle, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, sub_y),
                       spec.subtitle, font=sub_font, fill=SEPIA_DIM)

    return img


# ── Template: section_mark / chapter_heading ───────────────────────────── #

def _render_section_mark(spec: TypographySpec) -> Image.Image:
    """
    Aged paper with brackets framing the section title and a double-rule
    below it.  Reads as "manuscript margin marker".
    """
    img = _make_aged_paper_canvas(spec.width, spec.height)
    draw = ImageDraw.Draw(img)

    main_size = _size(spec, "section_main")
    sub_size  = _size(spec, "section_sub")
    sub_font  = _font("italic", sub_size)

    _hf_loader = lambda _weight, sz: _headline_font(sz)
    headline_budget = int(spec.width * (1 - 2 * MARGINS["horizontal_pct"]))
    main_font, _ = _fit_text_to_width(
        draw, spec.text, "headline", main_size, headline_budget,
        _font_loader=_hf_loader,
    )

    mw, mh_padded = _headline_metrics(draw, spec.text, main_font,
                                      fallback_h=main_size)
    _, mh = _measure(draw, spec.text, main_font)

    block_y = int(spec.height * 0.40)
    text_x  = (spec.width - mw) // 2

    # Brackets flanking the title.  Sized to the ink height (not the
    # padded calligraphy height), so the arms don't intrude past the
    # rule that follows below.
    bracket_size = int(mh * 0.80)
    bracket_gap = int(spec.width * 0.020)
    bracket_cy = block_y + mh // 2
    _draw_bracket(draw,
                  cx=text_x - bracket_gap,
                  cy=bracket_cy,
                  size=bracket_size, facing="right")
    _draw_bracket(draw,
                  cx=text_x + mw + bracket_gap,
                  cy=bracket_cy,
                  size=bracket_size, facing="left")

    _draw_text_rtl(draw, (text_x, block_y), spec.text,
                   font=main_font, fill=SEPIA_INK,
                   embedded_color=True)

    # Double rule below — use the padded headline height so calligraphic
    # descenders don't collide with the rule.
    rule_y = block_y + mh_padded + int(spec.height * 0.04)
    end_y = _draw_double_rule(draw, rule_y, spec.width, spec.height,
                              length_pct=0.20)

    if spec.subtitle:
        sub_y = end_y + int(spec.height * 0.030)
        sw, _ = _measure(draw, spec.subtitle, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, sub_y),
                       spec.subtitle, font=sub_font, fill=SEPIA_DIM)

    return img


_render_chapter_heading = _render_section_mark


# ── Template: pull_quote ───────────────────────────────────────────────── #

def _render_pull_quote(spec: TypographySpec) -> Image.Image:
    """
    Pull-quote with visible burgundy «» decorative quote marks at the
    margins.  Family A drew these at α=0.08 (nearly invisible); Family
    C makes them a design feature at α=1.0 in burgundy_dim.

    Layout:
        «               » (large, fixed at top corners of text block)

           the actual quote text wrapped over 2-3 lines

           ────  ────   (double-rule)
              — attribution
    """
    img = _make_aged_paper_canvas(spec.width, spec.height)
    draw = ImageDraw.Draw(img)

    word_count = len(spec.text.split())
    if word_count <= PULL_QUOTE_THRESHOLDS[0]:
        size_key = "pull_quote_lg"
    elif word_count <= PULL_QUOTE_THRESHOLDS[1]:
        size_key = "pull_quote_md"
    else:
        size_key = "pull_quote_sm"

    main_size = _size(spec, size_key)
    attr_size = _size(spec, "pull_quote_attr")
    main_font = _font("bold", main_size)         # body stays Bold (readability)
    attr_font = _font("italic", attr_size)

    # Decorative quote marks — sized to body weight (~1.0×) so they
    # punctuate rather than dominate; positioned just outside the text
    # column, not at the page margins (the earlier version stranded the
    # marks ~30% from the text edge, making them read as decoration
    # unrelated to the quote).
    quote_size_px = int(main_size * 1.0)
    quote_font = _font("bold", quote_size_px)

    column_w = int(spec.width * (1 - 2 * MARGINS["horizontal_pct"]))
    lines = _wrap_by_width(draw, spec.text, main_font, column_w)

    line_height = int(main_size * LINE_HEIGHT_MULT)
    block_h = line_height * len(lines)

    rule_gap = int(spec.height * 0.055)
    rule_thick = max(1, int(spec.height * RULE_THICKNESS_PX_1080 / 1080))
    double_rule_h = rule_thick * 2 + max(2, int(spec.height * 0.010))
    attr_gap = int(spec.height * 0.030)
    attr_h = 0
    if spec.subtitle:
        _, attr_text_h = _measure(draw, f"— {spec.subtitle}", attr_font)
        attr_h = attr_gap + attr_text_h

    total_h = block_h + rule_gap + double_rule_h + attr_h
    block_top = (spec.height - total_h) // 2

    # Find the widest line — that determines the text-block edges.  The
    # quote marks sit just outside those edges (gap = 0.5× quote size).
    widest_line_w = max(
        (_measure(draw, line, main_font)[0] for line in lines),
        default=0,
    )
    open_w, open_h = _measure(draw, QUOTE_OPEN, quote_font)
    close_w, _ = _measure(draw, QUOTE_CLOSE, quote_font)
    quote_gap = int(quote_size_px * 0.5)
    text_block_left = (spec.width - widest_line_w) // 2
    text_block_right = text_block_left + widest_line_w
    # The opening quote (visually on the LEFT of the text block; in
    # logical-Arabic-RTL this is the END of the quote, but visually
    # it's where the eye expects «).
    quote_y = block_top - int(open_h * 0.15)
    _draw_text_rtl(draw, (text_block_left - quote_gap - open_w, quote_y),
                   QUOTE_OPEN, font=quote_font, fill=BURGUNDY_DIM)
    _draw_text_rtl(draw, (text_block_right + quote_gap, quote_y),
                   QUOTE_CLOSE, font=quote_font, fill=BURGUNDY_DIM)

    # Body text
    last_y = _draw_centred_lines(
        draw, lines, main_font,
        canvas_size=(spec.width, spec.height),
        colour=SEPIA_INK,
        baseline_y=block_top,
        line_height=line_height,
    )

    # Double rule below
    rule_y = last_y + rule_gap
    end_y = _draw_double_rule(draw, rule_y, spec.width, spec.height,
                              length_pct=0.14)

    if spec.subtitle:
        attr_y = end_y + attr_gap
        attr_text = f"— {spec.subtitle}"
        aw, _ = _measure(draw, attr_text, attr_font)
        _draw_text_rtl(draw, ((spec.width - aw) // 2, attr_y),
                       attr_text, font=attr_font, fill=SEPIA_DIM)

    return img


# ── Template: name_reveal ──────────────────────────────────────────────── #

def _render_name_reveal(spec: TypographySpec) -> Image.Image:
    """Name in sepia, brackets flanking, double-rule + dim dates below."""
    img = _make_aged_paper_canvas(spec.width, spec.height)
    draw = ImageDraw.Draw(img)

    name_size = _size(spec, "name_main")
    sub_size  = _size(spec, "name_sub")
    sub_font  = _font("italic", sub_size)

    _hf_loader = lambda _weight, sz: _headline_font(sz)
    headline_budget = int(spec.width * (1 - 2 * MARGINS["horizontal_pct"]))
    name_font, _ = _fit_text_to_width(
        draw, spec.text, "headline", name_size, headline_budget,
        _font_loader=_hf_loader,
    )

    nw, nh_padded = _headline_metrics(draw, spec.text, name_font,
                                      fallback_h=name_size)
    _, nh = _measure(draw, spec.text, name_font)

    rule_gap   = int(spec.height * 0.035)
    sub_gap    = int(spec.height * 0.030)
    rule_thick = max(1, int(spec.height * RULE_THICKNESS_PX_1080 / 1080))
    double_rule_h = rule_thick * 2 + max(2, int(spec.height * 0.010))

    sub_h = 0
    if spec.subtitle:
        _, sub_h = _measure(draw, spec.subtitle, sub_font)

    total_h = nh_padded + rule_gap + double_rule_h + (sub_gap + sub_h if spec.subtitle else 0)
    block_top = (spec.height - total_h) // 2

    # Brackets — sized to ink, not the padded calligraphy height
    name_x = (spec.width - nw) // 2
    bracket_size = int(nh * 0.80)
    bracket_gap = int(spec.width * 0.018)
    bracket_cy = block_top + nh // 2
    _draw_bracket(draw,
                  cx=name_x - bracket_gap,
                  cy=bracket_cy,
                  size=bracket_size, facing="right")
    _draw_bracket(draw,
                  cx=name_x + nw + bracket_gap,
                  cy=bracket_cy,
                  size=bracket_size, facing="left")

    _draw_text_rtl(draw, (name_x, block_top),
                   spec.text, font=name_font, fill=SEPIA_INK,
                   embedded_color=True)

    rule_y = block_top + nh_padded + rule_gap
    end_y = _draw_double_rule(draw, rule_y, spec.width, spec.height,
                              length_pct=0.22)

    if spec.subtitle:
        sub_y = end_y + sub_gap
        sw, _ = _measure(draw, spec.subtitle, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, sub_y),
                       spec.subtitle, font=sub_font, fill=SEPIA_DIM)

    return img


# ── Template: date_stamp ───────────────────────────────────────────────── #

def _render_date_stamp(spec: TypographySpec) -> Image.Image:
    """
    Massive sepia date with optional descriptor above.  Family C's date
    stamp keeps the same dramatic scale as A and B but renders the date
    in SEPIA_INK rather than B's gold or A's charcoal — matches the
    rest of the family's ink-on-paper palette.
    """
    img = _make_aged_paper_canvas(spec.width, spec.height)
    draw = ImageDraw.Draw(img)

    # Date digits stay in Amiri Bold.  Quran calligraphic styles are
    # designed for Arabic script body text; Arabic-Indic digits render
    # at the same weight as Bold in practice, and the calligraphic
    # diacritic clearance just inflates vertical padding for no gain.
    date_size = _size(spec, "date_huge")
    sub_size  = _size(spec, "date_sub")
    date_font = _font("bold", date_size)
    sub_font  = _font("italic", sub_size)

    dw, dh = _measure(draw, spec.text, date_font)

    descriptor_gap = int(spec.height * 0.030)
    desc_h = 0
    if spec.subtitle:
        _, desc_h = _measure(draw, spec.subtitle, sub_font)

    total_h = (desc_h + descriptor_gap if spec.subtitle else 0) + dh
    block_top = (spec.height - total_h) // 2

    cursor = block_top
    if spec.subtitle:
        sw, _ = _measure(draw, spec.subtitle, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, cursor),
                       spec.subtitle, font=sub_font, fill=SEPIA_DIM)
        cursor += desc_h + descriptor_gap

    _draw_text_rtl(draw, ((spec.width - dw) // 2, cursor),
                   spec.text, font=date_font, fill=SEPIA_INK)

    return img


# ── Renderer registry ──────────────────────────────────────────────────── #

RENDERERS = {
    "title_card":       _render_title_card,
    "section_mark":     _render_section_mark,
    "chapter_heading":  _render_chapter_heading,
    "pull_quote":       _render_pull_quote,
    "name_reveal":      _render_name_reveal,
    "date_stamp":       _render_date_stamp,
}