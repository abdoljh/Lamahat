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
        ("CWD _Phase3/fonts/",             Path.cwd() / "_Phase3" / "fonts"),
        ("Colab Drive /content/_Phase3/fonts",
         Path("/content/_Phase3/fonts")),
        ("Colab Drive (mounted) _Phase3/fonts",
         Path("/content/drive/MyDrive/_Phase3/fonts")),
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
        Path("/content/_Phase3/fonts"),
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
                f"`_Phase3/fonts/` directory is present at runtime."
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
    """Compute pixel size for a SIZES entry given the spec's height."""
    return max(12, int(spec.height * SIZES[key]))


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


def _draw_text_rtl(draw: ImageDraw.ImageDraw, xy: tuple[int, int],
                   text: str, font: ImageFont.FreeTypeFont,
                   fill) -> None:
    """draw.text wrapper that handles Arabic shaping."""
    if not text:
        return
    prepared = _prepare_for_pillow(text)
    if _HAS_RAQM:
        draw.text(xy, prepared, font=font, fill=fill,
                  direction="rtl", language="ar")
    else:
        draw.text(xy, prepared, font=font, fill=fill)


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
                        line_height: int) -> int:
    """Top-aligned centred line stack.  Returns y immediately below last line."""
    w, _ = canvas_size
    y = baseline_y
    for line in lines:
        tw, _ = _measure(draw, line, font)
        _draw_text_rtl(draw, ((w - tw) // 2, y), line, font=font, fill=colour)
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
