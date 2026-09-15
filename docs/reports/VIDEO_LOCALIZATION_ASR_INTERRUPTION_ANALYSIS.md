# 视频本土化 ASR 中断诊断与架构优化建议

> 状态：historical diagnostic report
> 观察时间：2026-08-04
> 适用对象：项目 `1461afc465ed` 的 `english_asr` operation `ef3ceddc6345`
> 本文记录一次故障的证据和通用改进建议，不是当前实现依据；当前架构事实仍以
> `docs/architecture/SYSTEM_ARCHITECTURE.md`、领域 README、版本化契约和代码为准。

## 结论

这次不是 Qwen3-ASR、说话人区分、全文校对或“确定定点收尾范围”自身报错。

直接原因是：正式 ASR 长任务运行期间，开发服务器检测到
`backend/app/domains/video_localization/service.py` 变化并执行了自动热重载。旧服务进程
被正常关闭，进程内的 ASR worker 线程随之消失。新服务进程在启动恢复时发现该 operation
仍是 active，但它属于不可恢复的正式 `video-localization-workflow-v1`，因此按安全策略
标记为 `VIDEO_LOCALIZATION_OPERATION_INTERRUPTED`。

真正需要优化的系统性原因是：页面虽然把完整 ASR 展示成 18 个原子步骤，但正式完整流程
仍以一个进程内长调用执行。它还没有接入已有的 typed detail core、durable step attempt、
managed artifact 和逐步骤恢复能力。换句话说，当前是“展示上分步，执行恢复上仍是一整块”。

## 本轮落实结果

本轮按产品边界选择了两层不同处理：

- 正式模式保持一气执行，不读取开发快照；稳定启动默认不使用热重载，显式
  `--dev-reload` 时 API 与长任务 worker 分进程，避免改 Web/API 代码直接杀掉 ASR。
- 开发调试模式补齐全部 18 个原子任务的外部类型化输入/结果快照，并提供
  `development_target` 单节点重放。它只执行指定节点、不写正式字幕，也不会自动把
  一个失败 operation 改回运行中。
- 付费的 review-decisions 节点在模型完成后、确定性应用前另存 prepared 快照。
  使用本项目后续成功 operation `3584a50dccff` 的真实
  `review_decisions_r2_prepared` 重放时，没有再次调用模型；181 个输出片段、12 个修改、
  警告和质量结论都与正式流程保存结果一致。
- ASR 正式重跑 `3584a50dccff` 已在稳定服务拓扑下成功完成，并越过此前的
  `review_decisions_r2` 不变量失败点。该次结果生成 2,362 条字幕，无空文本、非法时间
  或时间重叠；仍保留逐词插值和说话人覆盖不足等非阻断质量提醒。

这次实现的是用户明确要求的“开发时坏在哪就只测哪一段”。它不等同于把正式流程改成
跨进程自动续跑：后者仍需要下文所述 durable step attempt、managed artifact 和正式
workflow 版本迁移，不能拿临时快照冒充。

## 后续重跑发现

P0 运行保护落地后，正式重跑 operation `f754cbfccd7d` 已越过原任务的
`whole_recheck_r1` 中断位置，证明旧失败不是该步骤算法必现错误。新任务随后在
`review_decisions_r2` 以
`review decisions violated immutable transcript invariants` 主动失败。

这是另一类独立问题：第二轮复查把所有历史修改记录都解释成仍应逐字存在的锁，而同一轮中
后续证据已经合法替代的中间文本也保留在审计历史里。于是“审计历史”和“本轮入口的有效锁”
被混为一谈，产生误报。

通用修正后的不变量是：

- 历史修改仍完整保留作审计；
- 只有本轮入口实际存在的已确认片段参与本轮保护；
- 更新后这些有效片段的出现次数不得减少；
- 片段 ID 和时间范围仍必须完全不变；
- 确定性的标点/空格规范化仍不能改变词汇内容。

这不是为当前视频增加名称或句子特例，而是纠正跨轮审计数据与执行约束的边界。正式完整流程
仍没有 durable step artifact，因此本次只能用 operation 失败快照和最小固定用例定点验证；
真正的付费步骤级重放仍属于 P1。

## 证据链

### 1. 页面与正式 operation 状态

- operation：`ef3ceddc6345`
- kind：`english_asr`
- workflow：`video-localization-workflow-v1`
- 创建：2026-08-04 16:48:27
- 结束：2026-08-04 17:17:46
- 错误码：`VIDEO_LOCALIZATION_OPERATION_INTERRUPTED`
- 用户可见错误：服务在任务运行期间停止，本次任务已中断。
- 中断时页面正在展示的步骤：`whole_recheck_r1`

“失败在：确定定点收尾范围”和“0 秒”不是该步骤抛出异常的证据。恢复逻辑只是把进程消失时
处于 running 的步骤投影成失败；该步骤没有保存完整 duration，所以 UI 显示为 0 秒。

### 2. attempt 与服务重启时间完全对上

第一次 attempt：

