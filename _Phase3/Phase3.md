# Phase 3 — Visual Generation · Session Handoff

> **Working tree**: `Lamahat/_Phase3/`
> **Pipeline status**: end-to-end working on Colab CPU. Latest render: 43-shot
> plan, 391 s, 26.6 MB MP4, ~21 min wall time. The `final_cut_3a.mov` in the
> repo (12.8 MB / 181 s) is a partial preview uploaded to fit GitHub's file-size
> limits — not the full render. The full deliverable is the cell-13 zip from
> `_phase3_b2c.ipynb` (saved to Drive as `output_files.zip → final_cut.mp4`).
> Audio (391 s) and plan (43 shots, 0.00 → 391.00 s) match end-to-end; there is
> no truncation bug.
>
> **Where the latest code lives**: drop zips at
> `_Phase3/artifacts/phase3 v3 *.zip` (each zip is one "patch drop" from the
> prior conversation, chronological). The most recent is
> `phase3 v3 resilience.zip` — its `PATCH_NOTES.md` is the canonical changelog
> for the current state of `plan.py`, `render.py`, and `sources/__init__.py`.
> **Where the fonts live**: `_Phase3/fonts/` ships the four Amiri TTFs
> (Regular, Bold, Italic, BoldItalic) directly in the repo.

---

## 1. What Phase 3 Now Is (vs. the v1 you may remember)

The original Phase 3 was section-based: parse 4 Arabic sections, pull 2-3
Wikimedia images per section, Ken-Burns them, crossfade, mux. That code still
lives at `phase3/__init__.py → generate_background_video()` and remains
reachable from Streamlit. It has been **superseded** by a *shot-based* pipeline
that is now the default end-to-end path.

**v2/v3 architecture**: a *shot plan* is the source of truth. The plan is a
list of 30–65 timestamped `Shot` dataclasses produced by one Claude Sonnet 4.6
call; the renderer executes the plan without making any creative choices.
Plans are JSON, inspectable, diff-able, regeneratable from cache.

```
Script + Audio ──► align.py ──► word timings (WhisperX | Whisper | interp)
                                       │
                                       ▼
                                   plan.py  ──► shot_plan.json (one Sonnet call)
                                       │
                                       ▼
                                  render.py  ──► MP4
                                       ▲
                                       │
                              sources/Fetcher  (user → book → cache → LoC →
                                                 Wikimedia → IA → Pexels,
                                                 Haiku-vision-scored)
```

The plan/render split is the architecture's core insight, and it deserves to
be defended: planning costs a Sonnet call (~$0.10 + ~90 s wall) and renders
cost ~20 min of CPU. When iterating on visuals you re-render; when iterating
on shot choices you re-plan. The auditor and the manifest both consume the
plan JSON without rendering — that means most diagnostic work happens in
seconds, not minutes.

### Two CLIs

| CLI | Purpose | Stops at |
|-----|---------|----------|
| `phase3_run.py` | Plan **and/or** render in one go. Owns the v1 path too. | configurable: `--dry-run`, `--keywords-only`, `--align-only`, `--plan-only`, or full render |
| `render_plan.py` | Render a previously-saved plan to MP4. | always produces an MP4 (or writes a manifest with `--build-manifest`) |

### File map

```
_Phase3/
├── phase3_run.py             # Plan-OR-render CLI
├── render_plan.py            # Render-only CLI (consumes plan JSON)
├── audit_plan.py             # Quality audit of a saved plan
├── _phase3_b2c.ipynb         # Colab driver — the canonical run lives here
├── samples/al_askari_script.txt
├── output/                   # render.log (51-shot prior run), audio, MOV preview
├── fonts/                    # Amiri-{Regular,Bold,Italic,BoldItalic}.ttf
├── artifacts/                # Drop archive (one zip per prior-session patch)
│   ├── phase3 v2 foundation.zip       # align.py + plan.py + phase3_run.py
│   ├── phase3 v2 patch1.zip           # streaming + resilient JSON parser
│   ├── phase3 v2 patch2.zip           # caps→8s + audit_plan.py
│   ├── phase3 v2 patch3.zip           # caps type-aware + title_card semantics
│   ├── phase3 v3 typography.zip       # Family A typography module (864 LOC)
│   ├── phase3 v3 stage1.zip           # render.py (Stage 1 renderer)
│   ├── phase3 v3 fontpatch.zip        # auto-discovery + download fallback
│   ├── phase3 v3 stage2.zip           # sources/ subpackage
│   └── phase3 v3 resilience.zip       # ← latest. caps→10/12/7 + merge pass + per-shot try/except
└── phase3/
    ├── __init__.py           # v1 entrypoint — legacy, still callable from Streamlit
    ├── parser.py             # Arabic section regexes + estimate_durations
    ├── align.py              # WhisperX | Whisper | interpolation → WordTiming list
    ├── plan.py               # Sonnet 4.6 shot planner + Shot dataclass + JSON I/O
    ├── render.py             # Plan → MP4 (assets, motion, captions, mux)
    ├── typography.py         # Pillow Family A typography cards (864 LOC)
    ├── compositor.py         # v1 background video assembler
    ├── effects.py            # ffprobe wrapper + Ken Burns helpers (v1)
    ├── keywords.py           # v1 keyword generator
    ├── pexels.py             # v1 video-clip fetcher
    ├── subtitler.py          # v1 ASS subtitle writer
    ├── wikimedia.py          # v1 image fetcher + vision scorer
    ├── render_previews.py    # Dev helper — render a typography template grid
    ├── test_smoke.py / test_typography.py / test_resilient.py
    └── sources/              # v2 image-fetch waterfall
        ├── __init__.py       # Fetcher orchestrator + FetcherConfig
        ├── base.py           # Source ABC, ImageCandidate, FetchResult
        ├── loc.py            # Library of Congress JSON API
        ├── wikimedia.py      # MediaWiki API; 400 px min dimension filter
        ├── internet_archive.py  # archive.org advancedsearch
        ├── pexels.py         # Pexels v1 photos endpoint
        ├── user_upload.py    # shot_NN.jpg overrides from a user directory
        ├── book_extract.py   # Phase 1a photo bank (vision-scored)
        ├── cache.py          # Disk cache keyed by query hash
        └── vision.py         # Claude Haiku vision scorer (0–3 × 3 axes, total 0–9)
```

