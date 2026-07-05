# Lamahat — Master Plan & Session Handoff

## Project Vision

Convert Arabic books (PDF) into high-impact, 3-to-5-minute video summaries —
long enough to deliver real value, short enough for modern attention spans.
The output is a fully automated MP4: Arabic TTS voice, relevant background
visuals with motion, and burned-in Arabic subtitles. Every phase is built to
run on Streamlit Community Cloud (1 GB RAM, no GPU).

Working repo: **abdoljh/Lamahat** · Streamlit Community Cloud deployment.
Runtime: **Python 3.12.13** (confirmed from Cloud logs — do NOT assume 3.14).

---

## Four-Phase Architecture

| Phase | Name | Goal | Status |
|-------|------|------|--------|
| 1a | PDF Preprocessing & OCR | PDF → strip margins → page images → Kraken OCR → normalised text | ✅ **Complete** |
| 1b | Chunking & Summarisation | Normalised text → semantic chunks → 625–850-word video script | ✅ **Complete** |
| 2 | Audio Synthesis (TTS) | Script → Arabic MP3 via gTTS (ElevenLabs next) | ✅ **Working** (gTTS) |
| 3 | Visual Generation | Script + audio → shot plan (Sonnet) → final MP4 with visuals, voice, subtitles | 🔧 **In Progress** (shot-based v2) |
| 4 | Workflow Integration | One-click pipeline: PDF → finished video | ✅ **Complete** (follows Phase 3) |

---

## Repo Structure

```
streamlit_app.py          # Streamlit entrypoint (Phases 1–3 UI)
phase3_run.py             # Phase 3 v2 CLI: dry-run / align / plan / full render
render_plan.py            # Render a saved shot plan JSON → MP4
prebuild_assets.py        # Build the review/ dossier (candidate images + pins)
condition_assets.py       # Normalise captured assets before render
audit_plan.py             # Quality audit of a saved shot plan
.streamlit/config.toml    # server.maxUploadSize=400 (bigger dossier .zip uploads)
fonts/                    # Amiri TTFs (incl. AmiriQuranColored for Family C)
resources/                # Sample inputs + the two Colab notebooks
                          #   _phase3_main.ipynb (total solution: align→plan→audit→
                          #     prebuild→condition→render) ·
                          #   _phase3_render_only.ipynb (render-only: revised review
                          #     dir → condition → render, no further API cost)
                          #   plus script, narration, bg_music, book_cover/, character/
phase1/
  __init__.py             # Exports Phase1Pipeline, Phase1aPipeline, Phase1Config, etc.
  pipeline.py             # Phase1aPipeline (8-step) + Phase1bPipeline + Phase1Pipeline
  core/
    header_footer.py      # Margin detection + strip_pdf() + detect_margins()
    page_export.py        # export_pages_as_images() + extract_footers_pdf()
    image_extract.py      # extract_images() — pixel-domain photo extraction
    kraken_engine.py      # Kraken OCR engine wrapper (Arabic, apt-20221130 model)
    ingestor.py           # PDF ingestion (PyMuPDF) — digital + scanned, RTL
    ocr_engine.py         # Tesseract / EasyOCR wrapper (legacy, not used in 1a)
    normalizer.py         # Arabic text normalisation (lam-alef, Farsi Yeh, noise)
    chunker.py            # Semantic chunking (~180 lines)
    diacritizer.py        # Mishkal / Farasa wrapper
    summarizer.py         # Hierarchical summarisation + script generation
    output_writer.py      # JSON + TXT serialisation
phase2/
  __init__.py
  tts.py                  # gTTS backend; ElevenLabs stub (NotImplementedError)
phase3/                   # Phase 3 v2 (shot-based). See phase3/PHASE3.md for depth.
  PHASE3.md               # deep architecture reference + session handoff
  __init__.py             # route orchestrators (generate_video_v2 /
                          #   build_total_solution / render_from_review /
                          #   slim_review_dir / zip_review_dir) + media helpers
  align.py                # WhisperX | Whisper | interpolation | load_word_timings
  plan.py                 # Sonnet shot planner + Shot dataclass + JSON I/O
  render.py               # plan → MP4: assets, motion, captions, grade, mux
  motion_parallax.py      # 2.5D depth parallax + fit-to-frame + camera continuity
  parser.py               # Arabic section regexes + duration estimator
  subtitler.py            # ASS subtitle helpers
  typography.py           # dispatcher; re-exports the public typography API
  typography_common.py    # shared tokens, font discovery, TypographySpec
  typography_a.py / _b.py / _c.py   # families A (editorial) / B (cinematic) / C (manuscript)
  sources/                # image-fetch waterfall (Wikimedia → Wikipedia lead
                          #   image → Pexels; LoC + IA removed — PHASE3.md §7.3)
    __init__.py           #   Fetcher + FetcherConfig (pinned_portrait, offline)
    base.py wikimedia.py wikipedia.py pexels.py  # loc.py / internet_archive.py unused
    user_upload.py book_extract.py cache.py vision.py  # vision rubric incl. era axis
    photo_bank.py         #   Path (C): curated bank → one-Sonnet-call shot assignment
    dedupe.py             #   dHash near-duplicate detection (adjacent-shot swap)
    decisions.py          #   dossier resolve + subject_is_character + pool
lightning-compat/         # Local shim: proxies lightning → pytorch-lightning==2.6.1
packages.txt              # Streamlit Cloud apt deps (ffmpeg, fonts-hosny-amiri, etc.)
requirements.txt          # Python deps (torch/lightning/kraken all active on Python 3.12)
requirements-colab.txt    # Colab-only extras (whisperx, openai-whisper, anthropic, …)
                          #   installed by the notebooks' bootstrap cell, which
                          #   git-clones THIS repo at a pinned branch (single
                          #   source of truth — see PHASE3.md §0 Colab workflow)
output/                   # Pipeline outputs; gitignored in production
samples/                  # Test PDFs (Al-Askari, preface, sample docs)
```

