"""
motion_parallax.py — depth-based 2.5D parallax motion for the Lamahat Phase 3 renderer.

PROTOTYPE v0.1 (for visual review).

Why this is a *per-frame* renderer and not another `_MOTION_FILTERS` ffmpeg string
--------------------------------------------------------------------------------------
The existing motions (`slow_push`, `ken_burns`, `pan_*`, `section_accent`) are
single-image ffmpeg `zoompan`/scale filters: every pixel of the still moves by the
same transform. Parallax is fundamentally different — the *foreground must move more
than the background*. A single ffmpeg transform cannot express a per-pixel,
depth-weighted displacement, so parallax has to synthesise frames itself.

The good news: it still emits a clip with the EXACT encoder profile the rest of the
pipeline uses, so `render_video()`'s stream-copy concat stays valid. Nothing else in
the pipeline has to change.

Depth source is pluggable
--------------------------
    estimate_depth_depthanything(img_bgr)  -> Depth Anything V2 (use in Colab; GPU + HF)
    estimate_depth_classical(img_bgr)      -> dependency-free proxy (degraded; CI/demo)

Both return float32 HxW in [0, 1] with **near == 1.0, far == 0.0**.

Public entry point
------------------
    render_parallax_clip(img_bgr, depth, out_path, duration_sec, motion="arc", ...)

Integration is a ~10-line dispatch in render_video(); see README.md.
"""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
from pathlib import Path

import cv2
import numpy as np

# ----------------------------------------------------------------------------- #
# Pipeline-matched output profile. These MUST stay identical to the values in
# render.py, or stream-copy concat in render_video() silently breaks.
# (Phase3.md §4: "libx264 -preset ultrafast -crf 22 -pix_fmt yuv420p -r 25".)
# ----------------------------------------------------------------------------- #
OUT_W, OUT_H = 1920, 1080
FPS = 25
_ENC = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(FPS)]

# Render on a slightly larger buffer than the output so camera displacement never
# pulls the frame edge into view (mirrors render.py's "1.6x buffer" philosophy;
# parallax needs far less head-room because the moves are gentle).
_BUFFER = 1.18


# ============================================================================= #
# Depth providers
# ============================================================================= #
_DA_PIPE: dict = {}   # model -> transformers depth-estimation pipeline (load once)


def _get_depthanything_pipe(model: str, device: str | None):
    """Lazily build and cache the Depth Anything pipeline (avoids reloading the
    model on every shot when render_video() runs many parallax shots)."""
    key = model
    if key not in _DA_PIPE:
        import torch
        from transformers import pipeline
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        _DA_PIPE[key] = pipeline("depth-estimation", model=model,
                                 device=0 if device == "cuda" else -1)
    return _DA_PIPE[key]


def estimate_depth_depthanything(img_bgr: np.ndarray,
                                 model: str = "depth-anything/Depth-Anything-V2-Small-hf",
                                 device: str | None = None) -> np.ndarray:
    """Depth Anything V2 via 🤗 transformers. Use this in Colab.

    Returns float32 HxW in [0,1], near=1. Requires `pip install transformers torch`
    and network access to huggingface.co (both available in Colab, neither in the
    Anthropic sandbox — hence the classical fallback below for offline demos).
    """
    from PIL import Image
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pipe = _get_depthanything_pipe(model, device)   # loaded once, reused per shot
    out = pipe(Image.fromarray(rgb))
    # DA V2 returns a PIL "depth" image whose brightness ~ proximity (near is bright).
    d = np.asarray(out["depth"], dtype=np.float32)
    d = cv2.resize(d, (img_bgr.shape[1], img_bgr.shape[0]), interpolation=cv2.INTER_CUBIC)
    return _normalize01(d)  # already near-bright -> near=1 after normalize


