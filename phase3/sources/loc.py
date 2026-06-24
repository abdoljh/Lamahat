"""
Library of Congress Prints & Photographs source.

LoC's JSON API is documented at https://www.loc.gov/apis/json-and-yaml/.
No authentication required.  The collection has extensive MENA holdings
from 1880-1940 — exactly the period for Arab biography content.

We query the "photos" format specifically to filter out manuscripts,
maps, and prints (which would be miscategorized otherwise).

Typical query response shape:
  {
    "results": [
      {
        "id": "https://www.loc.gov/item/...",
        "title": "...",
        "image_url": ["//tile.loc.gov/.../something.jpg", ...],
        "online_format": ["image"],
        "rights": "...",
        ...
      }
    ]
  }
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from .base import ImageCandidate, Source, is_free_license, simplify_query

log = logging.getLogger(__name__)

_API = "https://www.loc.gov/search/"
_TIMEOUT = 30      # was 20; LoC has 5-10s response times under load
_RETRIES = 1       # one retry on timeout — most LoC timeouts succeed on retry


class LibraryOfCongress(Source):
    name = "loc"

    def search(self, query: str, n: int = 4) -> list[ImageCandidate]:
        # LoC's /search/ uses AND-of-tokens on Subject/Description fields,
        # which are tagged with formal authority headings, not natural
        # language.  Long planner queries with descriptors never match.
        short_query = simplify_query(query, max_tokens=4)
        if short_query != query:
            log.debug("LoC: simplified %r → %r", query, short_query)
        params = {
            "q":   short_query,
            "fa":  "online-format:image|original-format:photo,print",
            "fo":  "json",
            "c":   str(min(n * 3, 25)),   # over-fetch then filter
        }
        url = _API + "?" + urllib.parse.urlencode(params)
        headers = {
            "User-Agent":
                "Lamahat/1.0 (https://github.com/abdoljh/Lamahat)",
            "Accept": "application/json",
        }

        data = None
        last_exc: Exception | None = None
        for attempt in range(_RETRIES + 1):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    data = json.loads(resp.read())
                break
            except (urllib.error.URLError, json.JSONDecodeError,
                    OSError) as exc:
                last_exc = exc
                if attempt < _RETRIES:
                    log.debug("LoC: attempt %d failed (%s); retrying",
                              attempt + 1, exc)
                    continue
                log.warning("LoC search failed for %r: %s", short_query, exc)
                return []

        if data is None:
            log.warning("LoC: no data after retries: %s", last_exc)
            return []

        results = data.get("results", [])
        candidates: list[ImageCandidate] = []

        for r in results:
            # Get the best image URL.  LoC returns multiple sizes; pick
            # the largest that's available.
            image_urls = r.get("image_url") or []
            if not image_urls:
                continue

            # URLs often come back protocol-relative ("//tile.loc.gov/...")
            # Pick the highest-quality (usually last in the array)
            url_str = image_urls[-1]
            if url_str.startswith("//"):
                url_str = "https:" + url_str

            # Skip thumbnails — they have "thumb" or small dimensions in name
            if any(t in url_str.lower() for t in ("thumb", "150", "200")):
                if len(image_urls) > 1:
                    # Try the second-to-last
                    fallback = image_urls[-2]
                    if fallback.startswith("//"):
                        fallback = "https:" + fallback
                    url_str = fallback

            # Rights / license assessment
            rights = r.get("rights", "")
            if isinstance(rights, list):
                rights = " ".join(str(x) for x in rights)
            # LoC's "no known restrictions" and "public domain" both count
            license_short = "PD" if (
                "no known" in rights.lower()
                or "public domain" in rights.lower()
            ) else rights[:30]

            if not is_free_license(license_short):
                log.debug("LoC: skipping %s — license %s",
                          r.get("title", "?")[:40], license_short)
                continue

            title = r.get("title", "Untitled")
            if isinstance(title, list):
                title = title[0] if title else "Untitled"

            candidates.append(ImageCandidate(
                url=url_str,
                title=title[:120],
                source="loc",
                license_short=license_short or "PD",
                source_url=r.get("id", ""),
                source_query=query,
            ))

            if len(candidates) >= n:
                break

        log.info("LoC: %d candidates for %r", len(candidates), short_query)
        return candidates