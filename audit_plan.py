#!/usr/bin/env python3
"""
audit_plan.py — Audit a Phase 3 shot plan JSON for quality and structural issues.

Reads a plan written by phase3_run.py --plan-only --save-plan and prints
a multi-section quality report.

Usage
-----
    python audit_plan.py output/shot_plan.json
    python audit_plan.py output/shot_plan.json --script samples/script.txt
                                               --audio output/audio.mp3

When --script is provided, typography text is checked against the script
for verbatim quotation.  When --audio is provided, the plan's last shot
end-time is checked against the actual audio duration.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


_ARABIC_WORD_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\w]+",
    re.UNICODE,
)


def _normalise_arabic(text: str) -> str:
    """Strip diacritics, punctuation, and tatweel for matching."""
    # Arabic diacritics (harakat)
    text = re.sub(r"[\u064B-\u0652\u0670\u0640]", "", text)
    text = re.sub(r"[،.؟!:؛\"'`]", "", text)
    return text.strip()


def probe_audio_duration(audio_path: Path) -> float | None:
    """Get audio duration via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(audio_path)],
            capture_output=True, text=True, timeout=10, check=True)
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception as exc:
        print(f"  (ffprobe failed: {exc})", file=sys.stderr)
        return None


_OVERLAY_VISUALS = {"typography", "section_mark", "chapter_heading"}
_HOLD_FLAG_SEC = 10.0


def _effective_holds(plan: list[dict], review_dir: Path | None,
                     overlay_on: bool) -> None:
    """
    Report EFFECTIVE visual holds — what the eye sees, not what the plan
    says (P1.2, SCREENING_REVIEW.md §2.2).  Two mechanisms make the
    perceived cut rate slower than the plan's:

      1. typography-over-image shots reuse the previous shot's footage,
         so an image shot + its overlay cards read as ONE hold;
      2. adjacent image shots can resolve to the same (or a perceptually
         identical) picture — detected via dHash when --review-dir is
         given, approximated by identical (visual, query) otherwise.

    Spans longer than 10 s of continuous footage are flagged.  Note:
    render-time dedupe (on by default) will swap automatic picks that
    duplicate their neighbour — this audit shows the dossier as-is, so
    a [duplicate] flag here means "dedupe will act or --no-dedupe will
    show a repeat".
    """
    decisions = None
    dhash = near_dup = None
    if review_dir is not None:
        try:
            import sys as _sys
            root = str(Path(__file__).resolve().parent)
            if root not in _sys.path:
                _sys.path.insert(0, root)
            from phase3.sources.decisions import Decisions
            from phase3.sources.dedupe import dhash as _dh
            from phase3.sources.dedupe import near_duplicate as _nd
            decisions = Decisions.load(review_dir)
            dhash, near_dup = _dh, _nd
        except Exception as exc:  # noqa: BLE001 — audit must not crash
            print(f"⚠  --review-dir given but dossier unusable ({exc}) — "
                  f"falling back to query-equality duplicate detection")
            decisions = None

    runs: list[dict] = []
    cur: dict | None = None

    def _new_run(i: int, s: dict, img_hash, img_key) -> dict:
        return {"first": i, "last": i, "start": s["start"], "end": s["end"],
                "n_img": 0, "n_ovl": 0, "dup": False,
                "img_hash": img_hash, "img_key": img_key}

    for i, s in enumerate(plan, 1):
        v = s["visual"]
        if v in _OVERLAY_VISUALS or v == "title_card":
            is_overlay = (v in _OVERLAY_VISUALS and overlay_on
                          and cur is not None and cur["img_key"] is not None)
            if is_overlay:
                cur["last"], cur["end"] = i, s["end"]
                cur["n_ovl"] += 1
                continue
            # Static card / title card — its own (non-footage) segment.
            if cur:
                runs.append(cur)
            cur = _new_run(i, s, None, None)
            runs.append(cur)
            cur = None
            continue

        # Image shot — identity via resolved dHash or (visual, query).
        img_hash = None
        img_key: object = (v, (s.get("search_query") or "").strip())
        if decisions is not None:
            try:
                p = decisions.resolve(i, review_dir)
            except Exception:  # noqa: BLE001
                p = None
            if p is not None:
                img_hash = dhash(p)
                img_key = str(p)
            else:
                img_key = ("placeholder", i)

        same = False
        if cur is not None and cur["img_key"] is not None:
            if img_hash is not None and cur["img_hash"] is not None:
                same = near_dup(img_hash, cur["img_hash"])
            else:
                same = (img_key == cur["img_key"])
        if same:
            cur["last"], cur["end"] = i, s["end"]
            cur["n_img"] += 1
            cur["dup"] = True
        else:
            if cur:
                runs.append(cur)
            cur = _new_run(i, s, img_hash, img_key)
            cur["n_img"] = 1
    if cur:
        runs.append(cur)

    n_shots = len(plan)
    plan_avg = sum(s["end"] - s["start"] for s in plan) / max(1, n_shots)
    eff_avg = sum(r["end"] - r["start"] for r in runs) / max(1, len(runs))
    mode = ("dossier-resolved dHash" if decisions is not None
            else "query-equality approximation")
    print(f"Effective visual holds "
          f"(overlay {'merged' if overlay_on else 'NOT merged'}; "
          f"duplicates via {mode}):")
    print(f"   Effective segments: {len(runs)} (plan shots: {n_shots})")
    print(f"   Effective avg hold: {eff_avg:.2f}s vs plan avg {plan_avg:.2f}s")

    long_runs = sorted((r for r in runs
                        if r["end"] - r["start"] > _HOLD_FLAG_SEC),
                       key=lambda r: r["start"])
    if long_runs:
        print(f"   ⚠ {len(long_runs)} continuous-footage span(s) "
              f"exceed {_HOLD_FLAG_SEC:.0f}s:")
        for r in long_runs:
            dur = r["end"] - r["start"]
            parts = []
            if r["n_img"]:
                parts.append(f"image ×{r['n_img']}")
            if r["n_ovl"]:
                parts.append(f"overlay ×{r['n_ovl']}")
            flag = "  [adjacent duplicate images]" if r["dup"] else ""
            span = (f"shot {r['first']}" if r["first"] == r["last"]
                    else f"shots {r['first']}–{r['last']}")
            print(f"      {dur:5.1f}s  {span:<14} ({', '.join(parts)}){flag}")
        if any(r["dup"] for r in long_runs):
            print("      (duplicate spans: render-time dedupe swaps the "
                  "automatic pick unless --no-dedupe;")
            print("       hand-picked/pool/photo-bank choices are respected "
                  "as-is)")
    else:
        print(f"   ✓ No continuous-footage span exceeds "
              f"{_HOLD_FLAG_SEC:.0f}s")
    print()