def estimate_depth_classical(img_bgr: np.ndarray) -> np.ndarray:
    """Dependency-free *proxy* depth. Degraded — for CI / offline smoke tests only.

    Heuristic: things lower in frame and higher in local contrast read as "nearer".
    Real runs should use estimate_depth_depthanything(). Documented as a fallback so
    the module imports and runs anywhere.
    """
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # local contrast (focus proxy): high-freq energy
    blur = cv2.GaussianBlur(gray, (0, 0), 3.0)
    contrast = _normalize01(cv2.GaussianBlur(np.abs(gray - blur), (0, 0), 9.0))
    # vertical gradient: bottom of frame is nearer
    vgrad = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None] * np.ones((1, w), np.float32)
    depth = 0.55 * vgrad + 0.45 * contrast
    return _normalize01(cv2.GaussianBlur(depth, (0, 0), 7.0))


# ============================================================================= #
# Camera paths  (t in [0,1] -> cx, cy in [-1,1], zoom >= 1.0)
# ============================================================================= #
def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def camera_path(name: str, t: float, intensity: float = 1.0) -> tuple[float, float, float]:
    """Return (cx, cy, zoom) for normalized time t.

    cx/cy are normalized lateral camera offset; zoom is a multiplicative push.
    `intensity` scales the whole move — use ~0.4 for historical hero stills (subtle),
    1.0 for landscapes/establishing shots.
    """
    s = _smoothstep(t)
    if name == "arc":            # lateral truck L->R with a gentle push — the classic reveal
        return ((2.0 * s - 1.0) * intensity, 0.08 * math.sin(math.pi * t) * intensity,
                1.0 + 0.030 * s * intensity)
    if name == "dolly_in":       # push straight in; near grows faster than far -> depth
        return (0.0, 0.0, 1.0 + 0.060 * s * intensity)
    if name == "dolly_out":
        return (0.0, 0.0, 1.0 + 0.060 * (1.0 - s) * intensity)
    if name == "truck_left":
        return (-1.0 * s * intensity, 0.0, 1.0 + 0.020 * s * intensity)
    if name == "truck_right":
        return (1.0 * s * intensity, 0.0, 1.0 + 0.020 * s * intensity)
    if name == "float":          # slow breathing drift for talking-head / portrait holds
        return (0.16 * math.sin(2 * math.pi * t) * intensity,
                0.10 * math.sin(math.pi * t) * intensity,
                1.0 + 0.015 * s * intensity)
    raise ValueError(f"unknown motion path: {name!r}")


# ============================================================================= #
# Core warp
# ============================================================================= #
def _normalize01(a: np.ndarray) -> np.ndarray:
    a = a.astype(np.float32)
    lo, hi = float(a.min()), float(a.max())
    return (a - lo) / (hi - lo + 1e-6)


def _cover_resize(img: np.ndarray, w: int, h: int, interp=cv2.INTER_AREA) -> np.ndarray:
    """Scale-to-cover (fill w x h, center-crop overflow)."""
    ih, iw = img.shape[:2]
    scale = max(w / iw, h / ih)
    nw, nh = int(math.ceil(iw * scale)), int(math.ceil(ih * scale))
    r = cv2.resize(img, (nw, nh), interpolation=interp)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return r[y0:y0 + h, x0:x0 + w]


def _remap_layer(layer, disp, xs, ys, px, py, cx, cy, zoom, amp_px):
    """Backward-warp one image/alpha layer by a depth-weighted camera move.
    Dense (no pinholes) — the building block for both warp modes."""
    s = 1.0 + (zoom - 1.0) * disp
    src_x = px + (xs - px) / s + cx * amp_px * disp
    src_y = py + (ys - py) / s + cy * amp_px * disp
    return cv2.remap(layer, src_x, src_y, interpolation=cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT101)


def _prepare_amodal_layers(img, disp, *, fg_thresh=0.55, feather=9.0,
                           inpaint_radius=7):
    """Split a still into a feathered foreground + an *amodal* background plate
    (the scene with the foreground painted out) plus a background depth field.

    This is the disocclusion fix: when the foreground later slides under the
    camera move, the gap it reveals shows the inpainted background plate instead
    of a smeared edge.  Done once per shot (cheap); the per-frame cost is just
    two warps + a composite.
    """
    alpha = np.clip((disp - fg_thresh) / max(1e-3, 1.0 - fg_thresh), 0, 1)
    alpha = cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), feather)
    fg_mask = (alpha > 0.04).astype(np.uint8) * 255
    k = max(3, int(round(feather)) | 1)                      # odd kernel
    dil = cv2.dilate(fg_mask, np.ones((k, k), np.uint8))
    bg_plate = cv2.inpaint(img, dil, inpaint_radius, cv2.INPAINT_TELEA)
    bg_disp = cv2.inpaint((disp * 255).astype(np.uint8), dil,
                          inpaint_radius, cv2.INPAINT_TELEA).astype(np.float32) / 255.0
    bg_disp = cv2.GaussianBlur(bg_disp, (0, 0), feather)
    return alpha.astype(np.float32), bg_plate, bg_disp.astype(np.float32)