---

## Phase 1a — PDF Preprocessing & OCR ✅

### What it does (8 steps in `Phase1aPipeline.run()`)
1. **Strip headers/footers** — `header_footer.strip_pdf()` sets CropBox on each page
2. **Export page images** — `page_export.export_pages_as_images()` at configurable DPI
3. **Bundle into ZIP** — `_bundle_to_zips()` splits across multiple ZIPs if `zip_split_mb` exceeded
4. **Extract footers** — `page_export.extract_footers_pdf()` on the ORIGINAL PDF → labeled PDF + images ZIP
5. **Extract photographs** — `image_extract.extract_images()` → pixel-domain photo segmentation + captions ZIP
6. **Kraken OCR** — `kraken_engine.ocr_page()` on each exported page image
7. **Normalise Arabic text** — `ArabicTextNormalizer`
8. **Save output files** — corrected TXT + normalised TXT + structured JSON

### Four modes (matching OCR-me)
| Mode | Strip | OCR | Footers/Photos |
|------|-------|-----|----------------|
| `single_book` | ✓ | ✓ | optional |
| `raw_export` | ✗ | ✗ | ✗ |
| `batch` | ✓ | ✓ | optional (caller iterates PDFs) |
| `visual` | ✓ | ✓ | optional (per-page UI is Streamlit-only) |

### Phase1Config key fields
```python
mode: str = "single_book"          # see table above
strip_margins: bool = True
hf_dpi: int = 300                  # DPI for margin detection
export_dpi: int = 400              # DPI for page image export
include_footers: bool = True
include_photos: bool = False
zip_split_mb: float = 250.0        # 0 = no split
ocr_backend: str = "kraken"        # "kraken" | "none"
kraken_bidi: str = "auto"          # "auto" | "R" | "L" | "off"
kraken_threshold: float = 0.5      # NLBin binarization threshold
kraken_pad: int = 16
kraken_autocast: bool = False
kraken_text_direction: str = "horizontal-rl"
kraken_no_legacy_polygons: bool = False
```