---

## 2. The Shot Data Model

```python
@dataclass
class Shot:
    start: float                                # seconds from t=0
    end: float
    visual: ShotVisual                          # see taxonomy below
    search_query: str = ""                      # English; "" for typography
    source_hint: str = "auto"                   # "wikimedia" | "loc" | "pexels" | "auto"
    motion: ShotMotion = "slow_push"
    motion_intensity: float = 1.0
    typography_template: TypographyTemplate | None = None
    typography_text: str = ""                   # Arabic, verbatim from script
    caption_text: str = ""                      # auto-filled from word_timings
    show_caption: bool = True
    note: str = ""                              # planner's free-form rationale
    section_id: str = ""                        # auto-assigned by midpoint
```

**Visual taxonomy** (8 kinds): `portrait`, `location`, `object`, `archive`,
`broll`, `typography`, `title_card`, `section_mark`.

**Motion taxonomy** (7 kinds): `static_hold`, `slow_push`, `fast_push`,
`slow_pull`, `pan_left`, `pan_right`, `ken_burns`. In `render.py` static
motion is applied to typography and placeholder cards; the listed motions only
fire for fetched real images.

**Typography templates** (5): `pull_quote`, `name_reveal`, `date_stamp`,
`chapter_heading`, plus implicit `title_card` / `section_mark` styles. All
rendered by `typography.py`.

### Plan invariants (enforced by `plan._validate_plan`)

These are the **current** caps (post the `phase3 v3 resilience` patch):

| Visual type   | Hard cap | Below cap = no split |
|---------------|----------|----------------------|
| `typography`  | 12 s     | most pull quotes pass |
| `portrait`    | 12 s     | full-face holds       |
| `archive`     | 10 s     | period photos         |
| `broll`       | 10 s     | atmosphere shots      |
| `location`    | 10 s     | establishing shots    |
| `object`      | 10 s     | manuscripts, maps     |
| `section_mark`|  7 s     | interstitial pauses   |
| `title_card`  |  7 s     | open + close          |

Beyond a cap, `_validate_plan` splits the shot into ~5 s pieces and tags each
`[auto-split k/n]`.

After the split logic, a **merge-adjacent-duplicates** pass fuses shots that
share `(visual, search_query)` or `(visual, typography_text)` back together
and concatenates their `caption_text`. This catches anything the splitter
shouldn't have touched and also fixes plans where Sonnet itself emitted
duplicates. On the most recent v2 plan: 64 → 59 shots after merge; on the
prior 69-shot run (worst case): estimated 69 → ~43.

Boundary snapping (`_snap_to_word_boundaries`) snaps every shot edge to a
real word boundary from the alignment timings; minimum shot duration after
snap is 1.5 s. Field exclusivity (`_normalise_fields`) clears `search_query`
on typography-kind shots and `typography_text` on image-kind shots so the
renderer's dispatch is unambiguous.

---

## 3. Sources Subsystem (Stage 2)

`sources/Fetcher.fetch_for_shot(query, shot_index)` runs this priority order
(committed code, `sources/__init__.py:77`):

1. **User upload** — `--user-dir <path>`. File matched by name pattern
   `shot_NN.jpg` (NN = 1-indexed shot number) or by `manifest.json`.
2. **Book extract** — `--book-extracts <Phase 1a photos.zip or dir>`.
   Vision-scored against the shot query (requires `--anthropic-key`). If
   vision is disabled, book extracts are skipped with a one-time warning.
3. **Disk cache** — `~/.cache/lamahat/images` keyed by query hash. Disable
   with `--no-cache`.
4. **Live web fetch** in order: `LoC → Wikimedia → IA → Pexels`. All
   candidates from all sources are pooled, downloaded, vision-scored, then
   ranked.
5. **Placeholder card** — last resort. Cream card with the search query
   printed inside, plus an `[auto-split k/n]` / motion / timing badge if
   debug mode is on. The renderer always falls back to this rather than
   leaving a gap, so the audio sync is preserved.

`VisionScorer` (Haiku, `claude-haiku-4-5-20251001`) emits three integer
scores per image, 0–3 each on `(subject, quality, cinematic)`, total 0–9.
Threshold to keep: `total ≥ 4 AND subject ≥ 1`. On API exception the
candidate is assigned `(2, 2, 1) = 5` and kept (fail-open) — see §7.4 for
why this needs refinement.

### Required-images manifest

