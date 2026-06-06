# Three Engines Complete — IndexTTS v1/v2 + OmniVoice

## TL;DR

> **Quick Summary**: 补齐 IndexTTS v1 和 OmniVoice 引擎适配器，重构 engine_registry 为 PRD 合规的 Engine Adapter 架构，前端加引擎选择器。全部走国内镜像，先验证 PyTorch/MLX 共存再动手。
>
> **Deliverables**:
> - IndexTTS v1 MLX 适配器（下载模型 + 推理集成）
> - OmniVoice PyTorch MPS 适配器（pip install + 推理集成）
> - engine_registry 重构为策略模式（PRD §8 Engine Adapter 规范）
> - schemas.py 扩展 engine_version 支持 3 引擎
> - 前端引擎选择下拉框（能力驱动 UI）
> - 三引擎端到端验证 + 集成测试
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: T1(risk check) → T3(model dl) → T5(v1 adapter) → T8(registry refactor) → T10(frontend selector) → T12(integration) → F1-F4

---

## Context

### Original Request
用户发现 boulder 001 验收不诚实：仅 IndexTTS v2 真正工作，v1 和 OmniVoice 的 manifest 声明 running 但实际无法启动。用户选择方案 D（A+B+C 全做），并要求先规划、风险评估、再动手。

### Interview Summary
**Key Discussions**:
- 方案 D：A（诚实降级 manifest）+ B（接 IndexTTS v1）+ C（接 OmniVoice）
- OmniVoice 使用官方 pip + PyTorch MPS 推理（不转 MLX）
- IndexTTS v1 优先用预转换 MLX 版 mlx-community/IndexTTS-1.5
- 风险 R1（PyTorch vs MLX 冲突）是唯一阻断项，必须先验证
- PRD v2.0 全文审阅完成，关键架构约束已提取并融入计划

**Research Findings**:
- OmniVoice = 小米 k2-fsa 团队，600+ 语种，sample_rate=24000
- IndexTTS v1 推理 API：IndexTTS.load_model() → tts.infer(audio_prompt, text, output_path)，sample_rate=24000
- MLX 社区预转换版：mlx-community/IndexTTS-1.5
- 三引擎 sample_rate 差异：v1=24000, v2=22050, OmniVoice=24000
- PRD 要求 "新增模型不得改动主页面结构"、"能力驱动 UI"、"架构一步到位"

### Risk Assessment (Pre-Plan)

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | PyTorch deps 与 MLX 冲突（numpy/torch 版本不兼容） | CRITICAL | T1 uv add omnivoice --dry-run 验证，失败则虚拟环境隔离 |
| R2 | OmniVoice MPS 推理慢于 MLX | MEDIUM | 接受并 benchmark，后续可优化 |
| R3 | IndexTTS v1 MLX 转换后推理异常 | MEDIUM | 用 mlx-community 预转换版，跳过自行转换 |
| R4 | 三引擎 sample_rate 不一致 | MEDIUM | manifest 声明各自 sample_rate，后端统一处理 |
| R5 | OmniVoice 首次加载慢（Qwen3-0.6B backbone） | LOW | 异步加载 + 进度反馈 |
| R6 | OmniVoice ref_text 需要额外步骤 | LOW | 可选字段，留空则跳过 |
| R7 | 前端参数差异大（v2 有 emotion，omnivoice 有 voice_mode） | LOW | 能力驱动 UI，按 manifest capabilities 动态渲染 |

---

## Work Objectives

### Core Objective
补齐 IndexTTS v1 和 OmniVoice 两个引擎的完整端到端链路，重构 engine_registry 为 PRD §8 合规的 Engine Adapter 架构，前端实现能力驱动的引擎选择器。

### Concrete Deliverables
- backend/app/services/adapters/ 目录：v1_adapter.py, omnivoice_adapter.py
- backend/app/services/engine_registry.py 重构为策略模式
- backend/app/models/schemas.py engine_version 扩展
- frontend/src/routes/generate/+page.svelte 引擎选择器
- models/indexTTS-1.5/ v1 模型文件
- .omo/evidence/ 全部 QA 证据

### Definition of Done
- [ ] curl /api/engines 返回 3 个引擎，status 均为 available/running
- [ ] curl /api/generate -d '{"text":"你好","engine":"indextts-v1"}' 返回有效音频
- [ ] curl /api/generate -d '{"text":"Hello","engine":"omnivoice"}' 返回有效音频
- [ ] curl /api/generate -d '{"text":"你好","engine":"indextts"}' 仍正常（v2 回归）
- [ ] 前端下拉框切换 3 引擎，参数面板随引擎变化
- [ ] 所有音频 sample_rate/duration/peak_amplitude 断言通过

### Must Have
- 三引擎端到端推理全部工作
- PRD §8 Engine Adapter 规范合规（manifest + capabilities + health_check）
- 能力驱动 UI（参数面板按 manifest 动态渲染）
- 模型下载走国内镜像（hf-mirror.com）
- 音频质量断言（sample_rate、duration>500ms、peak>0.01）
- Git 状态干净，wave squash 提交

### Must NOT Have (Guardrails)
- 不改动 v2 已有的推理逻辑（回归保护）
- 不添加 Script Studio / Task Queue / History 等 PRD P0+ 模块（本 boulder 仅做引擎层）
- 不做 MLX OmniVoice 转换（直接用 PyTorch MPS）
- 不向远端推送（无 remote）
- 不在 T1（风险检查）通过前做任何代码改动
- AI slop：不过度抽象、不加无用注释、不创建空文件
- 不破坏现有 API 路由（向后兼容）

