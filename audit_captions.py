#!/usr/bin/env python3
"""Whole-timeline audit of what the audience actually reads (P7.10).

Every caption fix so far was driven by a screenshot: someone spotted a
duplicated line or a missing clause, it got fixed, and the next
screening found another one somewhere else.  That is the wrong loop.
This tool checks the ENTIRE film mechanically, so a regression is found
before a render rather than after.

It reconstructs exactly what `render._write_captions` will burn, then
walks every narrated word and classifies it:

  OK          spoken and readable — burned as a caption, or on the
              typography card that is on screen at that moment
  LOST        spoken but nowhere on screen: no caption covers it and no
              card carries it.  The audience hears a sentence they
              cannot read.
  DUPLICATED  on the card AND in the caption burned underneath it at the
              same instant — the "stutter" the screenings kept catching
  LEAKED      burned inside a caption at a time when that word was NOT
              being spoken (text bleeding across a hidden card boundary)

Exit status is non-zero when any violation is found, so this can gate a
render.

Usage:
    python audit_captions.py --plan output/shot_plan.json \
        [--word-timings output/review/word_timings.json] \
        [--script output/review/script.txt] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from phase3.align import load_word_timings
from phase3.parser import parse_sections
from phase3.plan import (_norm_tokens, build_caption_events,
                         load_caption_events, load_plan,
                         repair_caption_events)
from phase3.render import TYPOGRAPHY_VISUALS, _write_captions

log = logging.getLogger("audit_captions")

# A word counts as "carried by the card" when the card's text contains it.
# A card is on screen for its whole shot, so any word spoken during that
# shot whose token appears on the card is readable.


def _parse_ass(path: Path) -> list[tuple[float, float, str]]:
    """(start, end, text) for every burned Dialogue line."""
    out: list[tuple[float, float, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        body = line.split(":", 1)[1]
        parts = body.split(",", 9)
        if len(parts) < 10:
            continue

        def _s(t: str) -> float:
            h, m, sec = t.strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(sec)

        text = parts[9].replace("\\N", " ").replace("‏", "")
        out.append((_s(parts[1]), _s(parts[2]), text))
    return out


def audit(plan_path: Path, timings_path: Path | None,
          script_path: Path | None) -> dict:
    shots = load_plan(plan_path)
    events = repair_caption_events(load_caption_events(plan_path), plan_path)

    timings = None
    if timings_path and timings_path.exists():
        timings = load_word_timings(timings_path)
    if not timings:
        # Fall back to the word timing stored inside the plan's events.
        from phase3.align import WordTiming
        timings = []
        for ev in events:
            for w in ev.words or []:
                timings.append(WordTiming(word=str(w[0]), start=float(w[1]),
                                          end=float(w[2]), source="plan"))
        timings.sort(key=lambda w: w.start)
    if not timings:
        raise SystemExit("no word timings available (pass --word-timings)")

    # Rebuild the event track the same way the renderer will, when we can.
    if script_path and script_path.exists():
        sections = parse_sections(script_path.read_text(encoding="utf-8"),
                                  script_path=script_path)
        if sections:
            events = build_caption_events(timings, sections)

    # Model the render loop's time-limited overlays (P7.14): a card drawn
    # over footage only owns the frame while its own line is spoken.  The
    # over-image path needs a prior real image to sit on, so it engages
    # only after the first non-typography shot — mirrored here exactly.
    from phase3.render import (_narration_words, _overlay_visible_window,
                               _OVERLAY_VISUALS)
    narr = _narration_words(events)
    overlay_windows: dict[int, tuple[float, float]] = {}
    seen_real = False
    for i, s in enumerate(shots):
        if s.visual not in TYPOGRAPHY_VISUALS:
            seen_real = True
            continue
        if s.visual in _OVERLAY_VISUALS and seen_real:
            w = _overlay_visible_window(s, narr)
            if w:
                overlay_windows[i] = w

    ass_path = plan_path.parent / ".audit_captions.ass"
    _write_captions(shots, ass_path, 1920, 1080, events=events,
                    overlay_windows=overlay_windows)
    burned = _parse_ass(ass_path)
    ass_path.unlink(missing_ok=True)

    # The windows the renderer silences, and the card text shown over
    # each.  A word inside one of these is readable ON THAT CARD — a
    # card holds for less time than its sentence takes to say, so its
    # line is deliberately suppressed for its whole spoken extent.
    from phase3.render import _card_speech_window
    card_windows = []
    for i, s in enumerate(shots):
        if s.visual not in TYPOGRAPHY_VISUALS:
            continue
        toks = set(_norm_tokens(s.typography_text or ""))
        if i in overlay_windows:
            # Time-limited overlay.  It is only ON SCREEN for its window
            # (that is what the leak check uses), but while it is on
            # screen it displays its ENTIRE line — so every word of that
            # line has been read by the audience, including the ones
            # spoken after the text has gone.  Credit readability across
            # the whole line, visibility only across the window.
            card_windows.append((*overlay_windows[i], toks, s))
            for w in (_card_speech_window(s, narr) or []):
                if w != (s.start, s.end):
                    card_windows.append((w[0], w[1], toks, s))
            continue
        if not getattr(s, "card_hides_captions", True):
            continue
        for w in (_card_speech_window(s, narr) or []):
            card_windows.append((w[0], w[1], toks, s))

    # Index the burned caption tokens by time.
    burned_tok = [(a, b, set(_norm_tokens(t)), t) for a, b, t in burned]

    def shot_at(t: float):
        for s in shots:
            if s.start - 1e-6 <= t < s.end + 1e-6:
                return s
        return shots[-1] if shots else None

    lost, dup, leaked = [], [], []
    ok = 0
    for w in timings:
        tok = (_norm_tokens(w.word) or [""])[0]
        if not tok:
            continue
        mid = (w.start + w.end) / 2
        s = shot_at(mid)

        # Is it burned as a caption while it is being spoken?
        in_caption = any(a <= mid <= b and tok in toks
                         for a, b, toks, _ in burned_tok)
        # Is it displayed on the card that suppressed it?
        on_card = any(a <= mid <= b and tok in toks
                      for a, b, toks, _ in card_windows)
        if not on_card:
            on_card = bool(s and s.visual in TYPOGRAPHY_VISUALS
                           and tok in set(_norm_tokens(s.typography_text or "")))

        if not in_caption and not on_card:
            lost.append((mid, w.word, s))
        else:
            ok += 1

    # Duplication is a LINE-level property, not a word-level one: Arabic
    # function words ("في", "من") legitimately appear on a card and in a
    # neighbouring sentence's subtitle at the same moment, and no viewer
    # reads that as a stutter.  What they do read as one is a burned
    # caption that substantially restates the card above it — so compare
    # whole texts, normalised by the shorter of the two.
    DUP_SHARE = 0.5
    MIN_DUP_TOKENS = 3      # a 1–2 word caption cannot "restate" anything;
                            # normalising by the shorter text would score
                            # any shared function word at 100 %
    for a, b, toks, text in burned_tok:
        if len(toks) < MIN_DUP_TOKENS:
            continue
        for i, s in enumerate(shots):
            if s.visual not in TYPOGRAPHY_VISUALS:
                continue
            v0, v1 = overlay_windows.get(i, (s.start, s.end))
            if v1 <= a or v0 >= b:
                continue
            card = set(_norm_tokens(s.typography_text or ""))
            if not card:
                continue
            share = len(card & toks) / min(len(card), len(toks))
            if share >= DUP_SHARE:
                dup.append((a, text, s, share))
                break

    # Leakage: a burned caption containing a token nobody was speaking
    # inside that caption's own time window.
    for a, b, toks, text in burned_tok:
        spoken = set()
        for w in timings:
            if w.end > a - 0.35 and w.start < b + 0.35:
                spoken.update(_norm_tokens(w.word))
        extra = toks - spoken
        if extra:
            leaked.append((a, b, sorted(extra), text))

    # LEAKAGE (the defect a viewer named on 15 separate shots): a
    # subtitle sharing the frame with a full-screen typography card.
    # Whatever the two texts say, two blocks of text at once reads as
    # corruption — the previous caption must be gone before the card
    # lands.
    leak = []
    for i, s in enumerate(shots):
        if s.visual not in TYPOGRAPHY_VISUALS:
            continue
        v0, v1 = overlay_windows.get(i, (s.start, s.end))
        for a, b, _toks, text in burned_tok:
            if b > v0 + 0.12 and a < v1 - 0.12:
                leak.append((v0, v1, text, s))
                break

    # Stubs: one- or two-word caption flashes.  Not a correctness bug —
    # the words ARE spoken then — but they read as flicker, so they are
    # reported separately rather than being silently dropped (dropping
    # them was P7.8's mistake: it deleted narration outright).
    stubs = [(a, b, t) for a, b, toks, t in burned_tok if len(t.split()) <= 2]

    return {"ok": ok, "lost": lost, "duplicated": dup, "leaked": leaked,
            "stubs": stubs, "leak": leak,
            "n_words": len(timings), "n_burned": len(burned),
            "shots": shots, "events": events, "timings": timings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--word-timings", type=Path, default=None)
    ap.add_argument("--script", type=Path, default=None)
    ap.add_argument("--max-lost", type=int, default=0, metavar="N",
                    help="tolerate up to N lost words before failing "
                         "(default 0).  DUPLICATED / LEAKED / "
                         "CAPTION-OVER-CARD always fail at 1 — those are "
                         "defects, whereas a handful of lost words is the "
                         "current floor on a real plan")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    plan = args.plan
    wt = args.word_timings
    sc = args.script
    if wt is None:
        for c in (plan.parent / "word_timings.json",
                  plan.parent / "review" / "word_timings.json"):
            if c.exists():
                wt = c
                break
    if sc is None:
        for c in (plan.parent / "script.txt",
                  plan.parent / "review" / "script.txt"):
            if c.exists():
                sc = c
                break

    r = audit(plan, wt, sc)
    n_defect = len(r["duplicated"]) + len(r["leaked"]) + len(r["leak"])
    n_lost_over = max(0, len(r["lost"]) - args.max_lost)
    n_bad = n_defect + n_lost_over
    print(f"\nAudit of {plan}")
    print(f"  {r['n_words']} narrated words, {r['n_burned']} burned caption lines")
    print(f"  readable      : {r['ok']}")
    print(f"  LOST          : {len(r['lost'])}"
          f"{f'   (budget {args.max_lost})' if args.max_lost else ''}")
    print(f"  DUPLICATED    : {len(r['duplicated'])}")
    print(f"  LEAKED lines  : {len(r['leaked'])}")
    print(f"  CAPTION-OVER-CARD: {len(r['leak'])}")
    print(f"  stub captions : {len(r['stubs'])}  (1–2 words; flicker, not loss)")

    def _runs(items):
        """Group consecutive word-level hits into readable runs."""
        runs, cur = [], []
        for mid, word, s in items:
            if cur and mid - cur[-1][0] < 1.2:
                cur.append((mid, word, s))
            else:
                if cur:
                    runs.append(cur)
                cur = [(mid, word, s)]
        if cur:
            runs.append(cur)
        return runs

    if r["lost"]:
        print("\n── LOST (spoken, never readable) ──")
        for run in _runs(r["lost"]):
            s = run[0][2]
            txt = " ".join(w for _, w, _ in run)
            print(f"  {run[0][0]:7.2f}s [{s.visual if s else '?'}] {txt}")
    if r["duplicated"]:
        print("\n── DUPLICATED (caption restates the card above it) ──")
        for t, text, s, share in r["duplicated"]:
            print(f"  {t:7.2f}s [{s.visual}] {share:.0%} shared")
            print(f"      card   : {s.typography_text}")
            print(f"      caption: {text}")
    if r["leak"]:
        print("\n── CAPTION VISIBLE DURING A TYPOGRAPHY CARD ──")
        for a, b, text, s in r["leak"]:
            print(f"  {a:7.2f}-{b:7.2f} [{s.visual}]")
            print(f"      card   : {s.typography_text[:70]}")
            print(f"      caption: {text[:70]}")

    if r["leaked"] and args.verbose:
        print("\n── LEAKED (burned but not spoken then) ──")
        for a, b, extra, text in r["leaked"]:
            print(f"  {a:7.2f}-{b:7.2f}  extra={extra}\n      {text}")

    if n_bad:
        detail = []
        if n_defect:
            detail.append(f"{n_defect} caption defect(s)")
        if n_lost_over:
            detail.append(f"{n_lost_over} lost word(s) over the "
                          f"{args.max_lost}-word budget")
        print(f"\nFAIL: {', '.join(detail)}")
    else:
        tail = f"; {len(r['lost'])} lost word(s) within budget" if r["lost"] else ""
        print(f"\nPASS: no caption defects{tail}")
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
