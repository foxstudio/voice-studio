# Engine Provider / Policy RFC

**Status**: Phase 4 in progress  
**Date**: 2026-06-11  
**Scope**: 后端引擎抽象、能力声明、health、runner selection、参数构建边界。  

## 1. Why

`backend/app/services/engine_registry.py` 当前同时承担了五类职责：

1. 静态 engine manifest 与 parameter schema。
2. engine alias 与 capabilities。
3. health check / runtime root 检查。
4. start / stop / ensure loaded 状态管理。
5. runner selection 与外部 worker 调用分派。

这让新增 engine、修参数、改 health、改 runner 生命周期都集中到一个文件，回归面过大。Phase 4 的目标不是重写引擎系统，而是把这些职责逐步拆成可测试的 policy/provider 层。

## 2. Non-goals

- 不改现有 API URL。
- 不移动 `~/VoiceStudio` 真实数据。
- 不移动项目内 `models/` 权重目录。
- 不改 `mlx_indextts` 包名。
- 不一次性删除 `engine_registry.py`。
- 不把 MiMo preset / voicedesign / voiceclone 合成一个混合入口。
- 不要求前端一次性改完所有硬编码 engine 判断。

## 3. Target Concepts

### 3.1 Engine Provider

每个 provider 负责一个 engine family 的行为边界：

- `IndexTTSProvider`
- `OmniVoiceProvider`
- `EmotiVoiceProvider`
- `F5Provider`
- `CosyVoiceProvider`
- `MiMoProvider`
- `QwenAsrProvider`

Provider 不应直接管理全局队列。Provider 只提供 engine 事实与执行策略。

### 3.2 Engine Manifest

Manifest 是面向 API / UI 的静态事实：

- `engine_id`
- `display_name`
- `engine_type`
- `provider`
- `version`
- `description`
- `supported_languages`
- `capabilities`
- `privacy_level`
- `parameter_schema`

当前 `_ENGINES` 中的 manifest 可以先原样迁出，保持 response shape 不变。

### 3.3 Engine Policy

Policy 是运行时决策事实：

- `execution_timeout_sec`
- `postprocessing_timeout_sec`
- `stale_grace_sec`
- `runner_kind`
- `requires_reference_audio`
- `requires_reference_text`
- `cloud_idempotency_required`
- `supports_persistent_worker`
- `supports_batch`
- `supports_longform`

Phase 4 初始 policy 可以只覆盖 timeout / runner / cloud idempotency，不追求一次性完整。

### 3.4 Request Builder

Request builder 负责把 `GenerateRequest` / `BatchGenerateRequest` 转成 runner kwargs。

当前分散位置：

- `task_queue._kwargs`
- `batch_queue._common_kwargs`
- `batch_queue._runner_segments`
- `backend/app/api/engines.py` diagnosis kwargs

目标是逐步收敛到 engine-specific builder，但必须保持当前参数契约测试通过。

### 3.5 Runner Selection

Runner selection 决定执行方式：

- in-process local package
- subprocess isolated runner
- persistent worker
- cloud client

当前在 `engine_registry.run_isolated` 中混合处理。Phase 4 只先引入可测试的 selection helper，不直接重写所有 runner。

## 4. Migration Plan

### Batch 0: RFC and Safety Baseline

已由本文完成。进入代码改造前必须确认：

- `.venv/bin/python -m pytest tests -q` 通过。
- `pnpm --dir frontend check` 通过。
- manifest dry-run 不修改真实数据。

### Batch 1: Add Read-only Policy Helper

新增小文件，例如：

- `backend/app/services/engine_policy.py`

只提供只读函数：

- `resolve_engine_id(engine_id)`
- `is_cloud_engine(engine_id)`
- `is_mimo_tts(engine_id)`
- `timeout_seconds_for(engine_id)`
- `runner_kind_for(engine_id)`
- `requires_idempotency_marker(engine_id)`