---

## Verification Strategy (MANDATORY)

> ZERO HUMAN INTERVENTION - ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES（pytest 已配置）
- **Automated tests**: Tests-after（本 boulder 以集成验证为主）
- **Framework**: pytest

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to .omo/evidence/task-{N}-{scenario-slug}.{ext}.

- **Backend/API**: Bash (curl) - Send requests, assert status + response fields
- **Frontend/UI**: Bash (curl) + Playwright - Check API, verify DOM elements
- **Model/Inference**: Bash (python) - Run inference, validate audio output

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - risk + deps + models):
  T1: PyTorch/MLX 共存风险验证 [quick] BLOCKING
  T2: OmniVoice pip install [quick] (depends: T1 PASS)
  T3: IndexTTS v1 模型下载 [quick] (depends: T1 PASS)
  T4: OmniVoice 模型下载验证 [quick] (depends: T2)

Wave 2 (After Wave 1 - backend adapters):
  T5: IndexTTS v1 adapter 实现 [deep] (depends: T3)
  T6: OmniVoice adapter 实现 [deep] (depends: T2, T4)
  T7: schemas.py engine_version 扩展 [quick] (depends: none)
  T8: engine_registry 策略模式重构 [deep] (depends: T5, T6, T7)

Wave 3 (After Wave 2 - frontend + integration):
  T9:  后端 API 路由适配 [quick] (depends: T8)
  T10: 前端引擎选择器 [visual-engineering] (depends: T9)
  T11: manifest 能力标签完善 [quick] (depends: T8)
  T12: 三引擎端到端集成测试 [deep] (depends: T9, T10, T11)

Wave FINAL (After ALL tasks):
  F1: Plan compliance audit (oracle)
  F2: Code quality review (unspecified-high)
  F3: Real manual QA (unspecified-high)
  F4: Scope fidelity check (deep)
  -> Present results -> Get explicit user okay

Critical Path: T1 -> T2 -> T6 -> T8 -> T9 -> T12 -> F1-F4
Parallel Speedup: ~50% faster than sequential
Max Concurrent: 4 (Wave 1 after T1)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | - | T2, T3 | 1 |
| T2 | T1 | T4, T6 | 1 |
| T3 | T1 | T5 | 1 |
| T4 | T2 | T6 | 1 |
| T5 | T3 | T8 | 2 |
| T6 | T2, T4 | T8 | 2 |
| T7 | - | T8 | 2 |
| T8 | T5, T6, T7 | T9, T11 | 2 |
| T9 | T8 | T10, T12 | 3 |
| T10 | T9 | T12 | 3 |
| T11 | T8 | T12 | 3 |
| T12 | T9, T10, T11 | F1-F4 | 3 |

### Agent Dispatch Summary

- **Wave 1**: 4 tasks - T1 quick, T2 quick, T3 quick, T4 quick
- **Wave 2**: 4 tasks - T5 deep, T6 deep, T7 quick, T8 deep
- **Wave 3**: 4 tasks - T9 quick, T10 visual-engineering, T11 quick, T12 deep
- **FINAL**: 4 tasks - F1 oracle, F2 unspecified-high, F3 unspecified-high, F4 deep

---

## TODOs

> FORMAT: Task labels use bare numbers: 1. 2. 3. — NOT T1. Task 1. Phase 1:.
> Final Verification Wave labels use F1. F2. etc.
> Every task MUST have: Agent Profile + QA Scenarios + Acceptance Criteria.

---

### Wave 1: Risk + Dependencies + Models

- [x] 1. PyTorch/MLX 共存风险验证 (BLOCKING)

  **What to do**:
  - 在项目根目录运行 `uv add omnivoice --dry-run` 验证依赖兼容性
  - 检查是否会产生 numpy/torch 版本冲突导致 MLX 失效
  - 如果 dry-run 失败，尝试 `uv add omnivoice --python 3.11` 或创建独立 venv
  - 验证冲突时运行 `python -c "import mlx; import torch; print('BOTH OK')"`
  - 记录结果到 evidence 文件

  **Must NOT do**:
  - 不实际安装任何包（dry-run only）
  - 不修改 pyproject.toml（仅检查）
  - 不在失败时强行继续

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (blocks entire plan)
  - **Parallel Group**: Sequential first
  - **Blocks**: T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12
  - **Blocked By**: None

  **References**:
  - `pyproject.toml` — 当前依赖列表，检查 numpy/mlx-core/torch 版本约束
  - `uv.lock` — 锁定文件，确认当前 MLX 依赖树

  **Acceptance Criteria**:
  - [x] `uv add omnivoice --dry-run` 输出不报错 OR 明确给出解决方案
  - [x] 如果 PASS：evidence 文件记录 `VERDICT: GO`
  - [ ] 如果 FAIL：evidence 文件记录 `VERDICT: NO-GO` + 冲突详情 + 建议方案

  **QA Scenarios**:
  ```
  Scenario: dry-run dependency check
    Tool: Bash
    Steps:
      1. cd /Users/foxmacstudio/Projects/mlx-indextts
      2. Run: uv add omnivoice --dry-run 2>&1
      3. Check output for version conflict warnings
    Expected Result: No critical dependency conflicts, or clear resolution path
    Evidence: .omo/evidence/task-1-risk-check.txt
  ```

  **Commit**: NO

