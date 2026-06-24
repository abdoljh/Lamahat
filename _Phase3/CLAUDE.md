# Phase 3 — Session Handoff (post-issues-2-3-4-5)

> **What this file is.** A focused handoff for the next Claude session.
> It supplements `_Phase3/Phase3.md`, not replaces it.  Phase3.md is the
> deep architecture reference; this file says **what just changed**,
> **what's open**, and **how to proceed without re-litigating decisions
> already made**.
>
> **Working tree**: `Lamahat/_Phase3/` on GitHub
> (`github.com/abdoljh/Lamahat/tree/main/_Phase3`).
> **Latest plan**: `output/re_generated_plan.json`, **61 shots**, 391 s
> total, avg 6.41 s/shot (this is the current post-issue-3 run; the
> previous 48-shot, avg 8.15 s plan is superseded).
> **Latest render**: ~31 MB MP4, captions OFF, default Family A.
> Family B and Family C renders also validated in smoke tests.
> **Canonical notebook**: `_phase3_main.ipynb` 
>
> **What this session shipped**: issues 2 and 3 closed, plus a
> meaningful refactor of `typography.py` into three sibling modules.
> Issue 1 (color philosophy) is the only Tier-1 work item remaining.

-----

## 1. Read order for the next session

1. **This file first** (10 minutes).  It captures everything material
   that happened across the multi-session sweep on issues 2, 3, 4, 5.
1. **Phase3.md §1–§3 and §12** — for the unchanged architecture
   philosophy and the "things not to touch" list.  Skip the prose in
   §15.2, §15.3, §15.4, §15.5; their *status* lines below are now
   authoritative.
1. **Only on demand**: deeper Phase3.md sections (the planner-validation
   history in §6, the open-issues catalogue in §7).  Don't read these
   end-to-end up front — they're reference, not orientation.

If something here conflicts with Phase3.md, this file wins for issues
2, 3, 4, 5; Phase3.md wins for everything else.

-----

## 2. Architecture in one screen

Nothing structural changed.  The pipeline is still:

```
Script + Audio ──► align.py ──► word_timings
                                      │
                                      ▼
                                  plan.py ──► shot_plan.json   (one Sonnet call)
                                      │
                                      ▼
                            prebuild_assets.py ──► review/decisions.json
                                                   review/shot_NN_<visual>/…
                                                   review/overrides/…
                                      │
                                      ▼
                              render_plan.py ──► MP4
```

Two CLIs, one notebook, one plan-then-render split.  Read Phase3.md §1
for the rationale if needed.

What’s new in resolution order at render time (see issue 5 below):

```
fetch_for_shot(shot_index) tries, in order:
  1. decisions.shots[N].override
  2. user-marked file in review/shot_NN_<visual>/  matching  (my|user)[_-].+\.(jpg|jpeg|png|webp)
  3. decisions.pinned_portrait  (only for portrait visuals)
  4. decisions.shots[N].chosen_file
  5. live waterfall: LoC → Wikimedia → IA → Pexels
```

Step 2 is the new one — it lets the user drop a better image into a
shot folder without editing JSON.  Alphabetical tiebreaker if multiple.

-----

## 3. What just shipped (issues 2, 3, 4, 5)

### Issue 3 — section transitions · **closed**

Two complementary moves landed together (§15.3):

|Concern|Where it landed|
|---|---|
|Planner avg shot duration: 5.0 s → 4.5 s|`plan.py:build_shot_plan()` signature default|
|Planner system-prompt "TARGET RANGE: 4.0–5.0 s avg" + cut-faster-around-section-marks nudge|`plan.py:_SYSTEM_PROMPT` rule 3|
|Section_mark per-visual cap: 7.0 s → 5.0 s|`plan.py:_SYSTEM_PROMPT` rule 3|
|User-prompt asymmetric framing flipped (target is a floor, not a ceiling)|`plan.py:_USER_PROMPT_TMPL`|
|Auto-split `TARGET_PIECE`: 5.0 → 4.5|`plan.py:_validate_plan`|
|0.3 s zoom-in "new chapter" accent on section_mark shots (1.00 → 1.05 over 8 frames, then static hold)|`render.py:_section_accent` + `_MOTION_FILTERS` registry|
|`RenderConfig.section_mark_accent: bool = True` (opt-out flag)|`render.py:RenderConfig`|
|Section_mark accent applied at render-dispatch time (no plan change required)|`render.py` line ~790 dispatch in `render_video()`|

