#!/usr/bin/env python3
"""
Diagnose issue 1 — color grading patch.

Verifies that:
  1. phase3.render defines GRADE_PRESETS with the expected 4 keys.
  2. _mux_final accepts a `grade` keyword argument.
  3. RenderConfig has a `grade` field with default "warm".
  4. render_plan.py exposes a --grade CLI flag with the right choices.
  5. Each preset's FFmpeg filter string actually compiles (runs a 1-frame
     encode against a synthesised test clip).

Run after applying the patch.  ~10 s total.

Usage:
    python diagnose_grade.py
"""
from __future__ import annotations

import inspect
import subprocess
import sys
import tempfile
from pathlib import Path


OK = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def check(label: str, cond: bool, detail: str = "") -> bool:
    marker = OK if cond else FAIL
    extra = f" — {detail}" if detail else ""
    print(f"  {marker} {label}{extra}")
    return cond


def main() -> int:
    print("=" * 60)
    print("Issue 1 — color grading patch diagnostic")
    print("=" * 60)

    errors = 0

    # ── 1. phase3.render imports ──────────────────────────────────── #
    print("\n[1] phase3.render module")
    try:
        from phase3 import render as r
    except Exception as e:
        print(f"  {FAIL} import phase3.render — {e}")
        return 1

    has_presets = hasattr(r, "GRADE_PRESETS")
    errors += not check("GRADE_PRESETS defined", has_presets)
    if has_presets:
        keys = sorted(r.GRADE_PRESETS)
        expected = ["bw", "cool", "neutral", "warm"]
        errors += not check(
            "presets = {warm, cool, neutral, bw}",
            keys == expected,
            f"got {keys}",
        )

    errors += not check(
        'DEFAULT_GRADE == "warm"',
        getattr(r, "DEFAULT_GRADE", None) == "warm",
        f"got {getattr(r, 'DEFAULT_GRADE', None)!r}",
    )

    # ── 2. _mux_final signature ───────────────────────────────────── #
    print("\n[2] _mux_final signature")
    sig = inspect.signature(r._mux_final)
    errors += not check(
        "'grade' parameter present",
        "grade" in sig.parameters,
        f"params: {list(sig.parameters)}",
    )

    # ── 3. RenderConfig.grade field ───────────────────────────────── #
    print("\n[3] RenderConfig.grade")
    cfg = r.RenderConfig()
    errors += not check(
        "RenderConfig().grade defaults to 'warm'",
        cfg.grade == "warm",
        f"got {cfg.grade!r}",
    )

    # ── 4. render_plan.py CLI ─────────────────────────────────────── #
    print("\n[4] render_plan.py CLI")
    try:
        result = subprocess.run(
            [sys.executable, "render_plan.py", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        help_text = result.stdout + result.stderr
        errors += not check("--grade flag in --help", "--grade" in help_text)
        errors += not check(
            "all 4 presets advertised",
            all(p in help_text for p in ("warm", "cool", "neutral", "bw")),
        )
    except FileNotFoundError:
        print(f"  {FAIL} render_plan.py not found in cwd — run from repo root")
        errors += 1
    except Exception as e:
        print(f"  {FAIL} render_plan.py --help failed: {e}")
        errors += 1

    # ── 5. FFmpeg filter validation (real encode) ─────────────────── #
    print("\n[5] FFmpeg filter strings (1-frame encode test)")
    if not has_presets:
        print("  (skipped — presets unavailable)")
    else:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "src.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i",
                 "testsrc2=size=320x180:rate=25:duration=1", str(src)],
                check=True,
            )
            for name, flt in sorted(r.GRADE_PRESETS.items()):
                out = td / f"{name}.png"
                proc = subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error",
                     "-i", str(src), "-vf", flt, "-frames:v", "1", str(out)],
                    capture_output=True, text=True,
                )
                ok = proc.returncode == 0 and out.exists() and out.stat().st_size > 0
                errors += not check(f"preset '{name}'", ok,
                                    proc.stderr[-100:] if not ok else "")

    # ── verdict ──────────────────────────────────────────────────── #
    print()
    if errors == 0:
        print(f"{OK} All checks passed. Color grade patch is live.")
        return 0
    print(f"{FAIL} {errors} check(s) failed. Patch did not apply cleanly.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
