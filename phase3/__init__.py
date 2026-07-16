"""
Phase 3 — Visual Generation (v2, shot-based).

The original section-based pipeline (`generate_background_video`) has been
fully replaced by the **shot-based** architecture.  A *shot plan* is now the
source of truth: a list of timestamped `Shot` dataclasses produced by one
Claude Sonnet call.  The renderer executes the plan without making creative
choices, so plans are inspectable, diff-able and regeneratable.

Pipeline
--------
    Script + Audio ──► align()          ──► word_timings
                       build_shot_plan() ──► list[Shot]   (one Sonnet call)
                       Fetcher           ──► per-shot imagery (LoC → Wikimedia
                                             → IA → Pexels, vision-scored)
                       render_video()    ──► MP4

Two entry points cover everything:

* `generate_video_v2()` — high-level orchestrator (align → plan → render) used
  by the Streamlit UI and by `phase3_run.py`.
* The CLIs at the repo root — `phase3_run.py` (plan), `prebuild_assets.py`
  (review dossier), `condition_assets.py` (asset conditioning) and
  `render_plan.py` (render a saved plan) — for the inspectable, multi-step
  Colab/CLI workflow.

See `phase3/PHASE3.md` for the deep architecture reference.
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

# Files the user drops in to override a shot's image (kept by slimming).
_USER_MARK_RE = re.compile(r"^(my|user)[_-].+\.(jpg|jpeg|png|webp)$", re.IGNORECASE)

from .align import align
from .parser import parse_sections
from .plan import (
    Shot,
    build_shot_plan,
    load_plan,
    save_plan,
    summarise_plan,
)
from .render import RenderConfig, render_video
from .sources import Fetcher, FetcherConfig

log = logging.getLogger(__name__)

__all__ = [
    "generate_video_v2",
    "build_total_solution",
    "render_from_review",
    "condition_review_dir",
    "slim_review_dir",
    "zip_review_dir",
    "extract_thumbnail",
    "probe_audio_duration",
    "Shot",
    "RenderConfig",
    "render_video",
    "build_shot_plan",
    "align",
    "parse_sections",
    "Fetcher",
    "FetcherConfig",
    "load_plan",
    "save_plan",
    "summarise_plan",
]

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Default on-disk image cache (shared with the render_plan.py CLI default).
_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "lamahat" / "images"


# ── Small media helpers (replace the v1 compositor/effects utilities) ─────── #

def probe_audio_duration(audio_path: Path) -> float:
    """Total duration of a media file in seconds via ffprobe (0.0 on failure)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(audio_path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except Exception as exc:  # noqa: BLE001 — ffprobe / parse failures are non-fatal
        log.warning("ffprobe duration failed for %s: %s", audio_path, exc)
        return 0.0


def extract_thumbnail(video_path: Path, out_path: Path, *, time: float = 5.0) -> Path:
    """Grab a single JPEG frame from `video_path` at `time` seconds."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{time:.2f}", "-i", str(video_path),
         "-frames:v", "1", "-q:v", "3", str(out_path)],
        capture_output=True, check=True,
    )
    return out_path


def _resolve_duration(
    audio_path: Path | None,
    audio_duration_sec: float | None,
    script_text: str,
) -> float:
    """Total narration duration: explicit override > ffprobe > char-rate estimate."""
    if audio_duration_sec:
        return audio_duration_sec
    if audio_path and Path(audio_path).exists():
        dur = probe_audio_duration(Path(audio_path))
        if dur > 0:
            return dur
    # Fallback: ~12 chars/sec for Arabic TTS, clamped to 1–6 min.
    n_chars = len(script_text.strip())
    return min(360.0, max(60.0, n_chars / 12.0))


# ── High-level orchestrator (align → plan → render) ───────────────────────── #

def generate_video_v2(
    script_text: str,
    output_path: Path,
    *,
    script_path: Path | None = None,
    audio_path: Path | None = None,
    audio_bytes: bytes | None = None,
    audio_duration_sec: float | None = None,
    anthropic_api_key: str = "",
    pexels_api_key: str = "",
    book_title: str = "",
    character_name: str = "",
    genre: str = "history",
    width: int = 1920,
    height: int = 1080,
    fps: int = 25,
    grade: str | None = "warm",
    typography_family: str = "A",
    add_captions: bool = True,
    align_backend: str = "auto",
    book_cover: Path | None = None,
    book_cover_fit: str = "contain",
    book_cover_align: str = "center",
    character_portrait: Path | None = None,
    music_path: Path | None = None,
    music_gain_db: float = -12.0,
    music_duck: bool = True,
    fades: bool = True,
    caption_backplate: str = "subtle",
    text_scrim: str | None = None,
    title_scale: float = 1.0,
    title_color: tuple[int, int, int] | None = None,
    caption_size: float = 1.0,
    caption_color: str | None = None,
    caption_pos: float | None = None,
    typography_over_image: bool = False,
    book_extracts: Path | None = None,
    user_dir: Path | None = None,
    review_dir: Path | None = None,
    enable_vision: bool = True,
    use_cache: bool = True,
    on_progress: Callable[[str, float], None] | None = None,
) -> Path:
    """
    Produce a finished MP4 from an Arabic script using the shot-based pipeline.

    This chains the three v2 stages in one call:

      1. `align()`           — word-level timings (WhisperX | Whisper |
                               interpolation, per `align_backend`).
      2. `build_shot_plan()` — one Claude Sonnet call → a validated shot plan
                               (requires `anthropic_api_key`).
      3. `render_video()`    — fetches imagery (LoC → Wikimedia → IA → Pexels,
                               vision-scored), renders typography cards, burns
                               captions and muxes audio.

    Parameters mirror the CLI flags of `phase3_run.py` / `render_plan.py`.
    `audio_bytes` (e.g. straight from Phase 2 TTS) is written to a temp file
    when `audio_path` is not given.  Returns `output_path`.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _prog(label: str, frac: float) -> None:
        log.info("[P3 %.0f%%] %s", frac * 100, label)
        if on_progress:
            on_progress(label, frac)

    if not anthropic_api_key:
        raise ValueError(
            "generate_video_v2 requires an Anthropic API key for the shot planner."
        )

    with tempfile.TemporaryDirectory(prefix="bk2v_p3v2_") as _tmp:
        tmp = Path(_tmp)

        # Materialise audio bytes to a file so align/render can read it.
        if audio_path is None and audio_bytes:
            audio_path = tmp / "narration.mp3"
            audio_path.write_bytes(audio_bytes)
        audio_path = Path(audio_path) if audio_path else None

        # ── Stage 0: parse + resolve duration ─────────────────────────── #
        _prog("Parsing script sections…", 0.02)
        sections = parse_sections(script_text, script_path=script_path)
        if not sections:
            raise ValueError("No recognisable sections found in script text.")
        total_dur = _resolve_duration(audio_path, audio_duration_sec, script_text)
        log.info("Parsed %d sections; total duration %.1f s",
                 len(sections), total_dur)

        # ── Stage 1: forced alignment ─────────────────────────────────── #
        _prog("Aligning narration to words…", 0.06)
        timings = align(
            script_text, audio_path, total_dur, prefer_backend=align_backend,
        )
        backend = timings[0].source if timings else "n/a"
        log.info("Aligned %d words via %s", len(timings), backend)

        # ── Stage 2: AI shot planner (one Sonnet call) ────────────────── #
        _prog("Planning shots (Claude Sonnet)…", 0.15)
        shots = build_shot_plan(
            sections=sections,
            word_timings=timings,
            book_title=book_title,
            character_name=character_name,
            genre=genre,
            total_duration_sec=total_dur,
            anthropic_api_key=anthropic_api_key,
            debug_dir=output_path.parent,
        )
        log.info("Planned %d shots", len(shots))

        # ── Stage 3: render ───────────────────────────────────────────── #
        fetcher = Fetcher(FetcherConfig(
            anthropic_api_key=anthropic_api_key,
            pexels_api_key=pexels_api_key,
            cache_dir=_DEFAULT_CACHE_DIR if use_cache else None,
            user_dir=Path(user_dir) if user_dir else None,
            book_extracts=Path(book_extracts) if book_extracts else None,
            book_title=book_title,
            character_name=character_name,
            enable_vision=enable_vision,
            review_dir=Path(review_dir) if review_dir else None,
            pinned_portrait=Path(character_portrait) if character_portrait else None,
        ))
        cfg = RenderConfig(
            width=width,
            height=height,
            fps=fps,
            add_captions=add_captions,
            fetcher=fetcher,
            book_cover=Path(book_cover) if book_cover else None,
            book_cover_fit=book_cover_fit,
            book_cover_align=book_cover_align,
            typography_family=typography_family,
            grade=grade,
            caption_backplate=caption_backplate,
            text_scrim=text_scrim,
            title_scale=title_scale,
            title_color=title_color,
            caption_size=caption_size,
            caption_color=caption_color,
            caption_pos=caption_pos,
            typography_over_image=typography_over_image,
            music_path=Path(music_path) if music_path else None,
            music_gain_db=music_gain_db,
            music_duck=music_duck,
            fades=fades,
        )

        def _render_prog(label: str, frac: float) -> None:
            # Map the renderer's 0–1 onto the back 65% of the overall bar.
            _prog(label, 0.35 + 0.65 * frac)

        render_video(
            shots, output_path,
            audio_path=audio_path,
            audio_duration_sec=total_dur,
            config=cfg,
            on_progress=_render_prog,
        )

    _prog("Final video complete ✓", 1.0)
    return output_path


# ── Two routes: total solution (with saved review dossier) & render-only ──── #
#
# These mirror the two notebooks:
#   • build_total_solution() = align → plan → prebuild (capture every candidate
#     into a review dossier) → condition → render.  Costs API; saves the dossier
#     so the user can later refine at no further API cost.
#   • render_from_review()   = load a (revised) review dossier → condition →
#     render.  No planner / fetcher API cost — uses the dossier's chosen images.

def condition_review_dir(
    review_dir: Path,
    *,
    sr: str = "none",
    tone: str = "documentary",
    dry_run: bool = False,
    target_cover: int = 2560,
    contain_floor: int = 1600,
    max_cap: int = 3200,
    min_usable: int = 600,
    mismatch: float = 0.28,
    quality: int = 95,
    crop_cover: str = "smart",
    mark: str = "*",
    keep_originals: bool = False,
    on_progress: Callable[[str, float], None] | None = None,
) -> dict:
    """Normalise captured assets to crisp, aspect-correct sizes before render.

    Thin wrapper over ``condition_assets.run`` (repo-root module) using its CLI
    defaults.  Returns the per-file conditioning report; a no-op-safe call when
    there is nothing to condition.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import condition_assets  # repo-root module
    return condition_assets.run(
        Path(review_dir),
        mismatch=mismatch, target_cover=target_cover, contain_floor=contain_floor,
        max_cap=max_cap, min_usable=min_usable, sr=sr, quality=quality,
        dry_run=dry_run, tone=tone, crop_cover=crop_cover,
        mark=mark, keep_originals=keep_originals,
        on_progress=on_progress,
    )


def slim_review_dir(review_dir: Path, *, mode: str = "chosen",
                    keep_n: int = 3) -> dict:
    """Trim a review dossier in place so its zip stays small enough to upload.

    Per image shot it keeps the images that actually render — the resolved
    ``chosen_file``, any ``override``, and any user-dropped ``my_``/``user_``
    files — and deletes the rest of that shot's downloaded candidates.

      mode="chosen": keep only the above (smallest; conditioned/sharpened copy
                     wins, original candidates are dropped).
      mode="top":    additionally keep the top ``keep_n`` candidates by vision
                     score, so a few alternatives remain to swap to.
      mode="all":    no-op.

    Portrait shots stay tiny regardless — their image comes from the committed
    character pool (``resources/character/``), not the dossier.  Returns a
    summary dict (``removed``, ``kept``, ``bytes_removed``).
    """
    review_dir = Path(review_dir)
    out = {"removed": 0, "kept": 0, "bytes_removed": 0}
    if mode == "all":
        return out
    dpath = review_dir / "decisions.json"
    if not dpath.exists():
        return out
    data = _json.loads(dpath.read_text(encoding="utf-8"))
    shots = data.get("shots", {})
    if isinstance(shots, list):
        shots = {str(i): s for i, s in enumerate(shots)}
    _exts = (".jpg", ".jpeg", ".png", ".webp")

    for shot in shots.values():
        cands = shot.get("candidates", []) or []
        keep_rel: set[str] = set()
        for f in (shot.get("chosen_file"), shot.get("override")):
            if f:
                keep_rel.add(str(f).replace("\\", "/"))
        if mode == "top":
            for c in sorted(cands, key=lambda c: c.get("score", -1),
                            reverse=True)[:max(1, keep_n)]:
                if c.get("file"):
                    keep_rel.add(str(c["file"]).replace("\\", "/"))

        # Every shot folder this shot touches (candidates + chosen).
        folders: set[Path] = set()
        for c in cands:
            if c.get("file"):
                folders.add((review_dir / c["file"]).parent)
        if shot.get("chosen_file"):
            folders.add((review_dir / shot["chosen_file"]).parent)

        for folder in folders:
            if not folder.is_dir() or folder.resolve() == review_dir.resolve():
                continue
            for img in folder.iterdir():
                if not (img.is_file() and img.suffix.lower() in _exts):
                    continue
                rel = str(img.relative_to(review_dir)).replace("\\", "/")
                if rel in keep_rel or _USER_MARK_RE.match(img.name):
                    out["kept"] += 1
                    continue
                try:
                    out["bytes_removed"] += img.stat().st_size
                    img.unlink()
                    out["removed"] += 1
                except OSError:
                    pass
    log.info("slim_review_dir(%s): kept %d, removed %d (%.1f MB freed)",
             mode, out["kept"], out["removed"], out["bytes_removed"] / 1e6)
    return out


def zip_review_dir(review_dir: Path, out_zip: Path) -> Path:
    """Zip a review dossier (candidates + decisions.json + plan + narration) so
    it can be downloaded and later fed back to the render-only route."""
    review_dir = Path(review_dir)
    out_zip = Path(out_zip)
    base = out_zip.with_suffix("") if out_zip.suffix == ".zip" else out_zip
    archive = shutil.make_archive(str(base), "zip", root_dir=str(review_dir))
    return Path(archive)


def _run_prebuild(
    plan_path: Path,
    review_dir: Path,
    *,
    script_path: Path | None,
    audio_path: Path | None,
    book_title: str,
    character_name: str,
    anthropic_api_key: str,
    pexels_api_key: str,
    character_portrait: Path | None,
    book_cover: Path | None,
    book_cover_fit: str,
    book_cover_align: str,
    enable_vision: bool,
    book_extracts: Path | None,
    photo_bank: Path | None = None,
) -> None:
    """Build the review dossier by driving the tested prebuild_assets.py CLI."""
    cmd = [
        sys.executable, str(_REPO_ROOT / "prebuild_assets.py"),
        "--plan", str(plan_path),
        "--review-dir", str(review_dir),
        "--book-title", book_title,
        "--character-name", character_name,
        "--book-cover-fit", book_cover_fit,
        "--book-cover-align", book_cover_align,
    ]
    if script_path:
        cmd += ["--script", str(script_path)]
    if anthropic_api_key:
        cmd += ["--anthropic-key", anthropic_api_key]
    if pexels_api_key:
        cmd += ["--pexels-key", pexels_api_key]
    if character_portrait:
        cmd += ["--character-portrait", str(character_portrait)]
    if book_cover:
        cmd += ["--book-cover", str(book_cover)]
    if book_extracts:
        cmd += ["--book-extracts", str(book_extracts)]
    if photo_bank:
        cmd += ["--photo-bank", str(photo_bank)]
    if not enable_vision:
        cmd += ["--no-vision"]
    log.info("prebuild: %s", " ".join(cmd[2:]))
    subprocess.run(cmd, check=True, cwd=str(_REPO_ROOT))


def render_from_review(
    review_dir: Path,
    output_path: Path,
    *,
    audio_path: Path | None = None,
    audio_bytes: bytes | None = None,
    audio_duration_sec: float | None = None,
    config: "RenderConfig | None" = None,
    anthropic_api_key: str = "",
    pexels_api_key: str = "",
    book_title: str = "",
    character_name: str = "",
    enable_vision: bool = False,
    condition: bool = True,
    sr: str = "none",
    plan_path: Path | None = None,
    offline: bool = True,
    on_progress: Callable[[str, float], None] | None = None,
) -> Path:
    """Render-only route: render a (revised) review dossier with no planner/
    fetcher API cost.  The dossier supplies each shot's image (override →
    user-marked → pinned portrait → chosen_file).  With `offline=True`
    (default) the live web sources are disabled entirely, so only the
    dossier's images are used and any uncovered shot gets a placeholder card —
    matching the route's promise to "render only the chosen candidates".  Set
    `offline=False` to let uncovered shots fall through to the live waterfall.

    `config` may be any RenderConfig (its `.fetcher` is overwritten to point at
    the dossier).  The shot plan and narration are read from inside the dossier
    (`shot_plan.json` / `narration.mp3`) unless given explicitly.
    """
    review_dir = Path(review_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _prog(label: str, frac: float) -> None:
        if on_progress:
            on_progress(label, frac)

    # Plan: explicit > review_dir/shot_plan.json
    if plan_path is None:
        plan_path = review_dir / "shot_plan.json"
    if not Path(plan_path).exists():
        raise FileNotFoundError(
            f"No shot plan found for render-only route (looked for {plan_path}). "
            "The review zip must contain shot_plan.json."
        )
    shots = load_plan(plan_path)

    # Audio: explicit > bytes > review_dir/narration.mp3
    if audio_path is None and audio_bytes:
        audio_path = review_dir / "narration.mp3"
        Path(audio_path).write_bytes(audio_bytes)
    if audio_path is None:
        cand = review_dir / "narration.mp3"
        audio_path = cand if cand.exists() else None
    audio_path = Path(audio_path) if audio_path else None

    if condition:
        _prog("Conditioning assets…", 0.01)
        try:
            # Map conditioning onto the first 8% of the bar so it never looks
            # frozen during the (CPU-heavy) Lanczos upscales.
            condition_review_dir(
                review_dir, sr=sr,
                on_progress=lambda lbl, f: _prog(lbl, 0.01 + 0.07 * f),
            )
        except Exception as exc:  # noqa: BLE001 — conditioning is best-effort
            log.warning("Asset conditioning skipped: %s", exc)

    cfg = config or RenderConfig()
    cfg.fetcher = Fetcher(FetcherConfig(
        anthropic_api_key=anthropic_api_key,
        pexels_api_key=pexels_api_key,
        book_title=book_title,
        character_name=character_name,
        enable_vision=enable_vision,
        review_dir=review_dir,
        offline=offline,
    ))

    def _render_prog(label: str, frac: float) -> None:
        _prog(label, 0.08 + 0.92 * frac)

    render_video(
        shots, output_path,
        audio_path=audio_path,
        audio_duration_sec=audio_duration_sec,
        config=cfg,
        on_progress=_render_prog,
    )
    _prog("Final video complete ✓", 1.0)
    return output_path


def build_total_solution(
    script_text: str,
    output_path: Path,
    review_dir: Path,
    *,
    script_path: Path | None = None,
    audio_path: Path | None = None,
    audio_bytes: bytes | None = None,
    audio_duration_sec: float | None = None,
    anthropic_api_key: str,
    pexels_api_key: str = "",
    book_title: str = "",
    character_name: str = "",
    genre: str = "history",
    align_backend: str = "auto",
    word_timings_path: Path | None = None,
    character_portrait: Path | None = None,
    book_cover: Path | None = None,
    book_cover_fit: str = "contain",
    book_cover_align: str = "center",
    book_extracts: Path | None = None,
    photo_bank: Path | None = None,
    enable_vision: bool = True,
    config: "RenderConfig | None" = None,
    condition: bool = True,
    sr: str = "none",
    # "top" (not "chosen") is the default: --backdrop-rotate and the
    # adjacent-dedupe swap both need ranked ALTERNATES in the dossier on
    # a later render-only pass; a chosen-only dossier silently disables
    # them (P6.3).
    slim_mode: str = "top",
    slim_keep_n: int = 3,
    on_progress: Callable[[str, float], None] | None = None,
) -> dict:
    """Total-solution route: align → plan → prebuild (capture every candidate
    into a review dossier) → condition → render.

    Leaves a self-contained dossier in `review_dir` (candidates, decisions.json,
    shot_plan.json, word_timings.json, narration.mp3) that `zip_review_dir` can
    package for later no-API-cost refinement via `render_from_review`.

    Returns ``{"video": Path, "review_dir": Path, "plan": Path}``.
    """
    review_dir = Path(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(output_path)

    def _prog(label: str, frac: float) -> None:
        log.info("[total %.0f%%] %s", frac * 100, label)
        if on_progress:
            on_progress(label, frac)

    if not anthropic_api_key:
        raise ValueError("build_total_solution requires an Anthropic API key.")

    # Narration: keep a copy inside the dossier so the zip is self-contained.
    if audio_path is None and audio_bytes:
        audio_path = review_dir / "narration.mp3"
        Path(audio_path).write_bytes(audio_bytes)
    elif audio_path is not None:
        try:
            shutil.copy2(audio_path, review_dir / "narration.mp3")
            audio_path = review_dir / "narration.mp3"
        except Exception:  # noqa: BLE001
            audio_path = Path(audio_path)
    audio_path = Path(audio_path) if audio_path else None

    total_dur = _resolve_duration(audio_path, audio_duration_sec, script_text)

    # Stage 1 — plan (align + Sonnet) and persist into the dossier.
    _prog("Parsing + aligning…", 0.03)
    sections = parse_sections(script_text, script_path=script_path)
    if not sections:
        raise ValueError("No recognisable sections found in script text.")
    if word_timings_path:
        # Precomputed (e.g. WhisperX in Colab) — skip on-Cloud ASR entirely.
        from .align import load_word_timings
        timings = load_word_timings(word_timings_path)
        log.info("Loaded %d precomputed word timings from %s",
                 len(timings), word_timings_path)
    else:
        timings = align(script_text, audio_path, total_dur,
                        prefer_backend=align_backend)

    _prog("Planning shots (Claude Sonnet)…", 0.08)
    shots = build_shot_plan(
        sections=sections, word_timings=timings,
        book_title=book_title, character_name=character_name, genre=genre,
        total_duration_sec=total_dur, anthropic_api_key=anthropic_api_key,
        debug_dir=review_dir,
    )
    plan_path = review_dir / "shot_plan.json"
    save_plan(shots, plan_path)
    script_path = review_dir / "script.txt"
    script_path.write_text(script_text, encoding="utf-8")
    (review_dir / "word_timings.json").write_text(
        __import__("json").dumps(
            [{"word": w.word, "start": w.start, "end": w.end, "source": w.source}
             for w in timings], ensure_ascii=False, indent=2),
        encoding="utf-8")

    # Stage 2 — prebuild the dossier (capture every candidate, pick the best).
    _prog("Capturing image candidates (prebuild)…", 0.20)
    _run_prebuild(
        plan_path, review_dir,
        script_path=script_path, audio_path=audio_path,
        book_title=book_title, character_name=character_name,
        anthropic_api_key=anthropic_api_key, pexels_api_key=pexels_api_key,
        character_portrait=character_portrait,
        book_cover=book_cover, book_cover_fit=book_cover_fit,
        book_cover_align=book_cover_align,
        enable_vision=enable_vision, book_extracts=book_extracts,
        photo_bank=photo_bank,
    )

    # Stage 3 — condition + render from the dossier.
    def _render_prog(label: str, frac: float) -> None:
        _prog(label, 0.55 + 0.45 * frac)

    render_from_review(
        review_dir, output_path,
        audio_path=audio_path, audio_duration_sec=total_dur,
        config=config, anthropic_api_key=anthropic_api_key,
        pexels_api_key=pexels_api_key, book_title=book_title,
        character_name=character_name, enable_vision=enable_vision,
        condition=condition, sr=sr, plan_path=plan_path,
        on_progress=_render_prog,
    )

    # Stage 4 — slim the dossier so its zip stays uploadable (render is done,
    # so chosen_file already points at the sharpened copy when conditioning ran).
    slim = slim_review_dir(review_dir, mode=slim_mode, keep_n=slim_keep_n)
    return {"video": output_path, "review_dir": review_dir,
            "plan": plan_path, "slim": slim}
