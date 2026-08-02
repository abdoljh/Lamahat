#!/usr/bin/env python3
"""Check that the SCRIPT and the NARRATION actually say the same thing (P7.15).

The captions are drawn from the script; the timing comes from aligning
that script to the audio.  If the two texts have drifted apart — the
script was edited after the MP3 was synthesised, or vice versa — then
every caption in the divergent stretch displays words the audience is
not hearing.  No renderer or planner can repair that: it is a data
mismatch, and the only fixes are to re-synthesise the narration or
restore the script.

Recorded on 2026-07-26 as the rule "after any script edit, either
re-synthesize the narration or revert the script — never mix".  This
tool enforces that rule instead of trusting it to be remembered.

Note on measuring divergence: compare by CONTENT, never by position.  A
positional diff of a script against its narration reports every token
after the first substitution as "different" — one changed clause reads
as 26 % of the film.  That is the same mistake the old aligner made.
`difflib` opcodes give the true picture; on the repo's current script
and narration that is 651/651, a clean 100 %.

Usage:
    python verify_narration.py --script output/review/script.txt \
        --word-timings output/review/word_timings.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import sys
from pathlib import Path

from phase3.align import _norm_for_match, tokenize_script

log = logging.getLogger("verify_narration")


def _fmt(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def verify(script_text: str, timings: list[tuple[str, float, float]],
           *, context: int = 8) -> dict:
    """Compare script tokens against the narrated tokens.

    `timings` holds what the aligner believes was SAID, in order.  The
    comparison is on normalised forms so a hamza seat or a final
    ta-marbuta doesn't register as a difference.
    """
    tokens = tokenize_script(script_text)
    a = [_norm_for_match(t) for t in tokens]
    b = [_norm_for_match(w) for w, _, _ in timings]

    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    matched = sum(blk.size for blk in sm.get_matching_blocks())
    regions = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        j_lo = min(j1, max(len(timings) - 1, 0))
        j_hi = min(max(j2 - 1, j_lo), max(len(timings) - 1, 0))
        regions.append({
            "tag": tag,
            "t0": timings[j_lo][1] if timings else 0.0,
            "t1": timings[j_hi][2] if timings else 0.0,
            "n_script": i2 - i1,
            "n_narrated": j2 - j1,
            "script": " ".join(tokens[i1:i1 + context]),
            "narrated": " ".join(w for w, _, _ in timings[j1:j1 + context]),
        })
    return {"n_script": len(tokens), "n_narrated": len(timings),
            "matched": matched, "regions": regions}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", required=True, type=Path)
    ap.add_argument("--word-timings", required=True, type=Path)
    ap.add_argument("--min-match", type=float, default=0.97,
                    help="fail below this matched fraction (default 0.97)")
    ap.add_argument("--max-regions", type=int, default=12)
    ap.add_argument("--max-run", type=int, default=3,
                    help="fail if any divergent run reaches this many tokens "
                         "(default 3 — about a second of wrong caption)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    for p in (args.script, args.word_timings):
        if not p.exists():
            print(f"ERROR: missing {p}", file=sys.stderr)
            return 2

    data = json.loads(args.word_timings.read_text(encoding="utf-8"))
    timings = [(str(w["word"]), float(w["start"]), float(w["end"]))
               for w in data]
    r = verify(args.script.read_text(encoding="utf-8"), timings)

    frac = r["matched"] / max(r["n_narrated"], 1)
    print(f"\nScript   : {args.script}   ({r['n_script']} tokens)")
    print(f"Narration: {args.word_timings}   ({r['n_narrated']} tokens)")
    print(f"Agreement: {r['matched']}/{r['n_narrated']}  ({frac:.1%})")

    real = [g for g in r["regions"] if max(g["n_script"], g["n_narrated"]) >= 2]
    if real:
        print(f"\n── {len(real)} divergent region(s) — the audience will read "
              f"words they are not hearing ──")
        for g in real[:args.max_regions]:
            print(f"\n  {_fmt(g['t0'])}–{_fmt(g['t1'])}  "
                  f"({g['n_script']} script / {g['n_narrated']} narrated tokens)")
            print(f"     script   : {g['script']}")
            print(f"     narration: {g['narrated']}")

    # A percentage alone is the wrong gate: one rewritten sentence is
    # 99 % agreement and still puts several seconds of wrong words on
    # screen.  Fail on either signal.
    worst = max((max(g["n_script"], g["n_narrated"]) for g in real), default=0)
    if frac < args.min_match or worst >= args.max_run:
        print(f"\nFAIL: agreement {frac:.1%}"
              f"{'' if frac >= args.min_match else f' (below {args.min_match:.0%})'}"
              f"; longest divergent run {worst} tokens"
              f"{'' if worst < args.max_run else f' (limit {args.max_run})'}.")
        print("The script and the narration are not the same text in those "
              "regions. Fix the DATA before rendering — either re-synthesise "
              "narration.mp3 from this script, or restore the script the MP3 "
              "was read from. Alignment cannot repair a content mismatch: "
              "captions are drawn from the script, so wherever the two "
              "differ the audience reads words nobody is saying.")
        return 1
    print("\nPASS: script and narration agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
