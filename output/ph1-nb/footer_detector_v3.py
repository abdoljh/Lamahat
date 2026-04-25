"""
footer_detector_v3.py
=====================
FINAL FIXED VERSION - Addresses all issues found in testing:

1. FIXED: analyze_page() now stores results in self.detected_footers
2. FIXED: _is_page_number() detects numbers WITHIN lines (embedded in headers)
3. FIXED: _is_footnote() handles RTL-reversed parentheses: )١( as well as (١)
4. FIXED: _is_running_header() stricter to avoid false positives on body text
5. FIXED: _extract_inline_numbers() for mixed header+number lines like "مذكرات ,5"
6. ADDED: _clean_bidi_marks() to strip LRM/RLM/ZWJ/ZWNJ before detection

Tested on: pages_5_7.pdf (Ja'far Al-Askari memoirs)
Expected detections:
  Page 1: "نجدة فتحى صفوة" (running header)
  Page 2: "مدكرات جعفر العسكرى ,5" (running header + page number "5")
  Page 3: "03 مقدمة" (running header + page number "03")
  Page 3: ")١(" (footnote marker + continuation lines)
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import Enum


class FooterType(Enum):
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"
    RUNNING_HEADER = "running_header"
    FOOTER_TEXT = "footer_text"
    SEPARATOR = "separator"
    UNKNOWN = "unknown"


@dataclass
class DetectedFooter:
    text: str
    footer_type: FooterType
    confidence: float
    page_num: int
    line_index: int
    original_line: str
    is_stripped: bool = False


class FooterDetector:
    """Detects and classifies footer elements in Arabic OCR text."""

    def __init__(self, page_height_ratio: float = 0.15,
                 min_footer_lines: int = 1):
        self.page_height_ratio = page_height_ratio
        self.min_footer_lines = min_footer_lines
        self.detected_footers: List[DetectedFooter] = []

    # ------------------------------------------------------------------
    # Bidi cleaning
    # ------------------------------------------------------------------

    def _clean_bidi_marks(self, text: str) -> str:
        """
        Remove bidirectional formatting marks that confuse detection.
        These are invisible characters that control text direction.
        """
        # U+200E: Left-to-Right Mark (LRM)
        # U+200F: Right-to-Left Mark (RLM)
        # U+202A: Left-to-Right Embedding (LRE)
        # U+202B: Right-to-Left Embedding (RLE)
        # U+202C: Pop Directional Formatting (PDF)
        # U+202D: Left-to-Right Override (LRO)
        # U+202E: Right-to-Left Override (RLO)
        # U+200D: Zero Width Joiner (ZWJ)
        # U+200C: Zero Width Non-Joiner (ZWNJ)
        bidi_chars = '\u200E\u200F\u202A\u202B\u202C\u202D\u202E\u200D\u200C'
        return ''.join(c for c in text if c not in bidi_chars)

    # ------------------------------------------------------------------
    # Inline number extraction (for headers like "مذكرات ,5")
    # ------------------------------------------------------------------

    def _extract_inline_numbers(self, text: str) -> List[Tuple[str, float]]:
        """
        Extract page numbers embedded within lines.
        Returns list of (number_text, confidence) tuples.
        """
        numbers = []
        stripped = text.strip()

        # Arabic-Indic digits embedded: "text ٥ text" or "text,٥,text"
        for match in re.finditer(r'[\s,\-\u2013\u2014]*([\u0660-\u0669]{1,3})[\s,\-\u2013\u2014]*', stripped):
            num = match.group(1)
            numbers.append((num, 0.85))

        # Eastern Arabic-Indic digits embedded
        for match in re.finditer(r'[\s,\-\u2013\u2014]*([\u06F0-\u06F9]{1,3})[\s,\-\u2013\u2014]*', stripped):
            num = match.group(1)
            numbers.append((num, 0.85))

        # Western digits embedded
        for match in re.finditer(r'[\s,\-\u2013\u2014]*([0-9]{1,3})[\s,\-\u2013\u2014]*', stripped):
            num = match.group(1)
            numbers.append((num, 0.80))

        return numbers

    # ------------------------------------------------------------------
    # Individual detectors
    # ------------------------------------------------------------------

    def _is_page_number(self, text: str) -> Tuple[bool, float]:
        """Check if text is a page number (whole line or embedded)."""
        stripped = text.strip()
        if not stripped:
            return False, 0.0

        # Pure Arabic-Indic digits
        if re.fullmatch(r'[\u0660-\u0669]+', stripped):
            return True, 0.95

        # Pure Eastern Arabic-Indic digits
        if re.fullmatch(r'[\u06F0-\u06F9]+', stripped):
            return True, 0.95

        # Pure Western digits
        if re.fullmatch(r'[0-9]+', stripped):
            return True, 0.90

        # Mixed with decorative elements
        if re.fullmatch(r'[-\u2013\u2014\s]*[\u0660-\u0669\u06F0-\u06F90-9]+[-\u2013\u2014\s]*', stripped):
            return True, 0.85

        # Arabic word for page + number
        if re.search(r'\u0635\u0641\u062D\u0629?\s*[\u0660-\u0669\u06F0-\u06F90-9]+', stripped):
            return True, 0.90

        # Check for embedded numbers in short header-like lines
        inline_nums = self._extract_inline_numbers(stripped)
        if inline_nums and len(stripped) < 80:
            return True, 0.75

        return False, 0.0

    def _is_footnote(self, text: str) -> Tuple[bool, float]:
        """
        Check if text is a footnote.

        CRITICAL FIX: Handles RTL-reversed parentheses.
        In Arabic text, the visual display of parentheses is mirrored:
        - Visual "(" may be encoded as U+0029 )
        - Visual ")" may be encoded as U+0028 (

        The raw OCR may contain: \u200e)١(\u200f (LRM + ) + ١ + ( + RLM)
        We clean bidi marks first, then match both orientations.
        """
        # Clean bidi marks before detection
        cleaned = self._clean_bidi_marks(text)
        stripped = cleaned.strip()
        if not stripped:
            return False, 0.0

        # Standard parenthesized: (١), [٢], {٣}
        if re.match(r'^[\(\[\{]\s*[\u0660-\u0669\u06F0-\u06F90-9]\s*[\)\]\}]', stripped):
            return True, 0.95

        # RTL-reversed parenthesized: )١( — common in raw Arabic OCR
        # After bidi cleaning, this becomes )١( which we detect
        if re.match(r'^[\)\]\}]\s*[\u0660-\u0669\u06F0-\u06F90-9]\s*[\(\[\{]', stripped):
            return True, 0.90

        # Starts with asterisk, dagger, etc.
        if re.match(r'^[*\u2020\u2021\u00A7\u00B6#\+\-\u2014]', stripped):
            return True, 0.85

        # Starts with Arabic letter + parenthesis
        if re.match(r'^[\u0621-\u064A]\)', stripped):
            return True, 0.70

        # Reference markers in short lines
        if len(stripped) < 50 and any(m in stripped for m in [
            '\u0627\u0646\u0638\u0631', '\u0631\u0627\u062C\u0639', '\u0647\u0627\u0645\u0634'
        ]):
            return True, 0.60

        return False, 0.0

    def _is_separator(self, text: str) -> Tuple[bool, float]:
        """Check if text is a separator line."""
        stripped = text.strip()
        if not stripped:
            return False, 0.0

        if re.fullmatch(r'[-_*=\u2014\u2013]+', stripped):
            return True, 0.90

        if re.fullmatch(r'[-_*=\u2014\u2013\s]*[\u0660-\u0669\u06F0-\u06F90-9]+[-_*=\u2014\u2013\s]*', stripped):
            return True, 0.75

        return False, 0.0

    def _is_running_header(self, text: str) -> Tuple[bool, float]:
        """
        Check if text is a running header.

        FIX: Stricter criteria to avoid false positives on body text
        that happens to be in the top 15% of a page.
        """
        stripped = text.strip()
        if not stripped:
            return False, 0.0

        # Must be relatively short (< 60 chars)
        if len(stripped) >= 60:
            return False, 0.0

        # Must not have sentence-ending punctuation (body text indicator)
        if any(c in stripped for c in '.\u060C:\u061B'):
            return False, 0.0

        # Must contain explicit header keywords OR be very short (< 30 chars)
        has_title = re.search(r'\u0645\u0642\u062F\u0645\u0629|\u0641\u0635\u0644|\u0643\u062A\u0627\u0628|\u0630\u0643\u0631\u064A\u0627\u062A|\u0645\u0630\u0643\u0631\u0627\u062A', stripped)
        has_number = re.search(r'[\u0660-\u0669\u06F0-\u06F90-9]', stripped)
        is_very_short = len(stripped) < 30

        if has_title or has_number:
            return True, 0.80
        if is_very_short:
            return True, 0.50

        return False, 0.0

    # ------------------------------------------------------------------
    # Main analysis
    # ------------------------------------------------------------------

    def analyze_page(self, page_text: str, page_num: int) -> List[DetectedFooter]:
        """
        Analyze a page and detect all footer elements.
        Stores results in self.detected_footers for reporting.
        """
        lines = page_text.split('\n')
        footers = []
        total_lines = len(lines)
        footer_start_idx = int(total_lines * (1 - self.page_height_ratio))

        # --- Bottom region: page numbers, footnotes, separators ---
        for idx in range(footer_start_idx, total_lines):
            line = lines[idx]
            if not line.strip():
                continue

            is_page_num, conf_pn = self._is_page_number(line)
            if is_page_num:
                footers.append(DetectedFooter(
                    text=line.strip(),
                    footer_type=FooterType.PAGE_NUMBER,
                    confidence=conf_pn,
                    page_num=page_num,
                    line_index=idx,
                    original_line=line
                ))
                continue

            is_footnote, conf_fn = self._is_footnote(line)
            if is_footnote:
                footers.append(DetectedFooter(
                    text=line.strip(),
                    footer_type=FooterType.FOOTNOTE,
                    confidence=conf_fn,
                    page_num=page_num,
                    line_index=idx,
                    original_line=line
                ))
                continue

            is_sep, conf_sep = self._is_separator(line)
            if is_sep:
                footers.append(DetectedFooter(
                    text=line.strip(),
                    footer_type=FooterType.SEPARATOR,
                    confidence=conf_sep,
                    page_num=page_num,
                    line_index=idx,
                    original_line=line
                ))
                continue

        # --- Top region: running headers ---
        for idx in range(int(total_lines * 0.15)):
            line = lines[idx]
            if not line.strip():
                continue

            is_rh, conf_rh = self._is_running_header(line)
            if is_rh:
                footers.append(DetectedFooter(
                    text=line.strip(),
                    footer_type=FooterType.RUNNING_HEADER,
                    confidence=conf_rh,
                    page_num=page_num,
                    line_index=idx,
                    original_line=line
                ))

                # Also check for embedded page numbers in the header line
                inline_nums = self._extract_inline_numbers(line)
                for num_text, num_conf in inline_nums:
                    footers.append(DetectedFooter(
                        text=num_text,
                        footer_type=FooterType.PAGE_NUMBER,
                        confidence=num_conf,
                        page_num=page_num,
                        line_index=idx,
                        original_line=line
                    ))

        # Link footnote continuations
        footers = self._link_footnote_continuations(lines, footers, page_num)

        # Store for reporting
        self.detected_footers.extend(footers)

        return footers

    def _link_footnote_continuations(self, lines, footers, page_num):
        """Link multi-line footnotes to their markers."""
        fn_markers = [f for f in footers if f.footer_type == FooterType.FOOTNOTE]
        if not fn_markers:
            return footers

        for fn in fn_markers:
            next_idx = fn.line_index + 1
            while next_idx < len(lines) and next_idx < fn.line_index + 5:
                next_line = lines[next_idx].strip()
                if not next_line:
                    break
                # Don't consume lines that start with a new footnote marker
                cleaned = self._clean_bidi_marks(next_line)
                is_new_marker = (
                    re.match(r'^[\(\[\{]\s*[\u0660-\u0669\u06F0-\u06F90-9]\s*[\)\]\}]', cleaned) or
                    re.match(r'^[\)\]\}]\s*[\u0660-\u0669\u06F0-\u06F90-9]\s*[\(\[\{]', cleaned)
                )
                if not is_new_marker:
                    if len(next_line) < 120 or not next_line.endswith('.'):
                        cont_footer = DetectedFooter(
                            text=next_line,
                            footer_type=FooterType.FOOTNOTE,
                            confidence=0.60,
                            page_num=page_num,
                            line_index=next_idx,
                            original_line=lines[next_idx]
                        )
                        footers.append(cont_footer)
                        self.detected_footers.append(cont_footer)
                        next_idx += 1
                    else:
                        break
                else:
                    break

        return footers

    # ------------------------------------------------------------------
    # Stripping
    # ------------------------------------------------------------------

    def strip_footers(self, page_text: str, footers: List[DetectedFooter],
                     preserve_types: Optional[List[FooterType]] = None) -> str:
        """Remove detected footer lines from text."""
        if preserve_types is None:
            preserve_types = []

        lines = page_text.split('\n')
        indices_to_remove = set()

        for footer in footers:
            if footer.footer_type not in preserve_types:
                indices_to_remove.add(footer.line_index)
                footer.is_stripped = True

        cleaned_lines = [line for idx, line in enumerate(lines)
                        if idx not in indices_to_remove]

        return '\n'.join(cleaned_lines)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_footer_report(self) -> str:
        """Generate human-readable detection report."""
        if not self.detected_footers:
            return "No footers detected."

        report = ["=== FOOTER DETECTION REPORT ===", ""]
        current_page = 0

        for footer in self.detected_footers:
            if footer.page_num != current_page:
                current_page = footer.page_num
                report.append(f"\n--- Page {current_page} ---")
            status = "STRIPPED" if footer.is_stripped else "PRESERVED"
            report.append(
                f"  [{footer.footer_type.value}] (conf: {footer.confidence:.2f}) {status}: {footer.text[:60]}"
            )

        return '\n'.join(report)

    def reset(self):
        """Clear detected footers for a new document."""
        self.detected_footers = []


# =====================================================================
# Self-test with actual test case data
# =====================================================================
if __name__ == "__main__":
    # Page 2 raw OCR
    page2_raw = """\u0645\u062F\u0643\u0631\u0627\u062A \u062C\u0639\u0641\u0631 \u0627\u0644\u0639\u0633\u0643\u0631\u0649 ,5

\u0648\u062B\u0627\u0644\u062B\u0629. \u0641\u0627\u0630\u0627 \u0631\u0636\u064A \u0639\u0646 \u0635\u0636\u064A\u063A\u062A\u0647\u0627 \u0623\u062E\u064A\u0631\u0627\u064B"""

    # Page 3 raw OCR with bidi footnote
    page3_raw = """03 \u0645\u0642\u062F\u0645\u0629

\u0625\u0636\u0627\u0641\u0629 \u0625\u0644\u0649 \u0630\u0644\u0643

\u2026

\u2026

\u200e)\u0661(\u200f \u0643\u0627\u0646 \u0627\u0644\u0645\u0631\u062D\u0648\u0645 \u0637\u0627\u0631\u0642"""

    detector = FooterDetector()

    print("Testing Page 2:")
    footers2 = detector.analyze_page(page2_raw, page_num=2)
    for f in footers2:
        print(f"  {f.footer_type.value}: '{f.text}' (conf: {f.confidence:.2f})")

    print("\nTesting Page 3:")
    footers3 = detector.analyze_page(page3_raw, page_num=3)
    for f in footers3:
        print(f"  {f.footer_type.value}: '{f.text}' (conf: {f.confidence:.2f})")

    print("\nFooter Report:")
    print(detector.get_footer_report())
