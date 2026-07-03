"""
Phase 3 sources — review dossier (decisions.json).

The review dossier is the contract between the *prebuild* pass (which
fetches and scores all candidates) and the *render* pass (which burns
the video).  Between those two passes the user can edit the dossier to:

  * Swap which candidate is `chosen` for any image shot.
  * Drop a personally-supplied image into `overrides/shot_NN.jpg` and
    set `"override": "overrides/shot_NN.jpg"` in the matching entry.
  * Pin a canonical portrait of the main character that gets reused
    for every `portrait` shot — the single most effective biography
    quality lever.

The dossier is plain JSON next to a directory of thumbnails the user
can preview.  No GUI, no Streamlit needed for review — open the folder
in a file browser, look at the candidate JPEGs, edit a text file.

Anatomy
-------
output/review/
├── decisions.json            ← The editable contract
├── README.txt                ← Human-readable usage notes
├── overrides/                ← User drops .jpg/.png here
│   └── (typically: shot_03.jpg, shot_38.jpg, character.jpg, ...)
├── shot_NN_<visual>/         ← One folder per image-needing shot
│   ├── context.txt           ← What this shot is about
│   ├── candidates.json       ← Machine copy of the candidate list
│   ├── <source>_a.jpg        ← Downloaded candidate thumbnails
│   ├── <source>_b.jpg
│   └── ...
└── ...

decisions.json shape
--------------------
{
  "version":   1,
  "book":      {"title": "...", "character": "Jafar al-Askari"},
  "pinned_portrait": "overrides/character.jpg" | null,
  "shots": {
    "3": {
      "visual":       "portrait",
      "query":        "Jafar al-Askari Iraqi officer historical portrait 1920s",
      "duration_sec": 7.1,
      "arabic_caption_excerpt": "...",
      "chosen":       "pexels:Portrait of a man in a historical military uniform",
      "chosen_url":   "https://images.pexels.com/...",
      "chosen_file":  "shot_03_portrait/pexels_a.jpg",
      "override":     null,
      "candidates": [
        {"source": "pexels", "title": "...", "score": 8,
         "score_breakdown": {"subject": 3, "quality": 2, "cinematic": 3},
         "url": "...", "file": "shot_03_portrait/pexels_a.jpg",
         "vision_reason": "..."},
        ...
      ]
    },
    ...
  }
}

Editing rules
-------------
* To pick a different candidate, copy its `"source:title"` string into
  `chosen` (and optionally update `chosen_url` / `chosen_file` to
  match — render will re-resolve from the candidates list anyway).
* To use a personal image, drop the file into `overrides/` and set
  `override` to its path relative to the review dir
  (e.g. `"overrides/shot_03.jpg"`).  `chosen` is ignored when
  `override` is set.
* To use the same image for *all* portrait shots, set
  `pinned_portrait` to a path under `overrides/` and the prebuild step
  will retroactively flag every `portrait` shot's override field.

The render pass resolves in this order per shot:
    override → chosen → fallback to original Fetcher waterfall.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


DECISIONS_VERSION = 1
DECISIONS_FILENAME = "decisions.json"
OVERRIDES_SUBDIR = "overrides"

# Visuals that need an external image (everything that isn't a
# typography card).  Mirrors render.TYPOGRAPHY_VISUALS in inverse.
_IMAGE_VISUALS = {"portrait", "location", "object", "archive", "broll"}


# ── User-marked file convention ────────────────────────────────────── #
#
# A user can drop a file into a shot folder (review/shot_NN_<visual>/)
# whose name begins with `my_` or `user_` (case-insensitive), and the
# render pass will pick it up automatically — no decisions.json edit
# required.  This is the lightest-weight way to override one shot.
#
# Recognised: my_jafar.jpg, MY_JAFAR.JPG, user_a.png, User_Portrait.webp
# Not recognised: pexels_a.jpg, loc_a.jpg, character.jpg (those are
# the auto-fetched candidates and the global pin, respectively).

_USER_FILE_RE = re.compile(
    r"^(my|user)[_-].+\.(jpg|jpeg|png|webp)$",
    re.IGNORECASE,
)

# Tokens too generic to identify a person when matching a portrait shot's
# query against the main character's name.
_NAME_STOPWORDS = {
    "al", "el", "ad", "ibn", "bin", "abu", "abd", "abdul", "the", "of", "and",
    "pasha", "bey", "bay", "effendi", "general", "colonel", "officer", "commander",
    "portrait", "historical", "photo", "photograph", "picture", "image",
    "ottoman", "iraqi", "arab", "young", "old", "man", "men",
}


def _name_tokens(name: str) -> list[str]:
    """Distinctive lowercase tokens of a person's name (drops 'al', 'pasha', …)."""
    toks = re.split(r"[\s\-_]+", (name or "").lower())
    return [t for t in toks if len(t) >= 3 and t not in _NAME_STOPWORDS]