- 16:48:27 开始；
- 最后 heartbeat 为 17:16:45；
- 旧进程消失后没有机会写终态。

恢复 attempt：

- 17:17:46.214 创建；
- 17:17:50.964 以 `VIDEO_LOCALIZATION_OPERATION_INTERRUPTED` 结束。

operation 本身在 17:17:46.350 被写成 failed。后端日志在同一位置记录：

```text
StatReload detected changes in 'app/domains/video_localization/service.py'. Reloading...
Shutting down
Application shutdown complete.
Finished server process [...]
Started server process [...]
```

因此这不是基于相似现象的猜测，而是 operation、attempt、heartbeat 与服务生命周期四条
证据在同一秒闭合。

### 3. ASR 和前置校对步骤其实已经完成

中断前已完成或降级完成：

- 原始听写：9 分 20 秒，181 个讲话片段，约 80,878 个字符；
- 说话人区分：8 分 27 秒，3 个说话人分组；
- 听写与说话人汇合；
- 全文理解；
- 画面取证：16 张截图、11 项观察；
- 资料核对：9 个问题、43 条可用资料；
- 名称统一：确认 11 个名称、修改 7 处；
- 第一轮全文分段检查：发现 194 个候选问题；
- 应用明确修改：改正 47 处，4 处保留原文。

说话人区分、画面、资料、名称和全文检查带 warning，但都是设计中的非阻断降级，不是本次
终止原因。

### 4. 这些完成记录不足以恢复正式结果

本次完整 ASR operation：

- `video_localization_operation_step_attempts` 中没有任何 step 行；
- 没有 typed detail core；
- 没有 managed operation artifact；
- Project 中只有供页面展示的步骤摘要、有限调试信息、搜索缓存和画面文件；
- Draft 的正式 `transcription` 仍为 null；
- Draft 的正式 source cue 数量仍为 0。

所以现有记录能证明“做到了哪里”，却不能作为可信、可校验、可续跑的上游输出。当前点击
“重试”会创建一个新 operation，重新进入完整流程。搜索缓存或已有画面文件可能减少少量
工作，但系统没有权威的跨 attempt 步骤产物复用保证，不能据此承诺 ASR 或模型调用不会重做。

## 根因分层

### 直接触发

运行服务使用 `uvicorn --reload`。ASR 期间修改后端 Python 文件触发热重载，承载 worker
线程的服务子进程被关闭。

### 执行拓扑问题

HTTP API、热重载服务和长任务 worker 位于同一个服务进程生命周期中。Web 代码变化会中断
与这次改动无关的 20～60 分钟后台任务。

### 工作流持久化问题

正式完整 ASR 仍从
`service.transcribe_english_source_audio()` 一次性进入
`AsrPipeline.run_full()`，最后才把完整 transcription/cues 原子写入 Draft。中间的
`on_report` 只更新展示摘要，不是步骤执行权威。

### 恢复问题

当前代码已经为 ASR 开发单步提供了 managed execution、step ledger、artifact、Provider
未知结果保护和 same-snapshot reader，但正式完整流程没有用这些能力来编排。启动恢复只能
识别“旧完整流程不可安全重放”，然后 fail closed。

### 可观测性问题

- operation 级中断被投影成“当前步骤失败”，容易误诊为步骤算法故障；
- 原 attempt 会永久保留为 running，恢复 attempt 才是 failed，读者需要自行理解租约历史；
- 后端普通日志没有统一时间戳和结构化 operation/attempt/step 上下文；
- 完整流程没有 durable step 行，页面步骤与恢复步骤不是同一份真相。

## 推荐的目标架构

不需要推翻模块化单体，也不应另写一套 ASR 算法。应把已经存在的 managed ASR 原子能力组装成
一个新的正式 workflow 版本。

```text
Web/API
  │
  ▼
正式 ASR parent operation（只负责编排）
  │
  ├─ initial_analysis ───────────────┐
  ├─ understand_document             │
  ├─ visual_evidence                 │
  ├─ research                        │  每一步：
  ├─ normalize_entities              ├─ durable step attempt
  ├─ section_review / decisions      ├─ typed managed artifact
  ├─ whole_recheck / quality_gate    ├─ input/output fingerprint
  ├─ alignment / boundaries          └─ Provider 重放语义
  └─ commit_transcription
          │
          ▼
   一次 fenced + CAS 的正式 Draft 写入
```

### 1. 新建正式 workflow 版本，不修改旧历史语义

例如新增 `asr-formal-workflow-v2`。旧 `video-localization-workflow-v1` 保持只读 legacy
adapter；不要把旧任务伪装成新版本，也不要尝试从不完整的旧摘要反推可信 artifact。

### 2. 提交时冻结完整输入身份

detail core 只保存不可从其他权威重建的、无路径的版本化身份：

- 音频/视频内容指纹与来源轨；
- ASR、说话人、LLM、视觉和搜索 Provider 的非密钥配置指纹；
- glossary、scene context、语言与分段策略指纹；
- workflow definition 和每个节点的 behavior fingerprint。

