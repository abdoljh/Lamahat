# Phase 3 — Screening Review & Improvement Plan (2026-07-03)

Reviewed artifacts: `output/ph3/final_cut_colab.mp4` + `final_cut_streamlit.mp4`
(55 s excerpts of the 418 s render), `re_generated_plan.json` (88 shots),
`render.log`, `word_timings.json`, `planner_raw_response.txt`, against the
current code on `main` (`phase3/render.py`, `typography_common.py`,
`sources/`).

---

## 1. What is verifiably working (don't relitigate these)

| Subsystem | Evidence |
|---|---|
| **Route parity** — Streamlit ≡ Colab | SSIM 0.998 between the two MP4s. The two routes produce the same film. This was the point of the dossier architecture and it holds. |
| **Real alignment** | `word_timings.json`: 652 words, every one `"source": "whisperx"`. The interpolation era is over for Colab runs. |
| **Plan structure** | 88 shots, 0 gaps/overlaps, avg 4.75 s (target was 4.0–5.0), range 2.4–6.7 s. The §15.3 pacing work landed exactly on spec. |
| **Dossier resolution chain** | All 59 image shots resolved from the review dossier; portrait pool rotated ranks 0–6 across the 7 portrait shots; conditioned `.cond.jpg` files consumed. §15.5 can be marked **closed** — the loop is exercised in production. |
| **Renderer** | 88 shots → 2056 s render, no errors, music bed ducked at −13 dB, neutral grade applied, parallax (depth-anything, CUDA) ran. |
| **Title card** | The 3D book-cover contain-right composition with Amiri title reads as designed. Minor polish only (see §4.4). |

The machine works. Every remaining problem is *editorial*: what lands on
screen and how text sits on it.

---

## 2. Screening-room critique (specific, timestamped)

### 2.1 Anachronistic stock imagery — the film's credibility problem

Of 59 image shots: **38 Pexels (64%) · 14 Wikimedia (24%) · 7 character pool
(12%)**. The Pexels majority produces period violations that an
Arabic-documentary audience will spot instantly:

| ~t (s) | Shot / query | What's actually on screen |
|---|---|---|
| 11.6–15.0 | broll, "Ottoman Empire soldiers marching early 1900s sepia" | **18th-century European reenactors** in tricorn hats and redcoats, on bright green grass, full modern color |
| ~30–39 | archive, "Ottoman military record book documents… 1900s" | An **1815 Italian ledger** — the page literally reads "1815" and "Giugno / Per Sommministrazioni" in Italian longhand |
| ~42–48 | broll, "soldier standing crossroads desert… sepia" | A **WWII British soldier** with a Lee-Enfield, modern teal-orange grade, no desert |
| ~26 | portrait pool `jafar_at_window_w.jpg` | Reads as **AI-generated** (pith helmet — a British-colonial marker — too-clean library, video-game lighting). Sits uneasily next to the authentic colorized officer photos |

Note the pattern in the queries: the planner emits *style tokens* —
"sepia", "historical", "documentary" — and Pexels matches them **literally**,
returning modern stock *styled* as old (sunset sepia grade ≠ 1916). The
waterfall then has nothing better because Wikimedia only hit on 14 shots.
This is §7.3/§8 exactly, now with production evidence.

### 2.2 Perceived pacing is ~2× slower than planned pacing

The plan says 4.75 s/shot; the film *feels* like 9–12 s/shot. Two mechanisms:

1. **Typography-over-image reuses the previous shot's footage** (by design).
   A portrait (4.1–6.8 s) followed by its pull-quote (6.8–11.6 s) is 7.5 s
   of continuous identical footage — one perceived shot. With 23 typography
   shots interleaved among 59 image shots, nearly half the "cuts" are
   invisible.
2. **Adjacent shots resolve to near-duplicate candidates.** The Italian
   ledger occupies two consecutive archive shots (~9–12 s continuous); the
   WWII soldier likewise (~9 s). Different `shot_NN` folders, different
   `pexels_*.cond.jpg` files, same visual.

Nothing in the plan, the dossier, or `audit_plan.py` currently detects either.

### 2.3 Overlay typography: hard-centered, scrim off, hostile frames