`render_plan.py --build-manifest output/required_images.txt` writes a
human-readable manifest listing every image-kind shot with its visual, the
duration, and Sonnet's English search query. This is the recommended first
move on any new script: review the manifest before any rendering happens.
The user can also use this manifest to decide which shots to override with
their own images (drop them into `--user-dir` as `shot_NN.jpg`, or supply a
`manifest.json` for semantic names).

---

## 4. Rendering Pipeline (one MP4 from one plan)

`render.render_video(shots, out_path, *, audio_path, audio_duration_sec, config, on_progress)`:

1. For each shot:
   - Build a 1920×1080 PNG asset:
     - Typography visuals → `typography.render()` (Family A card)
     - Image visuals → `Fetcher.fetch_for_shot()` → copy chosen JPEG to PNG
     - Fallback → `_placeholder_card()` (cream card with the search query)
     - Final fallback on exception → `_error_card()` (so the timeline doesn't
       collapse — audio sync depends on every shot producing a clip of its
       planned duration)
   - Encode the PNG to an MP4 clip of the shot's exact duration. Motion only
     applies if `is_real_image=True`; typography and placeholders always
     `static_hold`. Zoom is computed against a 1.6× buffer to avoid blurry
     pan-edges.
   - The per-shot loop is wrapped in `try/except` (resilience patch). On
     failure, a visible error card is emitted with the shot index and the
     truncated exception text. The render continues.
2. **Stream-copy concat** of all shot clips → `background.mp4`. Works only
   because every clip uses identical encoder settings (`libx264 -preset
   ultrafast -crf 22 -pix_fmt yuv420p -r 25`). Change one shot's profile and
   the concat silently breaks.
3. **ASS captions** (`_write_captions`): white Amiri text with charcoal
   outline (BorderStyle 1). Typography shots are excluded
   (`s.visual not in TYPOGRAPHY_VISUALS`) because the typography text *is*
   the on-screen text — drawing the caption again would double up. 0.05 s
   pre-roll on each caption.
4. **Final mux** (`_mux_final`): single FFmpeg pass that re-encodes the
   video (required to burn subs), adds AAC audio at 192 kbps with
   `-shortest`, then `-t max_duration` if set. The re-encode pass is ~5
   minutes of the ~21-minute total.

Everything FFmpeg is shelled via `subprocess.run`, working under
`tempfile.TemporaryDirectory` so RAM stays low — important for Streamlit
Cloud's 1 GB ceiling.

---

## 5. Decisions Inherited From the Prior Conversation

These are not bugs or open questions; they are settled product choices.
Useful to surface so the next session knows what's intentional.

| Decision | Choice | Why |
|----------|--------|-----|
| Target platform | YouTube long-form, 1920×1080 @ 25 fps | Chosen over Shorts/Reels; the visual grammar is different and we can't optimize for both. |
| Budget tier | ~$0.20–0.50/video (was $0.06) | Quality matters more than per-render cost for this project. |
| Typography aesthetic | **Family A — Aljazeera Documentary editorial** | Cream/charcoal, restrained Amiri, hairlines, no Islamic geometric ornament. Reads as native Arabic editorial, not imported Western prestige TV. Family B (cinematic dark gradient) and Family C (manuscript with corner ornaments) were explicitly rejected. |
| Color grading | Single warm knob, default cinematic-warm | Section-aware grading deferred until needed. |
| Section transitions | **Option (c)** — the `section_mark` shot *is* the transition | No crossfades anywhere except the final mux. Hard cuts elsewhere. |
| Captions style | White text + charcoal outline (BorderStyle 1) | This is a *fallback from a richer Family A design* (charcoal text on translucent cream bar). libass ignores alpha on BorderStyle 3 BackColour, so the translucent bar rendered as opaque white. Don't re-attempt the bar without testing the alpha behavior first. |
| Caption font size | 4.2% of frame height | Documentary-readable; smaller than mass-market TV captions. |
| Pacing target | 5.0–6.5 s avg shot duration | Documentary tempo, not TikTok. Current plan averages 9.09 s (see §7.6). |
| Shot caps + merge | 12s/10s/7s by visual type + merge adjacent duplicates | Resilience-patch behavior; supplants the earlier 6s/8s caps. |
| Typography density | 25–35 % of shots | Within target on the 43-shot v2 plan (35 %). |
| WhisperX vs interpolated | Both available; default is whichever installs cleanly | Interpolated is acceptable for now; ElevenLabs TTS upgrade likely lands first. |
| Source priority | LoC → Wikimedia → IA → Pexels | LoC first because of its MENA collection 1880-1940. In practice §7.3 shows none of LoC/Wikimedia/IA return candidates for the al-Askari queries — that is the open question. |
| Vision rubric | 0–3 × 3 axes, total 0–9, threshold 4 with subject ≥ 1 | Replaces v1's binary yes/no; allows ranking within kept candidates. |
| Image override path | `--user-dir` + `--book-extracts` + `--build-manifest` | All three implemented and tested end-to-end. |

### Unresolved strategic question from the prior conversation

The prior session ended with the previous Claude proposing **path (C)**:
abandon the web-source rabbit hole entirely, route Phase 1a book extracts
through one Sonnet call for global photo-to-shot matching, treat web sources
as nice-to-have. **The user never explicitly accepted or rejected this.**

The argument for (C) is strong:
- The book's own photos are curated by the book's editor for exactly this
  subject. They beat any text-search-based web fetch on relevance.
- LoC/Wikimedia/IA are returning 0 candidates for every al-Askari query
  (§7.3), so the "automatic" path is effectively producing Pexels-only
  results — modern stock for a 1904–1936 documentary.