def render_parallax_clip(img_bgr: np.ndarray,
                         depth: np.ndarray,
                         out_path: str | Path,
                         duration_sec: float,
                         *,
                         motion: str = "arc",
                         intensity: float = 1.0,
                         amp_px: float = 70.0,
                         depth_softness: float = 6.0,
                         warp: str = "backward",
                         fps: int = FPS,
                         out_w: int = OUT_W,
                         out_h: int = OUT_H) -> Path:
    """Render one depth-parallax MP4 clip with the pipeline-matched encoder profile.

    img_bgr   : source image (any size), BGR uint8.
    depth     : float32 HxW in [0,1], near=1 (same size as img OR will be resized).
    amp_px    : max lateral disparity (px) applied to the *nearest* plane. Historical
                hero stills look best at ~30-45; landscapes tolerate 70+.
    depth_softness: gaussian sigma to soften depth edges.
    warp      : "backward" (v0.1, dense single remap; fast, soft edges — best for
                subtle moves) or "inpaint" (v0.3, amodal-background layered
                composite; clean disocclusions for BOLD moves on sharp-edged
                photos, ~2x the per-frame cost).
    """
    out_path = Path(out_path)
    bw, bh = int(round(out_w * _BUFFER)), int(round(out_h * _BUFFER))
    mx, my = (bw - out_w) // 2, (bh - out_h) // 2

    # Resize image + depth onto the cover buffer.
    img = _cover_resize(img_bgr, bw, bh, interp=cv2.INTER_CUBIC)
    if depth.shape[:2] != img_bgr.shape[:2]:
        depth = cv2.resize(depth, (img_bgr.shape[1], img_bgr.shape[0]), interpolation=cv2.INTER_CUBIC)
    disp = _cover_resize(depth, bw, bh, interp=cv2.INTER_CUBIC)
    # Median pre-filter: estimated depth on flat photos often has isolated
    # spikes (a face, a medal) that the warp turns into local "melting".  A
    # median kill those speckles while preserving real edges far better than a
    # Gaussian.  This is the main robustness fix vs the deformation seen on
    # real group photos.
    dm = np.clip(disp, 0, 1)
    dm = cv2.medianBlur((dm * 255).astype(np.uint8), 5).astype(np.float32) / 255.0
    disp = dm
    if depth_softness > 0:
        disp = cv2.GaussianBlur(disp, (0, 0), depth_softness)
    disp = _normalize01(disp).astype(np.float32)          # near=1, far=0

    # Pixel grid on the buffer.
    xs, ys = np.meshgrid(np.arange(bw, dtype=np.float32),
                         np.arange(bh, dtype=np.float32))
    px, py = bw / 2.0, bh / 2.0                            # principal point

    # "inpaint" (opt-in): precompute amodal layers — but only if the foreground
    # separates cleanly.  On group photos / paintings the fg mask covers most of
    # the frame or shatters into many pieces; warping that deforms real content,
    # so we fall back to the safe backward warp.
    if warp == "inpaint":
        alpha, bg_plate, bg_disp = _prepare_amodal_layers(
            img, disp, feather=max(6.0, depth_softness))
        fg_area = float((alpha > 0.5).mean())
        n_comp, _ = cv2.connectedComponents((alpha > 0.5).astype(np.uint8))
        if fg_area > 0.30 or n_comp - 1 > 3:
            print(f"[motion_parallax] inpaint unsafe for this image "
                  f"(fg_area={fg_area:.0%}, regions={n_comp - 1}); "
                  f"using backward warp.")
            warp = "backward"

    n_frames = max(1, int(round(duration_sec * fps)))
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{out_w}x{out_h}", "-r", str(fps), "-i", "-", *_ENC, str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for f in range(n_frames):
            t = f / max(1, n_frames - 1)
            cx, cy, zoom = camera_path(motion, t, intensity)
            if warp == "inpaint":
                # Warp foreground (full disparity) and the inpainted background
                # plate (its own depth) separately, then composite FG over BG.
                # Revealed gaps show clean background, not a smeared edge.
                w_fg = _remap_layer(img, disp, xs, ys, px, py, cx, cy, zoom, amp_px)
                w_bg = _remap_layer(bg_plate, bg_disp, xs, ys, px, py, cx, cy, zoom, amp_px)
                w_a = _remap_layer(alpha, disp, xs, ys, px, py, cx, cy, zoom, amp_px)
                a3 = w_a[..., None]
                frame_buf = (w_fg.astype(np.float32) * a3
                             + w_bg.astype(np.float32) * (1.0 - a3)).astype(np.uint8)
            else:  # "backward" (v0.1): single dense remap
                frame_buf = _remap_layer(img, disp, xs, ys, px, py, cx, cy, zoom, amp_px)
            frame = frame_buf[my:my + out_h, mx:mx + out_w]
            proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
    finally:
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed ({rc}):\n{err[-2000:]}")
    return out_path


