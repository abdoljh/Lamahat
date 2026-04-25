"""
Phase 1 — FooterDetector
Classifies and strips footer/header elements from scanned Arabic OCR pages.

Detected element types
──────────────────────
PAGE_NUMBER    — standalone or embedded digit lines (Arabic-Indic, Eastern, Western)
FOOTNOTE       — lines starting with parenthesised numbers, asterisks, daggers;
                 handles RTL-reversed parentheses )١( common in raw Arabic OCR
RUNNING_HEADER — short top-of-page lines without sentence punctuation, with an
                 embedded number or known header keyword
SEPARATOR      — horizontal rule lines (dashes, underscores, equals signs)
FOOTER_TEXT    — generic short footer lines not matching other categories

Algorithm
─────────
For each page:
  • Top 15 % of lines  → check for RUNNING_HEADER
  • Bottom 15 % of lines → check for PAGE_NUMBER, FOOTNOTE, SEPARATOR
  • Multi-line footnote continuations are linked to their marker lines.

Adapted from output/ph1-nb/footer_detector_v3.py (tested on Al-Askari Memoirs,
pages_5_7.pdf).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class FooterType(Enum):
    PAGE_NUMBER    = "page_number"
    FOOTNOTE       = "footnote"
    RUNNING_HEADER = "running_header"
    FOOTER_TEXT    = "footer_text"
    SEPARATOR      = "separator"
    UNKNOWN        = "unknown"


@dataclass
class DetectedFooter:
    text:          str
    footer_type:   FooterType
    confidence:    float
    page_num:      int
    line_index:    int
    original_line: str
    is_stripped:   bool = False


class FooterDetector:
    """
    Detect and strip running headers, page numbers, footnotes, and separator
    lines from a single page of Arabic OCR output.

    Usage::

        detector = FooterDetector()
        footers  = detector.analyze_page(page_text, page_num)
        cleaned  = detector.strip_footers(page_text, footers)
        detector.reset()          # before the next page
    """

    def __init__(
        self,
        page_height_ratio: float = 0.15,
        min_footer_lines:  int   = 1,
    ):
        self.page_height_ratio  = page_height_ratio
        self.min_footer_lines   = min_footer_lines
        self.detected_footers: List[DetectedFooter] = []

    # ------------------------------------------------------------------ #
    #  Bidi cleaning                                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _clean_bidi_marks(text: str) -> str:
        """Strip invisible bidirectional-control characters before pattern matching."""
        _BIDI = '‎‏‪‫‬‭‮‍‌'
        return ''.join(c for c in text if c not in _BIDI)

    # ------------------------------------------------------------------ #
    #  Inline number extraction                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_inline_numbers(text: str) -> List[Tuple[str, float]]:
        """
        Return (number_string, confidence) pairs for digits embedded in a line.
        Covers Arabic-Indic (U+0660-0669), Eastern Arabic-Indic (U+06F0-06F9),
        and Western ASCII digits.
        """
        numbers: List[Tuple[str, float]] = []
        sep = r'[\s,\-–—]*'
        for pat, conf in [
            (rf'{sep}([٠-٩]{{1,3}}){sep}', 0.85),
            (rf'{sep}([۰-۹]{{1,3}}){sep}', 0.85),
            (rf'{sep}([0-9]{{1,3}}){sep}',            0.80),
        ]:
            for m in re.finditer(pat, text):
                numbers.append((m.group(1), conf))
        return numbers

    # ------------------------------------------------------------------ #
    #  Individual classifiers                                              #
    # ------------------------------------------------------------------ #

    def _is_page_number(self, text: str) -> Tuple[bool, float]:
        s = text.strip()
        if not s:
            return False, 0.0
        # Pure digit lines
        if re.fullmatch(r'[٠-٩]+', s):           return True, 0.95
        if re.fullmatch(r'[۰-۹]+', s):           return True, 0.95
        if re.fullmatch(r'[0-9]+', s):                     return True, 0.90
        # Digits with decorative surroundings
        if re.fullmatch(r'[-–—\s]*[٠-٩۰-۹0-9]+[-–—\s]*', s):
            return True, 0.85
        # Arabic word for "page" + number
        if re.search(r'صفحة?\s*[٠-٩۰-۹0-9]+', s):
            return True, 0.90
        # Embedded number in a short line
        if self._extract_inline_numbers(s) and len(s) < 80:
            return True, 0.75
        return False, 0.0

    def _is_footnote(self, text: str) -> Tuple[bool, float]:
        """
        Detect footnote marker lines.

        Handles RTL-reversed parentheses: in raw Arabic OCR the visual "("
        may be encoded as U+0029 ) and vice versa — yielding )١( instead of (١).
        Bidi marks are cleaned first.
        """
        cleaned = self._clean_bidi_marks(text)
        s = cleaned.strip()
        if not s:
            return False, 0.0
        # Standard: (١), [٢], {٣}  or Western equivalents
        if re.match(r'^[\(\[\{]\s*[٠-٩۰-۹0-9]\s*[\)\]\}]', s):
            return True, 0.95
        # RTL-reversed: )١(
        if re.match(r'^[\)\]\}]\s*[٠-٩۰-۹0-9]\s*[\(\[\{]', s):
            return True, 0.90
        # Asterisk, dagger, or similar typographic markers
        if re.match(r'^[*†‡§¶#\+\-—]', s):
            return True, 0.85
        # Arabic letter + closing paren  e.g.  أ)
        if re.match(r'^[ء-ي]\)', s):
            return True, 0.70
        # Short line with cross-reference keywords (انظر، راجع، هامش)
        if len(s) < 50 and any(kw in s for kw in [
            'انظر',   # انظر
            'راجع',   # راجع
            'هامش',   # هامش
        ]):
            return True, 0.60
        return False, 0.0

    def _is_separator(self, text: str) -> Tuple[bool, float]:
        s = text.strip()
        if not s:
            return False, 0.0
        if re.fullmatch(r'[-_*=—–]+', s):
            return True, 0.90
        if re.fullmatch(r'[-_*=—–\s]*[٠-٩۰-۹0-9]+[-_*=—–\s]*', s):
            return True, 0.75
        return False, 0.0

    def _is_running_header(self, text: str) -> Tuple[bool, float]:
        """
        Detect running headers in the top region of a page.

        Stricter than the cross-page frequency approach in normalizer:
          • Must be < 60 chars
          • Must NOT end with sentence punctuation (., ،, :, ;)
          • Must have an embedded page number OR a known chapter/title keyword
            OR be very short (< 30 chars, handled as likely title fragment)
        """
        s = text.strip()
        if not s or len(s) >= 60:
            return False, 0.0
        if any(c in s for c in '.،:؛'):
            return False, 0.0
        has_title = bool(re.search(
            r'مقدمة'   # مقدمة
            r'|فصل'               # فصل
            r'|كتاب'         # كتاب
            r'|ذكريات'  # ذكريات
            r'|مذكرات', # مذكرات
            s,
        ))
        has_number = bool(re.search(r'[٠-٩۰-۹0-9]', s))
        if has_title or has_number:
            return True, 0.80
        if len(s) < 30:
            return True, 0.50
        return False, 0.0

    # ------------------------------------------------------------------ #
    #  Multi-line footnote continuation                                    #
    # ------------------------------------------------------------------ #

    def _link_footnote_continuations(
        self,
        lines: List[str],
        footers: List[DetectedFooter],
        page_num: int,
    ) -> List[DetectedFooter]:
        """Attach continuation lines that follow a footnote marker."""
        fn_markers = [f for f in footers if f.footer_type == FooterType.FOOTNOTE]
        for fn in fn_markers:
            idx = fn.line_index + 1
            while idx < len(lines) and idx < fn.line_index + 5:
                line = lines[idx].strip()
                if not line:
                    break
                cleaned = self._clean_bidi_marks(line)
                is_new = (
                    re.match(r'^[\(\[\{]\s*[٠-٩۰-۹0-9]\s*[\)\]\}]', cleaned) or
                    re.match(r'^[\)\]\}]\s*[٠-٩۰-۹0-9]\s*[\(\[\{]', cleaned)
                )
                if is_new:
                    break
                if len(line) < 120 or not line.endswith('.'):
                    cont = DetectedFooter(
                        text=line,
                        footer_type=FooterType.FOOTNOTE,
                        confidence=0.60,
                        page_num=page_num,
                        line_index=idx,
                        original_line=lines[idx],
                    )
                    footers.append(cont)
                    self.detected_footers.append(cont)
                    idx += 1
                else:
                    break
        return footers

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def analyze_page(self, page_text: str, page_num: int) -> List[DetectedFooter]:
        """
        Classify all footer/header elements on one page.

        Results are appended to ``self.detected_footers`` and also returned.
        Call ``reset()`` between documents.
        """
        lines       = page_text.split('\n')
        total       = len(lines)
        footer_start = int(total * (1 - self.page_height_ratio))
        header_end   = int(total * self.page_height_ratio)
        footers: List[DetectedFooter] = []

        # ── Bottom region: footnotes → page numbers → separators ────────
        # Footnote is checked first: a line like (١) is a footnote marker,
        # NOT a page number, even though it contains an Arabic digit.
        for idx in range(footer_start, total):
            line = lines[idx]
            if not line.strip():
                continue
            for check, ftype in [
                (self._is_footnote,    FooterType.FOOTNOTE),
                (self._is_page_number, FooterType.PAGE_NUMBER),
                (self._is_separator,   FooterType.SEPARATOR),
            ]:
                detected, conf = check(line)
                if detected:
                    footers.append(DetectedFooter(
                        text=line.strip(), footer_type=ftype,
                        confidence=conf, page_num=page_num,
                        line_index=idx, original_line=line,
                    ))
                    break

        # ── Top region: running headers ──────────────────────────────────
        for idx in range(header_end):
            line = lines[idx]
            if not line.strip():
                continue
            detected, conf = self._is_running_header(line)
            if detected:
                footers.append(DetectedFooter(
                    text=line.strip(), footer_type=FooterType.RUNNING_HEADER,
                    confidence=conf, page_num=page_num,
                    line_index=idx, original_line=line,
                ))
                # Embedded page number inside a header line (e.g. "مذكرات ,5")
                for num_text, num_conf in self._extract_inline_numbers(line):
                    footers.append(DetectedFooter(
                        text=num_text, footer_type=FooterType.PAGE_NUMBER,
                        confidence=num_conf, page_num=page_num,
                        line_index=idx, original_line=line,
                    ))

        footers = self._link_footnote_continuations(lines, footers, page_num)
        self.detected_footers.extend(footers)
        return footers

    def strip_footers(
        self,
        page_text: str,
        footers: List[DetectedFooter],
        preserve_types: Optional[List[FooterType]] = None,
    ) -> str:
        """Remove footer lines from ``page_text``, skipping any ``preserve_types``."""
        if preserve_types is None:
            preserve_types = []
        lines = page_text.split('\n')
        remove = {
            f.line_index for f in footers
            if f.footer_type not in preserve_types
        }
        for f in footers:
            if f.footer_type not in preserve_types:
                f.is_stripped = True
        return '\n'.join(line for i, line in enumerate(lines) if i not in remove)

    def get_footer_report(self) -> str:
        """Human-readable summary of all detected footer elements."""
        if not self.detected_footers:
            return "No footers detected."
        lines = ["=== FOOTER DETECTION REPORT ===", ""]
        cur_page = 0
        for f in self.detected_footers:
            if f.page_num != cur_page:
                cur_page = f.page_num
                lines.append(f"\n--- Page {cur_page} ---")
            status = "STRIPPED" if f.is_stripped else "PRESERVED"
            lines.append(
                f"  [{f.footer_type.value}] (conf: {f.confidence:.2f}) {status}: {f.text[:60]}"
            )
        return '\n'.join(lines)

    def reset(self) -> None:
        """Clear accumulated detections (call between documents)."""
        self.detected_footers.clear()
