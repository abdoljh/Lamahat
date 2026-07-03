"""
Wikipedia lead-image source (PHASE3.md §7.3, "the likely next win").

For named historical subjects, the Wikipedia article's lead image is
almost always the canonical documentary photograph — exactly what a
biography video wants for `portrait` / `archive` / `location` shots.
One search call finds candidate articles; `prop=pageimages` returns
each article's lead image in original resolution.

License posture: `piprop=original` with `pilicense=free` returns ONLY
images Wikipedia itself classifies as freely licensed — non-free
fair-use lead images (common on biographies of modern figures) are
excluded server-side, so no separate Commons metadata round-trip is
needed.  The candidate is stamped "wikipedia-free" and still passes
through the normal vision scoring like any other source.

Ordering in the waterfall: after Wikimedia Commons (which finds richer
sets when it hits) and before Pexels (which must stay last-resort for
historical content).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from .base import ImageCandidate, Source, simplify_query

log = logging.getLogger(__name__)

_API = "https://en.wikipedia.org/w/api.php"
_HEADERS = {
    "User-Agent": "Lamahat/1.0 (https://github.com/abdoljh/Lamahat; bot)",
}

_MIN_DIMENSION = 320


class WikipediaLeadImage(Source):
    name = "wikipedia"

    def search(self, query: str, n: int = 4) -> list[ImageCandidate]:
        # Article titles are short — search with the simplified query
        # (named entity + core nouns), same treatment Wikimedia gets.
        short_query = simplify_query(query, max_tokens=4)
        if short_query != query:
            log.debug("Wikipedia: simplified %r → %r", query, short_query)

        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": short_query,
            "gsrnamespace": "0",              # articles
            "gsrlimit": str(min(max(n, 3), 8)),
            "prop": "pageimages|info",
            "piprop": "original|name",
            "pilicense": "free",              # server-side free-license filter
            "inprop": "url",
            "format": "json",
        }
        url = _API + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            log.warning("Wikipedia search failed for %r: %s", query, exc)
            return []

        pages = data.get("query", {}).get("pages", {}) or {}
        candidates: list[ImageCandidate] = []

        # Keep Wikipedia's own relevance order (search rank), not dict order.
        for page in sorted(pages.values(),
                           key=lambda p: p.get("index", 999)):
            orig = page.get("original") or {}
            url_str = orig.get("source", "")
            if not url_str:
                continue
            low = url_str.lower().split("?")[0]
            if low.endswith((".svg", ".gif", ".pdf", ".webm", ".ogv")):
                continue

            w = int(orig.get("width", 0) or 0)
            h = int(orig.get("height", 0) or 0)
            if 0 < w < _MIN_DIMENSION or 0 < h < _MIN_DIMENSION:
                continue

            title = page.get("title", "Untitled")
            candidates.append(ImageCandidate(
                url=url_str,
                title=f"Lead image: {title}"[:120],
                source="wikipedia",
                license_short="wikipedia-free",
                width=w,
                height=h,
                source_url=page.get("fullurl", ""),
                source_query=query,
            ))
            if len(candidates) >= n:
                break

        log.info("Wikipedia: %d lead-image candidates for %r",
                 len(candidates), short_query)
        return candidates

    def download(self, candidate, dest):
        # upload.wikimedia.org rejects generic agents — reuse our UA.
        try:
            req = urllib.request.Request(candidate.url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if len(data) < 1024:
                return None
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            candidate.local_path = dest
            return dest
        except Exception as exc:  # noqa: BLE001 — one bad URL must not kill the run
            log.warning("Wikipedia: download failed for %s: %s",
                        candidate.url[:80], exc)
            return None
