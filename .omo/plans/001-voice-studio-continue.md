# Voice Studio v2.0 继续开发 — 零 Bug 交付计划

## TL;DR

> **Quick Summary**: 基于 PRD v2.0，将 Voice Studio 从 ~70% 完成度补齐到 P0 全部验收通过。4 波严格 Wave 执行，每波有 checkpoint，质量优先零 bug。
> 
> **Deliverables**:
> - 后端 14 个 Tier-1 bug 全部修复
> - 前端 ~15 个 Tier-1 bug 全部修复
> - MLX 真实推理验证通过（非 mock）
> - 空壳页面补齐或明确标记
> - 环境可一键启动无冲突
> 
> **Estimated Effort**: Large (4 Waves, ~27 tasks)
> **Parallel Execution**: YES - 4 waves with parallelism within each
> **Critical Path**: Wave 0 (MLX+mock) → Wave 1 (backend) → Wave 2 (frontend) → Wave 3 (env+pages) → Final

---

## Context

### Original Request
用户要求在已有 Voice Studio PRD v2.0 dev 代码基础上继续工作，先做深度调研确认 PRD 无问题，然后按 PRD 执行补齐未完成内容。原话："宁慢勿烂"、"交付给我的东西是没有问题的，没有 bug 的"。

### Interview Summary
**Key Discussions**:
- **PRD 保持现状**: 无致命内部矛盾，按 PRD 执行
- **Mock 策略**: 禁止静默 fallback，缺模型直接报错，不返回假音频
- **OmniVoice → Phase 2**: 当前 0% 实现，从零移植不混入本期
- **Script Studio → P1 延后**: 空壳先确保已有功能全部正确
- **测试策略**: 非 TDD 形式，但每个交付物必须有可运行 QA 验证

**Research Findings**:
- 后端 20 文件 14 个 bug（settings round-trip 损坏、mock 静默欺骗、engine lifecycle 假的、voice delete 不删文件等）
- 前端 8 页面 ~15 个 bug（零 toast 系统、InspectorPanel 永远隐藏、Sidebar 硬编码、directAudioId 未用等）
- 模型 v2.0 确认存在 models/mlx-indexTTS-2.0/（sample_rate=22050），无 v1.5 模型
- 当前 history 全是 mock（duration_ms=1500），真实 MLX 推理从未验证
- 端口冲突 :8000 PID 97520、:5173 PID 36975

### Metis Review
**Identified Gaps** (addressed):
- 每个 bug fix 必须有测试（fail on current, pass on fixed）
- 音频断言需 sample_rate==22050, duration>500ms, peak_amplitude>0.01
- 不要把 bug fix + 新功能混在一个任务
- 锁定错误 UX 模式用 toast(Sonner) + inline form errors
- 分 4 个严格 Wave + checkpoint

---

## Work Objectives

### Core Objective
将 Voice Studio 从 ~70% 完成度补齐到 PRD v2.0 P0 全部验收通过，零 bug 交付，质量优先。

### Concrete Deliverables
- MLX IndexTTS v2.0 真实推理端到端可用
- 所有后端 API 返回正确数据（非 mock）
- 所有前端页面可交互、有错误反馈
- 一键启动无端口冲突

### Definition of Done
- [ ] `curl -s http://localhost:8000/api/health` 返回 healthy
- [ ] MLX 推理生成真实 WAV 文件（duration>500ms, sample_rate=22050）
- [ ] 前端 Generate 页面完整流程可走通（选 voice → 输入文本 → 生成 → 播放 → 存 history）
- [ ] Settings PATCH 后 GET 返回一致数据
- [ ] 零 mock fallback、零静默错误
- [ ] `pnpm build` 成功、后端 `uvicorn` 启动无报错

### Must Have
- Settings round-trip 正确（PATCH → GET 一致）
- Mock 清除，真实 MLX 推理工作
- 错误反馈可见（toast + inline）
- Voice CRUD 完整（含物理文件删除）
- Generate 页面 voice 模式端到端可用
- Engine 状态真实反映模型加载状态
- History 页面展示真实生成记录

### Must NOT Have (Guardrails)
- 禁止静默 mock fallback（缺模型必须报错）
- 禁止 "顺手优化" 不相关代码
- 禁止混入 OmniVoice / Script Studio 新功能
- 禁止添加 PRD 未要求的抽象层
- 禁止 AI slop：过度注释、过度错误处理、泛型命名
- 禁止修改 PRD

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (pytest configured for mlx_indextts core, no backend tests yet)
- **Automated tests**: Tests-after (per-bug fix verification scripts)
- **Framework**: pytest for backend, pnpm build for frontend
- **Primary verification**: Agent-executed QA scenarios (curl for API, Playwright for UI, Bash for CLI)

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.omo/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Backend API**: Use Bash (curl) - Send requests, assert status + response fields
- **Frontend UI**: Use Playwright - Navigate, interact, assert DOM, screenshot
- **MLX Inference**: Use Bash (Python) - Import, generate, verify audio properties
- **Config/Build**: Use Bash - Run commands, check exit codes

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0 (Foundation - MLX 验证 + Mock 清除):
├── Task 1: MLX v2.0 端到端推理验证 [deep]
├── Task 2: Settings round-trip 修复 [quick]
├── Task 3: Mock 清除 + 真实推理接入 [deep]
└── Task 4: Engine lifecycle 真实化 [deep]
→ CHECKPOINT: MLX 推理真实可用 + settings 正确

Wave 1 (Backend Tier-1 Bugs):
├── Task 5: Voice CRUD 补全（物理文件删除） [quick]
├── Task 6: Task queue 修复（单实例/cancel/retry） [unspecified-high]
├── Task 7: schemas.py 清理（重复字段 + 孤立类型） [quick]
├── Task 8: Duration 计算修正（22050Hz） [quick]
├── Task 9: History API 真实数据 [quick]
├── Task 10: Backend 错误处理统一 [quick]
├── Task 11: generate API 修复（路径/参数/WS） [unspecified-high]
└── Task 12: pyproject.toml 依赖声明 [quick]
→ CHECKPOINT: 后端所有 API 返回正确数据

