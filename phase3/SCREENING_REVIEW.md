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

### P3 — One film, one look (fixes §2.4, §2.5)

| # | Change | Where | Effort |
|---|---|---|---|
| 3.1 | Default `--grade warm` for the biography genre (knob already exists — this is a default flip + notebook/Streamlit default) | `render_plan.py`, UI | XS |
| 3.2 | **Tonal normalization in conditioning**: during `condition_assets.py`, pull modern-stock winners toward a target documentary palette (mild desaturation + warm curve + fine grain). Book/Wikimedia photos pass through untouched | `condition_assets.py` | M |
| 3.3 | Fix the section parser via the Phase 1b **sidecar JSON** (§7.2 option 2): Phase 1b already knows its 5 sections; emit `sections.json` next to the script; parser prefers it, regex stays as fallback | `phase1/core/summarizer.py`, `phase3/parser.py` | S |
| 3.4 | `grade_map.json` per section (unblocked by 3.3) | `render.py` | S, stretch |

### P4 — Hygiene

| # | Change | Where |
|---|---|---|
| 4.1 | Correct provenance labels: `source="review_dossier"` / `"pinned_portrait"` / `"portrait_pool"` instead of `user_upload` | `sources/__init__.py:184–211`, `sources/decisions.py` |
| 4.2 | Split render.log (INFO stream) from the tty progress bar | `render_plan.py` |
| 4.3 | Title card: raise title block, add author/subtitle line + gold accent rule (Family B) | `typography_b.py` |
| 4.4 | Word-by-word reveal on `pull_quote`/`name_reveal` (§7.9) — biggest perceived-production-value polish once P0–P2 land | `render.py` |

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

## 5. Ledger updates suggested for PHASE3.md

- §15.5 (asset review loop) → **closed**: this render consumed the dossier
  end-to-end (override chain, pool rotation, conditioned files all observed
  in `output/ph3/render.log`).
- §7.5 (alignment) → note WhisperX now proven in production via Colab
  (652/652 words `source: whisperx`).
- §8 Path (C) → decision taken (this document): implement as P0.1.