# ============================================================================= #
# Convenience: image path -> clip, estimating depth with the best available backend.
# ============================================================================= #
def parallax_from_image(image_path: str | Path,
                        out_path: str | Path,
                        duration_sec: float,
                        *,
                        motion: str = "arc",
                        intensity: float = 1.0,
                        amp_px: float = 70.0,
                        depth_backend: str = "auto") -> Path:
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(image_path)
    if depth_backend == "depthanything" or depth_backend == "auto":
        try:
            depth = estimate_depth_depthanything(img)
        except Exception as e:               # noqa: BLE001 — fall back loudly
            if depth_backend == "depthanything":
                raise
            print(f"[motion_parallax] Depth Anything unavailable ({e!r}); "
                  f"using classical proxy depth.")
            depth = estimate_depth_classical(img)
    else:
        depth = estimate_depth_classical(img)
    return render_parallax_clip(img, depth, out_path, duration_sec,
                                motion=motion, intensity=intensity, amp_px=amp_px)


# ============================================================================= #
# ── Integration helpers (v0.2) ──────────────────────────────────────────────
# Everything below is what render.py / prebuild_assets.py call. The goal is to
# keep the edits in those (unseen) files to a few mechanical lines; the logic
# lives here. See INTEGRATION.md.
# ============================================================================= #

# Per-visual defaults. Tuned conservative after reviewing a real hero portrait
# run at arc/intensity=1.0/amp=70 — that read slightly strong for a face. Faces
# get a gentle dolly_in (minimises disocclusion vs a lateral truck); landscapes
# keep the bolder arc.
# NOTE: every visual defaults to "backward" — the robust single-remap warp.
# The v0.3 "inpaint" (amodal-matte) mode deformed real multi-subject photos
# (melted faces / ghosting) because estimated depth on flat group images is
# noisy and the fg threshold cut through subjects.  It is now opt-in only
# (--parallax-warp inpaint) and self-guards (falls back to backward when the
# foreground can't be cleanly separated).  See render_parallax_clip().
PARALLAX_DEFAULTS: dict[str, dict] = {
    "portrait": dict(motion="dolly_in", intensity=0.40, amp_px=36, depth_softness=8.0, warp="backward"),
    "archive":  dict(motion="dolly_in", intensity=0.50, amp_px=42, depth_softness=8.0, warp="backward"),
    "location": dict(motion="arc",      intensity=0.70, amp_px=60, depth_softness=7.0, warp="backward"),
    "object":   dict(motion="float",    intensity=0.45, amp_px=38, depth_softness=8.0, warp="backward"),
    "broll":    dict(motion="arc",      intensity=0.60, amp_px=52, depth_softness=7.0, warp="backward"),
}
_PARALLAX_FALLBACK = dict(motion="dolly_in", intensity=0.45, amp_px=42, depth_softness=8.0, warp="backward")

