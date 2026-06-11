# Voice Studio Task Orchestration RFC

**版本**: v1.0（Phase 1）
**适用范围**: 语音生成/长文段落任务/批处理/ASR 的统一任务编排。  
**时间**: 2026-06-11  

## 1. 背景与目标

本 RFC 统一 Voice Studio 已有多套任务执行路径（`task_queue.py`、`longform_queue.py`、`batch_queue.py`、`asr_tasks.py`）的核心语义，作为 Phase 1 规格和 Phase 2 实施协议。  

目标优先级从高到低：

1. 统一状态语义：所有任务类型共享一致状态机。
2. 幂等与入队控制：同一 task 在同一 live queue 中只出现一次。
3. 服务重启恢复：明确可恢复与不可恢复边界，避免“重复提交—重复计费”。
4. 取消与超时：语义统一，可控且可观测。
5. 重试策略：不覆盖历史数据，允许幂等重放判断。
6. 云端/MiMo 安全：避免未确认的重复受理。

## 2. 术语与对象

### 2.1 任务对象

以下命名保持不变，RFC 只定义行为与状态语义，不改变接口 URL：

- `GenerationTask`：单任务，来自 `task_queue.py`。
- `LongformTask`：长文父任务，来自 `longform_queue.py`。
- `LongformSegmentTask`：长文分段子任务，来自 `longform_queue.py`。
- `BatchTask`：批处理父任务，来自 `batch_queue.py`。
- `BatchSegmentResult`：批处理子任务结果项，来自 `batch_queue.py`。
- `TranscriptionTask`：ASR 任务，来自 `asr_tasks.py`。

### 2.2 状态与生命周期相关字段（建议）

- `status`：任务主状态，必须属于统一状态集合。
- `attempt`：该任务实例执行次数（含初始执行）。
- `retry_attempt`：父任务级重试次数。
- `cancel_requested`：取消请求标记。
- `started_at`：任务开始执行时间。
- `updated_at`：状态更新时间。
- `queued_at`：入队时间。
- `result_id`：结果对象标识。
- `error_code` / `error_message`：失败诊断。
- `provider_request_id`：云端调用幂等键。
- `runner_id`：本机执行器实例标记。
- `heartbeat_at`：执行期心跳（用于重启恢复判定）。
- `result_epoch`：结果变更版本（可选，避免并发覆盖）。

## 3. 统一状态语义

统一状态集合：

- `pending`
- `queued`
- `running`
- `postprocessing`
- `success`
- `failed`
- `cancelled`
- `retrying`

### 3.1 状态定义与转移

| 状态 | 进入条件 | 允许从这些状态进入 | 可转换到 | 是否终态 |
| --- | --- | --- | --- | --- |
| `pending` | 创建任务后尚未入队 | `new`（创建） | `queued` | 否 |
| `queued` | 已入队，等待worker消费 | `pending`, `retrying`, `failed`(受控重试), `recovered-running` | `running`, `cancelled` | 否 |
| `running` | worker取得任务并开始执行 | `queued` | `postprocessing`, `failed`, `cancelled`, `retrying` | 否 |
| `postprocessing` | 执行体结束，进入结果落库/包装流程 | `running` | `success`, `failed`, `cancelled`, `retrying` | 否 |
| `retrying` | 判定需重试后进入重试排队阶段 | `failed` | `queued`, `failed` | 否 |
| `success` | 结果持久化成功且可读 | `postprocessing` | 否（除显式人工/管理恢复） | 是 |
| `failed` | 执行失败，写入错误码与消息 | `running`, `postprocessing`, `retrying` | `retrying`, `failed`（最终） | 否（允许显式重试），是（最终） |
| `cancelled` | 取消完成，停止后续处理 | `queued`, `running`, `postprocessing` | 不可回退 | 是 |

备注：

- `failed` 仅在明确重试动作（新建 retry 任务或增加 `retry_attempt`）下可转入 `retrying`；否则保持当前 `failed`，仅按 `retryable` 记录是否可重试。
- `retrying` 不是终态：它表示排队窗口内的“中间态”。
- `success`、`failed`（最终不可重试）、`cancelled` 为终态。

### 3.2 状态优先级（并发写保护）

- 终态写保护优先：`success`、`failed`（最终）、`cancelled` 一旦持久化，不得被后续 `runner`/`worker` 的迟到事件覆盖；仅允许显式人工/管理操作变更终态。
- `cancel_requested` 可阻止后续 `success` 落库；若任务已持久化为 `success`，该标记不能将其改为 `cancelled`。
- `failed` 进入 `retrying` 只允许在显式重试创建或 `retry_attempt` 递增时发生；普通运行事件不得将 `failed` 回退为 `retrying`。
- `progress` 不参与状态优先级计算，只用于展示，不作为并发写保护依据。

补充：
- 重试路径保留已存在错误上下文，不清空历史 `history/result`；重试应产生新上下文写入。

## 4. 服务重启恢复规则（核心）

#### 4.1 一般规则

