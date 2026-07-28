#!/usr/bin/env python3
"""Regenerate the caption track of an existing dossier's shot_plan.json.

Fixes plans saved before P7.7 (commit 873f9d8): those plans' caption
events carry no per-word timing (`words: []`), so a clipped span (e.g. a
sentence that starts under a hidden typography card) falls back to
burning the event's FULL text — leaking words spoken off-screen into the
visible caption (confirmed 2026-07-28 against a real render-only pass:
"الحركة القومية العربية،", spoken during a hidden typography card, was
burned into the following broll shot's caption).

This is a pure, local, deterministic re-derivation from the dossier's
own `word_timings.json` + `script.txt` — no Claude API call, no image
re-fetch, no change to shot timing/imagery. Re-run render-only afterward
against the SAME review dir to pick up the fix at zero extra API cost.

Usage:
    python regenerate_captions.py --review-dir output/review/
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from phase3.align import load_word_timings
from phase3.parser import parse_sections
from phase3.plan import build_caption_events, load_plan, save_plan

log = logging.getLogger("regenerate_captions")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review-dir", required=True, type=Path,
                     help="Dossier directory containing shot_plan.json, "
                          "word_timings.json, script.txt")
    ap.add_argument("--plan", type=Path, default=None,
                     help="Plan path override (default: <review-dir>/shot_plan.json)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    review_dir = args.review_dir
    plan_path = args.plan or (review_dir / "shot_plan.json")
    timings_path = review_dir / "word_timings.json"
    script_path = review_dir / "script.txt"

    for p in (plan_path, timings_path, script_path):
        if not p.exists():
            log.error("missing required file: %s", p)
            return 1

    old_captions = json.loads(plan_path.read_text(encoding="utf-8")).get("captions", []) \
        if plan_path.read_text(encoding="utf-8").strip().startswith("{") else []
    n_old_with_words = sum(1 for c in old_captions if c.get("words"))
    log.info("existing plan: %d caption events, %d already carry word timing",
             len(old_captions), n_old_with_words)

    shots = load_plan(plan_path)
    timings = load_word_timings(timings_path)
    script_text = script_path.read_text(encoding="utf-8")
    sections = parse_sections(script_text, script_path=script_path)
    if not sections:
        log.error("no recognisable sections in %s — aborting, plan left untouched",
                   script_path)
        return 1

    new_events = build_caption_events(timings, sections)
    log.info("regenerated %d caption events, all carrying word timing", len(new_events))

    save_plan(shots, plan_path, caption_events=new_events)
    log.info("wrote %s (%d shots, %d captions)", plan_path, len(shots), len(new_events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
