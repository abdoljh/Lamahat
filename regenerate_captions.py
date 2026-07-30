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
re-fetch, no change to shot timing/imagery.

NOTE: a dossier normally holds MORE THAN ONE `shot_plan.json` — the
render-only notebook renders `output/shot_plan.json` while the dossier
carries its own `output/review/shot_plan.json`.  Patching only one of
them silently changes nothing (confirmed 2026-07-29).  This script
therefore repairs EVERY copy it finds and prints each path.  Since P7.8
the renderer also self-heals at load time, so this script is now a
convenience for making the dossier permanently correct.

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
from phase3.plan import (build_caption_events, load_plan, save_plan,
                         sync_typography_to_speech)

log = logging.getLogger("regenerate_captions")


def _plan_copies(review_dir: Path, explicit: Path | None) -> list[Path]:
    """Every shot_plan.json the render might read, deduplicated."""
    if explicit:
        return [explicit]
    seen: dict[Path, None] = {}
    for cand in (review_dir / "shot_plan.json",
                 review_dir.parent / "shot_plan.json"):
        if cand.exists():
            seen.setdefault(cand.resolve(), None)
    return list(seen)


def _n_with_words(path: Path) -> tuple[int, int]:
    """(events, events carrying word timing) for a plan on disk."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return (0, 0)
    caps = data.get("captions", []) if isinstance(data, dict) else []
    return (len(caps), sum(1 for c in caps if c.get("words")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review-dir", required=True, type=Path,
                    help="Dossier directory containing word_timings.json + "
                         "script.txt (and usually shot_plan.json)")
    ap.add_argument("--plan", type=Path, default=None,
                    help="Repair only this plan (default: every shot_plan.json "
                         "in the review dir AND its parent)")
    ap.add_argument("--no-sync-cards", action="store_true",
                    help="skip the P7.9 typography/speech sync (captions only)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    review_dir = args.review_dir
    timings_path = review_dir / "word_timings.json"
    script_path = review_dir / "script.txt"

    for p in (timings_path, script_path):
        if not p.exists():
            log.error("missing required file: %s", p)
            return 1

    plans = _plan_copies(review_dir, args.plan)
    if not plans:
        log.error("no shot_plan.json found in %s or %s",
                  review_dir, review_dir.parent)
        return 1
    log.info("plan copies to repair: %s", ", ".join(str(p) for p in plans))

    timings = load_word_timings(timings_path)
    script_text = script_path.read_text(encoding="utf-8")
    sections = parse_sections(script_text, script_path=script_path)
    if not sections:
        log.error("no recognisable sections in %s — aborting, plans untouched",
                  script_path)
        return 1

    new_events = build_caption_events(timings, sections)
    log.info("regenerated %d caption events, all carrying word timing",
             len(new_events))

    for plan_path in plans:
        n_before, n_words_before = _n_with_words(plan_path)
        shots = load_plan(plan_path)
        if not args.no_sync_cards:
            shots, rep = sync_typography_to_speech(shots, timings)
            log.info("card sync: %d trimmed, %d left as designed but "
                     "uncovering captions, of %d card(s)",
                     rep["trimmed"], rep["uncovered"], rep["n_cards"])
        save_plan(shots, plan_path, caption_events=new_events)
        log.info("wrote %s (%d shots, %d captions; was %d captions / %d with "
                 "word timing)", plan_path, len(shots), len(new_events),
                 n_before, n_words_before)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