Wave 2 (Frontend Tier-1 Bugs):
├── Task 13: Toast 通知系统（Sonner） [visual-engineering]
├── Task 14: API client typed wrapper + 错误处理 [quick]
├── Task 15: Generate 页面修复（voice路径/directAudio/favorite/reuse） [visual-engineering]
├── Task 16: Sidebar 真实引擎状态 [quick]
├── Task 17: Layout + InspectorPanel 修复 [visual-engineering]
├── Task 18: Voice Library 修复（试听/删除确认） [quick]
├── Task 19: Engine Hub tabs 修复 [quick]
├── Task 20: Settings 页面补全 [quick]
├── Task 21: History 页面功能实现 [visual-engineering]
├── Task 22: WebSocket 客户端接入（替代轮询） [unspecified-high]
└── Task 23: TailwindCSS v4 配置修正 [quick]
→ CHECKPOINT: 前端所有页面可交互

Wave 3 (空壳页面 + 环境收尾):
├── Task 24: Tasks 页面实现 [visual-engineering]
├── Task 25: 端口冲突解决 + 启动脚本 [quick]
├── Task 26: Script Studio 占位页面完善 [quick]
└── Task 27: 全链路集成测试 [deep]
→ CHECKPOINT: 全链路可用

Wave FINAL (After ALL tasks — 4 parallel reviews):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
→ Present results → Get explicit user okay