### Phase1aResult key fields
```python
pages_zip_paths: list[Path]         # one or more ZIP parts
footers_pdf_path: Optional[Path]
footers_zip_path: Optional[Path]
n_footer_pages: int
photos_zip_path: Optional[Path]
n_photos: int
pages_zip_path: Optional[Path]     # compat property → pages_zip_paths[0]
```

### Kraken engine (`phase1/core/kraken_engine.py`) — critical design
The engine was completely rewritten to fix a crash in kraken 7.0.1.

**Key insight**: `blla.segment` needs the **binarized** image; `rpred.rpred` needs
the **original RGB** image. Passing binarized to rpred causes garbled output.

```python
def ocr_page(model, pil_img, *, threshold=0.5, ...):
    orig_rgb = pil_img.convert("RGB")          # → rpred (recognition)
    bw_img   = binarize_page(pil_img, threshold) # → blla.segment (layout)
    seg      = blla.segment(bw_img, text_direction=..., **_seg_extra)
    preds    = rpred.rpred(model, orig_rgb, seg, ...)  # NOT bw_img
```

**`_sig_params(fn)`** — runtime `inspect.signature` check used to guard every
optional kwarg before passing to kraken functions.  Kraken 7.0.x removed
`no_legacy_polygons` from `blla.segment` and `autocast` from `rpred.rpred`.
The helper avoids hard-coded parameter names that differ between versions:

```python
def _sig_params(fn) -> set:
    try:
        return set(inspect.signature(fn).parameters)
    except Exception:
        return set()

_seg_p = _sig_params(blla.segment)
if "autocast" in _seg_p:
    _seg_extra["autocast"] = autocast
if "no_legacy_polygons" in _seg_p and no_legacy_polygons:
    _seg_extra["no_legacy_polygons"] = True

_rpred_p   = _sig_params(rpred.rpred)
_bidi_kwarg = "bidi_reordering" if "bidi_reordering" in _rpred_p else "bidi_reorder"
if "autocast" in _rpred_p:
    _rpred_extra["autocast"] = autocast
```

**`binarize_page()`** — tries kraken's own `kbin.nlbin` first (most accurate),
falls back to scipy NLBin port, then simple global threshold.

**Pipeline call site** (`pipeline.py` step 6): passes the original image to
`ocr_page()` with `threshold=` kwarg; no pre-binarization in the caller.

### Requirements on Python 3.12 (confirmed)
- `torch>=2.4.0,<=2.10.0` — installed as transitive dep of kraken
- `lightning @ ./lightning-compat` — local shim (all PyPI lightning versions
  were quarantined 2026-04-30); proxies to `pytorch-lightning==2.6.1`
- `kraken==7.0.1` — uncommented in `requirements.txt`
- Streamlit Cloud uses **Python 3.12.13** (set in Advanced settings at first deploy)

### KrakenNotAvailableError
Raised by `load_model()` when `import kraken` fails (e.g., wrong Python version).
The sidebar in `streamlit_app.py` catches `_KRAKEN_AVAILABLE = False` and shows
a Python 3.12 warning. The pipeline catches it separately from generic exceptions
so the error message is passed through cleanly as a warning.

---

## Phase 1b — Chunking & Summarisation ✅

### What it does
1. Semantic chunking (default 1500 tokens, 200 overlap)
2. Hierarchical summarisation: Reader (Haiku, per chunk) → Consolidator (Haiku) → Scriptwriter (Sonnet) → Editor/Scorer (Haiku, up to 2 retries)
3. Outputs: `*_phase1.json`, `*_phase1.txt`, `*_phase1_raw.txt`, `book_script.txt`, `book_script_diacritized.txt`, `book_script_metadata.json`

### Key design decisions
- **Cost strategy**: Haiku for all bulk/scoring work; Sonnet only for the final script (~$0.05 per book).
- **Word-count gate**: 625–850 words. Scripts outside range trigger a targeted retry.
- **max_tokens = 3500** for Scriptwriter (Arabic ~4.2 tokens/word; 850 words ≈ 3570 tokens).
- **Diacritisation**: Mishkal applied only to the final script, never to raw OCR text.
- **No hallucinated names**: Scriptwriter is forbidden from inventing names not in the outline.
- **Hex-Placeholder Technique**: Used in `ingestor.py` to handle lam-alef ligature extraction from PDF spans without breaking RTL text ordering.

