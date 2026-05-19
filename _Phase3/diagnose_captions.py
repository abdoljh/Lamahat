#!/usr/bin/env python3
"""
diagnose_captions.py — Call _write_captions directly against the saved
plan and print every ASS event with its time window.  No FFmpeg, no
render.  Tells us definitively whether the patched caption-gap code
is the code that actually runs.

Run from _Phase3/:

    python diagnose_captions.py --plan output/al_askari_plan_v2.json

Expected output (if patched):
  - first column "gap_from_prev" should show 0.30 s between any two
    consecutive caption events
  - log line "GAP value used by _write_captions: 0.15"
"""
from __future__ import annotations
import argparse
import importlib
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--width",  type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    args = ap.parse_args()

    # Defeat .pyc cache: force reimport
    for mod in list(sys.modules):
        if mod.startswith("phase3"):
            del sys.modules[mod]

    from phase3.plan import load_plan
    from phase3 import render as r

    # Inspect the source to confirm what version of _write_captions is loaded
    src = Path(r.__file__).read_text(encoding="utf-8")
    gap_match = re.search(r"GAP\s*=\s*([\d.]+)", src)
    min_match = re.search(r"MIN_VISIBLE\s*=\s*([\d.]+)", src)
    pre_roll_match = re.search(r"shot\.start\s*\+\s*0\.05", src)
    print(f"render.py loaded from : {r.__file__}")
    print(f"GAP in source         : {gap_match.group(1) if gap_match else 'NOT FOUND'}")
    print(f"MIN_VISIBLE in source : {min_match.group(1) if min_match else 'NOT FOUND'}")
    if pre_roll_match:
        print(f"⚠ ALSO FOUND        : old 'shot.start + 0.05' pre-roll line — file has both?")
    print()

    shots = load_plan(args.plan)
    print(f"plan                  : {args.plan}  ({len(shots)} shots)")

    # Call _write_captions
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "captions.ass"
        result = r._write_captions(shots, dest, args.width, args.height)
        if result is None:
            print("⚠ _write_captions returned None — no captions to emit")
            return 0

        # Parse the ASS file
        ass = dest.read_text(encoding="utf-8")
        events = []
        for line in ass.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            # Dialogue: 0,H:MM:SS.cc,H:MM:SS.cc,Style,...,Text
            _, rest = line.split(":", 1)
            fields = rest.split(",", 9)
            start_s = _ts_to_sec(fields[1].strip())
            end_s   = _ts_to_sec(fields[2].strip())
            text    = fields[9].strip() if len(fields) >= 10 else ""
            events.append((start_s, end_s, text))

        print(f"events parsed         : {len(events)}")
        print()

        # Print first 10 events with gap to previous
        print(f"{'#':>3}  {'start':>7}  {'end':>7}  {'span':>5}  "
              f"{'gap_from_prev':>13}  text (first 50 chars)")
        print("─" * 100)
        prev_end = None
        gaps = []
        for i, (s, e, t) in enumerate(events[:15]):
            gap_str = ""
            if prev_end is not None:
                gap = s - prev_end
                gaps.append(gap)
                gap_str = f"{gap:+.3f}s"
            text_preview = (t[:50] + "…") if len(t) > 50 else t
            print(f"{i+1:>3d}  {s:7.3f}  {e:7.3f}  {e-s:5.3f}  "
                  f"{gap_str:>13}  {text_preview}")

        if gaps:
            avg_gap = sum(gaps) / len(gaps)
            print()
            print(f"Gaps (first {len(gaps)}): min={min(gaps):.3f}s  "
                  f"avg={avg_gap:.3f}s  max={max(gaps):.3f}s")
            print()
            # The verdict
            if any(0.28 < g < 0.32 for g in gaps):
                print("✓ Gaps of ~0.30s found — PATCH IS LIVE.")
                print("  If the rendered video looks unchanged, the change may")
                print("  be too subtle to notice (0.30s = 7-8 frames).")
            elif any(0.08 < g < 0.12 for g in gaps):
                print("✗ Gaps of ~0.10s found — STALE CODE IS RUNNING.")
                print("  The source shows the patched GAP=0.15, but the executing")
                print("  bytecode is the old 0.05-each-side version.  Most likely a")
                print("  .pyc cache problem.  In Colab:")
                print("    1. Runtime → Restart runtime")
                print("    2. Re-run cell 0 (clone repo)")
                print("    3. Apply your patched files to /content/phase3/")
                print("    4. Verify with: python diagnose_issue4.py")
                print("    5. Re-render")
            else:
                print(f"? Unexpected gap pattern.  Most common gap: "
                      f"{max(set(round(g, 2) for g in gaps), key=lambda v: sum(1 for g in gaps if round(g,2)==v))}")
    return 0


def _ts_to_sec(ts: str) -> float:
    """H:MM:SS.cc → seconds."""
    parts = ts.split(":")
    h, m, s = parts
    return int(h) * 3600 + int(m) * 60 + float(s)


if __name__ == "__main__":
    sys.exit(main())
