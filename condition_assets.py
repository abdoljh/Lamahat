#!/usr/bin/env python3
"""Resolution + framing conditioning for the Lamahat review dossier.

Normalises the captured assets so the renderer gets uniform, crisp,
aspect-correct input — WITHOUT distorting or cropping anything, and without
forcing a single pixel dimension.  Two render facts set the targets:

  * The 2.5D parallax stage fits each asset onto a buffer ≈ 1.18× the output
    (≈2266 px across for a 1920 frame) and then zooms IN up to ~8 %.  So a
    COVER-fill source wants long-edge ≈ 2560 to stay crisp through the move.
    (1920 is the *output* size — it is already soft at full zoom.)
  * A CONTAINED portrait only fills the frame HEIGHT (~1274 px × zoom), so it
    needs height ≈ 1600 and no more — which is why the main character's
    portraits keep their native dimensions instead of being needlessly blown up.

Policy (aspect-preserving; never distort, never crop):

    cover   (landscape, aspect in the cover band)
            -> long-edge normalised to --target-cover (default 2560), up or down
    contain (portrait / odd aspect, incl. the hero portraits)
            -> height floored at --contain-floor (default 1600); UP only,
               never downscaled, never cropped — dimensions preserved
    user-added images are premium
            -> upscaled with the SR path (Real-ESRGAN -> Lanczos fallback),
               never downscaled below native, never cropped
    too small even after upscaling (long-edge < --min-usable, default 600)
            -> kept as-is but flagged res_grade="low" for the reviewer

The cover/contain decision mirrors the renderer's `_fit_to_frame` aspect test
(same `--mismatch`, default 0.28) so the prediction here matches what actually
renders — there is still ONE framing authority (`_fit_to_frame`); this pass
only conditions *resolution* and records metadata.

For each chosen asset it writes a conditioned copy next to the original
(`<stem>.cond.jpg`), repoints `chosen_file` at it (keeping
`chosen_file_original`), and records in decisions.json:
    native_size, conditioned_size, aspect, aspect_class, framing, res_grade.

Idempotent: re-running skips assets already conditioned to the same target.
Non-destructive: originals are never modified or deleted.

Usage:
    python condition_assets.py --review-dir output/review
    python condition_assets.py --review-dir output/review --sr realesrgan
    python condition_assets.py --review-dir output/review --dry-run   # preview
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

from PIL import Image

log = logging.getLogger("condition_assets")

TARGET_AR = 16 / 9  # render frame aspect

# Filenames the pipeline writes for user-supplied assets (curated, premium).
# Anything matching is treated as user-added: SR upscaling, never downscaled.
_USER_PREFIXES = ("user_", "user-", "char_", "portrait_", "cover_")


# ── geometry helpers ──────────────────────────────────────────────────────── #

@dataclass
class Plan:
    native: tuple[int, int]
    aspect: float
    aspect_class: str          # "cover" | "contain"
    framing: str               # same as aspect_class (kept explicit for the dossier)
    target: tuple[int, int]    # conditioned size (may equal native)
    action: str                # "asis" | "upscale" | "downscale" | "sr"
    res_grade: str             # "ok" | "upscaled" | "sr" | "low"


def classify(w: int, h: int, mismatch: float) -> str:
    """cover vs contain — identical test to motion_parallax._fit_to_frame."""
    ar = w / h
    return "cover" if abs(ar - TARGET_AR) / TARGET_AR <= mismatch else "contain"


def plan_asset(w: int, h: int, *, user_added: bool, mismatch: float,
               target_cover: int, contain_floor: int,
               max_cap: int, min_usable: int) -> Plan:
    """Decide the conditioned size for one asset (aspect always preserved)."""
    ar = w / h
    long_edge = max(w, h)
    cls = classify(w, h, mismatch)

    def scaled_to_long(target_long: int) -> tuple[int, int]:
        s = target_long / long_edge
        return max(1, round(w * s)), max(1, round(h * s))

    def scaled_to_height(target_h: int) -> tuple[int, int]:
        s = target_h / h
        return max(1, round(w * s)), max(1, round(h * s))

    # Default: leave as-is.
    target, action, grade = (w, h), "asis", "ok"

    if cls == "cover":
        # Landscapes fill the frame → normalise the long edge to the cover target.
        if long_edge < target_cover:
            target, action = scaled_to_long(target_cover), ("sr" if user_added else "upscale")
            grade = "sr" if user_added else "upscaled"
        elif long_edge > max_cap and not user_added:
            # Cap oversized WEB assets to bound memory/time (user assets kept).
            target, action, grade = scaled_to_long(max_cap), "downscale", "ok"
        # else: already in [target_cover, max_cap] → as-is.
    else:
        # Portraits / odd aspect are CONTAINED → only the height must clear the
        # floor.  Never downscale, never crop → dimensions preserved.
        if h < contain_floor:
            target, action = scaled_to_height(contain_floor), ("sr" if user_added else "upscale")
            grade = "sr" if user_added else "upscaled"
        # else: already tall enough → as-is (this is the hero-portrait path).

    # Honesty flag: genuinely tiny sources can't be rescued to crispness.
    if long_edge < min_usable:
        grade = "low"

    return Plan(native=(w, h), aspect=round(ar, 4), aspect_class=cls,
                framing=cls, target=target, action=action, res_grade=grade)


# ── resampling ────────────────────────────────────────────────────────────── #

def _lanczos(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return img.resize(size, Image.LANCZOS)


_SR_STATE: dict = {}


def _realesrgan(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Real-ESRGAN upscale → Lanczos refine to the exact target.

    Lazy, optional, and self-healing: if torch / realesrgan / the weights are
    unavailable (e.g. an offline sandbox), log once and fall back to Lanczos so
    the pass never hard-fails on the SR path.
    """
    if _SR_STATE.get("disabled"):
        return _lanczos(img, size)
    try:  # pragma: no cover - environment dependent
        import numpy as np
        if "upsampler" not in _SR_STATE:
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                            num_block=23, num_grow_ch=32, scale=4)
            _SR_STATE["upsampler"] = RealESRGANer(
                scale=4, model_path="weights/RealESRGAN_x4plus.pth",
                model=model, half=False)
        up = _SR_STATE["upsampler"]
        out, _ = up.enhance(np.asarray(img.convert("RGB")), outscale=4)
        big = Image.fromarray(out)
        return big.resize(size, Image.LANCZOS) if big.size != size else big
    except Exception as exc:
        log.warning("Real-ESRGAN unavailable (%s) — falling back to Lanczos for "
                    "all SR upscales this run.", exc)
        _SR_STATE["disabled"] = True
        return _lanczos(img, size)


