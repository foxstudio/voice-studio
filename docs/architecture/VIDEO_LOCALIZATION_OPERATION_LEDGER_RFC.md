# 视频本土化 Operation Ledger RFC

状态：accepted / in progress

本文定义视频本土化后台任务从 Project JSON 生命周期记录迁移到 durable operation
ledger 的边界、顺序和失败语义。它是迁移约束，不表示所有阶段已经实现；当前事实仍以
`SYSTEM_ARCHITECTURE.md`、领域 README 和代码为准。

## 1. 目标与非目标

目标：

- 多进程或进程重启后，同一 operation 同时最多有一个有效执行者。
- 旧执行者失去 lease 后，不能续租、写进度、提交 artifact 或覆盖终态。
- 用户命令、任务状态、attempt、付费 provider 调用和 Project 兼容镜像各有明确提交点。
- 崩溃恢复依据持久状态和版本化指纹，不依据进程内集合或推测。
- 列表、详情、恢复和 WebUI 最终读取同一 operation 权威来源。

非目标：

- 开发 checkpoint 不升级为正式缓存或任务账本。
- legacy locator 表不升级为权威 ledger；reader 迁走后直接退役，不保留无消费者的双写。
- 第一批 claim primitive 不立即改变产品读路径或 Project JSON 权威状态。
- provider 不支持查询或幂等时，不以自动重放掩盖不确定结果。

## 2. 状态所有权

迁移完成后的所有权：

| 状态 | 唯一 owner |
| --- | --- |
| operation 命令、状态、取消意图、终态 | operation ledger |
| attempt、runner、lease、heartbeat、fencing token | operation attempt store |
| step 输入指纹、provider 请求、结果确定性 | operation step ledger |
| artifact 指纹、受管理引用、提交状态 | artifact store |
| Project 中的 operation 摘要 | ledger outbox 生成的兼容镜像 |
| 进程内排队、线程锁、当前 commit gate | process-local runtime |
| Project operation projection revision | 独立 payload-free revision state |

迁移期间，submit/cancel/retry 的接受结果已经由 payload-free operation command ledger
持久化，attempt store 是“谁有资格执行”的权威；列表、轮询和恢复从 ledger 读取
身份、状态、取消意图和生命周期时间。详情按 workflow 分流：semantic-v2 与
source-audio-v1 从 typed core（仅 semantic 需要）、step 和受管 artifact 组装，其他
workflow 暂从 Project compatibility mirror 补齐 progress、参数与结果 payload。
Draft 内嵌任务列表仍是兼容快照。真实 worker 只有在 claim 成功后
才执行，并让同一 worker 调用栈内的 Project 写入携带相同 fencing capability，同时把
状态元数据同步到 ledger。semantic-v2 和 `source_audio/extract_source_audio` 已正式
写入 step/artifact ledger 并使用 managed detail reader；其余 workflow 尚未迁移，
因此读模型仍须按下述顺序继续收敛。

当前 HTTP LLM runtime 已停止对 `5xx`、不完整响应和远端断开自动重放付费 POST；只有
明确拒绝执行的 `429` 继续有界退避。semantic-v2 已把响应中断映射为 durable
`result_unknown` step，并提供内部裁决/恢复原语；尚未迁移的模型工作流仍只有
`llm_result_unknown` 信号，没有统一的 durable step 与用户裁决入口。

shared TTS 的单句与长文本任务已在云端提交前持久化 uncertain；豆包 request ID 先写入
任务再随请求提交。重启、stale、超时和不明确失败不会自动重放，普通/长文本 retry
需要显式人工确认。cloud batch 也已把每个 segment 的 attempt number、输入 fingerprint、
Provider request/log ID 与 `prepared/success/failed/uncertain` 状态持久化；恢复只继续未发送
segment，不自动重放 attempted/uncertain segment。该保护仍属于 batch/task store 的保守
适配，不等于 video-localization operation step ledger，人工裁决入口仍待统一。

## 3. Durable claim 契约

### 3.1 时间

- lease 判断只使用 UTC epoch milliseconds，不使用本地时区字符串排序。
- `claimed_at`、`heartbeat_at` 等 ISO 字段仅用于诊断和人类阅读。
- 输入时间必须带时区；naive datetime 必须拒绝。
- lease duration 必须大于零，并始终满足：
  `lease duration > 3 × heartbeat interval + SQLite busy timeout`。
  当前产品配置为 60 秒 lease、10 秒 heartbeat 和 5 秒 SQLite busy timeout。

### 3.2 Claim

同一 transaction 使用 `BEGIN IMMEDIATE`：

1. 读取该 operation 的最新 attempt。
2. 若最新 attempt 为 `running` 且 lease 尚未过期，返回 `active_lease`，不写新行。
3. 否则分配单调 `attempt_number` 和单调 `fencing_token`。
4. 插入新的 `running` attempt，写入 runner、heartbeat 和 lease expiry。
5. 提交后才允许执行领域工作。

并发 claim 只能有一个 `acquired`。DB 不可用、写锁超时或 claim 结果不确定时，worker
不得继续执行。

### 3.3 Heartbeat 和完成

heartbeat 与完成必须同时匹配：

```text
attempt_id
operation_id
runner_id
fencing_token
status = running
lease_expires_at_ms > observed_at_ms
```

heartbeat 只能延长仍有效的 lease，不能复活已经过期的 attempt。过期 attempt 即使尚未
被新 runner 接管，也必须重新 claim。

### 3.4 Fencing

attempt number 是执行次数；fencing token 是写资格。两者都在
`(project_id, operation_id)` 范围内单调递增，但保留独立字段和校验语义，避免以后因
attempt 导入、归档或人工恢复破坏 fencing。

真实 worker 使用显式的 `OperationExecutionClaim`：

- `_process` 只有 claim 成功后才进入领域执行；DB 不确定或有效 foreign lease 都不执行。
- heartbeat 丢失会让 claim-loss 取消检查立即为真。
- 所有正式内容 `commit_guard` 在持有进程内取消锁期间再次校验 claim。
- Project、Draft、application update、operation status 和终态写入携带同一 token；
  同一调用栈内的嵌套 Project 保存通过 context scope 解析该 fence。
- submit/cancel/retry 命令使用 ledger revision 与 Project projection CAS，不继承当前
  worker 的 contextual fence；显式同时传入两类 capability 必须拒绝。否则取消命令会
  被它正在终止的 worker fence 反向否决。
- claim 丢失使用独立的 `ExecutionFenceLost` 控制流，不把任务错误写回 Project。
- ledger 中 operation 已取消或不再 active 时，以 `ExecutionOperationCancelled` 拒绝
  跨进程迟到提交，即使 lease 本身仍有效。

只在开始执行时 claim、但提交时不校验 token，不算完成 fencing。

当前已接入 worker 的提交边界：

- durable attempt 可派生 payload-free `ExecutionFence` capability。
- Project、Draft、application update 和 operation-status 写入口可接收或解析该 fence。
- operation store 使用 `BEGIN IMMEDIATE`，在同一 transaction 内校验
  attempt/project/operation/runner/token、running 状态、lease expiry 和当前 ledger
  operation active/cancel 状态，随后写 Project JSON 与 projection revision。
- 失效、过期、被接管、跨项目或已取消的 fence 在写入 Project、projection revision 和项目快照前
  停止。
- 启动恢复保留其他实例的有效 running lease，并为每个 operation 在到期后复查；DB
  claim 不确定时不改状态。恢复者也必须赢得新的 durable claim 和 fencing token，才能
  把遗留 running operation 标记为 interrupted failed；不得用“先查无 lease、再无 fence
  保存 Project”的两段式恢复。

这完成了迁移顺序中的 worker execution guard；command ledger 又解决了用户命令、
Project compatibility mirror、ledger state metadata 与 projection revision 的同库原子提交。
产品 operation 列表、详情、轮询、恢复和 fenced active/cancel guard 已切换到 ledger；
Draft 内嵌任务快照、step/artifact ledger、provider 请求幂等和 `result_unknown` 尚未
实现，所以 Phase 2 仍未完成。

## 4. 权威迁移顺序

1. **Claim primitive**：为 attempt store 增加 lease/fencing 字段、兼容迁移和并发测试；
   已完成。
2. **Worker execution guard**：queued worker 必须 claim 成功才执行；状态写、正式内容提交
   和终态全部 fence。保留 Project JSON 状态权威。已完成。
3. **Operation command ledger**：submit/cancel/retry 提交 ledger 与 outbox，Project
   operation 变为兼容镜像。命令侧已完成；当前同库 mirror 在同一 transaction 内同步
   应用，文件 snapshot outbox 和独立重放 consumer 尚未实现。
4. **Reader migration**：详情、列表、轮询、恢复的状态字段已切到 ledger，并继续用
   有界双向审计对账旧 JSON；完整 payload 和 Draft 内嵌任务列表仍保留兼容 fallback。
5. **Step/provider ledger**：付费请求先 prepared/submitted，保存 idempotency key 和
   request ID；不确定结果进入 `result_unknown`。
6. **Mirror cleanup**：对账稳定后关闭 JSON fallback，禁止客户端草稿替换 backend-owned
   operation 历史。

任何阶段回滚都只能回滚尚未切换的 reader/runner；已经成为命令权威的 ledger 不得静默
回退到旧 JSON。

Ledger 的持久身份是 `(project_id, operation_id)`。这是对历史项目克隆语义的兼容：
旧项目可能复制 operation ID，甚至保留源项目 ID；兼容迁移保留 operation ID，并把镜像
记录中的 project ID 规范为容器 Project。新 command 仍使用随机 operation ID。队列、
取消 gate、worker 路由和恢复 timer 已使用复合身份；legacy locator 已在 reader 迁走后
退役，不再创建或双写；attempt 表的唯一键、计数、claim/heartbeat/finish/list 和
latest inventory 也使用同一复合身份；`attempt_id` 只作为全局执行事件 ID。

## 5. 崩溃和恢复语义

| 崩溃点 | 恢复状态 | 自动动作 |
| --- | --- | --- |
| claim 提交前 | queued | 可重新 claim |
| claim 后、领域工作前 | running + lease | lease 到期后新 attempt |
| 本地纯函数执行中 | running + lease | 过期后按同一输入指纹重算 |
| provider 请求提交前 | prepared | 可安全提交 |
| provider 已提交、request ID 未持久化 | result_unknown | 禁止自动重放 |
| provider 返回、artifact 前 | submitted/result_unknown | provider 查询或人工确认 |
| artifact 原子提交后、step 前 | running + verified artifact | 校验指纹后补 step commit |
| operation 终态后、Project 镜像前 | ledger terminal + outbox pending | 只重试镜像 |

