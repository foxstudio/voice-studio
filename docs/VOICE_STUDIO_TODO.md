# Voice Studio TODO

Updated: 2026-06-08

## Current State

- Active branch: `codex/Soundpackage`
- Frontend dev server: `http://127.0.0.1:5173`
- Backend API: `http://127.0.0.1:8000/api`
- `main` and `codex/webui` are merged into `codex/Soundpackage`.
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

## Verification

Run before merging or after significant edits:

```bash
pnpm --dir frontend check
.venv/bin/python -m compileall -q backend/app
.venv/bin/python -m pytest tests/test_reference_features.py tests/test_mimo_cloud_contract.py tests/test_task_queue_stale.py tests/integration/test_three_engines.py -q
python3 /Users/foxmacstudio/.cc-switch/skills/local-tts/scripts/generate.py --check
python3 /Users/foxmacstudio/.cc-switch/skills/local-tts/scripts/generate.py --list-voices
```

Last known result:

- Frontend check: passed, 0 errors / 0 warnings.
- Backend compile: passed.
- Tests: 47 passed, 5 warnings.
- CLI health: `ok`.
- CLI voice list: 74 voices.

## Import Scripts

- `scripts/anime_voice_import.py`: HuggingFace streaming import for Genshin and Star Rail character voices with transcripts.
- `scripts/voice_importer.py`: smaller early HuggingFace character import script; keep as a simple reference or migrate into `anime_voice_import.py` later.
- `scripts/batch_voice_import.py`: curated local desktop voice-material import.
- `scripts/full_voice_import.py`: broad local desktop voice-material import.

Follow-up cleanup:

- Prefer `anime_voice_import.py` for transcript-bearing character voices.
- Prefer `batch_voice_import.py` for curated local material.
- Treat `full_voice_import.py` as an explicit bulk tool only; it can import many voices and should not be run casually.

## Remaining Work

### 1. Reference Text Backfill

Many imported character voices have reference audio but empty `reference_text`. F5-TTS and CosyVoice Zero-Shot require accurate reference text. This is the main quality blocker.

Recommended path:

- Add a voice-library action or script to transcribe selected reference audio with local ASR.
- Store the transcript back into `VoiceAsset.reference_text`.
- Mark uncertain transcripts in `quality_notes` instead of pretending they are exact.

### 2. Persistent Workers

F5-TTS and CosyVoice currently load their model per task. This is correct but slow.

Recommended path:

- Add persistent worker processes or an in-process model cache for external engines.
- Start with F5-TTS because its API wrapper is simpler.
- Keep per-engine lock behavior until concurrency is proven stable on MPS.

### 3. Voice Quality Evaluation

Smoke tests prove generation works, but there is no user-facing comparison flow.

Recommended path:

- Add a small evaluation view with reference playback, generated playback, parameters, and notes.
- Record subjective rating and failure reason per generated result.
- Optionally add ASR text coverage checks for long narration quality.

### 4. EmotiVoice Speaker Catalog

Only a curated subset of official EmotiVoice speakers is exposed in the parameter schema.

Recommended path:

- Add a searchable speaker catalog/import UI if the full catalog becomes useful.
- Keep the default parameter dropdown compact.

### 5. Commit And Merge Hygiene

After verification:

- Commit the current branch.
- Merge `codex/Soundpackage` into `main`.
- Keep external shared skill changes noted separately because `/Users/foxmacstudio/.cc-switch/skills/local-tts` is outside this repository.
