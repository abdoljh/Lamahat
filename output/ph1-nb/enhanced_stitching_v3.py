"""
enhanced_stitching_v3.py
=========================
FINAL FIXED version with all bug fixes from testing:

1. FIXED: Detection runs on RAW OCR before LLM correction
2. FIXED: Proper footer list management (stores in detector.detected_footers)
3. FIXED: Handles RTL-reversed parentheses in footnotes
4. FIXED: Stricter running header detection to avoid false positives
5. FIXED: Embedded page numbers in header lines (e.g., "مذكرات ,5")

Usage in notebook:
    from enhanced_stitching_v3 import stitch_pages_with_footers

    stitched_pages, flowing_text, all_footers, all_footnotes = \
        stitch_pages_with_footers(
            raw_pages=optimized_result,      # RAW OCR from Cell 4
            corrected_pages=corrected_pages,  # LLM corrected from Cell 5
            client=client,
            model_name=claude_model
        )
"""

import re
import json
from typing import Tuple, List, Dict

from footer_detector_v3 import FooterDetector, FooterType


def process_boundary_enhanced(prev_content: str,
                               next_content: str,
                               page_num: int,
                               client=None,
                               model_name: str = "claude-haiku-4-5-20251001",
                               strip_headers: bool = True,
                               strip_footers: bool = True,
                               preserve_footnotes: bool = True) -> Tuple[str, str, List]:
    """
    Enhanced boundary processing with footer/header detection.

    Args:
        prev_content: Text content of previous page (already stripped)
        next_content: Text content of next page (already stripped)
        page_num: Current page number
        client: Anthropic client instance
        model_name: Claude model name
        strip_headers: Whether to remove running headers
        strip_footers: Whether to remove footers/page numbers
        preserve_footnotes: Whether to keep footnote content

    Returns:
        (updated_prev_content, updated_next_content, boundary_footers)
    """

    prev_lines = [l for l in prev_content.split('\n') if l.strip()]
    next_lines = [l for l in next_content.split('\n') if l.strip()]

    if not prev_lines or not next_lines or client is None:
        return prev_content, next_content, []

    prev_tail = '\n'.join(prev_lines[-2:])
    next_head = '\n'.join(next_lines[:3])

    # Enhanced prompt with footer-specific fields
    prompt = (
        f"نهاية الصفحة {page_num}:\n{prev_tail}\n\n"
        f"بداية الصفحة {page_num+1}:\n{next_head}\n\n"
        "أجب بـ JSON فقط (لا تضف أي نص خارجه):\n"
        '{"header": نص الترويسة إن وجدت (عنوان كتاب أو فصل أو رقم صفحة) وإلا null,\n'
        ' "footer": نص التذييل إن وجد (رقم صفحة أو هامش) وإلا null,\n'
        ' "join": true إذا كانت الجملة منقطعة بين الصفحتين وتستكمل في الصفحة التالية,\n'
        ' "footnote_span": true إذا كان هناك هامش يمتد عبر الصفحتين\n'
        '}'
    )

    try:
        msg = client.messages.create(
            model=model_name, max_tokens=120,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(m.group()) if m else {}
    except Exception as e:
        print(f"  Boundary {page_num}->{page_num+1} LLM error: {e}")
        data = {}

    # Handle header stripping (from LLM detection)
    header_text = data.get('header')
    if header_text and strip_headers:
        lines = next_content.split('\n')
        for idx, line in enumerate(lines):
            if line.strip() and (header_text.strip() in line or line.strip() in header_text):
                lines[idx] = ''
                break
        next_content = '\n'.join(lines)

    # Handle footer stripping (from LLM detection)
    footer_text = data.get('footer')
    if footer_text and strip_footers:
        lines = prev_content.split('\n')
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if line.strip() and (footer_text.strip() in line or line.strip() in footer_text):
                lines[idx] = ''
                break
        prev_content = '\n'.join(lines)

    # Join split sentence across boundary
    if data.get('join') and not data.get('footnote_span'):
        next_stripped = next_content.lstrip().split('\n')
        first_idx = next((i for i, l in enumerate(next_stripped) if l.strip()), None)
        if first_idx is not None:
            continuation = next_stripped[first_idx].lstrip()
            next_stripped[first_idx] = ''
            prev_content = prev_content.rstrip() + ' ' + continuation
            next_content = '\n'.join(next_stripped)

    return prev_content, next_content, []


def stitch_pages_with_footers(raw_pages: str,
                               corrected_pages: list,
                               client,
                               model_name: str = "claude-haiku-4-5-20251001",
                               config: dict = None) -> Tuple[list, str, List, List]:
    """
    Complete stitching pipeline with footer support.

    KEY FIX: Detection runs on RAW OCR before LLM correction.

    Args:
        raw_pages: Raw OCR output with page markers ("--- Page N ---\n...")
        corrected_pages: List of "--- Page N ---\n..." strings from LLM
        client: Anthropic client instance
        model_name: Claude model name
        config: Configuration dict

    Returns:
        (stitched_pages, flowing_text, all_footers, all_footnotes)
    """
    if config is None:
        config = {
            'strip_page_numbers': True,
            'strip_running_headers': True,
            'strip_separators': True,
            'preserve_footnotes': True,
            'extract_footnotes_separately': True,
        }

    # =====================================================================
    # PHASE 0: Parse raw OCR into pages for detection
    # =====================================================================
    raw_page_blocks = re.split(r'(?=--- Page \d+ ---)', raw_pages)
    raw_contents = []
    for block in raw_page_blocks:
        block = block.strip()
        if not block:
            continue
        m = re.match(r'(--- Page \d+ ---)\n?(.*)', block, re.DOTALL)
        if m:
            raw_contents.append(m.group(2).strip())
        else:
            raw_contents.append(block)

    # =====================================================================
    # PHASE 1: Parse corrected pages
    # =====================================================================
    markers, corrected_contents = [], []
    for block in corrected_pages:
        lines = block.split('\n')
        if lines and re.match(r'--- Page \d+ ---', lines[0]):
            markers.append(lines[0])
            corrected_contents.append('\n'.join(lines[1:]))
        else:
            markers.append('')
            corrected_contents.append(block)

    # =====================================================================
    # PHASE 2: Detect footers on RAW OCR (BEFORE LLM correction)
    # =====================================================================
    print("\n=== Phase 2: Footer Detection on RAW OCR ===")
    detector = FooterDetector()
    all_footers = []
    all_footnotes = []

    for i, raw_content in enumerate(raw_contents):
        page_num = i + 1
        footers = detector.analyze_page(raw_content, page_num)
        all_footers.extend(footers)

        page_footnotes = [f for f in footers if f.footer_type == FooterType.FOOTNOTE]
        if page_footnotes:
            all_footnotes.append({
                'page': page_num,
                'footnotes': [f.text for f in page_footnotes]
            })

        print(f"  Page {page_num}: {len(footers)} footer elements detected")
        for f in footers:
            print(f"    - {f.footer_type.value}: '{f.text[:50]}' (conf: {f.confidence:.2f})")

    # =====================================================================
    # PHASE 3: Strip footers from CORRECTED content
    # =====================================================================
    print("\n=== Phase 3: Footer Stripping from Corrected Text ===")
    stripped_contents = []

    for i, content in enumerate(corrected_contents):
        page_num = i + 1
        page_footers = [f for f in all_footers if f.page_num == page_num]

        preserve_types = []
        if config['preserve_footnotes']:
            preserve_types.append(FooterType.FOOTNOTE)

        stripped = detector.strip_footers(content, page_footers, preserve_types)
        stripped_contents.append(stripped)

        removed = len([f for f in page_footers if f.footer_type not in preserve_types])
        preserved = len(page_footers) - removed
        print(f"  Page {page_num}: {removed} stripped, {preserved} preserved")

    # =====================================================================
    # PHASE 4: Boundary stitching
    # =====================================================================
    print(f"\n=== Phase 4: Boundary Stitching ({len(stripped_contents)-1} boundaries) ===")
    for i in range(len(stripped_contents) - 1):
        print(f"  Page {i+1} -> {i+2}...")
        stripped_contents[i], stripped_contents[i+1], _ = \
            process_boundary_enhanced(
                stripped_contents[i], stripped_contents[i+1], i+1,
                client=client,
                model_name=model_name,
                strip_headers=config['strip_running_headers'],
                strip_footers=config['strip_page_numbers'],
                preserve_footnotes=config['preserve_footnotes']
            )

    # =====================================================================
    # PHASE 5: Build outputs
    # =====================================================================
    stitched_pages = [
        f"{markers[i]}\n{stripped_contents[i]}".strip()
        for i in range(len(stripped_contents))
    ]

    flowing_text = re.sub(r'\n{3,}', '\n\n',
        '\n\n'.join(c.strip() for c in stripped_contents if c.strip())
    ).strip()

    print('\n=== Processing Complete ===')
    print(f"Total footer elements detected: {len(all_footers)}")
    print(f"Total footnotes extracted: {len(all_footnotes)}")

    return stitched_pages, flowing_text, all_footers, all_footnotes


def integrate_with_existing_notebook(optimized_result: str,
                                      corrected_pages: list,
                                      client,
                                      model_name: str,
                                      output_dir: str = 'ara-ocr'):
    """
    Drop-in replacement for existing notebook's stitching + save cells.

    Usage:
        from enhanced_stitching_v3 import integrate_with_existing_notebook
        stitched_pages, flowing_text, all_footers, all_footnotes = \
            integrate_with_existing_notebook(optimized_result, corrected_pages, client, claude_model)
    """
    import os

    stitched_pages, flowing_text, all_footers, all_footnotes = \
        stitch_pages_with_footers(
            raw_pages=optimized_result,
            corrected_pages=corrected_pages,
            client=client,
            model_name=model_name
        )

    # Save all outputs
    with open(os.path.join(output_dir, 'LLM_corrected_text.txt'), 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(stitched_pages))
    print('Saved: LLM_corrected_text.txt')

    with open(os.path.join(output_dir, 'flowing_text.txt'), 'w', encoding='utf-8') as f:
        f.write(flowing_text)
    print('Saved: flowing_text.txt')

    if all_footnotes:
        footnote_text = []
        for fn_group in all_footnotes:
            footnote_text.append(f"--- Page {fn_group['page']} ---")
            for fn in fn_group['footnotes']:
                footnote_text.append(fn)
            footnote_text.append('')
        with open(os.path.join(output_dir, 'extracted_footnotes.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(footnote_text))
        print('Saved: extracted_footnotes.txt')

    # Generate report
    detector = FooterDetector()
    detector.detected_footers = all_footers
    report = detector.get_footer_report()
    with open(os.path.join(output_dir, 'footer_report.txt'), 'w', encoding='utf-8') as f:
        f.write(report)
    print('Saved: footer_report.txt')

    # Summary
    summary = f"""
=================================================
OCR PROCESSING SUMMARY (v3)
=================================================
Input: pages_5_7.pdf
Pages processed: {len(corrected_pages)}

Footer Detection Results:
  - Total elements: {len(all_footers)}
  - Page numbers: {len([f for f in all_footers if f.footer_type == FooterType.PAGE_NUMBER])}
  - Footnotes: {len([f for f in all_footers if f.footer_type == FooterType.FOOTNOTE])}
  - Running headers: {len([f for f in all_footers if f.footer_type == FooterType.RUNNING_HEADER])}
  - Separators: {len([f for f in all_footers if f.footer_type == FooterType.SEPARATOR])}

Configuration:
  - Strip page numbers: True
  - Strip running headers: True
  - Preserve footnotes: True

Output Files:
  - LLM_corrected_text.txt (page-marked, footers stripped)
  - flowing_text.txt (clean continuous text)
  - extracted_footnotes.txt (footnotes only)
  - footer_report.txt (detection details)
=================================================
"""
    with open(os.path.join(output_dir, 'processing_summary.txt'), 'w', encoding='utf-8') as f:
        f.write(summary)
    print('Saved: processing_summary.txt')
    print('\n' + summary)

    return stitched_pages, flowing_text, all_footers, all_footnotes


if __name__ == "__main__":
    print("Enhanced stitching v3 loaded.")
    print("Key fixes:")
    print("  1. Detection runs on RAW OCR before LLM correction")
    print("  2. Proper footer list management")
    print("  3. RTL-reversed parentheses handled")
    print("  4. Stricter running header detection")
