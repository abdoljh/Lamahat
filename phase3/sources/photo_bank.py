"""
Curated photo bank → shot assignment ("Path C", PHASE3.md §8).

The user maintains a folder of hand-curated photographs for the book —
better copies of the book's own plates, plus any other authentic imagery
they collected.  Instead of hoping the web waterfall finds period-true
images, ONE Claude Sonnet call assigns bank photos to the plan's image
shots; the result lands in the review dossier as each shot's
`chosen_file`, so the normal dossier veto workflow (swap / override /
delete) still applies afterwards.

Two stages, both cached and idempotent:

1. caption_bank(bank_dir, ...)   — one Haiku vision call per *new* photo
   produces a one-line English caption.  Captions are cached in
   `<bank_dir>/captions.json` keyed by content hash, so re-runs are free
   and the user can hand-edit a caption (it is never overwritten while
   the file's bytes are unchanged).
2. assign_photo_bank(shots, bank_dir, ...) — one Sonnet call matches
   captioned photos to image shots.  Confident matches only; gaps are
   expected and fine (those shots keep their waterfall winner).

Bank folder convention (mirrors resources/character/ and
resources/book_cover/): `$LAMAHAT_RESOURCES/photo_bank/`, auto-detected
by prebuild_assets.py when --photo-bank is not given explicitly.

Cost: ~$0.005/photo captioning (once, cached) + ~$0.05-0.10 for the
single assignment call per plan.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

CAPTIONS_FILENAME = "captions.json"
CAPTIONS_VERSION = 1

_SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}

_CAPTION_MODEL = "claude-haiku-4-5-20251001"
_ASSIGN_MODEL = "claude-sonnet-4-6"

_CAPTION_SYSTEM = (
    "You caption historical photographs for a documentary archive index. "
    "Return only the caption text — one line, English, no quotes, no preamble."
)

_CAPTION_USER_TMPL = """\
This photograph belongs to the curated image bank for a documentary about:
  Book: {book_title}
  Main subject: {character_name}

Write ONE line (max 30 words) that would let an editor match this photo to a
narration moment without seeing it.  Include, when visible: who is shown
(named subject if recognisable from context, otherwise describe), the setting,
any period markers (uniforms, vehicles, architecture), and whether it is a
single-person portrait, a group photo, a location, a document, or an object.
"""

_ASSIGN_SYSTEM = """\
You are a documentary picture editor.  You receive (a) a curated bank of
authentic photographs with captions and (b) a shot list with search queries
and the Arabic narration each shot covers.  Assign bank photos to shots.

Rules:
1. Output JSON only: {"assignments": {"<shot_index>": "<filename>", ...}}
2. Assign a photo ONLY when it genuinely fits the shot's subject and moment.
   Leaving a shot unassigned is correct and expected — unassigned shots keep
   their existing image.  A wrong-but-plausible match is worse than a gap.
3. Use each photo at most {max_uses} time(s).  Spread the bank across the
   timeline rather than clustering assignments early.
4. Prefer: exact person match for `portrait` shots; exact place/event match
   for `location`/`archive` shots; documents/objects for `object` shots.
   Generic atmosphere photos may go to `broll` shots.
5. A photo of the wrong named person must never be assigned to a portrait
   shot about someone else.
"""

_ASSIGN_USER_TMPL = """\
Book: {book_title}
Main subject: {character_name}

PHOTO BANK ({n_photos} photos):
{photo_lines}

IMAGE SHOTS ({n_shots} shots):
{shot_lines}