约束：

- 不迁移 manifest。
- 不改 API response。
- 不改 runner 行为。
- `task_queue` 可以从 helper 读取 MiMo 判断和 timeout，但必须保持测试通过。

验收：

- 新增 `tests/test_engine_policy.py`。
- `tests/test_task_orchestration_contract.py` 仍全绿。

Current status: completed. `task_queue` now reads MiMo and timeout decisions from `engine_policy` while keeping public behavior unchanged.

### Batch 2: Move Engine Manifests Behind Adapter

新增 adapter，但保留 `engine_registry.list_engines()` / `get_engine()` 外部函数。

建议路径：

- `backend/app/services/engine_manifests.py`

迁移内容：

- `_EMOTION_OPTIONS`
- `_EMOTIVOICE_PROMPTS`
- `_COSYVOICE_SPEAKERS`
- `_ENGINES` 静态声明

约束：

- `engine_registry.py` 继续 re-export 当前 public functions。
- API response schema 不变。
- 不改前端。

验收：

- `tests/test_reference_features.py`
- `tests/test_mimo_cloud_contract.py`
- `tests/integration/test_three_engines.py`

Current status: completed. Static engine manifests and speaker/catalog constants now live in `engine_manifests.py`; `engine_registry.list_engines()` / `get_engine()` / `list_speakers()` keep the same public entry points and response shape.

### Batch 3: Health Check Strategy Split

把 health check 拆成策略函数：

- local model health
- external runtime health
- cloud configured health
- ASR runtime health

约束：

- `engine_registry.health_check(engine_id)` 仍保持入口。
- 不启动真实模型。
- 外部 root env 缺失仍返回/抛出当前语义，不静默 fallback。

Current status: completed. Health behavior now lives in `engine_health.py`; `engine_registry.health_check()` remains a compatibility facade.

### Batch 4: Request Builder Adapter

把参数构建从队列层抽到 builder，但先只做一个 engine：

优先顺序：

1. MiMo TTS，因为 cloud/idempotency 风险最高。
2. F5/CosyVoice，因为 worker 参数契约已有测试。
3. IndexTTS v2。

约束：

- 每批只迁一个 engine family。
- `tests/test_engine_parameter_contract.py` 必须全绿。
- 不修改前端参数名。

Current status: Batch 4A/4B/4C/4D completed. `engine_request_builder.py` now owns kwargs construction for all current TTS engine families: MiMo preset / voicedesign / voiceclone, F5, CosyVoice zero-shot, IndexTTS v2, OmniVoice, EmotiVoice, and CosyVoice SFT. Existing runner field compatibility, queue-level reference validation, OmniVoice auto-transcription avoidance, and single-vs-batch field differences are preserved.

### Batch 5: Runner Adapter Split

把 `engine_registry.run_isolated` 中的 persistent worker / isolated subprocess execution 抽到 runner adapter：

- persistent worker enabled?
- external root required?
- isolated subprocess fallback?
- worker shutdown?

约束：

- `engine_registry.run_isolated()` 仍保持入口。
- 不改变 persistent worker env 开关。
- 不改变 subprocess timeout / cancel / stderr 语义。
- F5/CosyVoice protocol tests 必须继续通过。

Current status: completed. Execution now lives in `engine_runner.py`; `engine_registry.run_isolated()` and `shutdown_workers()` delegate to it.

### Batch 6: Provider Skeleton

新增轻量 provider object，只组合现有模块，不改变 API response：

- manifest/detail from `engine_manifests`
- runtime policy from `engine_policy`
- health from `engine_health`
- request building remains in `engine_request_builder`

约束：

- `engine_registry.py` 继续作为 public facade。
- provider skeleton 不暴露本地路径、API key 或内部策略对象到 API。
- 不做目录迁移。