1. `success`、`failed`（最终）、`cancelled`（终态）不恢复，保持原状态。
2. `pending`、`queued`：恢复为 `queued`，并保留任务顺序记录（时间戳或优先级）。
3. `running`、`postprocessing`、`retrying`：  
   - 若未超过 `engine_timeout + stale_grace`，恢复为 `queued`，并打标签：
     `recovered_after_restart=true`，并写审计日志 `service_restart_requeue`。
   - 否则标记为 `failed`，`error_code=TASK_STALE`，`error_message` 说明超过恢复窗口，不自动重跑。
4. `running`/`postprocessing` 恢复时不得直接转 `running`，避免同时并发两次执行。

#### 4.2 恢复依据字段

恢复判断不能只看 `progress`。必须使用以下字段组合：

- `status`
- `started_at`
- `heartbeat_at` 或 `runner_id`
- `result_id`

`progress` 仅用于前端展示。

#### 4.3 引擎策略配置

恢复窗口需来自 engine policy（见第 5 节），不允许分散硬编码。

### 4.4 失败恢复边界

超过窗口的 `running`/`postprocessing`/`retrying` 一律：

- 写入 `failed`
- `retryable=false`（除非另有明确外部确认）
- 不自动重跑

## 5. 超时与进度

### 5.1 超时来源

超时（`timeout`）统一来自 `engine policy`，按任务类型和执行阶段独立配置，不允许本地散落常量。建议配置项包含：

- `execution_timeout_sec`
- `postprocessing_timeout_sec`
- `stale_grace_sec`
- `retry_backoff_sec[]`（重试退避）

### 5.2 进度字段约束

- `progress` 只作为展示字段，不作为恢复真相源。
- 恢复算法不得基于 `progress` 判断“是否成功/失败/卡死”。
- `result_id` 变化与 `status`/`heartbeat_at`才可作为执行实态依据。

## 6. 入队幂等与去重

### 6.1 同一 task_id 幂等规则

同一 `task_id` 在同一 `live queue` 中最多存在 1 条：

- `pending` / `queued` / `running` / `postprocessing` / `retrying` 期间禁止重复入队；
- `success`、`failed`（最终）、`cancelled` 为终态后，允许新任务 ID 重建同目标任务。

### 6.2 队列集合与生命周期同步

必须维护“队列集合”与“实际队列”的一致性：

1. 入队时，先检查集合中是否存在 `task_id`；
2. 加入集合与队列时需原子化或用幂等补偿；
3. Worker 取出任务后才移除 `queued set`，或使用明确 `inflight` 状态记录；
4. `cancel/complete/retry` 必须同步回写集合与任务状态。

### 6.3 重试任务 ID 与 attempt

- 重试不得复用原 `task_id` 直接覆盖历史结果。
- 两种可接受方案：
  1. 生成新 `task_id`（推荐），并保留 `parent_task_id`/`retry_origin_id`；
  2. 或保留原 `task_id` 但 `retry_attempt` 严格递增，且历史状态/结果只读不可变。  

Phase 1 初始建议采用方案 1。

### 6.4 不可重复窗口

- 任何在 `queued->running` 链路中的任务，必须在 worker claim 点加幂等保护，防止多 worker 同时执行。

## 7. 任务类型行为规格

### 7.1 single generation task（高优先）

路径：`task_queue / GenerationTask`

- 允许状态：全部状态集合。
- 重试条件：网络错误、局部资源失败、超时可重试；输入无效、配额不足不可重试。
- 取消：
  - `queued`：置 `cancelled` 不执行。
  - `running`：设置 `cancel_requested` 并触发 runner 终止。
  - `postprocessing`：终止后续步骤为主，产物保留可见；最终状态以 `cancelled` 为主。

### 7.2 longform（高优先）

路径：`longform_queue / LongformTask / LongformSegmentTask`

- Parent/segment 分离状态管理，segment 结果会回传 parent 汇总。
- Segment 失败策略：
  - parent 成功条件：全部关键 segment success（按产品策略，可将非关键 segment 允许忽略）
  - segment 可重试对象：`failed` / `cancelled` / 非 success；
  - 不允许重试 `success`。
- parent 取消：
  - 传播到所有未完成 segment；
  - 对正在运行 segment 标记 `cancel_requested=true`；
  - 已完成 segment 结果保留。

### 7.3 batch（高优先）

路径：`batch_queue / BatchTask / BatchSegmentResult`

- 批内子任务独立状态，聚合规则在 parent 中记录 `completed/failed/cancelled` 计数。
- `partial` 成功规则：允许 parent 最终为 `failed` 或 `success`，由批处理配置决定（推荐 default: failed-on-any, 可配置 partial_success）。  
- 批任务重试：
  - 仅对失败/取消 segment 执行重新提交；
  - 已成功 segment 不重算，不覆盖既有 audio/result。

### 7.4 ASR（低优先）

路径：`asr_tasks / TranscriptionTask`

- 状态机原则同上，优先级低于 TTS/Longform/Batch；实现阶段可先接入 `pending/queued/running/postprocessing/success/failed/cancelled` 基本链路。
- 建议先复用通用恢复与取消语义，除非转码/时间轴处理需要专用状态。

## 8. 取消语义

### 8.1 统一规则