- [x] 2. OmniVoice pip install

  **What to do**:
  - 运行 `uv add omnivoice` 安装 OmniVoice 及其 PyTorch 依赖
  - 安装完成后验证 `python -c "from omnivoice import OmniVoice; print('OK')"`
  - 同时验证 MLX 不受影响 `python -c "import mlx; print('MLX OK')"`
  - 如果安装失败，回退到 plan B：创建 `venv_omnivoice/` 独立环境

  **Must NOT do**:
  - 不降级 numpy 或 mlx-core 版本
  - 不删除或修改现有 MLX 相关依赖

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T3 after T1)
  - **Parallel Group**: Wave 1
  - **Blocks**: T4, T6
  - **Blocked By**: T1

  **References**:
  - `pyproject.toml` — 将被 uv add 修改
  - OmniVoice 官方 PyPI: `pip install omnivoice`

  **Acceptance Criteria**:
  - [x] `uv add omnivoice` 成功无错
  - [x] `python -c "from omnivoice import OmniVoice; print('OK')"` 输出 OK
  - [x] `python -c "import mlx; print('MLX OK')"` 输出 MLX OK

  **QA Scenarios**:
  ```
  Scenario: omnivoice import succeeds after install
    Tool: Bash
    Steps:
      1. Run: python -c "from omnivoice import OmniVoice; print('OK')"
    Expected Result: stdout contains "OK"
    Evidence: .omo/evidence/task-2-omnivoice-install.txt

  Scenario: MLX still works after omnivoice install
    Tool: Bash
    Steps:
      1. Run: python -c "import mlx; import mlx.nn; print('MLX OK')"
    Expected Result: stdout contains "MLX OK"
    Evidence: .omo/evidence/task-2-mlx-check.txt
  ```

  **Commit**: YES (groups with T3, T4)
  - Message: `chore(deps): add omnivoice + download v1 model`
  - Files: `pyproject.toml, uv.lock`

- [x] 3. IndexTTS v1 模型下载

  **What to do**:
  - 从国内镜像下载 MLX 预转换版 IndexTTS v1 模型
  - 命令: `huggingface-cli download mlx-community/IndexTTS-1.5 --local-dir models/mlx-indexTTS-1.5 --endpoint https://hf-mirror.com`
  - 如果 mlx-community 版不可用，备选：`IndexTeam/IndexTTS-1.5` + 自行转换
  - 下载完成后验证模型文件完整性（weights.npz 或 safetensors 存在）

  **Must NOT do**:
  - 不使用 huggingface.co 主站（走 hf-mirror.com）
  - 不删除或覆盖现有 v2 模型

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T2 after T1)
  - **Parallel Group**: Wave 1
  - **Blocks**: T5
  - **Blocked By**: T1

  **References**:
  - `models/mlx-indexTTS-2.0/` — 现有 v2 模型目录结构（参照格式）
  - `mlx_indextts/convert.py` — v1 转换脚本（备选方案需要）
  - mlx-community/IndexTTS-1.5 — HuggingFace 仓库名

  **Acceptance Criteria**:
  - [x] `models/mlx-indexTTS-1.5/` 目录存在且非空
  - [x] 模型权重文件存在（至少一个 .npz 或 .safetensors）
  - [x] `du -sh models/mlx-indexTTS-1.5/` 显示 > 500MB

  **QA Scenarios**:
  ```
  Scenario: v1 model files present and valid
    Tool: Bash
    Steps:
      1. Run: ls -la models/mlx-indexTTS-1.5/
      2. Run: du -sh models/mlx-indexTTS-1.5/
      3. Verify weight files exist
    Expected Result: Directory exists, size > 500MB, weight files present
    Evidence: .omo/evidence/task-3-v1-model-download.txt
  ```

  **Commit**: YES (groups with T2, T4)
  - Message: `chore(deps): add omnivoice + download v1 model`
  - Files: `models/mlx-indexTTS-1.5/`

- [x] 4. OmniVoice 模型下载验证

  **What to do**:
  - 验证 OmniVoice 模型缓存是否完整
  - 检查 `~/.cache/huggingface/hub/models--k2-fsa--OmniVoice/` 目录
  - 如果缓存不完整，从 hf-mirror.com 下载
  - 运行快速加载测试: `python -c "from omnivoice import OmniVoice; m=OmniVoice.from_pretrained('k2-fsa/OmniVoice', device_map='cpu'); print('Model OK')"`
  - 注意：首次加载会下载 ~3GB，使用 `HF_ENDPOINT=https://hf-mirror.com`

  **Must NOT do**:
  - 不使用 huggingface.co 主站
  - 不下载到项目目录（使用 HuggingFace 缓存）

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (waits for T2 pip install)
  - **Parallel Group**: Wave 1 (sequential after T2)
  - **Blocks**: T6
  - **Blocked By**: T2

  **References**:
  - `~/.cache/huggingface/hub/models--k2-fsa--OmniVoice/` — 已缓存 3GB（验证完整性）
  - OmniVoice API: `OmniVoice.from_pretrained('k2-fsa/OmniVoice', device_map='mps')`

  **Acceptance Criteria**:
  - [x] OmniVoice 模型可成功加载到 CPU（验证完整性）
  - [x] 无下载错误或 checksum 失败

  **QA Scenarios**:
  ```
  Scenario: omnivoice model loads successfully
    Tool: Bash
    Steps:
      1. Run: HF_ENDPOINT=https://hf-mirror.com python -c "from omnivoice import OmniVoice; m=OmniVoice.from_pretrained('k2-fsa/OmniVoice', device_map='cpu'); print('Model OK')"
    Expected Result: stdout contains "Model OK" (may take 30-60s first load)
    Evidence: .omo/evidence/task-4-omnivoice-model.txt
  ```

  **Commit**: YES (groups with T2, T3)
  - Message: `chore(deps): add omnivoice + download v1 model`