`typography_common.py:873`: `block_top = int(H * 0.50) - total_h // 2` — the
text block is **always vertically centered**. On the shot-3 frame it lands
directly across the three officers' clasped hands/faces; on the watercolor
shot (~20 s) the thin white Amiri sits over a **cream sky** and survives only
by its stroke — at 640 px preview it's near-illegible.
`DEFAULT_OVERLAY_SCRIM = "off"`, and the shadow+stroke treatment is tuned for
dark footage (it works beautifully on the WWII-soldier frame, fails on the
bright sketch).

### 2.4 No unifying look

Within 55 s the film jumps: colorized blue-grey studio portrait → saturated
green field → cream watercolor → teal-orange sunset. The render ran
`--grade neutral`, so nothing pulls these toward one palette. A documentary
of this genre needs the *film* to own the color, not the sources.

### 2.5 Structural: sections still collapse to 2

`section_id` distribution: `opening: 66, closing: 22`. The three thematic
middle sections were not detected (§7.2 still open). Sonnet's 4
`section_mark` shots partially compensate, but per-section grading and
visual identity (§15.1 stretch goal) are blocked on this.

### 2.6 Minor / hygiene

- Every dossier hit logs `using fetched image from user_upload` —
  provenance mislabeled (`sources/__init__.py:187,208` hard-code
  `source="user_upload"` for dossier and pin candidates). Makes log
  auditing (like this review) needlessly hard.
- `render.log` interleaves the progress bar, TensorFlow banners, and INFO
  lines; separating the machine log from the tty bar would keep it greppable.
- Title card: title sits low-left with a large dead zone above; no
  subtitle/author line, no Family-B gold accent rule.

---

## 3. The one strategic decision

**Stop treating Pexels as a content source for biography; treat it as a
last-resort texture source.** Everything in §2.1 follows from Pexels winning
64% of shots by elimination. The two moves that fix the *supply* side:

1. **Path (C)** — PHASE3.md §8, still unimplemented (`book_extract.py` today
   is a per-shot vision-scored candidate source, not the one-call
   plan-matcher). The Al-Askari book has period photographs of Jafar,
   Shevket Pasha, and the Arab Revolt already curated by the book's editor.
   One Sonnet call: *"here are the extracted photos + the 59 image shots;
   assign, leave gaps"* → `book_manifest.json` consumed exactly like a user
   manifest. ~$0.10, once per plan.
2. **Wikipedia lead-image source** for named subjects (§7.3) — for
   `portrait`/`archive` shots whose query contains a proper noun, fetch the
   article's lead image via `prop=pageimages`. One HTTP call, no vision pass,
   near-guaranteed period-correct for historical figures.

With those two in place, Pexels is only reached for genuinely abstract broll
("desert landscape", "handwriting close-up") — the one category it's good at.

---

## 4. Improvement plan (priority order, each independently shippable)

### P0 — Period-true imagery (fixes §2.1) — ✅ SHIPPED 2026-07-03

Scope adjustment from the user: the book's own plates are mostly murky
halftone scans (see `output/ph3/Memoirs of Jafar al-Askari Photos.pdf`);
better copies were prepared by hand.  Path (C)'s photo bank is therefore
the **user-curated folder** (`resources/photo_bank/`), not raw Phase 1a
extractions — and the review dossier stays the veto layer.

| # | Change | Where | Status |
|---|---|---|---|
| 0.1 | **Path (C)**: photo bank Haiku-captioned once (cached, hand-editable `captions.json`), ONE Sonnet call assigns photos → shots; assigned photos become dossier `chosen_file`, waterfall candidates kept as alternates (`--photo-bank-only` to skip). Auto-detected at `resources/photo_bank/`; `--photo-bank DIR` explicit; `build_total_solution(photo_bank=…)` | new `sources/photo_bank.py`, `prebuild_assets.py`, `phase3/__init__.py` | ✅ tested offline E2E |
| 0.2 | **Wikipedia lead-image source** between Wikimedia and Pexels; `pilicense=free` server-side license filter | new `sources/wikipedia.py`, `sources/__init__.py` | ✅ code; ⚠ live call unverified (sandbox blocked wikipedia.org) — confirm on first Colab prebuild |
| 0.3 | **Era-fit vision score**: 4th rubric axis `era` 0–3 judging content (uniforms/vehicles/architecture), not color grade; demotion tier in `rank_candidates`, never a hard filter; fail-open + legacy-scores pass | `sources/vision.py`, `sources/base.py` | ✅ unit-tested |
| 0.4 | **Pexels query hygiene**: style/period tokens + bare years stripped before the Pexels call only | `sources/pexels.py` | ✅ unit-tested |
| 0.5 | **Dossier era flags**: `⚠ ERA MISMATCH` in `context.txt`, `era` in `score_breakdown`, era-flagged winners listed in prebuild summary | `prebuild_assets.py` | ✅ |

