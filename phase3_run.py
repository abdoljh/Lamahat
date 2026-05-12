#!/usr/bin/env python3
"""
phase3_run.py — Standalone Phase 3 video generator.

Drives the existing phase3/ package from the command line without touching
the Streamlit app.  Useful for investigating visual quality, testing keyword
strategies, and comparing output options outside the full pipeline.

Usage
-----
    python phase3_run.py --script path/to/script.txt [options]

Required
--------
    --script PATH       Arabic video script (.txt, UTF-8)

Audio (optional — estimated from character count when omitted)
------
    --audio PATH        MP3 file from Phase 2 TTS
    --audio-duration S  Override total duration in seconds

Content context
---------------
    --book-title TEXT       Book title (improves keyword quality)
    --character-name TEXT   Main character / subject name
    --genre TEXT            history | biography | non-fiction | philosophy |
                            science | religion | novel  [default: history]

API keys (CLI > environment variable > .env file in current directory)
--------
    --anthropic-key KEY     ANTHROPIC_API_KEY  — enables keyword gen + vision scoring
    --pexels-key KEY        PEXELS_API_KEY     — optional Pexels video fallback

Visual options
--------------
    --output PATH           Output .mp4  [default: output/phase3_video.mp4]
    --color-grade NAME      warm | cool | neutral  [default: warm]
    --width N               [default: 1280]
    --height N              [default: 720]
    --images-per-section N  Wikimedia images per section  [default: 3]
    --no-subtitles          Skip ASS subtitle generation

Modes
-----
    --keywords-only         Generate + print keywords then exit (no video)
    --dry-run               Parse sections + estimate durations, print plan, exit
    --verbose               Show DEBUG-level log output

Output extras
-------------
    --save-keywords PATH    Write keyword JSON to this file (e.g. keywords.json)
    --thumbnail             Save a thumbnail JPEG beside the output video
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path


# ── Locate the package root so `phase3` is importable ───────────────────── #

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ── .env loader (no dependency on python-dotenv) ─────────────────────────── #

def _load_dotenv(path: Path) -> None:
    """Parse a simple KEY=VALUE .env file and set missing env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key   = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ── Progress printer ─────────────────────────────────────────────────────── #

_LAST_PCT: dict[str, float] = {"v": -1.0}

def _make_progress(verbose: bool):
    def _on_progress(label: str, frac: float) -> None:
        pct = int(frac * 100)
        if pct != _LAST_PCT["v"] or verbose:
            _LAST_PCT["v"] = pct
            bar_len  = 30
            filled   = int(bar_len * frac)
            bar      = "█" * filled + "░" * (bar_len - filled)
            print(f"\r  [{bar}] {pct:3d}%  {label:<55}", end="", flush=True)
        if frac >= 1.0:
            print()   # newline after 100%
    return _on_progress