启动恢复不得再把所有 `running` 一律改成 failed。只有无有效 lease、且 step/provider
确定性允许恢复的任务才可创建新 attempt；其余进入 interrupted 或 result_unknown。
恢复枚举只包含 active 状态；terminal cancelled 即使保留 `cancel_requested=true`，也不得
在后续每次启动时重复写 Project 镜像或推进 projection revision。

## 5A. Operation summary read model 迁移规格

本节是 `reader migration` 的接受规格，状态为 **implemented / authoritative for
summary feeds**。WP-03A–F 已完成：v2 有界 feed、v1 feed 和 summaries reader 只从
ledger + summary repository 组装，不再查询 Project compatibility mirror；WebUI 已
切换到 v2，并按显式用户意图加载 terminal history。完整 operation detail、step 和
artifact payload 仍保留 Project compatibility mirror，等待后续 WP-04。

### 5A.1 已验证的问题与边界

固定 50 条终态 operation 的受控 revision 推进基准已经证明：

- 同 revision 的进程内 single-flight/cache 可以避免重复构建，但每次 revision
  改变后仍要读取并解析约 10.54 MB 的 Project JSON。
- 12 路 cache-miss changed feed 的重复测量 p50 为约 65–66 ms、p95 为约
  155–156 ms；对应纯读窗口没有 SQLite 写入。
- 因此根因是 read amplification，不是已经证明的结构性 write amplification。
  Project mirror 的正式写路径、不同字段位置和 payload 长度变化仍要单独测量。

目标不是再增加一份 operation authority，而是增加一个可删除、可重建、严格有界的
read model。迁移期间必须保持以下所有权：

| 数据 | owner | summary reader 的使用方式 |
| --- | --- | --- |
| operation 身份、状态、取消、生命周期时间 | operation ledger | 每次读取直接 join，不复制为权威 |
| 列表所需的紧凑参数、进度和结果摘要 | summary projection | 版本化派生数据，可从兼容镜像回建 |
| 完整参数、完整步骤结果和错误详情 | Project mirror，后续迁 artifact/step store | 仅按 ID 的 detail reader 读取 |
| 当前媒体健康 | workspace/media health projection | 不伪装成历史 operation 的持久结果 |
| 开发 artifact 是否可打开 | artifact registry + 受限 resolver | 对当前有界页做动态补充，detail 再校验 |
| feed revision | projection state | 失效令牌，不是 operation authority |

### 5A.2 版本化领域契约

新增内部 `operation-summary-core-v1`。它是领域 read model，不是 API JSON 的数据库
副本。纯 projector 只接收 typed operation 和完成投影所需的 typed context，不读取
数据库、不访问网络、不决定任务步骤顺序。

core 必须保存：

- `operation_id`、`project_id` 和 `summary_schema_version`；
- `label`、`progress`、`started_at`、`error_code`、`error_message`；
- 当前公共 summary 契约允许的少量参数；
- 当前公共 summary 契约允许的紧凑 `result_summary`；
- core 本身不递归保存自己的 fingerprint；repository 在独立列保存 canonical core
  JSON 的 `content_fingerprint`。

core 不得保存：

- ledger 已拥有的 `kind`、`status`、`cancel_requested`、`created_at`、
  `completed_at` 作为第二权威；组装时始终使用 ledger 行；
- 完整 parameters、完整 task result、字幕正文、媒体二进制或 Provider 请求；
- 绝对路径、授权信息、runner ID、fencing token 或其他内部 capability；
- 由 API public contract 临时删除字段后的整份传输 payload。

终态 summary 继续只保留列表元数据；活动任务可保留现有有界
`task_step_results/task_final_result` 投影。字段选择由 summary schema 版本控制，不由
当前 workflow 的步骤数量、名称或顺序控制。仍在调整的 ASR/本土化步骤可以改变
`StepSpec`，但不能迫使存储表增删列。

建议代码边界：

```text
domain operation summary projector (pure)
    typed operation + typed projection context
        -> OperationSummaryCoreV1

application operation projection writer
    draft mutation result + ledger command/fence
        -> repository transaction input

summary repository
    persist/query rows and projection state only

application feed reader
    ledger rows + summary cores + bounded dynamic enrichment
        -> public feed contract
```

repository 不得 import `operation_queue`，也不得重新实现字段筛选、任务文案或
artifact 规则。`operation_queue.read_operation_feed()` 中现有的 summary 组装逻辑应在
调用方迁移并完成等价测试后删除，不能长期保留两套 projector。
core 的 frozen typed contract 与 canonical codec 可放在中立 schema；domain projector
和 repository 都单向依赖该契约，repository 不得为复用类型而反向 import domain。

当前已完成 WP-03A/B/C/D/E：纯 projector、path-free core-v1 编解码、canonical
fingerprint、additive summary/state schema、同事务 shadow writer、显式 promotion
、v1 repository reader、v2 有界 page reader 和 WebUI keyset history 已落地。
Draft Store 在进入 repository transaction 前从 typed operation 构建 cores；
ledger、Project compatibility mirror、summary rows、projection state 和 outbox
任一校验失败时整笔回滚。显式 project-keyset backfill、query-only 同 SQLite
snapshot 双向 reconciliation，以及 transaction 内重核 legacy/core/ledger 后的
bounded promotion 已实现。应用先读取不含 `projects.data` 的 descriptor：
`verified/authoritative` 在一个 read transaction 内读 ledger + core；
`missing/shadow` 在兼容窗口读 legacy；`repair_required` 或损坏 verified core
返回明确 repair error且不静默 fallback。v2 head 返回全部 active（硬上限 32）和
默认 50、最大 100 条 terminal history，cursor 页不重复 active；终态历史变化通过
`history_revision` 明确使 cursor 失效，活动进度不影响已有 cursor。v1 仍为兼容入口，
authority 仍属于 ledger + Project detail mirror；因此 WP-03F 仍未完成。

### 5A.3 持久结构

第一阶段采用一行一个 operation 的紧凑 JSON core；用于 identity、排序、join、版本和
审计的字段单独成列：

```sql
CREATE TABLE video_localization_operation_summaries (
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    summary_schema_version TEXT NOT NULL,
    ledger_state_revision INTEGER NOT NULL,
    core_revision INTEGER NOT NULL,
    content_fingerprint TEXT NOT NULL,
    core_json TEXT NOT NULL,
    projected_at TEXT NOT NULL,
    PRIMARY KEY (project_id, operation_id)
);
```

排序不复制到该表。reader 从 `video_localization_operations` 读取
`created_at/status`，并以 `(created_at DESC, operation_id DESC)` 做稳定 keyset
pagination，再按复合 ID join core。复合主键已经覆盖 join，不再创建同列同顺序的冗余
索引。`ledger_state_revision` 记录组装时看到的 ledger 版本；
`core_revision` 在 progress/result summary 等 core 内容改变时独立递增，因为这些变化
不一定推进 ledger state revision。这样状态和排序时间不会出现双权威，也不会误把
ledger revision 当作 summary row 版本。

`video_localization_operation_projection_state` 在兼容迁移中增加：

```text
summary_schema_version
summary_status = missing | shadow | verified | authoritative | repair_required
summary_row_count
summary_fingerprint
history_revision
history_fingerprint
last_verified_at
```

`projection_revision` 继续作为 project-scoped feed 失效令牌。迁移完成后只有以下变化
推进它：

- ledger-owned 列表字段改变；
- 某个 summary core 新增、改变或删除；
- 会改变 feed 动态补充结果的受管理 artifact 注册/注销。

`history_revision` 只在 terminal history 的成员、顺序或 terminal core 改变时递增；
活动任务的 progress/step 更新只推进 feed revision。该区分用于保证用户在任务运行期间
仍能连续翻阅旧历史。

WP-03B 只保存并验证 summary/history fingerprint 和 shadow `history_revision`。
现役 `projection_revision` 仍是 Project-scoped 保守 CAS/失效令牌，普通 Project
保存仍可能推进它。必须先完成独立 Project/Draft CAS，才能按本节最终规则收窄该令牌；
否则当前整份 Project mirror 写入可能在并发 workspace 更新时丢失数据。不得用
shadow 表已经存在来宣称 operation-scoped revision 或写放大已经解决。

项目改名、普通 workspace 写入或与 operation feed 无关的 Draft 字段不得推进该
revision。command 的 Project operation CAS 继续使用这一 operation-scoped revision；
通用 Project/Draft 并发仍由独立 repository CAS 解决，不能借此假装
`VL-AUD-008` 已完成。

### 5A.4 动态字段处理

当前 summary builder 还读取两类非 operation-core 状态，迁移时必须显式拆开：

1. `source_audio/stems` 的 v1 历史白名单指标已经作为 path-free durable core 写入。
   legacy 适配器只补齐旧数据缺失的这些字段并保留已有历史值；当前媒体是否仍可播放
   属于 `ProjectMediaHealth`，由 workspace 展示，不覆盖历史任务结果。若产品确实要
   在历史行显示当前媒体健康，必须通过独立 `current_media_health` 字段加入 API，
   而不是改写历史 summary。
2. 开发断点的 `artifact_available` 不能依赖 Project 中的绝对
   `artifact_path`。兼容回填只在旧 locator 通过既有边界校验后，登记
   `(project_id, operation_id, artifact_kind, managed_key/schema)`；managed key 由
   配置根和 operation ID 解析，不保存绝对路径。feed 只对当前有界页检查，detail
   reader 每次仍执行路径边界、文件存在、schema、project/operation identity 校验。

当前 v1/v2 repository feed 不持久化 artifact 布尔值，而是按 operation ID 在受管理
snapshot 根下动态验证确定性文件；v2 只对当前有界页解析该字段，detail reader 继续
验证兼容 locator、路径边界、文件、schema 和 project/operation identity。该受限
resolver 允许 verified summary 读取切换，但 artifact registry 仍是后续结构化收口；
不得把动态布尔值当作 artifact authority。

### 5A.5 有界 feed v2

`operation-feed-v1` 的外部形状要求一次返回全部历史，不能直接静默截断。迁移顺序为：

