# Phase 3 — Issue #5 drop · Online/offline asset review

Five files in this zip.  Two are new, two are patches, one is an
updated `Phase3.md`.

## New files

| Source in zip | Destination in repo |
|---|---|
| `decisions.py` | `_Phase3/phase3/sources/decisions.py` |
| `prebuild_assets.py` | `_Phase3/prebuild_assets.py` |

## Patched files

| Source in zip | Destination in repo | Diff |
|---|---|---|
| `sources_init.py` | `_Phase3/phase3/sources/__init__.py` | `sources_init.diff` |
| `render_plan.py` | `_Phase3/render_plan.py` | `render_plan.diff` |

`Phase3.md` is the updated handoff document — it now carries the
five-issue checklist (issues 1–4 open, issue 5 closed) and a new §15
with full implementation notes.

## Quick verification

After dropping the files in:

```bash
cd _Phase3
python -c "from phase3.sources import FetcherConfig; print('review_dir' in FetcherConfig.__dataclass_fields__)"
# Should print: True

python prebuild_assets.py --help
# Should show the new CLI's full help text
```

## End-to-end workflow

```bash
# 1. Plan as before
python phase3_run.py --plan-only \
    --script samples/al_askari_script.txt \
    --audio  output/al_askari_audio.mp3 \
    --save-plan output/al_askari_plan_v2.json \
    --book-title "مذكرات جعفر العسكري" \
    --character-name "Jafar al-Askari"

# 2. NEW: pre-fetch + score all candidates, write the dossier
python prebuild_assets.py \
    --plan          output/al_askari_plan_v2.json \
    --script        samples/al_askari_script.txt \
    --book-title    "مذكرات جعفر العسكري" \
    --character-name "Jafar al-Askari" \
    --anthropic-key "$ANTHROPIC_API_KEY" \
    --pexels-key    "$PEXELS_API_KEY" \
    --review-dir    output/review/ \
    --character-portrait /path/to/jafar.jpg

# 3. Review output/review/ and edit decisions.json as needed.

# 4. Render with the dossier
python render_plan.py \
    --plan       output/al_askari_plan_v2.json \
    --audio      output/al_askari_audio.mp3 \
    --review-dir output/review/ \
    --output     output/final_cut.mp4 \
    --anthropic-key "$ANTHROPIC_API_KEY" \
    --pexels-key    "$PEXELS_API_KEY" \
    --book-title    "مذكرات جعفر العسكري" \
    --character-name "Jafar al-Askari"
```

The `--review-dir` flag tells the renderer to consult the dossier
*before* doing any source query.  Shots without a dossier entry fall
through to the live waterfall as before — no breaking change to
existing workflows.

## Backward compatibility

- All previous `render_plan.py` flags still work.
- `--review-dir` is optional.  When omitted, behaviour is identical
  to the previous version.
- `FetcherConfig.review_dir` defaults to `None`.