### Script structure (4 required sections)
1. Cinematic opening hook
2. Three thematic points with examples
3. Reflective closing
4. Formal book presentation (title + call to action)

### Validated on
- Al-Askari Memoirs (255-page scanned Arabic book, split into 2 PDFs)
- Score: 41/50 · 629 words · 0 retries

---

## Phase 2 — Audio Synthesis ✅ (partial)

### What works
- **gTTS** (`lang='ar'`): free, no API key, produces Arabic MP3 in seconds.
- Streamlit UI: choose script source (Phase 1 session or upload `.txt`), plain vs. diacritized variant, generate + download MP3.

### What is needed next
- **ElevenLabs** integration (Chaouki voice) for broadcast-quality Arabic TTS.
  - Stub exists in `phase2/tts.py` (`NotImplementedError`).
  - Needs: `ELEVENLABS_API_KEY` secret + voice ID in UI, then call ElevenLabs REST API.
  - Priority: implement after Phase 3 visual quality is stable.

---

## Phase 3 — Visual Generation 🔧 (shot-based v2 — THE CORNERSTONE)

The original section-based pipeline has been **replaced** by a **shot-based**
architecture. A *shot plan* is the source of truth: a list of 30–65
timestamped `Shot` dataclasses produced by one Claude Sonnet call. The
renderer executes the plan without making creative choices, so plans are
inspectable, diff-able and regeneratable. **`phase3/PHASE3.md` is the deep
reference** — read it before touching this subsystem (its §0 has the current
state; the rest is design history).

### Pipeline

```
Script + Audio ──► align()          ──► word_timings (WhisperX | Whisper | interp
                                          | uploaded word_timings.json)
                   build_shot_plan() ──► list[Shot]   (one Sonnet call, ~$0.10)
                   Fetcher           ──► imagery (Wikimedia → Pexels, Haiku
                                          vision-scored; cache / user-upload /
                                          book-extract / review dossier)
                   render_video()    ──► MP4 (aspect-aware motion, typography
                                          cards, captions, grade, music bed, mux)
```

### Two routes

- **Total solution** (`resources/_phase3_main.ipynb`): align confirmation →
  regenerate plan → audit (optional) → prebuild assets → save review dir →
  condition assets → render. Builds the main video skeleton (costs API).
- **Render-only** (`resources/_phase3_render_only.ipynb`): load a revised
  review dir → condition assets → render. Refines the skeleton at **no further
  API cost**. Both notebooks expose settings cells for flexibility.

The **Streamlit Phase 3 tab implements both routes**: a "Route" selector picks
Total solution (→ `phase3.build_total_solution`; slims + offers the dossier as a
downloadable `.zip`) or Rendering only (→ `phase3.render_from_review`, offline,
no API cost). Render-only's **Dossier source** is *this session's dossier (no
upload)* · *upload `.zip`* · *fetch `.zip` from URL* (server-side — the fix for
Cloud upload `ClientDisconnect`s). The sidebar exposes the full render-look set —
grade, typography family, book cover (+fit/align), character pool/pin, music
(+level −12 dB/duck), captions (+backplate/size/pos), title size **+ optional
colour**, text scrim, **typography-over-image (default on)**, fades, **2.5D
parallax (+backend/warp)** — plus per-run: resolution, **sharpen** (conditioning,
opt-in), **saved dossier candidates** (chosen/top-3/all), and **alignment**
(backend + `word_timings.json` upload). The render log is downloadable.

### Entry points

