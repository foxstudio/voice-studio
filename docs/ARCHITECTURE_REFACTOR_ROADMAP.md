# Voice Studio 架构重构路线图

## 目标

把 Voice Studio 从“功能持续演进的本地语音工具”整理成一个可长期维护的产品型业务系统。目标不是推倒重写，也不是拆成微服务，而是保持本地单体部署，同时让内部边界像大型业务系统一样清晰。

推荐目标形态：**模块化单体 + 领域分层 + 统一工作流编排 + 引擎适配层**。

## 核心原则

- 不做一把梭重写。
- 不改变已有 API URL、数据库记录、任务 payload 和用户真实数据，除非单独评审。
- 不为了“面向对象”而硬塞 class；只有当对象能稳定表达业务概念时才引入。
- 每个重构批次必须有契约测试、回滚边界和验证命令。
- 新功能优先按新架构建设，旧功能逐步迁移。
- `gpt-5.3-codex-spark` 子代理可以承担边界清晰的只读梳理、文档、契约测试和小范围代码改造；主线程负责路线、整合、验收和风险判断。

## 目标分层

```text
backend/app/
  api/                 # FastAPI 路由，只做请求/响应和权限/参数入口
  core/                # 配置、路径、存储、数据库、错误、日志、系统生命周期
  workflows/           # 统一任务状态机、队列、恢复、取消、重试、进度广播
  domains/             # 业务域：生成、ASR、批量、项目、视频本土化
  engines/             # 引擎 provider、policy、health、request builder、runner
  schemas/             # Pydantic schema 和版本化数据契约
```

当前不需要一次性迁到这个目录。先在现有 `services/` 旁边逐步建立新模块，旧入口通过 facade 保持兼容。

## 业务域边界

未来新增复杂功能时，优先按业务域组织：

- `generation`：单条语音合成。
- `longform`：长文本分段与校对。
- `batch`：批量任务。
- `asr`：语音识别、时间戳补齐、字幕导出。
- `voice_library`：音色资产、参考音频、授权、情绪与标签。
- `project`：项目、角色、脚本段落、跨域索引。
- `video_localization`：外文视频中文配音工作台。

每个业务域至少包含：

```text
domains/<domain>/
  schemas.py           # 输入、输出、持久化 payload、错误码
  service.py           # 业务用例入口
  store.py             # 持久化读写，不混入业务规则
  workflow.py          # 长任务或多阶段流程
  quality_gate.py      # 可提交/可导出/可完成的检查
  export.py            # JSON/SRT/音频清单等导出
```

不是每个域都必须马上具备全部文件；从有真实职责的文件开始。

## 优先级

### P0：契约和事实基线

目的：重构前先知道什么不能坏。

任务：

- 固化现有关键 API 响应和错误码。
- 固化任务状态机语义。
- 固化引擎参数映射。
- 固化 project / history / task / batch 的兼容 payload。
- 给视频本土化新增 `domain-contract.md` 和 JSON schema。

验收：

- `pnpm --dir frontend run check`
- `uv run python -m compileall -q backend/app`
- `uv run pytest tests/test_task_orchestration_contract.py tests/test_engine_parameter_contract.py`
- `uv run pytest tests/test_schema_compatibility.py tests/test_task_queue_stale.py tests/test_engine_policy.py`
- `uv run pytest tests/test_video_localization_project.py`
- 相关新增 domain contract tests 通过。

### P1：统一任务平台

目的：把任务运行、状态、恢复、取消、重试从各业务模块里抽出来。

优先处理：

- `backend/app/services/task_queue.py`
- `backend/app/services/longform_queue.py`
- `backend/app/services/batch_queue.py`
- `backend/app/services/asr_tasks.py`

目标：

- 统一 `TaskStatus` 状态机。
- 统一 terminal 状态写保护。
- 统一取消、重试、恢复规则。
- 统一进度事件和错误上下文。
- 业务域只提交 workflow step，不直接管理全局队列。

建议迁移方式：

1. 先抽纯函数和状态判断 helper。
2. 再抽 `TaskStore` / `TaskStateService`。
3. 再把 worker 执行入口改成统一接口。
4. 最后让 longform / batch / asr 复用同一套状态服务。

禁止：

- 不直接改 DB 表结构。
- 不重写所有 queue。
- 不把失败任务静默重跑。
- 不删除历史结果。