### Wave 2: Backend Adapters

- [ ] 5. IndexTTS v1 adapter 实现

  **What to do**:
  - 创建 `backend/app/services/adapters/v1_adapter.py`
  - 实现 V1EngineAdapter 类，遵循 PRD §8 Engine Adapter 规范
  - 核心方法: `generate(text, ref_audio_path, output_path, **kwargs) -> str`
  - 使用 `mlx_indextts.generate.IndexTTS` 加载 v1 模型（`models/mlx-indexTTS-1.5/`）
  - 调用 `tts.infer(audio_prompt=ref_audio_path, text=text, output_path=output_path)`
  - manifest 声明: sample_rate=24000, capabilities=[local_inference, voice_clone, multilingual]
  - health_check: 检查模型目录存在 + 权重文件存在
  - 错误处理: 模型不存在时返回 engine_not_available，推理失败时返回 inference_error

  **Must NOT do**:
  - 不修改 v2 推理逻辑
  - 不修改 engine_registry.py（T8 处理）
  - 不修改 schemas.py（T7 处理）

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T6, T7)
  - **Parallel Group**: Wave 2
  - **Blocks**: T8
  - **Blocked By**: T3

  **References**:
  - `mlx_indextts/generate.py:228` — `class IndexTTS` 定义，`load_model(model_dir)` 静态方法
  - `mlx_indextts/generate.py:687` — `infer(audio_prompt, text, output_path)` 推理方法
  - `backend/app/services/tts_engine.py:110-129` — 现有 v1 分支代码（参考但不能直接用，需重构为 adapter）
  - `backend/app/services/engine_registry.py:14-40` — 现有 manifest 格式（参考 capability 标签体系）
  - `models/mlx-indexTTS-2.0/` — v2 模型目录结构（参照 v1 目录应有哪些文件）

  **Acceptance Criteria**:
  - [ ] `backend/app/services/adapters/v1_adapter.py` 文件存在
  - [ ] V1EngineAdapter 类包含 generate/health_check/get_manifest 方法
  - [ ] `python -c "from backend.app.services.adapters.v1_adapter import V1EngineAdapter; print('OK')"` 无错

  **QA Scenarios**:
  ```
  Scenario: v1 adapter module imports cleanly
    Tool: Bash
    Steps:
      1. Run: python -c "from backend.app.services.adapters.v1_adapter import V1EngineAdapter; print('OK')"
    Expected Result: stdout contains "OK"
    Evidence: .omo/evidence/task-5-v1-adapter-import.txt

  Scenario: v1 adapter generate with valid input
    Tool: Bash
    Steps:
      1. Run: python -c "from backend.app.services.adapters.v1_adapter import V1EngineAdapter; a=V1EngineAdapter(); a.generate(text='测试', ref_audio_path='tests/fixtures/ref.wav', output_path='/tmp/test_v1_adapter.wav')"
      2. Run: python -c "import wave; w=wave.open('/tmp/test_v1_adapter.wav'); assert w.getframerate()==24000; assert w.getnframes()>0; print(f'OK: {w.getframerate()}Hz, {w.getnframes()} frames')"
    Expected Result: Audio file created, sample_rate=24000, duration>500ms
    Evidence: .omo/evidence/task-5-v1-adapter-generate.txt

  Scenario: v1 adapter health check detects missing model
    Tool: Bash
    Steps:
      1. Temporarily rename models/mlx-indexTTS-1.5
      2. Run: python -c "from backend.app.services.adapters.v1_adapter import V1EngineAdapter; a=V1EngineAdapter(); print(a.health_check())"
      3. Restore models/mlx-indexTTS-1.5
    Expected Result: health_check returns False or error message about missing model
    Evidence: .omo/evidence/task-5-v1-adapter-health.txt
  ```

  **Commit**: YES (groups with T6, T7, T8)
  - Message: `feat(backend): add v1 + omnivoice adapters, refactor registry`
  - Files: `backend/app/services/adapters/v1_adapter.py`