- `queued` 取消：不执行，直接终态 `cancelled`。
- `running` 取消：设置 `cancel_requested=true`，runner/worker 应尽快退出；如 subprocess，发送终止信号并在超时后强制结束。
- `postprocessing` 取消：不承诺删除已写入产物；必须将 UI 文案与 API 标注为“最终结果可能存在”。

### 8.2 父子任务传播

- `LongformTask`、`BatchTask` cancel 需向未完成子任务传播。
- 传播时子任务状态遵循其当前状态优先级：`queued/cancelled` 直退，`running` 走 `cancel_requested`。
- parent 最终状态以 `cancelled` 为主，除非已有 `success`/`failed` 的确定性更高（按状态优先级处理）。

## 9. 失败与重试

### 9.1 失败信息

每次最终失败必须写入：

- `error_code`（分类错误码）
- `error_message`（人读信息）
- `retryable`（true/false）

### 9.2 可重试/不可重试

- 可重试：超时、瞬时网络、调度冲突、引擎偶发退出码。
- 不可重试：模型参数非法、输入超限、鉴权失败、配额被拒绝且不可恢复。

### 9.3 重试执行约束

- 重试不得清空原始 `history/result`；
- 对 `retrying`/`failed` 的任务可触发补提交，但必须产生新 `task_id` 或 `retry_attempt`。
- 仅重试失败/取消/非成功 segment（longform/batch 里定义为 `failed|cancelled|!=success`）。

## 10. 云端/MiMo 重复计费保护

### 10.1 幂等标识

云端/MiMo 任务提交前必须持有：

- `provider_request_id`（外部可回查的 idempotency key）
- 或本地 `local idempotency marker`（与任务生命周期绑定）

### 10.2 不可确认状态的重放保护

- 当某次提交后服务重启或网络断开导致“提交成功不确定”时，恢复不得盲目重放。
- 允许状态：
  - `failed` 且标记 `retry_required`
  - 或 `waiting_user_confirm`
- 不得直接转入新提交并自动再次调用 provider API。

### 10.3 与 provider 双向确认

- 支持 `provider_request_id` 查询/对账成功则把任务置 `success` 或 `failed`；
- 若仍无法确认，任务必须进入人工确认节点，避免重复计费。

当前实现若不支持 provider 查询或回查 API，应在 RFC 标注为 Phase 1/2 的实现待办项。

## 11. Phase 1 回归测试清单（Spec）

1. 重启恢复：
   - queued 可恢复为 queued
   - running 可恢复为 queued（在窗口内）且写恢复注记
   - postprocessing 可恢复为 queued（在窗口内）
   - retrying 可恢复为 queued
2. stale running 标 failed：
   - 超过 `engine_timeout + stale_grace` 的运行任务不自动重跑，转 failed + `TASK_STALE`。
3. 同一 task 不重复入队：
   - 同 task_id 多次 enqueue 只产生一次 active entry。
4. 取消路径：
   - cancel queued -> cancelled
   - cancel running -> 尽快终止并终态 cancelled
   - cancel postprocessing -> 结束后状态为 cancelled（产物保留）
5. longform parent cancel 传播：
   - 未启动 segment 不执行
   - 运行中 segment 尽快退出
6. segment failed 传播到 parent：
   - longform 执行中有关键 segment failed，parent 按策略终态失败
7. batch 部分失败状态：
   - 部分成功、部分失败下 parent 状态符合配置（默认失败；可配置 partial_success）
8. MiMo/cloud 重复提交防护：
   - 不确定状态下不得自动重复提交 provider request
   - 进入 failed/retry_required 或等待确认

## 12. Phase 2 实施建议

### 12.1 架构建议

- 引入 `TaskOrchestrator` 抽象与共享队列原语（状态机、入队、恢复、取消、重试）。
- 实现 `engine policy` 统一读取与下发，避免分散硬编码。

### 12.2 迁移顺序

1. 先接入 `GenerationTask` 统一语义；
2. 迁移 `Longform`（parent + segment）；
3. 迁移 `Batch`；
4. 最后接入 `ASR`。

### 12.3 兼容性约束

- 不改变外部 API URL。
- 数据库字段尽量复用，必要时兼容新增字段（以最小变更为准）。

## 13. 明确不做事项（Out of Scope）

- 不改 DB schema（除非 Phase 2 明确评审后通过迁移计划）。
- 不做多用户隔离功能。
- 不重写 WebUI。
- 不移动 `~/VoiceStudio`。
- 不重命名 `mlx_indextts`。

## 14. 风险与待实现项（当前实现差异）

- 当前代码中云端计费幂等与恢复回查能力未完全齐备，需在 Phase 2 落地：
  - provider_request_id 持久化与复用；
  - 重启重放决策的判定 API；
  - queued set 与 inflight 的严格一致性机制；
  - cancel_requested 到 runner 的统一退出协议。

## 15. 附录：最小一致性记录要求

- 每次状态转移必须记录：
  - from/to、timestamp、attempt、runner_id（若有）、error_code（失败时）
- 重要恢复事件必须记录审计日志：
  - `service_restart_requeue`
  - `retry_requeued`
  - `cancel_requested`
