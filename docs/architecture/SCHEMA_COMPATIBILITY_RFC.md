# Schema Compatibility RFC

**Status**: Batch A facade implemented, no import migration applied  
**Date**: 2026-06-11  
**Related**: `DIRECTORY_GOVERNANCE_RFC.md`  

## 1. Purpose

`backend/app/models` currently contains two different responsibilities:

- Pydantic request/response/domain schemas in `models/schemas.py`
- application exceptions in `models/exceptions.py`

The long-term directory shape should make this clearer, but directly renaming `app.models` would create broad import churn across API routers, services, tests, and docs. This RFC defines a compatibility-first migration path.

## 2. Goals

- Introduce clearer import targets without breaking existing imports.
- Keep API URLs, response schemas, DB records, task payloads, and serialized history unchanged.
- Allow new code to use `app.schemas` / `app.errors` while old code keeps working through `app.models.*`.
- Make the migration reviewable in small batches.

## 3. Non-goals

- Do not move `~/VoiceStudio`.
- Do not move project `models/` weights.
- Do not rename `mlx_indextts`.
- Do not update every import in one commit.
- Do not change Pydantic field names, aliases, defaults, or validation behavior.
- Do not remove `backend/app/models` in the 1.x line.

## 4. Proposed Compatibility Layer

Target shape:

```text
backend/app/
  schemas/
    __init__.py
    voice_studio.py
  errors/
    __init__.py
  models/
    __init__.py
    schemas.py       compatibility re-export
    exceptions.py    compatibility re-export
```

Recommended implementation target:

1. Move the implementation body of `models/schemas.py` to `schemas/voice_studio.py`.
2. Move the implementation body of `models/exceptions.py` to `errors/__init__.py`.
3. Leave `models/schemas.py` as:

```python
from app.schemas.voice_studio import *  # noqa: F403
```

4. Leave `models/exceptions.py` as:

```python
from app.errors import *  # noqa: F403
```

5. Do not change existing imports in API/services in the same batch.

This keeps the public Python import surface stable:

- `from app.models.schemas import GenerateRequest`
- `from app.models.exceptions import AppException`

while enabling new imports:

- `from app.schemas.voice_studio import GenerateRequest`
- `from app.errors import AppException`

## 5. Migration Batches

### Batch A: Compatibility Package

- Add `app.schemas` and `app.errors`.
- Keep `app.models.*` re-exports.
- Add tests proving old and new import paths point to the same classes.

Current status: completed as a facade-only compatibility layer. The implementation body still lives in `app.models.*`; `app.schemas`, `app.schemas.voice_studio`, and `app.errors` re-export existing classes to preserve identity and avoid a large move-only diff.

Validation:

```bash
.venv/bin/python -m compileall -q backend/app
.venv/bin/python -m pytest tests/test_schema_compatibility.py -q
```

### Batch B: Tests Import Migration

- Update tests to import new paths where practical.
- Keep production code unchanged.
- Run full backend tests.

Current status: completed for backend tests that do not intentionally assert old import compatibility. `tests/test_schema_compatibility.py` still imports `app.models.*` by design to prove the legacy facade remains stable.

Validation:

```bash
.venv/bin/python -m pytest tests/test_reference_features.py tests/test_task_orchestration_contract.py tests/test_mimo_cloud_contract.py tests/test_longform_queue.py tests/test_schema_compatibility.py tests/test_asr_tasks.py tests/test_voice_store_update.py tests/test_task_queue_stale.py tests/test_engine_parameter_contract.py -q
```

### Batch C: Service Import Migration

- Update service modules gradually.
- Do not mix with behavior changes.
- Run focused tests for queues, engine provider, and parameter contracts.

Current status: completed for `backend/app/services`. API routers and `backend/app/main.py` still use legacy imports pending Batch D.

Validation:

```bash
.venv/bin/python -m compileall -q backend/app
.venv/bin/python -m pytest tests/test_reference_features.py tests/test_task_orchestration_contract.py tests/test_engine_provider.py tests/test_engine_policy.py tests/test_engine_parameter_contract.py tests/test_longform_queue.py tests/test_asr_tasks.py tests/test_task_queue_stale.py tests/test_voice_store_update.py tests/test_mimo_cloud_contract.py -q
.venv/bin/python -m ruff check backend/app/services
```

### Batch D: API Import Migration

- Update FastAPI routers after services are stable.
- Run API contract tests.

Current status: completed for `backend/app/api` and `backend/app/main.py`.

Validation:

```bash
.venv/bin/python -m compileall -q backend/app
.venv/bin/python -m pytest tests/test_reference_features.py tests/test_longform_queue.py tests/test_mimo_cloud_contract.py tests/test_schema_compatibility.py tests/test_asr_tasks.py -q
.venv/bin/python -m ruff check backend/app/api backend/app/main.py
```

### Batch E: Deprecation Notice

- Keep `app.models.*` for the rest of 1.x.
- Add a short comment in compatibility files explaining they are stable re-export paths.
- Remove only in a later major cleanup after explicit approval.

Current status: completed. `app.models.*` remains the implementation source for 1.x compatibility; new code has migrated to `app.schemas.voice_studio` / `app.errors` outside compatibility tests and facade re-exports.

## 6. Acceptance Criteria

- Existing import paths still work.
- New import paths work.
- Class identity is preserved:

```python
from app.models.schemas import GenerateRequest as OldGenerateRequest
from app.schemas.voice_studio import GenerateRequest as NewGenerateRequest

assert OldGenerateRequest is NewGenerateRequest
```

- `/api/health`, `/api/engines`, `/api/generate`, `/api/tasks`, and `/api/history` response shapes do not change.
- No real data, audio, exports, DB, or model weights are moved.

## 7. Recommendation

Do not apply the compatibility package in the same release commit as versioning or WebUI polish. Treat it as the next backend-only batch after the v1.2.0 release boundary is tagged and verified.