- [ ] 6. OmniVoice adapter 实现

  **What to do**:
  - 创建 `backend/app/services/adapters/omnivoice_adapter.py`
  - 实现 OmniVoiceEngineAdapter 类，遵循 PRD §8 Engine Adapter 规范
  - 核心方法: `generate(text, ref_audio_path=None, ref_text=None, voice_mode='auto', **kwargs) -> str`
  - 使用 `from omnivoice import OmniVoice` + `OmniVoice.from_pretrained('k2-fsa/OmniVoice', device_map='mps')`
  - 调用 `model.generate(text=text, ref_audio=ref_audio_path, ref_text=ref_text)`
  - manifest 声明: sample_rate=24000, capabilities=[local_inference, voice_clone, voice_design, multilingual, emotion_control]
  - voice_mode 支持: auto / clone / design
  - health_check: 验证 `~/.cache/huggingface/hub/models--k2-fsa--OmniVoice/` 存在
  - 输出格式转换：OmniVoice 输出 tensor → WAV (24000Hz)
  - device_map: 优先 mps，回退 cpu

  **Must NOT do**:
  - 不做 MLX 转换（直接 PyTorch MPS）
  - 不修改 v1/v2 推理逻辑
  - 不硬编码 model path（使用 from_pretrained 缓存机制）

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T5, T7)
  - **Parallel Group**: Wave 2
  - **Blocks**: T8
  - **Blocked By**: T2, T4

  **References**:
  - OmniVoice API: `from omnivoice import OmniVoice` → `OmniVoice.from_pretrained('k2-fsa/OmniVoice', device_map='mps')` → `model.generate(text=..., ref_audio=..., ref_text=...)`
  - `backend/app/services/engine_registry.py:14-40` — 现有 manifest 格式
  - `~/.cache/huggingface/hub/models--k2-fsa--OmniVoice/` — 已缓存模型
  - OmniVoice 输出：tensor (shape=[1, samples])，需转 WAV (torchaudio.save 或 soundfile.write)

  **Acceptance Criteria**:
  - [ ] `backend/app/services/adapters/omnivoice_adapter.py` 文件存在
  - [ ] OmniVoiceEngineAdapter 类包含 generate/health_check/get_manifest 方法
  - [ ] import 无错

  **QA Scenarios**:
  ```
  Scenario: omnivoice adapter module imports cleanly
    Tool: Bash
    Steps:
      1. Run: python -c "from backend.app.services.adapters.omnivoice_adapter import OmniVoiceEngineAdapter; print('OK')"
    Expected Result: stdout contains "OK"
    Evidence: .omo/evidence/task-6-omnivoice-adapter-import.txt

  Scenario: omnivoice adapter generate with valid input (auto mode)
    Tool: Bash
    Steps:
      1. Run: python -c "from backend.app.services.adapters.omnivoice_adapter import OmniVoiceEngineAdapter; a=OmniVoiceEngineAdapter(); a.generate(text='Hello world', output_path='/tmp/test_omni_adapter.wav', voice_mode='auto')"
      2. Run: python -c "import wave; w=wave.open('/tmp/test_omni_adapter.wav'); assert w.getframerate()==24000; print(f'OK: {w.getframerate()}Hz')"
    Expected Result: Audio file created, sample_rate=24000, duration>500ms
    Evidence: .omo/evidence/task-6-omnivoice-adapter-generate.txt
  ```

  **Commit**: YES (groups with T5, T7, T8)
  - Message: `feat(backend): add v1 + omnivoice adapters, refactor registry`
  - Files: `backend/app/services/adapters/omnivoice_adapter.py`

- [ ] 7. schemas.py engine_version 扩展

  **What to do**:
  - 修改 `backend/app/models/schemas.py`
  - 将 `engine_version: Literal['v1', 'v2']` 扩展为 `Literal['v1', 'v2', 'omnivoice']`
  - 添加 OmniVoice 特有参数模型（如果需要）：voice_mode: Optional[Literal['auto', 'clone', 'design']] = 'auto'
  - 确保 GenerateRequest 向后兼容（engine 默认值仍为 'indextts' 即 v2）

  **Must NOT do**:
  - 不删除现有字段
  - 不改变现有 API 的默认行为

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T5, T6)
  - **Parallel Group**: Wave 2
  - **Blocks**: T8
  - **Blocked By**: None (can start in Wave 1 or 2)

  **References**:
  - `backend/app/models/schemas.py` — 当前 engine_version 定义
  - PRD §8 — parameter_schema 规范

  **Acceptance Criteria**:
  - [ ] engine_version 包含 'omnivoice' 选项
  - [ ] 现有 GenerateRequest 默认值不变
  - [ ] `python -c "from backend.app.models.schemas import GenerateRequest; r=GenerateRequest(text='test', engine='omnivoice'); print('OK')"`

  **QA Scenarios**:
  ```
  Scenario: omnivoice engine_version accepted
    Tool: Bash
    Steps:
      1. Run: python -c "from backend.app.models.schemas import GenerateRequest; r=GenerateRequest(text='test', engine='omnivoice'); print(r.engine)"
    Expected Result: stdout contains "omnivoice"
    Evidence: .omo/evidence/task-7-schemas-omnivoice.txt

  Scenario: default engine still works
    Tool: Bash
    Steps:
      1. Run: python -c "from backend.app.models.schemas import GenerateRequest; r=GenerateRequest(text='test'); print(r.engine)"
    Expected Result: stdout contains "indextts" (v2 default unchanged)
    Evidence: .omo/evidence/task-7-schemas-default.txt
  ```

  **Commit**: YES (groups with T5, T6, T8)
  - Message: `feat(backend): add v1 + omnivoice adapters, refactor registry`
  - Files: `backend/app/models/schemas.py`

