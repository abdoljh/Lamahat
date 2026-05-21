"""
Phase 3 — Typography Family B (Netflix-doc cinematic).

Dark gradient backgrounds, off-white headline type, gold accent rules.
Hierarchy through scale + a single accent colour.  No ornaments
(diamonds, decorative quote marks) — the gradient and the gold rule
carry the design.

Templates implement the same 5-renderer contract as Family A:
    (spec: TypographySpec) -> PIL.Image.Image

Design philosophy
-----------------
Netflix documentary cards lean on three moves: a near-black gradient
that grades from slightly-warm at top to deep at the bottom, type set
in a confident weight (bold for headlines, regular for body), and one
accent colour used sparingly for rules and dates.  No decorative
flourishes.  The gradient does the cinematic work; the type does the
editorial work.

Differences from Family A
-------------------------
- Dark background instead of cream; off-white text instead of charcoal
- Gold rules instead of warm-grey (the rule needs to *register* on dark)
- No diamond ornament on section_marks — a longer gold rule does the job
- No decorative «» quote marks on pull_quotes
- Title-card cream-mode is gone — Family B always uses the dark
  gradient base when no cover is supplied
- Title-card with-cover mode gets a stronger bottom gradient (the
  off-white title needs more darkness underneath to read)

History: new in the §15.2 refactor; sits alongside the verbatim
Family A renderers in typography_a.py.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .typography_common import (
    # Family B palette
    DARK_BASE, DARK_TOP, DARK_CARD,
    OFF_WHITE, OFF_WHITE_DIM, GOLD_DEEP,
    # tokens
    LINE_HEIGHT_MULT, MARGINS, PULL_QUOTE_THRESHOLDS,
    RULE_THICKNESS_PX_1080,
    # spec + helpers
    TypographySpec,
    _font, _size, _measure, _draw_text_rtl,
    _draw_hairline_rule, _wrap_by_width, _draw_centred_lines,
    _make_dark_gradient_canvas,
    _resize_cover_to_fill, _resize_cover_to_contain,
    _make_cover_contain, _make_cover_blur_pad,
)


# ── Shared local helpers ───────────────────────────────────────────────── #

def _draw_gold_rule(draw: ImageDraw.ImageDraw, y: int,
                    width: int, height: int,
                    length_pct: float = 0.10,
                    thickness_mult: float = 1.5) -> None:
    """
    Family-B accent rule: shorter and slightly thicker than Family A's
    hairline, in deep gold rather than warm grey.  The opacity is high
    because the rule must read clearly against the dark gradient — a
    semi-transparent rule disappears.
    """
    rule_len = int(width * length_pct)
    thickness = max(1, int(height * RULE_THICKNESS_PX_1080 * thickness_mult / 1080))
    x0 = (width - rule_len) // 2
    x1 = x0 + rule_len
    # Solid (no overlay).  At 1080p this is 3 px tall, the same visual
    # weight as Family A's hairline reads on cream.
    draw.rectangle([x0, y, x1, y + thickness], fill=GOLD_DEEP)


# ── Template: title_card ───────────────────────────────────────────────── #

def _render_title_card(spec: TypographySpec) -> Image.Image:
    """Two modes mirroring Family A: cover-photo or dark-gradient."""
    if spec.cover_image is not None and Path(spec.cover_image).exists():
        return _render_title_card_with_cover(spec)
    return _render_title_card_dark(spec)


def _render_title_card_with_cover(spec: TypographySpec) -> Image.Image:
    """
    Cover-image variant: composite the cover full-frame, apply a strong
    bottom-weighted gradient (darker than Family A's so the off-white
    title reads), draw the title in the accent colour.

    The gradient on Family B reaches ~225 alpha at the bottom vs A's
    190 — necessary because the title is off-white instead of gold,
    and off-white needs more dark space behind it.
    """
    fit = (spec.cover_fit or "fill").lower()
    align = (spec.cover_align or "center").lower()
    if align not in ("center", "left", "right"):
        align = "center"

    # Letterbox pad colour swaps to DARK_CARD so Family B doesn't fall
    # back to cream bars when contain/blur_pad isn't enough.
    if fit == "contain":
        bg = _make_cover_contain(spec.cover_image, spec.width, spec.height,
                                 align, pad_colour=DARK_CARD)
    elif fit in ("blur_pad", "blur-pad", "blurpad"):
        bg = _make_cover_blur_pad(spec.cover_image, spec.width, spec.height,
                                  align=align)
    else:
        cover = Image.open(spec.cover_image).convert("RGB")
        bg = _resize_cover_to_fill(cover, spec.width, spec.height)

    # Stronger gradient than Family A — bottom-weighted.
    grad = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    for y in range(spec.height):
        frac = y / max(1, spec.height - 1)
        alpha = int(225 * (frac ** 1.5))
        ImageDraw.Draw(grad).line(
            [(0, y), (spec.width, y)],
            fill=(*DARK_BASE, alpha),
        )
    cover_rgba = bg.convert("RGBA")
    cover_rgba.alpha_composite(grad)
    img = cover_rgba.convert("RGB")

    draw = ImageDraw.Draw(img)
    accent = spec.accent_color or OFF_WHITE   # off-white title, not gold

    main_size = _size(spec, "title_main")
    sub_size  = _size(spec, "title_sub")
    main_font = _font("bold", main_size)
    sub_font  = _font("regular", sub_size)   # regular not italic on dark

    mw, mh = _measure(draw, spec.text, main_font)

    sub_h = 0
    if spec.subtitle:
        _, sub_h = _measure(draw, spec.subtitle, sub_font)
    sub_gap = int(spec.height * 0.025)
    rule_gap = int(spec.height * 0.035)
    rule_thick_v = max(1, int(spec.height * RULE_THICKNESS_PX_1080 * 1.5 / 1080))
    total_block_h = (mh
                     + (rule_gap + rule_thick_v if spec.subtitle else 0)
                     + (sub_gap + sub_h if spec.subtitle else 0))

    block_top = int(spec.height * 0.55) - (total_block_h // 2)

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
        title_x = text_region_x0 + (text_region_w - mw) // 2
        sub_x_anchor = text_region_x0 + text_region_w // 2
    else:
        title_x = (spec.width - mw) // 2
        sub_x_anchor = spec.width // 2

    _draw_text_rtl(draw, (title_x, block_top),
                   spec.text, font=main_font, fill=accent)

    # Gold rule + subtitle below
    if spec.subtitle:
        rule_y = block_top + mh + rule_gap
        _draw_gold_rule(draw, rule_y, spec.width, spec.height,
                        length_pct=0.08)
        sub_y = rule_y + rule_thick_v + sub_gap
        sw, _ = _measure(draw, spec.subtitle, sub_font)
        sub_x = sub_x_anchor - sw // 2
        _draw_text_rtl(draw, (sub_x, sub_y),
                       spec.subtitle, font=sub_font, fill=OFF_WHITE_DIM)

    return img


def _render_title_card_dark(spec: TypographySpec) -> Image.Image:
    """
    Pure dark-gradient title card — no cover image.  The gradient is
    the entire backdrop; type sits dead-centre with a short gold rule
    between title and subtitle.
    """
    img = _make_dark_gradient_canvas(spec.width, spec.height)
    draw = ImageDraw.Draw(img)

    main_size = _size(spec, "title_main")
    sub_size  = _size(spec, "title_sub")
    main_font = _font("bold", main_size)
    sub_font  = _font("regular", sub_size)

    mw, mh = _measure(draw, spec.text, main_font)

    sub_h = 0
    if spec.subtitle:
        _, sub_h = _measure(draw, spec.subtitle, sub_font)

    rule_gap = int(spec.height * 0.04)
    sub_gap  = int(spec.height * 0.025)
    rule_thick = max(1, int(spec.height * RULE_THICKNESS_PX_1080 * 1.5 / 1080))

    total_block_h = mh + (rule_gap + rule_thick + sub_gap + sub_h
                          if spec.subtitle else 0)
    block_top = (spec.height - total_block_h) // 2

    _draw_text_rtl(draw, ((spec.width - mw) // 2, block_top),
                   spec.text, font=main_font, fill=OFF_WHITE)

    if spec.subtitle:
        rule_y = block_top + mh + rule_gap
        _draw_gold_rule(draw, rule_y, spec.width, spec.height,
                        length_pct=0.08)
        sub_y = rule_y + rule_thick + sub_gap
        sw, _ = _measure(draw, spec.subtitle, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, sub_y),
                       spec.subtitle, font=sub_font, fill=OFF_WHITE_DIM)

    return img


# ── Template: section_mark / chapter_heading ───────────────────────────── #

def _render_section_mark(spec: TypographySpec) -> Image.Image:
    """
    Dark gradient with the section text centred and a gold rule below.
    No diamond — the gold rule alone is the punctuation.

    The text uses Amiri Bold rather than Regular (which Family A uses)
    because off-white-on-dark needs more weight to feel anchored;
    regular weight floats off the gradient.
    """
    img = _make_dark_gradient_canvas(spec.width, spec.height)
    draw = ImageDraw.Draw(img)

    main_size = _size(spec, "section_main")
    sub_size  = _size(spec, "section_sub")

    main_font = _font("bold", main_size)
    sub_font  = _font("regular", sub_size)

    mw, mh = _measure(draw, spec.text, main_font)

    block_y = int(spec.height * 0.43)
    text_x  = (spec.width - mw) // 2

    _draw_text_rtl(draw, (text_x, block_y), spec.text,
                   font=main_font, fill=OFF_WHITE)

    rule_y = block_y + mh + int(spec.height * 0.045)
    _draw_gold_rule(draw, rule_y, spec.width, spec.height,
                    length_pct=0.12)

    if spec.subtitle:
        rule_thick = max(1, int(spec.height * RULE_THICKNESS_PX_1080 * 1.5 / 1080))
        sub_y = rule_y + rule_thick + int(spec.height * 0.030)
        sw, _ = _measure(draw, spec.subtitle, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, sub_y),
                  spec.subtitle, font=sub_font, fill=OFF_WHITE_DIM)

    return img


_render_chapter_heading = _render_section_mark


# ── Template: pull_quote ───────────────────────────────────────────────── #

def _render_pull_quote(spec: TypographySpec) -> Image.Image:
    """
    Magazine pull-quote on dark gradient.  No decorative quote marks;
    the gradient backdrop and bold off-white type carry the weight.

    Layout mirrors Family A: word-count-driven sizing, gold rule below,
    optional attribution.
    """
    img = _make_dark_gradient_canvas(spec.width, spec.height)
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
    main_font = _font("bold", main_size)
    attr_font = _font("regular", attr_size)

    column_w = int(spec.width * (1 - 2 * MARGINS["horizontal_pct"]))
    lines = _wrap_by_width(draw, spec.text, main_font, column_w)

    line_height = int(main_size * LINE_HEIGHT_MULT)
    block_h = line_height * len(lines)

    rule_gap = int(spec.height * 0.06)
    rule_thick = max(1, int(spec.height * RULE_THICKNESS_PX_1080 * 1.5 / 1080))
    attr_gap = int(spec.height * 0.030)
    attr_h = 0
    if spec.subtitle:
        _, attr_text_h = _measure(draw, f"— {spec.subtitle}", attr_font)
        attr_h = attr_gap + attr_text_h

    total_h = block_h + rule_gap + rule_thick + attr_h
    block_top = (spec.height - total_h) // 2

    last_y = _draw_centred_lines(
        draw, lines, main_font,
        canvas_size=(spec.width, spec.height),
        colour=OFF_WHITE,
        baseline_y=block_top,
        line_height=line_height,
    )

    rule_y = last_y + rule_gap
    _draw_gold_rule(draw, rule_y, spec.width, spec.height,
                    length_pct=0.10)

    if spec.subtitle:
        attr_y = rule_y + rule_thick + attr_gap
        attr_text = f"— {spec.subtitle}"
        aw, _ = _measure(draw, attr_text, attr_font)
        _draw_text_rtl(draw, ((spec.width - aw) // 2, attr_y),
                       attr_text, font=attr_font, fill=OFF_WHITE_DIM)

    return img


# ── Template: name_reveal ──────────────────────────────────────────────── #

def _render_name_reveal(spec: TypographySpec) -> Image.Image:
    """Bold off-white name + short gold rule + dim subtitle on dark gradient."""
    img = _make_dark_gradient_canvas(spec.width, spec.height)
    draw = ImageDraw.Draw(img)

    name_size = _size(spec, "name_main")
    sub_size  = _size(spec, "name_sub")
    name_font = _font("bold", name_size)
    sub_font  = _font("regular", sub_size)

    nw, nh = _measure(draw, spec.text, name_font)

    rule_gap   = int(spec.height * 0.035)
    sub_gap    = int(spec.height * 0.030)
    rule_thick = max(1, int(spec.height * RULE_THICKNESS_PX_1080 * 1.5 / 1080))

    sub_h = 0
    if spec.subtitle:
        _, sub_h = _measure(draw, spec.subtitle, sub_font)

    total_h = nh + rule_gap + rule_thick + (sub_gap + sub_h if spec.subtitle else 0)
    block_top = (spec.height - total_h) // 2

    _draw_text_rtl(draw, ((spec.width - nw) // 2, block_top),
              spec.text, font=name_font, fill=OFF_WHITE)

    rule_y = block_top + nh + rule_gap
    _draw_gold_rule(draw, rule_y, spec.width, spec.height,
                    length_pct=0.14)

    if spec.subtitle:
        sub_y = rule_y + rule_thick + sub_gap
        sw, _ = _measure(draw, spec.subtitle, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, sub_y),
                  spec.subtitle, font=sub_font, fill=OFF_WHITE_DIM)

    return img


# ── Template: date_stamp ───────────────────────────────────────────────── #

def _render_date_stamp(spec: TypographySpec) -> Image.Image:
    """
    Massive date in gold (the only template that uses gold as the
    primary text colour — dates are the visual exclamation marks in
    Netflix-style docs).  Optional descriptor above in off-white.
    """
    img = _make_dark_gradient_canvas(spec.width, spec.height)
    draw = ImageDraw.Draw(img)

    date_size = _size(spec, "date_huge")
    sub_size  = _size(spec, "date_sub")
    date_font = _font("bold", date_size)
    sub_font  = _font("regular", sub_size)

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
                  spec.subtitle, font=sub_font, fill=OFF_WHITE_DIM)
        cursor += desc_h + descriptor_gap

    _draw_text_rtl(draw, ((spec.width - dw) // 2, cursor),
              spec.text, font=date_font, fill=GOLD_DEEP)

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
