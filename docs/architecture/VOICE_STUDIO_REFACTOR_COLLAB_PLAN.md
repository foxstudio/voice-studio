# Voice Studio Refactor Collaboration Plan

**Owner**: 用户  
**总设计师 / 首席产品经理 / 架构验收**: Codex  
**执行工程师**: Codex sub-agents, 优先使用 `gpt-5.3-codex-spark` 处理边界清晰的小批次  
**当前日期**: 2026-06-11

## 1. 协作原则

本项目采用“小步批次 + 验收门禁”的方式推进。

- 前期只做低风险批次，每批解决一个明确问题包。
- 每批完成后必须给出修改文件、行为变化、验证命令、剩余风险。
- 高风险阶段必须先有 RFC / 测试契约，再改实现。
- 不允许擅自删除、移动、重写真实用户数据。
- 不允许一次性做跨阶段大重构。
- 不允许为了修测试降低测试价值；测试与实现冲突时先报告。

## 2. 角色分工

### 用户

- 决定是否进入高风险阶段。
- 确认涉及真实数据、目录迁移、API 兼容变化的方案。
- 接收每批验收结论和下一批指令。

### Codex

- 负责总路线、产品判断、架构验收和批次拆分。
- 审核 sub-agent diff、测试结果和风险说明。
- 在进入高风险实现前明确验收门槛。
- 可以委派 Spark 子代理完成边界清晰的代码/测试/文档任务。

### Spark 子代理

- 只按给定批次执行。
- 不做产品判断，不扩大范围。
- 不触碰真实数据、模型权重、`~/VoiceStudio`，除非批次明确允许只读审计。
- 交付时必须说明是否改动后端实现、前端、真实数据。

## 3. 当前进度

### Phase 0: 修绿与事实基线

状态：已完成，可验收。

已完成批次：

1. 前端检查修复
   - 修复 generate longform resolver 类型。
   - 修复 voice-library Svelte action 类型。
   - 修复低风险 a11y warning。
   - 验证：`pnpm --dir frontend check` 通过，`pnpm --dir frontend build` 通过。

2. 后端测试修复
   - 修复 F5/CosyVoice worker 测试对本机外部 root env 的依赖。
   - 保持 OmniVoice longform 阈值实现为 80，更新测试期望。
   - 删除 `voice_store.list_voices()` 不可达 return。
   - 验证：相关 pytest 通过，`compileall` 通过。

3. 只读数据 manifest 脚本
   - 新增 `scripts/migration/audit_voice_studio_data.py`。
   - 默认 dry-run，不写 manifest。
   - `--write-manifest` 时才写入 manifest。
   - 不修改 DB、不复制、不删除真实数据。

遗留事项：

- OmniVoice longform threshold 文档已与当前实现和测试统一为 80/150。
- FastAPI `on_event` deprecation warning 暂不处理，放入后续生命周期治理。

### Phase 1: 任务编排 RFC 与回归测试

状态：RFC 与第一批契约测试已完成，可作为 Phase 2 输入。

已完成批次：

1. `docs/architecture/TASK_ORCHESTRATION_RFC.md`
   - 定义统一状态机。
   - 定义重启恢复、取消、超时、重试、云端/MiMo 防重复提交。
   - 明确 terminal 状态冻结和迟到事件保护。

2. `tests/test_task_orchestration_contract.py`
   - 普通测试锁住当前可满足行为。
   - 原先的任务编排 xfail 已在后续 Phase 2/3 批次中转为 passing。

当前验证结果：

- `pnpm --dir frontend check`: 通过，0 errors，0 warnings。
- `pnpm --dir frontend build`: 通过。
- `.venv/bin/python -m compileall -q backend/app scripts/migration/audit_voice_studio_data.py tests/test_task_orchestration_contract.py`: 通过。
- `.venv/bin/python -m pytest tests -q`: `154 passed, 4 skipped, 7 warnings`。
- `.venv/bin/python scripts/migration/audit_voice_studio_data.py`: dry-run 通过，未写 manifest。

## 4. 后续路线

### Phase 2: 统一任务执行层

状态：第一轮已完成。Batch 1/2/3 已分别覆盖 `task_queue`、`longform_queue`、`batch_queue` 的小步语义加固；后续已补齐 batch retry/cancel/partial-success 契约。

目标：收敛 `task_queue`、`longform_queue`、`batch_queue` 的重复 worker、恢复、取消、超时逻辑。

进入条件：

- Phase 0 验收通过。
- Phase 1 RFC 与契约测试验收通过。
- 用户确认允许进入高风险后端实现阶段。

执行顺序：

1. `task_queue` 小步改造
   - 优先修入队幂等、恢复一致性、terminal 写保护。
   - 不改变外部 API URL。
   - 不引入大型框架。
   - 当前状态：已完成 Batch 1。单任务取消后迟到成功不会再写入 `success` / history，相关 xfail 已转为 passing。

