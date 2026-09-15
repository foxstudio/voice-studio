# 架构文档导航

开发前先看根目录 `AGENTS.md`，再按本页选择需要阅读的当前资料。不要把日期型审计、路线图或实施报告当成当前代码事实。

## 当前依据

| 范围 | 当前权威说明 |
| --- | --- |
| 整体系统、分层、公共入口、状态所有权 | [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) |
| 视频本土化模块地图 | [视频本土化领域 README](../../backend/app/domains/video_localization/README.md) |
| 视频本土化可重建缓存 | [VIDEO_LOCALIZATION_CACHE.md](VIDEO_LOCALIZATION_CACHE.md) |
| 视频本土化持久数据契约 | [domain-contract.md](../domains/video_localization/domain-contract.md) |
| 本地数据、模型和文件禁区 | [VOICE_STUDIO_DATA_POLICY.md](../VOICE_STUDIO_DATA_POLICY.md) |
| 操作系统、设备与可选运行能力 | [RUNTIME_CAPABILITIES.md](RUNTIME_CAPABILITIES.md) |
| VibeVoice ASR 模型、基准与自动选择 | [VIBEVOICE_ASR_MODELS.md](../guides/VIBEVOICE_ASR_MODELS.md) |
| 开源发布物、平台与模型分发边界 | [OPEN_SOURCE_RELEASE.md](../OPEN_SOURCE_RELEASE.md) |
| 引擎能力和参数 | [engines](../engines/) |

## 决策与 RFC

RFC 只在与当前代码一致或明确标记已接受时作为约束。`proposal`、`partially implemented` 和 `in progress` 都不是当前实现依据，动手前必须核对真实调用链。

| 文档 | 当前状态 | 使用方式 |
| --- | --- | --- |
| `SCHEMA_COMPATIBILITY_RFC.md` | partially implemented | `app.schemas` / `app.errors` 兼容入口已落地；全量导入迁移未完成 |
| `ENGINE_PROVIDER_POLICY_RFC.md` | in progress | 仅用于继续渐进拆分引擎边界，不能假设目标结构已全部存在 |
| `TASK_ORCHESTRATION_RFC.md` | phase 1 specification | 作为统一任务语义的方向；各队列仍保留当前真实实现 |
| `VIDEO_LOCALIZATION_OPERATION_LEDGER_RFC.md` | accepted / in progress | 约束视频本土化 operation 的 claim/lease/fencing、权威切换、付费调用和崩溃恢复；当前实现状态仍以系统文档与代码为准 |
| `SETTINGS_SYSTEM_RFC.md` | proposal | 只用于未来设置重构，不描述当前设置页 |
| `DIRECTORY_GOVERNANCE_RFC.md` | proposal | 只用于未来目录治理，未执行迁移 |

## 计划、审计和报告

以下类型只保存历史证据或待办背景，不是当前架构真相：

- 文件名含 `PLAN`、`ROADMAP`、`AUDIT`、`REPORT` 或日期的文档。
- 某次重构协作计划、阶段清单、代码行数和测试数量。
- 一次性接口探测、问题复盘和实现记录。

当前视频本土化跨层审计、优化阶段和测试门禁统一记录在
[VIDEO_LOCALIZATION_ARCHITECTURE_AUDIT_ROADMAP.md](VIDEO_LOCALIZATION_ARCHITECTURE_AUDIT_ROADMAP.md)。
该文档状态为 `proposal / working roadmap`，用于安排后续优化，不描述已经完成的架构。
其中可变的文件、路由、import DAG、前端入口可达性和测试形状通过只读脚本
`scripts/audit_video_localization_architecture.py` 重新生成，禁止手工复制旧统计作为
当前证据。
`scripts/audit_video_localization_architecture.py --check-policy` 是 quick/full 都会
执行的防倒退门禁；允许清单位于
`scripts/architecture/video_localization_architecture_policy.json`。允许项只能随治理
减少，新增循环依赖、跨层 importer、无 await 的 async 路由、async 路由中未卸载的
本地应用调用、不可达 Svelte 组件或源码字符串测试会失败。
生产构建后的 `/video-localization` 体积由
`scripts/audit_video_localization_bundle.mjs --check` 读取 Vite manifest 并执行预算；
`--json` 可输出机器可读证据。full 门禁会先构建再检查，quick 门禁只运行 analyzer
自身的固定 fixture 单测。

当历史文档与 `AGENTS.md`、`SYSTEM_ARCHITECTURE.md`、领域 README、版本化 Schema 或当前代码冲突时，以后者为准。

## 维护规则

- 整体架构事实只维护在 `SYSTEM_ARCHITECTURE.md`；强制规范只维护在根 `AGENTS.md`。
- 领域模块和数据契约在对应领域 README/contract 中维护。
- 新文档要说明它是 `current`、`accepted RFC`、`proposal` 还是 `historical report`。
- 文档不记录聊天过程和“本轮做了什么”；只保留以后做决策仍有用的信息。
