"""
Phase 3 — Shared typography primitives.

This module holds everything that is *not* family-specific:
  - Font discovery (Amiri across all distro/Colab paths)
  - Palette and size tokens that families MAY re-export or override
  - The TypographySpec dataclass and the TemplateName Literal
  - Low-level Pillow helpers (RTL text, measure, grain, canvas, rule,
    diamond, line wrapping, cover fitting)

Family A and Family B both import from here.  The public dispatcher
lives in `typography.py` (which re-exports for backward compatibility
with `render.py`).

History: extracted from the original monolithic typography.py during
the §15.2 Family-B work.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PIL.features import check as pil_check

log = logging.getLogger(__name__)

# Pillow 10+ with libraqm shapes Arabic correctly via direction='rtl'.
# When raqm is unavailable (some older builds, certain Pillow wheels),
# we fall back to manual reshaping + bidi reordering — slower and slightly
# less accurate (no kashida, no advanced ligatures) but correct enough.
_HAS_RAQM = pil_check("raqm")
if not _HAS_RAQM:
    try:
        import arabic_reshaper       # type: ignore
        from bidi.algorithm import get_display  # type: ignore
        log.warning(
            "Pillow lacks libraqm; falling back to arabic_reshaper + "
            "python-bidi.  Arabic shaping will be slightly less accurate. "
            "For best results: install Pillow with raqm support."
        )
    except ImportError as exc:
        raise RuntimeError(
            "Pillow was built without libraqm support, and "
            "arabic_reshaper / python-bidi are not installed.  "
            "Either install Pillow with raqm (best) or run "
            "`pip install arabic_reshaper python-bidi` as a fallback."
        ) from exc


# ══════════════════════════════════════════════════════════════════════════
# DESIGN SYSTEM — shared tokens
# ══════════════════════════════════════════════════════════════════════════

# ── Palette (RGB) — Family A (cream/charcoal) ───────────────────────── #

CREAM_LIGHT  = (244, 239, 230)   # #F4EFE6
CREAM_MEDIUM = (237, 231, 218)   # #EDE7DA
CREAM_DEEP   = (228, 220, 204)   # #E4DCCC
CHARCOAL     = (38,  35,  31)    # #26231F
GRAPHITE     = (88,  82,  72)    # #585248
WARM_GREY    = (140, 130, 115)   # #8C8273

# Accent colour shared across families when overlaying titles on a cover.
GOLD_AGED    = (200, 162,  74)   # #C8A24A

# ── Palette (RGB) — Family B (Netflix-doc, dark cinematic) ──────────── #
# Deep neutral charcoals with a faint warm bias (the warm bias avoids
# the dead-grey look of pure neutrals on cheap displays).  The light
# colour is a warm off-white that sits well against the dark base
# without flaring to TV-white.

DARK_BASE    = (16,  15,  14)    # #100F0E — base for vertical gradient (bottom)
DARK_TOP     = (32,  30,  28)    # #201E1C — top of vertical gradient
DARK_CARD    = (24,  22,  20)    # #181614 — solid dark fallback
OFF_WHITE    = (236, 230, 220)   # #ECE6DC — primary text on dark
OFF_WHITE_DIM = (170, 162, 150)  # #AAA296 — secondary text (subtitles, attribution)
GOLD_DEEP    = (188, 148,  64)   # #BC9440 — accent (rules, emphasis)

# ── Typography (font discovery) ──────────────────────────────────────── #

_FONT_ALIASES = {
    "regular":       ["Amiri-Regular.ttf"],
    "bold":          ["Amiri-Bold.ttf"],
    "italic":        ["Amiri-Italic.ttf", "Amiri-Slanted.ttf"],
    "bold_italic":   ["Amiri-BoldItalic.ttf", "Amiri-BoldSlanted.ttf"],
    # Calligraphic styles used by Family C for headlines.  Both files
    # are looked up (space-in-name and no-space variants ship in the
    # upstream Amiri archive).
    "quran":         ["AmiriQuran.ttf", "Amiri Quran.ttf"],
    "quran_colored": ["AmiriQuranColored.ttf"],
}
_REQUIRED_WEIGHTS = ("regular", "bold")


def _resolve_weights(font_dir: Path) -> dict | None:
    """Try to satisfy every weight slot from a candidate directory."""
    resolved: dict[str, Path] = {}
    for weight, aliases in _FONT_ALIASES.items():
        for fname in aliases:
            p = font_dir / fname
            if p.exists():
                resolved[weight] = p
                break
    if all(w in resolved for w in _REQUIRED_WEIGHTS):
        return {w: str(p) for w, p in resolved.items()}
    return None


def _discover_amiri_fonts() -> dict:
    """Locate Amiri fonts via repo > env > fontconfig > system > download."""
    import os
    import shutil
    import subprocess

    tried: list[str] = []

    def _try(label: str, directory: Path) -> dict | None:
        if not directory or not directory.exists():
            tried.append(f"  - {label}: {directory} (does not exist)")
            return None
        resolved = _resolve_weights(directory)
        if resolved:
            log.info("Amiri fonts loaded via %s: %s", label, directory)
            return resolved
        tried.append(f"  - {label}: {directory} "
                     f"(missing required weights regular/bold)")
        return None

    pkg_dir = Path(__file__).resolve().parent
    repo_relative_candidates = [
        ("repo fonts/ (next to phase3)",   pkg_dir.parent / "fonts"),
        ("repo fonts/ (one level up)",     pkg_dir.parent.parent / "fonts"),
        ("CWD fonts/",                     Path.cwd() / "fonts"),
        ("Colab /content/fonts",           Path("/content/fonts")),
        ("Colab Drive (mounted) Lamahat/fonts",
         Path("/content/drive/MyDrive/Lamahat/fonts")),
    ]
    for label, d in repo_relative_candidates:
        found = _try(label, d)
        if found:
            return found

    override = os.environ.get("LAMAHAT_AMIRI_DIR")
    if override:
        found = _try("LAMAHAT_AMIRI_DIR env var", Path(override))
        if found:
            return found

    fc_match = shutil.which("fc-match")
    if fc_match:
        try:
            result = subprocess.run(
                [fc_match, "-f", "%{file}", "Amiri:style=Regular"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                amiri_regular_path = Path(result.stdout.strip())
                if "amiri" in amiri_regular_path.name.lower():
                    found = _try("fc-match", amiri_regular_path.parent)
                    if found:
                        return found
                else:
                    tried.append(
                        f"  - fc-match: returned {amiri_regular_path} "
                        f"(not an Amiri file — fontconfig substituted)"
                    )
            else:
                tried.append(
                    f"  - fc-match: exit={result.returncode} "
                    f"stdout={result.stdout.strip()!r}"
                )
        except Exception as exc:
            tried.append(f"  - fc-match: raised {exc!r}")
    else:
        tried.append("  - fc-match: binary not on PATH")

    well_known = [
        Path("/usr/share/fonts/opentype/fonts-hosny-amiri"),
        Path("/usr/share/fonts/amiri"),
        Path("/usr/share/fonts/google-amiri-fonts"),
        Path("/usr/share/fonts/TTF"),
        Path("/usr/share/fonts/OTF"),
        Path("/opt/homebrew/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".local/share/fonts",
        Path.home() / ".fonts",
        Path.home() / "Library/Fonts",
        Path("/content/fonts"),
        Path("/content/Lamahat/fonts"),
        Path(os.environ.get("CONDA_PREFIX", "/nonexistent")) / "share/fonts",
    ]
    for d in well_known:
        if not d.exists():
            continue
        found = _try(f"system path {d}", d)
        if found:
            return found
        for amiri_regular in d.rglob("Amiri-Regular.ttf"):
            found = _try(f"system path (nested) {amiri_regular.parent}",
                         amiri_regular.parent)
            if found:
                return found

    log.warning(
        "Amiri not found in repo, env override, fontconfig, or system "
        "paths — falling back to upstream download.  Searched:\n%s",
        "\n".join(tried) or "  (no paths searched)",
    )
    return _download_amiri()


def _download_amiri() -> dict:
    """Download Amiri from upstream as last resort; cache under ~/.cache/lamahat/."""
    import io
    import urllib.request
    import zipfile

    cache_dir = Path.home() / ".cache" / "lamahat" / "fonts" / "amiri-1.003"
    if not cache_dir.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        url = "https://github.com/aliftype/amiri/releases/download/1.003/Amiri-1.003.zip"
        log.info("Downloading Amiri from %s", url)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Lamahat/1.0 (https://github.com/abdoljh/Lamahat)",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except Exception as exc:
            raise RuntimeError(
                f"Amiri font is not installed and could not be downloaded "
                f"from {url}: {exc}.  Recommended fix: ensure the repo's "
                f"`fonts/` directory is present at runtime."
            ) from exc

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                fname = Path(name).name
                if fname.endswith(".ttf"):
                    (cache_dir / fname).write_bytes(zf.read(name))

    resolved = _resolve_weights(cache_dir)
    if resolved is None:
        raise RuntimeError(
            f"Downloaded Amiri archive at {cache_dir} is missing the "
            f"required regular/bold weights.  Files present: "
            f"{sorted(p.name for p in cache_dir.iterdir())}"
        )
    log.info("Amiri fonts downloaded → %s", cache_dir)
    return resolved


FONT_PATHS = _discover_amiri_fonts()


# ── Sizes (fraction of video height) ─────────────────────────────────── #

SIZES = {
    "title_main":      0.110,
    "title_sub":       0.040,
    "section_main":    0.060,
    "section_sub":     0.022,
    "pull_quote_lg":   0.075,
    "pull_quote_md":   0.058,
    "pull_quote_sm":   0.044,
    "pull_quote_attr": 0.022,
    "name_main":       0.070,
    "name_sub":        0.028,
    "date_huge":       0.220,
    "date_sub":        0.036,
    "quote_mark":      0.450,
}

PULL_QUOTE_THRESHOLDS = (8, 13)

# ── Layout ────────────────────────────────────────────────────────────── #

MARGINS = {
    "horizontal_pct": 0.10,
    "vertical_pct":   0.18,
}

LINE_HEIGHT_MULT = 1.45

# ── Ornament & rules ─────────────────────────────────────────────────── #

RULE_THICKNESS_PX_1080 = 2
RULE_OPACITY = 0.45
RULE_LENGTH_PCT = 0.30

SECTION_DIAMOND_SIZE = 12

# ── Quote marks ──────────────────────────────────────────────────────── #

QUOTE_OPEN  = "\u00AB"   # «
QUOTE_CLOSE = "\u00BB"   # »
DECORATIVE_QUOTE_OPACITY = 0.08

# ── Paper grain ──────────────────────────────────────────────────────── #

GRAIN_OPACITY = 0.06
GRAIN_SEED = 42


# ══════════════════════════════════════════════════════════════════════════
# Template + spec types
# ══════════════════════════════════════════════════════════════════════════

TemplateName = Literal[
    "title_card", "section_mark", "chapter_heading",
    "pull_quote", "name_reveal", "date_stamp",
]

FamilyName = Literal["A", "B", "C"]


@dataclass
class TypographySpec:
    """Inputs needed to render one typography card."""
    template: TemplateName
    text: str
    subtitle: str = ""
    width: int = 1920
    height: int = 1080
    # Family selector — A is the cream/charcoal editorial default;
    # B is the Netflix-doc cinematic dark variant; C is reserved for
    # the manuscript family (not yet implemented).  Spec is forward-
    # compatible: unknown families fall through to A in the dispatcher.
    family: FamilyName = "A"
    # Title-card-only fields (see _render_title_card_with_cover).
    cover_image: Path | None = None
    cover_fit: str = "fill"
    cover_align: str = "center"
    accent_color: tuple[int, int, int] | None = None
    # Multiplier on the title-card font sizes (title_main / title_sub), applied
    # in _size().  1.0 = default; >1 larger, <1 smaller.  Lets the notebook tune
    # the main title without touching section/quote sizing.  Only affects keys
    # beginning "title_".
    title_scale: float = 1.0
    # Background scrim behind typography-over-image text.  None → fall back to
    # env LAMAHAT_OVERLAY_SCRIM, then DEFAULT_OVERLAY_SCRIM.  One of
    # "auto" / "off" / "soft" / "band" ("auto" samples backdrop_path under the
    # text block and escalates only for bright/busy frames; see
    # OVERLAY_SCRIM_PRESETS).  Only consulted by the over-image path
    # (render_text_overlay); static cards are unaffected.
    scrim: str | None = None
    # Vertical anchor for over-image text: None → env LAMAHAT_OVERLAY_ANCHOR →
    # "auto".  "auto" = lower third for pull_quote/typography/name_reveal/
    # date_stamp (documentary convention — keeps text off faces at frame
    # centre), centre for section_mark/chapter_heading; "center"/"lower"
    # force one behaviour.  Only the over-image path consults this.
    overlay_anchor: str | None = None
    # The image the overlay will sit on (the previous real shot's asset).
    # Consulted only by scrim="auto" to measure luminance under the text
    # block; None → "auto" degrades to "off".
    backdrop_path: Path | None = None


# ══════════════════════════════════════════════════════════════════════════
# Shared low-level helpers
# ══════════════════════════════════════════════════════════════════════════

def _font(weight: str, size_px: int) -> ImageFont.FreeTypeFont:
    """Load an Amiri weight at a specific pixel size."""
    path = FONT_PATHS.get(weight) or FONT_PATHS.get("regular")
    if path is None:
        raise RuntimeError(
            "No Amiri font available — _discover_amiri_fonts() returned empty."
        )
    return ImageFont.truetype(path, size_px)


def _size(spec: TypographySpec, key: str) -> int:
    """Compute pixel size for a SIZES entry given the spec's height.

    Title keys (``title_main`` / ``title_sub``) are scaled by
    ``spec.title_scale`` so the notebook can resize the main title without
    affecting section/quote/date sizing.
    """
    mult = spec.title_scale if key.startswith("title") else 1.0
    return max(12, int(spec.height * SIZES[key] * mult))


def _prepare_for_pillow(text: str) -> str:
    """Reshape + bidi if libraqm unavailable; pass-through otherwise."""
    if not text:
        return ""
    if _HAS_RAQM:
        return text
    return get_display(arabic_reshaper.reshape(text))


def _measure(draw: ImageDraw.ImageDraw, text: str,
             font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """Return (width, height) of `text` rendered with `font` in RTL."""
    if not text:
        return 0, 0
    prepared = _prepare_for_pillow(text)
    if _HAS_RAQM:
        bbox = draw.textbbox((0, 0), prepared, font=font,
                             direction="rtl", language="ar")
    else:
        bbox = draw.textbbox((0, 0), prepared, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_text_to_width(draw: ImageDraw.ImageDraw, text: str,
                       weight: str, start_size_px: int,
                       max_width: int,
                       min_size_px: int | None = None,
                       _font_loader=None) -> tuple[ImageFont.FreeTypeFont, int]:
    """
    Return (font, size_px) such that `text` rendered with the chosen font
    measures ≤ max_width.  Starts at start_size_px and shrinks by 1 px
    until it fits or hits the floor.

    Strategy: linear-decrement from the top.  Binary search is overkill
    for a ~50-step worst case (200 px → 12 px), and the linear walk
    keeps the chosen size as close to the planned size as possible —
    only shrinks as far as it absolutely must.

    `weight`: passed to `_font()` — "regular", "bold", "italic", or a
    headline alias resolved by the family.
    `_font_loader`: optional callable (weight, size) → ImageFont, for
    callers (e.g. Family C) that use a non-default headline-font factory
    like _headline_font().  Defaults to the module-level `_font`.

    Floor defaults to max(12, start_size_px // 2): never smaller than 50%
    of the planned size, and never below Pillow's practical minimum.
    """
    if min_size_px is None:
        min_size_px = max(12, start_size_px // 2)
    if start_size_px <= min_size_px:
        loader = _font_loader or _font
        return loader(weight, start_size_px), start_size_px
    if max_width <= 0:
        loader = _font_loader or _font
        return loader(weight, start_size_px), start_size_px

    loader = _font_loader or _font
    size = start_size_px
    while size > min_size_px:
        font = loader(weight, size)
        w, _ = _measure(draw, text, font)
        if w <= max_width:
            return font, size
        size -= 1
    # Hit the floor: return the minimum size, which may still overflow
    # but is better than rendering at 1 px or raising.
    return loader(weight, min_size_px), min_size_px


def _font_has_color_palette(font: ImageFont.FreeTypeFont) -> bool:
    """
    Return True if `font` carries a COLR/CPAL palette (so Pillow's
    `embedded_color=True` path renders coloured glyphs).

    The reliable check is to look for the COLR table in the underlying
    FreeType font.  If the table is present, the font has at least one
    coloured glyph layer; absence means it's monochrome.

    This is wrapped in a try/except because the freetype binding's
    `getbest_*` style probes aren't part of the documented API, so
    different Pillow versions expose font internals slightly differently.
    Failure modes (older Pillow, exotic build) fall back to False, which
    just disables the colour-glyph path — never errors at render time.
    """
    try:
        # Pillow's TTF/OTF wrapper exposes the FreeType face under .font;
        # we use the well-defined sfnt-table API to look for "COLR".
        tables = font.font.tables                  # type: ignore[attr-defined]
        if isinstance(tables, (list, tuple)):
            return any(t == b"COLR" or t == "COLR" for t in tables)
    except Exception:
        pass
    # Fallback: name-based heuristic.  AmiriQuranColored is the only
    # Family-C font where this matters today; the heuristic is purely
    # additive and never overrides a True from the tables check above.
    try:
        path = getattr(font, "path", "") or ""
        return "Colored" in path or "colored" in path
    except Exception:
        return False


def _draw_text_rtl(draw: ImageDraw.ImageDraw, xy: tuple[int, int],
                   text: str, font: ImageFont.FreeTypeFont,
                   fill,
                   embedded_color: bool = False,
                   stroke_width: int = 0,
                   stroke_fill=None) -> None:
    """draw.text wrapper that handles Arabic shaping.

    When ``embedded_color=True`` *and* the font carries a COLR/CPAL
    palette (e.g. AmiriQuranColored), Pillow renders the coloured-glyph
    layers from the font itself.  The supplied ``fill`` colour applies
    only to monochrome glyphs in that case — colour glyphs use the
    palette baked into the font.

    Callers that don't need coloured glyphs can leave ``embedded_color``
    at its default of False; behaviour is then identical to the
    original signature.

    ``stroke_width`` / ``stroke_fill`` are optional and default to the
    no-stroke behaviour, so existing call sites are unchanged.  Stroke is
    suppressed when coloured glyphs are active — colour layers already
    carry their own edges and the two paths don't combine cleanly across
    Pillow versions.
    """
    if not text:
        return
    prepared = _prepare_for_pillow(text)
    use_color = embedded_color and _font_has_color_palette(font)
    if use_color:
        stroke_width = 0
    stroke_kw = ({"stroke_width": stroke_width, "stroke_fill": stroke_fill}
                 if stroke_width else {})
    if _HAS_RAQM:
        draw.text(xy, prepared, font=font, fill=fill,
                  direction="rtl", language="ar",
                  embedded_color=use_color, **stroke_kw)
    else:
        draw.text(xy, prepared, font=font, fill=fill,
                  embedded_color=use_color, **stroke_kw)


def _make_canvas(width: int, height: int, bg_rgb: tuple) -> Image.Image:
    """Solid-colour canvas with paper grain applied."""
    img = Image.new("RGB", (width, height), bg_rgb)
    _apply_grain(img)
    return img


def _apply_grain(img: Image.Image) -> None:
    """Subtle deterministic film grain.  Mutates in place."""
    rng = random.Random(GRAIN_SEED)
    w, h = img.size

    noise_gray = Image.new("L", (w, h), 128)
    pixels = noise_gray.load()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            pixels[x, y] = 128 + rng.randint(-32, 32)
    noise_gray = noise_gray.filter(ImageFilter.GaussianBlur(radius=0.6))

    noise_rgb = Image.merge("RGB", (noise_gray, noise_gray, noise_gray))
    if img.mode != "RGB":
        rgb_base = img.convert("RGB")
        blended = Image.blend(rgb_base, noise_rgb, GRAIN_OPACITY)
        img.paste(blended.convert(img.mode))
    else:
        blended = Image.blend(img, noise_rgb, GRAIN_OPACITY)
        img.paste(blended)


def _draw_hairline_rule(draw: ImageDraw.ImageDraw,
                       y: int, width: int, height: int,
                       length_pct: float = RULE_LENGTH_PCT,
                       colour: tuple = WARM_GREY,
                       opacity: float = RULE_OPACITY) -> None:
    """Centred horizontal hairline rule at vertical y.  Colour + opacity
    are parameterised so darker families can use a gold/warm-white rule."""
    rule_len = int(width * length_pct)
    thickness = max(1, int(height * RULE_THICKNESS_PX_1080 / 1080))
    x0 = (width - rule_len) // 2
    x1 = x0 + rule_len
    overlay = Image.new("RGBA", draw._image.size, (0, 0, 0, 0))
    o_draw = ImageDraw.Draw(overlay)
    a = int(255 * opacity)
    o_draw.rectangle([x0, y, x1, y + thickness],
                     fill=(*colour, a))
    base = draw._image.convert("RGBA")
    base.alpha_composite(overlay)
    draw._image.paste(base.convert("RGB"))


def _draw_diamond(draw: ImageDraw.ImageDraw,
                  cx: int, cy: int, size: int,
                  colour: tuple = WARM_GREY) -> None:
    """Small filled diamond ornament."""
    pts = [(cx, cy - size), (cx + size, cy),
           (cx, cy + size), (cx - size, cy)]
    draw.polygon(pts, fill=colour)


def _wrap_text(text: str, max_chars_per_line: int) -> list[str]:
    """Break Arabic into lines ≤ max_chars characters (legacy proxy)."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) <= max_chars_per_line:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _wrap_by_width(draw: ImageDraw.ImageDraw, text: str,
                   font: ImageFont.FreeTypeFont,
                   max_width: int) -> list[str]:
    """Break Arabic so each line's rendered width ≤ max_width pixels."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        w, _ = _measure(draw, candidate, font)
        if w <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_centred_lines(draw: ImageDraw.ImageDraw,
                        lines: list[str],
                        font: ImageFont.FreeTypeFont,
                        canvas_size: tuple[int, int],
                        colour: tuple,
                        baseline_y: int,
                        line_height: int,
                        embedded_color: bool = False) -> int:
    """Top-aligned centred line stack.  Returns y immediately below last line."""
    w, _ = canvas_size
    y = baseline_y
    for line in lines:
        tw, _ = _measure(draw, line, font)
        _draw_text_rtl(draw, ((w - tw) // 2, y), line, font=font, fill=colour,
                       embedded_color=embedded_color)
        y += line_height
    return y


# ══════════════════════════════════════════════════════════════════════════
# Cover-image fitting helpers (used by title_card with cover)
# ══════════════════════════════════════════════════════════════════════════

def _resize_cover_to_fill(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """CSS background-size: cover."""
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h
    if src_ratio > target_ratio:
        new_h = target_h
        new_w = int(round(src_ratio * new_h))
    else:
        new_w = target_w
        new_h = int(round(new_w / src_ratio))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top  = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _resize_cover_to_contain(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """CSS background-size: contain.  Caller pads."""
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h
    if src_ratio > target_ratio:
        new_w = target_w
        new_h = int(round(new_w / src_ratio))
    else:
        new_h = target_h
        new_w = int(round(src_ratio * new_h))
    return img.resize((new_w, new_h), Image.LANCZOS)


def _h_offset_for_align(canvas_w: int, fg_w: int, align: str) -> int:
    """Horizontal x-offset for align mode."""
    spare = max(0, canvas_w - fg_w)
    if align == "left":
        return 0
    if align == "right":
        return spare
    return spare // 2


def _make_cover_contain(cover_path: Path, target_w: int, target_h: int,
                        align: str = "center",
                        pad_colour: tuple = CREAM_LIGHT) -> Image.Image:
    """Letterbox the cover entirely inside the frame.  Pads with pad_colour
    so families with non-cream backgrounds can override (e.g. DARK_CARD)."""
    cover = Image.open(cover_path).convert("RGB")
    fg = _resize_cover_to_contain(cover, target_w, target_h)
    canvas = Image.new("RGB", (target_w, target_h), pad_colour)
    x = _h_offset_for_align(target_w, fg.width, align)
    y = (target_h - fg.height) // 2
    canvas.paste(fg, (x, y))
    return canvas


def _make_cover_blur_pad(cover_path: Path, target_w: int, target_h: int,
                         blur_radius: int = 36,
                         align: str = "center") -> Image.Image:
    """Letterbox foreground + blurred-cover background fills the bars."""
    cover = Image.open(cover_path).convert("RGB")
    bg = _resize_cover_to_fill(cover, target_w, target_h)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    fg = _resize_cover_to_contain(cover, target_w, target_h)
    x = _h_offset_for_align(target_w, fg.width, align)
    y = (target_h - fg.height) // 2
    bg.paste(fg, (x, y))
    return bg


# ══════════════════════════════════════════════════════════════════════════
# Family B helpers — vertical gradient base
# ══════════════════════════════════════════════════════════════════════════

def _make_dark_gradient_canvas(width: int, height: int,
                               top: tuple = DARK_TOP,
                               bottom: tuple = DARK_BASE) -> Image.Image:
    """
    Vertical gradient from `top` (y=0) to `bottom` (y=height-1).

    The gradient runs slightly faster in the bottom half (gamma 1.15)
    so the lower portion of the frame anchors the eye while the top
    breathes — same easing trick used in cinematic still photography.

    Grain is applied at low opacity on top, giving the dark a tactile
    quality without veering into "noisy".
    """
    img = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        frac = (y / max(1, height - 1)) ** 1.15
        r = int(top[0] + (bottom[0] - top[0]) * frac)
        g = int(top[1] + (bottom[1] - top[1]) * frac)
        b = int(top[2] + (bottom[2] - top[2]) * frac)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    _apply_grain(img)
    return img


# ── Text-over-image overlay (typography-over-image feature) ───────────────── #

# Background scrim behind the over-image text.  The plate used to be a heavy
# charcoal band (peak α 205) + a global veil (α 40) — that is what read as a
# "caption background".  The text now carries its own legibility (a soft
# blurred shadow + a thin stroke), so the default is a fully TRANSPARENT
# background.  Two fallback plates remain for genuinely hostile frames:
#   "off"  → no plate at all (default; rely on shadow+stroke)
#   "soft" → barely-there separation (faint band + faint veil)
#   "band" → the legacy heavy plate (pre-transparency behaviour, for parity)
# Resolution order, highest priority first: spec.scrim → env
# LAMAHAT_OVERLAY_SCRIM → DEFAULT_OVERLAY_SCRIM.  Mirrors the --caption-backplate
# design so it stays tunable per-render without code edits.
OVERLAY_SCRIM_PRESETS: dict[str, tuple[int, int]] = {
    # name: (band_peak_alpha, global_veil_alpha)
    "off":  (0,   0),
    "soft": (90,  18),
    "band": (205, 40),
}
# "auto" (default) is resolved per shot by _auto_scrim_for_backdrop: the
# backdrop region under the text block is sampled and the plate escalates
# off → soft → band only when the frame is genuinely hostile (bright
# watercolor sky, busy midtones).  Dark footage keeps the plate-free look.
DEFAULT_OVERLAY_SCRIM = "auto"

# Luminance / busyness gates for "auto" (0-255 luma scale, empirical from
# the al-Askari screening frames: WWII-soldier sunset ≈ 60 → off; colorized
# group portrait midsection ≈ 130 busy → soft; cream watercolor sky ≈ 220
# → band).
_AUTO_SCRIM_BAND_LUMA = 185
_AUTO_SCRIM_SOFT_LUMA = 140
_AUTO_SCRIM_SOFT_STD  = 60


def _resolve_scrim(spec: "TypographySpec") -> str:
    """Pick the scrim preset name: explicit spec field → env var → module
    default.  May return "auto" — the over-image renderer resolves that to
    a concrete preset once the text-block geometry is known."""
    val = getattr(spec, "scrim", None) or os.environ.get("LAMAHAT_OVERLAY_SCRIM")
    val = (val or DEFAULT_OVERLAY_SCRIM).strip().lower()
    if val != "auto" and val not in OVERLAY_SCRIM_PRESETS:
        log.warning("Unknown overlay scrim %r; using %r.",
                    val, DEFAULT_OVERLAY_SCRIM)
        val = DEFAULT_OVERLAY_SCRIM
    return val


def _auto_scrim_for_backdrop(backdrop_path: Path | None,
                             y0_frac: float, y1_frac: float) -> str:
    """Concrete preset for scrim="auto": sample the backdrop band the text
    will occupy (mean luma + spread).  No backdrop / unreadable → "off"
    (matches the previous default — shadow+stroke only)."""
    if backdrop_path is None:
        return "off"
    try:
        with Image.open(backdrop_path) as img:
            g = img.convert("L")
            y0 = max(0, int(g.height * y0_frac))
            y1 = min(g.height, max(y0 + 1, int(g.height * y1_frac)))
            band = g.crop((0, y0, g.width, y1)).resize((64, 16))
            px = list(band.getdata())
    except Exception as exc:  # noqa: BLE001 — sampling is best-effort
        log.debug("auto-scrim: unreadable backdrop %s (%s) — off",
                  backdrop_path, exc)
        return "off"
    n = len(px)
    mean = sum(px) / n
    var = sum((p - mean) ** 2 for p in px) / n
    std = var ** 0.5
    if mean > _AUTO_SCRIM_BAND_LUMA:
        return "band"
    if mean > _AUTO_SCRIM_SOFT_LUMA or std > _AUTO_SCRIM_SOFT_STD:
        return "soft"
    return "off"


# ── Over-image text anchor (P2.1) ─────────────────────────────────────── #

# Vertical centre of the text block as a fraction of frame height.
OVERLAY_ANCHOR_FRACS = {"center": 0.50, "lower": 0.63}
DEFAULT_OVERLAY_ANCHOR = "auto"

# "auto" per template: quotes/names/dates sit lower-third (the documentary
# convention — and it keeps text off the faces/hands that live at frame
# centre); section marks and chapter headings are structural punctuation
# and stay centred.
_LOWER_THIRD_TEMPLATES = {"pull_quote", "name_reveal", "date_stamp"}


def _resolve_overlay_anchor(spec: "TypographySpec", template: str) -> float:
    """Return the text block's vertical-centre fraction for this overlay."""
    val = (getattr(spec, "overlay_anchor", None)
           or os.environ.get("LAMAHAT_OVERLAY_ANCHOR")
           or DEFAULT_OVERLAY_ANCHOR).strip().lower()
    if val == "auto":
        val = "lower" if template in _LOWER_THIRD_TEMPLATES else "center"
    if val not in OVERLAY_ANCHOR_FRACS:
        log.warning("Unknown overlay anchor %r; using center.", val)
        val = "center"
    return OVERLAY_ANCHOR_FRACS[val]


