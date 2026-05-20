# Phase 3 — Session Handoff (post-issues-4-and-5)

> **What this file is.** A focused handoff for the next Claude session.
> It supplements `_Phase3/Phase3.md`, not replaces it.  Phase3.md is the
> deep architecture reference; this file says **what just changed**,
> **what’s open**, and **how to proceed without re-litigating decisions
> already made**.
> 
> **Working tree**: `Lamahat/_Phase3/` on GitHub
> (`github.com/abdoljh/Lamahat/tree/main/_Phase3`).
> **Latest plan**: `output/al_askari_plan_v2.json`, **48 shots**, 391 s
> total (this is the current run — Phase3.md still refers to an older
> 43-shot run in its prose).
> **Latest render**: 26.1 MB MP4, ~924 s wall on Colab CPU, captions OFF.
> **Canonical notebook**: `_phase3_b3c.ipynb` (note: **b3c**, not b2c —
> Phase3.md still references the old `_phase3_b2c.ipynb`).

-----

## 1. Read order for the next session

1. **This file first** (10 minutes).  It captures everything material
   that happened across the multi-session sweep on issues 4 and 5.
1. **Phase3.md §1–§3 and §12** — for the unchanged architecture
   philosophy and the “things not to touch” list.  Skip the prose in
   §15.4 and §15.5; their *status* lines below are now authoritative.
1. **Only on demand**: deeper Phase3.md sections (the planner-validation
   history in §6, the open-issues catalogue in §7).  Don’t read these
   end-to-end up front — they’re reference, not orientation.

If something here conflicts with Phase3.md, this file wins for issues
4 and 5; Phase3.md wins for everything else.

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

## 3. What just shipped (issues 4 and 5)

### Issue 5 — file-convention dossier override · **closed**

Adds the step-2 resolution path above.  Implementation in
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

**The notebook is `_phase3_b3c.ipynb`.**  Cell 0 clones the GitHub
repo into `/content/`, so any patched file must either be in the
repo (then cell 0 picks it up) or copied into `/content/` *after*
cell 0 runs.  This bit users repeatedly during the v3 iterations;
when in doubt, push to the fork.

Files in the repo as of this handoff (all listed in cell 0’s copy log):

- `phase3/typography.py`, `phase3/render.py`, `phase3/sources/decisions.py` — patched
- `prebuild_assets.py`, `render_plan.py` — patched
- `trim_book_cover.py` — new helper at repo root
- `verify_user_marked.py`, `verify_title_card.py`, `diagnose_issue4.py`, `diagnose_captions.py` — new at repo root
- `samples/al_askari_script.txt`, `output/al_askari_audio.mp3` — unchanged inputs
- `output/al_askari_plan_v2.json` — current 48-shot plan
- `my_book.jpg`, `my_book_trimmed.jpg`, `my_jafar.jpg` — user-supplied artefacts at the repo root
- `output/review/` — dossier directory (cell 0 skips it; user uploads or regenerates with prebuild)
- `fonts/` — Amiri TTFs (the §7.1 fix relies on these being present)
- `artifacts/` — inert archive of older code drops; cell 0 skips it

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

This list supersedes Phase3.md §11 (which is now stale on items 1–5).

### Tier 1 — the next thing to do

#### Issue 3 (Phase3.md §15.3) — section transitions

**Goal**: faster rhythm at section boundaries, more audience hook.

Two complementary moves:

1. **Planner side** (`plan.py`): tighten average shot duration target
   from 5.0–6.5 s down to 4.0–5.0 s, especially around section
   boundaries.  The auto-split safety net catches anything Sonnet
   pushes too far (see Phase3.md §6 for the cap history — *don’t*
   lower the per-visual hard caps; only the planner-prompt target).
1. **Renderer side** (`render.py`): optionally introduce a single
   0.3 s motion accent on `section_mark` shots — a quick zoom-in or
   slide that signals “new chapter”.  Currently they’re `static_hold`
   like all typography.

Convergent with Phase3.md §7.6 (“shot duration distribution skews
long” — audit shows avg 9.09 s, target 5–6.5 s).  Doing this also
makes issue 4 patch B’s case stronger, since faster rhythm makes
white-on-outline captions read less stable.