# ── CLI argument parser ───────────────────────────────────────────────────── #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="phase3_run.py",
        description="Standalone Phase 3 video generator for Lamahat / Bk2Video.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Required
    p.add_argument("--script", required=True, metavar="PATH",
                   help="Arabic video script (.txt, UTF-8)")

    # Audio
    p.add_argument("--audio", metavar="PATH",
                   help="MP3 file from Phase 2 TTS")
    p.add_argument("--audio-duration", type=float, metavar="S",
                   help="Override total audio duration in seconds")

    # Content context
    p.add_argument("--book-title", default="", metavar="TEXT",
                   help="Book title for keyword context")
    p.add_argument("--character-name", default="", metavar="TEXT",
                   help="Main character / subject name")
    p.add_argument("--genre", default="history",
                   choices=["history", "biography", "non-fiction",
                            "philosophy", "science", "religion", "novel"],
                   help="Book genre [default: history]")

    # API keys
    p.add_argument("--anthropic-key", default="", metavar="KEY",
                   help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")
    p.add_argument("--pexels-key", default="", metavar="KEY",
                   help="Pexels API key (or set PEXELS_API_KEY env var)")

    # Visual options
    p.add_argument("--output", default="output/phase3_video.mp4", metavar="PATH",
                   help="Output .mp4 path [default: output/phase3_video.mp4]")
    p.add_argument("--color-grade", default="warm",
                   choices=["warm", "cool", "neutral"],
                   help="Colour grade [default: warm]")
    p.add_argument("--width", type=int, default=1280, metavar="N")
    p.add_argument("--height", type=int, default=720, metavar="N")
    p.add_argument("--images-per-section", type=int, default=3, metavar="N",
                   help="Max Wikimedia images per section [default: 3]")
    p.add_argument("--no-subtitles", action="store_true",
                   help="Skip ASS subtitle generation")

    # Modes
    p.add_argument("--keywords-only", action="store_true",
                   help="Generate keywords then exit (no video rendered)")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse sections and print plan, then exit")
    p.add_argument("--verbose", action="store_true",
                   help="Show DEBUG-level log messages")

    # Output extras
    p.add_argument("--save-keywords", metavar="PATH",
                   help="Write keyword JSON to file (e.g. keywords.json)")
    p.add_argument("--thumbnail", action="store_true",
                   help="Save a thumbnail JPEG beside the output video")

    return p


# ── Dry-run / plan printer ────────────────────────────────────────────────── #

def _print_plan(sections, durations) -> None:
    print("\n── Section Plan " + "─" * 50)
    total = sum(durations)
    for sec, dur in zip(sections, durations):
        bar = int(dur / total * 40)
        print(f"  {sec.section_id:<14}  {dur:6.1f}s  {'█' * bar}")
    print(f"  {'TOTAL':<14}  {total:6.1f}s")
    print("─" * 66 + "\n")


# ── Keyword-only runner ───────────────────────────────────────────────────── #

def _run_keywords_only(args, script_text: str, anthropic_key: str) -> dict:
    from phase3.parser import parse_sections, estimate_durations
    from phase3.keywords import generate_keywords, _fallback as _kw_fallback

    sections  = parse_sections(script_text)
    durations = estimate_durations(sections, args.audio_duration or len(script_text) / 12.0)
    _print_plan(sections, durations)

    print("── Generating keywords " + "─" * 44)
    if anthropic_key:
        keywords = generate_keywords(
            sections, args.genre, anthropic_key,
            book_title=args.book_title,
            character_name=args.character_name,
        )
    else:
        print("  (no Anthropic key — using genre fallbacks)")
        keywords = [_kw_fallback(s, args.genre) for s in sections]

    result = {}
    for kw in keywords:
        result[kw.section_id] = {
            "wikimedia":   kw.wikimedia,
            "pexels":      kw.pexels,
            "key_phrases": kw.key_phrases,
        }
        print(f"\n  [{kw.section_id}]")
        print(f"    Wikimedia:   {kw.wikimedia}")
        print(f"    Pexels:      {kw.pexels}")
        if kw.key_phrases:
            print(f"    Key phrases: {kw.key_phrases}")

    print()
    return result


# ── Main ─────────────────────────────────────────────────────────────────── #

def main() -> int:
    parser = _build_parser()
    args   = parser.parse_args()

    # ── Logging setup ─────────────────────────────────────────────────── #
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s  %(name)s  %(message)s",
        stream=sys.stdout,
    )
    # Always show phase3 package logs at INFO+
    logging.getLogger("phase3").setLevel(level)

    # ── Load .env ─────────────────────────────────────────────────────── #
    _load_dotenv(Path(".env"))
    _load_dotenv(_HERE / ".env")

    # ── Resolve API keys: CLI > env ────────────────────────────────────── #
    anthropic_key = (args.anthropic_key
                     or os.environ.get("ANTHROPIC_API_KEY", ""))
    pexels_key    = (args.pexels_key
                     or os.environ.get("PEXELS_API_KEY", ""))

    # ── Read script ───────────────────────────────────────────────────── #
    script_path = Path(args.script)
    if not script_path.exists():
        print(f"ERROR: script file not found: {script_path}", file=sys.stderr)
        return 1
    script_text = script_path.read_text(encoding="utf-8")
    print(f"\nScript : {script_path}  ({len(script_text):,} chars)")

    # ── Dry-run ───────────────────────────────────────────────────────── #
    if args.dry_run:
        from phase3.parser import parse_sections, estimate_durations
        dur_hint  = args.audio_duration or len(script_text) / 12.0
        sections  = parse_sections(script_text)
        durations = estimate_durations(sections, dur_hint)
        print(f"Sections found: {len(sections)}")
        _print_plan(sections, durations)
        return 0

    # ── Keywords-only ─────────────────────────────────────────────────── #
    if args.keywords_only:
        kw_data = _run_keywords_only(args, script_text, anthropic_key)
        if args.save_keywords:
            kw_path = Path(args.save_keywords)
            kw_path.parent.mkdir(parents=True, exist_ok=True)
            kw_path.write_text(
                json.dumps(kw_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Keywords saved → {kw_path}")
        return 0

    # ── Load audio ────────────────────────────────────────────────────── #
    audio_bytes: bytes | None = None
    if args.audio:
        audio_path = Path(args.audio)
        if not audio_path.exists():
            print(f"ERROR: audio file not found: {audio_path}", file=sys.stderr)
            return 1
        audio_bytes = audio_path.read_bytes()
        print(f"Audio  : {audio_path}  ({len(audio_bytes) / 1024:.0f} KB)")

    # ── Output path ───────────────────────────────────────────────────── #
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Summary line ──────────────────────────────────────────────────── #
    print(f"Output : {output_path}")
    print(f"Genre  : {args.genre}   Grade: {args.color_grade}   "
          f"{args.width}×{args.height}")
    print(f"Claude : {'✓  (keywords + vision scoring)' if anthropic_key else '✗  (genre fallbacks only)'}")
    print(f"Pexels : {'✓' if pexels_key else '✗  (Wikimedia images only)'}")
    print()

    # ── Run pipeline ─────────────────────────────────────────────────── #
    from phase3 import generate_background_video

    t0 = time.perf_counter()
    try:
        result = generate_background_video(
            script_text=script_text,
            output_path=output_path,
            audio_bytes=audio_bytes,
            audio_duration_sec=args.audio_duration,
            anthropic_api_key=anthropic_key,
            pexels_api_key=pexels_key,
            genre=args.genre,
            color_grade=args.color_grade,
            width=args.width,
            height=args.height,
            images_per_section=args.images_per_section,
            book_title=args.book_title,
            character_name=args.character_name,
            add_subtitles=not args.no_subtitles,
            on_progress=_make_progress(args.verbose),
        )
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - t0
    size_mb = result.stat().st_size / (1024 * 1024)
    print(f"\nDone in {elapsed:.0f}s  —  {result}  ({size_mb:.1f} MB)")

    # ── Thumbnail ─────────────────────────────────────────────────────── #
    if args.thumbnail:
        from phase3.compositor import extract_thumbnail
        thumb = result.with_suffix(".jpg")
        try:
            extract_thumbnail(result, thumb, time=5.0)
            print(f"Thumbnail → {thumb}")
        except Exception as exc:
            print(f"Thumbnail failed: {exc}", file=sys.stderr)

    # ── Save keywords (if pipeline was run with Anthropic key) ─────────── #
    if args.save_keywords:
        print(
            "Note: --save-keywords only captures keyword data in --keywords-only mode.\n"
            "Re-run with --keywords-only --save-keywords to inspect without rendering."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