def resample(img: Image.Image, plan: Plan, sr: str) -> Image.Image:
    if plan.target == plan.native:
        return img
    if plan.action == "sr" and sr == "realesrgan":
        return _realesrgan(img, plan.target)
    return _lanczos(img, plan.target)


# ── documentary tone normalization (P3.2, SCREENING_REVIEW.md §2.4) ───────── #

# Sources whose winners get pulled toward the documentary palette.  Modern
# stock is the only offender — authentic material (photo bank, Wikimedia,
# Wikipedia, book extracts, user files) passes through untouched.
_TONE_SOURCES = {"pexels"}

# Tone recipe: mild desaturation + a warm curve (lift reds, sink blues)
# + fine luminance grain.  Strong enough that a teal-orange Pexels frame
# stops jumping out of a sepia film; weak enough not to read as a filter.
_TONE_SATURATION = 0.82
_TONE_R_GAIN, _TONE_R_LIFT = 1.045, 4
_TONE_B_GAIN, _TONE_B_LIFT = 0.925, 0
_TONE_GRAIN = 5.0


def apply_documentary_tone(img: Image.Image) -> Image.Image:
    """Pull a modern stock photo toward the film's documentary palette."""
    from PIL import ImageEnhance
    import numpy as np

    img = ImageEnhance.Color(img.convert("RGB")).enhance(_TONE_SATURATION)
    arr = np.asarray(img).astype(np.float32)
    arr[..., 0] = arr[..., 0] * _TONE_R_GAIN + _TONE_R_LIFT
    arr[..., 2] = arr[..., 2] * _TONE_B_GAIN + _TONE_B_LIFT
    rng = np.random.default_rng(0xF11A)   # deterministic grain per pixel grid
    arr += rng.normal(0.0, _TONE_GRAIN, arr.shape[:2])[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _chosen_source(shot: dict) -> str:
    """The winning candidate's source for a dossier shot entry."""
    chosen = shot.get("chosen") or ""
    return chosen.split(":", 1)[0] if ":" in chosen else chosen


# ── per-asset conditioning ────────────────────────────────────────────────── #

def is_user_added(name: str) -> bool:
    base = Path(name).name.lower()
    return base.startswith(_USER_PREFIXES)


def condition_file(src: Path, *, mismatch: float, target_cover: int,
                   contain_floor: int, max_cap: int, min_usable: int,
                   sr: str, quality: int) -> tuple[Path, Plan] | None:
    """Condition one image in place-adjacent; return (conditioned_path, plan)."""
    try:
        with Image.open(src) as im:
            im.load()
            w, h = im.size
            user = is_user_added(src.name)
            plan = plan_asset(w, h, user_added=user, mismatch=mismatch,
                              target_cover=target_cover, contain_floor=contain_floor,
                              max_cap=max_cap, min_usable=min_usable)
            out = src.with_name(src.stem + ".cond.jpg")
            if plan.target == plan.native and src.suffix.lower() in (".jpg", ".jpeg"):
                # No resize needed — reference the original directly (no recompress).
                return src, plan
            conditioned = resample(im.convert("RGB"), plan, sr)
            conditioned.save(out, "JPEG", quality=quality, subsampling=0)
            return out, plan
    except Exception as exc:
        log.warning("Could not condition %s (%s) — left untouched.", src, exc)
        return None


# ── dossier driver ────────────────────────────────────────────────────────── #

def run(review_dir: Path, *, mismatch: float, target_cover: int,
        contain_floor: int, max_cap: int, min_usable: int, sr: str,
        quality: int, dry_run: bool, tone: str = "documentary",
        on_progress: Callable[[str, float], None] | None = None) -> dict:
    decisions_path = review_dir / "decisions.json"
    if not decisions_path.exists():
        raise SystemExit(f"No decisions.json in {review_dir}")
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    shots = decisions.get("shots", {})
    if isinstance(shots, list):  # tolerate either schema
        shots = {str(i): s for i, s in enumerate(shots)}

    stats = {"total": 0, "asis": 0, "upscale": 0, "downscale": 0, "sr": 0,
             "low": 0, "cover": 0, "contain": 0, "flipped_to_cover": 0,
             "skipped_no_file": 0, "toned": 0}

    _n_shots = len(shots) or 1
    for _i, (key, shot) in enumerate(shots.items()):
        if on_progress:
            on_progress(f"Conditioning assets… {_i + 1}/{_n_shots}",
                        (_i + 1) / _n_shots)
        rel = shot.get("chosen_file")
        if not rel:
            stats["skipped_no_file"] += 1
            continue
        src = review_dir / rel
        if not src.exists():
            log.warning("Shot %s: chosen_file %s missing.", key, rel)
            stats["skipped_no_file"] += 1
            continue
        stats["total"] += 1

        # Did #1 (mismatch 0.22→0.28) flip this asset from contain to cover?
        with Image.open(src) as im:
            w0, h0 = im.size
        if classify(w0, h0, 0.22) == "contain" and classify(w0, h0, mismatch) == "cover":
            stats["flipped_to_cover"] += 1

        result = (None if dry_run
                  else condition_file(src, mismatch=mismatch, target_cover=target_cover,
                                      contain_floor=contain_floor, max_cap=max_cap,
                                      min_usable=min_usable, sr=sr, quality=quality))
        if dry_run:
            user = is_user_added(src.name)
            plan = plan_asset(w0, h0, user_added=user, mismatch=mismatch,
                              target_cover=target_cover, contain_floor=contain_floor,
                              max_cap=max_cap, min_usable=min_usable)
            cond_rel = rel
        elif result is None:
            continue
        else:
            cond_path, plan = result
            cond_rel = str(cond_path.relative_to(review_dir))

        stats[plan.aspect_class] += 1
        stats[plan.action if plan.action in stats else "asis"] += 1
        if plan.res_grade == "low":
            stats["low"] += 1

        # Documentary tone normalization (P3.2): pull modern-stock winners
        # toward the film's palette so a teal-orange Pexels frame stops
        # jumping out of a sepia documentary.  Authentic sources pass
        # through untouched.  Idempotent — a shot toned on a previous run
        # (marker in decisions.json) is never re-toned.
        prev_tone = (shot.get("conditioning") or {}).get("tone", "")
        applied_tone = prev_tone
        if (not dry_run and tone == "documentary" and not prev_tone
                and _chosen_source(shot) in _TONE_SOURCES):
            try:
                tone_src = review_dir / cond_rel
                with Image.open(tone_src) as im:
                    toned = apply_documentary_tone(im)
                if tone_src.name.endswith(".cond.jpg"):
                    out = tone_src            # derived file — safe to rewrite
                else:
                    out = tone_src.with_name(tone_src.stem + ".cond.jpg")
                toned.save(out, "JPEG", quality=quality, subsampling=0)
                cond_rel = str(out.relative_to(review_dir))
                applied_tone = "documentary"
                stats["toned"] += 1
                log.info("Shot %s toned (documentary palette, source=%s)",
                         key, _chosen_source(shot))
            except Exception as exc:  # noqa: BLE001 — tone is best-effort
                log.warning("Shot %s: tone failed (%s) — left as-is", key, exc)

        # Record metadata + repoint chosen_file (original preserved).
        shot.setdefault("chosen_file_original", rel)
        shot["chosen_file"] = cond_rel
        shot["conditioning"] = {
            "native_size": list(plan.native),
            "conditioned_size": list(plan.target),
            "aspect": plan.aspect,
            "aspect_class": plan.aspect_class,
            "framing": plan.framing,
            "res_grade": plan.res_grade,
            "user_added": is_user_added(src.name),
            "tone": applied_tone,
        }
        log.info("Shot %s %-7s %s %sx%s -> %sx%s [%s]", key, plan.aspect_class,
                 plan.action, *plan.native, *plan.target, plan.res_grade)

    if not dry_run:
        decisions.setdefault("conditioning_meta", {})
        decisions["conditioning_meta"] = {
            "target_cover": target_cover, "contain_floor": contain_floor,
            "max_cap": max_cap, "mismatch": mismatch, "sr": sr,
        }
        decisions_path.write_text(json.dumps(decisions, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review-dir", required=True, type=Path)
    ap.add_argument("--target-cover", type=int, default=2560,
                    help="Long-edge (px) for cover-fill landscapes (default 2560).")
    ap.add_argument("--contain-floor", type=int, default=1600,
                    help="Min height (px) for contained portraits (default 1600).")
    ap.add_argument("--max-cap", type=int, default=3200,
                    help="Long-edge cap for oversized WEB assets (default 3200).")
    ap.add_argument("--min-usable", type=int, default=600,
                    help="Below this long-edge, flag res_grade=low (default 600).")
    ap.add_argument("--mismatch", type=float, default=0.28,
                    help="Cover/contain aspect tolerance — must match "
                         "_fit_to_frame (default 0.28).")
    ap.add_argument("--sr", choices=["none", "realesrgan"], default="none",
                    help="Upscaler for sub-floor sources (default none=Lanczos; "
                         "realesrgan falls back to Lanczos if unavailable).")
    ap.add_argument("--quality", type=int, default=95,
                    help="JPEG quality for conditioned copies (default 95).")
    ap.add_argument("--tone", choices=["documentary", "off"],
                    default="documentary",
                    help="Tonal normalization of modern-stock (Pexels) "
                         "winners: mild desaturation + warm curve + fine "
                         "grain so they sit inside the documentary palette. "
                         "Authentic sources (photo bank, Wikimedia/Wikipedia, "
                         "user files) are never touched. Default documentary; "
                         "'off' disables.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report the plan without writing anything.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s  %(name)s  %(message)s")
    stats = run(args.review_dir, mismatch=args.mismatch,
                target_cover=args.target_cover, contain_floor=args.contain_floor,
                max_cap=args.max_cap, min_usable=args.min_usable, sr=args.sr,
                quality=args.quality, dry_run=args.dry_run, tone=args.tone)
    print("\nConditioning summary"
          f"{' (dry-run)' if args.dry_run else ''}:")
    for k in ("total", "cover", "contain", "flipped_to_cover",
              "upscale", "sr", "downscale", "asis", "low", "toned",
              "skipped_no_file"):
        print(f"  {k:<18}: {stats.get(k, 0)}")
    if stats.get("low"):
        print(f"  ⚠ {stats['low']} asset(s) flagged res_grade=low — consider swapping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
