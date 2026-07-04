"""
Arabic Book Brief Engine — Phase 1
Streamlit Community Cloud entrypoint.

Repository root is the working directory on Community Cloud, so:
  • This file lives at repo root  →  streamlit run streamlit_app.py
  • The phase1 package lives at  →  phase1/
  • Config lives at              →  .streamlit/config.toml
  • Secrets injected via         →  st.secrets  (never committed)
"""

import html
import io
import json
import logging
import sys
import tempfile
from pathlib import Path

import streamlit as st

# ── Phase 1 package is at ./phase1 relative to repo root ──────────────── #
sys.path.insert(0, str(Path(__file__).parent))
from phase1 import (  # noqa: E402
    Phase1aPipeline, Phase1bPipeline, Phase1Config, Phase1aResult,
)
from phase2 import synthesize as tts_synthesize  # noqa: E402


def _secret(key: str, default: str = "") -> str:
    """``st.secrets.get()`` that never raises.

    Streamlit raises ``StreamlitSecretNotFoundError`` from ``st.secrets`` when
    no ``secrets.toml`` exists at all (not just when the key is missing).  That
    exception would halt the entire sidebar render mid-way, hiding every widget
    below it.  This wrapper falls back to ``default`` on any failure.
    """
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

# ── Logging ───────────────────────────────────────────────────────────── #
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