验收：

- `uv run pytest tests/test_task_orchestration_contract.py tests/test_task_queue_stale.py`
- `uv run pytest tests/test_longform_queue.py tests/test_asr_tasks.py`
- 覆盖 `TASK_NOT_FOUND`、取消态保护、stale 任务恢复、终态写保护。

### P2：引擎适配层收口

目的：新增模型或调整模型参数时，不再到处改分支。

当前已有基础：

- `engine_policy.py`
- `engine_manifests.py`
- `engine_health.py`
- `engine_request_builder.py`
- `engine_runner.py`
- `engine_provider.py`
- `engine_registry.py` 兼容 facade

下一步：

- 将 provider-specific 逻辑逐步迁到 `engines/providers/`。
- 保持 `engine_registry` 作为旧接口兼容层。
- 新增 `faster-whisper-turbo` 时必须走 provider / manifest / policy / health / request builder，而不是直接塞进 ASR API 分支。

验收：

- 引擎列表 response shape 不变。
- 单次、批量、诊断路径参数一致。
- 旧 engine id 和 MiMo legacy alias 兼容。
- `uv run pytest tests/test_engine_provider.py tests/test_engine_policy.py tests/test_persistent_worker_protocol.py`
- `uv run pytest tests/test_mimo_cloud_contract.py tests/test_engine_parameter_contract.py`

### P3：业务域模块化

目的：把“业务规则”从大 service 文件中分离出来。

优先顺序：

1. `video_localization`：新功能按新架构做，作为样板。
2. `asr`：把 ASR 任务和时间戳补齐整理成域服务。
3. `generation`：把单条生成从 queue 细节中拆出业务用例。
4. `batch` / `longform`：共享 workflow 平台后再收敛。
5. `project`：逐步从通用 JSON 容器升级成版本化 domain payload。

迁移策略：

- 新域先接入旧 store / 旧 queue，保证能跑。
- 新域有 contract tests 后，再替换内部实现。
- 旧 API 不删除，只改为委派到新域 service。

### P4：前端状态分层

目的：页面不要继续变成几千行业务脚本。

推荐结构：

```text
frontend/src/lib/api/
  video-localization.ts
  task-client.ts
  project-client.ts

frontend/src/lib/stores/
  video-localization.ts
  task-feed.ts

frontend/src/routes/video-localization/
  +page.svelte
  components/
```

原则：

- 页面负责布局和事件绑定。
- store 负责数据加载、派生状态、保存草稿、质量门结果。
- API client 负责后端通信。
- 大型表格、编辑器、参考音色池拆组件。

验收：

- 页面没有大段业务规则。
- 质量门结果可单元测试。
- `svelte-check` 和关键 Vitest 通过。

### P5：后清理与收口

目的：在新边界稳定后，再清理旧入口和重复路径。

任务：

- 将 `services/` 中真正属于 `workflows` / `domains` 的代码迁移完成。
- 让 `services/` 逐步变成兼容层或集成层，而不是继续承载全部业务规则。
- 逐步更新导入路径到 `app.schemas` / `app.errors`，保留 `app.models` 兼容直到有明确版本切换。
- 更新架构图、迁移日志和运行手册。

验收：

- 后端相关测试通过。
- `pnpm --dir frontend run check` 通过。
- API URL、核心 response shape、已有数据文件路径无破坏性变化。

## 视频本土化作为样板模块

视频本土化应成为第一块按新架构建设的复杂业务域。

建议后端结构：

```text
backend/app/api/video_localization.py
backend/app/domains/video_localization/
  contracts.py
  schemas.py
  service.py
  store.py
  workflow.py
  quality_gate.py
  export.py
  planner.py
  tts_plan.py
  types.py
```

文件职责：

- `contracts.py`：草稿、导出和 schema version 的领域契约。
- `store.py`：基于 `project_store` 封装读写，不直连数据库。
- `service.py`：对 API 暴露的用例入口。
- `workflow.py`：导入、分离、ASR、说话人、校对、TTS 计划、导出等阶段状态。
- `quality_gate.py`：是否可提交、可导出、可批量生成。
- `planner.py` / `tts_plan.py`：单条和批量 TTS manifest 生成。
- `export.py`：production JSON、字幕草稿、参考音清单。
- `types.py`：cue、speaker、artifact、review 状态等领域类型。