1. v1 先改由新 repository 组装，输出保持完全兼容；它只作为迁移适配器。
2. 新增 `operation-feed-v2`，WebUI 切换后才允许移除 v1 的主轮询职责。
3. `operations/summaries` 与 v1 在外部调用迁移完成后弃用；兼容窗口内两者只能调用
   同一个 summary repository，不能保留旧 Project reader。

v2 把实时集合和历史页分开：

```text
schema_version = operation-feed-v2
revision
history_revision
changed
active_operations[]     # 全部 active，硬上限 32
history[]               # 终态，默认 50、最大 100
history_total
next_cursor
```

- 没有 cursor 的 head 请求可以携带 `after_revision`；相等时返回
  `changed=false` 和空集合。
- history 使用 `(created_at, operation_id)` 的 opaque versioned cursor，不使用
  offset。cursor 带创建它的 `history_revision`；terminal history 已变化时返回明确的
  `stale_cursor`，客户端重新读取 head，不能混合两个历史快照。普通 active progress
  只改变 feed revision，不得使历史 cursor 失效。
- active 集合不分页，以免长历史把运行中任务挤出轮询；命令表的 active-kind 约束和
  32 条硬上限共同保证有界。legacy 数据超过上限必须进入审计/repair 状态，不能静默
  截断。
- page reader 在同一 SQLite read transaction 中读取 revision、ledger 行和 cores。
  `after_revision` 短路仍不得解析 Project JSON。
- 进程内 LRU/single-flight 可以继续存在，但缓存键必须包含数据库 runtime identity、
  project、revision、schema version、page size 和 cursor。缓存失效不是一致性来源。

### 5A.6 事务写入与不变量

Draft/application 层已经持有 typed `VideoLocalizationDraft`。它在进入 repository
transaction 前构建 summary cores，并把它们作为明确参数传给存储边界；通用
`project_store` 不从任意 Project JSON 猜测展示规则。

同一个 `BEGIN IMMEDIATE` transaction 按以下顺序执行：

1. 校验 command revision 或 worker execution fence。
2. 提交 ledger/outbox 状态。
3. 写 Project compatibility mirror。
4. 同步 ledger identity/state metadata。
5. upsert 本次改变的 summary cores，并删除仅允许删除的 legacy cores。
6. 比较 ledger feed 字段、core fingerprint 和 artifact registration；确有变化才推进
   projection revision。
7. 写 projection state 的 row count/fingerprint/status。
8. 标记同事务 mirror outbox applied 并提交。

提交后必须满足：

- ledger 中每个可见 operation 恰有一个同复合 ID、可由当前 schema 解码的 core；
- `summary_row_count` 等于实际行数；
- project-level fingerprint 由稳定排序的
  `(operation_id, ledger state_revision, core revision, core fingerprint)` 生成；
- revision 对所有可见变化单调增加，对无关 Project 保存不增加；
- history revision 对 terminal history 单调增加，不受 active progress 干扰；
- transaction 回滚时 Project、ledger、summary、revision 和 outbox 全部不变。

现有 Project mirror 在迁移期间仍整份写入，所以这一阶段只解决 feed read
amplification 和状态边界，不宣称已经解决 Draft 写放大。完整 payload、step 和
artifact 迁出后，才允许停止 operation runtime progress 对 Project blob 的重写。

### 5A.7 兼容迁移、对账和切换

迁移必须可中断、可重入、分批执行，普通 GET 不做持久化修复：

1. **Additive schema**：建表/加 state 列；所有 reader 仍用 legacy。
2. **Bounded backfill**：显式启动迁移器按 project 主键游标分批读取 Project mirror，
   构建 cores；每个 project 独立 transaction，失败只标记该项目
   `repair_required`，不推进项目业务更新时间。
3. **Shadow write**：所有新 operation 变化同事务写 core，runtime reader 仍用
   legacy。
4. **Read-only reconciliation**：对同一 SQLite snapshot 的 legacy/new 两个
   canonical feed 计算 fingerprint；报告 mismatch category、数量和复合 ID，不输出
   payload、路径或用户内容。被 limit 截断时 `--check` 必须失败。
5. **Verified**：只有 row count、identity、ledger state、core fingerprint、排序和
   public locator 检查全部一致的项目，才可由显式 promotion command 在重新核对
   fingerprint 的 transaction 内进入 `verified`；只读 reconciliation 本身不改状态。
6. **Reader switch**：先让 v1 compatibility reader 使用新 repository，再发布 v2
   和前端分页。`missing` 项目可在兼容窗口回退 legacy；`verified/authoritative`
   项目一旦 core 损坏必须返回明确 repair error，不能静默回退形成第二权威。
7. **Authority close（已完成）**：一个全库 `BEGIN IMMEDIATE` transaction 重新核对
   所有项目；limit 截断、未 verified 或损坏时零修改退出。通过后同时把 verified
   投影改为 `authoritative` 并写入 schema-versioned 全局 marker。新空库直接写
   marker，后续 writer 直接保持 authoritative；runtime legacy summary reader 已删除。
8. **Later cleanup**：detail/artifact/step reader 全部迁走后，另一个版本才移除 Draft
   内嵌 operation payload。不得与首次 summary reader 切换同批删除。

同一路径数据库替换、克隆项目复用 legacy operation ID、损坏 JSON、重复 active
legacy row、旧 schema、迁移中途断电和进程并发启动都必须有固定测试。

### 5A.8 回滚规则

- additive schema、backfill 和 shadow write 阶段可关闭新 reader；保留表和迁移状态，
  不在回滚版本中 DROP 数据。
- authority close 前，只要 Project mirror 仍在同事务双写且 reconciliation 仍一致，
  发布版本可以整体回退 reader；不能按单个请求悄悄 fallback。authority close 后当前
  版本只允许前滚修复，不提供删除 marker 或按项目降级的运行时开关。
- 一旦停止 Project operation payload 双写，就不得回滚到 legacy reader。此后只能从
  ledger/artifact 权威生成新的兼容导出，或前滚修复。
- schema 版本不兼容、fingerprint 不一致或 row 缺失时，保护用户数据优先：停止该项目
  summary feed、保留 detail 数据并提供只读审计/显式 repair，不自动覆盖。

### 5A.9 验证矩阵与退出门

实现批次至少需要以下证据：

| 层级 | 固定验证 |
| --- | --- |
| pure unit | 字段白名单、终态压缩、活动步骤有界、fingerprint 稳定、无 locator |
| repository | upsert/delete、revision 只在可见变化时推进、transaction rollback、损坏 row、DB replacement |
| migration | legacy/clone/空项目/损坏项目、分批续跑、幂等、不中断其他项目 |
| reconciliation | 双向缺行、状态差异、core 差异、排序差异、limit 截断、只读证明 |
| concurrency | writer 与 head/page reader 快照一致；12 路 miss 仅一个 projector；多进程不依赖内存缓存 |
| API | v1 等价、v2 cursor/limit/stale cursor、OpenAPI、public locator 清理 |
| frontend | head 轮询、加载更早、切项目 epoch、迟到页拒绝、submit/cancel/retry、刷新持久可见 |
| failure | DB busy、backfill 中断、core corruption、服务重启、artifact 丢失 |
| performance | 50、1,000、10,000 条历史；changed/unchanged/page/health 的 p50/p95/max、CPU/RSS/DB 变化 |

在当前固定机器和同一 50 条 fixture 上，第一阶段退出门是：

- changed cache-miss 不读取 `projects.data`，SQL query plan 使用 ledger/project
  indexes，响应工作量随 page size 而不是 Project blob 大小增长；
- 12 路 changed aggregate p95 不高于 `max(50 ms, 2 × paired health p95 + 15 ms)`，
  且不能通过删掉 cold first revision 美化结果；
- 10,000 条终态历史的 head response 仍最多 100 条 history，内存峰值和响应体积有
  明确预算，page 2 不扫描前 100 页；
- changed/unchanged/page 纯读窗口的 SQLite `data_version` 不变化；
- v1 canonical payload 与迁移前固定 fixture 一致，v2 与 v1 的 active + 全部分页
  history 合并结果一致；
- quick、full、生产构建、bundle budget 和最新真实服务 Web E2E 全部通过。

只有上述证据齐全，才能把 summary reader 标为 authoritative。SSE/WebSocket 只能在此
之后作为“revision 已变化”的提示通道；它不携带另一份 operation 状态，也不能用来掩盖
未解决的 Project blob 读取。

## 5B. Step、Provider 与 Artifact 权威迁移规格

完整 operation detail 仍把 `task_step_results`、调试明细和 artifact locator 保存在
Project compatibility mirror。该结构适合迁移期展示，不适合作为步骤执行资格、
Provider 幂等或崩溃恢复依据。步骤名称和流程拓扑仍可变化，但持久层只建模稳定语义：
“哪个 operation attempt 以什么输入执行了哪个 step、外部请求是否已经提交、结果是否
确定、产物是否已经受管理提交”。

### 5B.1 分批顺序

WP-04 拆成以下单向迁移，不允许一步同时改 workflow、reader 和清理旧数据：

1. **WP-04A durable step/provider primitive（done）**：增加通用 typed step-attempt
   状态机、additive SQLite schema、fencing、输入指纹、Provider 幂等键和
   `result_unknown`；先不接入具体 workflow，也不改变产品 reader。
2. **WP-04B managed artifact store（done）**：增加受管理 storage backend、相对 key、
   schema/content fingerprint、原子暂存与提交；数据库不保存任意绝对路径或二进制。
3. **WP-04C shadow integration（done）**：选择一个无付费、输出较小且已稳定的 step，把
   runner report 写到 step/artifact ledger，同时继续写 Project mirror；只读对账，
   不改变 WebUI。
4. **WP-04D provider integration（done）**：D1 先落地
   `result_unknown` 显式裁决账本；D2A 落地不依赖具体 workflow 的 durable Provider
   调用门面和跨 worker attempt 重放屏障；D2B 以恢复 artifact + 指纹 + 裁决组合命令
   关闭 success 恢复缺口；D3A/D3B 已按 cost class 迁移
   `semantic_tts_grouping` 的执行层、产品入口和启动恢复。不支持查询的未知结果必须人工
   裁决，禁止自动重放。
5. **WP-04E detail reader switch**：用 ledger + artifact reader 组装完整 detail，
   与 Project mirror 逐字段对账；切换后损坏新权威必须显式失败，不能静默 fallback。
6. **WP-04F mirror cleanup/retention**：关闭 step/detail JSON 双写，定义归档、删除、
   tombstone 和 artifact 回收；最后才移除 Draft 内嵌完整 operation payload。

