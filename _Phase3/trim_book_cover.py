#!/usr/bin/env python3
"""
trim_book_cover.py — Crop a photograph-of-a-book to just the book.

When the source cover image is a photograph that includes paper or
table background around the book itself, this script auto-detects
the book's bounding box by isolating the dark (non-cream) pixels and
crops the photo to a small margin around that box.

The output is then suitable for use with
    python prebuild_assets.py --book-cover <trimmed>.jpg
or
    python render_plan.py --book-cover <trimmed>.jpg
with --book-cover-fit contain (or blur_pad).

This is a one-shot helper.  No phase3 imports — pure Pillow + numpy.

Usage:
    python trim_book_cover.py input.jpg
    python trim_book_cover.py input.jpg --output book_cover.jpg
    python trim_book_cover.py input.jpg --threshold 160 --margin 5

Defaults:
    --output    same directory, basename + "_trimmed"
    --threshold 150   (luminance cutoff: pixels < threshold are 'book')
    --margin    3     (% of bbox added on each side for breathing room)

If auto-detection fails for your input (e.g. the cover is mostly bright,
or the background is dark), tune --threshold or fall back to a manual
crop in any image editor.
"""
import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
    import numpy as np
except ImportError as e:
    print(f"This script needs Pillow and numpy: pip install Pillow numpy", file=sys.stderr)
    print(f"  ({e})", file=sys.stderr)
    sys.exit(2)


def detect_book_bbox(arr: "np.ndarray", threshold: int) -> tuple[int, int, int, int] | None:
    """
    Return (x_min, y_min, x_max, y_max) of the dark region in arr,
    or None if no dark pixels are found.
    """
    if arr.ndim == 3:
        # RGB → luminance (simple mean is fine for this purpose)
        lum = arr.mean(axis=2)
    else:
        lum = arr
    mask = lum < threshold
    rows = mask.any(axis=1)
    cols = mask.any(axis=0)
    if not rows.any() or not cols.any():
        return None
    y_idx = np.where(rows)[0]
    x_idx = np.where(cols)[0]
    return int(x_idx[0]), int(y_idx[0]), int(x_idx[-1]), int(y_idx[-1])


def add_margin(box: tuple[int, int, int, int],
               canvas_w: int, canvas_h: int,
               margin_pct: float) -> tuple[int, int, int, int]:
    """Expand bbox by margin_pct on each side, clamped to canvas."""
    x0, y0, x1, y1 = box
    mx = int((x1 - x0) * margin_pct / 100)
    my = int((y1 - y0) * margin_pct / 100)
    return (max(0, x0 - mx),
            max(0, y0 - my),
            min(canvas_w, x1 + mx),
            min(canvas_h, y1 + my))


def main():
    ap = argparse.ArgumentParser(
        description="Crop a photo of a book to just the book",
        epilog="Run this once per source photo, then use the trimmed "
               "file as --book-cover.")
    ap.add_argument("input", type=Path, help="Photograph of the book")
    ap.add_argument("--output", "-o", type=Path, default=None,
                    help="Output path (default: <input>_trimmed.<ext>)")
    ap.add_argument("--threshold", type=int, default=150,
                    help="Luminance threshold (0-255). Pixels below this "
                         "are treated as 'book'. Default: 150.")
    ap.add_argument("--margin", type=float, default=3.0,
                    help="Breathing room as %% of bbox, each side. "
                         "Default: 3.0.")
    ap.add_argument("--quality", type=int, default=95,
                    help="JPEG output quality (1-100). Default: 95.")
    ap.add_argument("--show-bbox", action="store_true",
                    help="Print the detected bbox and stop, without writing.")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 1

    src = Image.open(args.input).convert("RGB")
    arr = np.array(src)
    print(f"Source: {args.input.name}  {src.size[0]}×{src.size[1]}"
          f"  aspect {src.size[0]/src.size[1]:.3f}")

    box = detect_book_bbox(arr, args.threshold)
    if box is None:
        print(f"ERROR: no dark pixels detected at threshold {args.threshold}. "
              f"Try a higher threshold, or crop manually.", file=sys.stderr)
        return 1
    x0, y0, x1, y1 = box
    print(f"Detected bbox: x [{x0}, {x1}]  y [{y0}, {y1}]  "
          f"({x1-x0}×{y1-y0})")

    if args.show_bbox:
        return 0

    x0, y0, x1, y1 = add_margin(box, src.size[0], src.size[1], args.margin)
    trimmed = src.crop((x0, y0, x1, y1))
    print(f"With {args.margin:.1f}% margin: {trimmed.size[0]}×{trimmed.size[1]}"
          f"  aspect {trimmed.size[0]/trimmed.size[1]:.3f}")

    out = args.output
    if out is None:
        stem = args.input.stem
        ext = args.input.suffix or ".jpg"
        out = args.input.with_name(f"{stem}_trimmed{ext}")
    trimmed.save(out, quality=args.quality)
    print(f"Saved: {out}  ({out.stat().st_size:,} bytes)")
    print()
    print("Next:")
    print(f"  python prebuild_assets.py ... --book-cover {out} "
          f"--book-cover-fit contain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
