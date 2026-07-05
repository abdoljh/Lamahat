"""
Phase 3 — Script section parser.

Splits an Arabic video script into its structural sections and
estimates per-section video durations proportional to character count.

Boundary detection, highest priority first (PHASE3.md §7.2):

1. **Sidecar** — a `sections.json` next to the script file (pass
   `script_path` to parse_sections).  Hand-written or emitted by a
   future Phase 1b; the explicit override when a script's structure
   defies detection.
2. **Legacy regexes** — the rigid v1 template headers (النقطة الأولى…,
   الخاتمة, تقديم الكتاب).
3. **Short-isolated-line heuristic** — the current Phase 1b summariser
   emits descriptive headers ("من الموصل إلى الاستانة — رحلة التحديث…")
   instead of the template ones; those are short lines (< 80 chars)
   sitting alone between blank lines.  Any such line after the title
   becomes a `point_N` boundary.  Without this, a 5-section script
   collapsed to opening/closing (66/22 shots on the 88-shot production
   plan) and per-section grading had nothing to key on.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# ── Section header patterns (Arabic) ────────────────────────────────────── #
# Each tuple: (compiled regex matching the *start* of a line, section_id)
# Order matters — more specific patterns first.
_SECTION_HEADERS: list[tuple[str, str]] = [
    (r"^النقطة\s+الأولى",   "point_1"),
    (r"^النقطة\s+الثانية",  "point_2"),
    (r"^النقطة\s+الثالثة",  "point_3"),
    (r"^النقطة\s+الرابعة",  "point_4"),
    (r"^النقطة\s+الخامسة",  "point_5"),
    (r"^الخاتمة",           "closing"),
    (r"^تقديم\s+الكتاب",    "cta"),
]

_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.UNICODE), sid) for p, sid in _SECTION_HEADERS
]


@dataclass
class ScriptSection:
    section_id: str   # e.g. "opening", "point_1", "closing", "cta"
    title: str        # first line of the section (the header text)
    text: str         # full section text including header
    char_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.char_count = len(self.text.strip())


# Short-isolated-line heuristic bounds
_HEADER_MAX_CHARS = 80


def _load_sidecar(script_path: Path | None) -> list[tuple[int, str]] | None:
    """Load boundaries from `sections.json` next to the script, if present.

    Format: {"sections": [{"section_id": "point_1", "start_line": 8}, …]}
    with 0-indexed start lines; each section runs to the next boundary.
    The opening (before the first boundary) is implicit as usual.
    """
    if script_path is None:
        return None
    sidecar = Path(script_path).with_name("sections.json")
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        entries = data.get("sections") or []
        out = sorted(
            (int(e["start_line"]), str(e["section_id"])) for e in entries
        )
        log.info("Section sidecar %s: %d boundaries", sidecar, len(out))
        return out
    except Exception as exc:  # noqa: BLE001 — bad sidecar must not kill a run
        log.warning("Unreadable sections sidecar %s (%s) — falling back to "
                    "detection", sidecar, exc)
        return None


def _heuristic_boundaries(lines: list[str]) -> list[int]:
    """Line indices of short isolated lines (a header candidate: < 80
    chars, blank line before and after, after the title line, not the
    trailing **metadata** block)."""
    out: list[int] = []
    first_content = next((i for i, l in enumerate(lines) if l.strip()), 0)
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or i <= first_content:
            continue
        if len(s) >= _HEADER_MAX_CHARS or s.startswith("**"):
            continue
        prev_blank = not lines[i - 1].strip()
        next_blank = i + 1 >= len(lines) or not lines[i + 1].strip()
        if prev_blank and next_blank:
            out.append(i)
    return out


def parse_sections(script_text: str,
                   script_path: Path | None = None) -> list[ScriptSection]:
    """
    Split a Phase-1 Arabic video script into structural sections.

    The opening block (everything before the first recognised header)
    is always returned as section_id='opening'.  `script_path` (optional)
    enables the `sections.json` sidecar override.

    Returns sections in document order.
    """
    lines = script_text.splitlines()

    # 1. Sidecar wins outright.
    sidecar = _load_sidecar(script_path)
    if sidecar is not None:
        boundaries: list[tuple[int, str]] = [
            (i, sid) for i, sid in sidecar if 0 <= i < len(lines)
        ]
    else:
        # 2. Legacy template regexes.
        by_line: dict[int, str] = {}
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            for pattern, sid in _COMPILED:
                if pattern.match(stripped):
                    by_line[i] = sid
                    break
        # 3. Short-isolated-line heuristic fills the gaps the rigid
        # template misses; regex identities win on collisions.
        point_n = 1
        for i in _heuristic_boundaries(lines):
            if i in by_line:
                continue
            by_line[i] = f"point_{point_n}"
            point_n += 1
        boundaries = sorted(by_line.items())
        if len(boundaries) > 1:
            log.info("Section detection: %d boundaries (%s)",
                     len(boundaries),
                     ", ".join(sid for _, sid in boundaries))

    # Sentinel at end
    boundaries.append((len(lines), "_end"))

    sections: list[ScriptSection] = []

    # Opening: everything before the first named section
    first = boundaries[0][0] if boundaries else len(lines)
    opening_text = "\n".join(lines[:first]).strip()
    if opening_text:
        opening_title = lines[0].strip() if lines else "مقدمة"
        sections.append(ScriptSection("opening", opening_title, opening_text))

    # Named sections
    for idx, (start, sid) in enumerate(boundaries[:-1]):
        end = boundaries[idx + 1][0]
        text = "\n".join(lines[start:end]).strip()
        if not text:
            continue
        title = lines[start].strip()
        sections.append(ScriptSection(sid, title, text))

    return sections


def estimate_durations(
    sections: list[ScriptSection],
    total_duration_sec: float,
) -> list[float]:
    """
    Distribute total_duration_sec across sections proportionally to
    their character counts.

    Returns a list of floats (seconds) in the same order as `sections`.
    Each section gets at least 5 seconds.
    """
    total_chars = sum(s.char_count for s in sections) or 1
    return [
        max(5.0, total_duration_sec * s.char_count / total_chars)
        for s in sections
    ]