- **`phase3.generate_video_v2()`** — high-level orchestrator (align → plan →
  fetch → render) used by `phase3_run.py`'s one-shot render. Requires an
  Anthropic key (the planner is a Sonnet call). Accepts `book_cover` /
  `book_cover_fit`, `character_portrait` (pinned across every portrait shot via
  `FetcherConfig.pinned_portrait`), `music_path` / `music_gain_db`, and the
  caption/title/scrim look options.
- **`phase3.build_total_solution()`** — align (or `word_timings_path`) → plan →
  prebuild dossier → condition → render → `slim_review_dir` → return paths.
- **`phase3.render_from_review()`** — render-only from a dossier; `offline=True`
  by default (dossier images only; placeholder for uncovered shots). Per-shot
  resolution: `override → my_/user_-marked → pinned/pool → chosen_file`.
- **`condition_review_dir()` / `slim_review_dir()` / `zip_review_dir()` /
  `align.load_word_timings()`** — dossier conditioning, size-slimming, zipping,
  and loading precomputed alignment.
- **CLIs** (inspectable multi-step Colab/CLI workflow):

```bash
# Inspect section plan (no API calls)
python phase3_run.py --script resources/script/main_script.txt --dry-run

# Plan only: align + Sonnet shot planner → JSON (no render)
python phase3_run.py --script ... --audio ... --plan-only \
  --book-title "مذكرات جعفر العسكري" --character-name "Jafar al-Askari" \
  --save-plan output/shot_plan.json
python audit_plan.py output/shot_plan.json          # quality audit

# Optional human review: pre-fetch every candidate into a dossier, edit it,
# then condition the chosen assets
python prebuild_assets.py --plan ... --review-dir output/review/ \
  --character-portrait portrait.jpg
python condition_assets.py --review-dir output/review/

# Render a saved plan → MP4 (see render_plan.py --help for the full flag set:
# --grade, --typography-family {A,B,C}, --book-cover*, --parallax, --music, …)
python render_plan.py --plan output/shot_plan.json --audio ... \
  --output output/final_cut.mp4 --review-dir output/review/

# Or one-shot align→plan→render in a single command
python phase3_run.py --script ... --audio ... --output output/video.mp4 \
  --book-title "..." --character-name "..." --grade warm --typography-family A
```

API keys: `--anthropic-key` / `--pexels-key` flags, `ANTHROPIC_API_KEY` /
`PEXELS_API_KEY` env vars, or a `.env` file.

### Things not to break (full list in phase3/PHASE3.md §12 + §0)

- **Plan/render split.** Two responsibilities; mixing them was the v1 mistake.
- **`_validate_plan` invariants** (contiguous shots, per-visual hard caps,
  merge-adjacent-duplicates). The renderer assumes them. Don't lower the caps.
- **Arabic uses libraqm** (Pillow) / **libass** (ASS captions) — **never**
  FFmpeg `drawtext` (no bidi shaping). `Fontname: Amiri`.
- **Aspect-aware fit** in `render._png_to_clip`: landscapes cover-fill, but
  portrait/odd sources are *contained whole* over a blurred fill — don't revert
  to a blind cover-crop or figures lose their heads/feet.
- **Subject-matching** (`decisions.subject_is_character`): the character
  pool/pin only lands on portraits whose query names the lead. Don't apply it to
  every `portrait` shot.
- **Render-only is offline by default** — it must use the dossier's images
  (`override → my_/user_ → pinned/pool → chosen_file`); don't make it search.
- **Resize images to ≤ 800 px** before Haiku vision scoring (larger → API 400).
- **Stream-copy concat** works only because every shot clip shares encoder
  settings — don't vary a single shot's profile.
- **Vision scoring is fail-open**; don't flip to fail-closed.
- **`--character-name` in Latin** (for Wikimedia search + subject-matching).
  Title may be Arabic.
- **Typography:** `typography.py` is a dispatcher re-exporting a fixed public
  surface; families A/B/C live in sibling modules. Family C needs
  `AmiriQuranColored.ttf` + `embedded_color=True` for the red i-dots.

### Open work (phase3/PHASE3.md §15 / §7 + phase3/SCREENING_REVIEW.md)