仍在调整的 ASR/本土化步骤只影响 `step_id`、workflow plan 和 step output schema，
不得要求 WP-04A/B 的表随步骤增删列。开发 checkpoint 继续位于正式 product data
之外，不导入正式 ledger。

### 5B.2 Step attempt 身份与状态机

一个 step 可能在同一 operation 的不同 worker attempt 中重复，也可能在同一 worker
attempt 中因明确安全的本地重试产生多个 step attempt。因此持久身份不是
`operation_id + step_id`，而是独立 `step_attempt_id`，并保留以下复合维度：

```text
project_id
operation_id
operation_attempt_id
step_id
step_attempt_number
fencing_token
workflow_version
input_fingerprint
```

同一 `(project, operation, operation_attempt, step, input_fingerprint)` 的 prepare
必须幂等复用原 row；不同输入创建单调的新 `step_attempt_number`。克隆项目可能复用
legacy operation ID，所以所有查询和唯一约束必须包含 `project_id`。

第一版稳定状态：

```text
prepared
  ├─ local_free ───────────────→ success | failed | cancelled
  └─ external_* → submitted ───→ success | failed | cancelled
                                  └────────→ result_unknown
result_unknown --显式 Provider 查询或人工裁决--> success | failed
```

- `external_paid` prepare 必须同时保存 `provider_name` 和
  `provider_idempotency_key`；缺任一字段不得调用 Provider。
- `submitted` 必须在网络请求之前提交。Provider request ID 可在预先可知时一并保存，
  或在收到响应后以只写一次的方式补齐。
- 远端断开、响应不完整、超时或进程在请求后崩溃时进入 `result_unknown`，不能改回
  `prepared`，也不能由普通 retry 自动创建另一个 paid attempt。
- `result_unknown` 的裁决必须走独立 typed command，保存裁决来源和原因；不能伪造
  worker fence。WP-04D1 已实现该内部命令；API/WebUI、Provider 查询和恢复 artifact
  留到 D2/D3。
- success 只保存 output fingerprint 和 artifact identity，不在 step row 内嵌字幕、
  调试大对象、媒体路径或 Provider 原始响应。

WP-04A 采用 append-oriented step attempt row；除状态、只写一次的 Provider request
ID、终态字段和单调 `status_revision` 外不覆盖身份与输入。WP-04D1 在独立不可变
adjudication table 中补齐未知结果裁决，不把裁决来源或原因塞回 step row。

### 5B.3 WP-04A 持久结构

第一批使用职责明确的表名，避免把多次执行压成一个可变 `operation_steps` row：

```sql
CREATE TABLE video_localization_operation_step_attempts (
    step_attempt_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    operation_attempt_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    step_schema_version TEXT NOT NULL,
    step_attempt_number INTEGER NOT NULL,
    fencing_token INTEGER NOT NULL,
    workflow_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    cost_class TEXT NOT NULL,
    provider_name TEXT,
    provider_idempotency_key TEXT,
    provider_request_id TEXT,
    status TEXT NOT NULL,
    status_revision INTEGER NOT NULL DEFAULT 1,
    prepared_at TEXT NOT NULL,
    submitted_at TEXT,
    result_unknown_at TEXT,
    completed_at TEXT,
    output_fingerprint TEXT,
    error_code TEXT,
    UNIQUE (
        project_id,
        operation_id,
        step_id,
        step_attempt_number
    ),
    UNIQUE (
        project_id,
        operation_id,
        operation_attempt_id,
        step_id,
        input_fingerprint
    ),
    UNIQUE (provider_name, provider_idempotency_key)
);
```

`cost_class` 第一版固定为 `local_free | external_free | external_paid`；`status` 固定为
上述状态集合，并由 DB `CHECK` 与 typed repository 双重校验。Provider 两列要么同时
为空，要么同时非空；`external_paid` 必须非空，`local_free` 必须为空。所有时间使用
带时区 UTC 文本，lease 判定仍只使用 attempt 表的 epoch milliseconds。

repository 每次写入都在同一 `BEGIN IMMEDIATE` 中：

1. 验证 execution fence 的 project/operation/attempt/runner/token 与未过期 lease；
2. 验证 operation ledger 仍 active、未取消，workflow version 与 prepare 输入一致；
3. 验证允许的状态转换、不可变字段和 Provider key；
4. 插入或条件更新 step row；
5. 提交后才允许调用具体 step 或 Provider。

有效 fence 的校验只能有一个公共实现，Project commit 和 step repository 必须复用；
repository 不得导入 `operation_queue`。SQLite 当前未统一启用 foreign key，因此
WP-04A 不能把未生效的 FK 当作完整性证据，必须在 transaction 内显式验证 operation
和 operation attempt；项目删除时先清 step rows，再清 attempt/operation。

### 5B.4 WP-04A 退出门

- 新空库和已有库都能 additive 初始化；schema bootstrap 返回调用方时不持有事务。
- 同一输入并发 prepare 只产生一行；不同输入的 step attempt number 严格单调。
- 过期、被接管、跨项目、已完成、已取消或不存在的 fence 均不能写 step。
- `external_paid` 无幂等键不得 prepare；相同 Provider key 不能关联两个 step。
- 非本地 step 未进入 submitted 不能成功；终态不能回到 active；
  `result_unknown` 不能通过普通 finish/retry 改写。
- Provider request ID 只能从空补写一次；不同值必须冲突。
- project scoped clone、DB busy、transaction rollback、服务重启读取、并发提交和删除
  清理均有固定测试。
- 本批不改变 operation API、Project mirror、WebUI 或真实 Provider 调用；因此 Web
  只做现有 operation 回归，不声称步骤 reader 已迁移。

### 5B.5 WP-04B Managed Artifact Store

正式 artifact 与开发 checkpoint 使用不同根、不同 schema 和不同 reader。WP-04B
第一版只接收最多 16 MiB 的版本化 step 输出；源视频、音轨、大模型文件和其他大型媒体
继续由现有媒体存储负责，不能借 artifact API 复制一份。artifact store 不接收调用方
传入的绝对路径或 storage key，只接收受限的 `artifact_kind`、`artifact_key`、
payload schema、media type 和 bytes，并在 project package 内生成：

```text
artifacts/operations/
  <operation_id>/<step_attempt_id>/
    <artifact_kind>/<artifact_key>-<artifact_id>.<ext>
.artifact-staging/<artifact_id>.part
```

所有 ID/key 只允许安全字符；持久化 key 必须是 POSIX 相对路径、不能包含空段、
`.`、`..`、反斜杠或符号链接逃逸。第一版 media type 白名单为 JSON、UTF-8 text 和
opaque binary；图片、音频等正式媒体仍使用已有媒体模块。最终路径由 artifact ID
参与，因此不覆盖旧文件。

metadata service 不直接 import 领域文件实现。它依赖中立 typed
`ManagedArtifactFileBackend` port，调用方在组合边界注入 project-package adapter；
port 只暴露生成 key、stage、commit、verified read、staging cleanup 和 fingerprint，
不允许传入项目根目录或任意路径。这样 durable state machine 可以独立测试，文件系统
细节也不会把 `services → domain` 的反向依赖带进运行时图。

metadata 表只保存受管理引用和校验信息：

```sql
CREATE TABLE video_localization_operation_artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_schema_version TEXT NOT NULL,
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    step_attempt_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    payload_schema_version TEXT NOT NULL,
    media_type TEXT NOT NULL,
    storage_backend TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    staging_key TEXT,
    content_fingerprint TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,
    status_revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    committed_at TEXT,
    CHECK (size_bytes > 0 AND size_bytes <= 16777216),
    CHECK (length(content_fingerprint) = 64),
    CHECK (
        (status = 'staged'
         AND staging_key IS NOT NULL
         AND committed_at IS NULL)
        OR
        (status = 'committed'
         AND staging_key IS NULL
         AND committed_at IS NOT NULL)
    ),
    UNIQUE (
        project_id,
        operation_id,
        step_attempt_id,
        artifact_kind,
        artifact_key
    ),
    UNIQUE (project_id, storage_backend, storage_key)
);
```

第一版没有为 `(status, created_at)` 预建 speculative index：当前没有 retention/
quarantine 查询，先增加只会放大每次写入。WP-04F 在确定清理查询与 query plan 后再
增加与真实访问模式匹配的索引。

第一版只写 `staged | committed`。读取只接受 committed；损坏或缺失返回明确 integrity
error，普通 GET 不修复、不 quarantine。retention、tombstone 与 quarantine command
属于 WP-04F。

文件系统与 SQLite 不能伪装成单事务，采用可重放两阶段协议：

1. **stage**：先只读校验 step fence 和同 semantic slot 的幂等结果；把 bytes 写到唯一
   staging 文件，flush + fsync；再在 `BEGIN IMMEDIATE` 中重新校验 fence/step 并插入
   staged metadata。DB 失败必须删除本次 staging 文件。
2. **commit**：在 `BEGIN IMMEDIATE` 内重核 fence、step、staging/final 路径、size 和
   SHA-256；使用同文件系统 `os.replace` 移到 final，再把 row 改为 committed。
3. 若进程在 replace 后、DB commit 前崩溃，row 仍是 staged、staging 文件已消失但 final
   文件存在。相同 commit 重试验证 final 指纹后补提交，不能重复生成新 artifact。
4. 若 final 已存在但指纹不同，或任一路径逃逸/经过 symlink，停止并保留证据，不覆盖。

同一 step semantic slot + 相同内容的 stage 幂等复用；同 slot 不同内容必须冲突。所有
stage/commit 写入复用 WP-04A 的 execution fence 和 step identity validator；artifact
repository 不导入 queue。项目删除先清 artifact metadata，再清 step rows；project
package 删除继续由项目生命周期边界负责。

WP-04B 退出门：

- 空库/旧库 additive schema、相对 key、受限 media type/size 和不保存 payload；
- stage/commit/read、同 slot 幂等、不同内容冲突、并发 stage/commit；
- DB 插入失败清 staging、replace 后 DB rollback 可重放、重启后 staged/final 恢复；
- 文件缺失、内容篡改、storage key 越界、project root symlink 和 schema 损坏 fail
  closed；
- 删除按 artifact → step → attempt/ledger 顺序，真实项目移动后相对 key仍可解析；
- 本批不接 workflow/API/WebUI，不把开发 checkpoint 导入正式表，也不声称 detail
  reader 已切换。

### 5B.6 WP-04C Source Audio Shadow Integration