**Result after re-plan + re-render**: shot count rose 48 → 61, avg
shot duration dropped 8.15 s → 6.41 s, range tightened to 3.91–9.23 s
(was 3.91–12.19 s).  Auto-split rate stable at 2%.  Section_mark shots
now begin with a brief zoom accent.

The plan landed *higher* than the 4.0–5.0 s target (6.41 s vs ~5 s),
but user accepted this as good enough and chose to move on rather
than push the planner harder.  **Don't re-tune unless asked.**

Hard caps in `_validate_plan` (`HARD_CAPS`) were **not** touched —
CLAUDE.md §6 rule.  The patch worked by tightening prompt guidance,
not by tightening safety nets.

### Issue 2 — typography families B and C · **closed**

The heaviest of this sweep.  Refactored `typography.py` from a single
1228-line monolith into a dispatcher + three sibling modules, and
shipped two new families (B and C) alongside the existing A.
Selectable via `--typography-family {A,B,C}`.

#### Architecture after refactor

```
phase3/typography_common.py    shared tokens, font discovery, helpers,
                                TypographySpec (now with .family field)
phase3/typography_a.py         Family A renderers (verbatim lift from old typography.py)
phase3/typography_b.py         Family B renderers (NEW)
phase3/typography_c.py         Family C renderers (NEW)
phase3/typography.py           dispatcher; re-exports public API
```

The public surface (`render`, `TypographySpec`, palette constants,
`FONT_PATHS`, `_font`, `_measure`, `_draw_text_rtl`, `_apply_grain`)
is preserved verbatim — `render.py`'s import line is unchanged.

Each family has a `RENDERERS` dict keyed by template name; the
dispatcher reads `spec.family` and picks the registry.  Adding
Family D (or whatever) is now a single new module + one dict entry
in `typography.py:_FAMILY_REGISTRIES`.

#### Family B — Netflix-doc cinematic

- Vertical dark gradient `#201E1C` (top) → `#100F0E` (bottom), gamma 1.15
- Off-white `#ECE6DC` headlines in Amiri Bold
- Dim off-white `#AAA296` for subtitles/attribution
- Deep gold `#BC9440` for short, slightly thicker accent rules
- No diamond ornament; no decorative «»
- `date_stamp` uses gold for the date itself (only template where gold is primary)

#### Family C — manuscript, sepia + ornament

Three iterations to get right:

- **v1**: aged-paper vignette, sepia ink, burgundy brackets, double-rules,
  visible «» on pull_quotes.  Initial bracket geometry was oversized
  (1.05–1.1× text height) and arms collided with rules above/below.
- **v2** (the geometry-and-font pass):
  - Brackets shrunk to 0.80–0.85× *ink* height, terminus dots removed
  - Pull_quote «» dropped from 1.6× to 1.0× and re-anchored to text-block
    edges (not page margins)
  - **Headlines now use AmiriQuranColored** when available (falls back to
    Amiri Quran B&W, then Bold).  Applies to title_card main, section_mark
    main, name_reveal main.  Body text (pull_quote lines, subtitles,
    attribution, date digits) stays in Amiri Bold for readability.
  - Headline vertical padding via `HEADLINE_VPAD_FRAC = 0.35` to clear
    Quran's larger diacritic clearance (otherwise subtitles overlap).
