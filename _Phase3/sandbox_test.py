"""
Sandbox test: render a 4.3 s section_mark clip with the new section_accent
motion, then probe the output with ffprobe and sample a few frames to
verify the zoom ramp.
"""
import subprocess
import sys
from pathlib import Path

# Build a flat module from render.py that doesn't need phase3.align etc.
# We only need _png_to_clip and the motion filters — strip the top-of-file
# imports of sibling modules and run the rest.
src = Path(__file__).parent / "phase3" / "render.py"
text = src.read_text()
import re
# Strip sibling imports: lines `from .plan import Shot` and the multi-
# line `from .typography import ( ... )` block. Comment them out by
# replacing with `pass` so line numbers in tracebacks stay close.
lines = text.splitlines()
out_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    if line.startswith("from .plan ") or line.startswith("from .typography "):
        # consume until the matching close-paren or end of single-line import
        if "(" in line and ")" not in line:
            while i < len(lines) and ")" not in lines[i]:
                i += 1
        i += 1
        out_lines.append("# (sibling import stripped for sandbox)")
        continue
    out_lines.append(line)
    i += 1
stripped = "\n".join(out_lines)
import types
ns_mod = types.ModuleType("render_isolated")
ns_mod.__file__ = str(src)
sys.modules["render_isolated"] = ns_mod
ns = ns_mod.__dict__
exec(compile(stripped, str(src), "exec"), ns)
_png_to_clip = ns["_png_to_clip"]
_MOTION_FILTERS = ns["_MOTION_FILTERS"]
print(f"loaded; motion keys: {sorted(_MOTION_FILTERS.keys())}")

png = Path("/tmp/sm_test.png")
out = Path("/tmp/sm_test_accent.mp4")
dur = 4.3   # matches shot 9 in the latest plan

clip = _png_to_clip(
    png, out,
    duration=dur,
    fps=25, width=1920, height=1080,
    motion="section_accent",
)
print(f"rendered {clip} (target {dur}s)")

# ── Probe ──
r = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries",
     "format=duration,bit_rate:stream=codec_name,width,height,r_frame_rate,nb_frames",
     "-of", "default=noprint_wrappers=1", str(clip)],
    capture_output=True, text=True)
print("── ffprobe ──")
print(r.stdout.strip())

# Sample frames at 0.00s, 0.16s (mid-ramp, on=4), 0.32s (post-ramp), 2.00s
samples = [0.00, 0.16, 0.32, 2.00]
print("\n── frame samples ──")
for t in samples:
    fpath = f"/tmp/sm_frame_{int(t*1000):04d}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{t:.3f}", "-i", str(clip),
         "-frames:v", "1", fpath],
        check=True)
    sz = Path(fpath).stat().st_size
    print(f"  t={t:.2f}s  →  {fpath}  ({sz} bytes)")

# Verify the static_hold path STILL works (regression check)
out2 = Path("/tmp/sm_test_static.mp4")
_png_to_clip(
    png, out2,
    duration=dur,
    fps=25, width=1920, height=1080,
    motion="static_hold",
)
r2 = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries",
     "format=duration:stream=nb_frames",
     "-of", "default=noprint_wrappers=1", str(out2)],
    capture_output=True, text=True)
print("\n── regression: static_hold path still works ──")
print(r2.stdout.strip())