- ~~**Source query quality** (§7.3)~~ / ~~**Path (C)** (§8)~~ — **shipped
  2026-07-03** (P0 batch, PHASE3.md §0): `sources/photo_bank.py` (curated
  bank → one Sonnet call → dossier chosen_file; auto-detected at
  `resources/photo_bank/`), Wikipedia lead-image source, era-fit vision
  axis (demotion tier), Pexels style-token stripping, era flags in the
  dossier.  ⚠ Wikipedia source needs live verification on first Colab run.
- ~~**P1 pacing**~~ / ~~**P2 typography**~~ — **shipped 2026-07-04**
  (PHASE3.md §0 P1/P2 batch): `sources/dedupe.py` adjacent
  near-duplicate swap (`--no-dedupe` to opt out), effective-holds audit
  (`audit_plan.py --review-dir`), lower-third overlay anchor
  (`--overlay-anchor`), adaptive text scrim (`--text-scrim auto` is the
  new default).  Deferred: P1.3 (overlay backdrop rotation), P2.3
  (saliency nudge) — revisit after the next screening.
- ~~**P3 look**~~ / ~~**P4 hygiene**~~ — **shipped 2026-07-05**
  (PHASE3.md §0 P3/P4 batch): documentary tone on Pexels winners in
  conditioning (`--tone off` to disable), section parser fixed
  (sidecar → regex → short-line heuristic; production script now
  opening + 3 points + closing), honest provenance labels, TTY-only
  progress bar, Family B title-card polish (+`--title-subtitle`),
  **word-by-word reveal** on over-image quotes (opt-in
  `--word-reveal` — screen before defaulting).
- **Color grade per-section variation** (the `--grade` knob exists;
  `grade_map.json` per section is now unblocked by the §7.2 parser fix;
  design must respect the stream-copy concat invariant).
- **ElevenLabs TTS** (Phase 2) — cleaner audio also helps alignment.
- **Better placeholders**: replace the Latin-query "TBD" card for un-sourced
  shots with a styled Arabic typography card (PHASE3.md §7.7).

---

## Phase 4 — Workflow Integration ✅

The Streamlit UI chains all phases in one session:

1. **Phase 1a** tab: Upload PDF → Configure mode/OCR/margins/footers/photos → Run → Download ZIPs, footer PDF, photos ZIP
2. **Phase 1b** tab: Run summarisation on Phase 1a output → Download script
3. **Phase 2** tab: Generate audio → Download MP3
4. **Phase 3** tab: Enter book title + character name → Generate video → Download MP4

Session state keys: `phase1a_result`, `phase1a_zip_parts`, `phase1a_footers_pdf`,
`phase1a_footers_zip`, `phase1a_photos_zip`, `phase1b_result`, `p3_video_bytes`,
`p3_thumb_bytes`. The Phase 3 tab calls `phase3.generate_video_v2()`.

Phase 4 is complete once Phase 3 produces broadcast-quality output.

---

## Immediate Next Steps (start here next session)

1. **End-to-end validation of Phase 1a on Streamlit Cloud**:
   - Run with Al-Askari Memoirs; confirm Kraken OCR completes without crash
   - Verify footer PDF and page images ZIP download correctly
   - Branch `claude/upgrade-phase1a-ocr-3usw0` must be deployed

2. **Exercise the Streamlit Phase 3 routes** (see phase3/PHASE3.md §0):
   - Total solution → downloads a slimmed review `.zip`; keep it in-session
   - Rendering only → reuse the in-session dossier (no upload), or the URL fetch
   - Review the MP4 for image relevance, portrait framing, caption timing, grade

3. **Exercise the P0 sourcing batch** (phase3/PHASE3.md §0, shipped
   2026-07-03): populate `resources/photo_bank/` with the curated photos,
   run prebuild on Colab, confirm the `photo_bank: N/59 image shots
   assigned` log line, verify the Wikipedia lead-image source returns
   candidates (⚠ not yet live-verified), and check the `⚠ ERA MISMATCH`
   flags in the dossier before rendering.