def audit(plan: list[dict],
          script_text: str | None,
          audio_duration: float | None,
          review_dir: Path | None = None,
          overlay_on: bool = True) -> None:
    n = len(plan)
    if n == 0:
        print("Plan is empty.")
        return

    # ── Basic timing stats ──────────────────────────────────────────── #
    durations = [s["end"] - s["start"] for s in plan]
    plan_start = plan[0]["start"]
    plan_end = plan[-1]["end"]

    print("=" * 70)
    print(f"PLAN AUDIT")
    print("=" * 70)
    print()
    print(f"Total shots:        {n}")
    print(f"Plan timeline:      {plan_start:.2f}s → {plan_end:.2f}s "
          f"({plan_end - plan_start:.1f}s)")
    print(f"Average shot:       {sum(durations)/n:.2f}s")
    print(f"Range:              {min(durations):.2f}s – {max(durations):.2f}s")

    if audio_duration is not None:
        diff = plan_end - audio_duration
        marker = "✓" if abs(diff) < 1.0 else "⚠"
        print(f"Audio duration:     {audio_duration:.2f}s  "
              f"({marker} {'matches' if abs(diff) < 1.0 else f'plan overshoots by {diff:+.1f}s'})")
    print()

    # ── Gap / overlap detection ─────────────────────────────────────── #
    gaps = []
    overlaps = []
    for i in range(n - 1):
        delta = plan[i + 1]["start"] - plan[i]["end"]
        if abs(delta) > 0.05:
            (gaps if delta > 0 else overlaps).append((i, delta))
    if gaps:
        print(f"⚠  {len(gaps)} gap(s) between shots:")
        for i, d in gaps[:5]:
            print(f"     shot {i+1}→{i+2}: gap of {d:+.2f}s")
        if len(gaps) > 5:
            print(f"     … and {len(gaps) - 5} more")
    if overlaps:
        print(f"⚠  {len(overlaps)} overlap(s) between shots:")
        for i, d in overlaps[:5]:
            print(f"     shot {i+1}→{i+2}: overlap of {-d:.2f}s")
    if not gaps and not overlaps:
        print("✓  No gaps or overlaps")
    print()

    # ── Effective visual holds (perceived pacing) ───────────────────── #
    _effective_holds(plan, review_dir, overlay_on)

    # ── Visual / motion histograms ──────────────────────────────────── #
    visuals = Counter(s["visual"] for s in plan)
    motions = Counter(s["motion"] for s in plan)
    sections = Counter(s.get("section_id", "(unknown)") for s in plan)

    print("Visual types:")
    for v, c in visuals.most_common():
        bar = "█" * int(c * 30 / n)
        print(f"   {v:<14} {c:>3} ({100*c/n:>4.0f}%) {bar}")
    print()
    print("Motion types:")
    for m, c in motions.most_common():
        bar = "█" * int(c * 30 / n)
        print(f"   {m:<14} {c:>3} ({100*c/n:>4.0f}%) {bar}")
    print()
    print("Section coverage:")
    for sid, c in sections.most_common():
        print(f"   {sid:<14} {c:>3} shots")
    print()

    # ── Auto-split fragmentation ────────────────────────────────────── #
    split_shots = [s for s in plan if "auto-split" in s.get("note", "")]
    if split_shots:
        # Count original shots that got split (unique base notes)
        bases = Counter()
        for s in split_shots:
            base = s.get("note", "").split(" [auto-split")[0]
            bases[base] += 1
        n_originals = len(bases)
        n_pieces = sum(bases.values())
        original_count = (n - n_pieces) + n_originals
        pct = 100 * n_pieces / n

        marker = "✓" if pct < 20 else ("⚠" if pct < 50 else "❌")
        print(f"{marker}  Auto-split shots: {n_pieces}/{n} ({pct:.0f}%) "
              f"from {n_originals} original(s)")
        if pct >= 20:
            print(f"     Likely meaning: Sonnet planned shots above the hard cap.")
            print(f"     If most splits are 2-piece duplicates, raise the cap.")
        print(f"     Implied original Sonnet shot count: {original_count}")
    else:
        print(f"✓  No auto-split shots — Sonnet's pacing fits the hard cap")
    print()

    # ── Typography audit ────────────────────────────────────────────── #
    typo_shots = [s for s in plan if s.get("typography_text", "").strip()]
    if typo_shots:
        # Dedupe by text (splits produce duplicates)
        seen = set()
        unique = []
        for s in typo_shots:
            tt = s["typography_text"].strip()
            if tt not in seen:
                seen.add(tt)
                unique.append(s)

        print(f"Typography texts: {len(unique)} unique "
              f"({len(typo_shots)} occurrences after splits)")

        # Word count distribution
        wcs = [len(s["typography_text"].split()) for s in unique]
        print(f"   Word count: min={min(wcs)}, max={max(wcs)}, avg={sum(wcs)/len(wcs):.1f}")

        # Template distribution
        tpls = Counter(s.get("typography_template") or "(none)" for s in unique)
        print(f"   Templates: " + ", ".join(f"{t}:{c}" for t, c in tpls.items()))
        print()

        # Verbatim check (if script provided)
        if script_text is not None:
            script_words = _ARABIC_WORD_RE.findall(_normalise_arabic(script_text))
            script_joined = " ".join(script_words)

            verbatim, partial, paraphrase = 0, 0, 0
            paraphrase_examples: list[str] = []

            for s in unique:
                q_words = _ARABIC_WORD_RE.findall(_normalise_arabic(s["typography_text"]))
                q_joined = " ".join(q_words)
                if q_joined in script_joined:
                    verbatim += 1
                else:
                    # Longest contiguous prefix match
                    n_match = 0
                    for k in range(len(q_words), 0, -1):
                        sub = " ".join(q_words[:k])
                        if sub in script_joined:
                            n_match = k
                            break
                    pct = n_match / len(q_words) if q_words else 0
                    if pct >= 0.6:
                        partial += 1
                    else:
                        paraphrase += 1
                        if len(paraphrase_examples) < 3:
                            paraphrase_examples.append(s["typography_text"])

            total = len(unique)
            print(f"   Verbatim from script:  {verbatim}/{total} "
                  f"({100*verbatim/total:.0f}%)")
            print(f"   Partial/condensed:     {partial}/{total} "
                  f"({100*partial/total:.0f}%)")
            print(f"   Paraphrased/invented:  {paraphrase}/{total} "
                  f"({100*paraphrase/total:.0f}%)")
            if paraphrase_examples:
                print(f"   Paraphrase examples:")
                for ex in paraphrase_examples:
                    print(f"     • {ex}")
        print()

    # ── Search query quality ────────────────────────────────────────── #
    queries = [s["search_query"].strip()
               for s in plan
               if s.get("search_query", "").strip()]
    if queries:
        avg_words = sum(len(q.split()) for q in queries) / len(queries)
        bare_queries = [q for q in queries if len(q.split()) <= 2]
        print(f"Search queries: {len(queries)} non-empty, avg {avg_words:.1f} words")
        marker = "✓" if not bare_queries else "⚠"
        print(f"   {marker} Bare (1–2 words): {len(bare_queries)} "
              f"{'(none — good specificity)' if not bare_queries else ''}")
        if bare_queries[:5]:
            for q in bare_queries[:5]:
                print(f"      • {q!r}")
        print()

    print("=" * 70)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan_path", help="Path to shot_plan.json")
    ap.add_argument("--script", help="Optional: original script .txt for verbatim audit")
    ap.add_argument("--audio", help="Optional: audio file for duration check")
    ap.add_argument("--review-dir", type=Path, default=None,
                    help="Optional: review dossier directory.  Resolves each "
                         "image shot exactly as the renderer would and "
                         "detects adjacent near-duplicate images by "
                         "perceptual hash (otherwise duplicates are "
                         "approximated by identical visual+query).")
    ap.add_argument("--assume-overlay", choices=("on", "off"), default="on",
                    help="Whether the effective-holds report merges "
                         "typography shots into the footage they overlay "
                         "(matches --typography-over-image at render; "
                         "default on — the notebooks' default).")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan_path).read_text(encoding="utf-8"))

    script_text = None
    if args.script:
        script_text = Path(args.script).read_text(encoding="utf-8")

    audio_dur = None
    if args.audio:
        audio_dur = probe_audio_duration(Path(args.audio))

    audit(plan, script_text, audio_dur,
          review_dir=args.review_dir,
          overlay_on=(args.assume_overlay == "on"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