def subject_is_character(query: str, character_name: str) -> bool:
    """True if a portrait shot's `query` is about the main `character_name`.

    Used to stop the main-character pool/pin from landing on a portrait shot
    whose subject is somebody else (e.g. showing Jafar al-Askari while the
    narration is about Mahmoud Shevket Pasha). When no character is configured
    every portrait is treated as the main character (back-compat).
    """
    toks = _name_tokens(character_name)
    if not toks:
        return True
    q = (query or "").lower()
    return any(t in q for t in toks)


def find_user_marked_file(
    review_dir: Path,
    shot_index: int,
    visual: str,
) -> Path | None:
    """
    Look in `review_dir/shot_NN_<visual>/` for files matching the
    user-naming convention.  Returns the alphabetically-first matching
    file, or None.

    Alphabetical (not newest-modified) so the choice is stable when the
    user accidentally deletes a file from the folder.
    """
    folder = review_dir / f"shot_{shot_index:02d}_{visual}"
    if not folder.is_dir():
        return None

    matches = sorted(
        p for p in folder.iterdir()
        if p.is_file() and _USER_FILE_RE.match(p.name)
    )
    if not matches:
        return None
    if len(matches) > 1:
        log.info(
            "Shot %d: %d user-marked files in %s, using alphabetically-first %s",
            shot_index, len(matches), folder.name, matches[0].name,
        )
    return matches[0]


@dataclass
class CandidateEntry:
    """One candidate image, as recorded in decisions.json."""
    source: str                       # 'loc' | 'wikimedia' | 'internet_archive' | 'pexels'
    title: str
    url: str
    file: str = ""                    # Path relative to review_dir
    score: int = -1                   # Sum of vision scores (0..9), -1 = unscored
    score_breakdown: dict | None = None
    vision_reason: str = ""
    width: int = 0
    height: int = 0
    license_short: str = ""


@dataclass
class ShotDecision:
    """One image shot's decision record."""
    visual: str
    query: str
    duration_sec: float
    arabic_caption_excerpt: str = ""
    chosen: str = ""                  # 'source:title' of preferred candidate
    chosen_url: str = ""
    chosen_file: str = ""
    override: str | None = None       # Path under review_dir (e.g. 'overrides/shot_03.jpg')
    candidates: list[CandidateEntry] = field(default_factory=list)


