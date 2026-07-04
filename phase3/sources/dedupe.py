"""
Perceptual-hash near-duplicate detection (P1.1, SCREENING_REVIEW.md §2.2).

The plan can cut every ~4.75 s and still *feel* like 9–12 s holds when
adjacent shots resolve to the same (or a near-identical) image — two
archive shots picking the same ledger photo reads as one long static
hold.  A difference hash (dHash) catches this cheaply: 64-bit gradient
signature, robust to re-encoding, resizing and the conditioning pass's
sharpening, while distinct photos land far apart.

Used by the Fetcher (swap to the next-ranked candidate at resolve time)
and by audit_plan.py --review-dir (report effective holds pre-render).
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Hamming distance at or below which two dHashes count as "the same
# image on screen".  64-bit hash: identical files → 0; same photo
# re-encoded / resized / sharpened → typically ≤ 6; distinct photos of
# the same subject → typically ≥ 15.  8 is the review plan's threshold.
DEFAULT_THRESHOLD = 8

_HASH_SIZE = 8   # 8×8 gradient grid → 64-bit hash


def dhash(path: Path | str, hash_size: int = _HASH_SIZE) -> int | None:
    """Difference hash of an image file.  None when unreadable (callers
    must treat None as "unknown — do not dedupe against it")."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            img = img.convert("L").resize(
                (hash_size + 1, hash_size), Image.LANCZOS)
            px = list(img.getdata())
    except Exception as exc:  # noqa: BLE001 — hashing is best-effort
        log.debug("dhash: unreadable %s (%s)", path, exc)
        return None

    w = hash_size + 1
    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            bits = (bits << 1) | int(px[row * w + col] > px[row * w + col + 1])
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def near_duplicate(a: int | None, b: int | None,
                   threshold: int = DEFAULT_THRESHOLD) -> bool:
    """True when both hashes are known and within `threshold` bits."""
    if a is None or b is None:
        return False
    return hamming(a, b) <= threshold
