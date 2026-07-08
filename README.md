# Lamahat — لمحات

> Arabic books (PDF) → cinematic 3–5 minute video summaries, fully
> automated: Arabic narration, period-true imagery with motion, and
> native Arabic typography.

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://bk2video.streamlit.app)

---

## How it works

| Phase | What it does | Status |
|-------|--------------|--------|
| **1a** | PDF → strip margins → page images → Kraken OCR → normalised Arabic text | ✅ |
| **1b** | Text → semantic chunks → hierarchical summarisation → 625–850-word video script (Claude Haiku + Sonnet) | ✅ |
| **2**  | Script → Arabic MP3 — gTTS (free) or ElevenLabs (premium voices, chunked + prosody-bridged) | ✅ |
| **3**  | Script + audio → **shot plan** (one Claude Sonnet call) → imagery (curated photo bank → Wikimedia → Wikipedia → Pexels, vision-scored) → MP4 with 2.5D parallax motion, Arabic typography overlays, color grade, music bed | 🔧 mature, in tuning |
| **4**  | One-click pipeline in Streamlit chaining all phases | ✅ |

Phase 3's design principle is **plan-then-render**: a JSON shot plan is
the source of truth, and a human-editable **review dossier** of every
image candidate sits between planning and rendering — you veto or swap
any picture before FFmpeg runs, and re-render at no further API cost.

Deep documentation:

- **`CLAUDE.md`** — master plan, per-phase reference, session handoff.
- **`phase3/PHASE3.md`** — Phase 3 architecture (§0 = current state).
- **`phase3/SCREENING_REVIEW.md`** — screening critiques and the
  shipped improvement batches (P0–P5).

---

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub and create an app at
   [share.streamlit.io](https://share.streamlit.io):
   - **Main file path:** `streamlit_app.py`
   - **Python version** (Advanced settings): **3.12**
2. Paste secrets in **Advanced settings**:

```toml
ANTHROPIC_API_KEY   = "sk-ant-…"   # required: Phase 1b summariser + Phase 3 planner/vision
PEXELS_API_KEY      = ""           # optional: stock b-roll fallback
ELEVENLABS_API_KEY  = ""           # optional: premium Arabic TTS
ELEVENLABS_VOICE_ID = ""           # optional: e.g. the Chaouki voice
```

3. Deploy. Runtime constraints (1 GB RAM, no GPU) are respected by
   design — heavy alignment (WhisperX) runs on Colab instead, and its
   `word_timings.json` is uploaded in the Phase 3 tab.

---

## Colab workflow (heavy lifting: alignment, planning, rendering)

The two notebooks in `resources/` clone **this repo** at a pinned
branch — one source of truth, no file copying:

- **`resources/_phase3_main.ipynb`** — total solution: align (WhisperX)
  → plan (Sonnet) → audit → prebuild the review dossier → condition →
  render.
- **`resources/_phase3_render_only.ipynb`** — re-render a revised
  dossier at **zero** further API cost.

Colab-only dependencies live in `requirements-colab.txt`; Streamlit
Cloud uses `requirements.txt` untouched.

### Resources conventions (auto-discovered)

```
resources/
├── script/            # Phase 1b script (main_script.txt)
├── audio/             # narration.mp3 + bg_music.mp3
├── book_cover/        # cover pool for the title card (--book-cover-pick)
├── character/         # lead-character portrait pool (rotated across portrait shots)
└── photo_bank/        # YOUR curated photos — captioned once (Haiku), assigned
                       # to shots by ONE Sonnet call, veto-able in the dossier
```

---

## Phase 3 CLI quick reference

```bash
# Plan (one Sonnet call, ~$0.10)
python phase3_run.py --script resources/script/main_script.txt \
    --audio resources/audio/narration.mp3 --plan-only \
    --book-title "مذكرات جعفر العسكري" --character-name "Jafar al-Askari" \
    --save-plan output/plan.json

# Audit before you render (pacing, era flags, effective holds)
python audit_plan.py output/plan.json --review-dir output/review

# Build the human-editable dossier of image candidates
python prebuild_assets.py --plan output/plan.json --review-dir output/review \
    --book-title "…" --character-name "…" --photo-bank resources/photo_bank

# Condition assets (resolution, aspect, documentary tone on stock)
python condition_assets.py --review-dir output/review

# Render (see --help for the full look set: --grade, --grade-map,
# --typography-family, --word-reveal, --backdrop-rotate, --parallax, …)
python render_plan.py --plan output/plan.json \
    --audio resources/audio/narration.mp3 \
    --review-dir output/review --output output/final_cut.mp4
```

API keys come from flags, environment variables, or a `.env` file.

---

## Local development

```bash
git clone https://github.com/abdoljh/Lamahat
cd Lamahat
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Python **3.12** (matches Streamlit Cloud). `ffmpeg` must be on PATH for
Phase 3 rendering (`packages.txt` provides it on Cloud; Colab ships it).