第一条正式 shadow 链选择 `source_audio` operation 内的 `extract_source_audio` step：
它是本地免费、无 Provider、拓扑稳定，生成的 WAV 已由现有媒体模块管理；本批只把
小型、path-free runner report 写入 artifact，不复制音频。正在频繁调整的 ASR、
本土化创作、视觉取证和付费步骤均不在本批范围。

输入契约为 `source-audio-step-input-v1`。`input_fingerprint` 基于源视频
content SHA-256 和 schema 生成；正常导入已有 content hash，不额外读取视频。历史项目
缺 hash 时才从受管理源视频计算一次。项目改名或 package 移动不会改变输入身份；若
prepare 后、真实提取前源视频内容改变，旧 step 标记
`VIDEO_LOCALIZATION_SHADOW_STEP_INPUT_CHANGED`，再用真实输入创建单调的新 attempt，
不能让旧 fingerprint 冒充实际执行输入。

成功输出为 `source-audio-step-output-v1`：

```json
{
  "schema_version": "source-audio-step-output-v1",
  "audio_extract_status": "completed",
  "duration_ms": 2345,
  "sample_rate": 44100,
  "channels": 1,
  "track_count": 1,
  "available_track_count": 1,
  "media_status": "available",
  "selected_source": "source_media"
}
```

输出不含 `audio_path`、project root、WAV bytes 或任意 locator，canonical JSON 必须
小于 1 KiB。顺序固定为：持有效 execution fence prepare local step → 执行现有媒体
提取和 guarded Project commit → stage/commit path-free artifact → 用 artifact
fingerprint 完成 step → 继续现有 operation final mirror。真实提取失败时 step 保存
原业务 error code，不生成 artifact。

这是 shadow 而不是 reader authority。prepare、artifact 或 step completion 的影子写
异常必须记录不含路径/payload 的 warning，并尽力把 step 标成
`VIDEO_LOCALIZATION_SHADOW_ARTIFACT_WRITE_FAILED`；不能把一个原本成功的 source-audio
产品操作改成失败。由此产生的 missing/failed shadow 必须由对账显式暴露，不能静默当作
matched。WP-04E 切 reader 前必须先关闭该 fail-open 窗口。

`reconcile_source_audio_operation(project_id, operation_id)` 在同一个 SQLite
`read_conn()` query-only snapshot 中读取 Project mirror、step 和 artifact metadata，
随后按持久 size/SHA-256 验证受管文件并比较 typed output。结果只返回
`matched | missing | incomplete | mismatch | invalid`、step attempt ID 和有界 issue
code；不修复、不回填、不改变 `data_version`，也不输出路径或 payload。第一版只对明确
指定的新 shadow operation 对账；历史 `operation-v1` 不做隐式 backfill。

WP-04C 退出门：

- 公共 source-audio operation 成功后同时得到 Project mirror、success step 和 committed
  path-free artifact，typed 对账为 matched；
- 原提取失败保留原 error code；影子 prepare/artifact 失败不改变既有产品结果，但对账
  必须报告 missing/incomplete；
- 输入竞争生成 failed old attempt + success new attempt，两个 fingerprint 不同；
- mirror 漂移、artifact 缺失/篡改、metadata/schema 损坏 fail closed；
- 对账证明 query-only、同 snapshot、`data_version` 不变；
- 既有 source-audio、attempt/fence、artifact、Project 删除和 operation reader 回归
  通过；quick/full、生产构建、最新真实服务及一个无付费 Web source-audio 操作通过；
- 本批不切 detail/Web reader，不回填历史步骤，不接付费 Provider。

### 5B.7 WP-04D Provider Integration

WP-04D 继续拆成三个单向批次：

1. **D1 explicit adjudication（done）**：先让 `result_unknown` 有可审计的最终裁决，
   不接 Provider、API 或 WebUI。
2. **D2 provider lifecycle adapter**：
   - **D2A（done）**：在 Provider 网络提交前持久化 submitted，响应到达后只写一次
     request ID；把超时、响应中断和进程崩溃转为 durable unknown，并阻断跨 worker
     attempt 的相同付费输入自动重放。
   - **D2B（done）**：把 Provider 查询恢复出的成功结果写入受管 artifact，逐字节
     校验 size/SHA-256 后再调用 D1 裁决；失败查询可直接走 D1 failed 裁决。
3. **D3 first paid workflow**：
   - **D3A durable semantic execution（done）**：把 resolved profile、精确输入、
     两轮 Provider 结果和安全调用记录固定为 typed contract；远程
     OpenAI-compatible/Codex CLI 保守归为 `external_paid`，loopback URL 才归为
     `external_free`。OpenAI-compatible 请求发送与 step ledger 相同的有界
     `Idempotency-Key`；新 worker 可复用旧 worker 已成功提交的同输入 artifact。
   - **D3B product wiring（implemented）**：service/operation queue 和启动恢复已接到
     D3A；提交时固定非秘密 profile configuration fingerprint，执行时复核并把同一
     resolved profile 传给 transport。只有 v2 且带合法指纹的任务会重入恢复；旧 success
     复用 artifact、paid unknown 阻断、loopback unknown 可重放。Draft
     source-fingerprint CAS、operation workflow summary、unknown 用户提示和无付费
     API/恢复集成测试均已接通。浏览器验收与本批最终门禁证据记录在工作路线图。

ASR 全文理解、本土化创作、画面取证、资料查询和 Web Search 都含多轮/批次调用，且部分
拓扑仍在调整，不作为第一条迁移入口。分轨、参考音和源音轨不冒充网络 Provider；
本地模型是否需要独立 submitted 语义在真实 engine port 统一后再决定。

#### D1 Explicit Adjudication

`video_localization_operation_step_adjudications` 每个 step attempt 最多一条
`operation-step-adjudication-v1`：

```text
adjudication_id
project_id + operation_id + step_attempt_id
decision: success | failed
source: provider_query | human_review
reason_code
expected_status_revision → resulting_status_revision
provider_request_id snapshot
output_fingerprint | error_code
decided_at
```

裁决不是 worker 工作，不能要求或伪造 execution fence。命令在
`BEGIN IMMEDIATE` 中先按复合 identity、`result_unknown` 和 expected revision 做 CAS，
再把 step 改成 success/failed，并在同一事务插入不可变记录；任一步失败全部回滚。
`provider_query` 必须已有 request ID，`human_review` 可处理 Provider 没有查询能力或
请求 ID 丢失的情况。success 必须给出 lowercase SHA-256 output fingerprint，failed
必须给出稳定 error code；reason 只接受有界大写 code，不存自由文本。完全相同的命令
幂等返回原记录，不同 decision/source/reason/result 冲突；记录与 terminal step 漂移
时 fail closed。

D1 本身只补齐 durable state transition；D2B 已在独立应用服务中要求 success 恢复先
写 committed artifact、核对指纹，再调用 transaction-level 裁决。裸 D1 repository
仍不会自行检查 artifact，也不会更新 Project compatibility mirror 或 operation 终态。
D3 才接真实 workflow/Provider query port；在此之前内部命令不是用户可用入口。

D1 退出门：

- 旧库 additive 建表且 bootstrap 不留隐式 transaction；
- success/failed 形状、Provider query request ID、复合 identity、expected revision 和
  UTC 时间均 fail closed；
- 同命令重放幂等，不同命令冲突；并发只有一条记录；
- step update 与 adjudication insert 同事务，注入故障零部分写；
- DB busy、重启、future schema、terminal drift、复合索引和项目删除有固定测试；
- quick/full、架构策略、生产构建和最新真实服务兼容验证通过；
- 本批不开放 API/WebUI、不接 Provider、不调用付费能力，也不改变 detail reader。

#### D2A Durable Invocation And Replay Barrier

`video_localization_provider_step_lifecycle` 是 workflow 与真实 Provider adapter 之间的
内部应用门面；它接收中立 `ManagedArtifactFileBackend` port 和一个注入的单次调用
callback，不 import 视频本土化领域实现，也不复制具体 LLM/TTS 请求。

固定顺序为：

1. 使用当前 live `ExecutionFence` 把同 operation 中较旧 worker attempt 的 external
   `submitted` 转为 `result_unknown`；prepared 不改，因为 durable 状态证明网络尚未
   提交。
2. prepare 当前 step。`external_paid` 会在同一 `BEGIN IMMEDIATE` 中按
   project/operation/step/workflow/input 检查其他 worker attempt 的
   `submitted/result_unknown`；命中时在创建新 row 之前抛出 typed replay block。
3. 将当前 step 提交为 submitted 并提交 SQLite transaction，随后才允许 callback
   越过网络边界。submitted 写失败时 callback 调用次数必须为 0。
4. Provider 明确拒绝且调用方能证明未接受工作时写 failed；超时、连接/响应中断、
   未分类异常全部写 result_unknown，错误消息不进入 ledger。
5. 成功响应先补只写一次的 request ID，再 stage/commit 小型 typed artifact，最后
   用 artifact SHA-256 完成 success。响应 JSON/schema 无效是已知 failed；响应已到达
   后的文件/SQLite 持久化失败是 result_unknown，不能自动再请求。
6. 同 worker attempt 重入 success 时必须重新读取并校验 committed artifact 和 step
   fingerprint；一致才返回 reused，不调用 Provider。submitted/result_unknown 重入
   均 fail closed。

跨 attempt 屏障有意限定在同一 project + operation + step + workflow + input。显式
创建新的 operation 是新的用户命令，不被偷偷折叠为旧请求；是否在 UI 对相同输入再次
计费做二次确认属于 D3 产品入口。`external_free` 仍记录 unknown，但允许新 worker
重放；cost class 必须由真实 Provider profile 决定，不能为了绕过屏障把远程服务标成
free。

D2A/D2B 没有 Provider query port。D3A/D3B 已由 `semantic_tts_grouping` 的独立执行层
复用这些门面并接入 service/operation queue；固定假 Provider 与 loopback 服务用于
故障和产品验收，禁止为了测试调用真实付费 Provider。没有 query port 时，paid unknown
仍必须由用户检查 Provider 记录后通过显式重试或后续裁决入口处理。

#### D3A Durable Semantic Grouping Execution

`semantic-tts-grouping-input-v2` 固定 prompt version、resolved profile/model/protocol、
Provider endpoint fingerprint、目标/最大字数和按顺序排列的字幕 ID/正文/说话人。完整
输入只用于内存中的请求和 SHA-256 计算；step ledger 只保存 fingerprint，不保存字幕
正文或 endpoint。每个最多两轮的请求是独立 step：

