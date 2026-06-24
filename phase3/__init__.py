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

See `PHASE3.md` for the deep architecture reference.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

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
    music_gain_db: float = -18.0,
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
        sections = parse_sections(script_text)
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
            music_path=Path(music_path) if music_path else None,
            music_gain_db=music_gain_db,
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