Assign photos to shots per the rules.  Return ONLY the JSON object.
"""


# ── Bank listing & captioning ─────────────────────────────────────────── #

def list_bank_photos(bank_dir: Path) -> list[Path]:
    """All supported images in the bank folder, sorted by name."""
    bank_dir = Path(bank_dir)
    if not bank_dir.is_dir():
        return []
    return sorted(
        p for p in bank_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTS
    )


def _content_hash(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _load_captions(bank_dir: Path) -> dict:
    p = Path(bank_dir) / CAPTIONS_FILENAME
    if not p.exists():
        return {"version": CAPTIONS_VERSION, "photos": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("photos", {})
        return data
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("photo_bank: unreadable %s (%s) — starting fresh",
                    p, exc)
        return {"version": CAPTIONS_VERSION, "photos": {}}


def _save_captions(bank_dir: Path, data: dict) -> None:
    p = Path(bank_dir) / CAPTIONS_FILENAME
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _encode_for_vision(path: Path) -> str:
    """JPEG base64 of the image, resized to ≤800 px wide (API constraint)."""
    from PIL import Image
    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.width > 800:
            new_h = int(img.height * 800 / img.width)
            img = img.resize((800, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode()


def caption_bank(bank_dir: Path, *, anthropic_api_key: str,
                 book_title: str = "", character_name: str = "",
                 model: str = _CAPTION_MODEL) -> dict[str, str]:
    """
    Return {filename: caption} for every photo in the bank, captioning
    only files whose content hash isn't already in captions.json.

    Hand-edited captions survive: an existing entry is reused verbatim
    while the file's bytes are unchanged.  Users can also pre-write the
    whole captions.json by hand and no vision call is made at all.
    """
    photos = list_bank_photos(bank_dir)
    if not photos:
        return {}

    cache = _load_captions(bank_dir)
    entries: dict = cache["photos"]
    captions: dict[str, str] = {}
    n_new = 0

    for photo in photos:
        digest = _content_hash(photo)
        entry = entries.get(photo.name)
        if entry and entry.get("hash") == digest and entry.get("caption"):
            captions[photo.name] = entry["caption"]
            continue

        caption = ""
        try:
            from anthropic import Anthropic
            b64 = _encode_for_vision(photo)
            client = Anthropic(api_key=anthropic_api_key)
            msg = client.messages.create(
                model=model,
                max_tokens=120,
                system=_CAPTION_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": b64}},
                        {"type": "text", "text": _CAPTION_USER_TMPL.format(
                            book_title=book_title or "unknown",
                            character_name=character_name or "not specified",
                        )},
                    ],
                }],
            )
            caption = msg.content[0].text.strip().splitlines()[0][:250]
            n_new += 1
            log.info("photo_bank: captioned %s — %s",
                     photo.name, caption[:70])
        except Exception as exc:  # noqa: BLE001 — captioning is best-effort
            log.warning("photo_bank: caption failed for %s: %s — using filename",
                        photo.name, exc)
            caption = photo.stem.replace("_", " ").replace("-", " ")

        entries[photo.name] = {"hash": digest, "caption": caption}
        captions[photo.name] = caption

    if n_new:
        _save_captions(bank_dir, cache)
        log.info("photo_bank: %d new caption(s) written to %s",
                 n_new, Path(bank_dir) / CAPTIONS_FILENAME)
    return captions


# ── Assignment (the one Sonnet call) ──────────────────────────────────── #

def _parse_assignment_json(raw: str) -> dict:
    """Parse the assignment JSON, tolerating fences and stray prose."""
    raw = (raw.strip()
           .removeprefix("```json").removeprefix("```")
           .removesuffix("```").strip())
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object in assignment response: {raw[:200]}")
    return json.loads(m.group(0))


def assign_photo_bank(
    shots: list,
    bank_dir: Path,
    *,
    anthropic_api_key: str,
    book_title: str = "",
    character_name: str = "",
    captions: dict[str, str] | None = None,
    max_uses_per_photo: int = 1,
    model: str = _ASSIGN_MODEL,
    debug_dir: Path | None = None,
) -> dict[int, str]:
    """
    One Sonnet call assigning bank photos to the plan's image shots.

    Returns {1-indexed shot number: bank filename}.  Empty dict on any
    failure (fail-open — the waterfall result stands).  Assignments to
    unknown shots/files or exceeding `max_uses_per_photo` are dropped
    with a warning rather than trusted.
    """
    from .decisions import is_image_shot

    bank_dir = Path(bank_dir)
    if captions is None:
        captions = caption_bank(
            bank_dir, anthropic_api_key=anthropic_api_key,
            book_title=book_title, character_name=character_name)
    if not captions:
        log.info("photo_bank: no photos in %s — skipping assignment", bank_dir)
        return {}

    image_shots: dict[int, object] = {
        i + 1: s for i, s in enumerate(shots) if is_image_shot(s.visual)
    }
    if not image_shots:
        return {}

    photo_lines = "\n".join(
        f"  {name}: {caption}" for name, caption in sorted(captions.items())
    )
    shot_lines = []
    for idx, s in sorted(image_shots.items()):
        arabic = (getattr(s, "typography_text", "")
                  or getattr(s, "caption_text", "") or "").strip()[:100]
        shot_lines.append(
            f"  shot {idx} [{s.visual}, {s.duration:.1f}s] "
            f"query: {s.search_query!r}"
            + (f"  narration: {arabic}" if arabic else "")
        )

    user_prompt = _ASSIGN_USER_TMPL.format(
        book_title=book_title or "unknown",
        character_name=character_name or "not specified",
        n_photos=len(captions),
        photo_lines=photo_lines,
        n_shots=len(image_shots),
        shot_lines="\n".join(shot_lines),
    )

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=anthropic_api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=4000,
            system=_ASSIGN_SYSTEM.replace("{max_uses}",
                                          str(max_uses_per_photo)),
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = msg.content[0].text
    except Exception as exc:  # noqa: BLE001 — fail-open, waterfall stands
        log.warning("photo_bank: assignment call failed: %s — no assignments",
                    exc)
        return {}

    if debug_dir is not None:
        try:
            Path(debug_dir).mkdir(parents=True, exist_ok=True)
            (Path(debug_dir) / "photo_bank_assignment_raw.txt").write_text(
                raw, encoding="utf-8")
        except OSError:
            pass

    try:
        data = _parse_assignment_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("photo_bank: unparseable assignment response: %s", exc)
        return {}

    result: dict[int, str] = {}
    uses: dict[str, int] = {}
    for idx_str, fname in (data.get("assignments") or {}).items():
        try:
            idx = int(idx_str)
        except (TypeError, ValueError):
            log.warning("photo_bank: non-integer shot index %r — dropped",
                        idx_str)
            continue
        if idx not in image_shots:
            log.warning("photo_bank: assignment to unknown shot %d — dropped",
                        idx)
            continue
        if not isinstance(fname, str) or fname not in captions:
            log.warning("photo_bank: shot %d assigned unknown file %r — "
                        "dropped", idx, fname)
            continue
        if uses.get(fname, 0) >= max_uses_per_photo:
            log.warning("photo_bank: %s already used %d time(s) — shot %d "
                        "dropped", fname, uses[fname], idx)
            continue
        result[idx] = fname
        uses[fname] = uses.get(fname, 0) + 1

    log.info("photo_bank: %d/%d image shots assigned from %d bank photos",
             len(result), len(image_shots), len(captions))
    return result