```text
semantic_grouping_round_1
semantic_grouping_round_2
```

每轮 artifact 使用 `semantic-tts-grouping-round-v1`，只保存 round index、结构状态、
字幕 ID 分组和 `VideoLocalizationLlmCallRecord`。它不保存 prompt、字幕正文、API Key、
原始 Provider envelope、隐藏 reasoning 或本地路径。第一轮只要得到可审计的结构化
Provider 响应就先成为 success artifact；领域连续性、覆盖、说话人和字数校验失败时，
错误摘要进入第二轮 input fingerprint，再提交第二个 step。两轮都不合法才使 workflow
失败，已成功的轮次证据不会丢失。

Provider profile 分类固定为：

- `codex_cli` → `external_paid`；
- 非 loopback OpenAI-compatible → `external_paid`；
- loopback OpenAI-compatible → `external_free`。

Provider idempotency key 由 project/operation、operation attempt、step 和 input
fingerprint 生成；OpenAI-compatible transport 发送同一个 `Idempotency-Key`，Codex CLI
没有等价 header，仍由本地 submitted/unknown 屏障保证不自动重放。超时、网络中断、
Provider unavailable 和 Codex 执行结果不确定统一进入
`VIDEO_LOCALIZATION_LLM_RESULT_UNKNOWN`；鉴权、配置、限流、拒绝或已知无效响应进入
failed，不把 Provider 错误正文写入 ledger。

step prepare 在创建新 attempt 前先查找同 operation/step/workflow/input 的旧 success；
cost class 和 Provider identity 一致时复用其 committed artifact，忽略新 attempt 尚未
使用的候选 idempotency key。这关闭“旧 worker 已提交 artifact、Draft/operation 尚未
提交”窗口，同时仍让旧 submitted/result_unknown 的 paid input 优先阻断。D3B 已让新
worker 先由该执行层决定 reuse/unknown，再决定 Draft 和 operation 终态；只有旧 schema
或缺少配置指纹的语义分组仍由兼容恢复路径泛化为 interrupted。

D3A 退出门：

- contract/fingerprint 覆盖 prompt、内容、speaker、limits、resolved model 和第二轮
  校验上下文；future/额外字段 fail closed；
- 远程、本地和 Codex cost class 有固定测试，真实 Provider 调用次数为 0；
- submitted 先于 callback，OpenAI-compatible 收到同一幂等键；
- 成功 artifact 可在同 worker 和新 worker 重入复用，callback 次数保持 1；
- 首轮 invalid 的 artifact 保留，第二轮输入包含有界错误摘要；
- timeout/result unknown 同 attempt 不重放，错误信息不含 Provider 原文；
- D3A 只证明独立执行层；service/queue、启动恢复、Draft CAS 和 operation workflow
  summary 已由 D3B 接入。detail reader authority 和用户裁决入口仍是后续工作。

#### D3B Product Wiring And Recovery

`semantic_tts_grouping.py` 现在只保留纯领域转换和校验；所有 Provider 调用都经过
`semantic_tts_grouping_execution.execute_semantic_tts_grouping`。operation 提交会解析
一次目标 profile，并把以下不含凭据的配置做 canonical SHA-256：

```text
profile_id + protocol + base_url + model_id
+ provider_model_id + reasoning_effort
```

运行时先重新解析并比对该指纹；不匹配时在 step prepare 和 Provider callback 之前返回
`VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROFILE_CHANGED`。匹配时把同一个
`ResolvedProfile` 传入 `llm_runtime.complete_json`，避免指纹检查后 transport 再读设置
产生 TOCTOU。显式 retry 会丢弃旧指纹、按当前配置创建新 operation；同类 active operation
仍保留原指纹参与参数冲突判断。

operation summary 固定 `semantic-tts-grouping-workflow-v2` 和 prepare/group/validate/write
四个公开原子任务，ledger 因而不再记录默认 `operation-v1`。worker 将 live
`ExecutionFence` 传给执行层；service 在结果返回后重新检查字幕 source fingerprint，并
通过原 commit gate 写 Draft。

启动恢复仅重入同时满足 v2 summary 和合法配置指纹的 active operation：

- 旧 attempt 已有 success artifact：跨 attempt 校验并复用，Provider 调用为零，再补交
  Draft 和 operation success；
- 旧 external-paid submitted/result_unknown：转/保持 unknown 并返回用户可见的
  `VIDEO_LOCALIZATION_SEMANTIC_GROUPING_RESULT_UNKNOWN`，Provider 调用为零；
- 旧 external-free unknown：允许新 attempt 再提交一次；
- 旧 workflow 或缺指纹：保持通用 interrupted 兼容行为，不冒险重放。

固定 Provider 单元/集成测试必须覆盖以上分支、配置变化零调用、显式重试重新锁定、API
提交、ledger workflow version、Draft 持久化和重启恢复。最终 Web 验收只使用 loopback
假 OpenAI-compatible 服务，不使用真实付费 Provider。

### 5B.8 WP-04E Detail Reader Authority

当前不能直接把 detail reader 从 Project mirror 切到 ledger。原因不是 reader 尚未
改一行调用，而是三个权威来源合起来仍缺 operation 级完整参数：

- command ledger 只拥有 identity、status、revision、workflow version 和参数指纹；
- summary core 只拥有有界列表字段，不能为了详情重新膨胀；
- step/artifact/adjudication 拥有执行状态、步骤输出和裁决，但 semantic round artifact
  不保存完整输入，Project mirror 仍是 `target_chars`、`max_chars`、profile 配置指纹等
  详情字段的唯一副本。

WP-04E 因而按以下顺序迁移，禁止用“先读 mirror，缺什么再补”的长期 fallback，也禁止把
`task_step_results`、Provider 原文或字幕正文整体复制到新的 detail JSON：

1. **E0 field ownership fixture**：为 `semantic-tts-grouping-workflow-v2` 和
   `source-audio` 固定成功、失败、unknown、cancelled fixture，逐字段标记 ledger、
   attempt、detail core、workflow registry、step、artifact、adjudication 和 Project
   domain 的唯一 owner；先证明现有来源缺口，不改变产品 reader。
2. **E1 additive detail core**：新增版本化、path-free 的 operation-level detail core，
   只保存不能由其他权威重建的公共参数和 presentation metadata。它不保存状态、时间、
   step result、artifact locator、字幕正文、Provider 响应、凭据或隐藏推理；canonical
   JSON、SHA-256、复合 project/operation identity 和 `extra=forbid` 为强制契约。
3. **E2 transactional shadow writer**：submit/retry 与 terminal commit 在现有 command
   transaction 内同步写 detail core；step/artifact 仍各写自己的表。对账器必须在同一
   query-only SQLite snapshot 中组装 ledger detail、验证受管 artifact bytes，再与
   Project mirror 的公共 payload 逐字段比较，返回
   `matched | missing | incomplete | mismatch | invalid`，不修复、不回填、不输出路径。
4. **E3 workflow-scoped reader switch**：只对已经满足 E2 退出门的 workflow 使用新
   authority；损坏或缺失的新权威显式返回 repair-required，不静默读取 mirror。旧
   `operation-v1` 继续走命名清楚的 legacy adapter，而不是伪装成同一 authority。
5. **E4 broaden and close**：逐个 workflow 补齐 typed output/reader 后扩大 allowlist；
   全库对账、故障注入、性能门和 Web E2E 通过后，关闭 runtime mirror fallback，再进入
   WP-04F 删除双写、retention、tombstone 和 artifact 回收。

detail assembler 的固定所有权为：

| 字段 | 唯一来源 |
|---|---|
| operation identity、kind、status、cancel、created/completed | command ledger |
| started、attempt、fencing 和执行资格 | attempt store |
| 完整公共参数、不可重建的 operation 级展示 metadata | detail core |
| workflow 拓扑、步骤名称、依赖和输出契约版本 | versioned workflow registry |
| step status、cost class、Provider request ID 和 error code | step store |
| typed step result、模型调用记录和小型业务输出 | committed managed artifact |
| unknown 的人工 success/failure 决策 | adjudication store |
| 当前项目产物可见性与 domain result | Project domain read model |

E1/E2 必须先用固定 fixture 证明新 detail core 明显小于旧完整 operation mirror，且读取
单条详情不查询 `projects.data`。E3 退出门还必须覆盖 mirror 漂移、core/step/artifact
缺失或篡改、future schema、错误 project/operation 复合身份、服务重启、刷新后详情、
公开路径脱敏和 query-only `data_version` 不变。

E0/E1 foundation 批次（2026-07-31）：

- `operation-detail-core-v1` 第一版只支持
  `semantic-tts-grouping-workflow-v2`。其 typed parameters 只含 profile ID、无密钥
  profile configuration fingerprint、target chars 和 max chars；workflow ID 与 scope
  由 registry/operation policy 重建，ledger lifecycle、step result、artifact、Provider
  响应和 Project domain result 不进入该 core。固定 fixture 的 canonical JSON 小于
  512 bytes，额外字段、future schema、无效 SHA-256 和错误字数边界均拒绝。
- additive repository 以 `(project_id, operation_id)` 为复合主键，写入前要求已有且
  kind/workflow 一致的 ledger row；同内容逐字节幂等，不同内容冲突。读取重新做 typed
  validation、canonical bytes、SHA-256、row/core/ledger 复合身份核对；损坏 JSON、
  metadata、fingerprint 和 future schema 全部 fail closed。caller-owned transaction
  回滚不会留下孤立 core，Project 删除会在删除 ledger 前清理 core。
- 专项 22 项、quick 后端 327 + 14 项、前端 207 项通过；full 后端 1119、前端 728、
  Svelte 0 errors / 0 warnings、production build、架构策略和 bundle budget 通过。
  客户端主块 456.36/129.94 kB、静态闭包 724.75/212.32 kB、SSR
  654.94/120.85 kB（raw/gzip）。本批没有产品 writer、API 或 WebUI 行为变化，因此
  不伪造浏览器 E2E；E2 才把 submit/retry 与 terminal commit 接入同事务 shadow write。

E2 transactional shadow writer 批次（2026-07-31）：

- Draft Store 只为当前 submit/retry command 或有效 execution fence 指向的 operation
  投影 detail core，并把它传入既有 `BEGIN IMMEDIATE` Project/ledger 提交点；新 command
  先创建 ledger identity，再在同事务写 core，worker 则先验证 fence。core 冲突、失效
  fence、Project revision CAS、command-owned 参数或已纳入 typed detail 管理的 workflow
  漂移都会让 ledger、outbox、Project、projection revision 和 core 一起回滚。旧
  workflow 暂保留原 summary-derived 兼容行为；普通内容/工作区保存不扫描或回填历史，
  retry 使用新 operation identity 写独立 core。
