"""
Phase 3 sources — orchestrator.

Top-level API
-------------
fetch_for_shot(query, shot_index, ...) → FetchResult

The orchestrator runs a priority waterfall:

  1. User upload by shot index / manifest
  2. Phase 1a book extract (vision-scored against the query)
  3. Cached web result for this query
  4. Live web fetch (LoC → Wikimedia → Internet Archive → Pexels),
     vision-scored, top-ranked candidate kept
  5. Returns FetchResult with .best = None → renderer uses placeholder

Each layer is optional.  When `anthropic_api_key` is empty, vision
scoring is skipped (candidates are kept in source priority order).
When `cache_dir` is None, no caching.  When `user_dir` is None or
`book_extracts` is None, those layers are skipped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .base import FetchResult, ImageCandidate, Source, ext_from_url
from .book_extract import BookExtractSource
from .cache import ImageCache
from .internet_archive import InternetArchive
from .loc import LibraryOfCongress
from .pexels import Pexels
from .user_upload import UserUploadSource
from .vision import VisionScorer, passes_threshold, rank_candidates
from .wikimedia import WikimediaCommons
from .wikipedia import WikipediaLeadImage

log = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────── #

@dataclass
class FetcherConfig:
    """All configuration needed to instantiate the fetcher."""
    anthropic_api_key: str = ""
    pexels_api_key: str = ""
    cache_dir: Path | None = None
    user_dir: Path | None = None
    book_extracts: Path | None = None   # Phase 1a photos.zip or directory
    book_title: str = ""
    character_name: str = ""

    # Per-shot fetching params
    n_candidates_per_source: int = 3
    enable_vision: bool | None = None   # None = enable iff API key provided

    # Pinned character portrait — when set, every `portrait` shot reuses this
    # one image (the single biggest documentary-quality win) without needing a
    # full review dossier.  A dossier override/pin still takes precedence.
    pinned_portrait: Path | None = None

    # Pre-render review dossier (see phase3.sources.decisions).  When set,
    # the fetcher consults the dossier before doing any source query and
    # returns the user-approved image when available.
    review_dir: Path | None = None

    # Offline mode (render-only route): never query the live web sources.
    # Only the dossier (override → user-marked → pinned/pool → chosen_file),
    # user_dir and book extracts are used; uncovered shots get a placeholder.
    offline: bool = False

    # Adjacent near-duplicate avoidance (P1.1).  When two consecutive image
    # shots would resolve to the same (or a perceptually identical) picture,
    # the later shot swaps to its next-ranked candidate so the perceived cut
    # actually happens.  Applies ONLY to automatic picks (dossier chosen_file
    # from the waterfall, live fetch) — never to explicit user choices
    # (override, user-marked, pool, pin, photo-bank assignment).
    dedupe_adjacent: bool = True
    dedupe_threshold: int = 8      # Hamming bits on a 64-bit dHash

    @property
    def vision_enabled(self) -> bool:
        if self.enable_vision is False:
            return False
        return bool(self.anthropic_api_key)


# ── Orchestrator ──────────────────────────────────────────────────── #

@dataclass
class Fetcher:
    """Stateful image-fetching orchestrator."""
    config: FetcherConfig

    def __post_init__(self):
        self.cache = ImageCache(self.config.cache_dir) if self.config.cache_dir else None
        self.user_source = UserUploadSource(self.config.user_dir)
        self.book_source = BookExtractSource(self.config.book_extracts)
        # Library of Congress and Internet Archive were dropped from the
        # waterfall: LoC returned 0 candidates for essentially every query and
        # IA's download URLs 404'd in practice. Re-add the classes here if
        # their query strategy is ever fixed (see phase3/PHASE3.md §7.3).
        # Wikipedia lead images sit between Commons (richer sets when it
        # hits) and Pexels (modern stock — last resort for historical work).
        self.web_sources: list[Source] = [] if self.config.offline else [
            WikimediaCommons(),
            WikipediaLeadImage(),
            Pexels(self.config.pexels_api_key),
        ]
        self.scorer = (
            VisionScorer(self.config.anthropic_api_key)
            if self.config.vision_enabled else None
        )
        # dHash of the previous image shot's resolved asset (P1.1).
        # fetch_for_shot is called in shot order by both the renderer and
        # prebuild, so "previous call" == "previous image shot on screen".
        self._prev_asset_hash: int | None = None
        # Optional pre-render dossier — loaded lazily so missing/invalid
        # files don't crash the renderer.
        self.decisions = None
        if self.config.review_dir is not None:
            try:
                from .decisions import Decisions
                self.decisions = Decisions.load(self.config.review_dir)
            except FileNotFoundError as exc:
                log.warning(
                    "review_dir=%s but no decisions.json found — "
                    "ignoring (%s)",
                    self.config.review_dir, exc,
                )
            except Exception as exc:
                log.warning(
                    "Failed to load decisions.json from %s: %s — "
                    "rendering without dossier",
                    self.config.review_dir, exc,
                )

    # ── Required-images manifest ────────────────────────────────── #

    def build_manifest(self, shots: list, out_path: Path) -> None:
        """
        Write a text manifest listing every image-kind shot so the
        user can review and optionally provide images.

        Format:
          shot_05  portrait      "Jafar al-Askari Iraqi general 1920s"
          shot_08  archive       "Ottoman Empire 1918"
          ...
        """
        from ..render import TYPOGRAPHY_VISUALS  # avoid circular

        lines = []
        lines.append("# Required images for Phase 3 video")
        lines.append("# Drop matching files into your --user-dir to override.")
        lines.append("# Filename pattern: shot_NN.jpg (NN = shot number, 01-indexed)")
        lines.append("# OR use a manifest.json in --user-dir.")
        lines.append("")
        lines.append(f"{'Shot':<10}{'Visual':<14}{'Duration':<10}{'Query':<60}")
        lines.append("─" * 95)

        for i, shot in enumerate(shots, start=1):
            if shot.visual in TYPOGRAPHY_VISUALS:
                continue
            lines.append(
                f"shot_{i:02d}    {shot.visual:<14}"
                f"{shot.duration:>5.1f}s    "
                f'"{shot.search_query}"'
            )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("Manifest written → %s", out_path)

    # ── Adjacent near-duplicate avoidance (P1.1) ─────────────────── #

    def _remember_asset(self, path: Path | None) -> None:
        """Record the image that just went on screen so the NEXT image
        shot can avoid duplicating it.  None (no image / placeholder)
        clears the memory — a placeholder between two identical photos
        already breaks the visual repetition."""
        from .dedupe import dhash
        self._prev_asset_hash = dhash(path) if path is not None else None

    def _dedupe_pick(self, path: Path, alternates: list[Path],
                     shot_index: int) -> Path:
        """Return `path`, or the first ranked alternate that is not a
        near-duplicate of the previous shot's image.  Keeps the duplicate
        (with a log line) when no alternate qualifies — honest degradation,
        a repeat beats a placeholder."""
        from .dedupe import dhash, near_duplicate
        if self._prev_asset_hash is None:
            return path
        thr = self.config.dedupe_threshold
        if not near_duplicate(dhash(path), self._prev_asset_hash, thr):
            return path
        for alt in alternates:
            if not near_duplicate(dhash(alt), self._prev_asset_hash, thr):
                log.info(
                    "Shot %d: %s duplicates the previous shot's image — "
                    "swapped to next-ranked candidate %s",
                    shot_index, path.name, alt.name)
                return alt
        log.info(
            "Shot %d: %s duplicates the previous shot's image and no "
            "alternate candidate exists — keeping the duplicate",
            shot_index, path.name)
        return path

    # ── Main entry point ───────────────────────────────────────── #

    def fetch_for_shot(self, query: str, shot_index: int,
                       visual_type: str | None = None) -> FetchResult:
        """
        Resolve an image for one shot.  Returns FetchResult; the
        caller uses result.best.local_path (or falls back to placeholder
        if result.has_image is False).

        `visual_type` (optional): the shot's visual type ("portrait",
        "archive", etc).  Tightens the score threshold for portrait
        shots — see vision.passes_threshold.
        """

        # 0. Review dossier — if the user pre-approved an image (or
        # dropped an override) for this shot, use it and skip every
        # source query.
        if self.decisions is not None:
            resolved = self.decisions.resolve_detailed(
                shot_index=shot_index,
                review_dir=self.config.review_dir,
            )
            if resolved.path is not None:
                path = resolved.path
                # Adjacent-duplicate avoidance — only for the waterfall's
                # automatic pick.  Explicit user choices (override, user-
                # marked, pool, pin) and Sonnet photo-bank assignments are
                # deliberate repetition-or-not decisions; respect them.
                if (self.config.dedupe_adjacent
                        and resolved.kind == "chosen_file"
                        and resolved.chosen_source != "photo_bank"):
                    path = self._dedupe_pick(path, resolved.alternates,
                                             shot_index)
                cand = ImageCandidate(
                    url=f"file://{path}",
                    title=f"review-dossier:{path.name}",
                    source="review_dossier",
                    license_short="user-supplied",
                )
                cand.local_path = path
                self._remember_asset(path)
                log.info("Shot %d: review-dossier hit %s",
                         shot_index, path.name)
                return FetchResult(query=query, candidates=[cand], best=cand)

        # 0.5 Pinned character portrait — set directly on FetcherConfig
        # (e.g. from generate_video_v2 / the Streamlit sidebar).  Applies only
        # to portrait shots ABOUT the main character (so it never lands on a
        # portrait of someone else); a dossier hit above still wins.
        from .decisions import subject_is_character
        if (visual_type == "portrait"
                and self.config.pinned_portrait
                and Path(self.config.pinned_portrait).exists()
                and subject_is_character(query, self.config.character_name)):
            p = Path(self.config.pinned_portrait).resolve()
            cand = ImageCandidate(
                url=f"file://{p}",
                title=f"pinned-portrait:{p.name}",
                source="pinned_portrait",
                license_short="user-supplied",
            )
            cand.local_path = p
            self._remember_asset(p)
            log.info("Shot %d: pinned character portrait %s", shot_index, p.name)
            return FetchResult(query=query, candidates=[cand], best=cand)

        # 1. User upload — highest priority among non-dossier paths
        user_cand = self.user_source.lookup_for_shot(shot_index, query)
        if user_cand:
            self._remember_asset(user_cand.local_path)
            log.info("Shot %d: user-supplied image %s",
                     shot_index, user_cand.title)
            return FetchResult(query=query, candidates=[user_cand], best=user_cand)

        # 2. Book extract — second priority, vision-scored against query
        book_cands = self.book_source.all_candidates(query)
        if book_cands and self.scorer:
            for c in book_cands:
                self.scorer.score(c,
                                  book_title=self.config.book_title,
                                  character_name=self.config.character_name,
                                  query=query)
            kept = [c for c in book_cands if passes_threshold(c, visual_type)]
            if kept:
                best = rank_candidates(kept)[0]
                self._remember_asset(best.local_path)
                log.info("Shot %d: book-extracted photo (score %d/9)",
                         shot_index, best.total_score)
                return FetchResult(query=query, candidates=book_cands, best=best)
        elif book_cands and not self.scorer:
            # Book extracts provided but no vision scoring — can't pick
            # the right one for this shot.  Warn once, then skip.
            if not getattr(self, "_book_warned", False):
                log.warning(
                    "Book extracts provided (%d photos) but vision scoring "
                    "is disabled.  Cannot match book photos to specific "
                    "shots without vision — set --anthropic-key or remove "
                    "--no-vision to use them.  Falling back to web sources.",
                    len(book_cands)
                )
                self._book_warned = True

        # 3. Cached web result.  Same query on adjacent shots returns the
        # same cached winner — exactly the repetition P1.1 exists to break,
        # so the cached best goes through the dedupe walk too.
        if self.cache:
            cached = self.cache.get(query)
            if cached and cached.has_image:
                log.info("Shot %d: cache hit", shot_index)
                if self.config.dedupe_adjacent:
                    ranked = rank_candidates(
                        [c for c in cached.candidates
                         if c.local_path and Path(c.local_path).exists()])
                    if ranked:
                        alt_paths = [Path(c.local_path) for c in ranked
                                     if c.url != cached.best.url]
                        picked = self._dedupe_pick(
                            Path(cached.best.local_path), alt_paths,
                            shot_index)
                        if picked != Path(cached.best.local_path):
                            new_best = next(
                                c for c in ranked
                                if Path(c.local_path) == picked)
                            cached = FetchResult(
                                query=cached.query,
                                candidates=cached.candidates,
                                best=new_best)
                self._remember_asset(
                    Path(cached.best.local_path) if cached.best else None)
                return cached

        # 4. Live web fetch
        result = self._fetch_live(query, shot_index, visual_type)

        # Store to cache for next time
        if self.cache:
            self.cache.put(result)

        return result

    # ── Live web fetch with vision scoring ──────────────────────── #

    def _fetch_live(self, query: str, shot_index: int,
                    visual_type: str | None = None) -> FetchResult:
        all_candidates: list[ImageCandidate] = []
        n = self.config.n_candidates_per_source

        for src in self.web_sources:
            try:
                cands = src.search(query, n=n)
            except Exception as exc:
                log.warning("Source %s raised: %s", src.name, exc)
                cands = []
            all_candidates.extend(cands)

        if not all_candidates:
            log.warning("Shot %d: no candidates from any web source for %r",
                        shot_index, query)
            self._remember_asset(None)   # placeholder breaks visual adjacency
            return FetchResult(query=query, candidates=[], best=None)

        # Download each candidate so we can vision-score it.
        # Each download is guarded — one corrupt URL doesn't kill the run.
        if self.cache:
            for i, c in enumerate(all_candidates):
                if c.local_path:
                    continue
                try:
                    dest = self.cache.candidate_path(
                        query, i, ext=ext_from_url(c.url))
                    src = self._source_by_name(c.source)
                    if src:
                        src.download(c, dest)
                except Exception as exc:
                    log.warning("Download failed for candidate %d of %r: %s",
                                i, query, exc)
        else:
            # No cache — use a temp dir
            import tempfile
            tmp_dir = Path(tempfile.mkdtemp(prefix="lamahat_fetch_"))
            for i, c in enumerate(all_candidates):
                try:
                    dest = tmp_dir / f"cand_{i:02d}{ext_from_url(c.url)}"
                    src = self._source_by_name(c.source)
                    if src:
                        src.download(c, dest)
                except Exception as exc:
                    log.warning("Download failed for candidate %d of %r: %s",
                                i, query, exc)

        # Drop candidates that failed to download
        downloaded = [c for c in all_candidates if c.local_path]
        if not downloaded:
            log.warning("Shot %d: all downloads failed for %r",
                        shot_index, query)
            self._remember_asset(None)
            return FetchResult(query=query, candidates=all_candidates, best=None)

        # Vision-score (also guarded — score() is fail-open internally
        # but extra belt-and-braces here)
        if self.scorer:
            for c in downloaded:
                try:
                    self.scorer.score(
                        c,
                        book_title=self.config.book_title,
                        character_name=self.config.character_name,
                        query=query,
                    )
                except Exception as exc:
                    log.warning("Scoring failed for %s: %s", c.title[:40], exc)
                    # Apply neutral score so the candidate stays in the pool
                    c.score_subject = 2
                    c.score_quality = 2
                    c.score_cinematic = 1
                    c.score_era = 2
                    c.vision_reason = f"[error] {exc!s}"[:200]
            kept = [c for c in downloaded if passes_threshold(c, visual_type)]
            if not kept:
                log.warning("Shot %d: all %d candidates failed threshold",
                            shot_index, len(downloaded))
                self._remember_asset(None)
                return FetchResult(query=query, candidates=downloaded, best=None)
            ranked = rank_candidates(kept)
        else:
            # No vision — keep source-priority order
            ranked = downloaded

        best = ranked[0]
        if self.config.dedupe_adjacent and len(ranked) > 1:
            alt_paths = [Path(c.local_path) for c in ranked[1:] if c.local_path]
            picked = self._dedupe_pick(Path(best.local_path), alt_paths,
                                       shot_index)
            if picked != Path(best.local_path):
                best = next(c for c in ranked if Path(c.local_path) == picked)
        self._remember_asset(Path(best.local_path) if best.local_path else None)
        log.info("Shot %d: best=%s (score %s)",
                 shot_index, best.title[:50],
                 best.total_score if best.is_scored else "n/a")
        return FetchResult(query=query, candidates=downloaded, best=best)

    def _source_by_name(self, name: str) -> Source | None:
        for src in self.web_sources:
            if src.name == name:
                return src
        return None


# ── Convenience export ─────────────────────────────────────────── #

def fetch_for_shot(query: str, shot_index: int, config: FetcherConfig,
                   visual_type: str | None = None) -> FetchResult:
    """One-shot wrapper.  Build a Fetcher and run one query."""
    return Fetcher(config).fetch_for_shot(query, shot_index, visual_type)


__all__ = [
    "Fetcher", "FetcherConfig", "FetchResult", "ImageCandidate",
    "fetch_for_shot",
]