4. **Real alignment on Cloud**: compute `word_timings.json` off-Cloud (Colab /
   `phase3_run.py --align-only`) and upload it in the Total-solution *Alignment*
   expander — WhisperX won't fit in Cloud's ~1 GB RAM.

5. **Implement ElevenLabs TTS** (Phase 2): `phase2/tts.py` stub +
   `ELEVENLABS_API_KEY`; cleaner audio also improves alignment.

---

## Key Technical Constraints

| Constraint | Detail |
|-----------|--------|
| Streamlit Cloud RAM | 1 GB — keep FFmpeg work in subprocesses; no large in-memory buffers |
| No GPU | All ML inference via API; local tools CPU-only |
| Python version | **3.12.13** (set once in Advanced settings at first deploy) |
| Kraken / torch | Active on Python 3.12; `lightning-compat/` shim required (PyPI quarantined) |
| Arabic RTL in video | Use ASS + libass; **never** FFmpeg `drawtext` (no Arabic bidi) |
| Claude vision image size | **Always resize to ≤ 800 px wide** before sending — oversized → `400 Could not process image` |
| Arabic font for FFmpeg | `fonts-hosny-amiri` (Debian trixie) → font family name `Amiri` in ASS |
| Do NOT use | `fonts-noto-arabic` — does not exist in Debian trixie repos |
| Pexels key | Optional — app must work without it |
| Anthropic API key | Required for Phase 1b summarisation and Phase 3 keywords/vision scoring |

---

## Model & Cost Strategy

| Task | Model | Cost per book |
|------|-------|---------------|
| Reader per chunk | `claude-haiku-4-5-20251001` | ~$0.01 |
| Consolidator | `claude-haiku-4-5-20251001` | ~$0.001 |
| Scriptwriter | `claude-sonnet-4-6` | ~$0.04 |
| Editor/Scorer | `claude-haiku-4-5-20251001` | ~$0.002 |
| Shot planner (Phase 3, one call) | `claude-sonnet-4-6` | ~$0.10 |
| Image relevance vision scoring | `claude-haiku-4-5-20251001` vision | ~$0.05 (per ~100 candidates) |
| **Total (current, gTTS)** | | **~$0.25** |
| TTS (gTTS) | Free | $0 |
| TTS (ElevenLabs target) | Chaouki voice | ~$0.10–0.30 |

**Rule**: Haiku for every bulk, scoring, or classification task. Sonnet/Opus only for creative output (the final script).

---

## Development Conventions

### Arabic text handling
- Never apply diacritization to raw OCR output — only to the final approved script
- Always use `arabic_reshaper` + `python-bidi` when rendering Arabic in Pillow
- In ASS subtitles, `ScaledBorderAndShadow: yes` and `WrapStyle: 0` for correct RTL wrapping
- Use MSA Arabic only in generated scripts; reject dialect substitutions

### FFmpeg subprocess calls
- All FFmpeg calls via `subprocess.run([...], check=True)` — never `os.system()`
- Build filter graphs as Python list → `','.join(filters)` to avoid shell injection
- `probe_duration()` in `effects.py` uses `ffprobe -v quiet -print_format json -show_format`
- Ken Burns via `zoompan`; scale image to 2× output resolution first to avoid upscaling artefacts

### Streamlit patterns
- Phase outputs in `st.session_state` keyed by phase: `phase1a_result`, `p3_video_bytes`, etc.
- Progress callbacks: `on_progress(message: str, fraction: float)` passed into pipeline functions
- All file paths in session state are absolute paths

### Git workflow
- Active development branch: `claude/phase3-revision-update-kka9ee`
- Commit message format: `Phase N: <what changed>`
- Push to `origin claude/phase3-revision-update-kka9ee` after each logical unit of work

### Secrets / environment
- `ANTHROPIC_API_KEY` — required for Phases 1b and 3
- `PEXELS_API_KEY` — optional
- `ELEVENLABS_API_KEY` — stub ready in `phase2/tts.py`
- On Cloud: `st.secrets["KEY_NAME"]`; locally: `.env` file (never commit)