- `operation_detail_reconciliation.py` 在同一个 `database.read_conn()` query-only
  snapshot 中发现 semantic-v2 候选，逐条核对 ledger identity/lifecycle/完整参数指纹、
  typed core 与 Project mirror；成功的 semantic round 还必须有匹配 step output
  fingerprint 的 committed managed artifact，并重新验证文件 bytes、媒体类型、payload
  schema 和 round contract。报告仅含 `matched | missing | incomplete | mismatch |
  invalid`、有界问题码、计数和复合身份；不含 profile、payload 或路径，也不修复数据。
- `audit_video_localization_operation_details.py --check` 对截断或任意非 matched 结果
  返回非零。故障注入覆盖 writer 回滚、stale fence、immutable conflict、workflow
  downgrade、缺 core、mirror 漂移、core/文件损坏、缺 artifact、terminal step 缺口及
  新进程 `PRAGMA data_version` 不变。107 项专项测试、quick 门禁（后端 327、
  benchmark/read 14、前端 207）和 full 门禁（后端 1138、前端 728、Svelte
  0 error/0 warning、生产构建与 bundle budget）通过。真实 Web 流程用本机固定
  Provider 验证首次失败、历史重试成功、2 个分组及刷新后成功/失败历史持久可见；同库
  query-only audit 为 2/2 matched。E2 不改变 API/WebUI detail reader，也不自动迁移
  旧 v2 operation；全库 missing 项需在 E3 authority switch 前通过显式迁移关闭。

E3 workflow-scoped reader 批次（2026-07-31）：

- `operation_detail_reader.py` 只把 `semantic-tts-grouping-workflow-v2` 纳入 managed
  allowlist。一次 `database.read_conn()` snapshot 从 ledger、detail core、workflow
  registry、attempt、step 和 committed managed artifact 组装公开详情；成功 artifact
  会重新验证文件 bytes、SHA-256、media type、payload schema 和 round contract。
  健康 managed 路径不查询 `projects.data`，旧 workflow 明确返回 legacy authority。
- semantic v2 执行现在为 prepare/group/validate/write 四个 canonical local step
  持久化 `local_free` attempt，Provider round 保持独立 external step。profile 变化、
  Provider/结果错误、两轮验证失败和最终 Draft commit 失败都有稳定的步骤终态与错误码，
  重入保持已成功步骤和 artifact 不可变。
- `migrate_video_localization_operation_details.py` 默认 query-only 规划历史缺失 core；
  显式 `--apply` 才按复合 keyset cursor 分批写入。每条候选先比较 Project mirror 与
  ledger 的身份、生命周期、workflow 和完整参数指纹；漂移、损坏或 future schema
  只报告 rejected，零部分修复，重复执行幂等且不推进项目 `updated_at`。
- 产品 detail endpoint 按 workflow 分流。managed core/step/artifact/ledger 缺失或损坏
  返回 `VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED`（409），不静默退回
  mirror；WebUI 展开历史时呈现该错误。旧 `operation-v1` 仍使用命名清楚的 legacy
  adapter。
- 专项 71 项、相邻 operation/project/semantic 287 项通过。quick 门禁已将 detail
  core/shadow/reconciliation/reader/migration 全部纳入常驻集合，后端 361 +
  benchmark/read 14、前端 208 项通过；full 后端 1153、前端 729、Svelte 0/0、
  production build、架构策略和 bundle budget 通过。客户端主块
  456.88/130.09 kB、静态闭包 725.49/212.52 kB、SSR 656.02/121.07 kB
  （raw/gzip）。
- 最新生产预览用本机固定 OpenAI-compatible Provider 验证：页面只提交 1 次带幂等键
  的 completion，生成 2 个连续组；详情显示 prepare/group/validate/write 四项成功，
  刷新和后端重启后仍可读取，console 0 warning/error。删除 typed core 后，API 返回
  明确 409 且 WebUI 显示 repair-required；默认 migration 只规划，显式 `--apply`
  恢复 1 条 core，Project `updated_at` 不变，重启后详情恢复且 query-only audit 为
  1/1 matched。全程未调用真实付费 Provider。semantic-v2 已通过 E3 退出门；E4
  仍负责逐个迁移其他 workflow 并最终关闭 legacy detail fallback。

#### 5B.8.1 E4 全量清单、迁移顺序与关闭条件

E4 不按 `OperationKind` 枚举顺序机械迁移。每一类任务必须先证明参数可重建、结果有
typed owner、写入失败不会被产品 success 吞掉、旧版本有明确兼容边界，才能加入 managed
reader allowlist。当前七类任务的目标顺序如下：

| kind | 当前参数与结果来源 | 当前缺口 | E4 决策 |
|---|---|---|---|
| `semantic_tts_grouping` | typed detail core + registry + canonical local/Provider step + managed artifact | E3 已满足 managed reader 退出门 | 保持 managed；作为其他 workflow 的故障注入基准 |
| `source_audio` | 新任务使用零输入 `source-audio-workflow-v1`；正式 local-free step 和 path-free `source-audio-step-output-v1` artifact 是结果权威，Project 只保留媒体草稿投影 | 旧 `operation-v1` 仍走 legacy adapter；全局 discriminated submit registry 尚未覆盖其他 kind | E4.2 已切 managed writer/reader/recovery；作为 stems 的本地媒体 saga 基准 |
| `stems` | 新任务使用零输入 `stem-separation-workflow-v1`；实际源音轨 fingerprint、canonical local step、双轨媒体和 path-free typed artifact 共同组成结果权威 | E4.3B 已完成 writer/reader/recovery；旧 `operation-v1` 仍使用 legacy adapter | 保持 managed media saga；缺失媒体只允许按已提交 contract 做确定性本地重建 |
| `reference_clips` | 新的后台自动候选使用零输入 `reference-candidates-workflow-v1`；实际人声、候选 cue/revision、canonical step、候选媒体和 path-free artifact 共同组成结果权威 | E4.3C 已完成自动候选 writer/reader/recovery；旧 `operation-v1` 仍使用 legacy adapter；用户明确选区是独立同步命令 | 保持自动候选 managed；不得把手动选区伪装为同一后台 workflow |
| `speaker_diarization` | 新任务使用 typed detail core + actual-audio fingerprint + canonical local step + path-free `speaker-diarization-step-output-v1` | E4.4A 已完成 standalone writer/reader/recovery；旧 `operation-v1` 外部快照只读，ASR initial-analysis 内的 diarization 仍属于后续 ASR workflow 迁移 | 保持 `speaker-diarization-workflow-v1` managed；E4.4B 再迁 ASR development snapshots，不把两种 workflow 伪装成同一结果权威 |
| `english_asr` | 新的 `stop_after=asr` 使用 typed detail core + actual-audio fingerprint + canonical local step + path-free `asr-raw-step-output-v1`；正式结果仍在 Project，其余开发子步骤仍在多种 snapshot | E4.4B1 已完成 raw-ASR writer/reader/recovery；initial-analysis 和后续 development snapshot 仍共享宽 workflow；正式流拓扑仍在调整；大型正文不能复制进 detail core | 保持 `asr-raw-development-workflow-v1` managed；E4.4B2 继续逐个迁开发断点，正式 `video-localization-workflow-v1` 仍是 legacy |
| `localization_draft` | v3 参数与 workflow summary；正式双轨在 Project，开发 checkpoint 独立保存 | 当前子任务流程持续调整；与 ASR 共用过宽的 `video-localization-workflow-v1`；大量 step result 仍在 mirror | 最后迁移；先稳定 definition/version 和 checkpoint/artifact 边界，不在 E4 前段追逐临时拓扑 |

审计同时发现一个必须先于 source-audio reader switch 修复的 identity 缺陷：
`_operation_identity()` 和 ledger audit 都先执行 `str(None)`、再应用默认值，导致缺少
`workflow_schema_version` 的任务把字面量 `"None"` 持久化成 workflow version；审计
端重复同一错误后会给出假 matched。`operation-v1` 只能表示显式 legacy adapter，
不能与 `"None"` 并存为两个等价版本。

E4 固定拆分为以下批次：

1. **E4.0 identity hygiene（done）**：修正缺失/`null` workflow version 的标准化；新增只读规划、
   显式 apply 的有界迁移，把 ledger 与既有 step attempt 中可证明等价的 `"None"`
   原子改为 `operation-v1`。存在 detail core、未知 workflow、Project mirror 漂移、
   step/ledger 身份不一致或截断时拒绝修改。审计实现必须与生产标准化函数共享 fixture，
   不得复制同一种 bug。
2. **E4.1 typed operation registry（in progress）**：registry 以 `(kind, workflow_version)` 为键，包含
   参数 schema、拓扑、detail assembler、迁移器和兼容 adapter。`source_audio`、
   `stems`、`reference_clips` 的零输入契约拒绝额外键；公开 submit API 最终改为
   discriminated union，不再以任意 `dict` 作为长期契约。当前 source-audio 已拒绝
   额外字段，legacy retry 会清除旧的无效键；其余 kind 和 OpenAPI union 仍待后续批次。
3. **E4.2 source-audio mandatory writer（done）**：引入 `source-audio-workflow-v1`，把
   `extract_source_audio` 从 fail-open shadow 改成受 fence 约束的正式 step。媒体产物先
   staging；Project media projection、ledger terminal、step terminal 与 artifact
   metadata 在一个数据库提交或可证明可重放的 saga 中闭合。任一阶段失败都必须留下
   确定状态，不能出现产品 success 但详情缺失。
4. **E4.3 stable local workflows**：依次迁移 stems、reference clips；只复用已通过
   crash matrix 的媒体/本地步骤模板，不为每类任务复制 writer、recovery 和 reader。
5. **E4.4 development artifacts（A/B1 done，B2 pending）**：E4.4A 已把 standalone
   diarization 迁入 canonical managed artifact；E4.4B1 已迁移 `stop_after=asr`
   raw-ASR 断点。E4.4B2 继续迁 initial-analysis 和其余 ASR development snapshots。
   任意本地路径只留在 runtime/store 内部，公共详情只返回 resource identity 和有界
   结构化结果。
