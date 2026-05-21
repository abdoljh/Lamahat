"""
Phase 3 — Typography Family A (Aljazeera-editorial).

Cream/charcoal palette, restrained ornament, generous white space.
Hierarchy through scale + weight, never colour.

The five renderers in this module honour the contract:
    (spec: TypographySpec) -> PIL.Image.Image

History: lifted verbatim from the original monolithic typography.py
during the §15.2 Family-B refactor.  Behaviour is unchanged; the only
edits were import paths.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .typography_common import (
    # palette
    CHARCOAL, CREAM_DEEP, CREAM_LIGHT, CREAM_MEDIUM, GRAPHITE, WARM_GREY,
    GOLD_AGED,
    # tokens
    LINE_HEIGHT_MULT, MARGINS, PULL_QUOTE_THRESHOLDS,
    RULE_THICKNESS_PX_1080, SECTION_DIAMOND_SIZE,
    # spec + helpers
    TypographySpec,
    _font, _size, _measure, _draw_text_rtl,
    _make_canvas, _draw_hairline_rule, _draw_diamond,
    _wrap_by_width, _draw_centred_lines,
    _resize_cover_to_fill, _resize_cover_to_contain,
    _make_cover_contain, _make_cover_blur_pad,
)


# ── Template: title_card ───────────────────────────────────────────────── #

def _render_title_card(spec: TypographySpec) -> Image.Image:
    """Two modes: cover-image (gold-on-photo) or cream (charcoal-on-cream)."""
    if spec.cover_image is not None and Path(spec.cover_image).exists():
        return _render_title_card_with_cover(spec)
    return _render_title_card_cream(spec)


def _render_title_card_with_cover(spec: TypographySpec) -> Image.Image:
    fit = (spec.cover_fit or "fill").lower()
    align = (spec.cover_align or "center").lower()
    if align not in ("center", "left", "right"):
        align = "center"

    if fit == "contain":
        bg = _make_cover_contain(spec.cover_image, spec.width, spec.height, align)
    elif fit in ("blur_pad", "blur-pad", "blurpad"):
        bg = _make_cover_blur_pad(spec.cover_image, spec.width, spec.height,
                                  align=align)
    else:
        cover = Image.open(spec.cover_image).convert("RGB")
        bg = _resize_cover_to_fill(cover, spec.width, spec.height)

    grad = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    for y in range(spec.height):
        frac = y / max(1, spec.height - 1)
        alpha = int(190 * (frac ** 1.4))
        ImageDraw.Draw(grad).line(
            [(0, y), (spec.width, y)],
            fill=(*CHARCOAL, alpha),
        )
    cover_rgba = bg.convert("RGBA")
    cover_rgba.alpha_composite(grad)
    img = cover_rgba.convert("RGB")

    draw = ImageDraw.Draw(img)
    accent = spec.accent_color or GOLD_AGED

    main_size = _size(spec, "title_main")
    sub_size  = _size(spec, "title_sub")
    main_font = _font("bold", main_size)
    sub_font  = _font("italic", sub_size)

    shaped_main = spec.text
    mw, mh = _measure(draw, shaped_main, main_font)

    sub_h = 0
    if spec.subtitle:
        _, sub_h = _measure(draw, spec.subtitle, sub_font)
    sub_gap = int(spec.height * 0.025)
    total_block_h = mh + (sub_gap + sub_h if spec.subtitle else 0)

    block_top = int(spec.height * 0.52) - (total_block_h // 2)

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

    title_y = block_top
    _draw_text_rtl(draw, (title_x, title_y),
                   shaped_main, font=main_font, fill=accent)

    if spec.subtitle:
        sub_y = block_top + mh + sub_gap
        sw, _ = _measure(draw, spec.subtitle, sub_font)
        sub_x = sub_x_anchor - sw // 2
        _draw_text_rtl(draw, (sub_x, sub_y),
                       spec.subtitle, font=sub_font, fill=CREAM_LIGHT)

    return img


def _render_title_card_cream(spec: TypographySpec) -> Image.Image:
    img = _make_canvas(spec.width, spec.height, CREAM_LIGHT)
    draw = ImageDraw.Draw(img)

    main_size = _size(spec, "title_main")
    sub_size  = _size(spec, "title_sub")

    main_font = _font("bold", main_size)
    sub_font  = _font("italic", sub_size)

    shaped_main = spec.text
    mw, mh = _measure(draw, shaped_main, main_font)

    rule_gap = int(spec.height * 0.04)
    sub_gap  = int(spec.height * 0.03)

    sub_h = 0
    if spec.subtitle:
        shaped_sub = spec.subtitle
        _, sub_h = _measure(draw, shaped_sub, sub_font)

    total_block_h = mh + (sub_gap + sub_h if spec.subtitle else 0)

    block_top = (spec.height - total_block_h) // 2
    title_y   = block_top
    sub_y     = block_top + mh + sub_gap if spec.subtitle else None

    _draw_hairline_rule(draw,
                       y=block_top - rule_gap,
                       width=spec.width, height=spec.height)
    _draw_hairline_rule(draw,
                       y=block_top + total_block_h + rule_gap,
                       width=spec.width, height=spec.height)

    draw = ImageDraw.Draw(img)

    _draw_text_rtl(draw, ((spec.width - mw) // 2, title_y),
              shaped_main, font=main_font, fill=CHARCOAL)

    if spec.subtitle and sub_y is not None:
        shaped_sub = spec.subtitle
        sw, _ = _measure(draw, shaped_sub, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, sub_y),
                  shaped_sub, font=sub_font, fill=GRAPHITE)

    return img


# ── Template: section_mark / chapter_heading ───────────────────────────── #

def _render_section_mark(spec: TypographySpec) -> Image.Image:
    img = _make_canvas(spec.width, spec.height, CREAM_MEDIUM)
    draw = ImageDraw.Draw(img)

    main_size = _size(spec, "section_main")
    sub_size  = _size(spec, "section_sub")

    main_font = _font("regular", main_size)
    sub_font  = _font("italic", sub_size)

    shaped_main = spec.text
    mw, mh = _measure(draw, shaped_main, main_font)

    block_y = int(spec.height * 0.42)
    text_x  = (spec.width - mw) // 2

    _draw_text_rtl(draw, (text_x, block_y), shaped_main,
                   font=main_font, fill=CHARCOAL)

    ornament_y = block_y + mh + int(spec.height * 0.04)
    _draw_hairline_rule(draw, y=ornament_y,
                       width=spec.width, height=spec.height,
                       length_pct=0.18)
    draw = ImageDraw.Draw(img)

    diamond_size = max(4, int(spec.height * SECTION_DIAMOND_SIZE / 1080))
    _draw_diamond(draw, spec.width // 2,
                  ornament_y + max(1, int(spec.height * 0.001)),
                  diamond_size)

    if spec.subtitle:
        sub_y = ornament_y + diamond_size + int(spec.height * 0.025)
        shaped_sub = spec.subtitle
        sw, _ = _measure(draw, shaped_sub, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, sub_y),
                  shaped_sub, font=sub_font, fill=GRAPHITE)

    return img


_render_chapter_heading = _render_section_mark


# ── Template: pull_quote ───────────────────────────────────────────────── #

def _render_pull_quote(spec: TypographySpec) -> Image.Image:
    img = _make_canvas(spec.width, spec.height, CREAM_DEEP)
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
    attr_font = _font("italic", attr_size)

    column_w = int(spec.width * (1 - 2 * MARGINS["horizontal_pct"]))
    lines = _wrap_by_width(draw, spec.text, main_font, column_w)

    line_height = int(main_size * LINE_HEIGHT_MULT)
    block_h = line_height * len(lines)

    rule_gap = int(spec.height * 0.06)
    rule_thick = max(1, int(spec.height * RULE_THICKNESS_PX_1080 / 1080))
    attr_gap = int(spec.height * 0.025)
    attr_h = 0
    if spec.subtitle:
        _, attr_text_h = _measure(draw, f"— {spec.subtitle}", attr_font)
        attr_h = attr_gap + attr_text_h

    total_h = block_h + rule_gap + rule_thick + attr_h
    block_top = (spec.height - total_h) // 2

    last_y = _draw_centred_lines(
        draw, lines, main_font,
        canvas_size=(spec.width, spec.height),
        colour=CHARCOAL,
        baseline_y=block_top,
        line_height=line_height,
    )

    rule_y = last_y + rule_gap
    _draw_hairline_rule(draw, y=rule_y,
                       width=spec.width, height=spec.height,
                       length_pct=0.14)
    draw = ImageDraw.Draw(img)

    if spec.subtitle:
        attr_y = rule_y + rule_thick + attr_gap
        attr_text = f"— {spec.subtitle}"
        aw, _ = _measure(draw, attr_text, attr_font)
        _draw_text_rtl(draw, ((spec.width - aw) // 2, attr_y),
                       attr_text, font=attr_font, fill=GRAPHITE)

    return img


# ── Template: name_reveal ──────────────────────────────────────────────── #

def _render_name_reveal(spec: TypographySpec) -> Image.Image:
    img = _make_canvas(spec.width, spec.height, CREAM_LIGHT)
    draw = ImageDraw.Draw(img)

    name_size = _size(spec, "name_main")
    sub_size  = _size(spec, "name_sub")
    name_font = _font("bold", name_size)
    sub_font  = _font("italic", sub_size)

    shaped_name = spec.text
    nw, nh = _measure(draw, shaped_name, name_font)

    rule_gap   = int(spec.height * 0.03)
    sub_gap    = int(spec.height * 0.03)
    rule_thick = max(1, int(spec.height * RULE_THICKNESS_PX_1080 / 1080))

    sub_h = 0
    if spec.subtitle:
        shaped_sub = spec.subtitle
        _, sub_h = _measure(draw, shaped_sub, sub_font)

    total_h = nh + rule_gap + rule_thick + (sub_gap + sub_h if spec.subtitle else 0)
    block_top = (spec.height - total_h) // 2

    _draw_text_rtl(draw, ((spec.width - nw) // 2, block_top),
              shaped_name, font=name_font, fill=CHARCOAL)

    rule_y = block_top + nh + rule_gap
    _draw_hairline_rule(draw, y=rule_y,
                       width=spec.width, height=spec.height,
                       length_pct=0.22)
    draw = ImageDraw.Draw(img)

    if spec.subtitle:
        sub_y = rule_y + rule_thick + sub_gap
        shaped_sub = spec.subtitle
        sw, _ = _measure(draw, shaped_sub, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, sub_y),
                  shaped_sub, font=sub_font, fill=GRAPHITE)

    return img


# ── Template: date_stamp ───────────────────────────────────────────────── #

def _render_date_stamp(spec: TypographySpec) -> Image.Image:
    img = _make_canvas(spec.width, spec.height, CREAM_LIGHT)
    draw = ImageDraw.Draw(img)

    date_size = _size(spec, "date_huge")
    sub_size  = _size(spec, "date_sub")
    date_font = _font("bold", date_size)
    sub_font  = _font("regular", sub_size)

    shaped_date = spec.text
    dw, dh = _measure(draw, shaped_date, date_font)

    descriptor_gap = int(spec.height * 0.025)
    desc_h = 0
    if spec.subtitle:
        shaped_sub = spec.subtitle
        _, desc_h = _measure(draw, shaped_sub, sub_font)

    total_h = (desc_h + descriptor_gap if spec.subtitle else 0) + dh
    block_top = (spec.height - total_h) // 2

    cursor = block_top
    if spec.subtitle:
        shaped_sub = spec.subtitle
        sw, _ = _measure(draw, shaped_sub, sub_font)
        _draw_text_rtl(draw, ((spec.width - sw) // 2, cursor),
                  shaped_sub, font=sub_font, fill=GRAPHITE)
        cursor += desc_h + descriptor_gap

    _draw_text_rtl(draw, ((spec.width - dw) // 2, cursor),
              shaped_date, font=date_font, fill=CHARCOAL)

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
