# Changelog

## 1.2.0 - 2026-06-11

### Backend stability

- Stabilized task orchestration semantics for single, longform, and batch generation.
- Added regression coverage for cancellation, retry, restart recovery, partial success, and cloud idempotency behavior.
- Split engine responsibilities into policy, manifests, health, request builder, runner, and provider facade modules while preserving API response shape.
- Hardened persistent worker error context and parameter consistency tests for supported TTS paths.
- Added dry-run Voice Studio data audit manifest tooling.

### WebUI

- Stabilized the generate results panel layout, scrolling, toolbar responsiveness, queue status hints, card headers, action placement, and audio controls.
- Constrained long error messages inside result cards with internal scrolling and fade overflow.
- Added delayed, unified tooltips for icon-only controls in the generate workflow.

### CI and verification

- Fixed CI dependency setup for async pytest usage.
- Fixed Ruff regex escape failures.
- Verified frontend checks/builds and GitHub Actions for the stabilization commits.

### Notes

- No real `~/VoiceStudio` data, local model weights, or generated audio assets are moved by this release.
- `frontend/README.md` remains untracked because it appears to be the default Svelte template README and has not been accepted as project documentation.

## 1.1.0 - 2026-06-11

- Baseline Voice Studio stabilization tag before the post-`v1.1.0` backend architecture and generate WebUI hardening batches.