建议工作流阶段：

1. `ingest`：导入视频、记录元数据、抽取音轨。
2. `separate_audio`：人声和背景声分离，记录 stems。
3. `transcribe`：英文 ASR，默认 `faster-whisper-turbo`，保留 qwen3 / mimo 兜底。
4. `diarize`：说话人初稿和参考音候选。
5. `review_cues`：人工校对三轨文本、speaker、参考音。
6. `plan_tts`：生成可提交的单条/批量 TTS manifest。
7. `export`：导出 production JSON、字幕草稿、参考音清单。

V1 质量门：

- 每个待生成 cue 有时间码、speaker、英文字幕、中文字幕、TTS 台词。
- clone 路线必须绑定干净分离人声参考音。
- 参考音必须有独立 ASR 文本。
- 混合说话必须拆分或标记 `preserve_original_audio`。
- 中文显示字幕和 TTS 台词必须分轨保存。
- 导出 JSON 必须包含 schema version、输入素材、模型参数、质量门结果。

## 测试和治理

### 必需测试类型

- Contract tests：API response、错误码、schema 兼容。
- State tests：任务状态转换、取消、重试、恢复。
- Mapping tests：引擎参数、TTS manifest、ASR route。
- Store tests：草稿保存、版本迁移、导出 JSON。
- E2E smoke：核心页面打开、关键按钮、无控制台错误。

### 每个重构批次交付要求

- 修改文件列表。
- 行为变化说明。
- 是否改变 API / DB / 真实数据。
- 验证命令和结果。
- 剩余风险。
- 下一批建议。

### 子代理使用规则

适合委派给 `gpt-5.3-codex-spark`：

- 只读模块盘点。
- 契约测试草案。
- 小范围接口 wrapper。
- 文档同步。
- 单文件或低耦合 helper 抽取。
- 前端组件拆分中的无业务判断部分。

不适合委派：

- 最终架构决策。
- 跨模块状态机改造。
- 数据库迁移。
- 删除/移动真实数据。
- 引擎行为变更。
- 需要产品取舍的功能设计。

## 明确不做

- 不改变 `/api/health`、`/api/tasks`、`/api/projects/...` 的 URL 与返回 shape，除非单独批准。
- 不改数据库表结构，不迁移历史 `voice_studio.db`。
- 不移动真实模型目录、`~/VoiceStudio` 实体数据、音频文件、导出产物持久路径。
- 不一次性重写 `generate` 页面和 `/video-localization` 页面全部 UI；先打通真实数据，再拆组件。
- 不为视频本土化 V1 新增数据库表；继续使用 `Project.parameters.video_localization` 保存草稿。
- 不把微服务、外部消息队列、事件总线作为第一轮主方案。
- 不破坏 `engine_id`、任务状态语义、错误码和 MiMo 幂等相关字段。

## 近期建议路线

### 第 1 批：文档和契约

- 完成本文档。
- 新增 `docs/domains/video_localization/domain-contract.md`。
- 新增视频本土化 schema contract tests。
- 补充当前 `/video-localization` 页面功能状态说明。

### 第 2 批：视频本土化 store 与真实草稿

- 前端新增 `lib/stores/video-localization.ts`。
- 页面从静态样例改为真实草稿数据。
- 支持保存草稿和导出 JSON。
- 不接视频上传，不接 ASR。

### 第 3 批：视频导入 V1

- 导入视频文件。
- 记录文件名、大小、时长、音轨元数据。
- 保存到项目目录或缓存目录。
- 不做人声分离。

### 第 4 批：ASR adapter

- 新增 `faster-whisper-turbo` engine manifest / policy / health。
- 新增本地 adapter。
- 只支持从音频文件转英文字幕初稿。

### 第 5 批：人声分离和参考音候选

- 接入人声/背景声 stem 记录。
- 生成干净参考音候选清单。
- 每个参考音独立 ASR。

### 第 6 批：TTS 交接

- 单条 cue 发送到 `/generate`。
- 批量生成 manifest。
- TTS 结果回写 cue。

## 长期方向

当视频本土化跑通后，再反向抽象共用能力：

- `WorkflowOrchestrator`
- `DomainStore`
- `QualityGate`
- `ArtifactRegistry`
- `EngineProvider`
- `TaskEventFeed`

这些抽象应该从真实功能中长出来，而不是提前空降。