2. `longform_queue` 小步改造
   - 与单任务共享状态判断/恢复 helpers。
   - 明确 parent/segment 失败传播。
   - 当前状态：已完成 Batch 2。长文取消不会覆盖已成功段，未完成段会保持 cancelled；失败段错误上下文与 retry_failed 不重算成功段已由测试锁住。

3. `batch_queue` 小步改造
   - 与共享 helpers 对齐。
   - 保持默认行为兼容，partial-success 另行产品决策。
   - 当前状态：已完成 Batch 3 与后续稳定性补强。默认批处理仍保持“任一失败则 parent failed”；显式 `partial_success=true` 时允许 parent success；retry/cancel 不覆盖已成功 segment。

验收标准：

- Phase 1 契约测试已全部转为普通 passing tests。
- 既有 smoke 流程不变。
- 不改变 API URL。
- 不触碰真实数据和模型权重。

### Phase 3: 统一 runner 与参数 schema

状态：已完成 Batch 0/1/2 与后续稳定性补强。当前已完成参数漂移审计、两个明确漂移修复、F5/CosyVoice persistent worker stdout/stderr 错误上下文加固，以及 MiMo/cloud 本地幂等 marker 与恢复保护。

目标：消除单次、批量、诊断、外部 worker 的参数漂移。

方向：

- 新增统一参数 normalization 层。
- 单次/批量/诊断复用同一映射。
- worker stdout/stderr 结构化，非 JSON 行进入日志上下文。
- MiMo preset / voicedesign / voiceclone 保持独立 profile。

### Phase 4: 引擎抽象与目录治理

状态：后端抽象与目录治理主线已完成当前安全批次。已完成 engine provider/policy RFC，落地 `engine_policy`、`engine_manifests.py`、`engine_health.py`、`engine_request_builder.py`、`engine_runner.py`、`engine_provider.py`；`docs/models` 已迁到 `docs/engines` 并保留过渡说明；schema compatibility facade 已落地，tests/services/api imports 已分层迁移；script path checker 门禁已清零。当前未改变 API response、DB schema，未移动真实数据或模型权重。

目标：让 engine capabilities/policy 成为单一事实源，再做低风险目录调整。

方向：

- 引入 engine provider / policy。
- 前端逐步消费 capabilities。
- 目录调整只做兼容迁移，不移动 `~/VoiceStudio` 或项目内 `models/` 权重。

### Phase 5: WebUI 产品化

目标：把 WebUI 整理为工作台、资产库、历史管理。

顺序：

1. `/history` 独立页面。
2. `/voice-library` 资产库升级。
3. `/generate` 工作台整理。

## 5. 下一批建议指令

Phase 4 后端抽象与目录治理当前安全批次已完成：policy / manifest / health / runner / request builder / provider skeleton 已拆分，`docs/models` 已迁到 `docs/engines`，schema compatibility imports 已分层迁移，script path dry-run checker 的 `--fail-on-risk` 门禁已清零。下一步进入 Phase 5 WebUI 产品化，优先处理 `/generate` 工作台当前可见布局和交互问题，再逐步拆 `/history` 与 `/voice-library`。

```text
你现在在 /Users/foxmacstudio/Projects/mlx-indextts 工作。

本轮进入 Phase 5 Batch 1：稳定 `/generate` 工作台结果卡片与筛选工具栏。不要修改后端 API、DB schema、真实数据或模型权重。

目标：
1. 保留当前用户已在本地看到的 `/generate` 页面改动，先读取 diff，不覆盖未提交前端修改。
2. 修复结果卡片 header：checkbox 左上、标题单行省略、状态 badge 右上。
3. 修复音频播放/下载按钮左对齐，与复用/删除按钮尺寸一致。
4. 修复结果工具栏：状态 tabs 单独一行；搜索/模型/来源/时间/排序左对齐并自适应；分页和批量操作右对齐并自适应折行。
5. 保证页面可滚动，所有无文字图标按钮有延迟 tooltip。

验证：
- pnpm --dir frontend check
- pnpm --dir frontend build
- 浏览器打开 http://localhost:5173/generate 做桌面宽屏和窄屏截图检查。

交付必须包含：
- 修改文件列表
- WebUI 布局/交互摘要
- 是否改变后端实现逻辑、外部 API/DB schema
- 验证命令与结果
- 是否触碰真实数据、模型权重、~/VoiceStudio

禁止：
- 不要重写 generate 页面业务流程
- 不要扩大到后端架构重构
- 不要修改真实数据
- 不要运行 formatter 改全项目
- 不要删除 .omo/.sisyphus/.playwright-cli
```