Current status: Batch 6A completed. `engine_provider.py` provides `EngineProvider`, provider lookup, legacy MiMo alias resolution, and detail listing. `engine_registry.list_engines()` / `get_engine()` / `health_check()` now delegate through the provider facade while preserving response shape.

## 5. Frontend Consumption Path

前端不要直接依赖 provider 内部对象。逐步消费现有 `/api/engines` response：

1. 继续使用 `manifest.capabilities`。
2. 增加后端 policy 字段前，先不改 response schema。
3. 若未来要暴露 policy，只暴露 UI 安全字段：
   - `requires_reference_audio`
   - `requires_reference_text`
   - `cloud_required`
   - `supports_batch`
   - `supports_longform`
4. 不暴露本地路径、API key、模型权重路径细节。

## 6. Compatibility Rules

- `mimo-v2.5-tts` legacy alias 继续解析到 `mimo-v2.5-tts-preset`，但不出现在 engine list。
- `mimo-v2.5-tts-preset` / `voicedesign` / `voiceclone` 保持独立 manifest。
- `f5-tts` 和 `cosyvoice-*` 的 persistent worker env 开关保持兼容：
  - `VOICE_STUDIO_F5_PERSISTENT_WORKER`
  - `VOICE_STUDIO_COSYVOICE_PERSISTENT_WORKER`
- 外部 engine root env 缺失必须显式失败：
  - `VOICE_STUDIO_F5_TTS_ROOT`
  - `VOICE_STUDIO_COSYVOICE_ROOT`
  - `VOICE_STUDIO_EMOTIVOICE_ROOT`

## 7. Current Safety Baseline

Current backend validation after Phase 4 manifest / health / runner / request builder split:

- `.venv/bin/python -m compileall -q backend/app/services/engine_registry.py backend/app/services/engine_runner.py backend/app/services/engine_health.py backend/app/services/engine_manifests.py`: passed
- `.venv/bin/python -m pytest tests/test_reference_features.py tests/test_mimo_cloud_contract.py tests/integration/test_three_engines.py tests/test_engine_policy.py tests/test_persistent_worker_protocol.py -q`: `59 passed, 3 warnings`
- `.venv/bin/python -m compileall -q backend/app/services/engine_request_builder.py backend/app/services/task_queue.py backend/app/services/batch_queue.py tests/test_engine_parameter_contract.py`: passed
- `.venv/bin/python -m pytest tests/test_engine_parameter_contract.py tests/test_task_orchestration_contract.py tests/test_mimo_cloud_contract.py tests/test_engine_policy.py -q`: `41 passed, 1 warning`
- `.venv/bin/python -m pytest tests/test_engine_parameter_contract.py tests/test_reference_features.py tests/test_persistent_worker_protocol.py tests/test_task_orchestration_contract.py -q`: `54 passed, 1 warning`
- `.venv/bin/python -m pytest tests/test_engine_parameter_contract.py tests/test_task_orchestration_contract.py tests/test_generate.py tests/test_models_v2.py -q`: `51 passed, 2 warnings`
- `.venv/bin/python -m pytest tests/test_engine_parameter_contract.py tests/test_task_orchestration_contract.py tests/test_reference_features.py tests/test_generate.py tests/test_text_planner.py -q`: `60 passed, 3 warnings`
- `.venv/bin/python -m pytest tests/test_engine_provider.py tests/test_engine_policy.py tests/test_mimo_cloud_contract.py tests/integration/test_three_engines.py -q`: `31 passed, 3 warnings`
- Full suite should be rerun after any future queue or engine-family changes.

Remaining warnings are third-party/runtime warnings:

- Starlette / httpx TestClient compatibility warning.
- SWIG MLX type deprecation warnings.

## 8. Next Recommended Batch

Implement **Phase 4 next backend batch: directory governance RFC or provider integration review**.

Recommended order: pause implementation-heavy provider refactoring until the current backend split is reviewed. The next safe work is documentation and import-boundary review before any directory migration.
