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
from phase3.render import _typography_spec
from phase3.typography import render as render_typography
from phase3.sources import Fetcher, FetcherConfig
from phase3.sources.base import is_free_license
from phase3.sources.photo_bank import (
    assign_photo_bank,
    caption_bank,
    list_bank_photos,
)
from phase3.sources.vision import (
    is_period_era,
    passes_threshold,
    rank_candidates,
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
    subject_is_character,
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
    """Resolve the resources directory.  Env LAMAHAT_RESOURCES wins; then
    `<cwd>/resources` when it exists (the repo-clone layout — Colab work
    happens inside /content/Lamahat after `git clone`); then the legacy
    Colab copy-to-/content layout; then `<cwd>/resources` regardless.

    This is the SINGLE source of truth for user-supplied character
    portraits, book covers and the photo bank.  Explicit path avoids the
    walk-up bug that captured `<review_dir>/overrides/` in earlier takes."""
    env = os.environ.get("LAMAHAT_RESOURCES")
    if env:
        return Path(env).expanduser().resolve()
    cwd_resources = (Path.cwd() / "resources").resolve()
    if cwd_resources.is_dir():
        return cwd_resources
    if Path("/content").is_dir():
        return Path("/content/resources")
    return cwd_resources


# ── Per-shot processing ───────────────────────────────────────────────── #

def _process_shot(idx: int, shot, *, fetcher: Fetcher, review_dir: Path,
                  script_text: str,
                  parallax: bool = False,
                  parallax_backend: str = "depthanything",
                  bank_photo: Path | None = None,
                  bank_caption: str = "",
                  skip_waterfall: bool = False,
                  skip_reason: str = "",
                  keep_n: int = 3) -> ShotDecision | None:
    """Run the waterfall for one shot and write its review folder.

    `bank_photo`: a curated photo the Sonnet assignment pass matched to
    this shot.  It becomes the chosen winner; waterfall candidates are
    still captured as alternates unless `skip_waterfall` is True.

    `skip_waterfall` / `skip_reason`: the shot is already covered (bank
    assignment, character pool) — no web fetch, no vision cost; the
    decision entry records why.

    `keep_n` (P6.3): only the top `keep_n` ranked, threshold-passing
    candidates are COPIED into the shot folder.  Every candidate stays
    in candidates.json / context.txt as metadata (with its source URL),
    so nothing is hidden — only bytes are saved and rejects never
    masquerade as pickable files.
    """

    if not is_image_shot(shot.visual):
        return None

    shot_dir_name = shot_folder_name(idx, shot.visual)
    shot_dir = review_dir / shot_dir_name
    # A previous conditioning pass may have starred this folder
    # (`shot_NN_visual*` = needs attention).  Reuse it under the plain
    # name so a re-prebuild never creates a duplicate sibling.
    if not shot_dir.exists():
        for starred in sorted(review_dir.glob(shot_dir_name + "*")):
            if starred.is_dir():
                starred.rename(shot_dir)
                break
    shot_dir.mkdir(parents=True, exist_ok=True)

    query = (shot.search_query or "").strip()
    era = (getattr(shot, "era", "") or "").strip()
    duration = float(shot.end - shot.start)

    log.info("Shot %d/%s: query=%r era=%r duration=%.1fs%s%s",
             idx, shot.visual, query[:60], era[:40], duration,
             f"  [photo_bank: {bank_photo.name}]" if bank_photo else "",
             f"  [waterfall skipped: {skip_reason}]" if skip_reason else "")

    # Run the fetcher — this writes candidates to the on-disk cache, vision-
    # scores them, and picks a winner.  We replay the data back into our
    # review-dir layout.
    result = None
    if not skip_waterfall:
        try:
            result = fetcher.fetch_for_shot(query=query, shot_index=idx,
                                            visual_type=shot.visual,
                                            era=era)
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
        # Rank first (era tier → total score), then copy only the top
        # `keep_n` threshold-passing candidates into the shot folder
        # (P6.3).  The rest stay as metadata-only entries — their URLs
        # remain browsable, but sub-threshold rejects never sit on disk
        # looking pickable, and the dossier stays small.
        era_strict = is_period_era(era)
        ranked = rank_candidates(list(result.candidates))
        copy_set = {
            id(c) for c in
            [c for c in ranked
             if c.local_path and Path(c.local_path).exists()
             and passes_threshold(c, shot.visual, era_strict=era_strict)
             ][:max(1, keep_n)]
        }
        # The winner is always copied, whatever its rank.
        if result.best is not None:
            copy_set.add(id(result.best))

        for cand in ranked:
            n_so_far = seen_per_source.get(cand.source, 0)
            seen_per_source[cand.source] = n_so_far + 1

            label = _short_source_label(cand.source, n_so_far)
            rel_file = ""
            if (id(cand) in copy_set
                    and cand.local_path and Path(cand.local_path).exists()):
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
        f"Required era: {era}" if era else "",
        f"Spoken / typography excerpt (Arabic): {arabic}" if arabic else "",
        "",
        "Candidates:",
    ]
    if skip_reason:
        context_lines.append(f"  (web waterfall skipped — {skip_reason}; "
                             "rerun prebuild with --fetch-covered to "
                             "capture web alternates)")
    if not candidates:
        if not skip_reason:
            context_lines.append("  (no candidates returned by any source)")
    else:
        for c in candidates:
            score_str = f"score {c.score}/9" if c.score >= 0 else "unscored"
            c_era = (c.score_breakdown or {}).get("era", -1)
            era_flag = "  ⚠ ERA MISMATCH" if 0 <= c_era < 2 else ""
            saved_flag = "" if c.file else "  · not saved (see URL)"
            context_lines.append(
                f"  [{c.source:<16}] {score_str:<12} {c.title[:80]}"
                f"{era_flag}{saved_flag}"
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
        covered=skip_reason,
    )


