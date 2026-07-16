#!/usr/bin/env python3
"""Resolution + framing conditioning for the Lamahat review dossier.

Normalises the captured assets so the renderer gets uniform, crisp,
aspect-correct input.  Two render facts set the targets:

  * The 2.5D parallax stage renders on a motion-proportional buffer
    (≈1.04–1.09× the output, P6.4/R1) in the OUTPUT aspect and zooms IN
    up to ~8 %.  A COVER source that is already 16:9 therefore renders
    with essentially zero hidden crop; 2560×1440 keeps it crisp through
    the move (1920 is the *output* size — already soft at full zoom).
  * A CONTAINED portrait only fills the frame HEIGHT, so it needs
    height ≈ 1600 and no more — which is why the main character's
    portraits keep their native dimensions instead of being blown up.

Policy (P6.4/R2 — the 16:9 crop happens HERE, deliberately, not blindly
in the renderer):

    cover   (landscape, aspect in the cover band)
            -> smart-cropped to EXACT 16:9 (gradient-energy placement,
               slight top bias so heads survive; the renderer used to
               take this crop blind-centred at render time, invisible to
               the reviewer) -> normalised to --target-cover × 9/16
               (default 2560×1440, the full-screen standard).  The
               .cond.jpg in the dossier now shows exactly the frame that
               will render.  `--crop-cover off` restores the legacy
               aspect-preserving long-edge behaviour.
    contain (portrait / odd aspect, incl. the hero portraits)
            -> height floored at --contain-floor (default 1600); UP only,
               never downscaled, NEVER cropped — dimensions preserved
    user-added images are premium (incl. photo-bank `bank_*` files)
            -> upscaled with the SR path (Real-ESRGAN -> Lanczos fallback),
               never downscaled below native
    too small even after upscaling (long-edge < --min-usable, default 600)
            -> kept as-is but flagged res_grade="low" for the reviewer

The cover/contain decision mirrors the renderer's `_fit_to_frame` aspect test
(same `--mismatch`, default 0.28) so the prediction here matches what actually
renders.

For each chosen asset the conditioned image REPLACES the raw download
(P6.5a — one copy on disk, so the dossier stays small): it is written as
`<stem>*.jpg` (the star marks "pixels were conditioned"; `--mark '+'`
for Windows-safe names, `--keep-originals` for the legacy add-a-copy
`.cond.jpg` behaviour), `chosen_file` and the winning candidate entry
are repointed at it, and decisions.json records:
    native_size, conditioned_size, aspect, aspect_class, framing,
    res_grade, crop_box (+ chosen_file_original as name provenance).
Untouched assets (nothing to crop/resize/tone) keep their name unmarked.

After conditioning, shot folders with NO pickable image are flagged by
appending the marker to the FOLDER name (`shot_NN_visual*`, P6.5b) so
the curator can spot them at a glance; folders covered by the photo
bank / character pool are never flagged, and a flagged folder is
unflagged automatically once it gains an image.  The renderer finds
my_/user_ files inside flagged folders too.

Idempotent: re-running skips assets already conditioned to the same target
(a 16:9 starred .jpg re-plans as a no-op).

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
# `bank_` = photo-bank copies — user-curated by definition (P6.4).
_USER_PREFIXES = ("user_", "user-", "char_", "portrait_", "cover_", "bank_")


# ── geometry helpers ──────────────────────────────────────────────────────── #

@dataclass
class Plan:
    native: tuple[int, int]
    aspect: float
    aspect_class: str          # "cover" | "contain"
    framing: str               # "cover" | "cover_crop" | "contain"
    target: tuple[int, int]    # conditioned size (may equal native/crop)
    action: str                # "asis" | "upscale" | "downscale" | "sr"
    res_grade: str             # "ok" | "upscaled" | "sr" | "low"
    crop: tuple[int, int] | None = None   # 16:9 window (w, h) in native px
    crop_box: tuple[int, int, int, int] | None = None  # placed (x0,y0,x1,y1)


def classify(w: int, h: int, mismatch: float) -> str:
    """cover vs contain — identical test to motion_parallax._fit_to_frame."""
    ar = w / h
    return "cover" if abs(ar - TARGET_AR) / TARGET_AR <= mismatch else "contain"


def plan_asset(w: int, h: int, *, user_added: bool, mismatch: float,
               target_cover: int, contain_floor: int,
               max_cap: int, min_usable: int,
               crop_cover: str = "smart") -> Plan:
    """Decide the conditioned crop + size for one asset.

    Cover-class assets are cropped to EXACT 16:9 (P6.4/R2) unless
    `crop_cover="off"`; contain-class assets are never cropped and
    keep their aspect.  Sizing rules match the pre-P6.4 behaviour,
    applied to the (cropped) frame.
    """
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
    framing = cls
    crop: tuple[int, int] | None = None

    if cls == "cover" and crop_cover != "off":
        # Largest exact-16:9 window inside the native frame.  This is the
        # crop the renderer's cover-fill used to take blind-centred and
        # invisibly; taking it here makes it saliency-placed and puts the
        # true frame in front of the reviewer.
        if ar > TARGET_AR:
            crop_w, crop_h = max(1, round(h * TARGET_AR)), h
        else:
            crop_w, crop_h = w, max(1, round(w / TARGET_AR))
        crop_w, crop_h = min(crop_w, w), min(crop_h, h)
        if (crop_w, crop_h) != (w, h):
            crop = (crop_w, crop_h)
            framing = "cover_crop"

        canvas = (target_cover, round(target_cover / TARGET_AR))   # 2560×1440
        if crop_w < canvas[0]:
            target = canvas
            action = "sr" if user_added else "upscale"
            grade = "sr" if user_added else "upscaled"
        elif crop_w > max_cap and not user_added:
            # Cap oversized WEB assets to bound memory/time (user assets kept).
            target = (max_cap, round(max_cap / TARGET_AR))
            action, grade = "downscale", "ok"
        else:
            # Already in [target_cover, max_cap] → keep the cropped size.
            target = (crop_w, crop_h)
            action = "asis" if crop is None else "crop"
    elif cls == "cover":
        # Legacy aspect-preserving path (--crop-cover off).
        if long_edge < target_cover:
            target, action = scaled_to_long(target_cover), ("sr" if user_added else "upscale")
            grade = "sr" if user_added else "upscaled"
        elif long_edge > max_cap and not user_added:
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
                framing=framing, target=target, action=action,
                res_grade=grade, crop=crop)


def smart_crop_origin(img: Image.Image, crop_w: int, crop_h: int,
                      mode: str = "smart") -> tuple[int, int]:
    """Place a crop_w×crop_h window inside `img`.

    "smart": gradient-energy placement — the window slides along the one
    free axis to cover the most visual detail.  Vertical crops carry a
    slight TOP bias (subjects/faces in documentary stills sit above
    centre; a centred crop is what used to take heads off).  Falls back
    to centre when numpy is unavailable or the image is degenerate.
    "center": geometric centre (the renderer's old blind behaviour).
    """
    w, h = img.size
    cx0, cy0 = max(0, (w - crop_w) // 2), max(0, (h - crop_h) // 2)
    if mode != "smart" or (crop_w >= w and crop_h >= h):
        return cx0, cy0
    try:
        import numpy as np
        g = np.asarray(img.convert("L"), dtype=np.float32)
        gy, gx = np.gradient(g)
        energy = np.abs(gx) + np.abs(gy)
        if crop_h < h and crop_w >= w:
            rows = energy.sum(axis=1)
            rows *= np.linspace(1.08, 0.92, rows.size)   # top bias
            csum = np.concatenate(([0.0], np.cumsum(rows)))
            scores = csum[crop_h:] - csum[:-crop_h]
            return 0, int(scores.argmax())
        if crop_w < w and crop_h >= h:
            cols = energy.sum(axis=0)
            csum = np.concatenate(([0.0], np.cumsum(cols)))
            scores = csum[crop_w:] - csum[:-crop_w]
            return int(scores.argmax()), 0
        return cx0, cy0
    except Exception as exc:  # noqa: BLE001 — placement is best-effort
        log.debug("smart_crop_origin fell back to centre (%s)", exc)
        return cx0, cy0


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
    # Compare against the CURRENT size — the caller may have cropped first.
    if tuple(img.size) == tuple(plan.target):
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


# ── conditioned-file marker (P6.5a) ─────────────────────────────────────── #
#
# A conditioned image REPLACES its source (one copy on disk — the dossier
# stays small) and carries a marker before the extension so the reviewer can
# see at a glance which files were pixel-modified:  pexels_a.jpg → pexels_a*.jpg
#
# NOTE: '*' is not a legal filename character on Windows.  Review the dossier
# on Drive/Colab/Linux/macOS, or run with --mark '+' for a Windows-safe name.
DEFAULT_MARK = "*"


def marked_name(src: Path, mark: str) -> Path:
    """`pexels_a.jpg` → `pexels_a*.jpg` (idempotent — a stem that already
    ends with the marker is not marked twice; legacy `.cond` suffixes are
    folded into the marker)."""
    stem = src.stem
    if stem.endswith(".cond"):
        stem = stem[: -len(".cond")]
    stem = stem.rstrip(mark)
    return src.with_name(f"{stem}{mark}.jpg")


def is_marked(path: Path | str, mark: str = DEFAULT_MARK) -> bool:
    return Path(path).stem.endswith(mark)


def _replace_source(src: Path, out: Path) -> None:
    """Delete the raw source once its conditioned replacement is written."""
    if src.resolve() != out.resolve():
        try:
            src.unlink()
        except OSError as exc:
            log.warning("Couldn't remove replaced source %s (%s)", src, exc)


def condition_file(src: Path, *, mismatch: float, target_cover: int,
                   contain_floor: int, max_cap: int, min_usable: int,
                   sr: str, quality: int,
                   crop_cover: str = "smart",
                   mark: str = DEFAULT_MARK,
                   keep_originals: bool = False) -> tuple[Path, Plan] | None:
    """Condition one image; return (conditioned_path, plan).

    The conditioned image REPLACES the source (P6.5a): it is written as
    `<stem><mark>.jpg` and the raw source is deleted, so each winner
    exists exactly once on disk.  Untouched files (nothing to crop or
    resize) keep their original name — the marker means "pixels were
    modified".  `keep_originals=True` restores the legacy add-a-copy
    behaviour (`<stem>.cond.jpg`, source kept).
    """
    try:
        with Image.open(src) as im:
            im.load()
            w, h = im.size
            user = is_user_added(src.name)
            plan = plan_asset(w, h, user_added=user, mismatch=mismatch,
                              target_cover=target_cover, contain_floor=contain_floor,
                              max_cap=max_cap, min_usable=min_usable,
                              crop_cover=crop_cover)
            if (plan.crop is None and plan.target == plan.native
                    and src.suffix.lower() in (".jpg", ".jpeg")):
                # Nothing to change — reference the file as-is
                # (no recompress, no marker).
                return src, plan
            out = (src.with_name(src.stem + ".cond.jpg") if keep_originals
                   else marked_name(src, mark))
            work = im.convert("RGB")
            if plan.crop is not None:
                cw, ch = plan.crop
                x0, y0 = smart_crop_origin(work, cw, ch, mode=crop_cover)
                plan.crop_box = (x0, y0, x0 + cw, y0 + ch)
                work = work.crop(plan.crop_box)
            conditioned = resample(work, plan, sr)
            conditioned.save(out, "JPEG", quality=quality, subsampling=0)
        if not keep_originals:
            _replace_source(src, out)
        return out, plan
    except Exception as exc:
        log.warning("Could not condition %s (%s) — left untouched.", src, exc)
        return None


# ── dossier driver ────────────────────────────────────────────────────────── #

def run(review_dir: Path, *, mismatch: float, target_cover: int,
        contain_floor: int, max_cap: int, min_usable: int, sr: str,
        quality: int, dry_run: bool, tone: str = "documentary",
        crop_cover: str = "smart",
        mark: str = DEFAULT_MARK,
        keep_originals: bool = False,
        on_progress: Callable[[str, float], None] | None = None) -> dict:
    decisions_path = review_dir / "decisions.json"
    if not decisions_path.exists():
        raise SystemExit(f"No decisions.json in {review_dir}")
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    shots = decisions.get("shots", {})
    if isinstance(shots, list):  # tolerate either schema
        shots = {str(i): s for i, s in enumerate(shots)}

    stats = {"total": 0, "asis": 0, "crop": 0, "upscale": 0, "downscale": 0,
             "sr": 0, "low": 0, "cover": 0, "contain": 0, "cropped": 0,
             "flipped_to_cover": 0, "skipped_no_file": 0, "toned": 0,
             "replaced": 0, "flagged_folders": 0}

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
                                      min_usable=min_usable, sr=sr, quality=quality,
                                      crop_cover=crop_cover, mark=mark,
                                      keep_originals=keep_originals))
        if dry_run:
            user = is_user_added(src.name)
            plan = plan_asset(w0, h0, user_added=user, mismatch=mismatch,
                              target_cover=target_cover, contain_floor=contain_floor,
                              max_cap=max_cap, min_usable=min_usable,
                              crop_cover=crop_cover)
            cond_rel = rel
        elif result is None:
            continue
        else:
            cond_path, plan = result
            cond_rel = str(cond_path.relative_to(review_dir))

        stats[plan.aspect_class] += 1
        stats[plan.action if plan.action in stats else "asis"] += 1
        if plan.crop is not None:
            stats["cropped"] += 1
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
                if tone_src.name.endswith(".cond.jpg") or is_marked(tone_src, mark):
                    out = tone_src            # derived file — safe to rewrite
                elif keep_originals:
                    out = tone_src.with_name(tone_src.stem + ".cond.jpg")
                else:
                    out = marked_name(tone_src, mark)
                toned.save(out, "JPEG", quality=quality, subsampling=0)
                if not keep_originals:
                    _replace_source(tone_src, out)
                cond_rel = str(out.relative_to(review_dir))
                applied_tone = "documentary"
                stats["toned"] += 1
                log.info("Shot %s toned (documentary palette, source=%s)",
                         key, _chosen_source(shot))
            except Exception as exc:  # noqa: BLE001 — tone is best-effort
                log.warning("Shot %s: tone failed (%s) — left as-is", key, exc)

        # Record metadata + repoint chosen_file.  The pre-conditioning name
        # is kept as provenance in `chosen_file_original`; in replace mode
        # (the default) that file no longer exists on disk — the marked
        # conditioned image is the only copy.
        shot.setdefault("chosen_file_original", rel)
        shot["chosen_file"] = cond_rel
        if cond_rel != rel:
            stats["replaced"] += int(not keep_originals)
            # Keep the candidate list consistent: the winning candidate's
            # `file` entry must follow the replacement, or render-time
            # alternate walks (dedupe swap, backdrop rotate) would chase a
            # deleted path.
            for cand in shot.get("candidates") or []:
                if cand.get("file") == rel:
                    cand["file"] = cond_rel
                    break
        shot["conditioning"] = {
            "native_size": list(plan.native),
            "conditioned_size": list(plan.target),
            "aspect": plan.aspect,
            "aspect_class": plan.aspect_class,
            "framing": plan.framing,
            "res_grade": plan.res_grade,
            "user_added": is_user_added(src.name),
            "tone": applied_tone,
            "crop_box": list(plan.crop_box) if plan.crop_box else None,
        }
        log.info("Shot %s %-10s %s %sx%s -> %sx%s [%s]%s", key, plan.framing,
                 plan.action, *plan.native, *plan.target, plan.res_grade,
                 f"  crop={plan.crop_box}" if plan.crop_box else "")

    # ── Flag attention-needing shot folders (P6.5b) ─────────────────── #
    # A shot folder with NO pickable image gets the marker appended
    # (`shot_NN_visual` → `shot_NN_visual*`) so the curator can spot it
    # in one glance; a previously starred folder that has since gained a
    # file (user drop, re-prebuild) is unstarred.  Folders that are empty
    # because a curated source covers the shot (photo bank assignment,
    # character pool — recorded in the decision's `covered` field) are
    # fine and never starred.  The renderer's user-file lookup tolerates
    # the starred name, so dropping my_/user_ files into a starred folder
    # still works.
    stats["flagged_folders"] = _flag_empty_folders(
        review_dir, shots, mark=mark, dry_run=dry_run)

    if not dry_run:
        decisions.setdefault("conditioning_meta", {})
        decisions["conditioning_meta"] = {
            "target_cover": target_cover, "contain_floor": contain_floor,
            "max_cap": max_cap, "mismatch": mismatch, "sr": sr,
            "crop_cover": crop_cover, "mark": mark,
        }
        decisions_path.write_text(json.dumps(decisions, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return stats


_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _shot_folder_base(key: str, visual: str) -> str:
    """Mirror of phase3.sources.decisions.shot_folder_name — duplicated
    here so this script stays importable without the phase3 package's
    heavy dependencies (cv2 via the renderer)."""
    return f"shot_{int(key):02d}_{visual}"


def _folder_has_pickable(folder: Path, shot: dict, review_dir: Path) -> bool:
    """True when the shot needs no curator attention: a chosen/override
    file that exists, any candidate image on disk, any image dropped into
    the folder, or an explicit `covered` note (photo bank / character
    pool resolve the shot at render time)."""
    if (shot.get("covered") or "").strip():
        return True
    # Dossiers from before the `covered` field: the skip note only exists
    # in the folder's context.txt.
    ctx = folder / "context.txt" if folder else None
    if ctx is not None and ctx.is_file():
        try:
            if "waterfall skipped" in ctx.read_text(encoding="utf-8"):
                return True
        except OSError:
            pass
    for key in ("override", "chosen_file"):
        rel = shot.get(key)
        if rel and (review_dir / rel).exists():
            return True
    for cand in shot.get("candidates") or []:
        rel = cand.get("file")
        if rel and (review_dir / rel).exists():
            return True
    if folder.is_dir():
        for f in folder.iterdir():
            if f.is_file() and f.suffix.lower() in _IMG_EXTS:
                return True
    return False


def _flag_empty_folders(review_dir: Path, shots: dict, *,
                        mark: str, dry_run: bool) -> int:
    """Star/unstar shot folders by attention state; returns the number of
    folders currently flagged."""
    flagged = 0
    for key, shot in shots.items():
        try:
            base = _shot_folder_base(key, shot.get("visual", ""))
        except (TypeError, ValueError):
            continue
        plain = review_dir / base
        starred = review_dir / (base + mark)
        folder = plain if plain.is_dir() else (
            starred if starred.is_dir() else None)
        if folder is None:
            continue
        needs_attention = not _folder_has_pickable(folder, shot, review_dir)
        if needs_attention:
            flagged += 1
            if folder == plain and not dry_run:
                try:
                    plain.rename(starred)
                    log.info("Shot %s: no pickable candidate — flagged %s",
                             key, starred.name)
                except OSError as exc:
                    log.warning("Shot %s: couldn't flag folder (%s)", key, exc)
        elif folder == starred and not dry_run:
            try:
                starred.rename(plain)
                log.info("Shot %s: folder has a pickable image again — "
                         "unflagged %s", key, plain.name)
            except OSError as exc:
                log.warning("Shot %s: couldn't unflag folder (%s)", key, exc)
    return flagged


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review-dir", required=True, type=Path)
    ap.add_argument("--target-cover", type=int, default=2560,
                    help="Width (px) of the 16:9 canvas for cover-fill "
                         "images — the full-screen standard is 2560×1440 "
                         "(default 2560).  With --crop-cover off this is "
                         "the legacy long-edge target instead.")
    ap.add_argument("--crop-cover", choices=["smart", "center", "off"],
                    default="smart",
                    help="16:9 crop for cover-class assets (P6.4/R2). "
                         "'smart' (default) places the crop by gradient "
                         "energy with a slight top bias — the renderer "
                         "previously took this exact crop blind-centred at "
                         "render time; 'center' crops geometrically; 'off' "
                         "restores the aspect-preserving legacy behaviour.")
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
    ap.add_argument("--mark", default=DEFAULT_MARK,
                    help="Marker character appended to conditioned image "
                         "names and to attention-needing shot folders "
                         "(default '*').  '*' is not a legal filename "
                         "character on Windows — use --mark '+' if the "
                         "dossier will be unzipped there.")
    ap.add_argument("--keep-originals", action="store_true",
                    help="Legacy behaviour: write the conditioned copy as "
                         "<stem>.cond.jpg NEXT TO the original instead of "
                         "replacing it (roughly doubles winner storage).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report the plan without writing anything.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if len(args.mark) != 1 or args.mark.isalnum():
        ap.error("--mark must be a single non-alphanumeric character")

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s  %(name)s  %(message)s")
    stats = run(args.review_dir, mismatch=args.mismatch,
                target_cover=args.target_cover, contain_floor=args.contain_floor,
                max_cap=args.max_cap, min_usable=args.min_usable, sr=args.sr,
                quality=args.quality, dry_run=args.dry_run, tone=args.tone,
                crop_cover=args.crop_cover, mark=args.mark,
                keep_originals=args.keep_originals)
    print("\nConditioning summary"
          f"{' (dry-run)' if args.dry_run else ''}:")
    for k in ("total", "cover", "contain", "cropped", "flipped_to_cover",
              "upscale", "sr", "downscale", "asis", "low", "toned",
              "replaced", "flagged_folders", "skipped_no_file"):
        print(f"  {k:<18}: {stats.get(k, 0)}")
    if stats.get("low"):
        print(f"  ⚠ {stats['low']} asset(s) flagged res_grade=low — consider swapping.")
    if stats.get("flagged_folders"):
        print(f"  ⚠ {stats['flagged_folders']} shot folder(s) have no pickable "
              f"image — flagged with '{args.mark}'; drop a my_/user_ file in "
              f"or they render as Arabic typography cards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
