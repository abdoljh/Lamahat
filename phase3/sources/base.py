"""
Phase 3 sources — shared types and base classes.

ImageCandidate is the unit of currency.  Every source returns these;
the vision scorer enriches them with scores; the orchestrator picks
the best for each shot.

The Source abstract base class defines the contract every concrete
source (LoC, Wikimedia, Internet Archive, Pexels) must implement.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)


SourceName = Literal[
    "loc",            # Library of Congress
    "wikimedia",      # Wikimedia Commons
    "wikipedia",      # Wikipedia article lead image
    "internet_archive",
    "pexels",
    "user_upload",    # User-supplied image
    "book_extract",   # Phase 1a-extracted photo from the source PDF
    "photo_bank",     # Curated photo bank, Sonnet-assigned (Path C)
]


@dataclass
class ImageCandidate:
    """
    One candidate image for a shot.

    All sources return ImageCandidate instances.  Fields are populated
    progressively:

    - Source returns:        url, title, license_short, source, width, height
    - Cache layer adds:      local_path
    - Vision scorer adds:    score_subject, score_quality, score_cinematic,
                             vision_reason
    """

    # Required at construction
    url: str                              # Direct URL to the bitmap
    title: str                            # Human-readable description
    source: SourceName                    # Where it came from

    # Optional metadata
    license_short: str = ""               # e.g. "PD", "CC-BY-4.0"
    license_url: str = ""                 # Link to license terms
    width: int = 0                        # Pixel width (0 = unknown)
    height: int = 0                       # Pixel height
    source_url: str = ""                  # URL of the source page (for attribution)
    source_query: str = ""                # The query that found this candidate

    # Populated by the cache layer after download
    local_path: Path | None = None

    # Populated by the vision scorer
    score_subject: int = -1               # 0-3, -1 = not scored
    score_quality: int = -1               # 0-3
    score_cinematic: int = -1             # 0-3
    score_era: int = -1                   # 0-3 period plausibility, -1 = not scored
    vision_reason: str = ""               # One-line rationale from Claude

    @property
    def total_score(self) -> int:
        """Sum of vision scores. -1 if not scored."""
        if self.score_subject < 0:
            return -1
        return self.score_subject + self.score_quality + self.score_cinematic

    @property
    def is_scored(self) -> bool:
        return self.score_subject >= 0

    @property
    def era_pass(self) -> bool:
        """False only when the scorer judged the image anachronistic for the
        shot's implied period (era 0-1).  Unscored era (-1) passes — the era
        axis is a demotion signal, never a hard filter (fail-open)."""
        return self.score_era < 0 or self.score_era >= 2

    def __str__(self) -> str:
        s = (self.total_score if self.is_scored
             else "unscored")
        return f"{self.source}:{self.title[:40]} [{s}]"


@dataclass
class FetchResult:
    """Result of a fetch_for_shot() call — multiple candidates, ranked."""
    query: str
    candidates: list[ImageCandidate]
    best: ImageCandidate | None = None    # Top-scored, downloaded, kept

    @property
    def has_image(self) -> bool:
        return self.best is not None and self.best.local_path is not None


class Source(ABC):
    """Abstract base for any image source."""

    name: SourceName

    @abstractmethod
    def search(self, query: str, n: int = 4) -> list[ImageCandidate]:
        """Search the source for up to n images matching `query`."""

    def download(self, candidate: ImageCandidate, dest: Path) -> Path | None:
        """
        Download the candidate's image to dest.  Returns the path on
        success, None on failure.

        Default implementation handles most HTTP sources via urllib;
        sources with auth (Pexels) override this.
        """
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(
                candidate.url,
                headers={"User-Agent":
                         "Lamahat/1.0 (https://github.com/abdoljh/Lamahat)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if len(data) < 1024:
                log.debug("Source %s: %s too small (%d bytes), skipping",
                          self.name, candidate.title, len(data))
                return None
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            candidate.local_path = dest
            log.debug("Source %s ↓ %s → %s (%d KB)",
                      self.name, candidate.title[:40], dest.name,
                      len(data) // 1024)
            return dest
        except (urllib.error.URLError, OSError) as exc:
            log.warning("Source %s: download failed for %s: %s",
                        self.name, candidate.url[:80], exc)
            return None


# ── Utilities ─────────────────────────────────────────────────────────── #

def query_hash(query: str, prefix_len: int = 16) -> str:
    """Stable short hash of a query string for use in cache keys."""
    import hashlib
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:prefix_len]


def ext_from_url(url: str, default: str = ".jpg") -> str:
    """Pick a sensible file extension based on URL path."""
    import urllib.parse
    path = urllib.parse.urlparse(url).path.lower().split("?")[0]
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return default


# License classification — what counts as "free" for our purposes.
# We accept anything Creative Commons, public domain, or explicitly
# permissive.  We reject anything with NC (non-commercial) or ND
# (no-derivatives) restrictions.
_FREE_LICENSE_PREFIXES = (
    "cc-", "cc0", "pd", "public domain", "attribution",
    "no known", "no restrictions",
)
_NONFREE_TERMS = (
    "nc", "non-commercial", "noncommercial",
    "nd", "no derivative", "no-derivative",
    "all rights reserved",
)


def is_free_license(license_str: str) -> bool:
    """Return True if the license string indicates a freely-usable image."""
    ls = (license_str or "").lower().strip()
    if any(term in ls for term in _NONFREE_TERMS):
        return False
    if not ls:
        return True   # Treat unknown as free; assume good faith from APIs
    return any(ls.startswith(p) for p in _FREE_LICENSE_PREFIXES)

# ── Query simplification ──────────────────────────────────────────────── #

# Stop-list of descriptors Sonnet's planner rule 6 appends to make Pexels
# happy, but which kill Wikimedia/LoC/IA searches (which use AND-of-tokens
# semantics).  Stripping these leaves the named entity + core nouns.
#
# Tokens dropped here are added by the new (post-§7.3) Sonnet prompt as
# "visual descriptors" — exactly what archive search engines reject.
_SIMPLIFY_STOPLIST: frozenset[str] = frozenset({
    # Visual/medium descriptors
    "sepia", "vintage", "historical", "documentary", "official",
    "photo", "photograph", "photographs", "photography", "picture",
    "image", "portrait", "portraits", "panorama", "panoramic",
    "cityscape", "landscape",
    # Era hedges (we keep specific years, drop fuzzy hedges)
    "early", "late", "mid", "century",
    # Decade words (we keep the named decade if it's a 4-digit year)
    "1900s", "1910s", "1920s", "1930s", "1940s",
    # Generic role/wear words that match anything
    "uniform", "uniforms", "clothing", "clothes", "wearing",
    "mustache", "mustached", "mustachioed", "bearded", "fez", "turban",
    # Adjectives that downgrade specificity
    "ancient", "old", "antique", "classical", "traditional",
    # Connectives that don't help full-text search
    "and", "or", "the", "of", "in", "at", "on", "with",
})


def simplify_query(query: str, max_tokens: int = 4) -> str:
    """Strip visual descriptors from a long planner query, keeping the
    named entity and core nouns intact.

    Designed for archive search engines (Wikimedia, LoC, Internet Archive)
    that use literal AND-of-tokens semantics — long queries with 10+
    tokens never match because no single document contains all tokens.

    Strategy:
      1. Tokenise on whitespace, preserving original case.
      2. Filter tokens whose lowercase form appears in _SIMPLIFY_STOPLIST.
      3. Keep at most `max_tokens` tokens (earliest first — the planner
         puts the named entity at the head of the query per rule 6).
      4. Re-join with single spaces.

    Returns the original query if simplification would empty it.

    Examples:
      "Jafar al-Askari Iraqi Ottoman military officer sepia portrait
       early 1900s uniform"
        → "Jafar al-Askari Iraqi Ottoman"
      "Constantinople Istanbul 1900s Bosphorus cityscape Ottoman historical
       photograph sepia panorama"
        → "Constantinople Istanbul Bosphorus Ottoman"
      "Mosul Iraq Tigris river city 1904 1900s sepia historical photo
       stone buildings Ottoman"
        → "Mosul Iraq Tigris river"
    """
    if not query:
        return query
    raw = query.split()
    kept: list[str] = []
    for tok in raw:
        if tok.lower() in _SIMPLIFY_STOPLIST:
            continue
        kept.append(tok)
        if len(kept) >= max_tokens:
            break
    if not kept:
        return query   # Don't return empty — fall back to original
    return " ".join(kept)