Critical Path: T1 → T3 → T11 → T15 → T22 → T27 → F1-F4
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 4 (Wave 0)
```

### Dependency Matrix

| Task | Blocked By | Blocks |
|------|-----------|--------|
| 1 | - | 3, 4 |
| 2 | - | 15, 20 |
| 3 | 1 | 8, 10, 11 |
| 4 | 1 | 16 |
| 5 | - | 18 |
| 6 | - | 22 |
| 7 | - | 11, 15 |
| 8 | 3 | - |
| 9 | 3 | 15 |
| 10 | 3 | 21 |
| 11 | 3, 7 | 15, 22 |
| 12 | - | - |
| 13 | - | 15, 17, 18, 19, 21 |
| 14 | - | 15, 16, 17, 18, 19, 20, 21, 22 |
| 15 | 2, 9, 11, 13, 14 | 27 |
| 16 | 4, 14 | - |
| 17 | 14 | - |
| 18 | 5, 13, 14 | - |
| 19 | 14 | - |
| 20 | 2, 14 | - |
| 21 | 10, 13, 14 | - |
| 22 | 6, 11, 14 | 27 |
| 23 | - | - |
| 24 | 14 | 27 |
| 25 | - | - |
| 26 | - | - |
| 27 | 15, 22, 24 | F1-F4 |

### Agent Dispatch Summary

- **Wave 0**: 4 tasks - T1→`deep`, T2→`quick`, T3→`deep`, T4→`deep`
- **Wave 1**: 8 tasks - T5,T7,T8,T9,T10,T12→`quick`, T6,T11→`unspecified-high`
- **Wave 2**: 11 tasks - T13,T15,T17,T21→`visual-engineering`, T14,T16,T18,T19,T20,T23→`quick`, T22→`unspecified-high`
- **Wave 3**: 4 tasks - T24→`visual-engineering`, T25,T26→`quick`, T27→`deep`
- **FINAL**: 4 tasks - F1→`oracle`, F2→`unspecified-high`, F3→`unspecified-high`, F4→`deep`

---

## TODOs

### Wave 0: MLX 验证 + Mock 清除

- [x] 1. MLX v2.0 端到端推理验证

  **What to do**:
  - 在 `backend/` 外部用 Python 直接调用 `mlx_indextts.generate_v2.IndexTTSv2`
  - 验证模型加载 `models/mlx-indexTTS-2.0/` 成功
  - 用 `~/VoiceStudio/voices/` 中的参考音频生成一段 TTS
  - 断言输出 WAV: `sample_rate==22050`, `duration>0.5s`, `peak_amplitude>0.01`
  - 记录首次推理延迟和 RTF

  **Must NOT do**:
  - 不修改任何 Voice Studio 代码，纯验证脚本
  - 不尝试加载不存在的 v1.5 模型

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`python-patterns`]
    - `python-patterns`: MLX 推理脚本需要正确的 Python 模式

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 2)
  - **Parallel Group**: Wave 0
  - **Blocks**: 3, 4
  - **Blocked By**: None

  **References**:
  - `mlx_indextts/generate_v2.py` — IndexTTSv2 class API（load_model/generate/save_audio）
  - `models/mlx-indexTTS-2.0/config.json` — 确认 sample_rate=22050, s2mel/bigvgan 配置
  - `~/VoiceStudio/voices/` — 参考音频目录（.wav 文件）
  - `README.md` — 已有 v2.0 CLI 用法示例和 Python API 示例

  **Acceptance Criteria**:
  - [ ] Python 脚本成功加载模型并生成 WAV 文件
  - [ ] 输出文件 `sample_rate==22050`
  - [ ] 输出文件 `duration > 0.5s`
  - [ ] 输出文件 `peak_amplitude > 0.01`
  - [ ] 控制台输出 RTF 值

  **QA Scenarios**:
  ```
  Scenario: MLX v2.0 real inference
    Tool: Bash (Python script)
    Preconditions: models/mlx-indexTTS-2.0/ exists with safetensors
    Steps:
      1. Run: uv run python -c "from mlx_indextts.generate_v2 import IndexTTSv2; tts=IndexTTSv2('models/mlx-indexTTS-2.0'); ..."
      2. Verify output file exists: /tmp/vs_mlx_test.wav
      3. python3 -c "import wave; w=wave.open('/tmp/vs_mlx_test.wav'); assert w.getframerate()==22050; assert w.getnframes()/w.getframerate()>0.5"
    Expected Result: File created, 22050Hz, >0.5s duration
    Evidence: .omo/evidence/task-1-mlx-inference.txt

  Scenario: Missing model error handling
    Tool: Bash
    Steps:
      1. Run with non-existent model path
      2. Verify error raised (not silent)
    Expected Result: FileNotFoundError or similar clear error
    Evidence: .omo/evidence/task-1-mlx-missing-model.txt
  ```

  **Commit**: NO (verification only, no code changes)

- [x] 2. Settings round-trip 修复

  **What to do**:
  - 修复 `backend/app/services/settings_store.py`:
    - Line 11-15: 读取时必须 `json.loads()` 解析 JSON 字符串
    - Line 20: 写入 `json.dumps()` 保持不变
  - 确保 PATCH `/api/settings` → GET `/api/settings` 数据完全一致
  - 覆盖所有字段: `output_dir`, `default_engine`, `sample_rate`, `temperature` 等

  **Must NOT do**:
  - 不添加新字段
  - 不改变 API 接口

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 1)
  - **Parallel Group**: Wave 0
  - **Blocks**: 15, 20
  - **Blocked By**: None

  **References**:
  - `backend/app/services/settings_store.py:11-20` — 当前损坏的读写逻辑
  - `backend/app/api/settings.py` — API 端点定义
  - `backend/app/models/schemas.py` — Settings 模型定义

  **Acceptance Criteria**:
  - [ ] PATCH 设置任意字段后 GET 返回完全一致的值
  - [ ] 所有路径类型字段（output_dir 等）正确持久化
  - [ ] 数值字段（sample_rate 等）正确持久化

  **QA Scenarios**:
  ```
  Scenario: Settings round-trip consistency
    Tool: Bash (curl)
    Preconditions: Backend running on :8000
    Steps:
      1. curl -X PATCH http://localhost:8000/api/settings -H 'Content-Type: application/json' -d '{"output_dir":"/tmp/vs_test","sample_rate":22050}'
      2. curl http://localhost:8000/api/settings
      3. Assert response.output_dir == '/tmp/vs_test' AND response.sample_rate == 22050
    Expected Result: GET returns exactly what was PATCHed
    Evidence: .omo/evidence/task-2-settings-roundtrip.txt

  Scenario: Settings survives restart
    Tool: Bash
    Steps:
      1. PATCH settings
      2. Restart backend (kill + start)
      3. GET settings and verify persisted
    Expected Result: Values survive restart
    Evidence: .omo/evidence/task-2-settings-persist.txt
  ```

  **Commit**: YES (groups with Wave 0)
  - Message: `fix(settings): json parse on read to fix round-trip corruption`
  - Files: `backend/app/services/settings_store.py`

- [x] 3. Mock 清除 + 真实推理接入

  **What to do**:
  - 修改 `backend/app/services/tts_engine.py`:
    - Line 68: 模型路径改为从 settings 或 config 读取，不硬编码
    - Line 155-156: 删除 FileNotFoundError 时的 mock fallback（`np.zeros` 返回假音频）
    - 改为 raise 异常或返回错误状态，绝不返回假音频
  - 接入真实 MLX 推理：调用 `mlx_indextts.generate_v2.IndexTTSv2`
  - 确保生成的音频 `sample_rate==22050`
  - 推理错误时返回明确的 HTTP 错误响应

  **Must NOT do**:
  - 不保留任何 mock fallback 路径
  - 不添加 OmniVoice 或其他引擎

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 0 (sequential after Task 1)
  - **Blocks**: 8, 10, 11
  - **Blocked By**: 1

  **References**:
  - `backend/app/services/tts_engine.py:68,155-156` — 硬编码路径 + mock fallback
  - `mlx_indextts/generate_v2.py` — IndexTTSv2 真实推理 API
  - `models/mlx-indexTTS-2.0/config.json` — 确认模型配置
  - Task 1 验证结果 — 确认 MLX 推理可用

  **Acceptance Criteria**:
  - [ ] tts_engine.py 中无 `np.zeros`/`silence`/`mock` 关键词
  - [ ] 模型缺失时返回 HTTP 503 + 明确错误信息
  - [ ] 推理成功时返回真实 WAV（sample_rate==22050, duration>0.5s）
  - [ ] history 表记录的 duration_ms > 1500（非 mock 的 1500ms）

  **QA Scenarios**:
  ```
  Scenario: Real TTS inference via API
    Tool: Bash (curl)
    Preconditions: Backend running, model loaded
    Steps:
      1. POST /api/generate with valid text + voice_id
      2. Verify response status 200
      3. Verify output WAV file exists and is >0.5s at 22050Hz
    Expected Result: Real audio generated, not mock
    Evidence: .omo/evidence/task-3-real-inference.txt

  Scenario: Model missing error
    Tool: Bash (curl)
    Preconditions: Model path invalid/missing
    Steps:
      1. Configure invalid model path in settings
      2. POST /api/generate
      3. Verify HTTP 503 with clear error message
    Expected Result: Clear error, no fake audio returned
    Evidence: .omo/evidence/task-3-model-missing.txt
  ```

  **Commit**: YES (groups with Wave 0)
  - Message: `fix(tts): remove mock fallback, integrate real MLX inference`
  - Files: `backend/app/services/tts_engine.py`

- [x] 4. Engine lifecycle 真实化

  **What to do**:
  - 修改 `backend/app/services/engine_registry.py`:
    - Line 54-61: start() 时真正加载 MLX 模型到内存
    - stop() 时真正释放模型资源
    - 状态变更反映真实加载状态
  - 修改 `backend/app/api/engines.py` 相应端点
  - GET `/api/engines` 返回真实状态（loaded/loading/error）

  **Must NOT do**:
  - 不添加 OmniVoice 引擎
  - 不实现进程级隔离（本期单进程即可）

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (after Task 1)
  - **Parallel Group**: Wave 0
  - **Blocks**: 16
  - **Blocked By**: 1

  **References**:
  - `backend/app/services/engine_registry.py:54-61` — 假的 start/stop
  - `backend/app/api/engines.py` — 引擎 API 端点
  - Task 1 验证结果 — MLX 模型加载 API

  **Acceptance Criteria**:
  - [ ] POST `/api/engines/{id}/start` 真正加载模型（首次推理无额外延迟）
  - [ ] GET `/api/engines` 返回真实 loaded 状态
  - [ ] start 失败时状态为 error 并有错误信息

  **QA Scenarios**:
  ```
  Scenario: Engine start loads model
    Tool: Bash (curl)
    Steps:
      1. POST /api/engines/indextts-v2/start
      2. GET /api/engines → assert status == 'loaded'
      3. POST /api/generate → verify no model loading delay (already loaded)
    Expected Result: Engine shows loaded, inference works immediately
    Evidence: .omo/evidence/task-4-engine-start.txt

  Scenario: Engine stop releases resources
    Tool: Bash (curl)
    Steps:
      1. POST /api/engines/indextts-v2/stop
      2. GET /api/engines → assert status == 'stopped'
    Expected Result: Status reflects stopped
    Evidence: .omo/evidence/task-4-engine-stop.txt
  ```

  **Commit**: YES (groups with Wave 0)
  - Message: `fix(engines): real model lifecycle with actual MLX loading`
  - Files: `backend/app/services/engine_registry.py`, `backend/app/api/engines.py`

### Wave 1: Backend Tier-1 Bugs

- [x] 5. Voice CRUD 补全（物理文件删除）

  **What to do**:
  - 修改 `backend/app/services/voice_store.py:45-46`：delete 时同时删除磁盘上的 WAV 文件
  - 删除前检查文件存在，删除后验证文件不存在
  - API 返回值中包含删除确认信息

  **Must NOT do**:
  - 不删除其他 voice 的文件
  - 不添加批量删除功能

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 18
  - **Blocked By**: None

  **References**:
  - `backend/app/services/voice_store.py:45-46` — 当前 delete 不删物理文件
  - `backend/app/api/voices.py` — Voice API 端点
  - `~/VoiceStudio/voices/` — 物理文件存储目录

  **Acceptance Criteria**:
  - [ ] DELETE `/api/voices/{id}` 后磁盘文件也被删除
  - [ ] 删除不存在的文件不报错（静默跳过）

  **QA Scenarios**:
  ```
  Scenario: Voice delete removes physical file
    Tool: Bash (curl)
    Steps:
      1. Upload a test voice via POST /api/voices
      2. Note the file path from GET /api/voices/{id}
      3. DELETE /api/voices/{id}
      4. ls the file path → assert not found
    Expected Result: File removed from disk
    Evidence: .omo/evidence/task-5-voice-delete.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `fix(voices): delete physical file on voice removal`
  - Files: `backend/app/services/voice_store.py`