- **v3** (the color-glyph fix):
  - `_draw_text_rtl` now accepts `embedded_color: bool = False`
  - When True *and* the font has a COLR/CPAL palette (detected via
    `_font_has_color_palette()`), Pillow renders the colored-glyph
    layers — the red i-dots (نقاط الإعجام) above/below Arabic letters.
  - Without this flag Pillow uses the monochrome outline, which made
    AmiriQuranColored look identical to plain Quran B&W.
  - The four Family-C headline draw calls pass `embedded_color=True`;
    Families A and B don't pass it (no change).

#### CLI wiring

`render_plan.py` now has:

```bash
--typography-family {A,B,C}      # default A
```

Threaded through `RenderConfig.typography_family` and into every
`TypographySpec` constructed in `_build_shot_asset()`.

#### Font discovery additions

`typography_common.py:_FONT_ALIASES` now recognises:

- `"quran"` ← `AmiriQuran.ttf` *or* `Amiri Quran.ttf` (with space — that's
  the filename in the user's fonts.zip)
- `"quran_colored"` ← `AmiriQuranColored.ttf`

Both slots are optional (only regular + bold required).  When Quran
fonts aren't present, Family C falls back to Bold — still works, just
no calligraphic feel.

### Issue 5 — file-convention dossier override · **closed**

Adds the step-2 resolution path (see §2 above).  Implementation in
`phase3/sources/decisions.py`:

- New `find_user_marked_file()` helper, regex
  `(my|user)[_-].+\.(jpg|jpeg|png|webp)` (case-insensitive).
- `Decisions.resolve()` checks user-marked files after `override`,
  before `pinned_portrait`.
- Resolution log lines lifted from DEBUG to INFO so they show in
  normal render output.

Test: `verify_user_marked.py` (in the repo).

### Issue 4 — title card + captions · **closed**

The biggest single change in this sweep.  Four code files, one
helper script, plus diagnostics.  Versions ran v1 → v2 → v3 → v3.1
→ v3.2 → v3.3 → v3.4 as different concerns surfaced.  Don’t bring
back any earlier version; v3.4 is the only state that ships.

|Concern                                                        |Where it landed                                                                                                              |
|---------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
|Title-card cover photo + aged-gold title                       |`typography.py:_render_title_card_with_cover`                                                                                |
|Title sizing: `title_main` 0.085→0.110, `title_sub` 0.030→0.040|`typography.py:SIZES`                                                                                                        |
|Removed cream hairlines on cover-mode title cards              |`typography.py:_render_title_card_with_cover`                                                                                |
|Caption inter-event gap 0.10 s → 0.30 s                        |`render.py:_write_captions` (`GAP = 0.15` each side)                                                                         |
|Caption minimum visible 0.6 s floor                            |`render.py:_write_captions` (`MIN_VISIBLE = 0.6`)                                                                            |
|Caption font 4.2% → 5.0% of height                             |`render.py:_write_captions` (`max(28, int(height * 0.050))`)                                                                 |
|Caption wrap to two lines (~8 words/line)                      |`render.py:_wrap_caption` + `WrapStyle: 2`, 18 % margins                                                                     |
|Escape order: `_escape_ass` BEFORE `_wrap_caption`             |`render.py:_write_captions` — fixes a v3.0/3.1 bug that printed `\,` artifacts on screen                                     |
|Cover-fit modes: `fill` / `contain` / `blur_pad`               |`typography.py:_make_cover_contain`, `_make_cover_blur_pad`; `RenderConfig.book_cover_fit`                                   |
|Cover horizontal alignment: `center` / `left` / `right`        |`typography.py:_h_offset_for_align`; `RenderConfig.book_cover_align`; title overlay shifts to the opposite side automatically|
|`--no-captions` documented for audio-only renders              |`render_plan.py` (flag already existed; help text is new)                                                                    |

CLI surface, end to end:

```bash
python prebuild_assets.py ... \
    --book-cover my_book_trimmed.jpg \
    --book-cover-fit contain \
    --book-cover-align left

python render_plan.py ... \
    --review-dir output/review/ \
    --no-captions \                # optional
    --book-cover-fit contain \     # optional CLI override of dossier
    --book-cover-align left        # optional CLI override of dossier
```

Precedence everywhere: **CLI flag > dossier (`book.cover_fit`,
`book.cover_align`) > default**.  Defaults: fit=`fill`, align=`center`.

#### Cover-image input requirement

If the source is a *photograph of a book on a desk* (book + paper
background), the renderer faithfully preserves the paper.  That’s
correct behaviour — fitting is supposed to preserve, not crop.  The
fix is upstream: trim the photo to just the book before passing to
`--book-cover`.  Helper script:

```bash
python trim_book_cover.py my_book.jpg
# Writes my_book_trimmed.jpg.  Pure Pillow + numpy, no phase3 imports.
# Auto-detects book bbox via luminance threshold (default 150),
# adds 3% margin, saves at quality 95.
```

The current chosen composition for the al-Askari run is
`trim → contain → align=left` (book on left, gold title in the right
cream area).

#### Issue 4 patch B — *not* shipped

Caption charcoal-bar backplate via FFmpeg `drawbox` filter in
`_mux_final`.  User confirmed white-on-charcoal-outline is acceptable
for now; backplate stays on the deck.  When picking this up, see
Phase3.md §7.8 — the technique is well-understood (libass’s
BorderStyle 3 alpha doesn’t blend, so `drawbox` runs separately).

### Diagnostics shipped

|Script                 |What it checks                                                                                                                                                                                             |
|-----------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|`diagnose_issue4.py`   |All four patched files present?  Caption GAP/MIN_VISIBLE/WrapStyle/`_wrap_caption`/escape order/5.0% font.  Inspects dossier for `cover_path` / `cover_fit`.  Predicts cream-mode vs cover-mode title card.|
|`diagnose_captions.py` |Calls `_write_captions` against the saved plan; prints ASS events with gap, line-count, and `\\N` detection; verdict at the end.                                                                           |
|`verify_title_card.py` |Renders both cover-mode and cream-mode title cards in isolation.                                                                                                                                           |
|`verify_user_marked.py`|Standalone tests for the issue-5 resolution order.                                                                                                                                                         |

Run all four in sequence after applying any future patch.  If any
fails, fix before rendering — they’re ~5 s of execution, render is
~15 min.

-----

## 4. Repo state and the Colab/repo dance

**The notebook is `_phase3_main.ipynb`.**  Cell 0 clones the GitHub
repo into `/content/`, so any patched file must either be in the
repo (then cell 0 picks it up) or copied into `/content/` *after*
cell 0 runs.  This bit users repeatedly during the v3 iterations;
when in doubt, push to the fork.

Files in the repo as of this handoff (all listed in cell 0's copy log):

- `phase3/typography_common.py` — **NEW** (issue 2 refactor): shared
  tokens, font discovery, helpers, TypographySpec
- `phase3/typography_a.py` — **NEW**: Family A renderers
- `phase3/typography_b.py` — **NEW**: Family B renderers
- `phase3/typography_c.py` — **NEW**: Family C renderers (incl.
  AmiriQuranColored support)
- `phase3/typography.py` — **REWRITTEN as dispatcher** (was monolith
  pre-issue-2)
- `phase3/render.py`, `phase3/sources/decisions.py` — patched
- `phase3/plan.py` — patched (issue 3 prompt tightening + auto-split
  target)
- `prebuild_assets.py`, `render_plan.py` — patched (render_plan.py
  now accepts `--typography-family {A,B,C}`)
- `trim_book_cover.py` — helper at repo root
- `verify_user_marked.py`, `verify_title_card.py`, `diagnose_issue4.py`, `diagnose_captions.py` — at repo root
- `resources/script/main_script.txt`, `resources/audio/narration.mp3` — unchanged inputs
- `output/re_generated_plan.json` — current **61-shot** plan
- `my_book.jpg`, `my_book_trimmed.jpg`, `my_jafar.jpg` — user-supplied artefacts at the repo root
- `output/review/` — dossier directory (cell 0 skips it; user uploads or regenerates with prebuild)
- `fonts/` — Amiri TTFs.  **Must include `AmiriQuranColored.ttf` and
  `Amiri Quran.ttf` (with space) for Family C to render the red
  i-dots.**  Falls back to Amiri Bold otherwise — no error, just no
  calligraphy.

### Network constraints in the sandbox

`raw.githubusercontent.com` and `api.github.com` are **blocked**.
Fetching source files from GitHub requires the HTML-scrape workaround:

```python
html = web_fetch("https://github.com/abdoljh/Lamahat/blob/main/_Phase3/phase3/render.py")
import re, json
m = re.search(r'"rawLines":(\[.*?\]),"stylingDirectives"', html, re.S)
lines = json.loads(m.group(1))
source = "\n".join(lines)
```

Binary files (zip, jpg, mp3) **cannot** be reconstructed from this —
the user must upload them via the chat attachment.

-----

## 5. Open issues, in priority order

This list supersedes Phase3.md §11 (which is now stale on items 2, 3,
4, 5; only item 1 remains as listed there).

### Tier 1 — the next thing to do

#### Issue 1 (Phase3.md §15.1) — color philosophy

**Goal**: a `--grade {warm,cool,neutral,bw}` knob on `render_plan.py`
with a cinematic-warm default.  Per-section variation is a stretch
goal.

**State**: not yet implemented.  No code exists.  The renderer currently
applies no global grading; the look is whatever the source imagery +
typography backgrounds provide.

**Approach**: probably a single FFmpeg `curves` / `eq` / `colorbalance`
chain applied in the final mux (`_mux_final` in `render.py`).  Each
preset is one filter string.  Plumb via `RenderConfig.grade` and
`--grade` flag.  The planner already emits `section_id` per shot, so
section-level variation can layer on later via a `grade_map.json`
without re-planning.

This is the only remaining Tier-1 item.  Issues 2 and 3 closed in the
last session.

### Tier 1 — issue 4 patch B (separate, smaller scope)

Caption charcoal-bar backplate.  See Phase3.md §7.8.  Touches
`_mux_final` in `render.py` to add a semi-transparent FFmpeg
`drawbox` filter alongside the ASS subtitles.  User has accepted
white-on-outline as the working baseline; this is a polish pass.

### Tier 1 — closing title_card clipping bug (found mid-session)

In the issue-3 render, the closing title_card showed a long credit
line `"تحقيق وتقديم: نجدة فتحي صفوت..."` that overflowed the cream
bar and clipped off the left edge.  Independent of typography family
(same root cause across A/B/C — the layout assumes a single-line
title fitting the available band).

**Two viable fixes** (do both):

1. **Planner-side**: add a `_SYSTEM_PROMPT` rule that
   `title_card.typography_text` must be a single line, ≤40 Arabic
   characters.  Attribution belongs in a separate `typography` shot,
   not crammed into the title_card.
2. **Renderer-side**: in `_render_title_card` (all three families),
   measure the text width against the available band and auto-shrink
   the font size until it fits.  Safety net for when the planner
   violates rule 1.

### Tier 2 — Phase3.md §15.1 already in Tier 1 above

(Was Tier 2 in the previous handoff; now Tier 1 since it's the only
remaining design-issue item.)

### Tier 3 — Phase3.md §7 backlog

The structural items remain open:

- **§7.3 — Source query strategy**.  LoC/Wikimedia/IA return 0
  candidates for every query in current runs.  Most-likely fixes:
  `query_simplify()`, lower Wikimedia `_MIN_DIMENSION`, add Wikipedia
  pageimages as a fifth source.  This is the single biggest visual
  quality lever still on the table.
- **§7.4 — Vision fail-open policy**.  When Haiku is down, all
  candidates score the neutral 5 and source-priority breaks ties,
  which means Pexels wins everything by elimination.  Fix is "demote
  unscored candidates only when scored ones exist."  Also add a
  circuit breaker after N consecutive vision errors.
- **§7.2 — Section parser** matches 2 of 5 logical sections.  Lossy
  but accepted; the planner partially compensates via `section_mark`
  shots.
- **§8 — Strategic path (C)**.  *Skip the web-source rabbit hole;
  use one Sonnet call to assign Phase 1a book photos to shots.*  This
  was the most impactful suggestion from a prior session and remains
  unanswered.  Worth raising again before §7.3 — it may obviate §7.3
  entirely.

### Tier 3 — minor

- §7.5 Whisper/X alignment (currently interpolated, ±300 ms drift)
- §7.7 Pillow typography placeholder cards
- §7.6 Shot duration tightening — **partially addressed by issue 3**;
  the 4.0–5.0 s target is in the prompt now, though Sonnet lands
  ~6.4 s.  Further tightening was explicitly deferred by the user.

-----

## 6. Things still not to touch

In addition to Phase3.md §12 (all still apply), prior sessions added:

- **Caption escape order: `_escape_ass` → `_wrap_caption`**, not the
  reverse.  The reverse double-escapes `\N` to `\\N` and prints a
  literal backslash on screen.  Caught in v3.0/3.1, fixed in v3.2.
- **Title overlay shifts to the opposite side when cover is
  offset**.  Don't centre the title when `cover_align != "center"` —
  it overlaps the book.  Logic is in `_render_title_card_with_cover`,
  driven by `has_spare_h`.
- **Don't add auto-trim to the renderer.**  The user explicitly
  chose Option A — manual trim once per source photo, then point
  `--book-cover` at the trimmed file.  Adding auto-trim to the
  pipeline was explicitly declined.  `trim_book_cover.py` is a
  separate one-shot helper, intentionally not imported by `phase3/`.
- **`--book-cover-fit` defaults to `fill`**, not `contain`, even
  though `contain` is what the al-Askari run uses.
- **Diagnostics are the cheap test.**  ~5 s of execution catches
  the most common failures.  Always run before a fresh 15-min render.

**New from the issue-2 and issue-3 session:**

- **Don't lower the per-visual hard caps in `plan.py:_validate_plan`.**
  Issue 3 was solved by tightening the planner *prompt*, not the safety
  net.  The hard caps (10 s typography/portrait, 8 s archive/broll/
  location/object, 7 s section_mark/title_card) were empirically tuned
  across three earlier iterations.  Don't change them.
- **Don't push the planner harder than 4.5 s target.**  User accepted
  6.41 s avg as the final state for issue 3 and chose to move on.  If
  the next session is tempted to "finish the job" on shot duration —
  don't, unless explicitly asked.
- **Don't remove `embedded_color=True` from Family C headline draws.**
  Without it, AmiriQuranColored renders identically to the plain Quran
  B&W variant.  The flag is a no-op on non-color fonts (Families A and
  B) so leave the four call sites alone.
- **Don't rename `HEADLINE_VPAD_FRAC = 0.35` in `typography_c.py`.**
  Quran/Quran-Colored have generous vertical clearance for diacritics
  that Pillow's textbbox doesn't capture.  This padding fraction was
  tuned empirically — below 0.30 the subtitle overlaps the headline.
- **Don't move the public typography exports out of `typography.py`.**
  The dispatcher re-exports `render`, `TypographySpec`, palette
  constants, `FONT_PATHS`, `_font`, `_measure`, `_draw_text_rtl`,
  `_apply_grain` — `render.py`'s import line depends on this exact
  surface.  Adding family-D etc. should not require a `render.py` patch.
- **Family A's renderer behaviour is verbatim from the pre-refactor
  monolith.**  `typography_a.py` is a verbatim lift; only the import
  paths changed.  Don't "improve" Family A under the refactor banner.

-----

## 7. Verification recipe

A few minutes of diagnostics save a 15-min render.  After applying
any change to `render.py`, `typography.py`, or the CLIs:

```bash
cd _Phase3
python verify_title_card.py --book-cover my_book.jpg
python diagnose_issue4.py --review-dir output/review/
python diagnose_captions.py --plan output/re_generated_plan.json
```

Expected baselines (post-v3.4, before any new patches):

|Check                                  |Expected                                                                                                         |
|---------------------------------------|-----------------------------------------------------------------------------------------------------------------|
|`verify_title_card.py`                 |`✓ All checks passed`                                                                                            |
|`diagnose_issue4.py` typography section|`GOLD_AGED present`, `cover_image present`, `title_main = 0.110`, `title_sub = 0.040`, `cream and cover variants`|
|`diagnose_issue4.py` render.py section |`GAP = 0.15`, `MIN_VISIBLE = 0.6`, `WrapStyle: 2`, `_wrap_caption() helper present`, `font size at 5.0%`         |
|`diagnose_issue4.py` dossier section   |`cover_path` set, `cover_fit: contain` (for current al-Askari run), `cover_align: left` (current run)            |
|`diagnose_captions.py` verdict         |`✓ Gaps ~0.30s AND captions wrapped cleanly — v3.2 PATCH IS LIVE.`                                               |

If any of these regress, the file wasn’t applied or the .pyc cache
is stale (restart the kernel in Colab).

-----

## 8. Quick reference for working with this user

A few patterns that emerged across the v3 iterations:

- **Bundle code drops as a single zip.**  The user’s persistent
  preference; honoured throughout this sweep.  `/mnt/user-data/outputs/`
  is the staging dir.
- **Land the patch + bump a version + write a README in the zip.**
  Past zips: `issue5_patch.zip`, `issue4_patch_A.zip` (v1 / v2 /
  v3 / v3.1 / v3.2 / v3.3 / v3.4).  Each README documents what
  changed *from the previous version*.
- **The user runs in Colab; the patched files live at `/content/`.**
  Cell 0 of `_phase3_main.ipynb` clones the GitHub repo into
  `/content/`.  If patched files aren’t in the repo yet, apply them
  *after* cell 0.  When the user reports “no change”, the first
  hypothesis is “cell 0 overwrote your patched files.”  The second
  is “stale .pyc.”  The third is “you didn’t re-run prebuild after
  changing a dossier field.”  Walk through them in that order.
- **Test in the sandbox before shipping.**  Render frames with the
  patched `typography.py` against the user’s actual `IMG_4654.JPG`
  before declaring the patch ready.  FFmpeg + libass are available
  in the sandbox; use them to confirm caption escape behaviour.
- **The user reads Arabic.**  Show before/after frames when the
  change is visual.  Don’t ship blind.
- **Phase3.md is canonical, but lags.**  This file (and any future
  CLAUDE.md drops) catches it up between official Phase3.md updates.

-----

## 9. Final state checksum

If the next session sees a `decisions.json` with this shape, the
current al-Askari run is intact:

```json
{
  "book": {
    "title": "مذكرات جعفر العسكري",
    "character": "Jafar al-Askari",
    "cover_path": "overrides/book_cover.jpg",
    "cover_fit": "contain",
    "cover_align": "left"
  },
  "pinned_portrait": "overrides/character.jpg",
  "shots": { /* 24 image shots */ }
}
```

`phase3/typography.py` after the issue-2 refactor should expose:

```python
import phase3.typography
print(list(phase3.typography._FAMILY_REGISTRIES))   # ['A', 'B', 'C']
```

And the import line in `render.py` should still match the original
verbatim:

```python
from .typography import (
    CHARCOAL, CREAM_DEEP, CREAM_LIGHT, CREAM_MEDIUM, FONT_PATHS, GRAPHITE,
    TypographySpec, WARM_GREY,
    _apply_grain, _draw_text_rtl, _font, _measure,
    render as render_typography,
)
```

Latest plan: 61 shots, 391 s, avg 6.41 s.  Latest render: ~31 MB MP4,
Family A default, captions OFF.  Family B and Family C smoke renders
validated (5 templates each × 1920×1080).  Family C uses
AmiriQuranColored for headlines and renders the red i-dots
correctly.  That is the current "good" state to preserve while
moving on to issue 1 (color philosophy).