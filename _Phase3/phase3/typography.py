"""
Phase 3 — Arabic typography card renderer (dispatcher).

This module is the public entry point.  It:
  1. Re-exports the symbols `render.py` and external callers depend on
     (TypographySpec, palette constants, font helpers).
  2. Routes `render(spec, out_path)` to the family-specific renderer
     registry based on `spec.family`.

Family implementations live in sibling modules:
  - typography_a.py  →  Family A (Aljazeera-editorial, cream/charcoal)
  - typography_b.py  →  Family B (Netflix-doc cinematic, dark gradient)
  - typography_c.py  →  Family C (manuscript, sepia + ornament) [pending]

History: refactored from a single 1228-line file in the §15.2 work.
The public surface — `render`, `TypographySpec`, and the helper/palette
exports `render.py` imports — is preserved verbatim.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import typography_a, typography_b
from .typography_common import (
    # Public re-exports (render.py imports these by name)
    CHARCOAL, CREAM_DEEP, CREAM_LIGHT, CREAM_MEDIUM, GRAPHITE, WARM_GREY,
    FONT_PATHS,
    TypographySpec, TemplateName,
    _apply_grain, _draw_text_rtl, _font, _measure,
    # Family A also exports these constants used elsewhere
    GOLD_AGED,
    # Family B palette (re-exported for callers that want to introspect)
    DARK_BASE, DARK_TOP, DARK_CARD,
    OFF_WHITE, OFF_WHITE_DIM, GOLD_DEEP,
)

log = logging.getLogger(__name__)


# ── Family registry ────────────────────────────────────────────────────── #

_FAMILY_REGISTRIES = {
    "A": typography_a.RENDERERS,
    "B": typography_b.RENDERERS,
    # "C" lands in the next patch (§15.2 part 2)
}


def render(spec: TypographySpec, out_path: Path) -> Path:
    """
    Render one typography card to disk and return the output path.

    Family selection:
      - spec.family ∈ {"A", "B"}                  →  that family's registry
      - unknown family (incl. "C" until shipped)  →  Family A (warns once)
      - unknown template within a family          →  pull_quote (Family A's
                                                     historical fallback)
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    family = (spec.family or "A").upper()
    registry = _FAMILY_REGISTRIES.get(family)
    if registry is None:
        log.warning("Unknown typography family %r — falling back to Family A. "
                    "Available: %s", spec.family,
                    sorted(_FAMILY_REGISTRIES.keys()))
        registry = _FAMILY_REGISTRIES["A"]

    # Per-family fallback to that family's pull_quote, which is the
    # safest "generic typography card" shape in every family.
    renderer = registry.get(spec.template) or registry.get("pull_quote")
    image = renderer(spec)
    image.save(out_path, format="PNG", optimize=True)
    log.debug("Rendered %s/%s typography card → %s",
              family, spec.template, out_path.name)
    return out_path


__all__ = [
    # Primary API
    "render", "TypographySpec", "TemplateName",
    # Palette (Family A, kept for backward compat)
    "CHARCOAL", "CREAM_DEEP", "CREAM_LIGHT", "CREAM_MEDIUM",
    "GRAPHITE", "WARM_GREY", "GOLD_AGED",
    # Palette (Family B)
    "DARK_BASE", "DARK_TOP", "DARK_CARD",
    "OFF_WHITE", "OFF_WHITE_DIM", "GOLD_DEEP",
    # Font + helpers (kept for backward compat)
    "FONT_PATHS",
    "_apply_grain", "_draw_text_rtl", "_font", "_measure",
]