- [x] 6. Task queue 修复（单实例/cancel/retry）

  **What to do**:
  - 修改 `backend/app/services/task_queue.py`:
    - 添加单实例保证（文件锁或 PID 检查）
    - cancel() 真正中断正在执行的任务
    - retry() 保留原始参数重新入队
  - 确保 worker 异常不会静默吞掉

  **Must NOT do**:
  - 不引入外部消息队列（Redis/Celery 等）

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 22
  - **Blocked By**: None

  **References**:
  - `backend/app/services/task_queue.py` — 当前 worker 实现
  - `backend/app/api/tasks.py` — Tasks API 端点

  **Acceptance Criteria**:
  - [ ] 同时只能有一个 worker 实例运行
  - [ ] DELETE `/api/tasks/{id}` 中断正在执行的任务
  - [ ] POST `/api/tasks/{id}/retry` 保留参数重新执行

  **QA Scenarios**:
  ```
  Scenario: Cancel running task
    Tool: Bash (curl)
    Steps:
      1. POST /api/generate (start long task)
      2. DELETE /api/tasks/{id} (cancel)
      3. GET /api/tasks/{id} → assert status == 'cancelled'
    Expected Result: Task cancelled, no orphaned process
    Evidence: .omo/evidence/task-6-cancel.txt

  Scenario: Retry failed task
    Tool: Bash (curl)
    Steps:
      1. Trigger a task that fails (invalid text)
      2. POST /api/tasks/{id}/retry
      3. Verify new task created with same params
    Expected Result: Retry preserves original parameters
    Evidence: .omo/evidence/task-6-retry.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `fix(tasks): single-instance worker, real cancel, proper retry`
  - Files: `backend/app/services/task_queue.py`

- [x] 7. schemas.py 清理（重复字段 + 孤立类型）

  **What to do**:
  - 修改 `backend/app/models/schemas.py`:
    - Line 139,150,153: `reference_audio_ids` 重复定义，统一为一个
    - Line 260-304: 清理孤立类型（Project/Script 相关，本期不实现）
  - 确保所有 API 端点的 request/response model 正确引用

  **Must NOT do**:
  - 不删除可能被前端使用的字段
  - 不修改 API 契约

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 11, 15
  - **Blocked By**: None

  **References**:
  - `backend/app/models/schemas.py:139,150,153,260-304` — 重复和孤立
  - `backend/app/api/generate.py` — 使用 schemas 的端点

  **Acceptance Criteria**:
  - [ ] `reference_audio_ids` 仅定义一次
  - [ ] 所有 API 端点正常工作（无 import 错误）
  - [ ] 孤立类型有 TODO 注释标记

  **QA Scenarios**:
  ```
  Scenario: Schema cleanup no regression
    Tool: Bash (curl)
    Steps:
      1. Start backend → verify no import errors
      2. GET /api/generate schema endpoint → 200
    Expected Result: All imports resolve, APIs work
    Evidence: .omo/evidence/task-7-schemas.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `fix(schemas): deduplicate reference_audio_ids, mark orphan types`
  - Files: `backend/app/models/schemas.py`

- [x] 8. Duration 计算修正（22050Hz）

  **What to do**:
  - 找到所有使用 44100Hz 计算 duration 的代码
  - 修改为使用实际 sample_rate（从 config 或模型获取）
  - 确保 history 记录的 duration_ms 准确

  **Must NOT do**:
  - 不硬编码 22050（从模型配置读取）

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: None
  - **Blocked By**: 3

  **References**:
  - `models/mlx-indexTTS-2.0/config.json` — sample_rate=22050
  - `backend/app/services/tts_engine.py` — duration 计算位置

  **Acceptance Criteria**:
  - [ ] History 记录的 duration_ms 与实际音频时长一致（误差<100ms）
  - [ ] sample_rate 从模型配置动态获取

  **QA Scenarios**:
  ```
  Scenario: Duration calculation accuracy
    Tool: Bash (Python + curl)
    Steps:
      1. Generate audio via API
      2. Read WAV file duration: wave.open().getnframes() / getframerate()
      3. GET /api/history → compare duration_ms with actual
    Expected Result: duration_ms matches actual ±50ms
    Evidence: .omo/evidence/task-8-duration.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `fix(tts): use actual sample_rate for duration calculation`
  - Files: `backend/app/services/tts_engine.py`

- [x] 9. History API 真实数据

  **What to do**:
  - 确保 `/api/history` 返回真实记录（非 mock duration_ms=1500）
  - 添加分页支持（offset/limit）
  - 删除历史时同时删除磁盘音频文件

  **Must NOT do**:
  - 不删除其他功能的文件

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 21
  - **Blocked By**: None

  **References**:
  - `backend/app/api/history.py` — History API 端点
  - `backend/app/services/history_store.py` — 数据访问层

  **Acceptance Criteria**:
  - [ ] GET `/api/history?limit=10` 返回真实记录
  - [ ] 分页参数正确工作
  - [ ] DELETE 同时删除音频文件

  **QA Scenarios**:
  ```
  Scenario: History returns real records
    Tool: Bash (curl)
    Steps:
      1. Generate audio via POST /api/generate
      2. GET /api/history?limit=10
      3. Assert duration_ms != 1500 (mock value)
    Expected Result: Real duration values from actual generation
    Evidence: .omo/evidence/task-9-history-real.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `fix(history): real data, pagination, file cleanup on delete`
  - Files: `backend/app/api/history.py`, `backend/app/services/history_store.py`