# Visual types that should never parallax (flat by design).
PARALLAX_SKIP_VISUALS = {"typography", "title_card", "section_mark"}


def params_for_visual(visual: str) -> dict:
    """Motion/intensity/amp for a shot's visual type."""
    return dict(PARALLAX_DEFAULTS.get(visual, _PARALLAX_FALLBACK))


# Depth maps are cached in a DEDICATED directory — never beside the source
# image.  Writing "<stem>.depth.png" next to pool/dossier images pollutes the
# candidate scan: the pool doubles in size and ".depth.png" even wins the
# alphabetical tiebreak, so the renderer ends up picking depth maps as photos.
# Override the location with $LAMAHAT_DEPTH_CACHE.
_DEPTH_CACHE_DIR = Path(os.environ.get(
    "LAMAHAT_DEPTH_CACHE", Path.home() / ".cache" / "lamahat_depth"))


def depth_cache_path(image_path) -> "Path":
    """Persistent depth-cache path for a source image: a hashed filename inside a
    dedicated cache dir (NOT a sibling of the source). Keyed by absolute path so
    distinct sources never collide."""
    p = Path(image_path).resolve()
    _DEPTH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:16]
    return _DEPTH_CACHE_DIR / f"{p.stem}.{h}.depth.png"


def ensure_depth_cached(image_path, *, backend: str = "depthanything") -> "Path":
    """Estimate depth for image_path and cache it in the dedicated depth-cache dir.

    Call this once a shot's image is chosen so the GPU/non-deterministic step
    happens off the render hot-path. Returns the cached depth PNG path; no-op if
    it already exists. NEVER writes next to the source image.
    """
    image_path = Path(image_path)
    out = depth_cache_path(image_path)
    if out.exists():
        return out
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(image_path)
    if backend == "classical":
        depth = estimate_depth_classical(img)
    else:
        depth = estimate_depth_depthanything(img)   # near=1, [0,1]
    cv2.imwrite(str(out), (np.clip(depth, 0, 1) * 255).astype(np.uint8))
    return out


def load_depth(depth_png) -> np.ndarray:
    """Load a cached depth PNG back to float32 [0,1], near=1."""
    d = cv2.imread(str(depth_png), cv2.IMREAD_GRAYSCALE)
    if d is None:
        raise FileNotFoundError(depth_png)
    return d.astype(np.float32) / 255.0


def render_shot_parallax(image_path, clip_path, duration_sec, visual,
                         *, depth_png=None, backend: str = "depthanything",
                         warp: str = "auto",
                         fps: int = FPS, out_w: int = OUT_W, out_h: int = OUT_H,
                         **overrides) -> "Path":
    """One-call entry for render.py's dispatch.

    Loads cached depth if present (preferred), else estimates+caches on the fly
    (logs a warning — prebuild should have done it). `overrides` can pin
    motion/intensity/amp_px/depth_softness per shot if the planner wants control.
    """
    image_path = Path(image_path)
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    # Depth resolution: prefer an explicit path, then a sidecar next to the asset
    # (render._build_shot_asset copies it there from the persistent cache); else
    # estimate in-memory.  Crucially we NEVER write a depth file next to the
    # source/asset here — that is what polluted the candidate pools.
    if depth_png is None:
        depth_png = image_path.with_name(image_path.stem + ".depth.png")
    if Path(depth_png).exists():
        depth = load_depth(depth_png)
    else:
        print(f"[motion_parallax] no cached depth for {image_path.name}; "
              f"estimating in-memory (prefer prebuild/render-stage caching).")
        depth = (estimate_depth_classical(img) if backend == "classical"
                 else estimate_depth_depthanything(img))
    p = params_for_visual(visual)
    p.update(overrides)
    if warp != "auto":                 # explicit override beats per-visual default
        p["warp"] = warp
    return render_parallax_clip(img, depth, clip_path, duration_sec,
                                fps=fps, out_w=out_w, out_h=out_h, **p)
