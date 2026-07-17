# Video Localization Domain

This package owns the backend business rules for source-video transcription and Chinese subtitle/dubbing localization. The persisted `english_asr` operation name remains a v1 compatibility identifier; new queued work carries an explicit source language and defaults to automatic English/Chinese detection.

## Layers

- `service.py`: thin application facade used by API routes, task queues, and legacy callers. It checks project existence, loads drafts, and delegates to domain modules.
- `draft_store.py`: project-parameter persistence for the `video_localization` draft plus quality-gate refresh before save/export.
- `schemas.py`: domain schema facade. It re-exports the current stable Pydantic models so domain code has one import boundary while preserving class identity.
- `source_pipeline.py`: atomic source media import, source-audio extraction, stem separation, and source-language ASR draft generation. Replacing source media invalidates the detected language together with the old transcription.
- `cues.py`: ASR cue creation, cue patch validation, and cue quality flags.
- `speakers.py`: speaker roster creation/update plus cue/reference-derived speaker timeline reconciliation.
- `localization.py`: Chinese subtitle draft and TTS-readable text draft rules, including semantic-aware source-word boundary mapping for localized timing.
- `boundary_review.py`: sparse LLM review of ambiguous ASR subtitle boundaries. Structured DeepSeek calls disable reasoning output so JSON capacity is reserved for decisions instead of hidden thought tokens.
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