- [x] 10. Backend 错误处理统一

  **What to do**:
  - 添加统一的错误响应格式：`{"error": {"code": "ERROR_CODE", "message": "human readable", "detail": {}}}`
  - 所有 API 端点的异常都经过统一 handler
  - 前端能根据 HTTP 状态码 + error 字段展示友好消息

  **Must NOT do**:
  - 不暴露内部堆栈信息给前端

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 15
  - **Blocked By**: None

  **References**:
  - `backend/app/main.py` — FastAPI app 入口，添加 exception_handler
  - `backend/app/api/*.py` — 所有 API 端点

  **Acceptance Criteria**:
  - [x] 所有 4xx/5xx 返回统一 JSON 格式
  - [x] 无堆栈信息泄露到前端
  **QA Scenarios**:
  ```
  Scenario: Error response format
    Tool: Bash (curl)
    Steps:
      1. GET /api/voices/nonexistent-id
      2. Assert JSON has 'error' field, status 404
    Expected Result: Consistent error format across all endpoints
    Evidence: .omo/evidence/task-10-error-format.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `feat(api): unified error response format`
  - Files: `backend/app/main.py`

- [x] 11. Generate API 接口完善（emotion/参数透传）

  **What to do**:
  - 修改 `backend/app/api/generate.py`：
    - 支持 emotion 参数透传到 IndexTTSv2
    - 支持 emo_alpha、temperature、max_tokens 等参数
  - 确保前端所有 GenerateOptions 字段都能到达后端
  - 使用 Task 7 清理后的 schemas

  **Must NOT do**:
  - 不改变已有的默认值行为

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 15
  - **Blocked By**: 3, 7

  **References**:
  - `backend/app/api/generate.py` — Generate API 端点
  - `backend/app/models/schemas.py` — 清理后的 schemas
  - `mlx_indextts/generate_v2.py` — IndexTTSv2 支持的参数（emotion, emo_alpha, temperature）

  **Acceptance Criteria**:
  - [ ] POST `/api/generate` 支持 emotion 参数
  - [ ] emo_alpha、temperature 参数正确透传
  - [ ] 默认参数不变时行为一致

  **QA Scenarios**:
  ```
  Scenario: Emotion parameter passthrough
    Tool: Bash (curl)
    Steps:
      1. POST /api/generate with emotion=happy, emo_alpha=0.7
      2. Verify generation succeeds
    Expected Result: Audio generated with emotion control
    Evidence: .omo/evidence/task-11-emotion.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `feat(generate): emotion and advanced params passthrough`
  - Files: `backend/app/api/generate.py`

- [x] 12. 后端启动脚本 + 健康检查

  **What to do**:
  - 确保后端启动时自动创建必要目录（~/VoiceStudio/config, outputs, voices）
  - 添加 `/api/health` 端点返回：引擎状态、模型路径、磁盘空间
  - 启动时日志输出关键信息（端口、模型路径、数据目录）

  **Must NOT do**:
  - 不自动下载模型

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocks**: 26
  - **Blocked By**: None

  **References**:
  - `backend/app/main.py` — FastAPI app 入口
  - `~/VoiceStudio/` — 数据目录结构

  **Acceptance Criteria**:
  - [ ] GET `/api/health` 返回 200 + JSON
  - [ ] 启动时自动创建缺失目录

  **QA Scenarios**:
  ```
  Scenario: Health check endpoint
    Tool: Bash (curl)
    Steps:
      1. GET /api/health
      2. Assert status 200, JSON has engine_status field
    Expected Result: Healthy response with system info
    Evidence: .omo/evidence/task-12-health.txt
  ```

  **Commit**: YES (groups with Wave 1)
  - Message: `feat(api): health check endpoint and auto-create data dirs`
  - Files: `backend/app/main.py`

