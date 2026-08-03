#!/usr/bin/env python3
"""Superimpose a typography card over an image — a still-frame sandbox.

The renderer composites text over footage inside a 40-minute FFmpeg run,
which is a slow way to answer "what would this look like?".  This does
the same composite on ONE still and writes a PNG, so a look can be tried
in a second and compared side by side.

Two modes:

  --text "…"     Render the card through `phase3.typography.render_overlay`
                 — the exact code path `render.py` uses for
                 --typography-over-image.  Template, family, scrim and
                 anchor behave as they do in the film, so a setting that
                 looks right here looks right there.

  --card PNG     Composite an ALREADY-RENDERED card (e.g. a dossier's
                 card_preview.png).  Those are opaque cards with a flat
                 background, so this adds the controls that make one sit
                 on a photograph: --card-key drops the flat background to
                 transparency, --card-blend/--card-opacity control how
                 what remains mixes with the image.

Comparing is the point, so --card-blend and --card-opacity accept
comma-separated lists; every combination is written as its own file with
the settings in the filename.

Examples
--------
    # Text over a photograph, exactly as the film would draw it
    python overlay_card.py --image map.jpg --output out/frame.png \\
        --text "من الموصل إلى الاستانة — رحلة التحديث والطموح" \\
        --template chapter_heading --scrim auto --anchor auto

    # An existing card over a photograph, background keyed out,
    # four looks to choose from
    python overlay_card.py --image map.jpg --card card_preview.png \\
        --card-key --card-blend normal,multiply --card-opacity 0.7,1.0 \\
        --output out/try.png

    # Word-by-word reveal, step 3
    python overlay_card.py --image map.jpg --text "…" --reveal 3 \\
        --output out/reveal3.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

TEMPLATES = ("pull_quote", "section_mark", "chapter_heading",
             "name_reveal", "date_stamp", "title_card")
BLENDS = ("normal", "multiply", "screen", "overlay", "soft_light")


# ── Background ──────────────────────────────────────────────────────── #

def fit_image(img: Image.Image, w: int, h: int, mode: str) -> Image.Image:
    """Fit `img` to a w×h canvas.

    Mirrors `render._png_to_clip`'s rule, which exists because a blind
    cover-crop decapitates portraits: "cover" fills the frame and crops
    the overflow, "contain" holds the whole picture over a blurred,
    darkened enlargement of itself.
    """
    img = img.convert("RGB")
    scale_cover = max(w / img.width, h / img.height)
    if mode == "cover":
        new = img.resize((max(1, round(img.width * scale_cover)),
                          max(1, round(img.height * scale_cover))),
                         Image.LANCZOS)
        left = (new.width - w) // 2
        top = (new.height - h) // 2
        return new.crop((left, top, left + w, top + h))

    bg = img.resize((max(1, round(img.width * scale_cover)),
                     max(1, round(img.height * scale_cover))), Image.LANCZOS)
    left = (bg.width - w) // 2
    top = (bg.height - h) // 2
    bg = bg.crop((left, top, left + w, top + h)).filter(
        ImageFilter.GaussianBlur(radius=max(8, w // 60)))
    bg = Image.blend(bg, Image.new("RGB", (w, h), (0, 0, 0)), 0.35)
    s = min(w / img.width, h / img.height)
    fg = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))),
                    Image.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


# ── Card preparation ────────────────────────────────────────────────── #

def key_out_background(card: Image.Image, tol: int, feather: float) -> Image.Image:
    """Make a card's flat background transparent.

    The key colour is the median of the four corners, which is what a
    designed card's paper/backdrop is.  Pixels within `tol` (Euclidean,
    0–441) of it become transparent; `feather` softens the edge so the
    letterforms do not alias against the photograph.
    """
    card = card.convert("RGBA")
    px = card.load()
    w, h = card.size
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    key = tuple(sorted(c[i] for c in corners)[1] for i in range(3))

    rgb = card.convert("RGB")
    diff = ImageChops.difference(rgb, Image.new("RGB", card.size, key))
    # Per-pixel distance from the key colour, approximated by the max
    # channel difference — cheap and stable for flat backgrounds.
    dist = diff.convert("L").point(lambda v: 255 if v > tol else 0)
    if feather > 0:
        dist = dist.filter(ImageFilter.GaussianBlur(radius=feather))
    alpha = ImageChops.multiply(card.getchannel("A"), dist)
    out = card.copy()
    out.putalpha(alpha)
    return out


def place(card: Image.Image, w: int, h: int, scale: float, pos: str,
          margin: float) -> Image.Image:
    """Scale the card to `scale` of canvas width and lay it on a w×h sheet."""
    target_w = max(1, round(w * scale))
    target_h = max(1, round(card.height * target_w / card.width))
    card = card.resize((target_w, target_h), Image.LANCZOS)
    sheet = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x = (w - target_w) // 2
    if pos == "center":
        y = (h - target_h) // 2
    elif pos == "lower":
        y = h - target_h - round(h * margin)
    else:                                    # upper
        y = round(h * margin)
    sheet.alpha_composite(card, (x, max(0, min(y, h - target_h))))
    return sheet


# ── Compositing ─────────────────────────────────────────────────────── #

def blend_rgb(base: Image.Image, top: Image.Image, mode: str) -> Image.Image:
    if mode == "normal":
        return top
    if mode == "multiply":
        return ImageChops.multiply(base, top)
    if mode == "screen":
        return ImageChops.screen(base, top)
    if mode == "overlay":
        return ImageChops.overlay(base, top)
    if mode == "soft_light":
        return ImageChops.soft_light(base, top)
    raise ValueError(f"unknown blend mode {mode!r}")


def composite(bg: Image.Image, overlay: Image.Image,
              blend: str, opacity: float) -> Image.Image:
    """Blend `overlay` onto `bg`, honouring the overlay's alpha.

    Blend modes need both layers as RGB, so the blend is computed
    full-frame and then masked back by the overlay's own alpha (times
    `opacity`) — otherwise a multiply would darken the whole picture,
    not just where the card is.
    """
    base = bg.convert("RGB")
    top = overlay.convert("RGB")
    blended = blend_rgb(base, top, blend)
    alpha = overlay.getchannel("A")
    if opacity < 1.0:
        alpha = alpha.point(lambda v: round(v * opacity))
    out = base.copy()
    out.paste(blended, (0, 0), alpha)
    return out


# ── Text mode ───────────────────────────────────────────────────────── #

def render_text_layer(args, w: int, h: int, backdrop: Path) -> Image.Image:
    """Draw the text through the renderer's own overlay path."""
    from phase3.typography_common import TypographySpec, render_text_overlay

    spec = TypographySpec(
        template=args.template,
        text=args.text,
        subtitle=args.subtitle,
        width=w, height=h,
        family=args.family,
        scrim=args.scrim,
        overlay_anchor=args.anchor,
        # scrim="auto" measures luminance under the text block on this
        # image before deciding whether a plate is needed at all.
        backdrop_path=backdrop,
    )
    return render_text_overlay(spec, reveal_upto=args.reveal)