- [ ] 8. engine_registry 策略模式重构

  **What to do**:
  - 重构 `backend/app/services/engine_registry.py` 为策略/工厂模式
  - 创建引擎适配器映射：engine_id → adapter class
  - 统一 start_engine() 方法：根据 engine_id 自动选择对应 adapter
  - 添加 omnivoice 启动分支：`elif engine_id == 'omnivoice': from adapters.omnivoice_adapter import OmniVoiceEngineAdapter`
  - 每个引擎的 manifest 包含 PRD §8 要求的完整字段：
    - engine_id, name, engine_type, provider, capabilities, supported_languages, sample_rate, privacy_level
  - 统一 generate() 入口：接收 engine_id + 参数 → 调用对应 adapter.generate()
  - 保持现有 v2 的 start_engine() 逻辑不变（回归保护）

  **Must NOT do**:
  - 不删除或修改 v2 已有的推理路径
  - 不引入过度抽象（简单映射即可，不需要完整 plugin 系统）
  - 不在 T5/T6/T7 完成前动手

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on T5, T6, T7)
  - **Parallel Group**: Wave 2 (sequential end)
  - **Blocks**: T9, T11
  - **Blocked By**: T5, T6, T7

  **References**:
  - `backend/app/services/engine_registry.py` — 当前实现，3 个 manifest + start_engine
  - `backend/app/services/tts_engine.py:62` — `_get_model()` 现有 v1/v2 分支
  - `backend/app/services/adapters/v1_adapter.py` — T5 创建
  - `backend/app/services/adapters/omnivoice_adapter.py` — T6 创建
  - PRD §8 Engine Adapter 规范 — manifest + parameter_schema + health_check
  - PRD §0.3 — "新增模型不得改动主页面结构" → 注册表模式实现

  **Acceptance Criteria**:
  - [ ] engine_registry.py 包含 engine_adapter_map 字典
  - [ ] start_engine('indextts') 仍走 v2 路径（回归）
  - [ ] start_engine('indextts-v1') 使用 V1EngineAdapter
  - [ ] start_engine('omnivoice') 使用 OmniVoiceEngineAdapter
  - [ ] list_engines() 返回 3 个引擎 manifest

  **QA Scenarios**:
  ```
  Scenario: registry returns 3 engine manifests
    Tool: Bash
    Steps:
      1. Start backend: cd backend && uvicorn app.main:app --port 8000 &
      2. Run: curl -s http://localhost:8000/api/engines | python -c "import sys,json; e=json.load(sys.stdin); print(f'{len(e)} engines: {[x['engine_id'] for x in e]}')"
    Expected Result: 3 engines: ['indextts', 'indextts-v1', 'omnivoice']
    Evidence: .omo/evidence/task-8-registry-3-engines.txt

  Scenario: v2 regression - generate still works
    Tool: Bash
    Steps:
      1. Run: curl -s -X POST http://localhost:8000/api/generate -H 'Content-Type: application/json' -d '{"text":"回归测试","engine":"indextts"}' -o /tmp/test_v2_regression.wav
      2. Run: python -c "import wave; w=wave.open('/tmp/test_v2_regression.wav'); assert w.getframerate()==22050; print(f'v2 OK: {w.getframerate()}Hz')"
    Expected Result: Audio file created, sample_rate=22050
    Evidence: .omo/evidence/task-8-v2-regression.txt
  ```

  **Commit**: YES (groups with T5, T6, T7)
  - Message: `feat(backend): add v1 + omnivoice adapters, refactor registry`
  - Files: `backend/app/services/engine_registry.py`
### Wave 3: Frontend + Integration

- [ ] 9. 后端 API 路由适配

  **What to do**:
  - 检查 `backend/app/routers/` 或 `backend/app/main.py` 中的 generate 路由
  - 确保 `/api/generate` 端点支持 `engine` 参数传递到 engine_registry
  - 确保 `/api/engines` 端点返回所有已注册引擎的完整 manifest
  - 添加 OmniVoice 特有参数（voice_mode, ref_text）的请求体支持
  - 保持现有 v2 的默认行为不变

  **Must NOT do**:
  - 不改变现有 API 的响应格式
  - 不删除现有路由

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T10, T11 after T8)
  - **Parallel Group**: Wave 3
  - **Blocks**: T10, T12
  - **Blocked By**: T8

  **References**:
  - `backend/app/routers/` 或 `backend/app/main.py` — 现有 API 路由定义
  - `backend/app/services/engine_registry.py` — T8 重构后的 registry
  - `backend/app/models/schemas.py` — T7 扩展后的 schemas

  **Acceptance Criteria**:
  - [ ] `/api/generate` 支持 engine 参数
  - [ ] `/api/engines` 返回 3 个引擎 manifest
  - [ ] v2 默认行为不变（不传 engine 时默认 indextts）

  **QA Scenarios**:
  ```
  Scenario: API returns 3 engines
    Tool: Bash
    Steps:
      1. Start backend
      2. Run: curl -s http://localhost:8000/api/engines | python -c "import sys,json; e=json.load(sys.stdin); print(f'{len(e)} engines')"
    Expected Result: 3 engines returned
    Evidence: .omo/evidence/task-9-api-engines.txt

  Scenario: API generate with engine param
    Tool: Bash
    Steps:
      1. Run: curl -s -X POST http://localhost:8000/api/generate -H 'Content-Type: application/json' -d '{"text":"test","engine":"indextts"}' -o /dev/null -w '%{http_code}'
    Expected Result: HTTP 200
    Evidence: .omo/evidence/task-9-api-generate.txt
  ```

  **Commit**: YES (groups with T10, T11, T12)
  - Message: `feat(fullstack): engine selector + integration tests`
  - Files: `backend/app/routers/` 或 `backend/app/main.py`