# ── Page config ───────────────────────────────────────────────────────── #
st.set_page_config(
    page_title="Arabic Book Brief — Phase 1",
    page_icon="📖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────── #
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,400&family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500&display=swap');

html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
h1, h2, h3 { font-family: 'Playfair Display', serif !important; }

.app-header {
    background: #0e0e0e; color: #f5f0e8;
    padding: 2rem 2.5rem 1.6rem; border-radius: 8px;
    margin-bottom: 2rem; position: relative; overflow: hidden;
}
.app-header::after {
    content: '📖'; position: absolute; right: 2rem; top: 50%;
    transform: translateY(-50%); font-size: 5rem; opacity: .07;
}
.app-header h1 { color: #f5f0e8 !important; margin: 0; font-size: 2rem; }
.app-header .sub { color: #b0a898; font-size: 0.85rem; margin-top: 0.4rem; }
.app-header .eyebrow {
    font-family: 'DM Mono', monospace; font-size: 0.65rem;
    letter-spacing: .18em; text-transform: uppercase;
    color: #c9a84c; margin-bottom: 0.5rem;
}
.badge {
    display: inline-block; font-family: 'DM Mono', monospace;
    font-size: 0.6rem; letter-spacing: .1em; text-transform: uppercase;
    padding: 3px 10px; border-radius: 2px; border: 1px solid;
    margin-right: 6px; margin-top: 8px;
}
.b-gold { border-color: #c9a84c; color: #c9a84c; }
.b-teal { border-color: #4aadad; color: #4aadad; }
.b-rust { border-color: #d97452; color: #d97452; }
.metric-row { display: flex; gap: 1rem; margin: 1rem 0; flex-wrap: wrap; }
.metric-card {
    flex: 1; min-width: 120px; background: white;
    border: 1px solid #e0dbd0; border-top: 3px solid #c9a84c;
    border-radius: 4px; padding: 1rem 1.2rem;
    box-shadow: 3px 3px 0 #e8dfcc;
}
.metric-card .val {
    font-family: 'Playfair Display', serif; font-size: 2rem;
    font-weight: 700; color: #0e0e0e; line-height: 1;
}
.metric-card .lbl {
    font-family: 'DM Mono', monospace; font-size: 0.65rem;
    letter-spacing: .12em; text-transform: uppercase;
    color: #7a7060; margin-top: 4px;
}
.metric-card.teal  { border-top-color: #1e6b6b; }
.metric-card.rust  { border-top-color: #b94f2a; }
.metric-card.purple{ border-top-color: #7c5cbf; }
.chunk-card {
    background: #fefcf8; border: 1px solid #e0dbd0;
    border-left: 4px solid #c9a84c; border-radius: 0 4px 4px 0;
    padding: 1rem 1.2rem; margin-bottom: 0.8rem;
    direction: rtl; text-align: right;
    font-size: 0.9rem; line-height: 1.8;
    color: #1a1a1a;
}
.chunk-meta {
    font-family: 'DM Mono', monospace; font-size: 0.6rem;
    letter-spacing: .1em; text-transform: uppercase;
    color: #7a7060; direction: ltr; text-align: left; margin-bottom: 0.4rem;
}
.chunk-card.scanned { border-left-color: #1e6b6b; }
.warn-card {
    background: #fff7ec; border-left: 4px solid #c9a84c;
    border-radius: 0 4px 4px 0; padding: 0.8rem 1rem;
    margin: 0.5rem 0; font-size: 0.85rem; color: #5a3d00;
}
.step-log {
    font-family: 'DM Mono', monospace; font-size: 0.75rem;
    background: #0e0e0e; color: #c8c0b0; padding: 1rem 1.2rem;
    border-radius: 4px; line-height: 1.8;
    max-height: 220px; overflow-y: auto;
}
.step-log .done  { color: #4aadad; }
.step-log .active{ color: #f0d98a; }
section[data-testid="stSidebar"] { background: #0e0e0e !important; }
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stMarkdown p,
section[data-testid="stSidebar"] span { color: #c8c0b0 !important; }
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 { color: #f0d98a !important; }
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────── #
st.markdown("""
<div class="app-header">
  <div class="eyebrow">Arabic Book Brief Engine · Phase 1a + 1b</div>
  <h1>Extraction, Normalisation &amp; Script</h1>
  <div class="sub">
    <b>Phase 1a</b> — Strip margins · Export page images · Kraken OCR · Normalise<br>
    <b>Phase 1b</b> — Chunk · Summarise · Generate Arabic video script
  </div>
  <div>
    <span class="badge b-gold">Header/Footer Stripping</span>
    <span class="badge b-teal">Kraken Offline OCR</span>
    <span class="badge b-rust">Semantic Chunking</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────── #
with st.sidebar:
    st.markdown("### ⚙️ Configuration")

    st.markdown("#### Phase 1a — PDF Preprocessing")

    _p1a_mode_labels = {
        "Single Book (strip + export + OCR)": "single_book",
        "Raw Export (no stripping, no OCR)":  "raw_export",
    }
    _p1a_mode_label = st.selectbox(
        "Mode",
        list(_p1a_mode_labels.keys()),
        index=0,
        help=(
            "**Single Book** — auto-detect & strip headers/footers, export clean page "
            "images, optionally extract footers and photographs, optionally run Kraken OCR.\n\n"
            "**Raw Export** — export original pages as colour images with no header/footer "
            "removal. Useful for books that have no running titles or page numbers."
        ),
    )
    p1a_mode = _p1a_mode_labels[_p1a_mode_label]

    strip_margins = True
    include_footers = False
    include_photos  = False
    if p1a_mode == "single_book":
        strip_margins = st.toggle(
            "Strip headers/footers",
            value=True,
            help=(
                "Automatically detect and remove running headers, footers, and footnote "
                "separators before exporting page images. Uses ink-density analysis."
            ),
        )
        include_footers = st.toggle(
            "Extract footnotes",
            value=True,
            help=(
                "Detect footnote regions on each page and export them as a labeled "
                "PDF and a separate image ZIP. Useful for preserving citation data."
            ),
        )
        include_photos = st.toggle(
            "Extract photographs",
            value=False,
            help=(
                "Detect photographic regions (and their captions) and save each as a "
                "separate PNG in a ZIP. Uses pixel-domain dark-region segmentation — "
                "works even when the PDF has no embedded image objects."
            ),
        )

    zip_split_mb = float(st.number_input(
        "ZIP split size (MB)",
        min_value=50, max_value=1000, value=250, step=50,
        help="Split the pages ZIP into parts no larger than this. 250 MB fits most tools.",
    ))

    ocr_dpi = st.slider("Scan DPI", 150, 600, 400, step=50)

    # OCR backend — only shown for Single Book mode
    ocr_backend      = "none"
    kraken_bidi      = "auto"
    kraken_threshold = 0.5
    kraken_pad       = 16
    if p1a_mode == "single_book":
        try:
            from phase1.core.kraken_engine import _KRAKEN_AVAILABLE as _kraken_ok
        except Exception:
            _kraken_ok = False

        if _kraken_ok:
            _ocr_backend_options = {
                "Kraken (offline, Arabic model)": "kraken",
                "None (export images only)": "none",
            }
            _ocr_backend_default = 0
        else:
            _ocr_backend_options = {"None (export images only)": "none"}
            _ocr_backend_default = 0
            st.warning(
                "⚠️ Kraken OCR unavailable on this Python version. "
                "Redeploy with **Python 3.12** and uncomment "
                "`torch`/`lightning`/`kraken` in `requirements.txt` to enable it.",
                icon="🐍",
            )

        _ocr_backend_label = st.selectbox(
            "OCR Backend",
            list(_ocr_backend_options.keys()),
            index=_ocr_backend_default,
            help=(
                "**Kraken** — offline Arabic OCR using the OpenITI apt-20221130 model.\n\n"
                "**None** — export clean page images only. Download the ZIP, run OCR "
                "externally, then upload the text to Phase 1b."
            ),
        )
        ocr_backend = _ocr_backend_options[_ocr_backend_label]

        if ocr_backend == "kraken":
            _bidi_map = {
                "Auto (let kraken decide)": "auto",
                "Force RTL": "R",
                "Force LTR": "L",
                "Off (raw display order)": "off",
            }
            kraken_bidi      = _bidi_map[st.selectbox(
                "Bidi reordering", list(_bidi_map.keys()), index=0,
                help="Controls how Kraken reorders bidirectional text. Auto is correct for Arabic.",
            )]
            kraken_threshold = st.slider(
                "Binarization threshold", 1, 99, 50,
                help="Higher = darker pixels counted as ink. 50 is a good default.",
            ) / 100.0
            kraken_pad = st.slider(
                "Line padding (px)", 0, 64, 16, step=4,
                help="Pixels of padding added around each detected text line.",
            )

    st.markdown("#### Chunking")
    max_tokens     = st.slider("Max Tokens / Chunk", 500, 3000, 1500, step=100)
    overlap_tokens = st.slider("Overlap Tokens",       0,  500,  200, step=50)

    st.markdown("#### Script Generation")
    anthropic_key = st.text_input(
        "Anthropic API Key",
        type="password",
        value=_secret("ANTHROPIC_API_KEY"),
        help="Required for script generation. Leave blank to extract text only.",
    )
    script_genre = st.selectbox(
        "Book Genre",
        ["non-fiction", "history", "biography", "novel",
         "philosophy", "science", "religion"],
        index=0,
        help="Affects the tone of the generated script.",
    )
    book_author = st.text_input(
        "Author / Editor / Translator",
        placeholder="e.g. تحقيق وتقديم نجدة فتحي صفوة",
        help="Injected verbatim into the formal book-presentation section of the script.",
    )
    book_pages = st.number_input(
        "Total Pages",
        min_value=0,
        value=0,
        step=1,
        help="Actual page count of the full book (0 = omit from script).",
    )
    book_structure = st.text_input(
        "Book Structure",
        placeholder="e.g. مقدمة و١٦ فصلاً وملاحق",
        help="Brief Arabic description of chapters / sections / appendices.",
    )
    scriptwriter_model = st.selectbox(
        "Scriptwriter model",
        ["claude-haiku-4-5-20251001", "claude-sonnet-4-20250514"],
        index=0,
        help=(
            "Model used for the creative Scriptwriter step only. "
            "Reader, Consolidator, and Editor always use Haiku.\n\n"
            "**Haiku** — default, lowest cost (~$0.001 per script).\n\n"
            "**Sonnet** — higher quality prose, ~40× more expensive (~$0.04 per script)."
        ),
    )
    diacritize_script = st.toggle(
        "Diacritise script (Mishkal)",
        value=True,
        help=(
            "Apply Mishkal diacritisation to the final script and save "
            "*_script_diacritized.txt. Turn off to skip diacritisation and "
            "save a few seconds per run."
        ),
    )

    st.markdown("---")
    st.markdown("#### 🎙 Phase 2: TTS")
    tts_backend = st.radio(
        "TTS Backend",
        ["gTTS (free)", "ElevenLabs (soon)"],
        index=0,
        help=(
            "**gTTS** — Google TTS, free, no API key. Use for development.\n\n"
            "**ElevenLabs** — Premium Arabic voices (e.g. Chaouki). Coming soon."
        ),
    )
    el_api_key  = ""
    el_voice_id = ""
    if tts_backend == "ElevenLabs (soon)":
        el_api_key  = st.text_input("ElevenLabs API Key", type="password", key="el_key")
        el_voice_id = st.text_input("Voice ID", placeholder="e.g. Chaouki voice ID", key="el_voice")

    st.markdown("---")
    st.markdown("#### 🎬 Phase 3: Visuals")
    pexels_api_key = st.text_input(
        "Pexels API Key",
        type="password",
        key="p3_pexels_key",
        help=(
            "Free API key from pexels.com/api. "
            "Used as fallback when Wikimedia has no images for a section. "
            "Leave blank to use Wikimedia only."
        ),
    )
    p3_color_grade = st.selectbox(
        "Color Grade",
        ["warm", "neutral", "cool", "bw"],
        index=0,
        key="p3_color_grade",
        help="warm — teal-shadow / orange-highlight cinematic · neutral — gentle "
             "contrast · cool — editorial blue · bw — desaturated",
    )
    p3_typography_family = st.selectbox(
        "Typography family",
        ["A", "B", "C"],
        index=0,
        key="p3_typography_family",
        help="A — Aljazeera-editorial cream/charcoal (default) · "
             "B — Netflix-doc cinematic dark gradient · "
             "C — Manuscript sepia + ornament",
    )

    # ── Visual assets: book cover · character portrait · music bed ───── #
    _P3_RES = Path(__file__).resolve().parent / "resources"
    _P3_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
    if "p3_asset_dir" not in st.session_state:
        st.session_state["p3_asset_dir"] = tempfile.mkdtemp(prefix="bk2v_p3_assets_")
    _p3_asset_dir = Path(st.session_state["p3_asset_dir"])

    def _p3_pool(subdir: str, exts: tuple[str, ...]) -> list[Path]:
        d = _P3_RES / subdir
        if not d.is_dir():
            return []
        return sorted(
            [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in exts],
            key=lambda x: x.name.lower(),
        )

    def _p3_save_upload(up, name: str) -> Path:
        dest = _p3_asset_dir / name
        dest.write_bytes(up.getvalue())
        return dest

    st.markdown("**Visual assets**")

    # Book cover → title card
    _p3_covers = _p3_pool("book_cover", _P3_IMG_EXTS)
    _p3_cover_choice = st.selectbox(
        "Book cover",
        ["None"] + [f.name for f in _p3_covers] + ["Upload…"],
        index=(1 if _p3_covers else 0),
        key="p3_cover_choice",
        help="Shown full-frame on the title card with a gold title overlay. "
             "Bundled covers live in resources/book_cover/.",
    )
    p3_book_cover_path = None
    if _p3_cover_choice == "Upload…":
        _p3_cu = st.file_uploader("Upload cover image",
                                  type=["jpg", "jpeg", "png", "webp"],
                                  key="p3_cover_up")
        if _p3_cu:
            p3_book_cover_path = _p3_save_upload(_p3_cu, "cover_" + _p3_cu.name)
    elif _p3_cover_choice != "None":
        p3_book_cover_path = _P3_RES / "book_cover" / _p3_cover_choice
    p3_book_cover_fit = (
        st.selectbox(
            "Cover fit", ["contain", "fill", "blur_pad"], index=0,
            key="p3_cover_fit",
            help="contain — letterbox, keeps the whole cover (best for clean "
                 "covers) · fill — scale-and-crop · blur_pad — blurred backdrop.",
        ) if p3_book_cover_path else "contain"
    )
    p3_book_cover_align = (
        st.selectbox(
            "Cover align", ["center", "left", "right"], index=0,
            key="p3_cover_align",
            help="Horizontal placement when the fit leaves spare space "
                 "(contain / blur_pad). The gold title shifts to the opposite "
                 "side automatically.",
        ) if p3_book_cover_path else "center"
    )

    # Character images → the pool rotates across every portrait shot
    _p3_ports = _p3_pool("character", _P3_IMG_EXTS)
    _P3_POOL_OPT = f"Pool — rotate all {len(_p3_ports)} images (recommended)"
    _p3_port_choice = st.selectbox(
        "Character images",
        [_P3_POOL_OPT] + [f.name for f in _p3_ports] + ["Pin one (upload)"],
        index=0,
        key="p3_port_choice",
        help="The pool in resources/character/ is rotated across the portrait "
             "shots so the subject is shown through varied authentic photos "
             "(not stock faces). Pick a single file to pin just that one, or "
             "upload your own. Total solution always prefers the pool when it "
             "has images.",
    )
    p3_character_path = None
    if _p3_port_choice == "Pin one (upload)":
        _p3_pu = st.file_uploader("Upload portrait image",
                                  type=["jpg", "jpeg", "png", "webp"],
                                  key="p3_port_up")
        if _p3_pu:
            p3_character_path = _p3_save_upload(_p3_pu, "portrait_" + _p3_pu.name)
    elif _p3_port_choice != _P3_POOL_OPT:
        p3_character_path = _P3_RES / "character" / _p3_port_choice

    # Background music bed → mixed under the narration
    _p3_has_music = (_P3_RES / "audio" / "bg_music.mp3").exists()
    _p3_music_choice = st.selectbox(
        "Background music",
        ["None"] + (["Sample bed (bg_music.mp3)"] if _p3_has_music else [])
        + ["Upload…"],
        index=(1 if _p3_has_music else 0),
        key="p3_music_choice",
        help="Mixed UNDER the narration: looped to length, side-chain ducked, "
             "and faded in/out. The swell lives in the gaps (intro, breaths, "
             "outro). resources/audio/bg_music.mp3 is the bundled bed.",
    )
    p3_music_path = None
    if _p3_music_choice == "Upload…":
        _p3_mu = st.file_uploader("Upload music file",
                                  type=["mp3", "m4a", "wav", "ogg"],
                                  key="p3_music_up")
        if _p3_mu:
            p3_music_path = _p3_save_upload(_p3_mu, "music_" + _p3_mu.name)
    elif _p3_music_choice.startswith("Sample"):
        p3_music_path = _P3_RES / "audio" / "bg_music.mp3"
    p3_music_gain = (
        st.slider("Music level (dB)", -30.0, -6.0, -12.0, 1.0,
                  key="p3_music_gain",
                  help="Bed level vs full scale. Lower = quieter under the voice.")
        if p3_music_path else -12.0
    )
    p3_music_duck = st.checkbox(
        "Duck music under narration", value=True, key="p3_music_duck",
        help="Side-chain compress the bed so it dips when the voice speaks.",
    ) if p3_music_path else True

    st.markdown("**Render options**")
    p3_add_captions = st.checkbox(
        "Burn Arabic captions", value=False, key="p3_add_captions",
        help="Timed narration captions at the bottom. Off by default — on-screen "
             "Arabic already comes from the typography cards.",
    )
    p3_caption_backplate = st.selectbox(
        "Caption backplate", ["subtle", "off", "solid"], index=0,
        key="p3_caption_backplate",
        help="Charcoal bar behind captions for legibility. off = outline only · "
             "subtle = 55%% · solid = 80%% (bright footage). No effect when "
             "captions are off.",
    )
    p3_caption_size = st.slider(
        "Caption size", 0.7, 1.6, 1.0, 0.1, key="p3_caption_size",
        help="Caption text size multiplier (only when captions are on).",
    )
    p3_caption_pos = st.slider(
        "Caption position (from bottom)", 0.03, 0.25, 0.06, 0.01,
        key="p3_caption_pos",
        help="Vertical position as a fraction of frame height. Larger = higher.",
    )
    p3_title_size = st.slider(
        "Title size", 0.7, 1.6, 1.0, 0.1, key="p3_title_size",
        help="Main title-card text size multiplier.",
    )
    p3_use_title_color = st.checkbox(
        "Custom title color", value=False, key="p3_use_title_color",
        help="Override the typography family's title accent (Family A: aged gold).",
    )
    p3_title_color_hex = st.color_picker(
        "Title color", "#C9A84C", key="p3_title_color",
        disabled=not p3_use_title_color,
    )
    p3_typography_over_image = st.checkbox(
        "Typography over image", value=True, key="p3_typo_over_image",
        help="Composite section/quote text over the most recent photo instead "
             "of a flat card — cuts the slideshow feel. title_card keeps its cover.",
    )
    p3_text_scrim = st.selectbox(
        "Text scrim", ["auto", "off", "soft", "band"], index=0,
        key="p3_text_scrim",
        help="Readability plate behind over-image text. auto = plate only "
             "when the frame under the text is bright/busy · off = always "
             "transparent · soft = light veil · band = strong dark band. "
             "Needs ‘Typography over image’.",
    )
    p3_overlay_anchor = st.selectbox(
        "Text position", ["auto", "center", "lower"], index=0,
        key="p3_overlay_anchor",
        help="Vertical anchor for over-image text. auto = quotes/names/dates "
             "sit lower-third (off faces), section marks stay centered.",
    )
    p3_fades = st.checkbox(
        "Cinematic fades", value=True, key="p3_fades",
        help="Black open/close fades and dip-through-black section breaths.",
    )

    # 2.5D parallax (depth-based motion). Off by default — CPU-heavy; the
    # depthanything backend needs torch+GPU, so 'classical' is the Cloud-safe
    # choice when this is enabled.
    p3_parallax = st.checkbox(
        "2.5D parallax (slow)", value=False, key="p3_parallax",
        help="Depth-based motion (foreground moves more than background) "
             "instead of flat zoom. Heavier per shot; on Streamlit Cloud use "
             "the 'classical' backend.",
    )
    if p3_parallax:
        p3_parallax_backend = st.selectbox(
            "Parallax backend", ["classical", "depthanything"], index=0,
            key="p3_parallax_backend",
            help="classical = dependency-free CPU (Cloud-safe). "
                 "depthanything = better depth but needs transformers+torch+GPU.",
        )
        p3_parallax_warp = st.selectbox(
            "Parallax warp", ["auto", "backward", "inpaint"], index=0,
            key="p3_parallax_warp",
            help="auto = per-visual default · backward = fast soft edges · "
                 "inpaint = clean disocclusions (~2× cost).",
        )
    else:
        p3_parallax_backend, p3_parallax_warp = "classical", "auto"

    st.markdown("---")
    with st.expander("🔬 Diagnostics", expanded=False):
        try:
            from PIL.features import check as _pil_check
            _raqm = _pil_check("raqm")
        except Exception as _e:
            _raqm = f"error: {_e}"
        st.markdown(
            f"**libraqm** (Arabic shaping in Pillow): "
            f"{'✅ available' if _raqm is True else ('❌ not available' if _raqm is False else _raqm)}"
        )
        try:
            import PIL
            st.markdown(f"**Pillow** version: `{PIL.__version__}`")
        except Exception:
            pass

    st.markdown(
        "<span style='font-family:DM Mono,monospace;font-size:0.6rem;"
        "color:#6b6355;letter-spacing:.1em'>ARABIC BOOK BRIEF ENGINE v1.0</span>",
        unsafe_allow_html=True,
    )

# ════════════════════════════════════════════════════════════════════════ #
#  Phase 1a — PDF Preprocessing & OCR                                     #
# ════════════════════════════════════════════════════════════════════════ #
st.markdown("""
<div class="app-header" style="margin-top:0">
  <div class="eyebrow">Phase 1a</div>
  <h1>PDF Preprocessing &amp; OCR</h1>
  <div class="sub">
    Strip headers/footers · Export clean page images · Extract footers &amp; photographs · Kraken Arabic OCR
  </div>
  <div>
    <span class="badge b-gold">Header/Footer Detection</span>
    <span class="badge b-teal">Kraken Offline OCR</span>
    <span class="badge b-rust">Photo &amp; Footer Extraction</span>
  </div>
</div>
""", unsafe_allow_html=True)

col_up, col_info = st.columns([2, 1])
with col_up:
    uploaded = st.file_uploader("Upload Arabic PDF", type=["pdf"])
with col_info:
    st.markdown("""
    **Phase 1a produces:**
    - `*_phase1a_pages[_part_N].zip` — clean page images (main text body)
    - `*_phase1a_footers.pdf` — footnote regions assembled (optional)
    - `*_phase1a_footers_imgs.zip` — footer image per page (optional)
    - `*_phase1a_photos.zip` — photographs + captions (optional)
    - `*_phase1a_corrected.txt` — raw Kraken OCR text, per page
    - `*_phase1a_normalized.txt` — after Arabic normalisation
    - `*_phase1a.json` — structured page data for Phase 1b

    Use **None** OCR backend to export images only, then upload
    OCR text to Phase 1b via *Upload User Corrected*.
    """)

if uploaded:
    if st.button("▶ Run Phase 1a", type="primary", use_container_width=True, key="p1a_run"):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path   = Path(tmp_dir) / uploaded.name
            output_dir = Path(tmp_dir) / "output"
            output_dir.mkdir()
            tmp_path.write_bytes(uploaded.read())

            progress_bar = st.progress(0.0)
            status_text  = st.empty()
            log_lines: list[str] = []
            log_ph = st.empty()

            def on_progress_1a(step: str, pct: float):
                progress_bar.progress(min(pct, 1.0))
                status_text.markdown(f"**{step}**")
                cls = "done" if pct >= 1.0 else "active"
                log_lines.append(f"<span class='{cls}'>{'✓' if pct>=1.0 else '›'} {step}</span>")
                log_ph.markdown(
                    "<div class='step-log'>" + "<br>".join(log_lines) + "</div>",
                    unsafe_allow_html=True,
                )

            cfg = Phase1Config(
                mode             = p1a_mode,
                strip_margins    = strip_margins,
                include_footers  = include_footers,
                include_photos   = include_photos,
                zip_split_mb     = zip_split_mb,
                export_dpi       = ocr_dpi,
                ocr_backend      = ocr_backend,
                kraken_bidi      = kraken_bidi,
                kraken_threshold = kraken_threshold,
                kraken_pad       = kraken_pad,
                max_tokens=max_tokens, overlap_tokens=overlap_tokens,
                output_dir=str(output_dir),
                anthropic_api_key=anthropic_key,
                script_genre=script_genre,
                book_author=book_author,
                book_pages=int(book_pages),
                book_structure=book_structure,
                diacritize=diacritize_script,
                scriptwriter_model=scriptwriter_model,
            )

            try:
                result_a = Phase1aPipeline(config=cfg, on_progress=on_progress_1a).run(tmp_path)

                # Persist to session state before temp dir is cleaned up
                st.session_state["phase1a_result"]           = result_a
                st.session_state["phase1a_corrected_bytes"]  = result_a.corrected_txt_path.read_bytes()
                st.session_state["phase1a_corrected_name"]   = result_a.corrected_txt_path.name
                st.session_state["phase1a_normalized_bytes"] = result_a.normalized_txt_path.read_bytes()
                st.session_state["phase1a_normalized_name"]  = result_a.normalized_txt_path.name
                st.session_state["phase1a_json_bytes"]       = result_a.normalized_json_path.read_bytes()
                st.session_state["phase1a_json_name"]        = result_a.normalized_json_path.name

                # Page image ZIPs (may be multi-part)
                st.session_state["phase1a_zip_parts"] = [
                    {"bytes": zp.read_bytes(), "name": zp.name}
                    for zp in result_a.pages_zip_paths
                    if zp.exists()
                ]

                # Footer outputs
                for key in ("phase1a_footers_pdf", "phase1a_footers_zip"):
                    st.session_state.pop(key, None)
                if result_a.footers_pdf_path and result_a.footers_pdf_path.exists():
                    st.session_state["phase1a_footers_pdf"] = {
                        "bytes": result_a.footers_pdf_path.read_bytes(),
                        "name":  result_a.footers_pdf_path.name,
                    }
                if result_a.footers_zip_path and result_a.footers_zip_path.exists():
                    st.session_state["phase1a_footers_zip"] = {
                        "bytes": result_a.footers_zip_path.read_bytes(),
                        "name":  result_a.footers_zip_path.name,
                    }

                # Photo output
                st.session_state.pop("phase1a_photos_zip", None)
                if result_a.photos_zip_path and result_a.photos_zip_path.exists():
                    st.session_state["phase1a_photos_zip"] = {
                        "bytes": result_a.photos_zip_path.read_bytes(),
                        "name":  result_a.photos_zip_path.name,
                    }

                st.session_state["phase1a_meta"] = {
                    "pdf_type":       result_a.pdf_type,
                    "total_pages":    result_a.total_pages,
                    "elapsed_sec":    result_a.elapsed_sec,
                    "warnings":       result_a.warnings,
                    "ocr_backend":    ocr_backend,
                    "mode":           p1a_mode,
                    "n_footer_pages": result_a.n_footer_pages,
                    "n_photos":       result_a.n_photos,
                    "asked_footers":  include_footers,
                    "asked_photos":   include_photos,
                }
                status_text.success("Phase 1a complete ✓")

            except Exception as exc:
                st.error(f"Phase 1a failed: {exc}")
                logging.exception("Phase 1a error")

# ── Phase 1a results ─────────────────────────────────────────────────── #
if "phase1a_meta" in st.session_state:
    meta_a = st.session_state["phase1a_meta"]

    for w in meta_a["warnings"]:
        st.markdown(f"<div class='warn-card'>⚠ {w}</div>", unsafe_allow_html=True)

    type_colors = {"digital": "#c9a84c", "scanned": "#1e6b6b", "mixed": "#b94f2a"}
    tc = type_colors.get(meta_a["pdf_type"], "#c9a84c")
    _extra_cards = ""
    if meta_a.get("n_footer_pages", 0):
        _extra_cards += (
            f'<div class="metric-card">'
            f'<div class="val">{meta_a["n_footer_pages"]}</div>'
            f'<div class="lbl">Footer pages</div></div>'
        )
    if meta_a.get("n_photos", 0):
        _extra_cards += (
            f'<div class="metric-card">'
            f'<div class="val">{meta_a["n_photos"]}</div>'
            f'<div class="lbl">Photos</div></div>'
        )
    st.markdown(f"""
    <div class="metric-row">
      <div class="metric-card">
        <div class="val">{meta_a['total_pages']}</div><div class="lbl">Pages</div>
      </div>
      <div class="metric-card" style="border-top-color:{tc}">
        <div class="val" style="font-size:1.3rem;padding-top:.3rem">{meta_a['pdf_type'].upper()}</div>
        <div class="lbl">PDF Type</div>
      </div>
      {_extra_cards}
      <div class="metric-card purple">
        <div class="val">{meta_a['elapsed_sec']:.1f}s</div><div class="lbl">Elapsed</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("#### 📥 Phase 1a Downloads")

    # ── Page images ZIP(s) ───────────────────────────────────────────────── #
    zip_parts = st.session_state.get("phase1a_zip_parts", [])
    if zip_parts:
        if len(zip_parts) == 1:
            st.download_button(
                "⬇ Page images ZIP (main text body)",
                data=zip_parts[0]["bytes"],
                file_name=zip_parts[0]["name"],
                mime="application/zip",
                use_container_width=True,
                help="Header/footer-stripped page images. Use with any external OCR tool.",
            )
        else:
            st.markdown(f"**Page images ZIP — {len(zip_parts)} parts** (select one to download):")
            _sel = st.selectbox(
                "Select part:",
                range(len(zip_parts)),
                format_func=lambda i: (
                    f"{zip_parts[i]['name']}  "
                    f"({len(zip_parts[i]['bytes']) / 1_048_576:.0f} MB)"
                ),
                key="p1a_zip_sel",
            )
            st.download_button(
                f"⬇ {zip_parts[_sel]['name']}",
                data=zip_parts[_sel]["bytes"],
                file_name=zip_parts[_sel]["name"],
                mime="application/zip",
                use_container_width=True,
                key="p1a_zip_dl",
            )

    # ── Footer outputs ───────────────────────────────────────────────────── #
    if "phase1a_footers_pdf" in st.session_state or "phase1a_footers_zip" in st.session_state:
        n_foot = meta_a.get("n_footer_pages", 0)
        st.markdown(f"**Footnote regions** — {n_foot} page(s) with footnotes detected:")
        fc1, fc2 = st.columns(2)
        if "phase1a_footers_pdf" in st.session_state:
            fd = st.session_state["phase1a_footers_pdf"]
            fc1.download_button(
                "⬇ Footers PDF", data=fd["bytes"], file_name=fd["name"],
                mime="application/pdf", use_container_width=True,
                help="All footnote strips assembled into a labeled PDF, one per page.",
            )
        if "phase1a_footers_zip" in st.session_state:
            fz = st.session_state["phase1a_footers_zip"]
            fc2.download_button(
                "⬇ Footer images ZIP", data=fz["bytes"], file_name=fz["name"],
                mime="application/zip", use_container_width=True,
                help="Individual footer PNG images at export DPI.",
            )
    elif meta_a.get("mode") == "single_book" and meta_a.get("asked_footers"):
        st.info("No footnote regions were detected in this document.")

    # ── Photo output ─────────────────────────────────────────────────────── #
    if "phase1a_photos_zip" in st.session_state:
        n_ph = meta_a.get("n_photos", 0)
        pz = st.session_state["phase1a_photos_zip"]
        st.download_button(
            f"⬇ Photographs ZIP ({n_ph} image{'s' if n_ph != 1 else ''})",
            data=pz["bytes"], file_name=pz["name"],
            mime="application/zip", use_container_width=True,
            help="Extracted photographic regions and their captions as individual PNGs.",
        )
    elif meta_a.get("mode") == "single_book" and meta_a.get("asked_photos"):
        st.info("No photographs were detected in this document.")

    # ── OCR text outputs ─────────────────────────────────────────────────── #
    if meta_a.get("ocr_backend") == "none" or meta_a.get("mode") == "raw_export":
        st.info(
            "Page images exported. Download the ZIP, run OCR externally (e.g. with "
            "Kraken, Google Vision, or any other tool), then upload the resulting "
            "text file to **Phase 1b → Upload User Corrected** below."
        )
    else:
        a1, a2, a3 = st.columns(3)
        with a1:
            st.download_button(
                "⬇ OCR text (raw)", data=st.session_state["phase1a_corrected_bytes"],
                file_name=st.session_state["phase1a_corrected_name"],
                mime="text/plain", use_container_width=True,
                help="Raw Kraken OCR output before Arabic normalisation",
            )
        with a2:
            st.download_button(
                "⬇ Normalized text", data=st.session_state["phase1a_normalized_bytes"],
                file_name=st.session_state["phase1a_normalized_name"],
                mime="text/plain", use_container_width=True,
                help="After Arabic normalisation — input to Phase 1b chunking",
            )
        with a3:
            st.download_button(
                "⬇ Phase 1a JSON", data=st.session_state["phase1a_json_bytes"],
                file_name=st.session_state["phase1a_json_name"],
                mime="application/json", use_container_width=True,
                help="Structured page data — upload to Phase 1b to skip re-running OCR",
            )

    with st.expander("🔬 Compare: raw OCR vs normalised"):
        st.caption("Left = raw Kraken OCR output · Right = after Arabic normalisation")
        rc1, rc2 = st.columns(2)
        with rc1:
            st.markdown("**OCR-corrected**")
            corr_txt = st.session_state.get("phase1a_corrected_bytes", b"").decode("utf-8", errors="replace")
            st.text_area("corr", corr_txt[:4000], height=300, label_visibility="collapsed")
        with rc2:
            st.markdown("**Normalised**")
            norm_txt = st.session_state.get("phase1a_normalized_bytes", b"").decode("utf-8", errors="replace")
            st.text_area("norm", norm_txt[:4000], height=300, label_visibility="collapsed")

# ════════════════════════════════════════════════════════════════════════ #
#  Phase 1b — Chunk & Summarise                                           #
# ════════════════════════════════════════════════════════════════════════ #
st.markdown("---")
st.markdown("""
<div class="app-header" style="margin-top:1rem">
  <div class="eyebrow">Phase 1b</div>
  <h1>Chunk &amp; Summarise</h1>
  <div class="sub">Semantic chunking · Hierarchical summarisation · Arabic video script (625–850 words)</div>
  <div>
    <span class="badge b-teal">Mishkal Diacritizer</span>
    <span class="badge b-rust">Semantic Chunking</span>
  </div>
</div>
""", unsafe_allow_html=True)

p1b_source = st.radio(
    "Input source",
    ["Phase 1a session result", "Upload User Corrected (.txt)"],
    horizontal=True,
    key="p1b_source",
    help=(
        "**Session result** — use the Phase 1a output from this session (no re-upload needed).\n\n"
        "**Upload User Corrected** — upload a plain .txt file you edited manually. "
        "The entire file is treated as a single normalised page and fed directly to chunking."
    ),
)

_p1b_ready = False
_p1b_source_obj = None   # Phase1aResult or Path

if p1b_source == "Phase 1a session result":
    if "phase1a_result" not in st.session_state:
        st.info("Run Phase 1a above first, or switch to **Upload User Corrected**.")
    else:
        meta_a = st.session_state.get("phase1a_meta", {})
        st.caption(
            f"Session result: {meta_a.get('total_pages', '?')} pages · "
            f"{meta_a.get('pdf_type', '?')} · {meta_a.get('elapsed_sec', 0):.1f}s"
        )
        _p1b_source_obj = st.session_state["phase1a_result"]
        _p1b_ready = True
else:
    p1b_txt_up = st.file_uploader(
        "Upload corrected text (.txt)",
        type=["txt"],
        key="p1b_txt_up",
        help=(
            "Upload a plain UTF-8 .txt file containing the corrected Arabic text. "
            "The entire file is treated as one page — no OCR re-run needed."
        ),
    )
    if p1b_txt_up:
        txt_content = p1b_txt_up.read().decode("utf-8", errors="replace")
        _p1b_source_obj = Phase1aResult(
            source_path          = p1b_txt_up.name,
            pdf_type             = "scanned",
            total_pages          = 1,
            metadata             = {"title": Path(p1b_txt_up.name).stem},
            pages                = [
                {
                    "page_number":  1,
                    "pdf_type":     "scanned",
                    "raw_text":     txt_content,
                    "raw_text_pre": "",
                }
            ],
            corrected_txt_path   = Path(tempfile.gettempdir()) / "dummy_corrected.txt",
            normalized_txt_path  = Path(tempfile.gettempdir()) / "dummy_normalized.txt",
            normalized_json_path = Path(tempfile.gettempdir()) / "dummy_phase1a.json",
        )
        _p1b_ready = True
        st.caption(f"Loaded: {p1b_txt_up.name} — {len(txt_content.split())} words")

if _p1b_ready:
    if st.button("▶ Run Phase 1b", type="primary", use_container_width=True, key="p1b_run"):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "output"
            output_dir.mkdir()

            progress_bar_b = st.progress(0.0)
            status_text_b  = st.empty()
            log_lines_b: list[str] = []
            log_ph_b = st.empty()

            def on_progress_1b(step: str, pct: float):
                progress_bar_b.progress(min(pct, 1.0))
                status_text_b.markdown(f"**{step}**")
                cls = "done" if pct >= 1.0 else "active"
                log_lines_b.append(f"<span class='{cls}'>{'✓' if pct>=1.0 else '›'} {step}</span>")
                log_ph_b.markdown(
                    "<div class='step-log'>" + "<br>".join(log_lines_b) + "</div>",
                    unsafe_allow_html=True,
                )

            cfg_b = Phase1Config(
                max_tokens=max_tokens, overlap_tokens=overlap_tokens,
                output_dir=str(output_dir),
                anthropic_api_key=anthropic_key,
                script_genre=script_genre,
                book_author=book_author,
                book_pages=int(book_pages),
                book_structure=book_structure,
                diacritize=diacritize_script,
                scriptwriter_model=scriptwriter_model,
            )

            try:
                result_b = Phase1bPipeline(config=cfg_b, on_progress=on_progress_1b).run(
                    _p1b_source_obj
                )

                st.session_state["json_bytes"]    = result_b.json_path.read_bytes()
                st.session_state["txt_bytes"]     = result_b.txt_path.read_bytes()
                st.session_state["raw_txt_bytes"] = result_b.raw_txt_path.read_bytes()
                st.session_state["json_name"]     = result_b.json_path.name
                st.session_state["txt_name"]      = result_b.txt_path.name
                st.session_state["raw_txt_name"]  = result_b.raw_txt_path.name
                if result_b.script_path and result_b.script_path.exists():
                    st.session_state["script_bytes"] = result_b.script_path.read_bytes()
                    st.session_state["script_name"]  = result_b.script_path.name
                else:
                    st.session_state.pop("script_bytes", None)
                    st.session_state.pop("script_name", None)
                if result_b.script_diac_path and result_b.script_diac_path.exists():
                    st.session_state["script_diac_bytes"] = result_b.script_diac_path.read_bytes()
                    st.session_state["script_diac_name"]  = result_b.script_diac_path.name
                else:
                    st.session_state.pop("script_diac_bytes", None)
                    st.session_state.pop("script_diac_name", None)
                if result_b.script_meta_path and result_b.script_meta_path.exists():
                    st.session_state["script_meta_bytes"] = result_b.script_meta_path.read_bytes()
                    st.session_state["script_meta_name"]  = result_b.script_meta_path.name
                else:
                    st.session_state.pop("script_meta_bytes", None)
                    st.session_state.pop("script_meta_name", None)
                st.session_state["result_meta"] = {
                    "pdf_type":    result_b.pdf_type,
                    "total_pages": result_b.total_pages,
                    "elapsed_sec": result_b.elapsed_sec,
                    "warnings":    result_b.warnings,
                    "chunks": [
                        {
                            "chunk_id":   c.chunk_id,
                            "chapter":    c.chapter,
                            "page_start": c.page_start,
                            "page_end":   c.page_end,
                            "word_count": c.word_count,
                            "token_est":  c.token_est,
                            "text":       c.text,
                        }
                        for c in result_b.chunks
                    ],
                }
                status_text_b.success("Phase 1b complete ✓")

            except Exception as exc:
                st.error(f"Phase 1b failed: {exc}")
                logging.exception("Phase 1b error")

# ── Phase 1b results ─────────────────────────────────────────────────── #
if "result_meta" in st.session_state:
    meta   = st.session_state["result_meta"]
    chunks = meta["chunks"]

    st.markdown("---")
    st.markdown("### Phase 1b Results")

    for w in meta["warnings"]:
        st.markdown(f"<div class='warn-card'>⚠ {w}</div>", unsafe_allow_html=True)

    type_colors = {"digital": "#c9a84c", "scanned": "#1e6b6b", "mixed": "#b94f2a"}
    tc = type_colors.get(meta["pdf_type"], "#c9a84c")
    total_words = sum(c["word_count"] for c in chunks)

    st.markdown(f"""
    <div class="metric-row">
      <div class="metric-card">
        <div class="val">{meta['total_pages']}</div><div class="lbl">Pages</div>
      </div>
      <div class="metric-card" style="border-top-color:{tc}">
        <div class="val" style="font-size:1.3rem;padding-top:.3rem">{meta['pdf_type'].upper()}</div>
        <div class="lbl">PDF Type</div>
      </div>
      <div class="metric-card teal">
        <div class="val">{len(chunks)}</div><div class="lbl">Chunks</div>
      </div>
      <div class="metric-card rust">
        <div class="val">{total_words:,}</div><div class="lbl">Words</div>
      </div>
      <div class="metric-card purple">
        <div class="val">{meta['elapsed_sec']:.1f}s</div><div class="lbl">Elapsed</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("#### 📥 Downloads")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("⬇ JSON (processed)", data=st.session_state["json_bytes"],
                           file_name=st.session_state["json_name"],
                           mime="application/json", use_container_width=True)
    with c2:
        st.download_button("⬇ Text (processed)", data=st.session_state["txt_bytes"],
                           file_name=st.session_state["txt_name"],
                           mime="text/plain", use_container_width=True)
    with c3:
        st.download_button("⬇ Text (raw extract)", data=st.session_state.get("raw_txt_bytes", b""),
                           file_name=st.session_state.get("raw_txt_name", "raw.txt"),
                           mime="text/plain", use_container_width=True,
                           help="Text straight from PyMuPDF/OCR before any normalisation")

    if "script_bytes" in st.session_state:
        st.markdown("#### 📝 Arabic Video Script")
        if "script_meta_bytes" in st.session_state:
            try:
                smeta = json.loads(st.session_state["script_meta_bytes"])
                scores  = smeta.get("scores", {})
                total   = smeta.get("total_score", 0)
                wc      = smeta.get("word_count", 0)
                retries = smeta.get("retries_used", 0)
                score_bar = " · ".join(f"{k} {v}/10" for k, v in scores.items())
                st.markdown(
                    f"<div class='metric-row'>"
                    f"<div class='metric-card'><div class='val'>{wc}</div><div class='lbl'>Words</div></div>"
                    f"<div class='metric-card teal'><div class='val'>{total}/50</div><div class='lbl'>Score</div></div>"
                    f"<div class='metric-card rust'><div class='val'>{retries}</div><div class='lbl'>Retries</div></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.caption(f"Criteria: {score_bar}")
                feedback = smeta.get("editor_feedback", "")
                if feedback:
                    st.caption(f"Editor: {feedback}")
            except Exception:
                pass

        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            st.download_button("⬇ Script (plain)", data=st.session_state["script_bytes"],
                               file_name=st.session_state["script_name"],
                               mime="text/plain", use_container_width=True)
        with sc2:
            st.download_button("⬇ Script (diacritized)",
                               data=st.session_state.get("script_diac_bytes", b""),
                               file_name=st.session_state.get("script_diac_name", "script_diac.txt"),
                               mime="text/plain", use_container_width=True)
        with sc3:
            st.download_button("⬇ Script metadata",
                               data=st.session_state.get("script_meta_bytes", b""),
                               file_name=st.session_state.get("script_meta_name", "script_meta.json"),
                               mime="application/json", use_container_width=True)

        with st.expander("📄 Preview script"):
            script_txt = st.session_state["script_bytes"].decode("utf-8", errors="replace")
            st.markdown(
                f"<div style='direction:rtl;text-align:right;font-size:0.95rem;"
                f"line-height:1.9;background:#fefcf8;padding:1.2rem 1.5rem;"
                f"border:1px solid #e0dbd0;border-radius:4px'>{script_txt}</div>",
                unsafe_allow_html=True,
            )
    elif anthropic_key:
        st.info("Script generation ran but produced no output — check warnings above.")

    st.markdown("#### 🔍 Chunk Preview")
    n = st.slider("Chunks to preview", 1, min(20, len(chunks)), 5)
    for c in chunks[:n]:
        border = "scanned" if meta["pdf_type"] == "scanned" else ""
        st.markdown(
            f"""<div class="chunk-card {border}">
              <div class="chunk-meta">
                chunk {c['chunk_id']:04d} · {c['chapter']}
                · pp. {c['page_start']}–{c['page_end']}
                · {c['word_count']} words · ~{c['token_est']} tokens
              </div>
              {html.escape(c['text'][:500])}{"…" if len(c['text']) > 500 else ""}
            </div>""",
            unsafe_allow_html=True,
        )

    with st.expander("🔬 Compare: raw extract vs normalised"):
        st.caption("Left = straight from PyMuPDF/OCR · Right = after normalisation")
        rc1, rc2 = st.columns(2)
        with rc1:
            st.markdown("**Raw extract**")
            raw_txt = st.session_state.get("raw_txt_bytes", b"").decode("utf-8", errors="replace")
            st.text_area("raw", raw_txt[:4000], height=300, label_visibility="collapsed")
        with rc2:
            st.markdown("**Processed**")
            proc_txt = st.session_state.get("txt_bytes", b"").decode("utf-8", errors="replace")
            st.text_area("proc", proc_txt[:4000], height=300, label_visibility="collapsed")

    with st.expander("🔎 Inspect raw JSON"):
        st.json(json.loads(st.session_state["json_bytes"]))

# ── Phase 2: Audio Generation ─────────────────────────────────────────── #
st.markdown("---")
st.markdown("""
<div class="app-header" style="margin-top:1rem">
  <div class="eyebrow">Arabic Book Brief Engine · Phase 2</div>
  <h1>Audio Generation</h1>
  <div class="sub">Convert your Arabic video script into spoken audio</div>
  <div>
    <span class="badge b-teal">gTTS Free</span>
    <span class="badge b-gold">ElevenLabs (soon)</span>
  </div>
</div>
""", unsafe_allow_html=True)

# Script source
p2_source = st.radio(
    "Script source",
    ["Phase 1 output", "Upload .txt file"],
    horizontal=True,
    key="p2_source",
    help=(
        "**Phase 1 output** — use a script generated by the pipeline above.\n\n"
        "**Upload .txt file** — load an existing book_script.txt or "
        "book_script_diacritized.txt from disk."
    ),
)

p2_text  = ""
p2_label = ""

if p2_source == "Phase 1 output":
    has_plain = "script_bytes"      in st.session_state
    has_diac  = "script_diac_bytes" in st.session_state
    if not has_plain and not has_diac:
        st.info(
            "No Phase 1 script in session. Run Phase 1 with an Anthropic API key, "
            "or switch to **Upload .txt file** to load an existing script."
        )
    else:
        variants = []
        if has_plain:
            variants.append("Plain (recommended)")
        if has_diac:
            variants.append("Diacritized")
        p2_variant = st.radio(
            "Script variant", variants, horizontal=True, key="p2_variant",
            help=(
                "**Plain** — recommended for both gTTS and ElevenLabs. "
                "The script is pre-cleaned (no markdown, TTS pause markers on headings). "
                "ElevenLabs applies its own diacritization internally.\n\n"
                "**Diacritized** — Mishkal harakat added. "
                "May conflict with ElevenLabs prosody on sentence-final consonants."
            ),
        )
        if p2_variant.startswith("Diacritized"):
            p2_text  = st.session_state["script_diac_bytes"].decode("utf-8", errors="replace")
            p2_label = "diacritized"
        else:
            p2_text  = st.session_state["script_bytes"].decode("utf-8", errors="replace")
            p2_label = "plain"

else:  # Upload .txt file
    p2_upload = st.file_uploader(
        "Upload script (.txt)",
        type=["txt"],
        key="p2_upload",
        help="Upload book_script.txt or book_script_diacritized.txt",
    )
    if p2_upload:
        p2_text  = p2_upload.read().decode("utf-8", errors="replace")
        p2_label = Path(p2_upload.name).stem

if p2_text:
    with st.expander("Preview script"):
        st.markdown(
            f"<div style='direction:rtl;text-align:right;font-size:0.95rem;"
            f"line-height:1.9;background:#fefcf8;color:#1a1a1a;padding:1.2rem 1.5rem;"
            f"border:1px solid #e0dbd0;border-radius:4px'>{html.escape(p2_text)}</div>",
            unsafe_allow_html=True,
        )

    tts_key = "gtts" if tts_backend == "gTTS (free)" else "elevenlabs"

    if tts_key == "elevenlabs" and (not el_api_key or not el_voice_id):
        st.warning("ElevenLabs requires both an API key and a Voice ID — fill them in the sidebar.")

    if st.button("🎙 Generate Audio", type="primary", use_container_width=True, key="p2_gen"):
        backend_label = "gTTS" if tts_key == "gtts" else "ElevenLabs"
        with st.spinner(f"Synthesizing with {backend_label}…"):
            try:
                audio = tts_synthesize(
                    p2_text,
                    backend=tts_key,
                    elevenlabs_api_key=el_api_key,
                    elevenlabs_voice_id=el_voice_id,
                )
                st.session_state["p2_audio_bytes"] = audio
                st.session_state["p2_audio_label"] = p2_label
                st.success("Audio ready ✓")
            except Exception as exc:
                st.error(f"TTS failed: {exc}")
                logging.exception("Phase 2 TTS error")

if "p2_audio_bytes" in st.session_state:
    import base64
    audio_bytes = st.session_state["p2_audio_bytes"]
    # st.audio can fail for large MP3s or non-standard MIME strings.
    # An inline <audio> element with the correct IANA type is more reliable.
    b64 = base64.b64encode(audio_bytes).decode()
    st.markdown(
        f'<audio controls style="width:100%;margin:0.5rem 0">'
        f'<source src="data:audio/mpeg;base64,{b64}" type="audio/mpeg">'
        f'</audio>',
        unsafe_allow_html=True,
    )
    dl_name = f"book_audio_{st.session_state.get('p2_audio_label', 'output')}.mp3"
    st.download_button(
        "⬇ Download MP3",
        data=audio_bytes,
        file_name=dl_name,
        mime="audio/mpeg",
        use_container_width=True,
        key="p2_dl",
    )

# ── Phase 3: Visual Generation ────────────────────────────────────────── #
st.markdown("---")
st.markdown("""
<div class="app-header" style="margin-top:1rem">
  <div class="eyebrow">Arabic Book Brief Engine · Phase 3</div>
  <h1>Final Video Assembly</h1>
  <div class="sub">AI shot plan · Visuals · Arabic voice · Burned-in subtitles · Complete MP4</div>
  <div>
    <span class="badge b-gold">Sonnet Shot Planner</span>
    <span class="badge b-teal">LoC · Wikimedia · IA · Pexels</span>
    <span class="badge b-rust">Vision-Scored Imagery</span>
    <span class="badge b-teal">Amiri Typography Cards</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Route (the first step in Phase 3) ─────────────────────────────────── #
p3_route = st.radio(
    "Route",
    ["Total solution — plan → fetch → render (saves a review dossier)",
     "Rendering only — re-render a saved review .zip (no API cost)"],
    key="p3_route",
    help="Total solution builds the video skeleton AND captures every image "
         "candidate into a review dossier you can download and refine. "
         "Rendering only re-renders a saved/edited review .zip at no further "
         "API cost — ideal for tweaking the look or swapping images.",
)
_p3_render_only = p3_route.startswith("Rendering only")

# ── Resolution (shared by both routes) ────────────────────────────────── #
p3_resolution = st.selectbox(
    "Resolution",
    ["1280×720 (faster)", "1920×1080 (full)"],
    index=0,
    key="p3_resolution",
    help="The shot-based render re-encodes every shot; 720p keeps memory and "
         "time down on Streamlit Cloud. 1080p is the broadcast target.",
)
_p3_w, _p3_h = (1280, 720) if p3_resolution.startswith("1280") else (1920, 1080)

p3_condition = st.checkbox(
    "Sharpen assets before render",
    value=False,
    key="p3_condition",
    help="Lanczos upscaling pass that normalises each chosen image to crisp, "
         "aspect-correct sizes. The renderer already fits images to the frame, "
         "so this is a quality polish. Leave off for a faster render.",
)


def _p3_hex_to_rgb(h):
    s = (h or "").lstrip("#")
    if len(s) != 6:
        return None
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _p3_build_config():
    """Build a RenderConfig from the sidebar look options (fetcher set later)."""
    from phase3 import RenderConfig
    return RenderConfig(
        width=_p3_w, height=_p3_h, fps=25,
        add_captions=p3_add_captions,
        book_cover=p3_book_cover_path,
        book_cover_fit=p3_book_cover_fit,
        book_cover_align=p3_book_cover_align,
        typography_family=p3_typography_family,
        grade=p3_color_grade,
        caption_backplate=p3_caption_backplate,
        caption_size=p3_caption_size,
        caption_pos=p3_caption_pos,
        text_scrim=p3_text_scrim,
        overlay_anchor=p3_overlay_anchor,
        title_scale=p3_title_size,
        title_color=_p3_hex_to_rgb(p3_title_color_hex) if p3_use_title_color else None,
        typography_over_image=p3_typography_over_image,
        parallax=p3_parallax,
        parallax_backend=p3_parallax_backend,
        parallax_warp=p3_parallax_warp,
        music_path=p3_music_path,
        music_gain_db=p3_music_gain,
        music_duck=p3_music_duck,
        fades=p3_fades,
    )


def _p3_make_cb():
    """A progress callback that streams a step log into the page."""
    prog = st.progress(0.0)
    status = st.empty()
    logbox = st.empty()
    lines: list[str] = []

    def cb(label: str, frac: float) -> None:
        prog.progress(min(max(frac, 0.0), 1.0))
        status.markdown(f"**{label}**")
        cls = "done" if frac >= 1.0 else "active"
        lines.append(f"<span class='{cls}'>{'✓' if frac >= 1.0 else '›'} {label}</span>")
        logbox.markdown(
            "<div class='step-log'>" + "<br>".join(lines[-12:]) + "</div>",
            unsafe_allow_html=True,
        )
    return cb


class _P3LogCapture:
    """Capture phase3.* log records during a render so they can be downloaded."""

    def __enter__(self):
        self._buf = io.StringIO()
        self._h = logging.StreamHandler(self._buf)
        self._h.setLevel(logging.INFO)
        self._h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | "
                                               "%(name)s | %(message)s"))
        logging.getLogger("phase3").addHandler(self._h)
        return self

    def __exit__(self, *exc):
        logging.getLogger("phase3").removeHandler(self._h)

    def text(self) -> str:
        return self._buf.getvalue()


def _p3_fetch_url(url: str, timeout: int = 120) -> bytes:
    """Download a dossier .zip server-side (bypasses the browser upload limit).

    Rewrites common share links to their direct-download form so a plain
    Google-Drive / Dropbox / GitHub link works.
    """
    import urllib.request as _ur
    import re as _re
    u = url.strip()
    m = _re.search(r"drive\.google\.com/file/d/([^/]+)", u)
    if m:
        u = f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    elif "dropbox.com" in u:
        u = u.replace("?dl=0", "?dl=1").replace("&dl=0", "&dl=1")
    elif "github.com" in u and "/blob/" in u:
        u = u.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    req = _ur.Request(u, headers={"User-Agent": "Lamahat/1.0"})
    with _ur.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _p3_store_outputs(mp4_path: Path, review_dir: Path | None = None) -> None:
    """Read the finished MP4 (+ optional review zip + thumbnail) into session."""
    from phase3 import extract_thumbnail, zip_review_dir
    st.session_state["p3_video_bytes"] = Path(mp4_path).read_bytes()
    _thumb = Path(mp4_path).with_suffix(".jpg")
    try:
        extract_thumbnail(mp4_path, _thumb, time=5.0)
        st.session_state["p3_thumb_bytes"] = _thumb.read_bytes()
    except Exception:
        st.session_state.pop("p3_thumb_bytes", None)
    st.session_state.pop("p3_review_zip", None)
    if review_dir is not None:
        try:
            _zip = Path(mp4_path).parent / "review_dir.zip"
            zip_review_dir(review_dir, _zip)
            st.session_state["p3_review_zip"] = _zip.read_bytes()
        except Exception as _ze:
            logging.warning("Review zip failed: %s", _ze)


if not _p3_render_only:
    # ═══════════════ TOTAL SOLUTION ═══════════════ #
    p3_script_src = st.radio(
        "Script source",
        ["Phase 1 output", "Upload .txt file"],
        horizontal=True,
        key="p3_script_src",
        help="Use a script already in session, or upload book_script_rev.txt / book_script.txt.",
    )
    p3_text = ""
    if p3_script_src == "Phase 1 output":
        if "script_bytes" in st.session_state:
            p3_text = st.session_state["script_bytes"].decode("utf-8", errors="replace")
        else:
            st.info("No Phase 1 script in session — switch to **Upload .txt file**.")
    else:
        p3_up = st.file_uploader("Upload script (.txt)", type=["txt"], key="p3_script_up")
        if p3_up:
            p3_text = p3_up.read().decode("utf-8", errors="replace")

    p3_audio_bytes: bytes | None = None
    if "p2_audio_bytes" in st.session_state:
        p3_audio_bytes = st.session_state["p2_audio_bytes"]
        st.caption("Using Phase 2 audio for word-level alignment.")
    else:
        p3_audio_up = st.file_uploader(
            "Upload audio (.mp3) — recommended for accurate timing",
            type=["mp3"], key="p3_audio_up",
            help="Skip to use a character-count estimate.",
        )
        if p3_audio_up:
            p3_audio_bytes = p3_audio_up.read()

    p3_genre = st.selectbox(
        "Book genre",
        ["history", "biography", "non-fiction", "philosophy",
         "science", "religion", "novel"],
        index=0, key="p3_genre",
        help="Steers the shot planner's tone and search-query style.",
    )
    _col_title, _col_char = st.columns(2)
    with _col_title:
        p3_book_title = st.text_input(
            "Book title (English)", key="p3_book_title",
            placeholder="e.g. Memoirs of Jafar al-Askari",
            help="Context for the shot planner and the vision-scoring rubric.",
        )
    with _col_char:
        p3_character_name = st.text_input(
            "Main character name (English)", key="p3_character_name",
            placeholder="e.g. Jafar al-Askari",
            help="Latin spelling — searches LoC / Wikimedia / IA for the "
                 "subject's portrait and anchors the vision rubric.",
        )

    if p3_text:
        with st.expander("Preview script sections"):
            try:
                from phase3.parser import parse_sections
                for s in parse_sections(p3_text):
                    st.markdown(
                        f"<div style='font-family:DM Mono,monospace;font-size:0.7rem;"
                        f"color:#c9a84c;margin-top:0.6rem'>{s.section_id.upper()}</div>"
                        f"<div style='direction:rtl;text-align:right;font-size:0.85rem;"
                        f"line-height:1.7'>{s.text[:200]}{'…' if len(s.text)>200 else ''}</div>",
                        unsafe_allow_html=True,
                    )
            except Exception:
                st.text(p3_text[:600])

        _p3_slim_label = st.selectbox(
            "Saved dossier candidates",
            ["Chosen only (smallest)", "Top 3 (swap-friendly)", "All candidates"],
            index=1,
            key="p3_slim",
            help="How many image candidates the downloadable review .zip keeps "
                 "per shot. Fewer = a much smaller zip that uploads under "
                 "Streamlit's limit for the Rendering-only route. 'Chosen only' "
                 "keeps just the picked (sharpened) image; 'Top 3' keeps a few "
                 "alternatives to swap to; 'All' keeps everything (can exceed "
                 "the upload limit).",
        )
        _p3_slim_mode = {"Chosen only (smallest)": "chosen",
                         "Top 3 (swap-friendly)": "top",
                         "All candidates": "all"}[_p3_slim_label]

        with st.expander("Alignment (word timing)"):
            _p3_align_label = st.selectbox(
                "Alignment backend",
                ["Interpolated — char-rate estimate (fast, default)",
                 "Whisper — openai-whisper (needs the package + RAM)",
                 "WhisperX — most accurate (needs torch+GPU)"],
                index=0, key="p3_align",
                help="Interpolated spreads time by character count — instant, "
                     "but cuts can drift ±0.2–0.5 s. Whisper/WhisperX give real "
                     "word-level timing IF the libraries are installed; on "
                     "Streamlit Cloud's ~1 GB RAM WhisperX usually won't fit. "
                     "Best accuracy on Cloud: upload a word_timings.json computed "
                     "elsewhere (Colab) below.",
            )
            _p3_align_backend = {"Interpolated": "interpolated",
                                 "Whisper": "whisper",
                                 "WhisperX": "whisperx"}[_p3_align_label.split(" ")[0]]
            _p3_wt_up = st.file_uploader(
                "Word timings (.json) — optional, overrides the backend",
                type=["json"], key="p3_word_timings",
                help="A precomputed alignment (the JSON phase3_run.py "
                     "--save-alignment / the Colab notebook writes). When set, "
                     "ASR is skipped entirely and these exact timings are used.",
            )

        st.caption(
            "Total solution aligns the narration, asks Claude Sonnet for a shot "
            "plan, captures image candidates into a review dossier, then "
            "conditions and renders. Requires an Anthropic key; takes several "
            "minutes. You'll get the MP4 **and** a review .zip to refine later."
        )

        if st.button("▶ Generate Final Video", type="primary",
                     use_container_width=True, key="p3_gen"):
            if not anthropic_key:
                st.warning("An Anthropic API key is required for the shot "
                           "planner. Add it in the sidebar.")
                st.stop()
            import tempfile as _tmp, shutil as _sh
            _out_dir = Path(_tmp.mkdtemp(prefix="bk2v_out_"))
            _out_mp4 = _out_dir / "final_video.mp4"
            # Keep the review dossier on the server for this session so the
            # Rendering-only route can reuse it WITHOUT a download/re-upload
            # round trip (the upload is what fails on Streamlit Cloud).
            _prev = st.session_state.pop("p3_session_review_dir", None)
            if _prev:
                _sh.rmtree(_prev, ignore_errors=True)
            _review = Path(_tmp.mkdtemp(prefix="bk2v_review_"))
            _wt_path = None
            if _p3_wt_up is not None:
                _wt_path = _review / "word_timings_uploaded.json"
                _wt_path.write_bytes(_p3_wt_up.getvalue())
            _cb = _p3_make_cb()
            try:
                from phase3 import build_total_solution
                build_total_solution(
                    script_text=p3_text,
                    output_path=_out_mp4,
                    review_dir=_review,
                    audio_bytes=p3_audio_bytes,
                    anthropic_api_key=anthropic_key,
                    pexels_api_key=pexels_api_key,
                    book_title=p3_book_title,
                    character_name=p3_character_name,
                    genre=p3_genre,
                    align_backend=_p3_align_backend,
                    word_timings_path=_wt_path,
                    character_portrait=p3_character_path,
                    book_cover=p3_book_cover_path,
                    book_cover_fit=p3_book_cover_fit,
                    book_cover_align=p3_book_cover_align,
                    config=_p3_build_config(),
                    condition=p3_condition,
                    slim_mode=_p3_slim_mode,
                    on_progress=_cb,
                )
                _p3_store_outputs(_out_mp4, review_dir=_review)
                # Persist for in-session Rendering-only reuse (no upload needed).
                st.session_state["p3_session_review_dir"] = str(_review)
                _cb("Final video ready ✓", 1.0)
            except Exception as _exc:
                st.error(f"Video generation failed: {_exc}")
                logging.exception("Phase 3 total-solution error")
                _sh.rmtree(_review, ignore_errors=True)
            finally:
                _sh.rmtree(_out_dir, ignore_errors=True)

else:
    # ═══════════════ RENDERING ONLY ═══════════════ #
    # Prefer the in-session dossier (no upload) when a Total-solution run is
    # still on the server — uploading a big .zip is what fails on Cloud.
    _sess_dir = st.session_state.get("p3_session_review_dir")
    _sess_ok = bool(_sess_dir) and (Path(_sess_dir) / "decisions.json").exists()

    p3_zip_up = None
    p3_zip_url = ""
    p3_plan_up = None
    p3_ro_audio_up = None

    _SESS = "This session's Total-solution dossier (no upload)"
    _UP = "Upload a review .zip"
    _URL = "Fetch a review .zip from a URL"
    _opts = ([_SESS] if _sess_ok else []) + [_UP, _URL]
    p3_ro_source = st.radio(
        "Dossier source", _opts, key="p3_ro_source",
        help="Re-render this session's dossier (no upload), upload a saved "
             ".zip, or — when the upload is too big for Streamlit Cloud — have "
             "the server fetch it from a direct-download URL.",
    )
    _use_session = p3_ro_source == _SESS
    _use_url = p3_ro_source == _URL

    if _use_session:
        st.caption(
            "Re-render this session's dossier with the current sidebar look "
            "options — no upload, no planner/fetch API cost. Drop my_/user_ "
            "files or edit the dossier on disk between runs to swap images."
        )
    elif _use_url:
        st.caption(
            "The server downloads the .zip directly — no browser-upload size "
            "limit. Paste a direct-download link (Google Drive / Dropbox / a "
            "GitHub release asset or raw URL). Best for large dossiers."
        )
        p3_zip_url = st.text_input(
            "Dossier .zip URL", key="p3_zip_url",
            placeholder="https://… (direct download to the review .zip)",
        )
        with st.expander("Plan / audio (only if not inside the zip)"):
            p3_plan_up = st.file_uploader("Shot plan (.json)", type=["json"],
                                          key="p3_plan_up")
            p3_ro_audio_up = st.file_uploader("Narration (.mp3)", type=["mp3"],
                                              key="p3_ro_audio_up")
    else:
        st.caption(
            "Upload a review .zip produced by Total solution (or "
            "prebuild_assets.py), then re-render it with the current sidebar "
            "look options — no API cost. If the upload keeps failing, use "
            "'Fetch a review .zip from a URL' instead."
        )
        p3_zip_up = st.file_uploader(
            "Review dossier (.zip)", type=["zip"], key="p3_review_zip_up",
            help="Must contain decisions.json and the shot folders. "
                 "Total-solution zips also include shot_plan.json and "
                 "narration.mp3.",
        )
        with st.expander("Plan / audio (only if not inside the zip)"):
            p3_plan_up = st.file_uploader("Shot plan (.json)", type=["json"],
                                          key="p3_plan_up")
            p3_ro_audio_up = st.file_uploader("Narration (.mp3)", type=["mp3"],
                                              key="p3_ro_audio_up")

    p3_ro_web = st.checkbox(
        "Fill gaps from the web", value=False, key="p3_ro_web",
        help="Off (default): render ONLY the dossier's images — overrides "
             "(overrides/ and my_/user_ files), the character pool, and each "
             "shot's chosen candidate; shots with no image get a placeholder. "
             "On: also search Wikimedia/Pexels for shots the dossier doesn't "
             "cover (costs time and may need API keys).",
    )

    _ro_ready = (_use_session or (p3_zip_up is not None)
                 or (_use_url and bool(p3_zip_url.strip())))
    if _ro_ready and st.button("▶ Render from Review", type="primary",
                               use_container_width=True, key="p3_render_only_btn"):
        import tempfile as _tmp, zipfile as _zf, shutil as _sh
        _out_dir = Path(_tmp.mkdtemp(prefix="bk2v_ro_"))
        _out_mp4 = _out_dir / "final_video.mp4"
        _cb = _p3_make_cb()

        def _ro_fail(msg: str) -> None:
            st.error(msg)
            _sh.rmtree(_out_dir, ignore_errors=True)
            st.stop()

        try:
            if _use_session:
                _review = Path(_sess_dir)
                _plan_path = _review / "shot_plan.json"
                _ro_audio = (_review / "narration.mp3"
                             if (_review / "narration.mp3").exists() else None)
            else:
                # Source the zip bytes: server-side URL fetch, or browser upload.
                if _use_url:
                    _cb("Downloading dossier…", 0.01)
                    try:
                        _zip_bytes = _p3_fetch_url(p3_zip_url)
                    except Exception as _dl:
                        _ro_fail(f"Could not download the dossier from that URL: "
                                 f"{_dl}. Make sure it's a direct-download link "
                                 f"to the .zip.")
                else:
                    _zip_bytes = p3_zip_up.getvalue()

                _extract_root = _out_dir / "review"
                _extract_root.mkdir(parents=True, exist_ok=True)
                try:
                    with _zf.ZipFile(io.BytesIO(_zip_bytes)) as _z:
                        _z.extractall(_extract_root)
                except _zf.BadZipFile:
                    _ro_fail("That file is not a valid .zip. Check the URL/file "
                             "points at the review dossier zip.")
                # The dossier is wherever decisions.json lives — find it no
                # matter how the zip nests/names folders (e.g. "Archive/").
                _found = sorted(_extract_root.rglob("decisions.json"))
                if not _found:
                    _ro_fail("No decisions.json found anywhere in the zip. "
                             "A review dossier must contain decisions.json "
                             "and the shot_NN_* folders.")
                _review = _found[0].parent
                _plan_path = _review / "shot_plan.json"
                if not _plan_path.exists():
                    _alt = sorted(_extract_root.rglob("shot_plan.json"))
                    if _alt:
                        _plan_path = _alt[0]
                    elif p3_plan_up:
                        _plan_path.write_bytes(p3_plan_up.getvalue())
                _ro_audio = None
                if (_review / "narration.mp3").exists():
                    _ro_audio = _review / "narration.mp3"
                elif p3_ro_audio_up:
                    _ro_audio = _review / "narration.mp3"
                    _ro_audio.write_bytes(p3_ro_audio_up.getvalue())

            # Both the plan and the narration must be present before rendering.
            if not _plan_path.exists():
                _ro_fail("Missing shot_plan.json. It must be inside the zip or "
                         "uploaded under 'Plan / audio'.")
            if _ro_audio is None or not Path(_ro_audio).exists():
                _ro_fail("Missing narration.mp3. It must be inside the zip or "
                         "uploaded under 'Plan / audio'.")

            from phase3 import render_from_review
            with _P3LogCapture() as _logcap:
                render_from_review(
                    review_dir=_review,
                    output_path=_out_mp4,
                    audio_path=_ro_audio,
                    plan_path=_plan_path,
                    config=_p3_build_config(),
                    anthropic_api_key=anthropic_key,
                    pexels_api_key=pexels_api_key,
                    book_title=st.session_state.get("p3_book_title", ""),
                    character_name=st.session_state.get("p3_character_name", ""),
                    condition=p3_condition,
                    offline=not p3_ro_web,
                    on_progress=_cb,
                )
            st.session_state["p3_ro_log"] = _logcap.text()
            _p3_store_outputs(_out_mp4)
            _cb("Final video ready ✓", 1.0)
        except Exception as _exc:
            st.session_state["p3_ro_log"] = (
                st.session_state.get("p3_ro_log", "")
                + f"\nERROR: {_exc}\n")
            st.error(f"Render failed: {_exc}")
            logging.exception("Phase 3 render-only error")
        finally:
            # Only the scratch dir — never the persistent session dossier.
            _sh.rmtree(_out_dir, ignore_errors=True)

# ── Output (shared) ───────────────────────────────────────────────────── #
if "p3_video_bytes" in st.session_state:
    if "p3_thumb_bytes" in st.session_state:
        st.image(st.session_state["p3_thumb_bytes"],
                 caption="First frame preview", use_container_width=True)
    p3_sz_mb = len(st.session_state["p3_video_bytes"]) / 1_048_576
    _video_desc = f"{_p3_h}p"
    if st.session_state.get("p3_add_captions"):
        _video_desc += " · Arabic captions"
    st.caption(f"Final video · {p3_sz_mb:.1f} MB · {_video_desc}")
    st.download_button(
        "⬇ Download Final Video (.mp4)",
        data=st.session_state["p3_video_bytes"],
        file_name="final_video.mp4",
        mime="video/mp4",
        use_container_width=True,
        key="p3_dl",
    )
    if "p3_review_zip" in st.session_state:
        _zmb = len(st.session_state["p3_review_zip"]) / 1_048_576
        st.download_button(
            f"⬇ Download Review dossier (.zip · {_zmb:.1f} MB)",
            data=st.session_state["p3_review_zip"],
            file_name="review_dir.zip",
            mime="application/zip",
            use_container_width=True,
            key="p3_dl_review",
            help="All captured candidates + decisions.json + plan + narration. "
                 "Edit and re-upload via the 'Rendering only' route.",
        )
    if st.session_state.get("p3_ro_log"):
        st.download_button(
            "⬇ Download render log (.txt)",
            data=st.session_state["p3_ro_log"].encode("utf-8"),
            file_name="render_log.txt",
            mime="text/plain",
            use_container_width=True,
            key="p3_dl_log",
            help="phase3 log of the Rendering-only run (sources, resolutions, "
                 "fallbacks) — useful for diagnosing image choices.",
        )