6. **E4.5 changing workflows**：ASR 正式流与 localization-v3 只有在 definition、
   输入输出版本和开发 replay 边界冻结后才逐步骤迁移。流程调整只新增 workflow
   version，不修改旧 version 的 reader/registry。
7. **E4.6 authority close**：全库所有 `(kind, workflow_version)` 要么 managed，要么在
   有界 legacy allowlist 中并有删除期限；未知组合 fail closed。全库对账、故障注入、
   性能预算、完整门禁和真实 Web E2E 通过后，才关闭 runtime mirror detail fallback。

每个 workflow 的固定测试矩阵至少包含：

- 参数：默认值、额外字段、边界值、canonical JSON、完整参数 fingerprint、retry 锁定；
- 生命周期：提交失败、queued、running、success、failed、cancelled、stale fence、
  服务重启和同项目并发；
- 存储：core/step/artifact 缺失、future schema、metadata/bytes/fingerprint 篡改、
  跨 project/operation 身份和 caller transaction rollback；
- 文件故障：staging 前后、文件 rename 前后、DB commit 前后、清理失败和相同输入重放；
- reader：健康路径不查询 `projects.data`，历史结果不被当前 Project 状态伪造，
  path/secret/Provider 原文不出公共响应，损坏时返回 repair-required；
- 迁移：query-only 默认、keyset 有界、apply 显式、幂等、拒绝部分修复、
  `updated_at` 不推进、审计自身不写库；
- 产品：submit/cancel/retry、运行中与历史、详情、错误提示、刷新、后端重启、窄屏、
  键盘焦点和 console 0 error；付费 workflow 使用固定 loopback Provider。

E4.0 完成证据（2026-07-31）：

- writer 和 ledger audit 共享 null-safe workflow normalizer；缺失/`null`/空值固定为
  `operation-v1`，非法类型和字面量 `"None"` 拒绝。普通 Project projection 不隐式
  部分迁移旧 sentinel。
- 默认 query-only、显式 apply 的 keyset migration 会在一个事务中更新 ledger 与已有
  step；detail core、mirror、参数、生命周期或 step identity 冲突时逐条拒绝。
- 真实库 250/250 sentinel 迁移后为 0 sentinel、0 step mismatch、440/440 audit
  matched；Project 全行与 ledger 非 workflow 字段均为 0 差异。完整门禁和真实 Web
  legacy source-audio 历史/详情/刷新通过，未调用 Provider。

E4.2 当前实现（2026-07-31）：

- 新提交的 source-audio 固定使用 `source-audio-workflow-v1` 和零输入参数契约；
  任意额外参数立即拒绝，旧 `operation-v1` 详情与 retry 仍有显式兼容边界。
- `source_audio_execution.py` 是唯一正式执行门面：源视频 SHA-256 锁定输入，受 fence
  的 local-free step 先提交 path-free typed artifact，再由同一次 Project save 写入
  媒体投影、operation success 与 ledger projection。artifact/step 或最终 Project
  保存失败时 operation 保持 failed，不能产生“产品 success、详情缺失”。
- 最终 Project 保存失败但 step/artifact 已成功的窗口是可重放 saga：新 execution
  claim 重新进入同一门面，复用已验证的本地 success artifact，并只重做不计费的本地
  媒体提取。源视频变化则 fail closed，不自动把旧输入升级成新输入。
- `source_audio_detail_reader.py` 在一个 query-only snapshot 中从 ledger、registry、
  step 和重新校验的 artifact bytes 组装 queued/running/success/failed/cancelled 详情，
  健康路径不查询 `projects.data`，公开响应不含音频绝对路径。统一 detail audit 已把
  source-audio-v1 纳入与 Project compatibility mirror 的只读对账。
- E4.3A 已先把确定性本地 step 的 fenced prepare、artifact commit、success replay、
  failure terminal 和零输入 detail state 抽到 `managed_local_step.py`、
  `managed_local_detail.py` 与 typed spec。source-audio writer/reader 已改用这些原语，
  原有 crash/replay/repair 行为和公开错误码保持；stems/reference-clips 尚未因此自动
  变成 managed，必须分别完成输入锁、Project saga、typed output 和故障矩阵后才可切换。
- E4.3B 把新 stems 提交切到 `stem-separation-workflow-v1`：零输入命令拒绝 extra，
  step 输入锁定实际源音轨 SHA-256，输出 artifact 保存双轨 SHA-256、引擎和质量状态但
  不保存路径。两个 operation-owned WAV 与 Project stems/operation success 组成可重放
  saga；崩溃恢复优先校验并复用 WAV，文件缺失才重跑本地分离且必须与 artifact 完全一致。
  详情读取和统一 reconciliation 均走 ledger/step/artifact 的 query-only snapshot；
  旧 `operation-v1` stems 保持 legacy adapter，retry 创建新版本任务。
- E4.3C 把后台自动参考音候选切到 `reference-candidates-workflow-v1`：零输入命令
  拒绝 extra，输入包含实际干净人声 SHA-256、有序候选 cue identity 和相关 Draft
  revision；typed artifact 只存候选媒体 ID、范围、时长和 SHA-256。operation-owned
  WAV 可在崩溃后直接复用，缺失时重切并要求 artifact replay；artifact/Project 失败、
  媒体损坏及相关并发编辑均不产生 product success。最终 Project save 只合并
  reference/cue/speaker 与 operation success，无关 UI/时间线改动保留。手动选区继续
  使用同步编辑入口；旧 `operation-v1` 自动任务只读，retry 创建新版本任务。隔离
  full 门禁通过后端 1192、前端 729、Svelte 0 errors / 0 warnings、production build
  与 bundle budget。合入并行开发状态后主工作区 quick 通过后端 400、读基准 14、
  前端 209；真实 Web 还验证了 stems running/success、参考音历史和最终指标、刷新与
  服务重启持久化、artifact 损坏 repair-required/恢复，以及手动选区与自动候选共存，
  浏览器 console 0 error，后端无 5xx/Traceback/ERROR。
- E4.4A 把 standalone speaker diarization 切到
  `speaker-diarization-workflow-v1`：typed detail core 保存 engine/track/range，
  prepared runtime context 在模型执行前锁定实际音频 SHA-256；canonical local-free
  step 写入完整 path-free 结果，恢复时复用 committed artifact 而不再次运行本地模型。
  shared local primitive 同时要求一个 operation/step 的输入 fingerprint 跨 attempt
  稳定，音频或参数变化 fail closed。unknown submit 参数、artifact 写入失败、bytes
  损坏和缺失权威均不能产生 product success；公共详情与 development-result API 不含
  `audio_path`。旧 `operation-v1` 快照只读，retry 创建新 workflow。
- E4.4B1 把 `stop_after=asr` 切到 `asr-raw-development-workflow-v1`：typed detail
  core 只保存请求引擎、音轨和语言，runtime prepare 再锁定 resolved track、实际音频
  SHA-256、引擎、语言和时长。canonical local-free step 的
  `asr-raw-step-output-v1` 保存完整原始听写、片段、质量和耗时，不保存 `audio_path`；
  结果 metadata 与片段数、字符数、首尾时间、音频时长和未完成区间必须交叉一致。
  committed artifact 可在恢复时直接重放且不再推理；输入变化、artifact 写入失败、
  语义矛盾或缺失权威都 fail closed。新任务不修改正式 Draft，旧 `operation-v1`
  raw-ASR snapshot 只读，retry 创建新 workflow。

#### D2B Recovered Result Commit

`video_localization_provider_result_recovery` 只接受 Provider 查询已经确认的成功内容；
它不负责联网查询，也不接受裸路径或任意文件引用。命令先校验 adjudication 形状、
复合 identity、exact result_unknown revision、external cost class 和已持久化 request
ID，再将 typed bytes 写入固定 semantic artifact slot。

文件与数据库采用可重放提交：

1. stage 在文件写入前、SQLite 登记前分别校验同一个 result_unknown revision；并发裁决
   发生时不登记恢复 artifact。
2. commit 由 caller 先持有 SQLite write transaction，再验证 artifact/step identity；
   `os.replace` 后更新 artifact metadata，并在同一 transaction 调用 D1
   transaction-level command 更新 step 与插入 adjudication。
3. artifact、step 和 adjudication 的 fingerprint 必须全部等于恢复 bytes 的
   SHA-256。数据库任一写失败会一起回滚；若 final file 已替换，metadata 仍为 staged，
   下一次相同命令会验证 final file 并补交数据库状态。
4. 完全相同的已完成命令重放必须重新读取文件，核对 payload schema、media type、
   artifact fingerprint、step 和 adjudication 后返回 reused；内容、契约、reason 或
   decision 不同均冲突。
5. failure adjudication 若先取得写锁，后续 success artifact commit 会因 status
   revision 失效而拒绝，已登记 artifact 保持 staged；success 与 failure 不会各提交
   一半。

D2B 仍是内部应用能力：没有 Provider query adapter、鉴权 API、WebUI、Project mirror
同步或 operation terminal reconciliation。D3 接入首个 workflow 时必须决定远程/本地
cost class，把真实 Provider 的“明确拒绝 / 结果未知 / 可查询恢复”映射到这些固定契约，
并继续使用假 Provider 做故障测试，不以真实计费调用做回归。

## 6. 数据和隐私

- attempt/ledger 不存完整输入输出、字幕文本、媒体路径或 provider 密钥。
- artifact 只保存受管理 ID、相对引用、版本和指纹。
- 审计输出不暴露 runner ID、绝对路径、payload 或授权信息。
- 当前删除路径会在同一 transaction 清理 projection revision、command ledger、outbox
  和 attempt；
  “先 tombstone/取消有效 lease，再清理受管理 artifact”的完整生命周期仍在 Project
  Phase 3 实现。

## 7. 验证门

每一阶段至少需要：

- transaction 级并发 claim 测试；
- lease 边界、过期接管、heartbeat、旧 token 防写测试；
- DB busy timeout 与 rollback 测试；
- worker 双实例竞争和服务重启故障注入；
- cancellation 与 lease 丢失的线性化测试；
- quick、full、生产构建；
- 接入 Web 可触发路径后，在最新服务上完成真实浏览器回归。

Phase 2 完成还必须证明：

- 同一 operation 任意时刻最多一个有效 runner；
- 失去 lease 的 runner 无法写任何用户可见结果；
- provider 不确定结果不会自动重复计费；
- operation 详情和恢复在重启后读取同一权威状态；
- Project JSON 只作为可重试兼容镜像，不再形成第二权威。
