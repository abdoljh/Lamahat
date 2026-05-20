"""Generate a stand-in section_mark PNG for sandbox testing."""
from PIL import Image, ImageDraw, ImageFont
import sys

W, H = 1920, 1080
img = Image.new("RGB", (W, H), (245, 240, 232))  # cream
d = ImageDraw.Draw(img)
# Draw a thin charcoal rule and a placeholder title
try:
    f_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 96)
    f_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 36)
except Exception:
    f_big = ImageFont.load_default()
    f_small = ImageFont.load_default()
title = "PART TWO"
sub = "The Young Officer"
tw = d.textbbox((0, 0), title, font=f_big)
d.text(((W - tw[2]) / 2, H / 2 - 100), title, fill=(40, 35, 30), font=f_big)
sw = d.textbbox((0, 0), sub, font=f_small)
d.text(((W - sw[2]) / 2, H / 2 + 30), sub, fill=(60, 55, 50), font=f_small)
d.line([(W * 0.4, H * 0.62), (W * 0.6, H * 0.62)], fill=(140, 110, 50), width=3)
img.save(sys.argv[1])
print(f"wrote {sys.argv[1]} ({W}x{H})")