大型听写正文不复制进 core，写入受管 artifact。

### 3. parent 只编排，原子算法继续复用现有门面

正式流程调用现有 managed execution：

- initial analysis；
- document understanding；
- visual evidence；
- research；
- entity normalization；
- section review；
- review decisions；
- whole recheck；
- transcript quality gate。

尚未 managed 化的 alignment、audio boundaries、boundary review 和 subtitle track 按同样模式
补齐。正式流程和开发单步使用同一原子实现，区别只在编排和是否执行最终 commit。

### 4. 每一步先持久化，再允许下游运行

每个步骤 success 必须同时具备：

- live execution fence；
- 匹配的 input fingerprint；
- committed artifact；
- 校验通过的 typed output；
- durable step success。

页面详情直接读这套权威，不再把 Project 中的 `task_step_results` 当恢复事实。

### 5. 按 cost class 决定恢复行为

- 本地纯函数：同输入可重算；
- 本地模型：优先复用已提交 artifact，缺失时同输入重算；
- 外部免费调用：按既有有界策略重试；
- 外部付费调用：
  - prepared 未提交，可安全提交；
  - committed artifact 可直接复用；
  - submitted/result_unknown 不自动重放，等待 Provider 查询或人工裁决。

这与现有 operation ledger RFC 和 managed Provider gateway 一致，不需要发明第二套恢复系统。

### 6. 正式 Draft 只在最后一步写一次

中间步骤不得逐步覆盖用户正在看的正式字幕，否则失败会留下“半套正式结果”。最后的
`commit_transcription` 校验所有上游 lineage、源媒体指纹和当前 Draft revision，再通过
fence + CAS 一次提交 transcription、speakers、cues 和来源元数据。

### 7. 把 API 与长任务 worker 生命周期分开

仍然可以保持一个仓库、一个数据库和同一组领域服务，不需要拆微服务。建议提供两个运行入口：

- reloadable Web/API 进程；
- 不启用热重载的 durable operation worker 进程。

二者复用同一 application/domain 代码，通过现有 SQLite claim、lease、heartbeat 和 fencing
竞争执行权。开发时改页面或 API 不再杀掉正在推理的 worker；部署或 worker 真崩溃时，新的
worker 仍按 step ledger 恢复。

### 8. 单独表达“步骤错误”和“进程中断”

operation detail 建议增加或派生：

- `termination_scope = operation | step | provider`；
- `active_step_at_termination`；
- `restart_reason = hot_reload | graceful_shutdown | crash | lease_lost | unknown`；
- 原 attempt 的 `superseded/expired` 终态。

UI 应显示“任务因服务热重载中断；中断时正在执行：确定定点收尾范围”，而不是“该步骤
0 秒失败”。

## 分阶段落地

### P0：立即降低再次发生概率

- 运行正式长任务时不要使用 `uvicorn --reload`；
- 在开发启动方式中把 worker 从 reloadable API 进程分离；
- 若当前启动模式会热重载，在提交长任务前给出明确提示；
- 不自动重跑本次已终止的任务。

这只是运行保护，不是最终修复。

### P1：先让正式全文校对链可续跑

- 新增正式 v2 parent workflow 和 typed detail core；
- 编排已经 managed 的 ASR 节点；
- 重启故障注入覆盖每个节点前、Provider 提交后、artifact 提交后；
- 浏览器验证“运行中 → 服务重启 → 同一 operation 新 attempt → 从最近成功步骤继续”。

这是本次问题的核心架构改动。

### P2：补齐校时与字幕生成节点

- managed alignment；
- managed audio boundaries；
- managed boundary review；
- managed subtitle track；
- 最终 fenced/CAS commit。

### P3：关闭 legacy 完整流程

- 新提交只允许 v2；
- v1 历史只读；
- 对账稳定后移除正式流程的 Project step-detail fallback；
- 增加 artifact retention、孤儿 staging 清理和 operation/attempt 审计。

## 不建议的“补丁式”方案

- 只把 timeout 调大；
- 捕获所有异常后把任务标成成功；
- 每个步骤都直接写正式 Draft；
- 服务重启后无条件从头自动重跑；
- 看到 Provider 结果未知也重新提交；
- 只关闭 `--reload`，却继续让正式流程没有 step artifact；
- 把现有页面步骤摘要当作恢复输入；
- 为这一个项目、这一段视频或某个名称写特殊判断。

这些做法要么掩盖根因，要么会造成重复计费、旧结果覆盖新项目状态或不可追溯的半成品。

## 对当前失败任务的处理建议

现有 operation 已进入 legacy terminal failed，且没有可恢复的正式 step artifact。不要修改
数据库把它改回 running，也不要把页面摘要手工写成 transcription。

如果需要重新生成，只能在后端代码稳定、不会热重载的条件下由用户显式重试；应提前说明：
当前实现大概率会重做 ASR 和模型步骤。旧记录、已有搜索缓存和画面证据保留作审计，不应冒充
新任务的权威输入。
