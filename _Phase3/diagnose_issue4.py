#!/usr/bin/env python3
"""
diagnose_issue4.py — Trace the title-card / caption changes against
the live repo state.  Run from _Phase3/.

This script answers: "Is the issue-4 patch actually in effect at
render time?"  It does NOT render a video.  It inspects the files
that govern the title card and captions, and reports each piece of
the chain so the failure mode is visible.

Usage:
    python diagnose_issue4.py
    python diagnose_issue4.py --review-dir output/review/
    python diagnose_issue4.py --review-dir output/review/ --book-cover my_cover.jpg
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("diag4")

OK   = "✓"
WARN = "⚠"
FAIL = "✗"


def check_typography_patched():
    """Confirm phase3/typography.py is the patched version."""
    log.info("\n── typography.py ──")
    sys.path.insert(0, str(Path.cwd()))
    try:
        from phase3 import typography as t
    except ImportError as e:
        log.info(f"{FAIL} cannot import phase3.typography: {e}")
        return False

    file_path = Path(t.__file__)
    log.info(f"  loaded from: {file_path}")

    ok = True
    if hasattr(t, "GOLD_AGED"):
        log.info(f"  {OK} GOLD_AGED constant present: {t.GOLD_AGED}")
    else:
        log.info(f"  {FAIL} GOLD_AGED constant MISSING — typography.py is unpatched")
        ok = False

    fields = {f.name for f in t.TypographySpec.__dataclass_fields__.values()}
    for fname in ("cover_image", "accent_color"):
        if fname in fields:
            log.info(f"  {OK} TypographySpec.{fname} present")
        else:
            log.info(f"  {FAIL} TypographySpec.{fname} MISSING — typography.py is unpatched")
            ok = False

    title_main = t.SIZES.get("title_main")
    title_sub  = t.SIZES.get("title_sub")
    if title_main == 0.110:
        log.info(f"  {OK} SIZES['title_main'] = 0.110 (patched)")
    else:
        log.info(f"  {FAIL} SIZES['title_main'] = {title_main} (expected 0.110)")
        ok = False
    if title_sub == 0.040:
        log.info(f"  {OK} SIZES['title_sub'] = 0.040 (patched)")
    else:
        log.info(f"  {FAIL} SIZES['title_sub'] = {title_sub} (expected 0.040)")
        ok = False

    # Check the two renderer split exists
    has_cream  = hasattr(t, "_render_title_card_cream")
    has_cover  = hasattr(t, "_render_title_card_with_cover")
    if has_cream and has_cover:
        log.info(f"  {OK} title-card renderer split into cream and cover variants")
    else:
        log.info(f"  {FAIL} renderer split MISSING — typography.py is unpatched "
                 f"(cream={has_cream}, cover={has_cover})")
        ok = False

    return ok


def check_render_patched():
    """Confirm phase3/render.py is the patched version."""
    log.info("\n── render.py ──")
    try:
        from phase3 import render as r
    except ImportError as e:
        log.info(f"{FAIL} cannot import phase3.render: {e}")
        return False

    file_path = Path(r.__file__)
    log.info(f"  loaded from: {file_path}")

    ok = True
    cfg = r.RenderConfig()
    if hasattr(cfg, "book_cover"):
        log.info(f"  {OK} RenderConfig.book_cover present (default: {cfg.book_cover!r})")
    else:
        log.info(f"  {FAIL} RenderConfig.book_cover MISSING — render.py is unpatched")
        ok = False

    # Caption gap arithmetic: grep the source for GAP = 0.15
    source = Path(r.__file__).read_text()
    if "GAP = 0.15" in source or "GAP=0.15" in source:
        log.info(f"  {OK} caption GAP = 0.15 found in source (patched)")
    else:
        log.info(f"  {FAIL} caption GAP = 0.15 NOT found — render.py is unpatched")
        ok = False

    if "MIN_VISIBLE = 0.6" in source:
        log.info(f"  {OK} caption MIN_VISIBLE = 0.6 found in source (patched)")
    else:
        log.info(f"  {WARN} caption MIN_VISIBLE = 0.6 NOT found — render.py may be partial-patched")

    # v3 additions — these check the changes that actually affect caption
    # layout, not just timing.  If GAP is present but these are missing,
    # the user has the v2 patch (timing only) but not v3 (line-break,
    # wrap-style, bigger font).
    if "WrapStyle: 2" in source:
        log.info(f"  {OK} ASS header has WrapStyle: 2 (v3 — line wrap enabled)")
    else:
        log.info(f"  {FAIL} ASS header is missing WrapStyle: 2 — "
                 f"long captions won't wrap to two lines")
        ok = False

    if "_wrap_caption" in source:
        log.info(f"  {OK} _wrap_caption() helper present (v3 — inserts \\N breaks)")
    else:
        log.info(f"  {FAIL} _wrap_caption() helper MISSING — "
                 f"captions will not be split into two lines")
        ok = False

    # Detect the v3.0/3.1 escape-order bug: _wrap_caption was being
    # called BEFORE _escape_ass, so the \N got doubled to \\N and
    # rendered as a literal backslash on screen.
    wrap_first  = re.search(r"text\s*=\s*_wrap_caption\([^)]*\)\s*\n\s*text\s*=\s*_escape_ass", source)
    escape_first = re.search(r"text\s*=\s*_escape_ass\([^)]*\)\s*\n\s*text\s*=\s*_wrap_caption", source)
    if escape_first:
        log.info(f"  {OK} caption escape order is _escape_ass → _wrap_caption (v3.2 — correct)")
    elif wrap_first:
        log.info(f"  {FAIL} caption escape order is _wrap_caption → _escape_ass (v3.0/3.1 bug)")
        log.info(f"         the \\N gets doubled to \\\\N, libass renders a literal '\\' on screen")
        ok = False

    if "height * 0.050" in source or "height*0.050" in source:
        log.info(f"  {OK} caption font size at 5.0% of frame height (v3)")
    elif "height * 0.042" in source:
        log.info(f"  {FAIL} caption font size still at 4.2% — render.py is v2, not v3")
        ok = False
    else:
        log.info(f"  {WARN} caption font size: pattern not recognised")

    return ok


def check_render_plan_patched():
    """Confirm render_plan.py CLI has the --book-cover flag."""
    log.info("\n── render_plan.py ──")
    rp = Path("render_plan.py")
    if not rp.exists():
        log.info(f"  {FAIL} render_plan.py not at {rp.resolve()}")
        return False
    src = rp.read_text()
    if "--book-cover" in src:
        log.info(f"  {OK} --book-cover flag found in CLI")
        return True
    else:
        log.info(f"  {FAIL} --book-cover flag MISSING — render_plan.py is unpatched")
        return False


def check_prebuild_patched():
    """Confirm prebuild_assets.py CLI has --book-cover."""
    log.info("\n── prebuild_assets.py ──")
    pb = Path("prebuild_assets.py")
    if not pb.exists():
        log.info(f"  {FAIL} prebuild_assets.py not at {pb.resolve()}")
        return False
    src = pb.read_text()
    if "--book-cover" in src:
        log.info(f"  {OK} --book-cover flag found in CLI")
        return True
    else:
        log.info(f"  {FAIL} --book-cover flag MISSING — prebuild_assets.py is unpatched")
        return False


def check_dossier_cover(review_dir: Path):
    """Inspect decisions.json for a cover entry."""
    log.info(f"\n── dossier ({review_dir}) ──")
    decisions_path = review_dir / "decisions.json"
    if not decisions_path.exists():
        log.info(f"  {WARN} no decisions.json at {decisions_path}")
        return None

    try:
        data = json.loads(decisions_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.info(f"  {FAIL} cannot parse decisions.json: {e}")
        return None

    book = data.get("book", {})
    log.info(f"  book.title:     {book.get('title', '(none)')}")
    log.info(f"  book.character: {book.get('character', '(none)')}")

    cover_path = book.get("cover_path")
    if cover_path:
        full = (review_dir / cover_path).resolve()
        if full.exists():
            log.info(f"  {OK} book.cover_path = {cover_path}")
            log.info(f"         resolves to: {full}")
            log.info(f"         file size: {full.stat().st_size} bytes")
            return full
        else:
            log.info(f"  {FAIL} book.cover_path = {cover_path} but file MISSING: {full}")
            return None
    else:
        log.info(f"  {WARN} book.cover_path not in dossier")
        log.info(f"         re-run prebuild_assets.py with --book-cover to set it,")
        log.info(f"         OR pass --book-cover PATH on render_plan.py")
        return None


def predict_resolution(args):
    """Predict which title-card path the renderer will take."""
    log.info(f"\n── prediction ──")

    chosen: Path | None = None
    why = ""

    # CLI override
    if args.book_cover:
        p = Path(args.book_cover).expanduser().resolve()
        if p.exists():
            chosen = p
            why = f"--book-cover {args.book_cover}"
        else:
            log.info(f"  {FAIL} --book-cover {p} does not exist — would error at render time")
            return

    # Dossier
    if chosen is None and args.review_dir:
        rd = Path(args.review_dir).resolve()
        d = check_dossier_cover(rd)
        if d:
            chosen = d
            why = f"dossier book.cover_path"

    if chosen:
        log.info(f"  {OK} title cards will use COVER MODE (gold + photo)")
        log.info(f"         cover: {chosen}")
        log.info(f"         source: {why}")
    else:
        log.info(f"  {WARN} title cards will fall back to CREAM MODE")
        log.info(f"         (this is why your title card looks unchanged)")
        log.info(f"")
        log.info(f"  To switch to cover mode for your next render:")
        log.info(f"      python render_plan.py --plan ... --audio ... \\")
        log.info(f"          --book-cover /path/to/cover.jpg --output ...")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review-dir", type=Path, default=None,
                    help="Path to output/review/")
    ap.add_argument("--book-cover", type=Path, default=None,
                    help="Path to a candidate cover image")
    args = ap.parse_args()

    typo_ok    = check_typography_patched()
    render_ok  = check_render_patched()
    rp_ok      = check_render_plan_patched()
    pb_ok      = check_prebuild_patched()

    predict_resolution(args)

    log.info("\n── summary ──")
    log.info(f"  typography.py patched:        {OK if typo_ok else FAIL}")
    log.info(f"  render.py patched:            {OK if render_ok else FAIL}")
    log.info(f"  render_plan.py patched:       {OK if rp_ok else FAIL}")
    log.info(f"  prebuild_assets.py patched:   {OK if pb_ok else FAIL}")

    if not (typo_ok and render_ok and rp_ok and pb_ok):
        log.info(f"\n  {FAIL} One or more files are unpatched.  Re-copy from issue4_patch_A.zip.")
        return 1

    log.info(f"\n  {OK} All four files are patched.")
    log.info(f"  If your render still shows no change, check the prediction above:")
    log.info(f"  if it says CREAM MODE, you need to supply --book-cover or re-run prebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