- A single Sonnet pass mapping 20 book photos to 30 shots is cheaper
  (~$0.05) and produces better results than 30 × 4 vision-scoring calls
  on bad candidates.

The argument against (C) is also real:
- It only works for books that come with extracted photos. For scripts
  authored without a source book (or with very few extractable images),
  there is no fallback except generic placeholders.
- LoC/Wikimedia/IA can probably be made to work with query-construction
  fixes (§7.3). Throwing them out gives up that future.

**Recommendation**: do both. Implement (C) as the primary path for any
script that has book extracts; keep the web-source waterfall as fallback for
shots where book extracts don't match well. The vision rubric already gives
us a way to decide ("if the best book-extract score is ≥ 6, use it;
otherwise fall back to web sources"). Make (C) the default when
`--book-extracts` is supplied.

---

## 6. The Canonical Run, Decoded

The notebook `_phase3_b2c.ipynb` is the authoritative reference for what
works today. Cell-by-cell:

| Cell | What it does | What its output proves |
|------|--------------|------------------------|
| 0 | Mount Drive, copy `_Phase3/` into `/content` | Colab working dir is `/content`, not `_Phase3/` |
| 1 | `pip install anthropic` (0.102.0) | Sonnet + Haiku reachable |
| 2 | WhisperX/Whisper install (commented out) | Alignment uses **interpolated** backend |
| 3 | `apt install fonts-hosny-amiri` (0.113-1) | System Amiri available via fontconfig |
| 4 | `pip install arabic-reshaper python-bidi` | Fallback path for non-libraqm Pillow builds |
| 5–6 | Matplotlib + `phase3.typography.FONT_PATHS` sanity check | Reveals an Amiri-discovery bug (§7.1) |
| 7 | Load API keys from Colab Secrets | Both set |
| 8 | `phase3_run.py --align-only --align-backend interpolated` | 653 word tokens, **only 2 sections parsed** (§7.2) |
| 9 | `phase3_run.py --plan-only` | Sonnet returns 43 shots covering 0.00–391.00 s in 91.4 s; one call (~$0.10) |
| 10 | `audit_plan.py` | 0 gaps/overlaps; 35 % typography (in target); 14 % auto-split (acceptable); 22 queries averaging 7.5 words; **no bare queries** |
| 11–12 | `render_plan.py` background + tail-monitor | "Done in 1263 s — output/final_cut.mp4 (26.6 MB)" |
| 13 | Zip outputs (excluding the .mp3) | `output_files.zip` with `final_cut.mp4 + render.log + plan.json + word_timings.json + planner_raw_response.txt` |
| 14 | Copy zip to Drive | Final deliverable: `/content/drive/MyDrive/_Phase3/output_files.zip` |

### Audit findings (cell 10, verbatim)

```
Total shots:        43
Plan timeline:      0.00s → 391.00s (391.0s)
Average shot:       9.09s
Range:              4.49s – 12.17s
✓  No gaps or overlaps

Visual types:
   typography      15 (  35%) ██████████   ← within target 25-35%
   archive          8 (  19%)
   portrait         7 (  16%)
   broll            4 (   9%)
   section_mark     4 (   9%)
   location         3 (   7%)
   title_card       2 (   5%)               ← open + close

Motion types:
   static_hold     28 (  65%)
   slow_push       13 (  30%)
   pan_right        2 (   5%)

Section coverage:
   opening         33 shots
   closing         10 shots                 ⚠ See §7.2

✓  Auto-split shots: 6/43 (14%)
Typography texts: 21 unique (avg 11.4 words)
Search queries: 22 non-empty, avg 7.5 words   ✓ none bare
```

Healthy on every dimension except *section structure*. Auto-split rate 14 %
is well below the 20 % "tighten the prompt" line.

---

## 7. Open Issues, In Priority Order

### Tier 1 — these distort *every* output

#### 7.1 Amiri discovery falls through despite the repo bundling fonts

`_Phase3/fonts/` ships `Amiri-Regular.ttf`, `Amiri-Bold.ttf`, `Amiri-Italic.ttf`,
`Amiri-BoldItalic.ttf` — the four required weights, right there in the repo.
But `typography._discover_amiri_fonts()` walks
`LAMAHAT_AMIRI_DIR → fc-match → ~10 well-known paths → upstream download` and
*never looks at `_Phase3/fonts/`*. On Streamlit Cloud (ephemeral, no
fontconfig refresh between cold starts) every run pays the 6 MB upstream
download. On Colab the same problem appears because cell 5's matplotlib
font registration sometimes runs before `fc-cache -fv` from cell 3, and
`fc-match Amiri:style=Regular` returns DejaVu — which then fails the
`"amiri" in name.lower()` sanity check at `typography.py:150`, and we fall
through to download.

The fix is one strategy insertion. After the env-var override and before
`fc-match`, look for `<repo_root>/_Phase3/fonts/Amiri-*.ttf`:

```python
# ── Strategy 1.5: repo-bundled fonts ──────────────────────────────
repo_fonts = Path(__file__).resolve().parent.parent / "fonts"
if (repo_fonts / "Amiri-Regular.ttf").exists():
    found = {k: repo_fonts / fname for k, fname in wanted.items()}
    if all(found[k].exists() for k in ("regular", "bold", "italic", "bold_italic")):
        log.info("Amiri fonts loaded from repo bundle %s", repo_fonts)
        return {k: str(v) for k, v in found.items() if v.exists()}
```

This makes the renderer self-contained: no apt, no fontconfig, no download.
The Streamlit Cloud cold-start cost drops to zero, and the Colab notebook
no longer cares whether cells 3 and 5 ran in the "right" order.

#### 7.2 Section parser only recognises rigid template headers

The notebook's alignment cell reports `653 word tokens, 2 sections`. The
real script has 5 logical sections (opening + 3 descriptive points +
closing) but `parser._SECTION_HEADERS` only matches the rigid v1 template
(`النقطة الأولى/الثانية/...` and `الخاتمة` and `تقديم الكتاب`). The
current Phase 1b summariser emits descriptive titles instead:

```
Line  9: من الموصل إلى الاستانة — رحلة التحديث والطموحْ
Line 17: الصراع الأيديولوجي والسياسي — بين الولاء والحلمْ
Line 25: الحرب والاختبار النهائي — الفعل والالتزامْ
Line 33: الخاتمة: شهادة لا تموتْ                    ← only this matches
```

Result: `opening = lines 1–32` (one monolithic 287-second blob) and
`closing = lines 33–43`. The planner sees a single huge section.

In the prior session this was *accepted* — the reasoning was that Sonnet
inserts `section_mark` shots at tonal breaks anyway, so the structural
mapping is functionally recovered. That reasoning has merit: `section_mark`
shots are present at the right places in the 43-shot plan. But the loss
shows up in `audit_plan.py`'s "Section coverage" report (`opening 33 shots,
closing 10 shots`) and in any future work that wants to do per-section
color grading or per-section image sourcing strategies.

Fix options, ranked by leverage:
1. **Sidecar JSON from Phase 1b**: emit `section_boundaries.json` alongside
   the script. The cleanest fix; keeps the script copy-pasteable for the
   user, doesn't require parser changes.
2. **Loosen the parser**: detect *any* line that ends with `.` or `ْ` and
   sits between blank lines as a candidate header. Cross-check with line
   length (headers are typically < 80 chars).
3. **Restore the rigid template in the Phase 1b prompt**. Quick fix but
   reduces script readability for the user.

#### 7.3 LoC / Wikimedia / Internet Archive return 0 candidates per query

The single biggest visual-quality issue. In the latest 43-shot plan,
*every* image shot's query went through this waterfall:

```
LoC:               0 candidates for 'Jafar al-Askari Iraqi general historical portrait'
Wikimedia:         0 candidates for 'Jafar al-Askari Iraqi general historical portrait'
Internet Archive:  0 candidates for 'Jafar al-Askari Iraqi general historical portrait'
Pexels:            3 candidates for 'Jafar al-Askari Iraqi general historical portrait'
```

But Wikimedia Commons demonstrably has `Category:Mahmud_Shevket_Pasha`
with PD photographs; LoC has 1880-1940 MENA holdings; IA has period books.
The problem is **query construction**, not content availability. Probable
causes in descending order:

1. **Over-specific multi-word queries**. MediaWiki's `gsrsearch` is
   phrase-AND. Six tokens — `'Jafar al-Askari Iraqi general historical
   portrait'` — require all six in file metadata, which is rare. Same for
   LoC and IA. **Fix**: add `query_simplify()` that strips generic tails
   (`portrait historical photograph archive picture`), keeps proper nouns
   + dates. Try simplified first; full form as fallback.
2. **400 px minimum dimension filter (Wikimedia)**.
   `wikimedia.py:_MIN_DIMENSION = 400` rejects every result whose
   `thumbwidth/thumbheight` is below 400. Many period photographs in
   Commons are stored as 300–380 px JPEGs and get rejected even when
   they're exactly right. **Fix**: drop to 320 or remove when `thumburl`
   is present.
3. **Wikimedia `-diagram -anatomy -chart -schematic` exclusion**. Combined
   with already-narrow queries it removes borderline matches. Make it
   opt-in.
4. **LoC's facet filter**. `'fa': 'online-format:image|original-format:photo,print'`
   returns 0 sometimes even when the same free-text query in LoC's web UI
   returns thousands. Try without `fa` and post-filter in code.
5. **IA's `mediatype:(image)` filter**. Excludes `mediatype:texts` items
   that contain images. Most period-book scans on IA are `texts` with
   downloadable image derivatives. Broader search + derive image URL from
   the metadata endpoint.
6. **Network timeouts**. Log shows several `LoC search failed: The read
   operation timed out` at `timeout=20`. One retry with exponential backoff.

Concrete next steps in priority order:
- Add `query_simplify(q)`; test on the 22 queries from the current plan.
- Lower Wikimedia `_MIN_DIMENSION` to 320 (or skip when `thumburl` is set).
- Add unit tests with known-good queries (`Jafar al-Askari`, `Faisal bin
  Hussein 1920`, `Mahmud Shevket Pasha`) that **fail** if a source returns 0.
- Consider Wikipedia article images via `prop=pageimages|images` on the
  article slug — one call, no vision pass needed, gets the lead image.

The bigger pivot here is path (C) from §5: if book extracts work well
enough, the urgency of fixing web sources drops considerably.

#### 7.4 Vision scoring fails open in a way that defeats source priority

`VisionScorer.score()` catches API exceptions and stamps `(2, 2, 1) = 5`
(`_apply_neutral_score`) so the candidate isn't silently dropped. In the
51-shot run captured at `_Phase3/output/render.log`, the first vision call
succeeded; from shot 5 onward every call returned HTTP 400
`credit_balance_too_low`. With **all** candidates from all sources tied at
5, `sorted()` is stable and the **original list order** breaks the tie.
Original order is `[LoC, Wikimedia, IA, Pexels]`. LoC/Wikimedia/IA all
returned 0 candidates anyway (§7.3), leaving Pexels as the sole survivor —
so Pexels wins every shot **by elimination, not by quality**.

| Shot query | Pexels winner (verbatim from log) |
|-----------|------------------------------------|
| `Jafar al-Askari Iraqi general historical portrait` | "A stylish businessman with a briefcase exits a plane" |
| `Mahmud Shevket Pasha Ottoman general portrait historical` | "Close-up of bronze Ottoman soldier statues in Istanbul" |
| `Arab Revolt 1916 Sharif Hussein Faisal forces historical photograph` | "Libyan soldiers holding rifles and red flares" |
| `Jafar al-Askari portrait Iraqi statesman historical` | "Vandalized sculpture in a Baghdad park" |

Fixes:
- **Restore Anthropic credits** before any further benchmarking — this is
  critical path.
- **Demote unscored candidates** only when at least one scored cleanly:
  in `vision.rank_candidates`, partition the pool into "real-scored" and
  "neutral-5"; if any real-scored exist, drop the neutral-5s.
- **Circuit breaker**: after N consecutive vision errors with the same
  error class (`credit_balance_too_low`, `rate_limit_error`), disable
  vision entirely for the rest of the run and log once. The 51-shot log
  is ~70 KB and almost entirely one repeated error.

#### 7.5 `--verbose` floods the log with base64 image data

`render_plan.py:131` and `phase3_run.py:388` both do `level = logging.DEBUG if
args.verbose else logging.INFO` and apply it to the root logger. That
turns on DEBUG for `anthropic` and `httpx`, which dump every API request
body to the log — including base64-encoded images. A single 800 px JPEG
becomes ~388 KB of base64; one full vision-scoring run is hundreds of MB
of log file, and `tee` typically can't keep up.

The previous Claude staged the fix but it hasn't shipped:

```python
level = logging.DEBUG if args.verbose else logging.INFO
logging.basicConfig(level=level, ...)
# Silence the noisy third-party loggers regardless of verbosity
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
```

Until this lands, `--verbose` is unsafe to use with `tee`. The mid-pipeline
"crashes" reported in the prior conversation were almost certainly the
terminal or `tee` buffer giving up, not Python actually crashing — the
resilience patch's per-shot `try/except` makes a Python-level crash quite
hard to trigger.

### Tier 2 — quality plateaus

#### 7.6 Shot duration distribution skews long

Audit: average 9.09 s, range 4.49–12.17 s. The planner's prompt targets
~5 s per shot, and the hard caps are 7/10/12 s by visual type. The 14 %
auto-split rate shows Sonnet is brushing against the caps. Documentary
pacing favours 4–6 s holds; 9 s averages feel slow. Two reasons it ran
long:

1. Only 2 sections parsed (§7.2) → planner had less structural pressure
   to introduce variety.
2. `_sized_target_shots(391, 5.0)` returns 65; the planner was told to
   aim for 65 shots but returned 43. Sonnet's interpretation of
   "documentary pacing" tilts longer than the prompt asks.

Fixes:
- Tighten the prompt: change "5–8 s on typography and portraits" → "4–6 s
  on typography and portraits". Add an explicit rule: "Average shot
  duration must be 5.0–6.5 s."
- In `_validate_plan`, if `avg < 5.0` or `avg > 7.0`, log a warning so
  this regresses visibly.

#### 7.7 Forced alignment uses the interpolated backend

WhisperX/Whisper install is commented out in cell 2 of the notebook. The
interpolated backend distributes time by character count (~12 chars/sec).
Drift can be ±200–500 ms per word. Caption sync is acceptable for a
documentary; shot-boundary precision suffers because
`_snap_to_word_boundaries` snaps to *interpolated* word endpoints.

For Streamlit Cloud's 1 GB ceiling, WhisperX peaks may collide with FFmpeg
work. Options:
- Run alignment in a separate subprocess so the model RAM is reclaimed
  before the renderer starts.
- Accept interpolated for now; revisit when ElevenLabs TTS lands (cleaner
  audio → easier alignment).

### Tier 3 — polish

#### 7.8 Stylish placeholders for unmatched image shots

When all sources for an image shot return nothing or vision rejects
everything, `render._placeholder_card()` produces a cream card showing
the search query in Latin. Replace with a fully-styled typography card
that reuses the *Arabic* key phrase from the *same section's* text —
turns gaps into intentional design moments.

#### 7.9 Animated word-by-word reveal on typography shots

Currently `static_hold`. A 0.4 s per-word reveal on `pull_quote` and
`name_reveal` would dramatically improve perceived production value. The
shaping is already RTL-correct (libraqm or arabic_reshaper + python-bidi),
so it's just FFmpeg subtitle timing on top of the existing PNG.

#### 7.10 ElevenLabs TTS (handoff from Phase 2)

Tier 2 in the master plan; cleaner audio also helps alignment (§7.7).
Stub exists in `phase2/tts.py`.

---

## 8. Working Configuration

### CLI invocations from `_phase3_b2c.ipynb`

```bash
# Cell 8 — alignment sanity check (interpolation only; instant)
python phase3_run.py \
  --script samples/al_askari_script.txt \
  --audio  output/al_askari_audio.mp3 \
  --align-only \
  --align-backend interpolated

# Cell 9 — plan the shots (one Sonnet call, ~90 s, ~$0.10)
python phase3_run.py \
  --script         samples/al_askari_script.txt \
  --audio          output/al_askari_audio.mp3 \
  --book-title     "مذكرات جعفر العسكري" \
  --character-name "Jafar al-Askari" \
  --plan-only \
  --save-plan      output/al_askari_plan_v2.json
# NOTE: --character-name is in English (Latin), not Arabic — for the
# benefit of LoC/Wikimedia/IA which can't search Arabic well.

# Cell 10 — audit the plan
python audit_plan.py output/al_askari_plan_v2.json

# Cell 11 — render (~21 min, runs in &-background)
python render_plan.py \
  --plan           output/al_askari_plan_v2.json \
  --audio          output/al_askari_audio.mp3 \
  --output         output/final_cut.mp4 \
  --anthropic-key  "$ANTHROPIC_API_KEY" \
  --pexels-key     "$PEXELS_API_KEY" \
  --book-title     "مذكرات جعفر العسكري" \
  --character-name "Jafar al-Askari" \
  > output/render.log 2>&1 &
```

### Optional audit with audio cross-check

```bash
python audit_plan.py output/al_askari_plan_v2.json \
  --script samples/al_askari_script.txt \
  --audio  output/al_askari_audio.mp3
```

Adds: plan-end vs. real audio duration delta, verbatim check of typography
text against the script.

### Optional manifest mode (no network)

```bash
python render_plan.py \
  --plan output/al_askari_plan_v2.json \
  --build-manifest output/required_images.txt
```

Lists every image-kind shot with visual / duration / search query. Useful
for review before rendering, and for deciding which shots to override with
`--user-dir`.

### Required environment

| Variable | Where | Required for |
|----------|-------|--------------|
| `ANTHROPIC_API_KEY` | `.env`, `--anthropic-key`, or Colab Secrets | Sonnet planner, Haiku vision scoring |
| `PEXELS_API_KEY`    | same | Pexels image source (only working web source until §7.3 lands) |

### Models used

| Task | Model | Cost / 3-min video |
|------|-------|---------------------|
| Shot planner (one call) | `claude-sonnet-4-6` (24,000 max_tokens, streaming) | ~$0.10 |
| Image relevance scorer | `claude-haiku-4-5-20251001` (vision, ~150 max_tokens) | ~$0.50 (for ~100 candidates) |
| Forced alignment | WhisperX (currently disabled) | $0 either way |

---

## 9. Recommended Session Order

By leverage, not difficulty.

1. **Restore Anthropic credits** before any further benchmarking. Without
   them both the planner and the scorer degrade silently (§7.4).
2. **Patch §7.5 `--verbose` logger noise** before re-running any
   diagnostic. Two lines of code. Otherwise the next "crash" report will
   be the same mid-base64 truncation as the last one.
3. **Decide on path (C)** from §5. If book extracts become the primary
   image source, much of the urgency around §7.3 (LoC/Wikimedia/IA
   queries) drops away. If not, prioritize §7.3.
4. **Fix the section parser** (§7.2). Sidecar JSON from Phase 1b is the
   cleanest path.
5. **Patch the vision fail-open policy** (§7.4). Even when credits are
   available, the policy should demote unscored candidates *only when
   scored ones exist*.
6. **Pillow typography placeholder cards** (§7.8) — converts the "TBD"
   look into a design feature when sources fail.
7. **Repo-bundled font discovery** (§7.1) — eliminates a 6 MB cold-start
   download on Streamlit Cloud. Trivial change, big infrastructure win.
8. **Tighten shot duration distribution** (§7.6).

---

## 10. Things Not To Touch (or touch with care)

- **The plan/render split.** Two CLIs, two responsibilities. Mixing them
  was the original mistake; the split is what makes iteration fast.
- **`_validate_plan` invariants.** Renderer assumes them. Loosen one →
  break the concat pass or the caption layer.
- **The current caps (12/10/7 by visual type) and merge pass.** These are
  the resilience-patch values. Lowering caps will regenerate the 72 %
  auto-split issue. Disabling merge will fragment captions across what
  should be single holds.
- **Arabic rendering uses `libraqm` when available, falls back to
  `arabic_reshaper` + `python-bidi`.** Don't add a third path. Don't use
  FFmpeg `drawtext` for any Arabic — it has no bidi.
- **800 px image-resize before vision scoring** (`vision.py:117`). Larger
  → API 400.
- **Stream-copy concat in `_concat_clips`.** Works only because every
  shot clip uses identical encoder settings. Changing one shot's profile
  silently breaks the concat — fall back to filter_complex concat if you
  need per-shot variations.
- **`fail-open` in `VisionScorer.score`.** Don't flip it to fail-closed —
  that would drop *all* candidates on a 5 s Anthropic blip. Instead,
  demote unscored candidates only when scored ones exist (§7.4).
- **Caption style is BorderStyle 1 (outline+shadow), not BorderStyle 3
  (opaque box).** The Family A intent was a translucent cream bar; libass
  ignores alpha on BorderStyle 3 BackColour and renders it as solid white.
  If you re-attempt the bar, verify the alpha behavior in your libass
  version first.
- **`title_card` and `section_mark` ignore `typography_template` hints.**
  The renderer forces `title_card` template on `visual=title_card` shots
  even if Sonnet annotated `typography_template=chapter_heading`. Don't
  re-enable hint precedence here; it leads to inconsistent openings.
- **Stage 1 backward compatibility.** `render_video(..., fetcher=None)`
  must still produce the all-placeholder rough cut. The Stage 2 fetcher
  is opt-in.

---

## 11. Quick Reference — Useful One-Liners

```bash
# Histogram of shot types in a plan
python -c "import json; from collections import Counter; \
  d=json.load(open('output/al_askari_plan_v2.json')); \
  print(Counter(s['visual'] for s in d))"

# Total plan duration vs. audio
python -c "import json; d=json.load(open('output/al_askari_plan_v2.json')); \
  print('plan end:', d[-1]['end'])" && \
  ffprobe -v quiet -show_entries format=duration \
    -of default=nw=1:nk=1 output/al_askari_audio.mp3

# Find which shots ended up on Pexels (= which queries failed every other source)
grep "using fetched image from pexels" output/render.log | wc -l

# Inspect the planner's raw response (saved on every plan build)
less output/planner_raw_response.txt

# List cached images after a real render
ls -la ~/.cache/lamahat/images/

# Quick font sanity check on a fresh checkout
python -c "from phase3.typography import FONT_PATHS; print(FONT_PATHS)"
```

---

## 12. Known Environment Constraints

| Constraint | Detail |
|-----------|--------|
| Streamlit Cloud RAM | 1 GB — keep FFmpeg work in subprocesses. v2 render obeys this. |
| Python | **3.12.13** (set in Cloud Advanced settings). Don't assume 3.13/3.14. |
| Colab CPU runtime | ~21 min for a 391 s render at 1920×1080. Mostly FFmpeg + vision RTTs. |
| FFmpeg subtitle path escaping | `:` and `\` need escaping in `-vf "ass=…"`. See `_mux_final`. |
| Claude vision max image size | Always resize to ≤ 800 px wide. Larger → 400 error. |
| Arabic font in ASS | `Fontname: Amiri`. Once §7.1 lands, the repo bundle is authoritative. |
| Pexels key | Optional in the contract, mandatory in practice given §7.3. |
| Anthropic key | Required for planner AND scorer. Treat as critical-path. |
| GitHub upload size | Test artefacts > 25 MB get truncated/partial in `_Phase3/output/`. The `final_cut_3a.mov` is a 181 s preview of a 391 s render. Real output: `output_files.zip` from cell 13. |
| Drive/Dropbox/GitHub shares | Claude's web_fetch can't authenticate to Drive or follow `claude.ai/share/...` links. For artifacts going forward, either commit to the repo (works for raw.githubusercontent.com) or upload directly in the chat. |

---

## 13. The Artifacts Folder — Reading the Archaeology

`_Phase3/artifacts/` is a chronological record of every "drop" from the
prior session. If you need to reconstruct *why* a piece of code looks the
way it does, the zip with the closest date and the matching filename is
where to look. The drops, in order:

1. **`phase3 v2 foundation.zip`** — first drop. Introduces the plan/render
   split with `align.py`, `plan.py`, and a revised `phase3_run.py` that
   accepts `--plan-only`. Sets the architectural direction for everything
   that follows.
2. **`phase3 v2 patch1.zip`** — fixes a Sonnet JSON-truncation crash by
   adding streaming, raising `max_tokens` to 24,000, and adding a
   resilient JSON parser that salvages complete shots from truncated
   responses. Also lowers target shot count for long scripts.
3. **`phase3 v2 patch2.zip`** — raises auto-split cap from 6 s to 8 s,
   recalibrates target to ~5 s avg, adds `audit_plan.py`, tightens the
   prompt's audio-bound and verbatim-typography rules.
4. **`phase3 v2 patch3.zip`** — caps become visual-type-aware
   (typography/portrait 10 s, archive/broll/location/object 8 s,
   section_mark 7 s, title_card 7 s). Adds field-exclusivity post-pass
   and floating-point tolerance. Reduces auto-split rate from 16 % → 3 %
   on the same Sonnet output.
5. **`phase3 v3 typography.zip`** — the Family A typography module (864
   LOC). Five card templates (`title_card`, `section_mark`, `pull_quote`,
   `name_reveal`, `date_stamp`) with Pillow + libraqm. Includes the
   "abandon arabic_reshaper, use raqm directly" finding.
6. **`phase3 v3 stage1.zip`** — `render.py`: Stage 1 renderer that
   produces a complete MP4 with audio, captions, and typography, using
   informative placeholder cards (search query + visual type + timing
   badge) where real images will go.
7. **`phase3 v3 fontpatch.zip`** — replaces hardcoded font paths in
   `typography.py` with the four-strategy auto-discovery (env var →
   fc-match → well-known paths → upstream download). This is the patch
   that §7.1 should extend with a "repo-bundled fonts" strategy 1.5.
8. **`phase3 v3 stage2.zip`** — the `sources/` subpackage. Multi-source
   image fetcher, disk cache, vision rubric, user-upload and
   book-extract paths, `--build-manifest` mode.
9. **`phase3 v3 resilience.zip`** — the most recent drop. Raises caps to
   the current 12/10/7 values, adds the merge-adjacent-duplicates pass,
   and wraps every download / vision call / per-shot render in
   `try/except` so one bad image produces a visible error card rather
   than killing the render. **Its `PATCH_NOTES.md` is the canonical
   changelog for the current state of the pipeline.**

If a future session needs to roll back any specific change (e.g., the
caps), the relevant zip is the source of truth for what the code looked
like before and after.