def _draw_overlay_text_block(
    img: Image.Image,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    line_h: int,
    block_top: int,
    canvas_w: int,
    fill,
    main_size: int,
    *,
    shadow: bool,
    stroke_px: int,
) -> ImageDraw.ImageDraw:
    """Draw centred RTL text lines so they stay legible over moving footage
    *without* a visible plate.

    Two cheap, plate-free legibility devices:

      1. A soft drop shadow — the whole block is rendered onto its own
         transparent layer, offset a touch, then Gaussian-blurred ONCE and
         composited underneath.  This gives a dark aura that lifts light text
         off busy/bright backgrounds (the group-photo midsection, say) the way
         a scrim did, but localised to the glyph silhouettes instead of a band.
      2. A thin stroke on the crisp top text for hard edge contrast.

    `render_text_overlay` is called once per shot (not per frame), so the
    single full-canvas blur is negligible.
    """
    if shadow:
        shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow_layer)
        offset = max(2, main_size // 30)
        y = block_top
        for ln in lines:
            w_, _ = _measure(sdraw, ln, font)
            _draw_text_rtl(sdraw, ((canvas_w - w_) // 2 + offset, y + offset),
                           ln, font=font, fill=(0, 0, 0, 200))
            y += line_h
        radius = max(4, main_size // 12)
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius))
        img.alpha_composite(shadow_layer)

    draw = ImageDraw.Draw(img)
    y = block_top
    for ln in lines:
        w_, _ = _measure(draw, ln, font)
        _draw_text_rtl(draw, ((canvas_w - w_) // 2, y), ln, font=font,
                       fill=fill, stroke_width=stroke_px,
                       stroke_fill=(0, 0, 0, 140) if stroke_px else None)
        y += line_h
    return draw


def render_text_overlay(spec: "TypographySpec") -> Image.Image:
    """Transparent RGBA overlay = the shot's text, legible over moving footage.

    Used by the typography-over-image path: instead of a full static card, the
    text is composited over moving footage (a parallaxing image).  Text is light
    and centred and carries its own legibility (a soft blurred shadow + a thin
    stroke), so by default the BACKGROUND IS FULLY TRANSPARENT — no plate.

    A background scrim can be re-enabled for hostile frames via
    ``spec.scrim`` / ``LAMAHAT_OVERLAY_SCRIM`` ("off" | "soft" | "band"); see
    ``OVERLAY_SCRIM_PRESETS``.  Everything outside the text (and any optional
    scrim) stays transparent.
    """
    W, H = spec.width, spec.height
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    t = spec.template
    if t in ("section_mark", "chapter_heading"):
        size_key = "section_main"
    elif t == "name_reveal":
        size_key = "name_main"
    elif t == "date_stamp":
        size_key = "date_huge"
    else:  # pull_quote / typography / fallback
        wc = len(spec.text.split())
        size_key = ("pull_quote_lg" if wc <= PULL_QUOTE_THRESHOLDS[0]
                    else "pull_quote_md" if wc <= PULL_QUOTE_THRESHOLDS[1]
                    else "pull_quote_sm")

    main_size = _size(spec, size_key)
    col_w = int(W * (1 - 2 * MARGINS["horizontal_pct"]))
    main_font, main_size = _fit_text_to_width(draw, spec.text, "bold", main_size, col_w)
    lines = _wrap_by_width(draw, spec.text, main_font, col_w) or [spec.text]
    lh = int(main_size * LINE_HEIGHT_MULT)
    block_h = lh * len(lines)

    sub_font = _font("italic", _size(spec, "pull_quote_attr"))
    sub_text = (f"— {spec.subtitle}"
                if spec.subtitle and t in ("pull_quote", "typography")
                else spec.subtitle)
    sub_gap, rule_gap = int(H * 0.030), int(H * 0.022)
    rule_thick = max(2, int(H * 0.0022))
    sub_h = _measure(draw, sub_text, sub_font)[1] if spec.subtitle else 0

    total_h = block_h + (rule_gap + rule_thick + sub_gap + sub_h if spec.subtitle else 0)
    # Vertical anchor (P2.1): lower third for quotes/names/dates, centre for
    # section marks — then clamp so tall blocks never crowd the frame edges.
    anchor_frac = _resolve_overlay_anchor(spec, t)
    block_top = int(H * anchor_frac) - total_h // 2
    block_top = min(block_top, int(H * 0.93) - total_h)   # bottom margin 7%
    block_top = max(block_top, int(H * 0.06))             # top margin 6%
    center_y = block_top + total_h // 2

    # Background scrim.  "auto" (default) samples the backdrop band the text
    # occupies and escalates off → soft → band only for bright/busy frames;
    # dark footage keeps the plate-free shadow+stroke look.  Explicit
    # off/soft/band (spec/env) skip the sampling entirely.
    scrim_mode = _resolve_scrim(spec)
    if scrim_mode == "auto":
        pad = int(main_size * 0.6)   # text shadow/stroke bleed past the bbox
        scrim_mode = _auto_scrim_for_backdrop(
            getattr(spec, "backdrop_path", None),
            (block_top - pad) / H, (block_top + total_h + pad) / H)
        log.debug("overlay scrim auto → %s", scrim_mode)
    band_peak, veil_alpha = OVERLAY_SCRIM_PRESETS[scrim_mode]

    if veil_alpha:
        img.alpha_composite(Image.new("RGBA", (W, H), (*CHARCOAL, veil_alpha)))
    if band_peak:
        import numpy as np
        # Band taller than the text block so the falloff is gentle (no hard
        # bar edge).  Same gaussian shape as before, just amplitude-controlled.
        ys = np.arange(H, dtype=np.float32)
        sigma = max(115.0, total_h * 0.95 + main_size * 1.0)
        band = (np.exp(-((ys - center_y) / sigma) ** 2) * band_peak).astype(np.uint8)
        scrim = np.repeat(band[:, None], W, axis=1)
        veil = Image.new("RGBA", (W, H), (*CHARCOAL, 0))
        veil.putalpha(Image.fromarray(scrim, "L"))
        img.alpha_composite(veil)
    draw = ImageDraw.Draw(img)

    accent = spec.accent_color or GOLD_DEEP
    # Legibility treatment: always stroke; add the blurred shadow whenever the
    # plate is light/absent (a heavy "band" plate makes the shadow redundant).
    stroke_px = max(2, main_size // 38)
    draw = _draw_overlay_text_block(
        img, lines, main_font, lh, block_top, W,
        fill=OFF_WHITE, main_size=main_size,
        shadow=(band_peak < 150), stroke_px=stroke_px,
    )

    if spec.subtitle:
        rule_y = block_top + block_h + rule_gap
        rule_len = int(W * 0.16)
        draw.rectangle([(W - rule_len) // 2, rule_y,
                        (W + rule_len) // 2, rule_y + rule_thick], fill=(*accent, 255))
        sy = rule_y + rule_thick + sub_gap
        sw, _ = _measure(draw, sub_text, sub_font)
        _draw_text_rtl(draw, ((W - sw) // 2, sy), sub_text, font=sub_font,
                       fill=accent, stroke_width=max(1, main_size // 64),
                       stroke_fill=(0, 0, 0, 120))

    return img
