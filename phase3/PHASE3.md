# Phase 3 — Visual Generation · Session Handoff

> **Location**: this file lives at `phase3/PHASE3.md`. It is the deep reference;
> the master plan is `../CLAUDE.md`.
> **Working tree**: the v2 package is `Lamahat/phase3/`; CLIs, `fonts/`,
> `resources/` and `.streamlit/config.toml` are at the repo root.
> The old `_Phase3/` staging directory has been removed.

---

## 0. Current state (read this first — supersedes older sections below)

Phase 3 v2 is integrated into the app. The rest of this document is the
original design handoff; where it conflicts with this section, **this section
wins**. Nothing below has been deleted so the design rationale stays on record.

### Session handover (2026-07-05) — start a new session here

The screening-review cycle (`phase3/SCREENING_REVIEW.md`) drove five
shipped batches this session, all on branch
**`claude/phase3-review-plan-8sbeue`** (merge to `main` when its PR
lands; the Colab notebooks pin `BRANCH` in their bootstrap cell):

| Batch | One-liner |
|---|---|
| **P0** sourcing | Path (C) photo bank (`resources/photo_bank/` → one Sonnet call → dossier), Wikipedia lead-image source, era-fit vision axis, Pexels style-token stripping |
| **Colab** | Single-repo workflow: notebooks git-clone THIS repo at a pinned branch; `requirements-colab.txt`; cwd-first resources discovery |
| **P1/P2** pacing + text | dHash adjacent-duplicate swap, effective-holds audit (`audit_plan.py --review-dir`), lower-third overlay anchor, adaptive text scrim |
| **P3/P4** look + polish | Documentary tone on Pexels winners, section parser fixed (5 sections, sidecar override), honest provenance logs, title-card polish (+`--title-subtitle`), word-by-word reveal (opt-in `--word-reveal`) |
| **P5** screening fixes | `visual_type` passthrough bug fixed, era rubric hardened (wrong century EITHER direction = 0), `--backdrop-rotate` (P1.3), `--grade-map` per-section grading (P3.4), **ElevenLabs TTS** (Phase 2 complete) |
| **P6** era + cost + dossier (2026-07-12) | `Shot.era` contract (hard gate + Arabic gap card), staged waterfall (no Pexels for period shots), batched per-shot vision scoring, prebuild skips bank/pool-covered shots, top-3 dossier at prebuild, `$LAMAHAT_CACHE` persistence — see the P6 section below + `phase3/REVIEW_2026-07-12.md` |
| **P7** movie-quality (2026-07-26) | Screening critique batch ("still a slide show"): full-coverage dossier (a folder for EVERY shot incl. typography card previews), sentence-integrity editing (punctuation-aware planner prompt, clause-snapped cuts, sentence-level caption events in the v2 plan JSON that survive cuts), era gate softened (era 0-only rejection, Pexels as era-gated last resort), living typography cards (`card_drift`), `motion_intensity` wired into zoompan, auto-split pieces alternate camera direction, split-undo merge bug fixed, `--typography-over-image` + `--word-reveal` now default ON — see the P7 section below |

**Verified on screen** (second screening, SCREENING_REVIEW.md §5): the
3-minute sample carries photo-bank imagery, working word reveal,
lower-third quotes, adaptive scrim, one warm look.

**Open / waiting on the next screening:**
- **P7 (2026-07-26) is on `claude/phase3-movie-quality-d1n58l`** — the
  movie-quality batch answering the third screening ("slide show"
  critique + three notes).  Regenerate the plan (the planner prompt
  changed), re-prebuild (full-coverage dossier), and re-render before
  judging.  See the P7 section below.
- User curation: bank photos for the education + CUP beats; the four
  era-miss shots (SCREENING_REVIEW.md §5.1); `PHOTO_BANK_MAX_USES=2`.
- First ElevenLabs narration render (set `ELEVENLABS_API_KEY` /
  `ELEVENLABS_VOICE_ID`), then re-run WhisperX alignment on Colab.
- Screening decisions: make `--word-reveal` default? Is
  `--backdrop-rotate 10` right? A `grade_map` three-act arc?
- Deferred with rationale: P2.3 saliency nudge; Phase 1b LLM-emitted
  section sidecar; Wikipedia source still ⚠ not live-verified (sandbox
  blocked the API both sessions — check the `Wikipedia: N lead-image
  candidates` log line on the next Colab prebuild).
- Legacy-code cleanup (§16 list) remains optional.

### Two routes (mirrors the two notebooks in `resources/`)

- **Total solution** → `phase3.build_total_solution()`: align (or a supplied
  `word_timings.json`) → Sonnet plan → **prebuild** every candidate into a
  review dossier → optional condition → render. Slims the dossier
  (`slim_review_dir`, default keep top-3) and can hand it back as a `.zip`.