### P1 — Recover perceived pacing (fixes §2.2) — ✅ SHIPPED 2026-07-04 (1.3 deferred)

| # | Change | Where | Status |
|---|---|---|---|
| 1.1 | **Anti-duplicate resolution**: dHash (`sources/dedupe.py`) each resolved asset; an image shot within Hamming ≤ 8 of the previous image shot swaps to its next-ranked candidate (era-pass first). Applies ONLY to automatic picks — override / user-marked / pool / pin / photo-bank choices are never second-guessed. `FetcherConfig.dedupe_adjacent` (default on), `render_plan.py --no-dedupe` | new `sources/dedupe.py`, `sources/decisions.py` (`resolve_detailed`), `sources/__init__.py` | ✅ tested (re-encoded copy hamming 0, distinct 27; swap + opt-out verified) |
| 1.2 | **Effective-holds audit**: merges typography-over-image spans with their backdrop and detects adjacent duplicates (dHash with `--review-dir`, query-equality without); flags continuous-footage spans > 10 s. On the 88-shot production plan: effective avg 6.85 s vs plan 4.75 s, 10 flagged spans | `audit_plan.py` (`--review-dir`, `--assume-overlay`) | ✅ verified on real plan + synthetic dossier |
| 1.3 | Switch an overlay's backdrop to the next-ranked candidate when a span exceeds ~10 s | `render.py` | ⏸ deferred — 1.1 removes the duplicate-driven spans; revisit if overlay-driven spans still read slow after the next screening |

### P2 — Typography placement & legibility (fixes §2.3) — ✅ SHIPPED 2026-07-04 (2.3 deferred)

| # | Change | Where | Status |
|---|---|---|---|
| 2.1 | **Lower-third anchor**: pull_quote/name_reveal/date_stamp overlays center at y≈0.63 (documentary lower-third, off the faces at frame center); section_mark/chapter_heading stay centered; block clamped to 6%/7% frame margins. `--overlay-anchor {auto,center,lower}`, Streamlit "Text position", notebooks' `OVERLAY_ANCHOR` | `typography_common.py`, `render.py`, `render_plan.py`, `streamlit_app.py`, notebooks | ✅ pixel-verified (0.638 / 0.513 / forced-center 0.523) |
| 2.2 | **Adaptive scrim**: `"auto"` (new default) samples the backdrop band the text occupies — mean luma > 185 → `band`, > 140 or std > 60 → `soft`, else `off`. Explicit off/soft/band still win; no backdrop → off | `typography_common.py` (`_auto_scrim_for_backdrop`), `render.py` | ✅ pixel-verified (bright→band α213, dark→off α0, busy→soft) |
| 2.3 | Saliency-aware nudge via the parallax depth map | `render.py` | ⏸ deferred — lower-third + adaptive scrim resolve the observed failures; revisit only if text still lands on faces |
| 2.4 | §15.4 caption sizes + inter-event gap | — | ✅ found already shipped in a prior session (title_sub 0.040, name_sub 0.028, GAP 0.15 s in `_write_captions`) |

### P3 — One film, one look (fixes §2.4, §2.5) — ✅ SHIPPED 2026-07-05 (3.4 deferred)

