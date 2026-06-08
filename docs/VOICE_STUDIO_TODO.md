# Voice Studio TODO

Updated: 2026-06-08

## Current State

- Active branch: `main`
- Frontend dev server: `http://127.0.0.1:5173`
- Backend API: `http://127.0.0.1:8000/api`
- `codex/Soundpackage` has been merged into `main`.
- The generate page now loads from `/Users/foxmacstudio/Projects/mlx-indextts`.

## Completed

- Added local engine entries for EmotiVoice, F5-TTS, CosyVoice SFT, and CosyVoice Zero-Shot.
- Added engine-specific generate parameters and presets.
- Separated reference-voice engines from preset-speaker engines in the generate UI.
- Restored the voice dropdown to show all local voices and display existing library tags such as `仅测试`.
- Added F5/CosyVoice Zero-Shot reference text validation instead of silently running unusable requests.
- Added batch support for the new engines through the shared batch runner.
- Extended agent CLI parameters in `/Users/foxmacstudio/.cc-switch/skills/local-tts/scripts/generate.py`.
- Extended `scripts/voice_studio_batch.py` for model-specific batch parameters.
- Updated shared `local-tts` skill documentation and batch agent documentation.
- Added partial voice update support for `reference_text`, `quality_status`, `quality_notes`, and `favorite`.
- Added `scripts/backfill_voice_reference_text.py` to dry-run or apply local ASR reference-text backfill.
- Backfilled `reference_text` for all 48 voices that had reference audio but no reference text; ASR-derived lines are tracked with `quality_status` and review notes.
- Added voice-library review filtering, quality status chips, and a manual reviewed action for ASR-derived reference text.
- Added `/api/engines/{engine_id}/speakers` and EmotiVoice speaker search in the generate page, backed by the local official voice wiki README.
- Added longform parent metadata to segment tasks and history records for merged longform exports.
- Added a persistent F5-TTS worker process that reuses the external `F5TTS` model between requests; set `VOICE_STUDIO_F5_PERSISTENT_WORKER=0` to fall back to per-task isolation.
- Smoke-tested two real F5-TTS voice-clone generations with the same local reference voice; the second request reused the worker and completed quickly.
- Added a persistent CosyVoice worker process shared by SFT and Zero-Shot; set `VOICE_STUDIO_COSYVOICE_PERSISTENT_WORKER=0` to fall back to per-task isolation.
- Smoke-tested CosyVoice SFT and CosyVoice Zero-Shot on the persistent worker. Reuse avoids model reload, but CosyVoice inference itself remains slower than F5 on this machine.
- Merged the Soundpackage branch work into `main`.

## Verification

Run before merging or after significant edits:

```bash
pnpm --dir frontend check
.venv/bin/python -m compileall -q backend/app
.venv/bin/python -m pytest tests/test_longform_queue.py tests/test_reference_features.py tests/test_mimo_cloud_contract.py tests/test_task_queue_stale.py tests/integration/test_three_engines.py tests/test_voice_store_update.py -q
python3 /Users/foxmacstudio/.cc-switch/skills/local-tts/scripts/generate.py --check
python3 /Users/foxmacstudio/.cc-switch/skills/local-tts/scripts/generate.py --list-voices
python3 scripts/backfill_voice_reference_text.py --limit 5
```

Last known result:

- Frontend check: passed, 0 errors / 0 warnings.
- Backend compile: passed.
- Tests: 57 passed, 7 warnings.
- CLI health: `ok`.
- CLI voice list: 74 voices.
- F5 real smoke: two `f5-tts` tasks succeeded with voice `c27a673f6db5`; outputs `/tmp/voice-studio-f5-smoke-1.wav` and `/tmp/voice-studio-f5-smoke-2.wav` are both 24 kHz WAV files, about 3.7 seconds each.
- CosyVoice real smoke: `cosyvoice-sft` task `eeb3d1a94ee2` succeeded in 18.0s after worker warm-up, and `cosyvoice-zero-shot` task `cf9e34d08fef` succeeded in 20.9s using voice `c27a673f6db5`; outputs are 22.05 kHz WAV files.
- Reference text backfill: 48 updated, 0 skipped; local API currently has 0 voices with reference audio but empty `reference_text`, 40 voices still tagged `ASR待复核`, and 8 ASR-filled voices already marked `verified`.
- EmotiVoice speaker catalog: local README has 2,000+ speaker rows; API search and generate-page speaker filtering are available.

## Import Scripts

- `scripts/anime_voice_import.py`: HuggingFace streaming import for Genshin and Star Rail character voices with transcripts.
- `scripts/curated_voice_import.py`: curated 25-character HuggingFace import with transcripts and style tags.
- `scripts/voice_importer.py`: smaller early HuggingFace character import script; keep as a simple reference or migrate into `anime_voice_import.py` later.
- `scripts/batch_voice_import.py`: curated local desktop voice-material import.
- `scripts/full_voice_import.py`: broad local desktop voice-material import.

Follow-up cleanup:

- Prefer `anime_voice_import.py` for transcript-bearing character voices.
- Prefer `curated_voice_import.py` when a small high-signal subset is enough.
- Prefer `batch_voice_import.py` for curated local material.
- Treat `full_voice_import.py` as an explicit bulk tool only; it can import many voices and should not be run casually.

## Remaining Work

### 1. Reference Text Review

Imported character voices now have ASR-derived `reference_text`, so F5-TTS and CosyVoice Zero-Shot can use them as reference voices. The remaining quality work is human review: ASR can mishear names, particles, or stylized character lines.

Recommended path:

- Use the voice-library `复核` filter to show `ASR待复核` voices.
- Listen to the reference audio and compare it with the `台词` chip text.
- Click `已复核` only after the reference text is manually confirmed.

### 2. Persistent Workers

F5-TTS and CosyVoice now have persistent external workers that reuse loaded models between requests while preserving timeout/cancel reset behavior. CosyVoice SFT and Zero-Shot share one `AutoModel` worker; the model stays loaded, but generation is still relatively slow on local MPS.

Recommended path:

- Add a visible worker status/reset control after the first few field tests.
- Track warm/cold generation time per engine in the task UI so slow inference is easier to distinguish from model loading.

### 3. Voice Quality Evaluation

Smoke tests prove generation works, but there is no user-facing comparison flow.

Recommended path:

- Add a small evaluation view with reference playback, generated playback, parameters, and notes.
- Record subjective rating and failure reason per generated result.
- Optionally add ASR text coverage checks for long narration quality.

### 4. EmotiVoice Speaker Catalog

The full local EmotiVoice speaker catalog is exposed through a searchable API and the generate page. The parameter schema still keeps a compact default subset, so the page stays fast before searching.

Recommended path:

- Audition promising speaker IDs and promote the best 5-10 into built-in presets.
- Keep the full catalog searchable rather than loading all 2,000+ rows into the default dropdown.

### 5. Agent-Facing Usage Notes

Backend and agent instructions now cover the new engines at a high level. The remaining polish is to keep examples current as real presets and worker behavior change.

Recommended path:

- Add one short example per engine after reference-text backfill is verified on real voices.
- Keep external shared skill changes noted separately because `/Users/foxmacstudio/.cc-switch/skills/local-tts` is outside this repository.