- **Rendering only** → `phase3.render_from_review()`: re-render a saved/edited
  dossier at **no planner/fetch API cost**. `offline=True` by default (uses only
  the dossier's images; uncovered shots get a placeholder). Resolution order per
  shot: `override → my_/user_-marked → pinned/pool → chosen_file → (waterfall
  only if not offline)`.

### Streamlit Phase 3 tab

- A **Route** selector is the first control. Total solution takes the script +
  audio + context; Rendering only takes a **Dossier source**: *this session's
  Total-solution dossier (no upload)* · *upload a `.zip`* · *fetch a `.zip` from
  a URL* (server-side download — the fix for Cloud upload `ClientDisconnect`s).
  The Total-solution dossier is kept on the server for the session so it can be
  re-rendered with **no upload**.
- Sidebar exposes the full render-look set: grade, typography family, book cover
  (+fit/align), character pool/pin, music (+level −12 dB default/duck), captions
  (+backplate/size/pos), title size **+ optional custom colour**, text scrim,
  **typography-over-image (on by default)**, cinematic fades, **2.5D parallax
  (+backend/warp)**. Plus per-run: resolution, **sharpen assets** (conditioning,
  opt-in), **saved dossier candidates** (chosen/top-3/all), and an **alignment**
  expander (backend + `word_timings.json` upload). A render log is downloadable.

### New/changed public API in `phase3/__init__.py`

`generate_video_v2` (one-shot align→plan→render), `build_total_solution`,
`render_from_review`, `condition_review_dir`, `slim_review_dir`,
`zip_review_dir`, `extract_thumbnail`, `probe_audio_duration`, plus the
re-exports (`RenderConfig`, `render_video`, `build_shot_plan`, `align`,
`parse_sections`, `Fetcher`, `FetcherConfig`, `load_plan`, `save_plan`,
`summarise_plan`). `align.load_word_timings()` loads a precomputed alignment.

### Fixes landed this session (older "open issues" now resolved)

- **Portrait cropping** — `render._png_to_clip` is aspect-aware: portrait/odd
  sources are *contained whole* over a blurred fill (mirrors `_fit_to_frame`),
  so figures are never cut in the default (non-parallax) motion path.
- **Wrong face on a portrait** — `decisions.subject_is_character(query, name)`
  gates the character pool/pin so the lead only appears on portraits whose query
  is about them; other subjects use their own fetched image.
- **Character pool** — resolved from `resources/character/`, deterministically
  shuffled, rotated across the lead's portrait shots.
- **Sources** — Library of Congress and Internet Archive were **removed** from
  the waterfall (LoC returned 0 for every query; IA downloads 404'd). Only
  **Wikimedia + Pexels** remain (`sources/__init__.py`); re-add the classes if
  their query strategy is fixed (§7.3).
- **`FetcherConfig`** gained `pinned_portrait` and `offline`.
- **Streamlit robustness** — `st.secrets` access is wrapped (`_secret`) so a
  missing `secrets.toml` can't blank the sidebar; `.streamlit/config.toml` sets
  `maxUploadSize = 400`.

### P0 sourcing batch (2026-07-03) — period-true imagery

Landed after the first full 88-shot production render exposed the Pexels
problem (64% of image shots; tricorn-hat reenactors captioned as the
Ottoman army — see `phase3/SCREENING_REVIEW.md` §2.1).  Five changes:

- **Path (C) shipped** (`sources/photo_bank.py`): a curated photo-bank
  folder (auto-detected at `$LAMAHAT_RESOURCES/photo_bank/`, or
  `prebuild_assets.py --photo-bank DIR`) is Haiku-captioned once (cached
  in `captions.json`, hand-editable) and ONE Sonnet call assigns photos
  to image shots.  Assigned photos become the dossier's `chosen_file`
  (waterfall candidates stay as alternates; `--photo-bank-only` skips
  the waterfall for assigned shots).  The dossier veto workflow is
  unchanged — the bank photo is just the pre-selected winner.  Invalid
  assignments (unknown shot/file, reuse over `--photo-bank-max-uses`)
  are dropped, and the call is fail-open.  Wired through
  `build_total_solution(photo_bank=…)`; Streamlit gets it via the
  resources auto-detect with zero UI change.  This decides §8: the bank
  is the *user's curated collection* (the book's own plates are mostly
  murky halftones — better copies were prepared by hand), not raw
  Phase 1a extractions.
- **Wikipedia lead-image source** (`sources/wikipedia.py`): for named
  subjects the article's lead image is usually the canonical documentary
  photo.  `generator=search` + `prop=pageimages&piprop=original` with
  `pilicense=free` (server-side license filter).  Sits between Wikimedia
  and Pexels in the waterfall.  ⚠ Not yet live-verified (sandbox network
  policy blocked wikipedia.org) — confirm on the first Colab prebuild;
  failure mode is graceful (0 candidates, waterfall continues).
- **Era-fit vision score**: the Haiku rubric gained a 4th axis, `era`
  (0–3, "could the CONTENT plausibly be from the implied period — judge
  uniforms/vehicles/architecture, not the color grade").  Era acts as a
  **demotion tier** in `rank_candidates`, never a hard filter: any
  era-passing candidate outranks every era-failing one, but if all
  candidates are anachronistic the least-bad still renders (fail-open
  preserved; unscored/legacy candidates pass).
- **Pexels query hygiene**: style/period tokens (`sepia`, `historical`,
  `vintage`, decade words, bare years…) are stripped before the Pexels
  call only — Pexels matches them literally against modern *styled*
  stock.  Wikimedia/Wikipedia keep the full query.
- **Era flags in the dossier**: `context.txt` marks candidates
  `⚠ ERA MISMATCH`, `score_breakdown` gains `"era"`, and the prebuild
  summary lists the shots whose *winner* is era-flagged so the curator
  triages those first.

### P1/P2 batch (2026-07-04) — perceived pacing + overlay typography

Landed after the user verified both routes end-to-end with the P0 batch.
Details and verification numbers in `phase3/SCREENING_REVIEW.md` §4.

- **Adjacent near-duplicate avoidance** (`sources/dedupe.py`, P1.1):
  consecutive image shots resolving to perceptually identical pictures
  (dHash Hamming ≤ 8) swap the later shot to its next-ranked candidate.
  Only automatic picks are swapped — override / user-marked / pool /
  pinned / photo-bank choices are respected.  `Decisions.resolve_detailed`
  now reports the resolution kind + ranked alternates;
  `Decisions.resolve` is a back-compat wrapper.  Opt out with
  `render_plan.py --no-dedupe` / `FetcherConfig.dedupe_adjacent=False`.
- **Effective-holds audit** (`audit_plan.py`, P1.2): reports what the eye
  sees — typography-over-image spans merged into their backdrop shot,
  duplicates detected via the dossier (`--review-dir`) or query equality.
  The 88-shot production plan audits at 6.85 s effective vs 4.75 s
  planned with 10 spans > 10 s — the numeric confirmation of the
  screening finding.
- **Lower-third overlay anchor** (P2.1): pull quotes / name reveals /
  date stamps now sit at y≈0.63 instead of dead center (off the faces);
  section marks stay centered.  `--overlay-anchor {auto,center,lower}`,
  Streamlit "Text position", notebook `OVERLAY_ANCHOR`.
- **Adaptive scrim** (P2.2): `--text-scrim auto` is the new default —
  the backdrop band under the text is sampled and the plate escalates
  off → soft → band only on bright/busy frames (the cream-watercolor
  legibility failure), keeping the plate-free look on dark footage.
- §15.4 caption sizes (P2.4) were found already shipped (title_sub
  0.040, name_sub 0.028, 0.15 s caption gap) — **§15.4 can be marked
  closed.**
- Deferred consciously: P1.3 (overlay backdrop rotation past 10 s) and
  P2.3 (saliency nudge) — re-evaluate after the next screening pass.

### P6 batch (2026-07-12) — era contract + cost + top-3 dossier

Implements §1/§3/§4 of `phase3/REVIEW_2026-07-12.md` (the review driven
by the third screening: ~$1/run, dossier bloat, Pexels winning 46 % of
image shots in a period documentary).  Framing (§2 / P6.4) is the next
batch.

- **`Shot.era` (P6.1/E1)**: every image shot in the plan now carries the
  historical period it must depict (planner rule 12; e.g.
  `"1900s-1910s Ottoman Mesopotamia"`), or the literal `"timeless"` for
  generic b-roll where modern stock is acceptable.  Legacy plans load
  with `era=""` → old soft-demotion behaviour throughout.
- **Era hard gate (E2)**: for period shots,
  `passes_threshold(..., era_strict=True)` REJECTS era ≤ 1 candidates.
  When nothing era-true survives, the renderer draws an **Arabic
  typography gap card** (§7.7 shipped — pull-quote card from the shot's
  narration text) instead of the least-bad anachronism; the Latin "TBD"
  card remains the final fallback when no Arabic text exists.  Fail-open
  is preserved: unscored/vision-down candidates still pass (neutral
  score sets era=2).
- **No Pexels for the past (E3)**: `_fetch_live` is staged — archival
  sources (Wikimedia → Wikipedia) first; Pexels is a second stage that
  (a) never runs for period shots and (b) for timeless/legacy shots only
  runs when the archival stage produced < 2 keepers.
- **Explicit era in the rubric (E4)**: the vision prompt receives the
  plan's period verbatim instead of inferring it from the query.
- **Batched vision scoring (C1)**: `VisionScorer.score_batch()` scores
  all of a shot's candidates in one Haiku call (chunks of ≤ 8 numbered
  images, JSON-array response, per-image and per-chunk fail-open).  One
  Anthropic client per scorer instead of per call.
- **Prebuild skips covered shots (C3)**: photo-bank-assigned shots and
  character-pool portraits (`subject_is_character`) no longer run the
  web waterfall — `--fetch-covered` opts back in; the shot's
  `context.txt` records the skip.  `--photo-bank-only` is now a legacy
  alias.
- **Cross-source dedupe (C4)**: pooled candidates are de-duplicated by
  URL and dHash (≤ 6 bits) before scoring — the Wikipedia lead image no
  longer gets scored (and dossier'd) twice when Commons already
  returned it.
- **Top-3 dossier (P6.3/D1-D3)**: prebuild ranks candidates and copies
  only the top `--dossier-keep` (default 3) threshold-passing files per
  shot; every candidate stays in `candidates.json`/`context.txt` as
  metadata (marked `· not saved (see URL)`).  The main notebook slims
  legacy dossiers (`slim_review_dir(mode="top")`) before zipping, and
  `build_total_solution`'s slim default changed `"chosen"` → `"top"`
  (a chosen-only dossier silently disabled `--backdrop-rotate` and the
  dedupe swap on render-only re-runs).
- **Cache persistence (C5)**: prebuild's cache is always on, defaulting
  to `$LAMAHAT_CACHE` or `~/.cache/lamahat/images` (previously the CLI
  default was NO cache).  The notebook settings cell shows the
  Drive-mount override so Colab re-runs stop re-downloading and
  re-scoring.  A cached winner that fails a period shot's era gate is
  ignored and refetched.

Expected effect: fresh total-solution run ≈ $0.30–0.40 (from ≈ $1),
re-runs near-free with a Drive cache; dossier ~3–5× smaller with no
sub-threshold or duplicate files; zero modern-stock imagery on shots
the plan marks as historical.

**Verified on the first P6 run (2026-07-12, output/ph3)**: Pexels
winners 26 → **0** (bank 25 · wikipedia 17 · wikimedia 13 · pool 11);
era gate rejecting modern Baghdad photos with logged reasons; one
Arabic gap card rendered where nothing era-true survived.

### P6.4 batch (2026-07-12) — framing: see the whole image

Implements §2 of `phase3/REVIEW_2026-07-12.md`.  The old pipeline
stacked three crops on a 3:2 source — blind 16:9 centre-crop (−15.6 %
height) × fixed 1.18 parallax buffer (−15.3 % each axis) × dolly zoom —
so only ~60 % of the pixels ever reached the screen.

- **Motion-proportional buffer (R1)**: `motion_parallax._buffer_size()`
  replaces the fixed 1.18× buffer.  Margin = the shot's actual maximum
  lateral disparity (`amp_px × max(1, intensity)`) + 16 px pad, buffer
  locked to the output aspect.  Portraits (amp 36) now render on a
  ≈1.05× buffer → ~95 % of the buffer visible instead of 85 %.  Zoom
  needs no margin (s ≥ 1 samples inward).
- **Conditioning-time 16:9 smart crop (R2)**: cover-class assets are
  cropped to EXACT 16:9 in `condition_assets.py` — gradient-energy
  placement with a slight top bias (heads survive), recorded as
  `crop_box` in decisions.json and **visible in the dossier's
  .cond.jpg**.  The renderer's cover-fill then has nothing left to
  crop.  `--crop-cover {smart,center,off}` (default smart; off =
  legacy aspect-preserving behaviour).  Contain-class (portraits) still
  never cropped.
- **The full-screen standard (R3)**: `--target-cover 2560` now means a
  **2560×1440 (16:9)** canvas — not a 3:2 long edge.  2560×1706 was the
  wrong target because of its aspect: every 3:2 source donated 15.6 %
  of its height to a blind crop.
- Photo-bank files (`bank_*`) are now classed user-added in
  conditioning: SR upscale path, never downscaled.

Net for a conditioned 16:9 source on a portrait-amp parallax shot:
~95 % of the deliberately-framed image on screen, vs ~60 % of an
arbitrarily-cropped one before.

### P6.5 batch (2026-07-13) — dossier review ergonomics (user-requested)

Two `condition_assets.py` behaviours requested after reviewing the
first P6.4 dossier:

- **Conditioned images REPLACE their source** (P6.5a): written as
  `<stem>*.jpg` (star before the extension = "pixels were conditioned")
  and the raw download deleted — one copy per winner, dossier roughly
  halves for conditioned shots.  `chosen_file`, the winning candidate's
  `file` entry, and `chosen_file_original` (name provenance) stay
  consistent in decisions.json, so dedupe-swap / `--backdrop-rotate`
  alternate walks never chase a deleted path.  Untouched assets keep
  their unmarked name.  `--keep-originals` restores the legacy
  `.cond.jpg` add-a-copy behaviour; `--mark '+'` gives Windows-safe
  names (`*` is illegal in Windows filenames — review the dossier on
  Drive/Colab/Linux/macOS otherwise).
- **Attention-needing shot folders are starred** (P6.5b): after
  conditioning, a `shot_NN_visual/` folder with NO pickable image is
  renamed `shot_NN_visual*` so the curator reaches it at a glance;
  it renders as an Arabic typography card unless an image is dropped
  in.  Folders that are empty because a curated source covers the shot
  (photo-bank assignment, character pool — the new `covered` field in
  decisions.json, with a context.txt fallback for older dossiers) are
  never starred, and a starred folder is automatically unstarred once
  it gains an image.  `find_user_marked_file` tolerates the starred
  name (my_/user_ drops inside a starred folder still resolve), and
  prebuild reuses a starred folder under its plain name instead of
  creating a duplicate.  The dossier README documents both markers.

### P7 batch (2026-07-26) — movie-quality (third-screening critique)

The user's verdict on the third cut: "lacks the intended audience
attraction and gives the impression of not more than a slide show",
with three recorded notes.  Each traced to a code cause; all fixed on
branch `claude/phase3-movie-quality-d1n58l`:

- **Note 1 — "not all shots are listed" (P7.1)**: `prebuild_assets.py`
  skipped every typography-kind shot, so the dossier showed shot_02,
  shot_04 … with no shot_01/shot_03 — reading as if script parts were
  cut.  Now EVERY shot gets a `shot_NN_<visual>/` folder: typography
  shots carry `context.txt` (timing, template, exact Arabic card text)
  plus a rendered `card_preview.png` (`--typography-family` matches the
  render look; `--no-card-previews` to skip).  Their decisions.json
  entries carry `covered="typography card …"`, so conditioning never
  stars them and slimming/render are untouched.
- **Note 2 — "Arabic sentence cut across shots" (P7.2)**: three layers.
  (a) The tokenizer keeps Arabic marks (، ؛ ؟) inside tokens and drops
  ASCII marks; `align.script_punct_flags()` normalises both into one
  flag per word, and the planner prompt's WORDS lines now show the
  punctuation with an explicit CUT ON SENTENCES rule.  (b)
  `plan._snap_to_clause_boundaries()` nudges every off-sentence
  boundary onto the nearest sentence end (±0.9 s) or clause end
  (±0.5 s) when neither neighbour breaks the 1.6 s floor or its
  HARD_CAP.  (c) `plan.build_caption_events()` builds a SENTENCE-level
  subtitle track stored in the v2 plan JSON ({"version":2, "shots",
  "captions"}); the renderer's events mode keeps one continuous line
  across image-image cuts, clipping only where typography shots show
  their own text.  Legacy bare-list plans still load everywhere
  (`load_plan`, `audit_plan.py` unwrap).
- **Note 3 — "era restriction has many disadvantages" (P7.3)**: the P6
  hard gate (reject era ≤ 1 + no Pexels for period shots) starved weakly
  covered period shots into typography gap cards.  Softened: only
  era==0 (clearly modern/anachronistic) is rejected under the strict
  gate; era==1 "doubtful" passes but stays in the demotion tier (only
  renders when nothing era-true exists).  Pexels is now a LAST-RESORT
  stage for period shots (queried only at zero archival keepers, still
  era-gated).  The planner prompt now steers thematic/emotional beats
  toward "timeless" metaphor b-roll instead of doomed narrow-period
  archival queries.
- **Slide-show structural fixes (P7.4)**: typography cards get a
  near-subliminal `card_drift` push (1.00→1.03) instead of frozen
  frames (`--no-card-motion` restores static); the planner's
  `motion_intensity` is finally wired into every zoompan expression
  (clamped 0.4–1.6); auto-split pieces alternate camera direction
  (push→pull→push) so long shots read as edited sequences; and a real
  bug: the adjacent-identical merge pass was silently re-joining the
  pieces the runaway splitter had just produced — merging now respects
  the per-visual HARD_CAP.  Defaults flipped ON (CLI
  `--no-typography-over-image` / `--no-word-reveal` to opt out):
  typography-over-image and word-by-word reveal.
- **P7.6 hotfix** (2026-07-27): the photo-bank assignment validator did
  `int(idx_str)` on keys Sonnet returns as `"shot 5"` — every assignment
  in every run was silently dropped.  Fixed to extract the integer from
  any key shape; prebuild now prints a loud banner + writes
  `PHOTO_BANK_NOT_ASSIGNED.txt` if a populated bank ever again yields
  zero assignments.  Sentence-cut grid widened to ±2.6 s/±1.2 s
  (was ±0.9 s/±0.5 s, too timid).  Biography portrait floor added to
  the planner prompt (6–10 protagonist portraits; P7.3's "prefer
  timeless" guidance had collapsed portraits to near zero).
- **P7.7 hotfix** (2026-07-28): three bugs found by watching the actual
  rendered captions (not log analysis) on the first run with the P7.6
  fixes and captions on — all reproduced pixel-for-pixel with a direct
  `ffmpeg`+`libass` render and fixed:
  (a) a clipped caption span burned the event's FULL text instead of
  only the words spoken during the visible remainder, so a title
  card's own words leaked into the next shot's caption — fixed via
  `CaptionEvent.words` (per-word timing) and per-span text
  reconstruction in `_write_captions`;
  (b) `show_caption=False` on a real-image shot (its old per-shot-
  caption semantics) could silently delete an entire clause of the
  sentence track when it fell inside that shot — fixed by restricting
  events-mode hidden ranges to `TYPOGRAPHY_VISUALS` only;
  (c) a caption line ending in ، ؛ or ؟ renders with the mark visually
  escaping to the START of the RTL line (confirmed libass/fribidi
  neutral-character bidi gap, not digit-related, not exclusive to the
  whole-Dialogue-field end — first line of a `\N`-wrapped caption needs
  it too) — fixed with an invisible RLM (U+200F) appended after the
  mark, via `_wrap_caption`'s new `_anchor_trailing_punct`.

- **P7.8 hotfix** (2026-07-29): P7.7(a) was correct but invisible in
  practice, for two reasons found on the user's next render-only pass:
  (a) **a dossier holds more than one `shot_plan.json`.** The
  render-only notebook renders `output/shot_plan.json` while the
  dossier carries `output/review/shot_plan.json`; a plan repaired by
  hand hit the copy the renderer never reads, so the film came out
  byte-identical. `plan.repair_caption_events()` now runs at load time
  in **both** render entry points — when caption events lack `words`
  it attaches per-word timing from whichever `word_timings.json` sits
  beside the plan or in the dossier, but only where the words inside an
  event's own range reproduce its stored text EXACTLY (a mismatched
  narration is left alone, never silently re-captioned).
  `regenerate_captions.py` also repairs every copy it finds now. The
  copy question no longer changes the output.
  (b) **word-accurate clipping produces stubs.** A sentence whose body
  is spoken under a typography card leaves a one- or two-word tail
  ("أن", "كان") flashing on the following shot. The card already shows
  that sentence's text, so `_write_captions` drops a *clipped* span
  under `MIN_CLIPPED_WORDS = 3`; unclipped short events are untouched
  (61 → 54 caption lines on the reference plan).

### P5 batch (2026-07-05) — second-screening fixes

After the user's 3-minute sample confirmed the P0–P4 stack on screen
(SCREENING_REVIEW.md §5), the remaining misses were addressed:

- **`visual_type` bug fixed**: render and prebuild now pass the shot's
  visual into `fetch_for_shot`, so the stricter portrait subject floor
  (vision.MIN_KEEP_SUBJECT_PORTRAIT) actually applies.
- **Era rubric hardened**: wrong century in EITHER direction scores 0
  (a Baroque painting is as anachronistic for 1908 as a modern photo);
  wrong region's institutions cap at 1.
- **P1.3 shipped** — `--backdrop-rotate SEC` (default 10): a long run
  of over-image text cards rotates its backdrop to the source shot's
  next-ranked dossier alternate instead of freezing one frame.
- **P3.4 shipped** — `--grade-map file.json` per-section grading at the
  final mux via timeline-enabled filters (colortemperature/eq/hue;
  NOT curves — Colab's ffmpeg may predate its timeline support).
  Unmapped sections fall back to `--grade`.
- **ElevenLabs TTS shipped** (Phase 2): chunked synthesis with prosody
  bridging, ffmpeg part-joining, Streamlit backend enabled with
  `ELEVENLABS_API_KEY`/`ELEVENLABS_VOICE_ID` secrets.  Cleaner
  narration also feeds better WhisperX alignment (§7.5/§7.10 closed).
- Notebooks: `PHOTO_BANK_MAX_USES` (default 2) on prebuild,
  `GRADE_MAP` on both render paths.

### P3/P4 batch (2026-07-05) — one look + hygiene + word reveal

Verification detail in `phase3/SCREENING_REVIEW.md` §4 (P3/P4 tables).

- **Documentary tone in conditioning** (P3.2): Pexels winners are pulled
  toward the film's palette (desat 0.82, warm curve, fine grain) by
  `condition_assets.py`; authentic sources untouched.  `--tone off` to
  disable; idempotent across re-runs.
- **Section parser fixed** (P3.3, closes §7.2): `sections.json` sidecar →
  legacy regexes → short-isolated-line heuristic.  The production script
  now yields opening + 3 points + closing; per-section work (grade_map,
  §15.1 stretch) is unblocked.  `parse_sections` gained `script_path=`.
- **Provenance labels honest** (P4.1): render logs now say
  `review_dossier` / `pinned_portrait` instead of `user_upload`.
- **Greppable render.log** (P4.2): the \r progress bar draws only on a
  TTY.
- **Title card polish** (P4.3): Family B accent rule always draws and
  centres on the text region (bug: was frame-centred with align=right);
  block at 0.52; optional `--title-subtitle` sub-line.
- **Word-by-word reveal** (P4.4 / §7.9, **opt-in** `--word-reveal`):
  over-image quotes build up in word-groups over ≤ 1.6 s via cumulative
  overlays + timed FFmpeg enables.  Full-text-render + feathered alpha
  masking guarantees zero reflow between steps.  Screen it before making
  it a default.
- Deferred: P3.4 per-section grade_map (design must respect the
  stream-copy concat invariant); Phase 1b LLM-emitted section sidecar.

### Running on Colab — single-repo workflow (2026-07-03)

**One repo is the source of truth: `abdoljh/Lamahat`.**  The earlier
patterns — copying `_Phase3/` from Drive, then mirroring files into a
separate `Assemble-Video` repo — produced version skew (a new
`phase3/__init__.py` next to an old `phase3/sources/`,
`ImportError: cannot import name 'Fetcher'`).  Retired.

Both notebooks now start with a **bootstrap cell** that clones the repo
at a pinned branch and works *inside the clone*:

```python
REPO   = "https://github.com/abdoljh/Lamahat.git"
BRANCH = "main"          # pin a feature branch to test unreleased work
!git clone --depth 1 --branch {BRANCH} {REPO} /content/Lamahat
%cd /content/Lamahat
```

Code, `fonts/`, `resources/` (script, narration, music, pools, photo
bank) and the notebooks all arrive together at one commit — skew is
structurally impossible.  Colab-only deps live in
**`requirements-colab.txt`** (anthropic, arabic-reshaper, python-bidi,
whisperx, openai-whisper); Streamlit Cloud keeps `requirements.txt`
untouched.  `align.py` imports whisperx/whisper lazily, so one codebase
serves both platforms.

`_resources_root()` (prebuild, decisions, render_plan) now prefers
`<cwd>/resources` when it exists, so pool discovery works from the
clone layout (`/content/Lamahat/resources`); the legacy
copy-to-`/content` layout and the `LAMAHAT_RESOURCES` env var still
work.  To host big/private assets on Drive instead of the repo, set
`LAMAHAT_RESOURCES=/content/drive/MyDrive/Lamahat/resources` in the
settings cell — nothing is copied.

### Alignment on Streamlit Cloud

Interpolation is the default (instant, ±0.2–0.5 s drift). WhisperX needs
torch + a ~1 GB Arabic model and generally **won't fit** in Cloud's ~1 GB RAM.
The Cloud-friendly path is to compute `word_timings.json` off-Cloud (Colab /
`phase3_run.py --align-only`) and upload it in the Total-solution *Alignment*
expander (`build_total_solution(word_timings_path=…)`), skipping ASR entirely.

### Original design context (still true)

Renders an ~6-min, 1920×1080 / 25 fps MP4. Committed inputs under `resources/`:
`script/main_script.txt` (Phase 1b), `audio/narration.mp3` (**ElevenLabs v3**,
not gTTS), `audio/bg_music.mp3`, `book_cover/`, `character/`, and the two
notebooks. Generated at render time (not committed): the shot-plan JSON and the
`review/` dossier.

---

## 1. What Phase 3 Is Now (vs. the v1 in CLAUDE.md)

The original Phase 3 was section-based: parse 4 Arabic sections, pull 2-3
Wikimedia images per section, Ken-Burns them, crossfade, mux. That code still
lives at `phase3/__init__.py → generate_background_video()` and remains
reachable from Streamlit. It has been **superseded** by a *shot-based*
pipeline that is now the default end-to-end path.

**v2 architecture (current)** — a *shot plan* is the source of truth. The
plan is a list of 30–65 timestamped `Shot` dataclasses produced by one Claude
Sonnet 4.6 call; the renderer executes the plan without making creative
choices. Plans are JSON, inspectable, diff-able, regeneratable from cache.

```
Script + Audio ──► align.py  ──► word_timings (WhisperX | Whisper | interp)
                                       │
                                       ▼
                                   plan.py   ──► shot_plan.json
                                                 (one Sonnet call, ~$0.10)
                                       │
                                       ▼
                                  render.py  ──► MP4
                                       ▲
                                       │
                              sources/Fetcher
                              (LoC → Wikimedia → IA → Pexels,
                               + cache + user-upload + book-extract,
                               Haiku-vision-scored)
```

**Design philosophy** (preserved verbatim from the prior session for posterity):

1. **Plan-then-render is the unlock.** The plan is a JSON document —
   inspectable, diffable, regeneratable without re-rendering. You should be
   able to look at a plan and know whether the video will be good before a
   single FFmpeg call runs.
2. **The "shot" is the unit.** Not the section. A shot has start/end (from
   word timings), a visual spec (search query + motion + framing), and
   optional overlay text. The compositor just executes the plan — no
   decisions, no fallbacks, no surprises.
3. **Honest degradation.** If WhisperX isn't installed, fall back to
   interpolated word timings. If a web source fails, fall back to the next.
   If all sources fail, fall back to a placeholder card. The render must
   complete.

### Why "shots" instead of "sections"

The diagnosis from the prior session that drove the rewrite, kept here so a
future Claude (or future me) doesn't re-litigate it:

- The original v1's *visual unit* was a 30–60 s section. Ken-Burnsing 3
  images across 45 s means each image holds for 15 s — an eternity in modern
  video. Documentary editors cut every 4–8 s with narration variation.
- Cuts were *decoupled from speech*. Visuals changed at section boundaries,
  but the dramatic moments in narration (a name, a date, a turning phrase)
  happen mid-section. Without forced alignment the system can't see them.
- Wikimedia is the *wrong primary source* for biography. It's optimized for
  "is there a photo of this thing", not "is there a *compelling* photo".
- Ken-Burns-on-everything is the AI-video tell. Real docs mix static holds
  on faces, fast pushes on action beats, whip pans for transitions.

The shot-based architecture solves all four: word-aligned cuts (or
interpolated word timings as a fallback), a 7-element motion vocabulary, and
a 4-source image waterfall ranked by Haiku vision scoring.

### Target platform & budget (locked decisions from prior session)

| Decision | Choice |
|---|---|
| Platform | **YouTube long-form** (1920×1080, 25 fps, 4–7 min) |
| Cost tier | "Quality matters" — ~$0.20–0.50 / video is fine; not $0.06 |
| Typography aesthetic | **Family A — Aljazeera Documentary editorial** (cream/charcoal, Amiri, hairlines, no Islamic geometric ornament) |
| Color grading | Knob with cinematic-warm as default; per-section variation later |
| Section transitions | The `section_mark` typography shot *is* the transition; hard cuts everywhere else, no crossfades |

### Active issues checklist (historical — all five now closed)

The ledger of the five issues identified after the first end-to-end
run.  All closed as of 2026-07-05.

| # | Issue | Status | Tracking |
|---|---|---|---|
| 1 | **Color philosophy** — knob with cinematic-warm default, tunable per section | ✓ **closed** (`--grade {warm,cool,neutral,bw}` shipped with warm default; per-section `--grade-map` shipped in the P5 batch via timeline-enabled mux filters) | §15.1 |
| 2 | **Typography aesthetic** — Family A too faint; offer Families B & C as selectable variants for testing | ✓ **closed** (Families B and C shipped; `--typography-family {A,B,C}` flag wires through `RenderConfig`; Family C uses AmiriQuranColored for headlines with red i-dots) | §15.2 |
| 3 | **Section transitions** — current rhythm too slow, doesn't hook the audience | ✓ **closed** (planner re-targeted 4.0–5.0 s; section_mark visual cap 7→5 s; 0.3 s zoom-in motion accent on section_marks; final state 61 shots avg 6.41 s — user accepted, declined further tightening) | §15.3 |
| 4 | **Captions** — title-card subtitle too small; main captions OK; under-line text small; subtitles appear merged | ✓ **closed** (patch v3.4); see CLAUDE.md | §15.4 |
| 5 | **Online/offline asset review** — pre-render dossier of all candidates + character pin + per-shot override | ✓ **closed** (file-convention override path shipped; resolution order: override → user-marked file → pinned_portrait → chosen_file → live fetch) | §15.5 |

Working principle for all five: every change exposes a knob (CLI flag,
config field, or dossier entry), keeps the existing default working,
and lands testable in isolation.  The structural plan-then-render
architecture means the user can iterate on aesthetic choices by
re-rendering against the same plan — no replanning, no replanning
cost, no re-fetching.

Family A was chosen explicitly over Family B (Netflix-doc cinematic dark
gradients — "reads as imported, not native") and Family C (manuscript /
Islamic geometric ornament — "too on-the-nose"). The aesthetic is
deliberately quiet: when this plays for someone who reads Arabic
journalism and watches Aljazeera Documentary, the visual language has to
feel native, not borrowed.

### Two CLIs

| CLI | Purpose | Stops at |
|-----|---------|----------|
| `phase3_run.py` | Plan **and/or** render in one go. Owns the v1 path too. | configurable: `--dry-run`, `--keywords-only`, `--align-only`, `--plan-only`, or full render |
| `render_plan.py` | Render a previously-saved plan to MP4. | always produces an MP4 (or writes a manifest with `--build-manifest`) |

Splitting plan vs. render is deliberate. Planning costs a Sonnet call
(~$0.10 + ~90 s wall). Rendering costs CPU minutes (~20 min). When iterating
on visuals you re-render; when iterating on shot choices you re-plan. The
split is what made the auto-split / cap-tuning / typography-template
iterations debuggable across the prior session — you could read a 43-shot
JSON, audit it, fix the prompt, regenerate, *then* render once.

### File map

```
_Phase3/
├── render_plan.py            # ★ canonical render entry (consumes plan + review/)
├── condition_assets.py       # asset resolution/aspect conditioning (pre-render)
├── prebuild_assets.py        # builds the review/ dossier (plan stage)
├── phase3_run.py             # plan-OR-render orchestration CLI
├── audit_plan.py             # quality audit of a saved plan
├── _phase3_render_only.ipynb  # ★ canonical Colab render notebook
├── resources/                # committed project inputs
│   ├── script/               #   main_script.txt  (Phase 1b output)
│   ├── audio/                #   narration.mp3 (ElevenLabs v3) + _music.mp3
│   ├── book_cover/           #   cover images (round-robin / --book-cover-pick)
│   └── character/            #   main-character portrait pool
├── fonts/                    # Amiri TTFs (incl. AmiriQuranColored for Family C)
├── output/                   # render.log, plan JSON, final MP4 (generated)
│   └── review/               # asset dossier (generated; decisions.json + shots)
└── phase3/
    ├── render.py             # plan → MP4: assets, motion, captions, grade, mux
    ├── motion_parallax.py    # 2.5D depth parallax + _fit_to_frame + camera continuity
    ├── plan.py               # Sonnet shot planner + Shot dataclass + JSON I/O
    ├── align.py              # WhisperX | Whisper | interpolation → word timings
    ├── parser.py             # Arabic section regexes
    ├── typography.py         # dispatcher (re-exports render / render_overlay)
    ├── typography_common.py  # shared tokens, fonts, TypographySpec, scrim presets
    ├── typography_a.py / _b.py / _c.py   # families A (editorial) / B (cinematic) / C (manuscript)
    ├── subtitler.py          # ASS subtitle helpers
    └── sources/              # image-fetch waterfall
        ├── __init__.py       # Fetcher orchestrator + FetcherConfig
        ├── base.py loc.py wikimedia.py internet_archive.py pexels.py
        ├── user_upload.py book_extract.py cache.py vision.py
        └── decisions.py      # review dossier load/save + render-time resolution

# Legacy / duplicates (superseded — safe to delete; see §16):
#   phase3/__init__.py (v1 generate_background_video), compositor.py, effects.py,
#   keywords.py, pexels.py, wikimedia.py, render_previews.py;
#   phase3/render_plan.py & phase3/prebuild_assets.py (stale copies of the root
#   entry points — the ROOT versions are canonical);
#   root dev scaffolding: diagnose_*.py, verify_*.py, sandbox_test.py,
#   make_test_png.py, trim_book_cover.py.
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
`slow_pull`, `pan_left`, `pan_right`, `ken_burns`. `static_hold` is applied
to typography and placeholder cards always; the other six only fire for
fetched real images (see `_MOTION_FILTERS` in `render.py`).

**Typography templates** (5): `pull_quote`, `name_reveal`, `date_stamp`,
`chapter_heading`, plus implicit `title_card` / `section_mark` styles. All
rendered by `typography.py`.

### Plan invariants (enforced by `plan._validate_plan`)

1. Shots are sorted by `start` and **contiguous**: `shot[i].end == shot[i+1].start`.
2. First shot starts at `0.0`; last shot ends at `total_duration_sec`.
3. Per-visual hard caps (with 0.1 s floating-point tolerance); above the cap
   `_validate_plan` splits a shot into ~5 s pieces and tags each
   `[auto-split k/n]`:
   - `typography`, `portrait` → **12 s**
   - `archive`, `broll`, `location`, `object` → **10 s**
   - `section_mark` → **7 s**
   - `title_card` → **7 s**
4. **Adjacent shots with identical `(visual, search_query)` or
   `(visual, typography_text)` are merged** (`_shots_can_merge`) and their
   `caption_text` concatenated. This pass runs *after* splitting, so it
   reverses any unnecessary split. It's the single most important plan
   post-processing step — see §6 history of how it was tuned.
5. Field exclusivity: typography-kind shots (`title_card`, `section_mark`,
   `typography`) keep `typography_text`, drop `search_query`. Image-kind
   shots do the opposite (`_normalise_fields`).
6. Shot boundaries snap to actual word boundaries (`_snap_to_word_boundaries`).
   Minimum shot duration after snap: 1.5 s.

These invariants are why the renderer can be dumb — by the time it sees a
plan, the math is consistent.

### Title card / typography template dispatch — known footgun

When `visual` is `title_card`, `section_mark`, or `chapter_heading`, the
renderer **forces the template by visual type** regardless of what
`typography_template` says. Sonnet often annotates a `title_card` shot with
`typography_template: "chapter_heading"` (hedging), and trusting that
annotation produces the wrong opening visual. Fix is in `render.py` —
typography template hint is only respected when `visual == "typography"`.

---

## 3. Sources Subsystem (image-fetch waterfall)

`sources/Fetcher.fetch_for_shot(query, shot_index)` runs this priority order:

1. **User upload** — `--user-dir <path>`. File matched by name pattern
   `shot_NN.jpg` (NN = 1-indexed shot number) or by `manifest.json`.
2. **Book extract** — `--book-extracts <Phase1a photos.zip or dir>`.
   Vision-scored against the shot query (requires `--anthropic-key`).
3. **Disk cache** — `~/.cache/lamahat/images` keyed by query hash. Disable
   with `--no-cache`.
4. **Live web fetch** in order: LoC → Wikimedia → IA → Pexels. All
   candidates from all sources are pooled, downloaded, vision-scored, then
   ranked by `vision.rank_candidates`. Top survivor wins.

`VisionScorer` (Haiku, `claude-haiku-4-5-20251001`) emits three integer
scores per image (`subject` / `quality` / `cinematic`, 0–3 each, total 0–9).
Keep threshold: `total ≥ 4 AND subject ≥ 1`. **Critically, the Haiku call
is fail-open** — on any exception the candidate is assigned
`(subject=2, quality=2, cinematic=1) = 5` and kept. See §7.4 for the
downstream consequence.

### License posture

`base.is_free_license()` accepts everything CC-*, PD, "no known
restrictions", plus unknown. Rejects anything with `NC`, `ND`, "all rights
reserved". Pexels is hard-coded as `"Pexels License"` (permissive but with
attribution conventions — double-check before public release).

### Required-images manifest mode

`render_plan.py --build-manifest output/required_images.txt` produces a
review table without hitting the network:

```
shot_05  portrait  "Jafar al-Askari Iraqi general historical portrait 1920s"
shot_08  archive   "Ottoman Empire collapse historical document 1918"
shot_12  location  "Mosul Iraq historical photo 1904 Ottoman city"
...
```

You can review it before any render. Drop your own images into
`--user-dir` as `shot_NN.jpg`, or write a `manifest.json` mapping shot
indices to filenames. The renderer picks them up via path (1) of the
waterfall.

---

## 4. Rendering Pipeline (one MP4 from one plan)

`render.render_video(shots, out_path, *, audio_path, audio_duration_sec, config, on_progress)`:

1. For each shot:
   - Build a 1920×1080 PNG asset:
     - Typography visuals → `typography.render()` (Family A card)
     - Image visuals → `Fetcher.fetch_for_shot()` → copy chosen JPEG to PNG
     - Fallback → `_placeholder_card()` (cream card with the search query)
     - Final fallback on exception → `_error_card()` (so the timeline
       doesn't collapse — audio sync depends on every shot producing a clip
       of its planned duration)
   - Encode the PNG to an MP4 clip of the shot's exact duration. Motion only
     fires when `is_real_image=True`; typography and placeholders always
     `static_hold`. Zoom is computed against a 1.6× buffer to avoid blurry
     pan-edges. For real photos already larger than the output buffer, the
     code probes native dimensions to avoid unnecessary upscaling.
2. **Stream-copy concat** of all shot clips → `background.mp4`. Works only
   because every clip uses identical encoder settings (`libx264 -preset
   ultrafast -crf 22 -pix_fmt yuv420p -r 25`). Change one shot's profile
   and the concat silently breaks.
3. **ASS captions** (`_write_captions`):
   - **Current**: white Amiri text with charcoal outline (BorderStyle 1).
   - **Intended** (Family A spec): small Amiri Regular charcoal on
     translucent cream bar, bottom 8 % of frame, "Aljazeera Documentary
     subtitle, not TV captions".
   - **Why the gap**: libass's BorderStyle 3 + alpha-tinted BackColour
     doesn't actually blend — the "50% cream" backplate rendered as opaque
     white. White-on-charcoal-outline was the working fallback. To restore
     the intended look, the cleanest path is to burn the backplate as a
     separate semi-transparent FFmpeg `drawbox` alongside the ASS subs.
4. **Final mux** (`_mux_final`): single FFmpeg pass that re-encodes the
   video (required to burn subs), adds AAC audio at 192 kbps with
   `-shortest`, then `-t max_duration` if set. The re-encode pass is ~5 min
   of the ~21-min total.

Everything FFmpeg is shelled via `subprocess.run`, working under
`tempfile.TemporaryDirectory` so RAM stays low — important for Streamlit
Cloud's 1 GB ceiling.

### Captions skip typography shots

A typography shot already shows its Arabic text full-screen at hero size.
Drawing the caption again at the bottom would be redundant. `_write_captions`
filters: `s.visual not in TYPOGRAPHY_VISUALS`. Don't undo this.

---

## 5. The Canonical Run, Decoded

The notebook `_phase3_main.ipynb` is the authoritative reference for what
works today. The cell-by-cell pipeline:

| Cell | What it does | What its output proves |
|------|--------------|------------------------|
| 0 | Mount Drive, copy `_Phase3/` into `/content` | Colab working dir is `/content`, not `_Phase3/` — CLI paths are relative |
| 1 | `pip install anthropic` (0.102.0) | Sonnet + Haiku reachable |
| 2 | WhisperX/Whisper install — **commented out** | Alignment uses interpolated backend (§7.5) |
| 3 | `apt install fonts-hosny-amiri` (0.113-1) | Amiri available system-wide via fontconfig |
| 4 | `pip install arabic-reshaper python-bidi` | Fallback path for non-libraqm Pillow builds |
| 5–6 | matplotlib + `phase3.typography.FONT_PATHS` sanity check | Reveals Amiri-discovery bug — §7.1 |
| 7 | Load API keys from Colab Secrets | Both set |
| 8 | `phase3_run.py --align-only --align-backend interpolated` | 653 word tokens, **only 2 sections parsed** — §7.2 |
| 9 | `phase3_run.py --plan-only` | Sonnet returns 43 shots covering 0.00–391.00 s in 91.4 s; one call (~$0.10) |
| 10 | `audit_plan.py` | 0 gaps/overlaps; 35 % typography (in target); 14 % auto-split; 22 search queries, avg 7.5 words; no bare queries |
| 11–12 | `render_plan.py` background + tail-monitor | "Done in 1263 s — output/final_cut.mp4 (26.6 MB)" |
| 13 | Zip outputs (excluding the .mp3) | `output_files.zip` with `final_cut.mp4 + render.log + plan.json + word_timings.json + planner_raw_response.txt` |
| 14 | Copy zip to Drive | Final deliverable on `/MyDrive/_Phase3/output_files.zip` |

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
   title_card       2 (   5%)               ← open + close, correct

Motion types:
   static_hold     28 (  65%)
   slow_push       13 (  30%)
   pan_right        2 (   5%)

Section coverage:
   opening         33 shots
   closing         10 shots                 ← see §7.2

✓  Auto-split shots: 6/43 (14%) from 6 original(s)
Typography texts: 21 unique (avg 11.4 words)
Search queries: 22 non-empty, avg 7.5 words   ✓ none bare
```

The plan is healthy on every dimension except *section structure*.
Auto-split is 14 %, comfortably below the 20 % "tighten the prompt" line.
Typography density at 35 % sits right on the prompt's target ceiling.

---

## 6. History of the Plan-Validation Iteration

Worth preserving because the cap values look magic in the code and a future
session might lower them "to keep shots short". They were tuned through
three iterations of *empirical* feedback — don't lower them again without
re-reading this section.

| Iteration | Cap | Result |
|---|---|---|
| v1 (initial) | 6 s for everything | **74 % auto-split**, 67 Sonnet shots became 106 pieces, avg 3.7 s — TikTok pacing not documentary |
| v2 (raised) | 8 s for everything | 16 % auto-split, 67 → 64. Better, but typography pull-quotes that genuinely needed 13 s of read time were being chopped into three identical 4.4 s halves |
| v3 (type-aware + merge) | typography/portrait 12 s, archive/broll/location/object 10 s, section_mark 7 s, **+ merge-adjacent-duplicates pass** | 3 % auto-split, avg 6.5 s. Documentary pacing |

The lesson: a typography pull quote held for 12 s is correct, not a runaway
that needs splitting. Documentary pacing favours longer holds on faces and
hero text; only when shots exceed *visual-type-specific* caps do they need
splitting. And even then, if two split pieces have identical content, the
merge pass fuses them back so the caption layer doesn't see three separate
2-second caption windows for what was one 6-second hold.

The 0.1 s floating-point tolerance on the cap check matters: shots that
land at exactly the cap (e.g. an 8.04 s archive after word-boundary
snapping) used to get split into two 4 s halves. Tolerance prevents that.

---

## 7. Open Issues, In Priority Order

### Tier 1 — these distort *every* output

#### 7.1 Amiri discovery falls through despite a system install — **FIXED**

**Status**: patched in the bundled `typography.py` and `render.py`.
See `typography.diff` for the full change.

**Original failure mode** (now resolved): cell 6 reported
`Amiri not found on system — downloading from upstream`, even though
cell 3 successfully installed `fonts-hosny-amiri` to
`/usr/share/fonts/opentype/fonts-hosny-amiri/`. The 6 MB fallback
download was triggered on every cold start.

**Root cause** (revealed by reading the actual Debian package contents):
the Ubuntu jammy package `fonts-hosny-amiri 0.113-1` (what Colab installs)
ships `Amiri-Slanted.ttf` and `Amiri-BoldSlanted.ttf` — not
`Amiri-Italic.ttf` and `Amiri-BoldItalic.ttf` that the discovery code
required. Upstream renamed `Slanted` → `Italic` in version 0.114 (2020),
but Ubuntu still packages the pre-rename release. The
`all(found[k].exists() for k in required)` gate failed on `italic` and
`bold_italic`, every system-path strategy fell through, and the code hit
the upstream download path.

Two compounding issues hid the root cause:
- The repo's bundled `_Phase3/fonts/` directory (already present at
  `github.com/abdoljh/Lamahat/_Phase3/fonts/`) was not searched at all by
  discovery, and the Colab cell 0 doesn't copy `fonts/` into `/content/`.
- The fail-open message was misleading: "Amiri not found on system" is
  technically true but doesn't say *which paths* were tried or why each
  was rejected.

**The fix** (4 changes):

1. **Repo-bundled `fonts/` is now Strategy 1** — searched before
   fontconfig, environment override, or system paths. Six probe paths
   cover the package layout (`_Phase3/fonts/`), CWD-relative invocations,
   and the live Colab Drive mount (`/content/drive/MyDrive/_Phase3/fonts`)
   in case the notebook doesn't copy the directory.
2. **Per-weight filename aliases** via a `_FONT_ALIASES` map. `italic`
   slot now accepts `Amiri-Italic.ttf` or `Amiri-Slanted.ttf`;
   `bold_italic` accepts `Amiri-BoldItalic.ttf` or
   `Amiri-BoldSlanted.ttf`. The Colab Debian package now resolves cleanly.
3. **Required weights narrowed to `regular` + `bold`**. Italic and
   `bold_italic` are optional — `_font()` already falls back to regular
   when an italic weight is missing, so requiring them at discovery time
   was an overconstraint.
4. **`render.py` passes `fontsdir=<amiri dir>` to the libass `ass` filter**
   so burned-in captions render Amiri even when fontconfig hasn't been
   refreshed (the secondary failure mode where libass silently
   substitutes DejaVu and Arabic letters lose shaping).

**Diagnostics also improved**: each discovery strategy now logs its
specific reason for not matching (e.g. `fc-match: returned
/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf (not an Amiri file —
fontconfig substituted)`), and the final RuntimeError lists every path
tried. No more "Amiri not found on system" one-liners.

**Verification**: run `python verify_font_discovery.py` (bundled). It
logs which strategy succeeded, prints all resolved weight paths, and does
an end-to-end Pillow render smoke test.

| Scenario | Before patch | After patch |
|---|---|---|
| Repo `fonts/` available | Ignored, falls through to download | **Strategy 1 — picks it immediately** |
| Colab post-`apt install fonts-hosny-amiri` (0.113-1) | Falls through (italic filename mismatch), downloads | **fc-match resolves, aliases pick up `Slanted` files** |
| `fc-match` returns DejaVu substitute (matplotlib race) | Falls through silently, downloads | Rejected with reason logged, falls through to system paths |
| Fresh Colab cold start, fonts not yet on Drive | 6 MB upstream download per cold start | **Drive-mount probe finds them; one Drive read** |
| Truly no Amiri anywhere | Downloads silently | Downloads; on download failure, lists every path tried |

#### 7.2 Section parser only recognises rigid template headers

(Note: in the prior session this was *accepted* as adequate — "Sonnet
correctly identifies subtopic boundaries via `section_mark` shots". I'm
flagging it here as still worth addressing because it removes structural
pressure on the planner. Demote to Tier 2 if you disagree.)

The alignment cell reports `653 word tokens, 2 sections`. The real script
has 5 logical sections — opening + 3 descriptive points + closing — but
`parser._SECTION_HEADERS` only matches the rigid v1 template
(`النقطة الأولى/الثانية/...`, `الخاتمة`, `تقديم الكتاب`). The current Phase
1b summariser emits descriptive titles instead:

```
Line  9: من الموصل إلى الاستانة — رحلة التحديث والطموحْ
Line 17: الصراع الأيديولوجي والسياسي — بين الولاء والحلمْ
Line 25: الحرب والاختبار النهائي — الفعل والالتزامْ
Line 33: الخاتمة: شهادة لا تموتْ                    ← only this matches
```

Result: `opening = lines 1–32` (one 287-second blob) and
`closing = lines 33–43`. Audit confirms: `opening 33 shots, closing 10
shots`. Sonnet still introduces 4 `section_mark` shots on tonal breaks,
but the *intended* structural mapping (one set of visuals per thematic
point) is lost.

**Fix options**, ranked by leverage:
1. **Loosen the parser**: detect any line that ends with `.` or `ْ` and
   sits between blank lines. Cross-check with line length (<80 chars).
   Promote those to auto-generated sections `point_1`, `point_2`, ….
2. **Synchronise with Phase 1b**: have Phase 1b emit a sidecar JSON
   listing section boundaries by line number. Cleaner — keeps the script
   copy-pasteable.
3. As a stop-gap, set `parser._SECTION_HEADERS` to a single broad pattern
   matching "any short line followed by a blank line".

#### 7.3 LoC / Wikimedia / Internet Archive return 0 candidates per query

The single biggest visual-quality issue. In the latest 43-shot plan, *every*
image shot's query went through this waterfall:

```
LoC:               0 candidates for 'Jafar al-Askari Iraqi general historical portrait'
Wikimedia:         0 candidates for 'Jafar al-Askari Iraqi general historical portrait'
Internet Archive:  0 candidates for 'Jafar al-Askari Iraqi general historical portrait'
Pexels:            3 candidates for 'Jafar al-Askari Iraqi general historical portrait'
```

But Wikimedia Commons demonstrably has `Category:Mahmud_Shevket_Pasha` with
PD photographs; LoC has 1880-1940 MENA holdings; IA has period books. The
problem is **query construction and filter strictness**, not content
availability.

**Probable causes, in descending order**:

1. **Over-specific multi-word queries**. MediaWiki's `gsrsearch` is
   phrase-AND. Six tokens — `'Jafar al-Askari Iraqi general historical
   portrait'` — require all six in file metadata. **Fix**: add
   `query_simplify()` that strips generic tails (`portrait historical
   photograph archive picture`), keeps proper nouns + dates. Simplified
   first, full as fallback.
2. **Query/index mismatch**. LoC tags historical photos as "Ottoman Empire
   — History — 1909-1918" or by city/person — broad English phrases like
   "Ottoman Empire Arab officers 1910" don't hit those tags. **Fix**: tune
   the planner prompt to emit archive-style queries for `archive`/
   `portrait`/`location` visual types (period place names, dynastic
   tags, dates as ranges).
3. **400 px minimum dimension filter (Wikimedia)**. `wikimedia.py:_MIN_DIMENSION = 400`
   rejects results whose `thumbwidth/thumbheight` is below 400 px. Many
   period photos are stored as 300–380 px JPEGs and get rejected.
   **Fix**: drop to 320 or remove the filter when `thumburl` is present
   (thumbs are always rescalable).
4. **`-diagram -anatomy -chart -schematic` exclusion** passed raw to
   `gsrsearch`. Combined with narrow queries, removes borderline matches.
   **Fix**: opt-in.
5. **LoC facet filter** `'fa': 'online-format:image|original-format:photo,print'`
   sometimes returns 0 when the same query in LoC's web UI returns
   thousands. **Fix**: try without `fa`, post-filter in code.
6. **IA's `mediatype:(image)` excludes `texts` items** that contain images
   (most period-book scans). **Fix**: broader search, derive image URL
   from metadata endpoint.
7. **Network timeouts** at `timeout=20`. **Fix**: one retry with
   exponential backoff.

**Concrete next steps**:
- Add `query_simplify(q)`; test on the 22 queries from the current plan.
- Lower Wikimedia `_MIN_DIMENSION` to 320, or skip when `thumburl` is set.
- Add unit tests with known-good queries (`Jafar al-Askari`, `Faisal bin
  Hussein 1920`, `Mahmud Shevket Pasha`) that **fail** if a source returns 0.
- Consider a fifth source: Wikipedia article images via
  `prop=pageimages|images` on the article slug. For named subjects the
  lead image is usually the documentary photo we want — one call, no
  vision pass needed.

#### 7.4 Vision scoring fail-open defeats source priority

The first vision call (shot 4) succeeded; from shot 5 onward, every call
returned HTTP 400 `credit_balance_too_low`. `VisionScorer.score()` catches
the exception and stamps `(subject=2, quality=2, cinematic=1) = 5`
(`_apply_neutral_score`) so the candidate isn't silently dropped.

**Downstream consequence**: with all candidates from all sources tied at 5,
`sorted()` is stable and the **original list order** breaks the tie.
Original order is `Fetcher.web_sources = [LoC, Wikimedia, IA, Pexels]`.
LoC/Wikimedia/IA all returned 0 candidates anyway (§7.3), leaving Pexels
as the sole survivor — so Pexels wins every shot **by elimination, not by
quality**.

Visible result: a documentary about an Ottoman general 1904–1936 gets
contemporary Pexels clip-art.

| Shot query | Pexels winner (verbatim from log) |
|-----------|------------------------------------|
| `Jafar al-Askari Iraqi general historical portrait` | "A stylish businessman with a briefcase exits a plane" |
| `Mahmud Shevket Pasha Ottoman general portrait historical` | "Close-up of bronze Ottoman soldier statues in Istanbul" |
| `Arab Revolt 1916 Sharif Hussein Faisal forces historical photograph` | "Libyan soldiers holding rifles and red flares" |
| `Jafar al-Askari portrait Iraqi statesman historical` | "Vandalized sculpture in a Baghdad park" |

**Fixes**:
- **Restore Anthropic credits** — without them both the planner (Sonnet)
  and the scorer (Haiku) degrade. Treat the credit balance as critical
  path.
- **Change the fail-open policy**: when at least one *real-scored*
  candidate exists in the pool, drop the unscored neutral-5 ones from the
  ranked list. When *no* candidate scored cleanly, fall back to source
  priority (which is current behaviour, just with extra noise).
- **Add a circuit breaker**: after N consecutive vision errors with the
  same error class (`invalid_request_error / credit_balance_too_low`),
  disable vision for the rest of the run and rely on source priority alone.
  Log once. The render log was 70 KB almost entirely from one repeated
  error.

### Tier 2 — quality plateaus

#### 7.5 Forced alignment uses the interpolated backend

WhisperX/Whisper install is commented out in cell 2. The interpolated
backend distributes time by character count (~12 chars/sec heuristic).
Drift is ±200–500 ms per word — adequate for caption sync, but
`_snap_to_word_boundaries` snaps to *interpolated* word endpoints, not
measured ones.

Cost trade-off:
- WhisperX: ~30–60 s on CPU for a 3-min file, ~600 MB peak RSS, free.
- Whisper-only: ~45 s, similar memory, less accurate word boundaries.
- Interpolated: instant, free, ~300 ms typical drift.

Diacritics throw the interpolation off in a small way: characters in the
Arabic Presentation Forms block count toward duration, so a heavily
diacritised word like `مُذَكِّرات` gets 1.02 s while plain `جعفر` gets 0.41 s.
The TTS reads them at similar speed. Real WhisperX alignment removes this
distortion entirely.

For Streamlit Cloud's 1 GB ceiling, WhisperX may collide with FFmpeg work.
Two paths:
- Run alignment in a subprocess so model RAM is reclaimed before render.
- Accept interpolated for now; revisit when ElevenLabs TTS lands (cleaner
  audio → easier alignment).

#### 7.6 Shot duration distribution skews long — **partially addressed**

**Original audit** (43-shot plan): average 9.09 s, range 4.49–12.17 s.
The planner prompt's target was ~5 s per shot; hard caps 10–12 s.
The 14 % auto-split rate showed Sonnet brushing against the caps.

**Post-issue-3 audit** (61-shot plan): average 6.41 s, range 3.91–9.23 s,
auto-split 2 %.  The planner-prompt fixes from §15.3 — explicit
"TARGET RANGE: 4.0–5.0 s avg", section_mark cap 7→5 s, user-prompt
framing flipped — pulled the average down by ~1.7 s and tightened
the upper range from 12.2 s to 9.2 s.  Sonnet still lands ~1.4 s
above target, but the user accepted this state.

**What's left**: if a future session wants to push further, the
remaining levers are (1) tighten the per-visual upper bounds in rule
3 of the system prompt (typography/portrait 10→8 s, archive/broll 8→7
s), (2) re-frame the user-prompt to make the count target *exact*
rather than *aim*.  CLAUDE.md §6 documents the user's instruction to
not push harder unless asked.

### Tier 3 — polish

#### 7.7 Pillow typography cards for unmatched image shots

When all sources for an image shot return nothing or vision rejects
everything, `render._placeholder_card()` produces a cream card showing the
search query in Latin. It's *technically* fine but reads as "TBD". Replace
it with a fully-styled typography card that reuses the *Arabic* key phrase
from the same section — turns gaps into intentional design moments.

#### 7.8 Restore the intended caption styling

Captions currently use white-on-charcoal-outline (BorderStyle 1) as a
fallback. The Family A spec is small Amiri Regular charcoal on translucent
cream bar, bottom 8 % of frame. Path forward: burn the backplate as a
semi-transparent FFmpeg `drawbox` alongside the ASS subs (libass alone
doesn't honor alpha on BackColour with BorderStyle 3).

#### 7.9 Animated word-by-word reveal on typography shots

Currently `static_hold`. A 0.4 s per-word reveal on `pull_quote` and
`name_reveal` would dramatically improve perceived production value
without any new sources. RTL shaping is already correct (libraqm or
arabic_reshaper + python-bidi fallback), so it's just FFmpeg subtitle
timing on top of the existing PNG.

#### 7.10 ElevenLabs TTS handoff from Phase 2

Tier 2 in the master plan. Cleaner audio also helps WhisperX alignment
(§7.5). Stub exists in `phase2/tts.py`.

---

## 8. Unresolved Strategic Question — Path (C)

The prior session ended with a strategic proposal that the user did not
explicitly answer. Preserving it here verbatim because it changes what gets
built next:

> Pexels is structurally wrong for biography content. Should we still call
> it? Pexels indexes modern stock photography. For "Ottoman Empire Arab
> officers 1910" it has zero historical photos and returns modern
> atmospheric content because that's what it has.
>
> **Path (C) — skip the web-source rabbit hole entirely**: build the
> "Phase 1a → planner-matched book extracts" path. After Phase 1a extracts
> photos, run *one Claude call* that says "here are 20 extracted photos;
> here is the shot list; assign the best photo to each shot, leave gaps
> where no photo matches." Output is `book_manifest.json` mapping shot
> indices to filenames. The renderer reads it like a user-supplied
> manifest — no per-render vision-scoring loop, no web fetches in the
> critical path.
>
> The book's editor already curated those photos for the subject. Sonnet
> can match them to shots more reliably than any text-search API. We may
> have been chasing a sourcing problem that has a much simpler answer.

**Recommended decision**: take path (C) for the al-Askari run (the book
has period photographs of Jafar himself, Mahmud Shevket Pasha, the Arab
Revolt). Keep web sources as the secondary path for shots where no book
photo matches. This converts §7.3 from "fix three different APIs and hope"
to "ship working video using curated content, polish APIs later".

The implementation is small: a new function in `sources/book_extract.py`
that takes `(plan, photo_bank, anthropic_key)` and returns a manifest dict
in the same shape `user_upload.py` already consumes. One Sonnet call
(~$0.10), runs once per plan, not per-shot.

---

## 9. What Worked vs. What's a Compromise

| Subsystem | State | Notes |
|-----------|-------|-------|
| Parser → 8 sections | **Compromised** | Only 2 of 5 expected sections matched. Header regexes assume rigid template (§7.2) |
| Alignment | **Compromised** | Interpolated backend only. WhisperX install commented out in notebook |
| Planner (Sonnet) | ✅ Working | 91 s, ~$0.10/call, 43 well-formed shots, JSON parses cleanly |
| Plan validation | ✅ Working | Audit passes all structural checks |
| Typography rendering | ✅ Working | Amiri loaded (eventually — see §7.1) |
| Source: Pexels | ✅ Working | 3 candidates per query, every shot |
| Source: LoC / Wikimedia / IA | **Broken in practice** | 0 candidates for every query in both observed runs (§7.3) |
| Vision scoring | **Broken mid-run** | First call succeeded, rest hit `credit_balance_too_low`. Fail-open then bricks ranking (§7.4) |
| Renderer | ✅ Working | 43-shot run: 1263 s, 26.6 MB, no errors, exactly 391 s output |
| Captions (ASS) | ✅ Working / ⚠ aesthetic gap | Burns cleanly. White-on-outline is a fallback from the intended cream-bar design (§7.8) |
| Mux | ✅ Working | AAC 192 kbps; `-shortest` + `-t` cap |

---

## 10. Working Configuration

### CLI invocations from `_phase3_main.ipynb`

```bash
# Cell 8 — alignment sanity check (interpolation only; instant)
python phase3_run.py \
  --script samples/main_script.txt \
  --audio  output/narration.mp3 \
  --align-only \
  --align-backend interpolated

# Cell 9 — plan the shots (one Sonnet call, ~90 s, ~$0.10)
python phase3_run.py \
  --script         samples/main_script.txt \
  --audio          output/narration.mp3 \
  --book-title     "مذكرات جعفر العسكري" \
  --character-name "Jafar al-Askari" \
  --plan-only \
  --save-plan      output/re_generated_plan.json
# NOTE: --character-name is in English (Latin), not Arabic — for the
# benefit of LoC/Wikimedia/IA which can't search Arabic well.

# Cell 10 — audit the plan
python audit_plan.py output/re_generated_plan.json \
  --script samples/main_script.txt \
  --audio  output/narration.mp3
# --script + --audio add typography-verbatim check and audio-vs-plan
# duration delta to the standard output.

# Cell 11 — render (~21 min, &-background)
python render_plan.py \
  --plan           output/re_generated_plan.json \
  --audio          output/narration.mp3 \
  --output         output/final_cut.mp4 \
  --anthropic-key  "$ANTHROPIC_API_KEY" \
  --pexels-key     "$PEXELS_API_KEY" \
  --book-title     "مذكرات جعفر العسكري" \
  --character-name "Jafar al-Askari" \
  > output/render.log 2>&1 &
```

### Required environment

| Variable | Where | Required for |
|----------|-------|--------------|
| `ANTHROPIC_API_KEY` | `.env` at repo root or `_Phase3/`, or `--anthropic-key`, or Colab Secrets | Sonnet planner, Haiku vision scoring |
| `PEXELS_API_KEY`    | same | Pexels image source (currently the only working source — §7.3) |

### Models used

| Task | Model | Configuration | Cost / 4–7 min video |
|------|-------|---------------|----------------------|
| Shot planner (one call) | `claude-sonnet-4-6` | `max_tokens=24000`, streaming | ~$0.10 |
| Image relevance scorer | `claude-haiku-4-5-20251001` (vision) | ~150 max_tokens, **always resize image to ≤ 800 px wide** | ~$0.50 (for ~100 candidates) |
| Forced alignment | WhisperX `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` | Currently disabled; interpolation used | $0 either way |

### `--verbose` and the log-size trap

`--verbose` previously enabled DEBUG-level logging on the `anthropic` and
`httpx` loggers, which dumped full base64 image payloads (~400 KB per
vision call) into the log. A 69-shot run produced 200+ MB log files that
got truncated mid-base64 and lost the actual crash traceback.

Staged fix from prior session: keep verbose for `phase3.*` loggers, but
suppress DEBUG on `anthropic` and `httpx`. Verify this has shipped before
diagnosing any future crash from logs.

---

## 11. Recommended Session Order

The user-driven work is the §15 issues checklist (1–5).  The
technical backlog is §7.  Order by what unblocks the most progress:

**§15 issues — finish these first**

1. **Close the issue 5 loop** (§15.5) — render once with
   `--review-dir output/review/`.  Confirms the override → pin →
   chosen_file resolution actually behaves at render time the way it
   does in the unit tests.  Cheapest test in the queue and the last
   thing standing between issue 5 and ✅.
2. **Issue 4 — captions** (§15.4) — three independent tweaks inside
   `typography.py` and `_write_captions`.  Cheapest aesthetic win.
3. **Issue 3 — section transitions** (§15.3) — tighten the planner
   prompt's average shot duration to 4–5 s, optionally add a 0.3 s
   motion accent on `section_mark`.  Touches `plan.py` + `render.py`.
4. **Issue 1 — color grading knob** (§15.1) — `--grade
   {warm,cool,neutral,bw}` flag mapping to one FFmpeg filter chain in
   the final mux.
5. **Issue 2 — typography families B & C** (§15.2) — the heaviest of
   the five.  Roughly 700 LOC per family; can be parallelised.

**§7 technical backlog — pick up after the issue checklist is done**

6. **Restore Anthropic credits** before any further benchmarking
   (§7.4).  Without them planner and scorer both degrade silently.
7. **Decide on path (C)** (§8) — the unresolved strategic question.
   Probably unlocks more visual quality than fixing §7.3.
8. **Fix the source query strategy** (§7.3) — only if path (C) isn't
   sufficient or as a parallel improvement.
9. **Patch the vision fail-open policy** (§7.4).
10. **Fix the section parser** (§7.2) — 5 logical sections collapse to 2.
11. **Decide on Whisper/X for alignment** (§7.5).
12. **Restore intended caption styling** (§7.8) — cream-bar via
    FFmpeg drawbox layer.  Convergent with §15.4.
13. **Pillow typography placeholder cards** (§7.7).
14. ~~**Amiri discovery on system paths** (§7.1)~~ — **fixed**.
15. **Tighten shot duration distribution** (§7.6).  Convergent with §15.3.

---

## 12. Things Not To Touch (or touch with care)

- **The plan/render split.** Two CLIs, two responsibilities. Mixing them
  was the original mistake; the split is what made every iteration in §6
  diagnosable from a JSON file.
- **`_validate_plan` invariants.** Renderer assumes them. Loosen one →
  break the concat pass or the caption layer.
- **The merge-adjacent-duplicates pass in `_validate_plan`.** Without it,
  long pull quotes get split into identical halves with separate caption
  windows. The pass is what makes long holds feel like single takes.
- **Arabic rendering uses `libraqm` when available, fallback to
  `arabic_reshaper` + `python-bidi`.** Confirmed working on Pillow 12.2.0.
  Don't add a third path. Don't use FFmpeg `drawtext` for any Arabic — it
  has no bidi shaping.
- **800 px image-resize before vision scoring** (`vision.py:~117`).
  Larger → API 400. Known constraint.
- **Stream-copy concat in `_concat_clips`.** Works only because every
  shot clip uses identical encoder settings. Changing one shot's encoder
  profile silently breaks the concat — fall back to filter_complex concat
  if you need per-shot variations.
- **`fail-open` in `VisionScorer.score`.** Don't flip it to fail-closed —
  that drops *all* candidates the moment Anthropic has a 5 s blip.
  Instead, demote unscored candidates only when scored ones exist (§7.4).
- **Title cards force the template by visual type, not by Sonnet's
  `typography_template` hint.** Sonnet hedges with `chapter_heading`;
  trust the `visual` field.
- **`--character-name` in Latin, not Arabic** — for LoC/Wikimedia/IA
  search compatibility. The book title can stay Arabic.
- **Captions skip typography shots** (`TYPOGRAPHY_VISUALS` filter in
  `_write_captions`). The typography is the caption.

---

## 13. Quick Reference

```bash
# Histogram of shot types in a plan
python -c "import json; from collections import Counter; \
  d=json.load(open('output/re_generated_plan.json')); \
  print(Counter(s['visual'] for s in d))"

# Total plan duration vs. audio
python -c "import json; d=json.load(open('output/re_generated_plan.json')); \
  print('plan end:', d[-1]['end'])" && \
  ffprobe -v quiet -show_entries format=duration \
    -of default=nw=1:nk=1 output/narration.mp3

# Which shots ended up on Pexels (= queries that failed every other source)
grep "using fetched image from pexels" output/render.log | wc -l

# Inspect planner's raw response (saved on every plan build)
less output/planner_raw_response.txt

# List cached images after a real render
ls -la ~/.cache/lamahat/images/

# Verify Amiri discovery before rendering (no FFmpeg cost)
python -c "from phase3.typography import FONT_PATHS; print(FONT_PATHS)"

# Manifest mode — review image-shot list without hitting network
python render_plan.py --plan output/re_generated_plan.json \
  --build-manifest output/required_images.txt && \
  cat output/required_images.txt
```

---

## 14. Known Environment Constraints

| Constraint | Detail |
|-----------|--------|
| Streamlit Cloud RAM | 1 GB — keep FFmpeg work in subprocesses. v2 render obeys this. |
| Python | **3.12.13** (set in Cloud Advanced settings). Don't assume 3.13/3.14. |
| Colab CPU runtime | ~21 min for a 391 s render at 1920×1080. Mostly FFmpeg + vision RTTs. |
| FFmpeg subtitle path escaping | `:` and `\` need escaping in `-vf "ass=…"`. See `_mux_final`. |
| Claude vision max image size | Always resize to ≤ 800 px wide. Larger → 400 error. |
| Pillow libraqm | Confirmed available on Pillow 12.2.0 (Colab, Streamlit Cloud). Modern raqm handles Arabic shaping natively with `direction="rtl"` — *don't* use `arabic_reshaper` on text destined for Pillow with libraqm, it actively breaks shaping by replacing Unicode characters with explicit presentation-form glyphs that bypass raqm. |
| Arabic font in ASS | `Fontname: Amiri` (`fonts-hosny-amiri` Debian package on Cloud). |
| Pexels key | Optional in the contract, mandatory in practice given §7.3. |
| Anthropic key | Required for planner AND scorer. Treat as critical-path. |
| GitHub upload size | Artefacts > 25 MB get truncated/partial in `_Phase3/output/`. `final_cut_3a.mov` is a 181 s preview of a 391 s render. Real output: `output_files.zip` from cell 13. |
| `raw.githubusercontent.com` / `media.githubusercontent.com` | Often blocked from sandboxed network policies. Use file uploads or direct paste for log/artefact handoff to a fresh Claude session. |

---

## 15. Issue tracking (this session's review-the-rough-cut feedback)

### 15.1 Color philosophy — **closed** (2026-07-05)

**Goal**: knob with cinematic-warm as default; per-section variation
later.

**Outcome**: `--grade {warm,cool,neutral,bw}` shipped (warm default,
GRADE_PRESETS in render.py, applied at the final mux) and per-section
variation shipped in the P5 batch as `--grade-map file.json`
(GRADE_PRESETS_TIMELINE; unmapped sections fall back to `--grade`).

**Original notes** (kept for context): the renderer used to apply no
grading; all grading was baked into the source imagery and the
typography backgrounds.

**Notes for next session**: the right shape is probably a
`--grade {warm,cool,neutral,bw}` flag on `render_plan.py` that maps to
a single FFmpeg `curves`/`eq`/`colorbalance` chain applied in the final
mux.  Section-level variation is a stretch goal — the planner already
emits `section_id` per shot, so a `grade_map.json` keyed on section_id
can drop in later without re-planning.

### 15.2 Typography aesthetic — Families B and C — **closed**

**Outcome**: Families B and C shipped alongside Family A.  Selectable
via `--typography-family {A,B,C}` on `render_plan.py`.

**Architecture**: `typography.py` was refactored from a 1228-line
monolith into a dispatcher + three sibling modules:

- `typography_common.py` — shared design tokens, font discovery,
  helpers (`_font`, `_measure`, `_draw_text_rtl`, `_apply_grain`,
  cover-fitting helpers), and the `TypographySpec` dataclass (now with
  a `family: Literal["A","B","C"]` field).
- `typography_a.py` — Family A renderers, verbatim lift; behaviour
  unchanged.
- `typography_b.py` — Family B (Netflix-doc cinematic): vertical
  dark gradient, off-white Amiri Bold headlines, deep gold accent
  rules, no diamond/quote ornaments.
- `typography_c.py` — Family C (manuscript): aged-paper vignette,
  sepia ink, burgundy bracket ornaments, double-rules, visible «»
  on pull_quotes.  Headlines use AmiriQuranColored (falls back to
  Amiri Quran B&W, then Bold).
- `typography.py` — dispatcher; re-exports the public API verbatim
  so `render.py`'s import line is unchanged.

**Public surface preserved**: `render`, `TypographySpec`, palette
constants, `FONT_PATHS`, `_font`, `_measure`, `_draw_text_rtl`,
`_apply_grain` are all importable from `phase3.typography` at the
same names as before.

**Family C colour-glyph wiring**: Pillow only activates COLR/CPAL
palettes when `draw.text(..., embedded_color=True)` is passed.  The
`_draw_text_rtl` helper gained an `embedded_color: bool = False`
kwarg that the four Family-C headline calls set to True; the helper
checks `_font_has_color_palette(font)` before activating the path,
so the flag is a no-op on Bold/Quran B&W.

**Headline vertical padding**: Family C sets `HEADLINE_VPAD_FRAC =
0.35` to clear Quran's larger diacritic-clearance bbox (otherwise
subtitles overlap the headline glyphs).

### 15.3 Section transitions — **closed**

**Outcome**: rhythm tightened on both planner and renderer sides.

**Planner changes** (`plan.py`):

- `build_shot_plan()` default `target_shot_duration`: 5.0 → 4.5 s
- `_SYSTEM_PROMPT` rule 3: per-visual `section_mark` cap dropped from
  7.0 to 5.0 s; pacing nudge rewritten as "TARGET RANGE: 4.0–5.0 s
  avg" with explicit "cut faster around section_marks"
- `_USER_PROMPT_TMPL`: framing flipped — target is now a floor, not
  a ceiling
- `_validate_plan` `TARGET_PIECE`: 5.0 → 4.5 s (auto-split target)

Hard caps (`HARD_CAPS` in `_validate_plan`) were **not** touched —
the fix is in the prompt, not the safety net.

**Renderer changes** (`render.py`):

- New motion type `section_accent` in `_MOTION_FILTERS`: 0.3 s zoom-in
  ramp from 1.00 → 1.05 over 8 frames (`min(1.05, 1.00 + 0.05/8 * on)`),
  then static hold for the remainder
- New `RenderConfig.section_mark_accent: bool = True` (opt-out flag)
- Dispatch in `render_video()` overrides `static_hold` to
  `section_accent` for any shot where `visual == "section_mark"` and
  the config flag is True.  Other typography (title_card,
  chapter_heading, pull_quote, etc.) remains `static_hold`.

The accent is applied at render-dispatch time, not in the plan, so
re-rendering an existing plan picks it up without re-planning.

**Final state**: shot count 48 → 61; avg shot duration 8.15 s → 6.41
s; range 3.91–9.23 s.  Auto-split rate stable at 2%.  User accepted
6.41 s avg (above the 4.5 s prompt target) and explicitly declined
further planner tightening — don't push it harder unless asked.

### 15.4 Captions — **open**

**Goal**: bigger title-card subtitle, less merging in body captions,
restore the intended Family A cream-bar look.

**State** (from the latest render):
- Main body captions: almost accepted (white-on-charcoal-outline).
- Title-card sub-line: too small.
- Under-line text on `name_reveal` / `date_stamp`: too small.
- Multi-line captions appear merged.

**Notes for next session**: three independent fixes.
(a) bump the title-card subtitle from `SIZES["title_sub"]` (0.030
height-fraction) to ~0.040 — but verify it doesn't push the bottom
hairline rule out of frame on long subtitles.
(b) for `name_reveal` / `date_stamp` sub-lines, lift from 0.022 to
0.028.
(c) the "merged" look is libass burning consecutive caption events
back-to-back with no inter-event gap — add 0.15 s pre-roll/post-roll
silence inside `_write_captions` so the eye sees one event end before
the next begins.

### 15.5 Online/offline asset review — **closed** (2026-07-03: the
88-shot production render consumed the dossier end-to-end — override
chain, pool rotation and conditioned files all observed in
`output/ph3/render.log`)

**Status**: mechanism shipped in code and merged to `main`.  Dossier
exists at `_Phase3/review/decisions.json` and has been built end-to-end
against the latest plan — 27 image shots, every one with a
prebuild-chosen `chosen_file`.  The user has begun hand-curating:
`pinned_portrait` is set to `overrides/character.jpg`, and shot 2's
`chosen_file` has been swapped to `shot_02_portrait/my_jafar_1.jpg`.
What's missing is a **render run that consumes this dossier** — once
that closes the loop we'll know whether the resolution chain (override
→ pin → chosen_file → live fetcher) behaves correctly in production.

Carrying as *under consideration* until that render lands.

**One small inconsistency to be aware of when reading the dossier**:
for hand-edited shots (currently shot 2), the human-readable `chosen`
and `chosen_url` fields still describe the *original* prebuild winner
("pexels:Man in a tuxedo …"), while `chosen_file` points to the
user's edit (`my_jafar_1.jpg`).  Only `chosen_file` is authoritative
at render time — the renderer ignores `chosen` and `chosen_url`.  Not
a bug, but a usability sharp edge worth tidying eventually (e.g. a
small `prebuild_assets.py --sync-chosen-strings` post-edit pass).

**Goal**: let the user see every image candidate before rendering,
override per-shot, and pin a canonical character portrait.  Book +
main character context must inform the rubric.

**Implementation shipped** (live in `_Phase3/`):

Three new pieces and a small Fetcher patch.

| File | Role |
|---|---|
| `phase3/sources/decisions.py` (new) | `Decisions` dataclass: load/save the dossier JSON, resolve overrides at render time |
| `prebuild_assets.py` (new, at repo root) | CLI that runs the full waterfall ahead of render, writes the dossier |
| `phase3/sources/__init__.py` (patch) | `FetcherConfig.review_dir` field; `Fetcher.__post_init__` loads the dossier; `fetch_for_shot()` checks it first |
| `render_plan.py` (patch) | `--review-dir` flag |

**Workflow** (the user-facing change):

```bash
# Step 1 — plan as before
python phase3_run.py --plan-only --script ... --audio ... \
    --save-plan output/re_generated_plan.json

# Step 2 (NEW) — pre-fetch every candidate, write the dossier
python prebuild_assets.py \
    --plan          output/re_generated_plan.json \
    --script        samples/main_script.txt \
    --book-title    "مذكرات جعفر العسكري" \
    --character-name "Jafar al-Askari" \
    --anthropic-key "$ANTHROPIC_API_KEY" \
    --pexels-key    "$PEXELS_API_KEY" \
    --review-dir    output/review/ \
    --character-portrait /path/to/jafar.jpg

# Step 3 — user reviews output/review/
#   - Open shot_NN_*/context.txt to see the Arabic excerpt + English query
#   - Look at the downloaded candidate thumbnails
#   - Edit decisions.json to swap candidates or set overrides
#   - Drop personal images into output/review/overrides/

# Step 4 — render with the dossier
python render_plan.py \
    --plan       output/re_generated_plan.json \
    --audio      output/narration.mp3 \
    --review-dir output/review/ \
    --output     output/final_cut.mp4
```

**Per-shot resolution order** at render time, when `--review-dir` is set:

1. `decisions.shots[N].override` → user-supplied file in
   `overrides/shot_NN.jpg`.
2. `decisions.pinned_portrait` → applied to *every* `portrait` shot
   that has no explicit override.
3. `decisions.shots[N].chosen_file` → the prebuilt candidate the
   dossier marked best.
4. Live fetcher waterfall (LoC → Wikimedia → IA → Pexels) — only
   reached when the dossier said nothing for this shot.

The **pinned portrait** is the single biggest documentary-quality
win.  Instead of 5 different Pexels stock photos of "a man in
uniform" appearing at 5 different portrait moments (each captioned as
Jafar al-Askari), the same authentic image appears every time.  Set
once via `--character-portrait`, persisted in the dossier, applies
retroactively to every portrait shot.

**Book + main character** propagate as designed: `--book-title` and
`--character-name` flow into `FetcherConfig` exactly as in the prior
implementation, and they're recorded in the dossier under
`book.title` / `book.character` for reference at render time.  The
character name in particular disambiguates Pexels noise — every shot
folder's `context.txt` quotes the Arabic line being spoken, so the
user can tell whether a candidate fits the moment without watching a
rough cut.

**What the dossier folder looks like on disk**:

```
output/review/
├── decisions.json              ← The one file the user edits
├── README.txt                  ← In-folder usage guide
├── overrides/
│   ├── character.jpg           ← Pinned portrait (from --character-portrait)
│   ├── shot_05.jpg             ← Per-shot override the user dropped in
│   └── shot_38.jpg
├── shot_03_portrait/
│   ├── context.txt             ← "Arabic excerpt: قد يكونون من داخل صفوفك..."
│   ├── candidates.json
│   ├── loc_a.jpg
│   ├── wikimedia_a.jpg
│   ├── pexels_a.jpg
│   └── ...
├── shot_05_archive/
│   ...
```

**Edge cases handled**:

- `--character-portrait` argument absent → no pin, behaviour
  unchanged from before.
- `decisions.json` references an override file that's missing →
  logged warning, falls through to the next resolution step
  (typically the pin or the live fetcher).
- `--review-dir` points at a directory with no `decisions.json` →
  logged warning, renderer continues exactly as before
  (zero-friction adoption — no break for existing workflows).
- Dossier loaded from a different `review_dir` than where the file
  paths point → all paths inside the dossier are *relative*
  (`overrides/shot_05.jpg`, `shot_03_portrait/pexels_a.jpg`), so the
  dossier is portable; move the directory, change `--review-dir`, it
  still works.
- Zero candidates returned for a shot (the §7.3 reality):
  `chosen`/`chosen_file` stay empty; user can still drop an override
  or rely on the pin.

**Test coverage** (in a sandbox with no network):

| Scenario | Behaviour |
|---|---|
| Prebuild with no API keys / no network | 28 shots processed, 0 candidates each, dossier written cleanly, exit 0 |
| `--character-portrait` pointing at a real file | Copied to `overrides/character.jpg`, recorded as `pinned_portrait` in `decisions.json` |
| User edits `decisions.json`, sets `override` on shots 5 and 38 | Fetcher returns those files at fetch_for_shot |
| Portrait shots without explicit override | All four resolve to `overrides/character.jpg` via the pin |
| Portrait shot 38 with both override and applicable pin | Override wins (correct precedence) |
| Non-portrait shot without any dossier entry (shot 8 broll) | Dossier returns None, fetcher falls through to live waterfall |

**What this drop does not do** (intentionally deferred):

- **No GUI for review.** The dossier is plain JSON in a folder.  A
  Streamlit review pane is a Phase 4 concern; the JSON contract
  written now is forward-compatible with any UI built later.
- **No automated source-side query improvements.**  This issue is
  about giving the user *control over selection*, not about making
  the live sources return better candidates for historical biography
  content.  Source-quality work remains §7.3.


---

## 16. Final render interface (canonical)

This section reflects the **final** state at hand-back to the main application.

### Inputs & layout

Committed under `resources/` (cloned with the repo):
`resources/script/main_script.txt`, `resources/audio/narration.mp3`
(ElevenLabs v3 narration), `resources/audio/bg_music.mp3` (score bed),
`resources/book_cover/`, `resources/character/`.
Generated at render time (uploaded/synced, not committed): the shot-plan JSON
and the `review/` dossier.

### Pipeline order

```
plan (Sonnet)  →  prebuild_assets.py (review/ dossier)  →  condition_assets.py
   →  render_plan.py  →  final MP4
```

`condition_assets.py --review-dir output/review [--sr realesrgan] [--dry-run]`
normalises captured assets to crisp, aspect-correct sizes **before** render:
cover landscapes → long-edge 2560; contained/hero portraits → height ≥1600,
never down-scaled or cropped; user-added images upscaled (SR→Lanczos fallback),
never down-scaled; aspect always preserved; sub-600px flagged `res_grade=low`.
It repoints `chosen_file` to a `.cond.jpg` (original kept) and records
`native_size`/`aspect_class`/`framing`/`res_grade` in `decisions.json`.

### render_plan.py flags (current)

Core: `--plan --audio --output --review-dir --width --height --fps`.
Sourcing: `--anthropic-key --pexels-key --user-dir --book-extracts --book-title
--character-name --cache-dir --no-cache --no-vision --build-manifest`.
Book cover: `--book-cover --book-cover-pick N --book-cover-fit {fill,contain,blur_pad}
--book-cover-align {center,left,right}`.
Look: `--typography-family {A,B,C} --grade {warm,cool,neutral,bw}
--caption-backplate {off,subtle,solid} --parallax --parallax-backend
--parallax-warp --typography-over-image`.
Audio/assembly: `--music PATH --music-gain DB --no-duck --no-fades`.
**Text styling (notebook-controllable):** `--text-scrim {off,soft,band}`
(over-image plate), `--title-size MULT --title-color #RRGGBB` (main title),
`--caption-size MULT --caption-color #RRGGBB --caption-pos FRAC` (narration
captions; only when captions are enabled — the notebook runs `--no-captions`,
so the caption knobs are dormant until that flag is removed).
Captions are off by default in the notebook because on-screen Arabic comes from
the typography-over-image layer, not burned subtitles.

The Colab notebook `_phase3_render_only.ipynb` exposes these as a single
settings cell (`TEXT_SCRIM`, `TITLE_SIZE`, `TITLE_COLOR`, `CAPTION_SIZE`,
`CAPTION_COLOR`, `CAPTION_POS`, plus `GRADE`, `MUSIC_DB`, book-cover knobs, …).

### Recommended cleanup before hand-off (optional, not blocking)

The repo carries legacy/duplicate code that the v2 render path does not use:
v1 (`phase3/__init__.py:generate_background_video`, `compositor.py`,
`effects.py`, `keywords.py`, `render_previews.py`), root-level duplicates of
fetchers (`phase3/pexels.py`, `phase3/wikimedia.py` vs `phase3/sources/*`), and
**stale copies of the entry points** (`phase3/render_plan.py`,
`phase3/prebuild_assets.py`) that do **not** carry the latest flags — the ROOT
versions are canonical. Dev scaffolding (`diagnose_*.py`, `verify_*.py`,
`sandbox_test.py`, `make_test_png.py`, `trim_book_cover.py`) and stray artifacts
(`output/review/T`, `review/R`, `temp/typo_*.png`) can be removed. Deleting
these reduces the surface the main application has to reason about; none are
imported by `render_plan.py` → `phase3/render.py`.