def _process_typography_shot(idx: int, shot, *, review_dir: Path,
                             typography_family: str = "A",
                             preview: bool = True) -> ShotDecision:
    """Write the review folder for a typography-kind shot (title_card /
    section_mark / typography).

    These shots need no image, but they ARE part of the film — a dossier
    that silently skips them reads as if parts of the script were cut
    (shot_02, shot_04 … with no shot_01/shot_03).  Every shot now gets a
    folder: context.txt states the timing and the exact Arabic text the
    card will carry, and card_preview.png shows the rendered card so the
    curator reviews the *whole* film, not just the image shots.

    The decision entry records `covered` so conditioning never stars the
    folder and the render path is untouched (the renderer only consults
    the dossier for image shots).
    """
    shot_dir_name = shot_folder_name(idx, shot.visual)
    shot_dir = review_dir / shot_dir_name
    if not shot_dir.exists():
        for starred in sorted(review_dir.glob(shot_dir_name + "*")):
            if starred.is_dir():
                starred.rename(shot_dir)
                break
    shot_dir.mkdir(parents=True, exist_ok=True)

    text = (getattr(shot, "typography_text", "") or "").strip()
    template = (getattr(shot, "typography_template", None)
                or {"title_card": "title_card",
                    "section_mark": "section_mark",
                    "chapter_heading": "chapter_heading"}.get(
                        shot.visual, "pull_quote"))
    duration = float(shot.end - shot.start)

    log.info("Shot %d/%s: typography card (%s) duration=%.1fs",
             idx, shot.visual, template, duration)

    preview_note = "(preview not rendered)"
    if preview:
        try:
            spec = _typography_spec(shot, width=1280, height=720,
                                    typography_family=typography_family)
            render_typography(spec, shot_dir / "card_preview.png")
            preview_note = "card_preview.png"
        except Exception as exc:  # noqa: BLE001 — preview is best-effort
            log.warning("Shot %d: card preview failed (%s) — context only",
                        idx, exc)
            preview_note = f"(preview failed: {exc})"

    context_lines = [
        f"Shot {idx} — {shot.visual}  (typography card — no image needed)",
        f"Duration: {duration:.2f} s   (timeline {shot.start:.2f} → {shot.end:.2f} s)",
        f"Template: {template}",
        f"Arabic text on the card: {text}" if text else "",
        f"Spoken during this shot (Arabic): "
        f"{(getattr(shot, 'caption_text', '') or '').strip()[:200]}",
        "",
        f"Rendered preview: {preview_note}",
        "This shot renders from its text — there is nothing to source or",
        "swap here.  To change the wording, edit the shot plan JSON.",
    ]
    (shot_dir / "context.txt").write_text(
        "\n".join(L for L in context_lines if L) + "\n", encoding="utf-8")

    return ShotDecision(
        visual=shot.visual,
        query="",
        duration_sec=duration,
        arabic_caption_excerpt=(text or "")[:120],
        covered="typography card — renders from text, no image required",
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
                    help="Disk cache root.  Defaults to $LAMAHAT_CACHE or "
                         "~/.cache/lamahat/images.  Point it at a Drive "
                         "path on Colab so re-runs reuse downloads and "
                         "vision scores across sessions (P6.2/C5).")
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
                    help="(Legacy alias — skipping covered shots is now the "
                         "default; see --fetch-covered to opt back in.)")
    ap.add_argument("--photo-bank-max-uses", type=int, default=1,
                    help="How many shots one bank photo may cover (default 1).")
    ap.add_argument("--fetch-covered", action="store_true",
                    help="Also run the web waterfall for shots that are "
                         "already covered — photo-bank assignments and "
                         "character-pool portraits.  Default is to SKIP "
                         "them (P6.2/C3): they resolve from curated files "
                         "at render time, so fetching/scoring web "
                         "candidates for them is pure API cost.")
    ap.add_argument("--n-candidates", type=int, default=3,
                    help="Candidates to request per source (default 3)")
    ap.add_argument("--dossier-keep", type=int, default=3,
                    help="Copy at most N top-ranked, threshold-passing "
                         "candidates into each shot folder (default 3). "
                         "All candidates stay in candidates.json as "
                         "metadata; only files on disk are capped (P6.3).")
    ap.add_argument("--typography-family", choices=("A", "B", "C"),
                    default="A",
                    help="Typography family used for the card_preview.png "
                         "written into each typography shot's folder "
                         "(default A).  Match the family you plan to "
                         "render with so previews are faithful.")
    ap.add_argument("--no-card-previews", action="store_true",
                    help="Skip rendering card_preview.png for typography "
                         "shots (folders + context.txt are still written).")
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
            if not bank_assignments:
                # A populated bank producing ZERO assignments is almost
                # certainly a failure (API error, response format, key
                # parsing) — not a creative judgement.  Say so loudly and
                # leave a marker in the dossier so it can't ship unnoticed.
                msg = (f"photo bank has {n_bank} photo(s) but ZERO were "
                       f"assigned to shots — inspect "
                       f"{review_dir / 'photo_bank_assignment_raw.txt'} "
                       "before rendering; the film will use only web "
                       "imagery otherwise")
                log.error("photo_bank: %s", msg)
                print(f"\n{'!' * 64}\n⚠ PHOTO BANK NOT USED: {msg}\n{'!' * 64}\n")
                (review_dir / "PHOTO_BANK_NOT_ASSIGNED.txt").write_text(
                    msg + "\n", encoding="utf-8")

    # ── Configure the fetcher ─────────────────────────────────── #
    # Cache always on: without one, every prebuild re-downloads and
    # re-scores everything.  $LAMAHAT_CACHE lets Colab point it at a
    # Drive path that survives the VM (P6.2/C5).
    cache_dir = args.cache_dir
    if cache_dir is None:
        env_cache = os.environ.get("LAMAHAT_CACHE")
        cache_dir = (Path(env_cache).expanduser() if env_cache
                     else Path.home() / ".cache" / "lamahat" / "images")
    log.info("Image/score cache: %s", cache_dir)

    cfg = FetcherConfig(
        anthropic_api_key=args.anthropic_key,
        pexels_api_key=args.pexels_key,
        cache_dir=cache_dir,
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
    n_typography = 0
    skipped_covered = 0
    for idx0, shot in enumerate(shots):
        idx = idx0 + 1                # decisions.json uses 1-indexed

        # Typography-kind shots get a folder too (context + card preview)
        # so the dossier is a complete storyboard — shot_01, shot_02, …
        # with no holes.  They carry `covered` and are never starred.
        if not is_image_shot(shot.visual):
            decisions.shots[idx] = _process_typography_shot(
                idx, shot, review_dir=review_dir,
                typography_family=args.typography_family,
                preview=not args.no_card_previews,
            )
            n_typography += 1
            continue

        bank_photo = bank_assignments.get(idx)

        # Covered shots (P6.2/C3): a bank assignment or a character-pool
        # portrait resolves from curated files at render time — running
        # the web waterfall for them is pure fetch + vision cost.
        # Default: skip; --fetch-covered restores the old behaviour of
        # capturing web alternates for everything.
        skip_reason = ""
        if not args.fetch_covered:
            if bank_photo is not None:
                skip_reason = f"photo-bank assignment ({bank_photo.name})"
            elif (pool_has_images and shot.visual == "portrait"
                    and subject_is_character(shot.search_query or "",
                                             args.character_name)):
                skip_reason = "character pool covers this portrait"
        elif bank_photo is not None and args.photo_bank_only:
            skip_reason = f"photo-bank assignment ({bank_photo.name})"

        if skip_reason:
            skipped_covered += 1

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
            skip_waterfall=bool(skip_reason),
            skip_reason=skip_reason,
            keep_n=args.dossier_keep,
        )
        if decision is None:
            continue
        decisions.shots[idx] = decision
        processed += 1

    # ── Persist ───────────────────────────────────────────────── #
    decisions.save(review_dir)

    # ── Summary ───────────────────────────────────────────────── #
    have_chosen     = sum(1 for d in decisions.shots.values() if d.chosen)
    no_candidates   = sum(1 for d in decisions.shots.values()
                          if is_image_shot(d.visual)
                          and not d.candidates and not d.covered)
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
    print(f"Shots in dossier:       {len(decisions.shots)} of {len(shots)} "
          f"(complete storyboard)")
    print(f"Image shots processed:  {processed}")
    print(f"Typography shots:       {n_typography}  (card previews, "
          f"no image needed)")
    print(f"  with a chosen winner: {have_chosen}")
    print(f"  with no candidates:   {no_candidates}")
    if skipped_covered:
        print(f"  covered (no web fetch): {skipped_covered}  "
              f"(bank/pool — use --fetch-covered to capture alternates)")
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