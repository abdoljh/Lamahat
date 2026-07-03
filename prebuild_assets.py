#!/usr/bin/env python3
"""
Phase 3 — pre-render asset review pass.

Reads a saved shot plan, runs the full image-fetch waterfall (LoC →
Wikimedia → Internet Archive → Pexels) for every image-needing shot,
downloads all candidates, vision-scores them, and writes a *review
dossier* to disk that the user can audit and edit before the actual
render burns anything to video.

After this finishes, the user opens the review directory in a file
browser, looks at the candidate thumbnails, edits `decisions.json` to
swap candidates / pin a portrait / drop personal images into
`overrides/`, then re-runs `render_plan.py --review-dir <same-dir>`.

The render pass consumes the dossier: for each shot it uses the
override → pinned portrait → chosen candidate, falling back to the
live fetcher only if nothing was pre-resolved.

Usage
-----
  python prebuild_assets.py \\
      --plan          output/al_askari_plan_v2.json \\
      --script        resources/script/al_askari_script.txt \\
      --audio         resources/audio/al_askari_audio.mp3 \\
      --book-title    "مذكرات جعفر العسكري" \\
      --character-name "Jafar al-Askari" \\
      --anthropic-key "$ANTHROPIC_API_KEY" \\
      --pexels-key    "$PEXELS_API_KEY" \\
      --review-dir    output/review/ \\
      --character-portrait /path/to/jafar.jpg  # optional but recommended

Cost
----
~28 image-needing shots × 3 sources × ~3 candidates per source × 1
Haiku vision call each = ~250 Haiku calls.  Plus pooled scoring per
shot.  Empirically ~$0.40-$0.60 with current pricing.  This is paid
once per plan and reused by every subsequent re-render.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

# Repo-root imports — this file sits at the repo root, beside the phase3/ package
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase3.plan import load_plan
from phase3.sources import Fetcher, FetcherConfig
from phase3.sources.base import is_free_license
from phase3.sources.photo_bank import (
    assign_photo_bank,
    caption_bank,
    list_bank_photos,
)
from phase3 import motion_parallax as mp
from phase3.sources.decisions import (
    CandidateEntry,
    Decisions,
    DECISIONS_FILENAME,
    OVERRIDES_SUBDIR,
    ShotDecision,
    is_image_shot,
    shot_folder_name,
    write_readme,
)


log = logging.getLogger("phase3.prebuild")


# ── Helpers ───────────────────────────────────────────────────────────── #

def _read_script(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _arabic_excerpt_for_shot(shot, full_script: str) -> str:
    """
    Best-effort: pull a short Arabic phrase the shot is "about".
    Prefers shot.typography_text (already the planner's pick).  Falls
    back to shot.caption_text.  Truncates to ~120 chars.
    """
    txt = (getattr(shot, "typography_text", "") or "").strip()
    if not txt:
        txt = (getattr(shot, "caption_text", "") or "").strip()
    if len(txt) > 120:
        txt = txt[:117] + "…"
    return txt


def _short_source_label(source: str, index_in_source: int) -> str:
    """Filename token used in candidate filenames: pexels_a, pexels_b, ..."""
    letter = chr(ord("a") + index_in_source) if index_in_source < 26 else f"{index_in_source}"
    return f"{source}_{letter}"


def _copy_into(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _resources_root() -> Path:
    """Resolve the resources directory.  Env LAMAHAT_RESOURCES wins;
    falls back to /content/resources (Colab) or ./resources elsewhere.

    This is the SINGLE source of truth for user-supplied character
    portraits and book covers.  Explicit path avoids the walk-up bug
    that captured `<review_dir>/overrides/` in earlier takes."""
    env = os.environ.get("LAMAHAT_RESOURCES")
    if env:
        return Path(env).expanduser().resolve()
    if Path("/content").is_dir():
        return Path("/content/resources")
    return (Path.cwd() / "resources").resolve()


# ── Per-shot processing ───────────────────────────────────────────────── #

def _process_shot(idx: int, shot, *, fetcher: Fetcher, review_dir: Path,
                  script_text: str,
                  parallax: bool = False,
                  parallax_backend: str = "depthanything",
                  bank_photo: Path | None = None,
                  bank_caption: str = "",
                  skip_waterfall: bool = False) -> ShotDecision | None:
    """Run the waterfall for one shot and write its review folder.

    `bank_photo`: a curated photo the Sonnet assignment pass matched to
    this shot.  It becomes the chosen winner; waterfall candidates are
    still captured as alternates unless `skip_waterfall` is True.
    """

    if not is_image_shot(shot.visual):
        return None

    shot_dir_name = shot_folder_name(idx, shot.visual)
    shot_dir = review_dir / shot_dir_name
    shot_dir.mkdir(parents=True, exist_ok=True)

    query = (shot.search_query or "").strip()
    duration = float(shot.end - shot.start)

    log.info("Shot %d/%s: query=%r duration=%.1fs%s",
             idx, shot.visual, query[:60], duration,
             f"  [photo_bank: {bank_photo.name}]" if bank_photo else "")

    # Run the fetcher — this writes candidates to the on-disk cache, vision-
    # scores them, and picks a winner.  We replay the data back into our
    # review-dir layout.
    result = None
    if not (bank_photo and skip_waterfall):
        try:
            result = fetcher.fetch_for_shot(query=query, shot_index=idx)
        except Exception as exc:
            log.warning("Shot %d: fetcher raised %s — emitting empty decision",
                        idx, exc)
            result = None

    # Build the candidate entry list.  We copy each downloaded cache file
    # into the shot folder so the user can browse without traversing the
    # ~/.cache hierarchy.
    candidates: list[CandidateEntry] = []
    seen_per_source: dict[str, int] = {}
    chosen_entry: CandidateEntry | None = None

    # Sonnet-assigned bank photo: copied into the shot folder, recorded as
    # candidate #1 and the chosen winner.  Waterfall candidates (below)
    # remain as alternates the curator can swap back to.
    if bank_photo is not None and bank_photo.exists():
        dest = shot_dir / f"bank_{bank_photo.name}"
        try:
            _copy_into(bank_photo, dest)
            bank_entry = CandidateEntry(
                source="photo_bank",
                title=f"Photo bank: {bank_caption or bank_photo.name}"[:120],
                url=bank_photo.as_uri(),
                file=f"{shot_dir_name}/{dest.name}",
                license_short="user-supplied",
            )
            candidates.append(bank_entry)
            chosen_entry = bank_entry
            if parallax:
                try:
                    mp.ensure_depth_cached(dest, backend=parallax_backend)
                except Exception as exc:
                    log.warning("Shot %d: depth pre-warm failed: %s", idx, exc)
        except OSError as exc:
            log.warning("Shot %d: couldn't copy bank photo %s → %s: %s",
                        idx, bank_photo, dest, exc)

    if result is not None:
        for cand in result.candidates:
            n_so_far = seen_per_source.get(cand.source, 0)
            seen_per_source[cand.source] = n_so_far + 1

            label = _short_source_label(cand.source, n_so_far)
            rel_file = ""
            if cand.local_path and Path(cand.local_path).exists():
                ext = Path(cand.local_path).suffix or ".jpg"
                dest = shot_dir / f"{label}{ext}"
                try:
                    _copy_into(Path(cand.local_path), dest)
                    rel_file = f"{shot_dir_name}/{dest.name}"
                except OSError as exc:
                    log.warning("Shot %d: couldn't copy %s → %s: %s",
                                idx, cand.local_path, dest, exc)

            score = cand.total_score if cand.is_scored else -1
            score_breakdown = None
            if cand.is_scored:
                score_breakdown = {
                    "subject":   cand.score_subject,
                    "quality":   cand.score_quality,
                    "cinematic": cand.score_cinematic,
                }
                if cand.score_era >= 0:
                    score_breakdown["era"] = cand.score_era

            entry = CandidateEntry(
                source=cand.source,
                title=cand.title,
                url=cand.url,
                file=rel_file,
                score=score,
                score_breakdown=score_breakdown,
                vision_reason=cand.vision_reason,
                width=cand.width,
                height=cand.height,
                license_short=cand.license_short,
            )
            candidates.append(entry)

            # The fetcher's `best` is the one that won; mark it — unless a
            # bank photo already claimed the shot (bank wins, waterfall
            # candidates stay as alternates).
            if chosen_entry is None and result.best is not None and (
                cand.url == result.best.url and cand.source == result.best.source
            ):
                chosen_entry = entry
                # Pre-warm a depth map next to the chosen candidate's cache
                # file (the same path render.py reads) so render --parallax can
                # apply 2.5D motion without re-estimating depth.
                if (parallax and cand.local_path
                        and Path(cand.local_path).exists()):
                    try:
                        mp.ensure_depth_cached(Path(cand.local_path),
                                               backend=parallax_backend)
                    except Exception as exc:
                        log.warning("Shot %d: depth pre-warm failed: %s",
                                    idx, exc)

    # Write per-shot artefacts (context, candidates copy).
    arabic = _arabic_excerpt_for_shot(shot, script_text)
    context_lines = [
        f"Shot {idx} — {shot.visual}",
        f"Duration: {duration:.2f} s   (timeline {shot.start:.2f} → {shot.end:.2f} s)",
        f"Search query (English): {query}",
        f"Spoken / typography excerpt (Arabic): {arabic}" if arabic else "",
        "",
        "Candidates:",
    ]
    if not candidates:
        context_lines.append("  (no candidates returned by any source)")
    else:
        for c in candidates:
            score_str = f"score {c.score}/9" if c.score >= 0 else "unscored"
            era = (c.score_breakdown or {}).get("era", -1)
            era_flag = "  ⚠ ERA MISMATCH" if 0 <= era < 2 else ""
            context_lines.append(
                f"  [{c.source:<16}] {score_str:<12} {c.title[:80]}{era_flag}"
            )
            if c.vision_reason:
                context_lines.append(f"      → {c.vision_reason[:120]}")
    (shot_dir / "context.txt").write_text(
        "\n".join(L for L in context_lines if L is not None) + "\n",
        encoding="utf-8",
    )
    (shot_dir / "candidates.json").write_text(
        json.dumps([c.__dict__ for c in candidates], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    chosen_str = ""
    chosen_url = ""
    chosen_file = ""
    if chosen_entry is not None:
        chosen_str  = f"{chosen_entry.source}:{chosen_entry.title}"
        chosen_url  = chosen_entry.url
        chosen_file = chosen_entry.file

    return ShotDecision(
        visual=shot.visual,
        query=query,
        duration_sec=duration,
        arabic_caption_excerpt=arabic,
        chosen=chosen_str,
        chosen_url=chosen_url,
        chosen_file=chosen_file,
        override=None,
        candidates=candidates,
    )


# ── Main ──────────────────────────────────────────────────────────────── #

def main():
    ap = argparse.ArgumentParser(
        description="Pre-fetch and score candidate images, then emit a "
                    "review dossier for the user to edit before render.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--plan", type=Path, required=True,
                    help="Saved shot plan JSON (from phase3_run --plan-only)")
    ap.add_argument("--script", type=Path, default=None,
                    help="Original Arabic script (for context.txt excerpts)")
    ap.add_argument("--review-dir", type=Path, required=True,
                    help="Where to write the dossier (created if absent)")
    ap.add_argument("--book-title", default="",
                    help="Arabic book title — used as vision-scoring context")
    ap.add_argument("--character-name", default="",
                    help="Main character name in Latin — vision context "
                         "and search query disambiguation")
    ap.add_argument("--character-portrait", type=Path, default=None,
                    help="Path to a personal photo of the main character.  "
                         "Copied into overrides/character.jpg and pinned "
                         "as `pinned_portrait` in decisions.json — every "
                         "`portrait` shot will then use this single image.")
    ap.add_argument("--book-cover", type=Path, default=None,
                    help="Path to the book cover image.  Copied into "
                         "overrides/book_cover.<ext> and recorded as "
                         "book.cover_path in decisions.json — the title "
                         "card is then composited over the cover with a "
                         "gold title instead of the default cream design.")
    ap.add_argument("--book-cover-fit",
                    choices=("fill", "contain", "blur_pad"),
                    default="fill",
                    help="How the cover image is fitted into the 16:9 "
                         "frame.  'fill' (default) scales-and-crops to "
                         "fill — best for 16:9 hero artwork.  'contain' "
                         "letterboxes the entire image with cream side "
                         "bars — preserves every pixel.  'blur_pad' "
                         "letterboxes with a blurred-cover background — "
                         "cinematic for portrait-shaped covers.")
    ap.add_argument("--book-cover-align",
                    choices=("center", "left", "right"),
                    default="center",
                    help="Horizontal alignment of the cover image when "
                         "the fit mode leaves spare horizontal space "
                         "(contain or blur_pad).  'center' (default) "
                         "places equal bars on each side; 'left' or "
                         "'right' flushes the cover to that edge.  The "
                         "gold title overlay automatically shifts to the "
                         "opposite side so it sits over the empty area.")
    ap.add_argument("--anthropic-key", default="",
                    help="ANTHROPIC_API_KEY for Haiku vision scoring")
    ap.add_argument("--pexels-key", default="",
                    help="PEXELS_API_KEY (optional)")
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="Disk cache root.  Defaults to ~/.cache/lamahat/images")
    ap.add_argument("--book-extracts", type=Path, default=None,
                    help="Phase 1a photos.zip or directory.  Vision-scored "
                         "against each shot's query.")
    ap.add_argument("--photo-bank", type=Path, default=None,
                    help="Directory of curated photos for the book (Path C). "
                         "Each photo is Haiku-captioned once (cached in "
                         "captions.json), then ONE Sonnet call assigns photos "
                         "to shots; assigned photos become the dossier's "
                         "chosen winners.  Auto-detected at "
                         "$LAMAHAT_RESOURCES/photo_bank/ when omitted.")
    ap.add_argument("--photo-bank-only", action="store_true",
                    help="For shots that received a photo-bank assignment, "
                         "skip the web waterfall entirely (faster, cheaper; "
                         "no alternate candidates captured for those shots).")
    ap.add_argument("--photo-bank-max-uses", type=int, default=1,
                    help="How many shots one bank photo may cover (default 1).")
    ap.add_argument("--n-candidates", type=int, default=3,
                    help="Candidates to request per source (default 3)")
    ap.add_argument("--no-vision", action="store_true",
                    help="Skip vision scoring entirely (faster, no API cost; "
                         "candidates are pooled unscored)")
    ap.add_argument("--parallax", action="store_true",
                    help="Pre-compute & cache a depth map (<stem>.depth.png) "
                         "next to each chosen image so render.py --parallax can "
                         "apply 2.5D parallax motion without re-estimating "
                         "depth at render time.")
    ap.add_argument("--parallax-backend",
                    choices=("depthanything", "classical"),
                    default="depthanything",
                    help="Depth estimator for --parallax (default "
                         "depthanything; 'classical' is a dependency-free CPU "
                         "fallback for smoke tests).")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s  %(message)s",
    )
    # Don't dump base64 image data into the log on --verbose
    for noisy in ("anthropic", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # ── Load plan ─────────────────────────────────────────────── #
    if not args.plan.exists():
        log.error("Plan file not found: %s", args.plan)
        return 2
    shots = load_plan(args.plan)
    log.info("Loaded plan: %d shots", len(shots))

    n_image_shots = sum(1 for s in shots if is_image_shot(s.visual))
    log.info("Image-needing shots: %d (the rest are typography)", n_image_shots)

    script_text = _read_script(args.script) if args.script else ""

    # ── Prepare review directory ──────────────────────────────── #
    review_dir = args.review_dir.resolve()
    review_dir.mkdir(parents=True, exist_ok=True)
    overrides_dir = review_dir / OVERRIDES_SUBDIR
    overrides_dir.mkdir(parents=True, exist_ok=True)
    write_readme(review_dir)

    pinned_portrait_rel: str | None = None
    # Detect user-supplied portrait pool at `$LAMAHAT_RESOURCES/character/`.
    # When the pool exists with at least one image, skip the
    # single-file pin copy and leave `pinned_portrait` unset in
    # decisions.json — the renderer's pool resolver
    # (Decisions._list_portrait_pool) will discover the pool directly.
    pool_root = _resources_root()
    pool_dir = pool_root / "character"
    pool_has_images = (
        pool_dir.is_dir() and any(
            f.is_file() and f.suffix.lower() in (".jpg", ".jpeg",
                                                  ".png", ".webp")
            for f in pool_dir.iterdir()
        )
    )

    if pool_has_images:
        log.info("Portrait pool detected at %s — skipping pinned-portrait "
                 "copy (pool will be used at render time)", pool_dir)
        if args.character_portrait is not None:
            log.info("(--character-portrait %s ignored in favour of the "
                     "pool)", args.character_portrait)
    elif args.character_portrait is not None:
        src = args.character_portrait.expanduser().resolve()
        if not src.exists():
            log.error("--character-portrait path does not exist: %s", src)
            return 2
        ext = src.suffix.lower() or ".jpg"
        dest = overrides_dir / f"character{ext}"
        _copy_into(src, dest)
        pinned_portrait_rel = f"{OVERRIDES_SUBDIR}/{dest.name}"
        log.info("Pinned portrait copied → %s", dest)

    book_cover_rel: str | None = None
    # Detect user-supplied book cover at `$LAMAHAT_RESOURCES`:
    #   `$LAMAHAT_RESOURCES/book_cover.<ext>`  (single file)
    #   `$LAMAHAT_RESOURCES/book_cover/`        (directory pool)
    # In both cases skip the prebuild copy and leave book.cover_path
    # unset; render_plan.py auto-discovers + applies --book-cover-pick.
    resources_root_cover: Path | None = None
    if pool_root.is_dir():
        # 1. Single-file override
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            for f in pool_root.iterdir():
                if (f.is_file() and f.stem.lower() == "book_cover"
                        and f.suffix.lower() == ext):
                    resources_root_cover = f
                    break
            if resources_root_cover:
                break
        # 2. Directory pool
        if resources_root_cover is None:
            pool_d = pool_root / "book_cover"
            if pool_d.is_dir() and any(
                    f.is_file() and f.suffix.lower() in (".jpg", ".jpeg",
                                                          ".png", ".webp")
                    for f in pool_d.iterdir()):
                resources_root_cover = pool_d

    if resources_root_cover is not None:
        kind = "directory pool" if resources_root_cover.is_dir() else "file"
        log.info("Book cover %s detected at %s — skipping prebuild copy "
                 "(render_plan will auto-discover it)",
                 kind, resources_root_cover)
        if args.book_cover is not None:
            log.info("(--book-cover %s ignored in favour of resources/)",
                     args.book_cover)
    elif args.book_cover is not None:
        src = args.book_cover.expanduser().resolve()
        if not src.exists():
            log.error("--book-cover path does not exist: %s", src)
            return 2
        ext = src.suffix.lower() or ".jpg"
        dest = overrides_dir / f"book_cover{ext}"
        _copy_into(src, dest)
        book_cover_rel = f"{OVERRIDES_SUBDIR}/{dest.name}"
        log.info("Book cover copied → %s", dest)

    # ── Photo bank (Path C): caption + one-call Sonnet assignment ── #
    # Explicit --photo-bank wins; otherwise auto-detect the resources
    # convention (same pattern as character/ and book_cover/).
    bank_dir: Path | None = None
    if args.photo_bank is not None:
        bank_dir = args.photo_bank.expanduser().resolve()
        if not bank_dir.is_dir():
            log.error("--photo-bank path is not a directory: %s", bank_dir)
            return 2
    else:
        auto = pool_root / "photo_bank"
        if auto.is_dir() and list_bank_photos(auto):
            bank_dir = auto
            log.info("Photo bank auto-detected at %s", bank_dir)

    bank_assignments: dict[int, Path] = {}
    bank_captions: dict[str, str] = {}
    if bank_dir is not None:
        n_bank = len(list_bank_photos(bank_dir))
        if not n_bank:
            log.warning("Photo bank %s contains no images — skipping", bank_dir)
        elif not args.anthropic_key:
            log.warning("Photo bank %s found (%d photos) but no "
                        "--anthropic-key — captioning/assignment need the "
                        "API; skipping", bank_dir, n_bank)
        else:
            log.info("Photo bank: captioning %d photo(s) (cached ones are "
                     "free)…", n_bank)
            bank_captions = caption_bank(
                bank_dir, anthropic_api_key=args.anthropic_key,
                book_title=args.book_title,
                character_name=args.character_name)
            log.info("Photo bank: assigning photos to shots (one Sonnet "
                     "call)…")
            assigned = assign_photo_bank(
                shots, bank_dir,
                anthropic_api_key=args.anthropic_key,
                book_title=args.book_title,
                character_name=args.character_name,
                captions=bank_captions,
                max_uses_per_photo=args.photo_bank_max_uses,
                debug_dir=review_dir,
            )
            bank_assignments = {idx: bank_dir / fname
                                for idx, fname in assigned.items()}

    # ── Configure the fetcher ─────────────────────────────────── #
    cfg = FetcherConfig(
        anthropic_api_key=args.anthropic_key,
        pexels_api_key=args.pexels_key,
        cache_dir=args.cache_dir,
        user_dir=None,
        book_extracts=args.book_extracts,
        book_title=args.book_title,
        character_name=args.character_name,
        n_candidates_per_source=args.n_candidates,
        enable_vision=(False if args.no_vision else None),
    )
    fetcher = Fetcher(config=cfg)

    # ── Walk shots, build decisions ───────────────────────────── #
    book_dict = {"title": args.book_title, "character": args.character_name}
    if book_cover_rel:
        book_dict["cover_path"] = book_cover_rel
        book_dict["cover_fit"] = args.book_cover_fit
        book_dict["cover_align"] = args.book_cover_align
    decisions = Decisions(
        book=book_dict,
        pinned_portrait=pinned_portrait_rel,
        shots={},
    )

    processed = 0
    for idx0, shot in enumerate(shots):
        idx = idx0 + 1                # decisions.json uses 1-indexed
        bank_photo = bank_assignments.get(idx)
        decision = _process_shot(
            idx=idx, shot=shot,
            fetcher=fetcher,
            review_dir=review_dir,
            script_text=script_text,
            parallax=args.parallax,
            parallax_backend=args.parallax_backend,
            bank_photo=bank_photo,
            bank_caption=(bank_captions.get(bank_photo.name, "")
                          if bank_photo else ""),
            skip_waterfall=args.photo_bank_only,
        )
        if decision is None:
            continue
        decisions.shots[idx] = decision
        processed += 1

    # ── Persist ───────────────────────────────────────────────── #
    decisions.save(review_dir)

    # ── Summary ───────────────────────────────────────────────── #
    have_chosen     = sum(1 for d in decisions.shots.values() if d.chosen)
    no_candidates   = sum(1 for d in decisions.shots.values() if not d.candidates)
    by_source = {}
    for d in decisions.shots.values():
        if d.chosen:
            src = d.chosen.split(":", 1)[0]
            by_source[src] = by_source.get(src, 0) + 1

    # Winners whose vision score flagged the era as implausible — the
    # curator should look at these shots first.
    era_flagged: list[int] = []
    for idx, d in decisions.shots.items():
        for c in d.candidates:
            if (d.chosen_file and c.file == d.chosen_file
                    and 0 <= (c.score_breakdown or {}).get("era", -1) < 2):
                era_flagged.append(idx)
                break

    print()
    print("─" * 64)
    print(f"Review dossier ready: {review_dir}")
    print(f"Image shots processed:  {processed}")
    print(f"  with a chosen winner: {have_chosen}")
    print(f"  with no candidates:   {no_candidates}")
    if by_source:
        print("Winner source breakdown:")
        for src, n in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"  {src:<16} {n}")
    if bank_assignments:
        print(f"Photo bank:             {bank_dir}")
        print(f"  assigned to {len(bank_assignments)} shot(s): "
              f"{', '.join(str(i) for i in sorted(bank_assignments))}")
    if era_flagged:
        print(f"⚠ Era-mismatch winners on {len(era_flagged)} shot(s): "
              f"{', '.join(str(i) for i in sorted(era_flagged))}")
        print("  (see the ⚠ ERA MISMATCH lines in each shot's context.txt)")
    if pinned_portrait_rel:
        print(f"Pinned portrait:        {pinned_portrait_rel}")
        n_portraits = sum(1 for d in decisions.shots.values()
                          if d.visual == "portrait")
        print(f"  affects {n_portraits} portrait shot(s) at render time")
    if book_cover_rel:
        print(f"Book cover:             {book_cover_rel}")
        print(f"  fit mode:             {args.book_cover_fit}")
        print(f"  align:                {args.book_cover_align}")
        n_title_cards = sum(1 for s in shots if s.visual == "title_card")
        print(f"  affects {n_title_cards} title-card shot(s) at render time")
    print()
    print("Next:")
    print(f"  1. Open {review_dir}/ and review each shot folder.")
    print(f"  2. Edit {review_dir/DECISIONS_FILENAME} to swap candidates,")
    print(f"     drop overrides into {review_dir/OVERRIDES_SUBDIR}/,")
    print(f"     or set 'pinned_portrait'.")
    print(f"  3. Render:")
    print(f"     python render_plan.py --plan {args.plan} \\")
    print(f"         --review-dir {review_dir} \\")
    print(f"         --audio <audio> --output <output.mp4>")
    print("─" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())