# ── CLI ─────────────────────────────────────────────────────────────── #

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, type=Path,
                    help="background photograph")
    ap.add_argument("--output", required=True, type=Path,
                    help="output PNG (settings are appended when a sweep "
                         "produces more than one file)")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fit", choices=("cover", "contain"), default="cover")

    g = ap.add_argument_group("text mode (renders the card like the film does)")
    g.add_argument("--text", default="", help="Arabic card text")
    g.add_argument("--subtitle", default="")
    g.add_argument("--template", choices=TEMPLATES, default="pull_quote")
    g.add_argument("--family", choices=("A", "B", "C"), default="B")
    g.add_argument("--scrim", choices=("auto", "off", "soft", "band"),
                   default="auto", help="background plate behind the text "
                                        "(auto = only on bright/busy frames)")
    g.add_argument("--anchor", choices=("auto", "center", "lower"),
                   default="auto")
    g.add_argument("--reveal", type=int, default=None, metavar="N",
                   help="word-by-word reveal: draw only the first N words")

    c = ap.add_argument_group("card mode (composites an existing card PNG)")
    c.add_argument("--card", type=Path, help="pre-rendered card image")
    c.add_argument("--card-scale", type=float, default=1.0,
                   help="card width as a fraction of the frame [1.0]")
    c.add_argument("--card-pos", choices=("center", "lower", "upper"),
                   default="center")
    c.add_argument("--card-margin", type=float, default=0.08,
                   help="margin from the edge for lower/upper [0.08]")
    c.add_argument("--card-key", action="store_true",
                   help="drop the card's flat background to transparency")
    c.add_argument("--card-key-tol", type=int, default=28,
                   help="how far from the corner colour still counts as "
                        "background [28]")
    c.add_argument("--card-feather", type=float, default=1.2,
                   help="soften the keyed edge, in pixels [1.2]")
    c.add_argument("--card-blend", default="normal",
                   help="comma-separated: " + ", ".join(BLENDS))
    c.add_argument("--card-opacity", default="1.0",
                   help="comma-separated 0–1 values")

    args = ap.parse_args(argv)

    if not args.image.exists():
        print(f"ERROR: no such image: {args.image}", file=sys.stderr)
        return 2
    if bool(args.text) == bool(args.card):
        print("ERROR: give exactly one of --text or --card", file=sys.stderr)
        return 2

    W, H = args.width, args.height
    bg = fit_image(Image.open(args.image), W, H, args.fit)

    blends = [b.strip() for b in args.card_blend.split(",") if b.strip()]
    opacities = [float(o) for o in args.card_opacity.split(",") if o.strip()]
    for b in blends:
        if b not in BLENDS:
            print(f"ERROR: unknown blend {b!r} (choose from {', '.join(BLENDS)})",
                  file=sys.stderr)
            return 2

    if args.text:
        layer = render_text_layer(args, W, H, args.image)
        note = f"text/{args.template}/family {args.family}/scrim {args.scrim}"
    else:
        if not args.card.exists():
            print(f"ERROR: no such card: {args.card}", file=sys.stderr)
            return 2
        card = Image.open(args.card).convert("RGBA")
        if args.card_key:
            card = key_out_background(card, args.card_key_tol, args.card_feather)
        layer = place(card, W, H, args.card_scale, args.card_pos, args.card_margin)
        note = (f"card {args.card.name}"
                f"{' keyed' if args.card_key else ''}"
                f"/scale {args.card_scale}/{args.card_pos}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sweep = len(blends) * len(opacities) > 1
    written = []
    for b in blends:
        for o in opacities:
            out = composite(bg, layer, b, o)
            path = args.output
            if sweep:
                path = args.output.with_name(
                    f"{args.output.stem}_{b}_{o:g}{args.output.suffix or '.png'}")
            out.save(path)
            written.append((path, b, o))

    print(f"\nBackground : {args.image}  →  {W}×{H} ({args.fit})")
    print(f"Overlay    : {note}")
    for path, b, o in written:
        print(f"  ✓ {path}   blend={b} opacity={o:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