| # | Change | Where | Status |
|---|---|---|---|
| 3.1 | Default `--grade warm` | — | ✅ found already in place (render_plan default, Streamlit index 0, notebooks `GRADE="warm"`) |
| 3.2 | **Tonal normalization in conditioning**: Pexels-sourced winners get mild desaturation (0.82) + warm curve (R×1.045, B×0.925) + fine grain during `condition_assets.py`; authentic sources (photo bank, Wikimedia/Wikipedia, user files) untouched. `--tone {documentary,off}` (default documentary), idempotent via a `tone` marker in `conditioning` | `condition_assets.py`, `phase3.condition_review_dir(tone=…)` | ✅ unit-tested (R 60→75, B 180→154 on a cold-blue frame) |
| 3.3 | **Section parser**: `sections.json` sidecar (explicit override) → legacy template regexes → NEW short-isolated-line heuristic (< 80 chars between blanks, after the title). The production script now parses **opening + point_1..3 + closing** instead of opening/closing | `phase3/parser.py`, `parse_sections(script_path=…)` through phase3_run + orchestrators | ✅ verified on the real script + sidecar override test. Phase 1b LLM-emitted boundaries deferred (its script format already satisfies the heuristic) |
| 3.4 | `grade_map.json` per section | `render.py` | ⏸ deferred — unblocked by 3.3 now; needs per-clip grading design that respects the stream-copy concat invariant |

### P4 — Hygiene — ✅ SHIPPED 2026-07-05

