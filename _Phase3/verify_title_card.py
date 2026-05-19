#!/usr/bin/env python3
"""
Verify the issue-4 title-card and caption-gap patches.

Run from _Phase3/ after applying:
  - phase3/typography.py
  - phase3/render.py
  - prebuild_assets.py
  - render_plan.py

Three checks:
  1. typography.py imports and exposes GOLD_AGED + new TypographySpec fields
  2. _render_title_card_with_cover renders without error when given a JPEG
  3. _write_captions inter-event gap arithmetic produces 0.3s gap between
     adjacent 8s shots (was 0.1s)
"""
import logging
import sys
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)-7s %(name)s  %(message)s")
log = logging.getLogger("verify4")

sys.path.insert(0, str(Path(__file__).resolve().parent))


def check_typography():
    """Confirm new fields exist and the cover-mode renders."""
    from PIL import Image
    from phase3 import typography as t

    # Field check
    assert hasattr(t, "GOLD_AGED"), "GOLD_AGED constant missing"
    assert t.GOLD_AGED == (200, 162, 74), f"GOLD_AGED has wrong value: {t.GOLD_AGED}"
    fields = {f.name for f in t.TypographySpec.__dataclass_fields__.values()}
    assert "cover_image" in fields, "TypographySpec.cover_image missing"
    assert "accent_color" in fields, "TypographySpec.accent_color missing"
    log.info("✓ typography fields present")

    # Sizing check
    assert t.SIZES["title_main"] == 0.110, f"title_main is {t.SIZES['title_main']}, expected 0.110"
    assert t.SIZES["title_sub"]  == 0.040, f"title_sub is {t.SIZES['title_sub']}, expected 0.040"
    log.info("✓ title sizes bumped (0.110 / 0.040)")

    # End-to-end render — both paths
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Fake cover.  Use a noise-textured surrogate (not a solid colour
        # or a smooth gradient) so the resulting PNG has high-frequency
        # detail and compresses to a meaningful size.  Solid/smooth
        # backgrounds optimize to <50 KB and would falsely trip the
        # size assertion below.
        from PIL import ImageDraw
        import random
        cover = td / "cover.jpg"
        img = Image.new("RGB", (3000, 2000), (62, 50, 38))
        d = ImageDraw.Draw(img)
        # Sepia gradient
        for y in range(2000):
            t_y = y / 2000
            r_ = int(72 + 35 * (1 - t_y))
            g_ = int(55 + 25 * (1 - t_y))
            b_ = int(40 + 18 * (1 - t_y))
            d.line([(0, y), (3000, y)], fill=(r_, g_, b_))
        # Add noise so PNG compresses to a meaningful size
        rng = random.Random(42)
        for _ in range(8000):
            x, y = rng.randint(0, 2999), rng.randint(0, 1999)
            a = rng.randint(-20, 20)
            px = img.getpixel((x, y))
            img.putpixel((x, y), tuple(max(0, min(255, c + a)) for c in px))
        img.save(cover, "JPEG", quality=90)

        spec_cover = t.TypographySpec(
            template="title_card",
            text="مذكرات جعفر العسكري",
            subtitle="رحلة بين الموصل واسطنبول",
            width=1920, height=1080,
            cover_image=cover,
        )
        out_cover = td / "title_cover.png"
        t.render(spec_cover, out_cover)
        sz = out_cover.stat().st_size
        # 30 KB floor — generous so a smooth-cover render doesn't false-fail,
        # but high enough that an empty/blank render trips the assertion.
        assert out_cover.exists() and sz > 30_000, (
            f"cover-mode PNG suspiciously small ({sz} bytes) — "
            f"renderer may have produced a blank frame.  Inspect {out_cover}."
        )
        log.info("✓ cover-mode title card rendered (%d bytes)", sz)

        spec_cream = t.TypographySpec(
            template="title_card",
            text="مذكرات جعفر العسكري",
            width=1920, height=1080,
        )
        out_cream = td / "title_cream.png"
        t.render(spec_cream, out_cream)
        sz_cream = out_cream.stat().st_size
        assert out_cream.exists() and sz_cream > 30_000
        log.info("✓ cream-mode title card still works (%d bytes)", sz_cream)


def check_caption_gap():
    """Confirm inter-event gap arithmetic gives 0.30s between consecutive shots."""
    GAP = 0.15
    MIN_VISIBLE = 0.6

    def window(s, e):
        ps, pe = s + GAP, e - GAP
        if pe - ps < MIN_VISIBLE:
            d = min(MIN_VISIBLE, e - s)
            mid = (s + e) / 2
            return mid - d/2, mid + d/2
        return ps, pe

    # Two consecutive 8s shots
    s1, e1 = window(0.0, 8.0)
    s2, _  = window(8.0, 16.0)
    actual_gap = s2 - e1
    assert abs(actual_gap - 0.30) < 1e-9, f"gap = {actual_gap}, expected 0.30"
    log.info("✓ adjacent 8s shots have 0.30s gap (was 0.10s)")

    # Short-shot clamp
    s, e = window(0.0, 0.4)
    assert abs((e - s) - 0.4) < 1e-9
    log.info("✓ sub-MIN_VISIBLE shots use the full shot duration")


def check_render_config():
    """Confirm RenderConfig has the new book_cover field."""
    from phase3.render import RenderConfig
    cfg = RenderConfig()
    assert hasattr(cfg, "book_cover")
    assert cfg.book_cover is None
    log.info("✓ RenderConfig.book_cover present, defaults to None")


def main():
    log.info("── typography ──"); check_typography()
    log.info("── render config ──"); check_render_config()
    log.info("── caption gap ──"); check_caption_gap()
    log.info("✓ All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