@dataclass
class Decisions:
    """The whole dossier."""
    book: dict
    pinned_portrait: str | None = None
    shots: dict[int, ShotDecision] = field(default_factory=dict)
    version: int = DECISIONS_VERSION

    # ── Serialisation ──────────────────────────────────────────── #

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "book":    self.book,
            "pinned_portrait": self.pinned_portrait,
            "shots": {
                str(idx): {
                    "visual":       d.visual,
                    "query":        d.query,
                    "duration_sec": d.duration_sec,
                    "arabic_caption_excerpt": d.arabic_caption_excerpt,
                    "chosen":       d.chosen,
                    "chosen_url":   d.chosen_url,
                    "chosen_file":  d.chosen_file,
                    "override":     d.override,
                    "candidates":   [asdict(c) for c in d.candidates],
                }
                for idx, d in self.shots.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Decisions":
        shots: dict[int, ShotDecision] = {}
        for idx_str, raw in (data.get("shots") or {}).items():
            cands = [CandidateEntry(**c) for c in (raw.get("candidates") or [])]
            shots[int(idx_str)] = ShotDecision(
                visual=raw.get("visual", ""),
                query=raw.get("query", ""),
                duration_sec=raw.get("duration_sec", 0.0),
                arabic_caption_excerpt=raw.get("arabic_caption_excerpt", ""),
                chosen=raw.get("chosen", ""),
                chosen_url=raw.get("chosen_url", ""),
                chosen_file=raw.get("chosen_file", ""),
                override=raw.get("override"),
                candidates=cands,
            )
        return cls(
            version=data.get("version", DECISIONS_VERSION),
            book=data.get("book", {}),
            pinned_portrait=data.get("pinned_portrait"),
            shots=shots,
        )

    def save(self, review_dir: Path) -> Path:
        review_dir = Path(review_dir)
        review_dir.mkdir(parents=True, exist_ok=True)
        out = review_dir / DECISIONS_FILENAME
        out.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Decisions written → %s", out)
        return out

    @classmethod
    def load(cls, review_dir: Path) -> "Decisions":
        review_dir = Path(review_dir)
        p = review_dir / DECISIONS_FILENAME
        if not p.exists():
            raise FileNotFoundError(
                f"No decisions.json in {review_dir}.  Run the prebuild "
                f"step first: python prebuild_assets.py --review-dir "
                f"{review_dir} ..."
            )
        data = json.loads(p.read_text(encoding="utf-8"))
        d = cls.from_dict(data)
        log.info(
            "Decisions loaded from %s: %d shots, pinned_portrait=%s",
            p, len(d.shots),
            d.pinned_portrait or "(none)",
        )
        return d

    # ── Portrait pool (multi-image character override) ─────────── #
    #
    # Looks at an EXPLICIT directory: `$LAMAHAT_RESOURCES/character/`,
    # defaulting to `/content/resources/character/` in Colab and
    # `<cwd>/resources/character/` elsewhere.
    #
    # Why explicit rather than walking-up from review_dir: prebuild
    # creates `<review_dir>/overrides/` itself, which captured the
    # walk-up search and silently broke pool discovery in earlier takes.
    # `resources/` is a new name with no historical conflicts.
    #
    # Portrait shots round-robin through sorted `*.jpg/*.jpeg/*.png/
    # *.webp` (by portrait-shot rank, modulo-wrapped).
    #
    # Fallback chain at resolve-time for portrait shots:
    #   1. `$LAMAHAT_RESOURCES/character/` pool       ← THIS
    #   2. legacy `pinned_portrait` (file in decisions.json)
    #   3. shot.chosen_file (prebuild's auto-pick)

    _PORTRAIT_POOL_EXTS = (".jpg", ".jpeg", ".png", ".webp")
    _PORTRAIT_POOL_SUBDIR = "character"

    @staticmethod
    def _resources_root() -> Path:
        """Resolve the resources directory.  Env var wins; then
        `<cwd>/resources` when it exists (repo-clone layout, e.g.
        /content/Lamahat after `git clone` on Colab); then the legacy
        copy-to-/content layout; then `<cwd>/resources` regardless."""
        env = os.environ.get("LAMAHAT_RESOURCES")
        if env:
            return Path(env).expanduser().resolve()
        cwd_resources = (Path.cwd() / "resources").resolve()
        if cwd_resources.is_dir():
            return cwd_resources
        colab = Path("/content/resources")
        if colab.is_dir() or Path("/content").is_dir():
            return colab
        return cwd_resources

    def _list_portrait_pool(self, review_dir: Path) -> list[Path]:
        """Return the character pool files from `$LAMAHAT_RESOURCES/character/`,
        or [] if directory absent / empty.  `review_dir` is unused but
        kept in the signature for back-compat with earlier takes.

        The list is deterministically shuffled (seeded by the pool contents)
        rather than left alphabetical, so portrait shots cycle through a varied
        order — "show all the character images" — while staying reproducible
        across a re-render of the same dossier.
        """
        root = self._resources_root()
        p = (root / self._PORTRAIT_POOL_SUBDIR).resolve()
        if not p.is_dir():
            return []
        pool: list[Path] = []
        for f in p.iterdir():
            if f.is_file() and f.suffix.lower() in self._PORTRAIT_POOL_EXTS:
                pool.append(f)
        pool.sort(key=lambda x: x.name.lower())
        seed = hash(tuple(f.name for f in pool)) & 0xFFFFFFFF
        random.Random(seed).shuffle(pool)
        return pool

    def _portrait_rank_of(self, shot_index: int) -> int:
        """Return the 0-based ordinal of `shot_index` among portrait
        shots (in shot-index order).  Returns -1 if not a portrait shot.
        Cached on first call."""
        cache = getattr(self, "_portrait_rank_cache", None)
        if cache is None:
            cache = {}
            rank = 0
            for idx in sorted(self.shots):
                if self.shots[idx].visual == "portrait":
                    cache[idx] = rank
                    rank += 1
            self._portrait_rank_cache = cache
        return cache.get(shot_index, -1)

    # ── Resolution ─────────────────────────────────────────────── #

    def resolve(self, shot_index: int, review_dir: Path) -> Path | None:
        """
        Return an absolute path to the image the renderer should use
        for `shot_index`, or None if no decision was recorded.

        Resolution order:
            1. `override`           — explicit user pick in decisions.json
            2. user-marked file     — my_*.jpg / user_*.jpg dropped into
                                      the shot folder (no JSON edit required)
            3. pinned portrait      — for `portrait` visuals only
            4. `chosen_file`        — prebuild's auto-pick
            5. None                 — caller falls back to the Fetcher
                                      waterfall at render time
        """
        review_dir = Path(review_dir).resolve()
        shot = self.shots.get(shot_index)
        if shot is None:
            return None

        # 1. Explicit override declared in decisions.json
        if shot.override:
            p = (review_dir / shot.override).resolve()
            if p.exists():
                log.info("Shot %d: override hit %s", shot_index, p.name)
                return p
            log.warning(
                "Shot %d: override declared %s but file is missing — "
                "ignoring and falling through",
                shot_index, p,
            )

        # 2. User-marked file dropped into the shot folder.
        # Matches my_*.{jpg,jpeg,png,webp} or user_*.{...} (case-insensitive).
        # Alphabetically-first wins for stability.  Lets the user replace
        # or supplement images without touching decisions.json.
        user_file = find_user_marked_file(review_dir, shot_index, shot.visual)
        if user_file:
            log.info(
                "Shot %d: user-marked file hit %s",
                shot_index, user_file.name,
            )
            return user_file

        # 3. Portrait pool / pinned portrait, for portrait shots only — and
        #    only when THIS portrait is about the main character. A portrait
        #    shot whose query names someone else (e.g. Mahmoud Shevket Pasha)
        #    must NOT get the main character's face; it falls through to its
        #    own chosen_file / fetched image instead.
        _char = (self.book or {}).get("character", "")
        if shot.visual == "portrait" and subject_is_character(shot.query, _char):
            pool = self._list_portrait_pool(review_dir)
            if pool:
                rank = self._portrait_rank_of(shot_index)
                if rank < 0:
                    rank = 0   # defensive — shouldn't happen
                chosen = pool[rank % len(pool)]
                log.info(
                    "Shot %d: portrait pool hit %s (rank %d of %d)",
                    shot_index, chosen.name, rank, len(pool),
                )
                return chosen
            if self.pinned_portrait:
                p = (review_dir / self.pinned_portrait).resolve()
                if p.exists() and p.is_file():
                    log.info("Shot %d: pinned-portrait hit %s",
                             shot_index, p.name)
                    return p
                log.warning(
                    "Shot %d: pinned_portrait %s missing — ignoring",
                    shot_index, p,
                )

        # 4. Pre-downloaded candidate the dossier marked as chosen
        if shot.chosen_file:
            p = (review_dir / shot.chosen_file).resolve()
            if p.exists():
                log.info("Shot %d: chosen-file hit %s", shot_index, p.name)
                return p

        return None


def is_image_shot(visual: str) -> bool:
    return visual in _IMAGE_VISUALS


def shot_folder_name(shot_index: int, visual: str) -> str:
    """Stable directory name for one shot's candidates."""
    return f"shot_{shot_index:02d}_{visual}"


def write_readme(review_dir: Path) -> None:
    """Write a human-friendly usage guide into the review directory."""
    text = """\
LAMAHAT — Phase 3 review dossier
=================================

This directory is the contract between the prebuild pass (which fetches
and scores candidate images) and the render pass (which burns the
video).  Edit it freely between the two passes.

WHAT'S IN HERE
--------------
  decisions.json       The file you edit.  Hand-edit-friendly JSON.
  overrides/           Drop your own .jpg / .png files here.
  shot_NN_VISUAL/      One folder per image-needing shot.  Contains:
                       - context.txt     what this shot is about
                       - candidates.json same as decisions.json["shots"]["NN"]["candidates"]
                       - SOURCE_X.jpg    the actual downloaded candidate

WHAT YOU CAN CHANGE IN decisions.json
-------------------------------------
Per shot:
  * "chosen"        Move to a different candidate by copying its
                    "source:title" string here.
  * "override"      Set to a file path under this directory (typically
                    "overrides/shot_NN.jpg") to use your own image.
                    Overrides win over everything else.

Global:
  * "pinned_portrait"  Set to a path under overrides/ (e.g.
                       "overrides/character.jpg") to use one canonical
                       portrait at every "portrait" shot.

EXAMPLES
--------
Use my own picture for shot 3:
  1. Save your image as overrides/shot_03.jpg
  2. In decisions.json, find shot "3" and set:
        "override": "overrides/shot_03.jpg"

Use the same Jafar al-Askari portrait at every "portrait" shot:
  1. Save the photo as overrides/character.jpg
  2. In decisions.json, set:
        "pinned_portrait": "overrides/character.jpg"

Swap to the Wikimedia candidate instead of the Pexels one:
  1. Open shot_NN_portrait/candidates.json
  2. Copy the "source:title" of the candidate you prefer
  3. In decisions.json, paste it into "chosen"

THEN
----
  python render_plan.py --plan ... --review-dir <this-dir> --output ...
"""
    (review_dir / "README.txt").write_text(text, encoding="utf-8")