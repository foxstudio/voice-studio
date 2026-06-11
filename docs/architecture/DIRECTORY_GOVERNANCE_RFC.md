# Directory Governance RFC

**Status**: proposed, no migration applied  
**Date**: 2026-06-11  
**Scope**: backend package boundaries, docs naming, scripts grouping, compatibility strategy.  

## 1. Current Findings

The project is now easier to evolve after the Phase 4 backend split, but directory boundaries are still uneven:

- `backend/app/models` contains both Pydantic schemas and exceptions.
- `backend/app/services` contains stores, queues, runners, engine policy, engine manifests, health, providers, and request builders.
- `docs/models` describes TTS/ASR engines, not backend data models.
- `scripts` mixes import utilities, maintenance scripts, evaluation tools, dev smoke scripts, and generated JSON reports.

Current import usage is broad:

- API modules import `app.models.schemas` and `app.models.exceptions`.
- Service modules import `app.models.schemas` and `app.models.exceptions`.
- Tests import `app.models.schemas` directly.
- Docs reference `backend/app/models/schemas.py`.

That means a direct rename of `backend/app/models` would be high churn and should not be done without a compatibility layer.

## 2. Non-goals

- Do not move `~/VoiceStudio`.
- Do not move project `models/` weights.
- Do not rename the `mlx_indextts` package.
- Do not change API URLs, DB schema, or serialized task payloads.
- Do not run broad formatter-only churn.
- Do not move directories and update imports in the same batch as behavior changes.

## 3. Recommended Package Boundaries

### 3.1 Backend

Recommended long-term shape:

```text
backend/app/
  api/                 FastAPI routers only
  schemas/             Pydantic request/response/domain schemas
  errors/              AppException and HTTP error types
  services/            orchestration, stores, queues, engine adapters
  services/engines/    future home for provider-specific engine modules
```

Current safe strategy:

1. Keep `backend/app/models` in place for now.
2. If migrating later, create `backend/app/schemas` and `backend/app/errors`.
3. Leave compatibility re-exports:
   - `backend/app/models/schemas.py` re-exports from `app.schemas`.
   - `backend/app/models/exceptions.py` re-exports from `app.errors`.
4. Update imports gradually by layer:
   - new code imports from `app.schemas` / `app.errors`.
   - old code keeps working through `app.models.*`.

### 3.2 Services

Current Phase 4 split created useful internal boundaries:

- `engine_manifests.py`: public manifest/catalog facts.
- `engine_policy.py`: runtime policy facts.
- `engine_health.py`: health/runtime checks.
- `engine_request_builder.py`: runner kwargs construction.
- `engine_runner.py`: subprocess / persistent worker execution.
- `engine_provider.py`: thin composition facade.
- `engine_registry.py`: public compatibility facade.

Do not migrate these into `services/engines/` yet. First stabilize imports and tests for one release boundary.

### 3.3 Docs

`docs/models` has been migrated to `docs/engines`, because the files describe engine behavior:

- `indextts-v2.md`
- `omnivoice.md`
- `emotivoice.md`
- `f5-tts.md`
- `cosyvoice.md`
- `mimo-v2.5.md`
- `qwen3-asr-mlx.md`

Completed migration:

1. Added `docs/engines`.
2. Moved engine docs from `docs/models` to `docs/engines`.
3. Left a short `docs/models/README.md` redirect for one transition period.

### 3.4 Scripts

Recommended grouping:

```text
scripts/
  imports/       voice import and corpus ingestion
  maintenance/   backfill, cleanup, migration, data audit
  evaluation/    quality suites, alignment tests, deep eval
  dev/           smoke scripts and local debugging helpers
  reports/       generated JSON reports, not source logic
```

Candidate mapping:

- `imports/`: `anime_voice_import.py`, `batch_voice_import.py`, `curated_voice_import.py`, `full_voice_import.py`, `genshin_batch_import.py`, `genshin_npc_import.py`, `voice_importer.py`, `batch_import_local.py`
- `maintenance/`: `backfill_voice_reference_text.py`, `genshin_cleanup_refs.py`, `genshin_ref_text_check.py`, `genshin_reorder_refs.py`, `replace_short_refs.py`, `migration/audit_voice_studio_data.py`
- `evaluation/`: `alignment_test.py`, `alignment_test_v2.py`, `dump_pytorch_outputs.py`, `dump_pytorch_outputs_v2.py`, `run_voice_studio_deep_eval.py`, `run_voice_studio_quality_suite.py`, `verify_mlx_v2.py`
- `dev/`: `webui_smoke_playwright.mjs`, `qwen_forced_align_worker.py`, `genshin_asr_check.py`, `genshin_asr_fix.py`, `analyze_genshin_pack.py`
- `reports/`: `genshin_analysis.json`, `genshin_import_report.json`, `genshin_npc_import_report.json`, `import_report.json`

Do not move scripts until their hard-coded paths and docs references are reviewed. Several scripts contain absolute project paths.

## 4. Migration Batches

### Batch A: Documentation-only Boundaries

- Land this RFC.
- Do not move files.
- Validate:
  - `.venv/bin/python -m compileall -q backend/app`
  - selected backend tests.

### Batch B: Docs Engines Rename

- Move `docs/models` to `docs/engines`.
- Update README and docs references.
- Add `docs/models/README.md` redirect if needed.
- No backend code changes.

Current status: completed. Engine docs now live in `docs/engines`; `docs/models/README.md` remains as a compatibility note.

### Batch C: Script Grouping Dry-run

- Add a script inventory manifest listing source, target group, and references.
- Do not move scripts.
- Flag scripts with absolute paths.

### Batch D: Script Grouping Migration

- Move only scripts that have no absolute path or have been patched safely.
- Update docs and README.
- Keep generated JSON reports under `scripts/reports` or move them to ignored artifacts after review.

### Batch E: Schema Package Compatibility Layer

- Add `backend/app/schemas` and/or `backend/app/errors`.
- Re-export through `backend/app/models`.
- Do not update all imports in the same batch.

### Batch F: Gradual Import Migration

- Update imports by layer:
  1. tests
  2. services
  3. api
- Run full backend tests after each layer.

## 5. Acceptance Criteria

- No real data, audio, exports, DB, or model weights are moved.
- `backend/app/models` imports remain compatible until explicitly removed in a later major cleanup.
- API response schemas are unchanged.
- README, docs, scripts, and workflows do not point to missing paths.
- Full backend tests pass after any code import migration.

## 6. Recommendation

Do not perform directory migration immediately. The backend has just been split into clearer modules; the next safest step is to let this settle, then do docs-only renames and script inventory before touching Python package paths.