| # | Change | Where | Status |
|---|---|---|---|
| 4.1 | Provenance labels: dossier hits log `review_dossier`, pin hits `pinned_portrait` (were both `user_upload`) | `sources/__init__.py`, `sources/base.py` | ✅ |
| 4.2 | Progress bar draws only on a TTY; redirected logs (the notebooks' `> render.log 2>&1`) get clean INFO lines only | `render_plan.py` | ✅ |
| 4.3 | Title card (Family B): gold accent rule now always draws (anchors the title against dead space) and centres on the *text region* (was frame-centred — misaligned with `--book-cover-align right`); block raised 0.55→0.52; new `--title-subtitle` / `TITLE_SUBTITLE` for an author/date sub-line | `typography_b.py`, `render.py` (`RenderConfig.title_subtitle`), CLI + notebooks | ✅ visually verified against the production cover |
| 4.4 | **Word-by-word reveal** (§7.9, opt-in `--word-reveal` / `WORD_REVEAL` / Streamlit checkbox): over-image pull quotes and name reveals extend word-group by word-group over min(1.6 s, 45% of the shot). Text is rendered complete every step and masked with a feathered per-line alpha ramp — pixel-identical stability (verified strictly monotone, lost=0). Cumulative PNGs stacked with timed FFmpeg overlays; encoder profile unchanged so concat-by-copy holds | `typography_common.py` (`reveal_upto`), `typography.py` (`render_overlay_steps`), `render.py` (`_overlay_steps_on_clip`) | ✅ tested end-to-end incl. a rendered clip |

### Sequencing rationale

P0 first because no amount of typography polish rescues a tricorn-hat
reenactor captioned as the Ottoman army — imagery credibility is the ceiling
on everything else. P1 second because it's cheap and the pacing win is
already paid for in the plan. P2/P3 are look-and-feel and can ride any
render. Every item keeps an existing knob or adds one, keeps defaults
working, and is testable in isolation (per the §0 working principle).

### What I recommend explicitly deferring

- WhisperX-on-Streamlit-Cloud (the upload-`word_timings.json` path works and
  was used in this run's toolchain).
- Further planner pacing pressure (user accepted 4.75 s; PHASE3.md says
  don't push).
- Re-adding LoC / Internet Archive — Wikipedia lead image + Path (C)
  dominate them for this content class.

---

## 5. Second screening — `final_cut (sample).mp4` (2026-07-05)

3-minute 720p sample rendered with the full P0–P4 stack. Frame-by-frame
review (60 samples at 3 s intervals).

### What the batches visibly delivered

- **The imagery is now period-true where it matters.** The colorized
  Ottoman officer at the tent, Harbiye Mektebi, the handwritten memoir
  close-ups (real Arabic handwriting — the 1815 Italian ledger is gone),
  the officer group portraits, the 1908 CUP proclamation photo, the
  Abdulhamid II portrait, the Young-Turk-era lithograph: the photo bank
  and free-source waterfall carry most of the film.
- **The word-by-word reveal works in production** — visible mid-build on
  the tent portrait and the memoir book; no jitter, correct RTL order.
- **Lower-third + adaptive scrim behave**: quote text sits off the
  subjects; the `الاستانة — ١٩٠١` date stamp gets a dark plate over the
  bright map and stays legible.
- **The custom illustrated map with the Mosul→Istanbul route arrows is
  the single best new moment in the film** — exactly the kind of asset
  the photo-bank pipeline was built to carry.
- **One unified warm/sepia look**; the title card's gold rule + centred
  block reads designed.

### What still breaks the spell (ranked)

1. **Four era/subject misses in the back half**, where the bank had no
   assignment and the waterfall found confident-but-wrong candidates:
   modern **Swedish royal guards** (~t 80–90 s), a modern **Russian
   officer with red-beret cadets** (~t 85–90 s), a **Meiji-era Japanese
   group photo** on the education beat (~t 150 s), and a **17th-century
   Baroque portrait** on the CUP-organizing beat (~t 160 s).  Notably
   the bank *contains* Young Turks photos — the assignment either left
   those shots empty or `--photo-bank-max-uses 1` spent the photo
   elsewhere.  The Baroque portrait shows an era-rubric blind spot:
   "old but the WRONG old" can pass a lenient judge.
2. **Long effective holds around typography-over-image runs persist**
   (the cavalry footage ≈15 s, the open memoir ≈12 s with their overlay
   cards) — this is exactly deferred item **P1.3** (rotate the overlay
   backdrop when a run exceeds ~10 s), now justified by evidence.
3. **Render-time fetches never pass the shot's visual type** —
   `_build_shot_asset` and prebuild call `fetch_for_shot(query, idx)`
   without `visual_type`, so the stricter portrait threshold
   (`MIN_KEEP_SUBJECT_PORTRAIT`) never actually applies.  Small bug,
   real consequence for misses like #1.
4. Minor: a star/flare blemish burned into the cavalry source asset
   (curation note); the B&W Shevket portrait letterboxes over black
   instead of the blurred fill.

### Recommended next batch (P5) — ✅ code items SHIPPED 2026-07-05

| # | Action | Status |
|---|---|---|
| 5.1 | Curate the four miss shots via the dossier; `--photo-bank-max-uses 2` and 3–4 more bank photos for the education / CUP beats | **user curation** — the main notebook now exposes `PHOTO_BANK_MAX_USES` (default 2) and passes it to prebuild |
| 5.2 | Era rubric hardened ("wrong century in EITHER direction scores 0; wrong region's institutions ≤ 1") + `visual_type` now passed through render **and** prebuild fetches, so the stricter portrait subject floor finally applies | ✅ |
| 5.3 | **P1.3 shipped**: past `--backdrop-rotate` seconds (default 10, 0 off) of one continuous over-image backdrop, the next overlay card switches to the source shot's next-ranked dossier alternate (near-duplicates skipped, camera restarts wide, per-shot alternate pointer so repeats keep rotating). Fail-open — no dossier/alternates → previous behaviour | ✅ tested (rotation, exhaustion, no-dossier) |
| 5.4 | **P3.4 shipped**: `--grade-map file.json` ({section_id: grade}); unmapped sections fall back to `--grade`. Applied at the final mux via timeline-enabled stages (`colortemperature`/`eq`/`hue` — deliberately not `curves`, whose timeline support is too new for Colab's system ffmpeg). Clip encoding and stream-copy concat untouched. Notebooks expose `GRADE_MAP` | ✅ verified in a real ffmpeg run (warm window stayed red-dominant, bw window fully desaturated) |
| 5.5 | **ElevenLabs TTS shipped** (`phase2/tts.py`): sentence-boundary chunking ≤ 4500 chars with `previous_text`/`next_text` prosody bridging, ffmpeg-concat part joining (byte-join fallback), ElevenLabs error details surfaced verbatim. Streamlit Phase 2 tab: backend enabled (no more "(soon)"), key/voice pre-filled from `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` secrets | ✅ tested (chunking lossless, join duration correct, mocked API incl. error path) |

## 6. Ledger updates suggested for PHASE3.md

- §15.5 (asset review loop) → **closed**: this render consumed the dossier
  end-to-end (override chain, pool rotation, conditioned files all observed
  in `output/ph3/render.log`).
- §7.5 (alignment) → note WhisperX now proven in production via Colab
  (652/652 words `source: whisperx`).
- §8 Path (C) → decision taken (this document): implement as P0.1.