- [ ] 10. 前端引擎选择器

  **What to do**:
  - 修改 `frontend/src/routes/generate/+page.svelte`
  - 添加引擎选择下拉框（从 `/api/engines` 获取列表）
  - 实现能力驱动 UI：根据选中引擎的 manifest capabilities 动态渲染参数面板
  - v2 引擎：显示 emotion slider、diffusion steps 等
  - v1 引擎：隐藏 emotion 相关参数
  - OmniVoice 引擎：显示 voice_mode 下拉框（auto/clone/design）
  - 使用 `frontend/src/lib/api/engines.ts` 的 `listEngines()` API

  **Must NOT do**:
  - 不改动现有 v2 的推理流程
  - 不引入新的 UI 框架或组件库
  - 不硬编码引擎列表（从 API 动态获取）

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on T9)
  - **Parallel Group**: Wave 3 (after T9)
  - **Blocks**: T12
  - **Blocked By**: T9

  **References**:
  - `frontend/src/routes/generate/+page.svelte` — 当前生成页面（L8 硬编码 indextts）
  - `frontend/src/lib/api/engines.ts` — `listEngines()` API 调用
  - PRD §0.3 — "新增模型不得改动主页面结构"、"能力驱动 UI"
  - PRD §10-13 — 各引擎参数差异（emotion, voice_mode 等）

  **Acceptance Criteria**:
  - [ ] 下拉框显示 3 个引擎选项
  - [ ] 切换引擎时参数面板动态变化
  - [ ] 选中 v2 时显示 emotion slider
  - [ ] 选中 OmniVoice 时显示 voice_mode 选择
  - [ ] 默认选中 indextts（v2）

  **QA Scenarios**:
  ```
  Scenario: engine dropdown renders 3 options
    Tool: Playwright
    Steps:
      1. Navigate to /generate
      2. Click engine selector dropdown
      3. Count options
    Expected Result: 3 options visible (indextts, indextts-v1, omnivoice)
    Evidence: .omo/evidence/task-10-engine-dropdown.png

  Scenario: switching engine changes params panel
    Tool: Playwright
    Steps:
      1. Select 'omnivoice' from dropdown
      2. Check for voice_mode selector
      3. Select 'indextts-v1'
      4. Verify emotion slider is hidden
    Expected Result: UI adapts to engine capabilities
    Evidence: .omo/evidence/task-10-capability-ui.png
  ```

  **Commit**: YES (groups with T9, T11, T12)
  - Message: `feat(fullstack): engine selector + integration tests`
  - Files: `frontend/src/routes/generate/+page.svelte`

- [ ] 11. manifest 能力标签完善

  **What to do**:
  - 审查 3 个引擎的 manifest，确保 capabilities 标签完整准确
  - v1: [local_inference, voice_clone, multilingual]
  - v2: [local_inference, voice_clone, multilingual, emotion_control]
  - OmniVoice: [local_inference, voice_clone, voice_design, multilingual, emotion_control]
  - 确保 sample_rate 声明正确（v1=24000, v2=22050, omni=24000）
  - 确保 privacy_level 声明正确（全部 local）
  - 确保 supported_languages 声明正确

  **Must NOT do**:
  - 不添加不存在的能力标签

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with T9, T10 after T8)
  - **Parallel Group**: Wave 3
  - **Blocks**: T12
  - **Blocked By**: T8

  **References**:
  - `backend/app/services/engine_registry.py` — 各引擎 manifest 定义
  - PRD §8 — capabilities 标签体系
  - README.md — Version Comparison 表（sample_rate, features）

  **Acceptance Criteria**:
  - [ ] 3 个引擎 manifest 的 capabilities 标签与实际能力匹配
  - [ ] sample_rate 声明与 README 一致
  - [ ] privacy_level 全部为 local

  **QA Scenarios**:
  ```
  Scenario: manifest capabilities match reality
    Tool: Bash
    Steps:
      1. Run: curl -s http://localhost:8000/api/engines | python -c "import sys,json; e=json.load(sys.stdin); [print(f'{x['engine_id']}: sr={x['sample_rate']}, caps={x['capabilities']}') for x in e]"
    Expected Result: Correct sample_rate and capabilities for each engine
    Evidence: .omo/evidence/task-11-manifest-check.txt
  ```

  **Commit**: YES (groups with T9, T10, T12)
  - Message: `feat(fullstack): engine selector + integration tests`
  - Files: `backend/app/services/engine_registry.py`