- [x] 13. Frontend API client typed wrapper

  **What to do**:
  - 创建 `frontend/src/lib/api/client.ts`：
    - 封装 fetch 调用，统一错误处理
    - 所有 API 端点的 TypeScript 类型定义
    - 自动重试机制（网络错误时）
  - 替换所有直接 fetch 调用使用新 client

  **Must NOT do**:
  - 不引入 axios 等外部依赖
  - 不改变 API 行为，只封装调用

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 15, 17, 19, 20, 21
  - **Blocked By**: 10

  **References**:
  - `frontend/src/lib/api/client.ts` — 现有 26 行无 typed wrapper
  - `backend/app/api/*.py` — API 端点定义

  **Acceptance Criteria**:
  - [ ] 所有 API 调用使用统一 client
  - [ ] 错误处理统一（网络错误、4xx、5xx）
  - [ ] TypeScript 类型完整

  **QA Scenarios**:
  ```
  Scenario: API client error handling
    Tool: Bash (curl)
    Steps:
      1. 启动后端，停止后端
      2. 前端调用 API，验证错误提示
    Expected Result: 友好错误提示，无静默失败
    Evidence: .omo/evidence/task-13-api-client.txt
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `feat(api): typed API client with unified error handling`
  - Files: `frontend/src/lib/api/client.ts`, `frontend/src/lib/api/*.ts`

- [x] 14. Generate 页面语音路径修复

  **What to do**:
  - 修复 `generate/+page.svelte:70-74`：
    - voice 路径添加 `.wav` 扩展名
    - 使用 voice_id 构建完整路径
  - 确保 favorite 选择后路径正确

  **Must NOT do**:
  - 不改变 voice 数据结构

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 15
  - **Blocked By**: None

  **References**:
  - `frontend/src/routes/generate/+page.svelte:70-74` — voice 路径构建
  - `backend/app/services/voice_store.py` — voice 数据结构

  **Acceptance Criteria**:
  - [ ] voice 路径包含 .wav 扩展名
  - [ ] favorite 选择后路径正确传递到后端

  **QA Scenarios**:
  ```
  Scenario: Voice path correctness
    Tool: Bash (curl)
    Steps:
      1. 选择 favorite voice
      2. 生成音频
      3. 检查后端日志中的 voice 路径
    Expected Result: 路径包含 .wav 扩展名
    Evidence: .omo/evidence/task-14-voice-path.txt
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `fix(generate): voice path with .wav extension`
  - Files: `frontend/src/routes/generate/+page.svelte`

- [ ] 15. Generate 页面 WebSocket 集成

  **What to do**:
  - 替换 120s 轮询为 WebSocket 实时进度：
    - 连接 `/ws/tasks/{task_id}`
    - 显示实时进度百分比
    - 完成后自动获取结果
  - 添加连接状态指示器
  - 断线自动重连

  **Must NOT do**:
  - 不保留轮询作为 fallback
  - 不改变任务状态机

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`typescript-patterns`, `svelte`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 22
  - **Blocked By**: 6, 13, 14

  **References**:
  - `backend/app/api/tasks.py` — WebSocket 端点
  - `frontend/src/routes/generate/+page.svelte` — 当前轮询实现

  **Acceptance Criteria**:
  - [ ] 实时显示生成进度
  - [ ] 完成后自动获取音频
  - [ ] 断线重连机制

  **QA Scenarios**:
  ```
  Scenario: WebSocket real-time progress
    Tool: Bash (curl)
    Steps:
      1. 生成长文本音频
      2. 观察前端进度更新
      3. 验证完成后自动播放
    Expected Result: 实时进度，无 120s 等待
    Evidence: .omo/evidence/task-15-websocket.txt
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `feat(generate): WebSocket real-time progress`
  - Files: `frontend/src/routes/generate/+page.svelte`, `frontend/src/lib/api/tasks.ts`

- [ ] 16. Sidebar 引擎状态真实化

  **What to do**:
  - 修改 `Sidebar.svelte:40-43`：
    - 从 `/api/engines` 获取真实状态
    - 动态显示引擎状态（running/stopped/error）
    - 添加状态颜色指示
  - 定期刷新状态（30s）

  **Must NOT do**:
  - 不硬编码状态值

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 22
  - **Blocked By**: 4, 13

  **References**:
  - `frontend/src/lib/components/Sidebar.svelte:40-43` — 硬编码状态
  - `backend/app/api/engines.py` — 引擎状态 API

  **Acceptance Criteria**:
  - [ ] 引擎状态显示真实值
  - [ ] 状态颜色指示（绿/红/黄）
  - [ ] 30s 自动刷新

  **QA Scenarios**:
  ```
  Scenario: Engine status real-time
    Tool: Bash (curl)
    Steps:
      1. 启动引擎
      2. 检查 Sidebar 状态显示
      3. 停止引擎，观察状态变化
    Expected Result: 状态实时更新，颜色正确
    Evidence: .omo/evidence/task-16-sidebar-status.txt
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `feat(sidebar): real engine status from API`
  - Files: `frontend/src/lib/components/Sidebar.svelte`

- [ ] 17. Layout + InspectorPanel 修复

  **What to do**:
  - 修改 `+layout.svelte`：
    - grid 从 2 列改为 3 列（sidebar / main / inspector）
    - InspectorPanel 默认隐藏，选中内容时显示
  - InspectorPanel 放在正确的 grid-column: 3

  **Must NOT do**:
  - 不改变 Sidebar 和主内容区的布局

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`svelte`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: 13

  **References**:
  - `frontend/src/routes/+layout.svelte:30` — grid 仅 2 列
  - `frontend/src/lib/components/InspectorPanel.svelte` — 永远隐藏

  **Acceptance Criteria**:
  - [ ] Layout 3 列 grid 正确
  - [ ] InspectorPanel 选中内容时可见

  **QA Scenarios**:
  ```
  Scenario: InspectorPanel visibility
    Tool: Playwright
    Steps:
      1. 打开 Generate 页面
      2. 选择一个 voice
      3. 检查 InspectorPanel 是否显示
    Expected Result: InspectorPanel 在右侧可见
    Evidence: .omo/evidence/task-17-inspector.png
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `fix(layout): 3-column grid with InspectorPanel`
  - Files: `frontend/src/routes/+layout.svelte`, `frontend/src/lib/components/InspectorPanel.svelte`

- [ ] 18. Voice Library 修复（试听/删除确认）

  **What to do**:
  - Voice Library 页面：
    - 点击 voice 时播放预览音频
    - 删除时弹出确认对话框
    - 删除成功后刷新列表
  - 使用 Toast 显示删除结果

  **Must NOT do**:
  - 不添加批量删除功能

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: 5, 13

  **References**:
  - `frontend/src/routes/voices/+page.svelte` — Voice Library 页面
  - `backend/app/api/voices.py` — Voice API

  **Acceptance Criteria**:
  - [ ] 点击 voice 可试听
  - [ ] 删除前有确认弹窗
  - [ ] 删除后列表刷新

  **QA Scenarios**:
  ```
  Scenario: Voice preview and delete
    Tool: Playwright
    Steps:
      1. 打开 Voice Library
      2. 点击一个 voice → 检查音频播放
      3. 点击删除 → 确认弹窗 → 确认
      4. 检查列表已刷新
    Expected Result: 试听播放，删除确认，列表更新
    Evidence: .omo/evidence/task-18-voice-library.png
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `fix(voices): preview playback and delete confirmation`
  - Files: `frontend/src/routes/voices/+page.svelte`

- [ ] 19. Engine Hub tabs 修复

  **What to do**:
  - Engine Hub 页面 tabs 切换修复
  - 每个 tab 显示正确的引擎信息
  - 启动/停止按钮状态正确

  **Must NOT do**:
  - 不添加 OmniVoice tab

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: 13

  **References**:
  - `frontend/src/routes/engines/+page.svelte` — Engine Hub 页面
  - `backend/app/api/engines.py` — 引擎 API

  **Acceptance Criteria**:
  - [ ] 每个 tab 点击后对应 panel 可见，其他 panel 隐藏
  - [ ] 启动/停止按钮文字与引擎实际状态一致（running→显示Stop，stopped→显示Start）
  - [ ] 启动/停止按钮状态正确

  **QA Scenarios**:
  ```
  Scenario: Engine Hub tabs
    Tool: Playwright
    Steps:
      1. 打开 Engine Hub
      2. 切换 tabs
      3. 检查每个 tab 内容正确
    Expected Result: Tab 切换正常，内容正确
    Evidence: .omo/evidence/task-19-engine-hub.png
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `fix(engines): Hub tabs switching and button state`
  - Files: `frontend/src/routes/engines/+page.svelte`

- [ ] 20. Settings 页面补全

  **What to do**:
  - Settings 页面：
    - 显示当前设置值
    - 修改后保存到后端
    - 保存成功/失败有 Toast 提示
  - 与 Task 2 修复的后端 Settings API 配合

  **Must NOT do**:
  - 不添加 PRD 未要求的设置项

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: 2, 13

  **References**:
  - `frontend/src/routes/settings/+page.svelte` — Settings 页面
  - `backend/app/api/settings.py` — Settings API

  **Acceptance Criteria**:
  - [ ] 显示当前设置值
  - [ ] 修改后保存成功
  - [ ] Toast 提示保存结果

  **QA Scenarios**:
  ```
  Scenario: Settings save and feedback
    Tool: Playwright
    Steps:
      1. 打开 Settings 页面
      2. 修改 output_dir
      3. 点击保存
      4. 检查 Toast 提示
    Expected Result: 保存成功，Toast 显示
    Evidence: .omo/evidence/task-20-settings.png
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `fix(settings): page with save and toast feedback`
  - Files: `frontend/src/routes/settings/+page.svelte`

- [ ] 21. History 页面功能实现

  **What to do**:
  - History 页面：
    - 从 `/api/history` 获取真实记录
    - 显示生成时间、文本、时长、状态
    - 点击可播放音频
    - 删除时确认并删除磁盘文件
  - 分页加载（无限滚动或分页按钮）

  **Must NOT do**:
  - 不使用 mock 数据

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`typescript-patterns`, `svelte`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: 9, 13

  **References**:
  - `frontend/src/routes/history/+page.svelte` — History 页面 skeleton
  - `backend/app/api/history.py` — History API

  **Acceptance Criteria**:
  - [ ] 显示真实历史记录
  - [ ] 可播放音频
  - [ ] 分页功能正常

  **QA Scenarios**:
  ```
  Scenario: History page real data
    Tool: Playwright
    Steps:
      1. 先生成几段音频
      2. 打开 History 页面
      3. 检查记录列表非空
      4. 点击一条记录播放
    Expected Result: 真实记录显示，可播放
    Evidence: .omo/evidence/task-21-history.png
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `fix(history): page with real data, playback, pagination`
  - Files: `frontend/src/routes/history/+page.svelte`

- [ ] 22. WebSocket 客户端接入（替代轮询）

  **What to do**:
  - 创建 `frontend/src/lib/api/websocket.ts`：
    - WebSocket 连接管理
    - 自动重连机制
    - 事件分发
  - 在 Generate 页面使用 WebSocket 替代 120s 轮询
  - 显示连接状态指示器

  **Must NOT do**:
  - 不保留轮询作为 fallback
  - 不改变任务状态机

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: 27
  - **Blocked By**: 6, 11, 13

  **References**:
  - `backend/app/api/tasks.py` — WebSocket 端点
  - `frontend/src/routes/generate/+page.svelte` — 当前轮询实现

  **Acceptance Criteria**:
  - [ ] WebSocket 连接成功
  - [ ] 实时接收任务进度
  - [ ] 断线自动重连

  **QA Scenarios**:
  ```
  Scenario: WebSocket connection and progress
    Tool: Playwright
    Steps:
      1. 启动后端
      2. 打开 Generate 页面
      3. 生成音频
      4. 观察进度条实时更新
    Expected Result: 实时进度，无 120s 等待
    Evidence: .omo/evidence/task-22-websocket.png
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `feat(websocket): client with auto-reconnect`
  - Files: `frontend/src/lib/api/websocket.ts`, `frontend/src/routes/generate/+page.svelte`

- [x] 23. TailwindCSS v4 配置修正

  **What to do**:
  - 检查 `frontend/tailwind.config.ts` 或 `frontend/postcss.config.js`
  - 确保 TailwindCSS v4 配置正确
  - 修复任何样式编译错误
  - 确保 `pnpm build` 成功

  **Must NOT do**:
  - 不升级 TailwindCSS 版本
  - 不改变现有样式逻辑

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `frontend/tailwind.config.ts` — TailwindCSS 配置
  - `frontend/postcss.config.js` — PostCSS 配置

  **Acceptance Criteria**:
  - [ ] `pnpm build` 成功
  - [ ] `pnpm dev` 启动后浏览器打开页面，CSS 类生效（检查按钮有背景色、间距正确）

  **QA Scenarios**:
  ```
  Scenario: TailwindCSS build success
    Tool: Bash
    Steps:
      1. cd frontend && pnpm build
      2. 检查输出无错误
    Expected Result: 构建成功
    Evidence: .omo/evidence/task-23-tailwind.txt
  ```

  **Commit**: YES (groups with Wave 2)
  - Message: `fix(tailwind): v4 config correction`
  - Files: `frontend/tailwind.config.ts`, `frontend/postcss.config.js`

- [ ] 24. Tasks 页面实现

  **What to do**:
  - Tasks 页面：
    - 从 `/api/tasks` 获取任务列表
    - 显示任务状态、进度、创建时间
    - 支持取消和重试操作
  - 实时更新（WebSocket）

  **Must NOT do**:
  - 不实现批量操作

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`typescript-patterns`, `svelte`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: 27
  - **Blocked By**: 13

  **References**:
  - `frontend/src/routes/tasks/` — 空目录
  - `backend/app/api/tasks.py` — Tasks API

  **Acceptance Criteria**:
  - [ ] 显示任务列表
  - [ ] 可取消和重试
  - [ ] 实时状态更新

  **QA Scenarios**:
  ```
  Scenario: Tasks page functionality
    Tool: Playwright
    Steps:
      1. 生成几段音频
      2. 打开 Tasks 页面
      3. 检查任务列表
      4. 取消一个任务
    Expected Result: 任务显示，操作正常
    Evidence: .omo/evidence/task-24-tasks.png
  ```

  **Commit**: YES (groups with Wave 3)
  - Message: `feat(tasks): page with list, cancel, retry`
  - Files: `frontend/src/routes/tasks/+page.svelte`

- [ ] 25. 端口冲突解决 + 启动脚本

  **What to do**:
  - 检查端口 :8000 和 :5173 是否被占用
  - 如果占用，终止相关进程
  - 创建一键启动脚本 `start.sh`：
    - 检查端口
    - 启动后端
    - 启动前端
    - 打开浏览器

  **Must NOT do**:
  - 不修改系统级端口配置

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: 27
  - **Blocked By**: None

  **References**:
  - `~/VoiceStudio/` — 数据目录
  - `backend/app/main.py` — 后端启动
  - `frontend/package.json` — 前端启动

  **Acceptance Criteria**:
  - [ ] 端口冲突解决
  - [ ] 一键启动脚本可用

  **QA Scenarios**:
  ```
  Scenario: Port conflict resolution
    Tool: Bash
    Steps:
      1. 检查端口占用
      2. 终止占用进程
      3. 运行 start.sh
      4. 验证服务启动
    Expected Result: 端口空闲，服务启动
    Evidence: .omo/evidence/task-25-port.txt
  ```

  **Commit**: YES (groups with Wave 3)
  - Message: `fix(env): port conflict resolution and startup script`
  - Files: `start.sh`

- [ ] 26. Script Studio 占位页面完善

  **What to do**:
  - Script Studio 页面：
    - 显示“即将推出”提示
  - 确保导航正常

  **Must NOT do**:
  - 不实现实际功能

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `frontend/src/routes/script-studio/+page.svelte` — Script Studio 页面

  **Acceptance Criteria**:
  - [ ] 页面显示“即将推出”
  - [ ] 导航正常

  **QA Scenarios**:
  ```
  Scenario: Script Studio placeholder
    Tool: Playwright
    Steps:
      1. 打开 Script Studio 页面
      2. 检查“即将推出”提示
      3. 点击返回导航
    Expected Result: 占位页面显示，导航正常
    Evidence: .omo/evidence/task-26-script-studio.png
  ```

  **Commit**: YES (groups with Wave 3)
  - Message: `feat(script-studio): placeholder page`
  - Files: `frontend/src/routes/script-studio/+page.svelte`

- [ ] 27. 全链路集成测试

  **What to do**:
  - 端到端测试：
    - 启动后端和前端
    - 测试 Generate 页面完整流程
    - 测试 Voice CRUD 流程
    - 测试 Settings 保存流程
    - 测试 History 查看流程
  - 验证所有“Must Have”条件

  **Must NOT do**:
  - 不修改代码，纯测试

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`python-patterns`, `typescript-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (sequential)
  - **Blocks**: F1-F4
  - **Blocked By**: 15, 22, 24

  **References**:
  - 所有后端 API 端点
  - 所有前端页面

  **Acceptance Criteria**:
  - [ ] 所有“Must Have”条件验证通过
  - [ ] Generate→Voice→Settings→History 全流程每个步骤返回 HTTP 200，前端无 console.error

  **QA Scenarios**:
  ```
  Scenario: End-to-end integration
    Tool: Playwright + Bash
    Steps:
      1. 启动后端和前端
      2. Generate 页面：选 voice → 输入文本 → 生成 → 播放 → 存 history
      3. Voice Library：上传 → 试听 → 删除
      4. Settings：修改 → 保存 → 验证
      5. History：查看 → 播放 → 删除
    Expected Result: 所有流程无错误
    Evidence: .omo/evidence/task-27-integration.png
  ```

  **Commit**: NO (测试完成，无代码修改)

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist in .omo/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Review all changed files for: type ignores, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names. Verify no mock fallback patterns remain.
  Output: `Files [N clean/N issues] | Mock patterns [CLEAN/N found] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state (kill port 8000/5173, restart backend + frontend). Execute EVERY QA scenario from EVERY task. Test cross-task integration: Generate flow end-to-end, Voice CRUD cycle, Settings round-trip. Save to `.omo/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec. Check "Must NOT do" compliance. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | VERDICT`

---

## Commit Strategy

- **Wave 0**: `fix(backend): MLX verification, settings round-trip, mock cleanup, engine lifecycle`
- **Wave 1**: `fix(backend): Tier-1 bug fixes - voice CRUD, task queue, schemas, duration, history`
- **Wave 2**: `fix(frontend): Tier-1 bug fixes - toast, API client, generate, layout, pages`
- **Wave 3**: `feat(frontend): tasks page, script studio placeholder, env cleanup`
- 每波完成后 `pnpm build` + `curl health` 验证

---

## Success Criteria

### Verification Commands
```bash
# Backend health
curl -s http://localhost:8000/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status')=='healthy'"

# MLX real inference (verify not mock)
uv run python -c "
from mlx_indextts.generate_v2 import IndexTTSv2
tts = IndexTTSv2('models/mlx-indexTTS-2.0')
audio = tts.generate(text='测试', reference_audio='ref.wav', output_path='/tmp/vs_test.wav')
import wave; w=wave.open('/tmp/vs_test.wav'); assert w.getframerate()==22050; assert w.getnframes()/w.getframerate()>0.5
"

# Settings round-trip
curl -s -X PATCH http://localhost:8000/api/settings -H 'Content-Type: application/json' -d '{"output_dir":"/tmp/vs_test"}'
curl -s http://localhost:8000/api/settings | python3 -c "import sys,json; assert json.load(sys.stdin)['output_dir']=='/tmp/vs_test'"

# Frontend build
cd frontend && pnpm build

# No mock patterns
grep -r "mock\|fake_audio\|np.zeros\|silence" backend/app/services/tts_engine.py && echo "FAIL" || echo "OK"
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] MLX inference verified real (not mock)
- [ ] All API endpoints return correct data
- [ ] Frontend all pages interactive with error feedback
- [ ] Zero silent errors / swallowed exceptions