The cleanest order is: planner first (re-plan, audit, render once),
then evaluate whether the motion accent is needed.

### Tier 1 — issue 4 patch B (separate, smaller scope)

Caption charcoal-bar backplate.  See Phase3.md §7.8.  Touches
`_mux_final` in `render.py` to add a semi-transparent FFmpeg
`drawbox` filter alongside the ASS subtitles.  User has accepted
white-on-outline as the working baseline; this is a polish pass.

### Tier 2 — Phase3.md §15.1 and §15.2

Color grading knob (`--grade {warm,cool,neutral,bw}`) and Typography
Families B/C.  Both are larger scopes, both currently `open`.
Phase3.md §15.1 and §15.2 describe the shape — neither has any code
yet.

### Tier 3 — Phase3.md §7 backlog

The structural items remain open:

- **§7.3 — Source query strategy**.  LoC/Wikimedia/IA return 0
  candidates for every query in current runs.  Most-likely fixes:
  `query_simplify()`, lower Wikimedia `_MIN_DIMENSION`, add Wikipedia
  pageimages as a fifth source.  This is the single biggest visual
  quality lever still on the table.
- **§7.4 — Vision fail-open policy**.  When Haiku is down, all
  candidates score the neutral 5 and source-priority breaks ties,
  which means Pexels wins everything by elimination.  Fix is “demote
  unscored candidates only when scored ones exist.”  Also add a
  circuit breaker after N consecutive vision errors.
- **§7.2 — Section parser** matches 2 of 5 logical sections.  Lossy
  but accepted; the planner partially compensates via `section_mark`
  shots.
- **§8 — Strategic path (C)**.  *Skip the web-source rabbit hole;
  use one Sonnet call to assign Phase 1a book photos to shots.*  This
  was the most impactful suggestion from the prior session and
  remains unanswered.  Worth raising again before §7.3 — it may
  obviate §7.3 entirely.

### Tier 3 — minor

- §7.5 Whisper/X alignment (currently interpolated, ±300 ms drift)
- §7.7 Pillow typography placeholder cards
- §7.6 Shot duration tightening (convergent with §15.3)

-----

## 6. Things still not to touch

In addition to Phase3.md §12 (all still apply), this session added:

- **Caption escape order: `_escape_ass` → `_wrap_caption`**, not the
  reverse.  The reverse double-escapes `\N` to `\\N` and prints a
  literal backslash on screen.  Caught in v3.0/3.1, fixed in v3.2.
- **Title overlay shifts to the opposite side when cover is
  offset**.  Don’t centre the title when `cover_align != "center"` —
  it overlaps the book.  Logic is in `_render_title_card_with_cover`,
  driven by `has_spare_h`.
- **Don’t add auto-trim to the renderer.**  The user explicitly
  chose Option A — manual trim once per source photo, then point
  `--book-cover` at the trimmed file.  Adding auto-trim to the
  pipeline was explicitly declined.  `trim_book_cover.py` is a
  separate one-shot helper, intentionally not imported by `phase3/`.
- **`--book-cover-fit` defaults to `fill`**, not `contain`, even
  though `contain` is what the al-Askari run uses.  The default
  serves the “user supplies a 16:9 hero image” case (zero code
  surprise); `contain` is what you opt into when supplying a
  portrait-shaped cover.
- **Diagnostics are the cheap test.**  ~5 s of execution catches
  the most common failures (wrong file applied, stale .pyc, missing
  dossier values).  Always run before a fresh 15-min render.

-----

## 7. Verification recipe

A few minutes of diagnostics save a 15-min render.  After applying
any change to `render.py`, `typography.py`, or the CLIs:

```bash
cd _Phase3
python verify_title_card.py --book-cover my_book.jpg
python diagnose_issue4.py --review-dir output/review/
python diagnose_captions.py --plan output/al_askari_plan_v2.json
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
  Cell 0 of `_phase3_b3c.ipynb` clones the GitHub repo into
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

Latest render: 924 s wall, 26.1 MB MP4, captions OFF, title card
shows the trimmed book cover flush-left with the gold title sitting
in the right-side cream area.  That is the current “good” state to
preserve while moving on to issue 3.