# Video Localization Domain

This package owns the backend business rules for English video to Chinese subtitle and dubbing localization.

## Layers

- `service.py`: thin application facade used by API routes, task queues, and legacy callers. It checks project existence, loads drafts, and delegates to domain modules.
- `draft_store.py`: project-parameter persistence for the `video_localization` draft plus quality-gate refresh before save/export.
- `schemas.py`: domain schema facade. It re-exports the current stable Pydantic models so domain code has one import boundary while preserving class identity.
- `source_pipeline.py`: source media import, source-audio extraction, stem separation, and English ASR draft generation.
- `cues.py`: ASR cue creation, cue patch validation, and cue quality flags.
- `localization.py`: Chinese subtitle draft and TTS-readable text draft rules.
- `reference_clips.py`: clean-vocal reference clip candidates and manual reference review updates.
- `tts_pipeline.py`: pure cue-level TTS request/result mapping.
- `tts_orchestration.py`: adapter between this domain and the shared batch TTS queue.
- `audio_access.py`: downloadable/cached audio file lookup for generated TTS, reference clips, and source cue clips.
- `operation_queue.py`: worker/thread lifecycle and async operation execution.
- `operation_state.py`: async operation state machine rules, prerequisites, metadata status updates, and result summaries.
- `quality_gate.py`: production blockers/warnings for draft save and export readiness.
- `readiness.py`: reader-facing production readiness JSON.
- `subtitles.py`: SRT export formatting.
- `media_assets.py`: filesystem and ffmpeg/demucs media operations.
- `exporting.py`: SRT, production JSON, and readiness export orchestration.

## Boundary Rules

- Keep feature-specific business rules inside this package, not in `backend/app/api`.
- Keep API routes as transport glue: request/response shape, attachment responses, and error mapping only.
- Keep shared TTS queue behavior in existing shared services; this package should adapt to it through `tts_orchestration.py`.
- Keep media filesystem operations in `media_assets.py`; business modules should request intent-level operations from it.
- New domain modules should import schemas from `app.domains.video_localization.schemas`, not directly from `app.schemas.voice_studio`.