- [ ] 12. 三引擎端到端集成测试

  **What to do**:
  - 编写集成测试脚本 `tests/integration/test_three_engines.py`
  - 测试 v1 端到端：text → generate → WAV (24000Hz, >500ms)
  - 测试 v2 端到端：text → generate → WAV (22050Hz, >500ms)（回归）
  - 测试 OmniVoice 端到端：text → generate → WAV (24000Hz, >500ms)
  - 测试错误情况：无效引擎名、空文本、缺失模型
  - 测试前端：引擎选择下拉框切换、参数面板变化
  - 所有证据保存到 `.omo/evidence/`

  **Must NOT do**:
  - 不修改已有测试
  - 不删除测试 fixtures

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on T9, T10, T11)
  - **Parallel Group**: Wave 3 (sequential end)
  - **Blocks**: F1-F4
  - **Blocked By**: T9, T10, T11

  **References**:
  - `tests/` — 现有测试目录结构
  - `backend/app/services/engine_registry.py` — 重构后的 registry
  - PRD §24 — 非功能需求（页面 200ms、长文本分段等）

  **Acceptance Criteria**:
  - [ ] `tests/integration/test_three_engines.py` 存在
  - [ ] v1 端到端测试通过
  - [ ] v2 端到端测试通过（回归）
  - [ ] OmniVoice 端到端测试通过
  - [ ] 错误情况测试通过
  - [ ] 所有 evidence 文件存在

  **QA Scenarios**:
  ```
  Scenario: v1 end-to-end generate
    Tool: Bash
    Steps:
      1. Run: curl -s -X POST http://localhost:8000/api/generate -H 'Content-Type: application/json' -d '{"text":"集成测试","engine":"indextts-v1"}' -o /tmp/test_v1_e2e.wav
      2. Run: python -c "import wave; w=wave.open('/tmp/test_v1_e2e.wav'); assert w.getframerate()==24000; assert w.getnframes()/w.getframerate()>0.5; print(f'v1 E2E OK: {w.getframerate()}Hz, {w.getnframes()/w.getframerate():.1f}s')"
    Expected Result: WAV file, 24000Hz, >0.5s
    Evidence: .omo/evidence/task-12-v1-e2e.txt

  Scenario: v2 regression end-to-end
    Tool: Bash
    Steps:
      1. Run: curl -s -X POST http://localhost:8000/api/generate -H 'Content-Type: application/json' -d '{"text":"回归测试","engine":"indextts"}' -o /tmp/test_v2_e2e.wav
      2. Run: python -c "import wave; w=wave.open('/tmp/test_v2_e2e.wav'); assert w.getframerate()==22050; print(f'v2 E2E OK: {w.getframerate()}Hz')"
    Expected Result: WAV file, 22050Hz
    Evidence: .omo/evidence/task-12-v2-e2e.txt

  Scenario: OmniVoice end-to-end generate
    Tool: Bash
    Steps:
      1. Run: curl -s -X POST http://localhost:8000/api/generate -H 'Content-Type: application/json' -d '{"text":"Hello integration test","engine":"omnivoice"}' -o /tmp/test_omni_e2e.wav
      2. Run: python -c "import wave; w=wave.open('/tmp/test_omni_e2e.wav'); assert w.getframerate()==24000; print(f'omni E2E OK: {w.getframerate()}Hz')"
    Expected Result: WAV file, 24000Hz
    Evidence: .omo/evidence/task-12-omni-e2e.txt

  Scenario: error handling - invalid engine
    Tool: Bash
    Steps:
      1. Run: curl -s -X POST http://localhost:8000/api/generate -H 'Content-Type: application/json' -d '{"text":"test","engine":"nonexistent"}' -w '\nHTTP_CODE:%{http_code}'
    Expected Result: HTTP 4xx error with meaningful message
    Evidence: .omo/evidence/task-12-error-invalid-engine.txt
  ```

  **Commit**: YES (groups with T9, T10, T11)
  - Message: `feat(fullstack): engine selector + integration tests`
  - Files: `tests/integration/test_three_engines.py`

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.

- [ ] F1. **Plan Compliance Audit** — oracle
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint). For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files in .omo/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — unspecified-high
  Run linting + type checks. Review all changed files for: unused imports, empty catches, console.log in prod, AI slop (excessive comments, over-abstraction, generic names). Verify no v2 regression.
  Output: `Lint [PASS/FAIL] | Types [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — unspecified-high
  Start from clean state. Test all 3 engines via API: v1 voice clone, v2 emotion, OmniVoice auto/clone. Test frontend dropdown switch. Test error cases: missing model, invalid engine name, empty text. Save evidence to .omo/evidence/final-qa/.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — deep
  For each task: read "What to do", read actual diff. Verify no scope creep (no Script Studio, no Task Queue, no History). Check v2 regression (v2 generate still works identically). Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Wave 1** (T1-T4): `chore(deps): add omnivoice + download v1 model`
  - T1 is NO COMMIT (dry-run only)
  - T2-T4 squash if all pass
  - Pre-commit: `python -c "import omnivoice; import mlx_indextts; print('OK')"`
- **Wave 2** (T5-T8): `feat(backend): add v1 + omnivoice adapters, refactor registry`
  - Pre-commit: `cd backend && python -m pytest tests/ -x`
- **Wave 3** (T9-T12): `feat(fullstack): engine selector + integration tests`
  - Pre-commit: `curl -s http://localhost:8000/api/engines | python -m json.tool`

---

## Success Criteria

### Verification Commands
```bash
# 3 engines registered
curl -s http://localhost:8000/api/engines | python -c "import sys,json; engines=json.load(sys.stdin); assert len(engines)>=3; print(f'{len(engines)} engines OK')"

# v1 inference
curl -s -X POST http://localhost:8000/api/generate -H "Content-Type: application/json" -d '{"text":"测试语音","engine":"indextts-v1"}' -o /tmp/test_v1.wav && python -c "import wave; w=wave.open('/tmp/test_v1.wav'); print(f'v1: {w.getnframes()//w.getframerate()}s, {w.getframerate()}Hz')"

# v2 regression
curl -s -X POST http://localhost:8000/api/generate -H "Content-Type: application/json" -d '{"text":"测试语音","engine":"indextts"}' -o /tmp/test_v2.wav && python -c "import wave; w=wave.open('/tmp/test_v2.wav'); print(f'v2: {w.getnframes()//w.getframerate()}s, {w.getframerate()}Hz')"

# omnivoice inference
curl -s -X POST http://localhost:8000/api/generate -H "Content-Type: application/json" -d '{"text":"Hello world","engine":"omnivoice"}' -o /tmp/test_omni.wav && python -c "import wave; w=wave.open('/tmp/test_omni.wav'); print(f'omni: {w.getnframes()//w.getframerate()}s, {w.getframerate()}Hz')"
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] v2 regression test pass
- [ ] 3 engines all return valid audio
- [ ] Frontend engine selector functional
- [ ] Evidence files in .omo/evidence/
