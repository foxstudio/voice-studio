# Voice Studio 系统架构

本文是动态维护的架构活文档，只描述当前系统边界、长期目标和仍存在的过渡边界。
强制开发规范以根目录 `AGENTS.md` 为唯一来源；本文只记录这些规范在当前系统中的真实落点，不复制一套新的规则。
兼容过渡存在时，本文会明确写出旧权威、现行权威和退出条件，不把迁移目标描述成当前事实。

## 产品边界

Voice Studio 是一个面向本地和云端语音工作的模块化单体，主要能力包括：

- 单句、长文本和批量语音合成。
- 本地与云端引擎、声音克隆、预置音色和声音设计。
- 音色库、任务队列、生成历史和设置。
- ASR、说话人区分、字幕整理和视频本土化工作台。
- WebUI、REST/OpenAPI 和供自动化工具或 Agent 使用的公共能力。

它不是多个互不相关的小应用。相同能力在页面、API、后台任务和 Agent 调用中必须共用同一实现。

## 架构原则

1. 单一能力、单一实现、多个适配入口。
2. 领域规则与外部引擎、存储、UI 解耦。
3. 原子子任务拥有版本化输入输出，可独立调用和测试。
4. 工作流只表达依赖、并行、汇合、失败和降级，不复制子任务算法。
5. 状态所有权明确，页面不创建后端业务状态的第二份真相。
6. 向后兼容放在边界适配层，不污染新领域模型。

## 分层与依赖方向

```text
WebUI / REST / Agent
        │
        ▼
API 与前端适配层
        │
        ▼
应用服务 / 工作流 / 后台任务
        │
        ▼
领域门面、实体、原子子任务、版本化契约
        │
        ├──────────► 引擎 / Provider 适配
        │
        └──────────► Store / 文件与媒体适配
```

上层可以依赖下层公开接口，下层不得反向依赖页面或 API。第三方差异不得穿透到领域和前端。

生产源码应用使用单进程同源拓扑：SvelteKit 通过 static adapter 生成
`frontend/build`，FastAPI 在所有 `/api` 路由之后安装只读 SPA 分发边界。未知的页面
路由回退到 `index.html`，未知 API 和缺失静态资源保持 404；指纹资源使用长期不可变
缓存，SPA 入口不缓存。该边界仅在 `VOICE_STUDIO_SERVE_FRONTEND=1` 时启用，普通
`start.sh` 仍保留 Vite + FastAPI 双进程开发方式。

普通 TTS、批量与长文本队列仍是单应用进程所有者。FastAPI lifespan 在启动任何队列前
对当前 `voice_studio.db.backend.lock` 获取跨平台非阻塞独占锁，并持有到全部 worker
关闭之后；同一数据库的第二个后端会直接拒绝启动，避免重复恢复任务或重复提交云端调用。
不同数据根的开发/测试实例使用不同锁，互不影响。该单实例保护是当前可靠运行边界，
不是持久 claim/lease 调度的替代品。

## 目录职责

| 目录 | 主要职责 |
| --- | --- |
| `backend/app/api` | HTTP 传输、请求响应、错误和下载格式 |
| `backend/app/domains` | 领域规则、稳定门面、原子能力和工作流结果 |
| `backend/app/services` | 跨领域应用服务、共享任务和引擎运行时 |
| `backend/app/schemas` | 新代码使用的公共 Schema 导入入口 |
| `backend/app/errors` | 新代码使用的应用错误导入入口 |
| `backend/app/models` | 1.x 兼容期的 Schema 与异常实现源；不得继续扩展成新的公共入口 |
| `frontend/src/lib` | API 客户端、共享状态、通用组件和工具 |
| `frontend/src/routes` | 页面编排和页面级交互 |
| `tests` | 后端契约、领域行为、兼容和集成回归 |

大型旧文件允许渐进拆分，但新逻辑必须先放到正确边界，不能继续扩大“万能页面”或“万能 service”。

## 当前产品能力地图

下表覆盖当前 WebUI 的全部一级页面。它用于让后续开发先找到真实入口和状态所有者；“过渡中”表示当前仍由多个既有服务协作，不能据此虚构一个尚不存在的统一类。

| 产品能力 | 页面 | 后端入口与当前所有者 | 状态与公共契约 | 当前边界 |
| --- | --- | --- | --- | --- |
| 单句语音合成 | `/generate` | `api/generate.py` → `services/task_queue.py`、`engine_request_builder.py` | 生成任务、历史记录和音频文件；REST/OpenAPI | 任务执行仍集中在大型队列服务，过渡中 |
| 长文本与批量 | `/script-studio` | `api/longform.py`、`api/batches.py` → `longform_queue.py`、`batch_queue.py` | 长文父子任务、批次与分段结果；REST/OpenAPI | 两类队列保留各自语义，统一状态规范仍在渐进实施 |
| 音色库 | `/voice-library` | `api/voices.py` → `voice_store.py`、云端音色适配器 | 音色、参考音频和云端绑定；REST/OpenAPI + 数据库/文件 | 本地音色与供应商绑定由同一页面组合，Provider 差异留在后端 |
| 引擎管理 | `/engine-hub` | `api/engines.py` → `engine_registry.py`、`model_catalog.py`、engine policy/provider 模块 | 引擎能力、健康状态、模型来源/下载状态和参数 Schema；REST/OpenAPI | 页面按运行引擎关联模型清单，同一能力只显示一张卡；未关联的辅助模型与参考版本继续可见，来源和文件位置收进共用详情；注册表仍承担部分兼容职责，Provider 拆分过渡中 |
| 任务与生成历史 | 侧栏、`/generate` | `api/tasks.py`、`api/history.py` → `task_queue.py`、`history_store.py` | 运行状态、取消/重试、生成记录和波形；REST/OpenAPI | 页面只消费任务与历史，不自行创建第二套任务状态 |
| 音频工具与语音转写 | `/audio-tools` | `api/audio_tools.py`、`api/asr.py` → `audio_tools.py`、`asr_service.py`、`asr_tasks.py` | 音频处理和转写任务；REST/OpenAPI | 通用 ASR 与视频本土化 ASR 共享底层 Provider，但拥有不同用例编排 |
| 质量评测 | `/eval-reference` | `api/evaluations.py` → `asr_service.py`、`text_verifier.py`、`history_store.py` | 评测请求、材料与结果；REST/OpenAPI | 当前是跨服务应用用例，尚无独立领域包 |
| 视频本土化 | `/video-localization` | `api/video_localization.py` → `services/video_localization_operations.py`、`services/video_localization_exports.py`、`services/video_localization_tts_handoff.py`、`domains/video_localization/service.py` 与领域门面 | 项目草稿、媒体、字幕、说话人、任务和导出；版本化领域契约 + REST/OpenAPI | operation 命令与 TTS 跨状态源回写已有独立应用端口；TTS 注册/放轨/终态使用 durable outbox、跨进程 lease/fencing 和启动重放；共享队列不反向导入本土化 service |
| 设置 | `/settings` | `api/settings.py` → `settings_store.py`、`llm_runtime.py` 及拆分后的设置/Provider 模块 | 配置、密钥引用、目录、连接状态、默认 TTS/ASR 与默认大模型；typed REST/OpenAPI + 设置存储 | 设置页只选择默认引擎并链接到引擎管理，不复制模型下载和来源管理；其余设置服务仍较大，设置系统重构尚未实施 |

引擎音频试听完成后，由 `task_queue.record_completed_audio_diagnosis` 在同一事务写入任务与历史；每次使用独立音频文件，来源标记为 `engine_diagnosis`。试听响应返回稳定的历史音频地址，合成工作台复用现有查询、播放、下载与删除入口。试听不触发自动转写校对，用户仍可在历史中手动校对。旧的最近试听地址保留读取兼容，历史固定文件不伪造为新任务。

单条生成音频的下载序号由历史下载服务在确认文件存在后原子分配，
`download_counters` 保存跨模型、跨日期的全局累计值。任务生成、页面读取、试听和波形加载不分配序号。

## 公共调用方式

WebUI、REST/OpenAPI、后台队列和外部 Agent 共享同一能力链：

```text
调用方 → typed request → 应用服务/领域门面 → versioned result
```

- UI 只能通过 API 或明确的前端适配层使用能力。
- API 路由不包含业务算法。
- 后台队列负责任务生命周期，不拥有领域算法。
- 外部 Agent 通过 OpenAPI 可见的 typed schema 和说明调用，不传任意本地路径驱动内部实现。

### 共享大模型运行时与 Provider

翻译、校对、结构化 JSON 和关键帧识别等业务模块统一调用
`services/llm_runtime.py` 的文字或多模态门面。运行时根据默认大模型配置把同一
请求分发给 OpenAI Compatible HTTP Provider 或本机 Codex CLI Provider；
领域子任务、提示词和版本化输入输出契约不因 Provider 不同而复制。

本机 Codex Provider 只调用 CLI 公开命令并复用 CLI 自己维护的 ChatGPT
登录，不读取认证文件、令牌或浏览器 Cookie，也不接受 API Key。文字请求在
只读、临时、无持久会话的执行环境中运行；图片仅作为临时输入文件传给同一
多模态门面。登录、退出、换号、状态和订阅测试接口只允许本机访问，设置页
继续使用既有的“测试通过后设为默认”规则。

同一服务进程内的本机 Codex Provider 允许两个模型调用并行，超过上限的调用在共享 Provider
边界排队，轮到调用后才开始计算该次模型执行超时。工作流保持无数据依赖节点
的并行语义，也不会因为并发上限暂时被占用而返回“忙碌”并失败。
该限制不是跨进程或全账号调度。CLI 超时仍按失败返回，只附带白名单事件计数、
已观察终态、回复是否出现和已有错误分类；不保留原文、思考内容或凭据，不因观察到
部分输出就判定成功或自动重发。没有完成事件不能单独证明模型慢或网络故障。

每个语言模型配置同时保存模型 ID 和思考深度。模型 ID 留空表示使用 Provider
默认模型；思考深度可跟随模型默认或显式选择 `low`、`high`、`max`。运行时
在每次文字或多模态调用前读取该配置，领域代码不得写死某个模型或思考深度。
OpenAI Compatible HTTP 门面只对明确拒绝执行的 `429` 做有界退避；`5xx` 不自动重放，
响应读取中断或远端断开返回 `llm_result_unknown`。这避免单次调用栈在没有 provider
幂等键时重复提交付费 POST；step ledger、request ID 持久化和人工裁决尚未完成。
正式工作流、开发单步、批次重放、REST/OpenAPI 和外部 Agent 共享这条解析链。
OpenAI Compatible 配置从 Provider 的 `/models` 获取目录；本机 Codex 配置
通过只允许回环访问的 Codex app-server `model/list` 获取当前 ChatGPT 账号
可见模型。模型目录读取不生成内容，前端仍允许手动填写，以兼容新模型和私有模型。
配置只有在目标模型真实返回约定内容后才标记为已验证并允许设为默认；测试响应
记录实际执行模型 ID，不能用登录状态、端口连通或模型目录读取代替模型回复验证。

### 云端语音生成与人工重放

通用单句和长文本 TTS 对 MiMo、豆包等云端生成使用保守恢复语义。进入 Provider
提交窗口前，任务先持久化 `provider_state_uncertain=true`；豆包 TTS 同时先持久化
`provider_request_id`，再把同一值作为 `X-Api-Request-Id` 提交。成功产物和历史记录
落库后才清除 uncertain。

服务重启、stale reconcile、长文本等待超时或响应结果不明确时，云端任务转为失败，
不自动重新排队；单句和长文本重试 API 都要求 `confirm_cloud_replay=true`，WebUI
在提交前明确提示可能再次计费。云端批量队列按 segment 建立 durable attempt：每个
segment 在 Provider 调用前先提交 attempt number、输入 fingerprint、状态与 Provider
request ID（若该 Provider 支持）。成功、明确失败和结果不明分别收口为
`success/failed/uncertain`；重启只继续从未进入提交窗口的 segment，绝不自动重放
attempted/uncertain segment，显式重试仍排除已有成功产物。MiMo 的本地内容 fingerprint
只用于识别输入，不是 Provider 幂等键；豆包 request ID 按 Provider 契约每次 attempt
生成唯一值，也不能当作跨 attempt 去重键。人工裁决 UI 仍未建立，但逐 segment
提交边界、恢复语义与审计身份已经持久化。

视频本土化绑定的单句和长文本任务通过
`TtsHandoffApplicationService` 协调共享 task/history 与项目 Draft 投影。应用组合根
注入具体项目投影函数；`task_queue`、`longform_queue` 和通用 generate API 只依赖该
应用边界，不直接导入视频本土化领域 facade。绑定身份、generation ID、项目、字幕、
cue 和历史记录不一致时拒绝放轨。

跳转生成页先保存版本化、一次性的客户端 handoff intent 并立即完成路由切换；生成页
再异步调用无副作用的 handoff preview 填充参考音，不让媒体探测、裁切或项目大文档保存
阻塞页面打开。页面内直接复用参数生成时，客户端先生成稳定的 submission ID，并通过正式
handoff reserve 在任何参考音裁切或 Provider 提交前登记权威 TTS workflow；页面收到这次
快速确认后立即投影“准备中”片段，再用同一个 ID 进入 handoff prepare 和 `/generate`。
准备参考音失败、提交校验失败或
入队失败都会把这个已登记 workflow 收口为 failed 并保留为可删除的任务事实，页面不得
以纯前端临时片段代替它。没有 generation task 的 prepared workflow 超过两分钟会在
专用任务读取时标记为提交中断，不自动触发 Provider。任务执行中的进度
百分比写库是非关键观测，SQLite 短暂冲突只记录 warning，不得终止推理子进程。
页面按项目只维持一个 TTS workflow 增量轮询器。规范化
`video_localization_tts_workflows` 是任务面板读模型，保存 Draft 时同事务同步工作流事实，
共享 task 状态变化时同事务只推进命中的 workflow row revision；feed 只返回新增、变化、
删除的 ID 和对应 runtime，不重新传输未变化任务，也不反序列化或改写完整本土化 Draft。
前端的持久时间线 Draft 与运行时片段投影也保持单向边界：`TimelineEditController`、撤销历史、
车道压缩和保存意图只接收可持久片段；TTS 初始化、workflow 进度和历史记录放轨中的临时片段
由独立运行时集合持有，只在时间线/预览渲染边界与权威任务、权威时间线合成。工作区刷新、
放轨结果和保存响应不得把这些临时片段反向写入持久 Draft，也不得据此产生删除 tombstone。
交互剪辑通过有版本的时间线命令提交片段、字幕和轨道修改。编辑保存只校验本次命令的
身份、范围与并发状态，不运行全项目质量检查或重排其它片段；手动编辑的精确时间区间
在后续读取中保持不变。旧片段的兼容规范化与生成流程仍由共享时间转换模块负责。
页面投影、碰撞判断、播放和导出不得各自改写已保存的剪辑范围。句内停顿用音频切片
之间的时间线间隔表达，不能通过拉长一个不可播放的音频片段占位。
生成成功后的放轨只提交一次最终原子写入，不先保存“正在放轨”的中间 Draft；这样任务
进度刷新不会与真实配音片段写入争抢整份大项目文档。

落轨完成事实与可编辑片段分离：自动投递读取已有 workflow placement 成功记录，
历史拖入/替换使用一次操作的请求回执；重复投递不重建已删除片段或恢复原始裁剪。
内部 Draft 保留读取时的 Project 版本，保存不得借用后来读到的新版本覆盖旧草稿；
局部响应也必须携带自身数据所属版本，前端不接纳落后于已知版本的回执。

`video_localization_tts_handoff_outbox` 保存三个版本化内部事件：
`task_registration`、`result_placement` 和 `workflow_terminal`。注册事件与 task 或
longform 父任务同事务提交；放轨事件与 history 同事务提交；失败/取消终态事件与
longform 父任务同事务提交。请求内先做一次同步投影，失败事件保持 pending；应用启动
会有界重放。消费者按 task/workflow 身份幂等，applied 行保留为去重事实；项目 reset
在同一事务中把 pending 行退役，delete 在同一事务删除项目事件，旧历史不会在清空后
重新放轨。正常注册失败会删除尚未入队的源任务并把事件标为 abandoned；若进程在原子
提交后、投影前退出，启动重放会完成注册。放轨事件冻结提交时的 plan revision、group
与目标字幕 ID，恢复时不从当前项目猜测；缺少该血缘的旧事件在计划模式下只会安全拒绝，
不能绕过当前计划或被误当成当前结果。

Agent 驱动的配音生产使用独立的版本化
`DubbingProductionApplicationService`。它是 Web、HTTP/OpenAPI、共享 TTS worker
和未来薄 CLI 的唯一公共门面，拥有确定性项目快照、生成计划、当前片段的逐词/VAD 气口证据和原子放轨。生成结果在媒体和历史记录
提交后通过 durable handoff 进入采用流程；实际音频先做无台词提示的独立转写，再进入逐词/VAD 气口处理和原子放轨。
内容证据按音频字节、识别引擎与协议复用；当前目标台词和参考台词始终重新比较，强制对齐不证明实际说了什么。
明确错漏或参考台词泄漏不能覆盖旧结果；检查暂不可用保留候选等待恢复，不触发重新生成。
单组和连续执行共享一次原参数整组重试；仍不合格时返回需要语义拆分或参考音决策的组失败，不假称全部恢复方案已尝试。
这里不增加内容审批标签或全轨审计。来源 revision 覆盖规划读取的逐字时间与声学边界；每次计划提交获得单调递增的
plan revision，生成任务在提交时冻结 plan/group，旧任务不能向重新规划后的状态回填。
整片生产使用有界前瞻窗口，默认同时推进两个、最多三个相邻语义组。OmniVoice 的单一
Provider worker 仍串行执行付费生成；并行只发生在“下一组生成”和“上一组本地逐词/VAD、
逐词气口裁切、放轨与门禁提交”之间。执行请求可冻结精确的起止 group、并发上限和
常规语速基线，以上约束随任务、重试及连续推进持久传递；范围外片段不参与恢复、重排
或写入，常规语速只在冻结基线前后 0.05 内变化，原声语速证据不能自行授权越界。候选收尾会把逐词
对齐证明安全的首尾空白实际裁到小幅保护余量后再提交时间线门禁，临时放轨片段不能被
误认为已经完成人工剪辑。
重新规划会在同一次 Project CAS 中取消仍在执行的旧 workflow，并把 workflow/task 身份写入
丢弃集合；worker 放轨也必须先核对当前 plan revision 和 group，不能只依赖相同的 group ID。
显式重做一组或一段范围时同样保留生成历史，但会把所选组的旧 workflow、task 和 result
身份写入丢弃集合，生产进度不得再次认领这些旧候选。恢复当前组时会依次尝试仍有效的候选，
只有全部无法安全落位才记失败；失败事实写入前必须先终结对应 workflow，不能让后续组被假运行状态卡住。
worker 同时冻结候选音频 SHA-256；逐词/VAD 证据只用于当前片段气口裁切和投影一致性。
生成失败保存最小后端失败事实并继续后续独立组，不自动重试或切换引擎。
完整 Draft 兼容写不能覆盖该后端状态或候选/片段 CQC 镜像；重新规划只保留来源、整组输入、音频字节及当前裁切投影全部未变的已完成证据，其他旧报告和冻结输入失效。
缺少历史证据时不补造通过记录，时间线只认可匹配的冻结输入与权威报告。生产执行不调用 `timeline` 或 `delivery` 审计；
配音字幕工作流改用自身的当前可听片段、音频字节、文字、时间和提交时来源未变化校验；
dub 音频导出依赖渲染器的媒体可读性、时间/裁切和成品校验。进入计划模式仍是持久且单向的，
源内容变化会清空旧计划和当前片段证据，但不会锁住已有媒体和字幕导出。
旧项目在迁移期保持只读兼容，
不伪造历史 CQC；它可以读取和导出已有旧结果，但提交新 TTS 或接收旧任务放轨前必须先建立
当前版本计划。本土化专用旧 batch 提交/回填接口不再暴露；历史 batch 字段只读，通用
批量 TTS 仍是独立能力，不能绕过 plan-bound handoff 写入本土化时间线。组合媒体和单独配音 SRT 的最终校验/发布同时持有进程内 Draft 锁与 SQLite
`BEGIN IMMEDIATE` 发布边界，因此独立 Web/worker 进程不能在最后一次重读和发布之间改写
Project；导出指纹排除 CQC/计划/审计元数据，但保留实际媒体字节、时间、裁切和字幕投影。

显式的既有正式结果核对命令是这条只读兼容边界上的唯一认领入口。它只接受主配音轨中
对当前语义组形成唯一、完整字幕覆盖的候选，并同时验证不可变生成历史、目标字幕集合、
规范化台词、历史 `passed` 编辑门和当前片段投影指纹；全部相同才保存当前计划的来源证明。
命令不重新生成、不移动、不裁切也不删除片段。只有音频但缺少完整证明的组投影为待复核，
不得回到待生成；片段位置或裁切一旦变化，来源证明因投影指纹不匹配而自动失效。

当前交付语义仍是 at-least-once 投影，不是 exactly-once。每个 pending 事件通过
SQLite `BEGIN IMMEDIATE` 领取 60 秒租约并递增 fencing token；目标 Project 写在同一
事务内复核 event/project/runner/token/expiry，租约过期或被接管的旧进程不能提交。
标记 applied/failed 也要求同一 live claim。投影期间每 10 秒续租，heartbeat 只延长
仍由同一 runner/token 持有且尚未过期的 claim，不能复活旧 claim；并发 runner、过期
接管、旧 token 拒写、慢投影续租和子进程领取后直接退出的恢复有固定数据回归。启动恢复
在后台线程执行，不阻塞 API 就绪；自动重放连续失败 5 次的事件转为 abandoned，避免失效
事件在每次启动时无限重试。
完整服务进程强杀以及长期 outbox retention/压缩仍属于后续门禁。

## 原子任务与工作流

原子任务至少定义：

- 稳定 ID 和大白话名称。
- 输入、输出和契约版本。
- 依赖任务和执行方式：`parallel`、`serial` 或 `join`。
- 状态、警告、失败和降级语义。
- 普通结果摘要、质量提醒和默认折叠的高级调试信息。

没有数据依赖的任务并行执行；汇合是正式原子任务，需要校验项目、音轨、指纹和上游契约。

### ASR 当前公开门面

`backend/app/domains/video_localization/asr_pipeline.py` 中的 `AsrPipeline` 是 ASR 子任务的统一领域入口。

通用转写消费者先通过 `/api/asr/tasks` 上传音频并轮询结果；需要严格字幕时间证据时，
再调用 `/api/asr/{transcription_id}/subtitle-evidence`。该接口只解析服务端已经受管保留的
音频，可接收调用方已经锁定的分段文字，并通过同一个 `AsrPipeline` 返回不含本地路径的
真实逐字时间、停顿和字幕入点。它不执行全文校对，也不决定最终字幕文字；项目规则继续
拥有最终文案与断句权威。外部 Skill 和项目脚本不得为复用这些能力导入 Voice Studio
内部 Python 模块。支持联合转写和说话人分析的 Provider 可以在通用转写片段中返回可选
`speaker_id` 与 `confidence`；公共任务和历史记录必须保留这份声学证据。

合成配音字幕使用独立的 `dub_subtitle_generation` operation。服务端选择所有有内容且
未静音的 dub lane（忽略 solo），按时间线渲染成一条任务级临时音轨，只调用一次
中文 raw ASR，再使用生成配音时冻结的本土化台词执行本地强制对齐和字幕断句；受管
配音的人物归属复用 cue 元数据，不重新做声纹识别。
结果原子写入独立 `draft.dub_subtitles`，不会覆盖源 ASR、本土化字幕或触发 TTS
失效。版本化输入只保存音频指纹、裁切/时间、必要参考文字与人物归属，不保存本地
路径；任务提交参数只允许选择 ASR engine。所有有内容 lane 均静音时任务以明确错误
结束，运行期间配音、静音状态或参考文字变化会由 source fingerprint CAS 拒绝旧结果；
成功后上述来源再次变化会清空派生轨。整轨 ASR 无文字时任务明确失败，不写入空结果。
ASR 只承担声音验证和校时辅助；最终字幕文字以本土化台词为准，所有同音字、漏字、
专名和标点差异作为有界调试记录保存，不作为正常质量警告。临时音轨在任务结束后删除，
不进入项目正式数据或跨任务缓存。多个未静音 lane 同时说话时，播放器按 lane 稳定显示多行。
跨相邻 ASR 计算块的有据校正使用有界合并块重新归属；共享同一字词声学区间的相邻语义片段
保持为一条字幕，不制造假时间。若整轨 ASR 只漏掉个别正式片段，提交节点仅在该候选冻结
CQC 证明逐 token 完整匹配、无额外文字且波形存在正时长发声时，按其真实发声区间补齐并
记录分组人工复核状态；证据不足时不从参考稿猜测。
源 ASR、本土化字幕和合成配音字幕复用同一上屏数字投影：只把有明确数值语境的中文
年份、世纪、时长、数量级、序数、度量和拉丁产品版本转成阿拉伯数字，并在数字与中文
相邻处保留空格；成语、模糊数量和语法性短语保持中文。合成配音字幕发生自动转换时把
`display:number-form-normalized` 留作提示型 CQC 证据，不触发 TTS 重生，也不阻断字幕或导出。
源 ASR / 本土化字幕与合成配音字幕共用一个字幕显示入点校准能力：FA 首字之后只按项目
真实帧率搜索 `1–3` 帧内首个稳定短时能量峰；没有可靠峰或帧率未知时保留保守回退，
不得用该显示规则移动 TTS 音频或维护第二套固定偏移逻辑。
三类字幕也共用一个字幕显示出点能力：入点保持不变，声学结束后默认保留 `500ms`，
空间允许时总显示时长不少于 `20` 帧；同轨下一条字幕前至少留 `2` 帧，并受媒体结尾
约束。多条配音 lane 只按共享 lane 截断。该尾留属于字幕显示策略，不移动时间线音频。

ASR 运行时依赖必须是 DAG。编排层使用独立的 `review_warning_policy.py` 过滤
reader callout，不得为复用一个展示规则反向导入 `section_review.py`；原子步骤之间的
共享数据继续通过 `review_contracts.py`。名称归一原子服务通过
`entity_normalization_runtime.py` 组合仍在迁移期的 canonical-name 算法，不静态反向
依赖 `asr_flow.py`。领域包根保留历史公共 helper 的惰性导出，导入任一子模块不会
预加载 quality/readiness 全链路。

正式 ASR 的第一父级阶段固定为：

```text
生成原始听写 → 理解与校对全文 → 时间与字幕整理
```

- `生成原始听写` 输出 `asr-raw-v2`。声学识别的名称提示仅来自用户显式词表；文件名和括号中的媒体标识只作研究上下文，不注入识别词汇。提示只帮助拼写，不强制音频中不存在的词。
- 语言复核 Provider 的结果不确定时不自动重复付费请求，也不采纳无法确认的模型修改；正式流程保留原始 ASR 与显式术语表修改，使用同一本地文字质量门记录复听提醒。只有文字为空、时间结构损坏或本地不变量失败才阻断校时，普通模型中断不再制造“声明继续但实际停止”的假降级。
- 复核降级保留同次执行已经完成的步骤、耗时和查证结果，并将中断节点收口为明确提醒；这些执行事实不代表未完成的文字审核通过。ASR 最终提交同时校验源媒体与开始执行时的原字幕/转写对象；运行期间的人工字幕修改不会被旧结果覆盖，缩放等无关工作台变化不阻断保存。
- 正式请求默认把原始听写和匿名说话人分析作为两条并行分支，再由独立任务汇合。检测到至少两个可用声音角色时才给字幕增加匿名归属；单一稳定声音不增加冗余标签，说话人模型不可用或普通失败只产生任务级提醒，不阻断主转写，也不把整条字幕轨标成待分配人物。匿名分组不提高文字准确率，也不推断真实角色名。没有可用文字、时间顺序不可用或只有不可验证的合成时间戳时才失败。
- MOSS 长片说话人区分按十五分钟核心窗口执行，窗口前后各读取两秒上下文并按片段中点确定唯一归属；每个窗口的原始匿名标签先隔离命名，再由 CAM++ 跨窗口合并。缺少足够干净参考音的短标签只保留为待复核独立分组，不能阻断其他有证据标签的合并。
- Qwen 长音频按约 30 秒切块。自动语言返回空结果时只为该块尝试英语恢复；每块生成量有明确上限，且明显超过人类语速的输出作为 `implausible_output` 未完成范围保存，不能把模型重复循环伪装成完整覆盖。
- 个别 ASR 音频分块没有返回文字或有效时间戳时，其余有效片段继续进入本土化；`VideoLocalizationTranscriptionState` 持久化 `raw_asr_warning_codes` 和 typed `raw_asr_incomplete_ranges`，刷新和历史详情不能丢失提醒。
- standalone `speaker-diarization-workflow-v1` 与显式的 `asr-initial-analysis-development-workflow-v1` 继续输出 `speaker-diarization-v1` 和 `asr-joined-transcript-v1`，只用于需要匿名人物分组或开发诊断的场景，不是正式字幕的默认依赖。
- 正式流程、开发断点和结果查询均复用 `AsrPipeline`，开发快照不进入正式数据结构。
- 正式 ASR 与本土化字幕共用纯终态投影，在服务保存字幕的同一 Project/SQLite 事务中写入 operation 成功及其查询投影；取消、执行租约和并发编辑检查仍先于提交。提交后的进度或快照通知失败只保留诊断，不把已保存成果反写为失败。采用既有单库事务边界，不新增工作流服务；设计依据见 [SQLite 原子提交说明](https://www.sqlite.org/atomiccommit.html)。

第二父级“理解与校对全文”的第一个原子任务是 `understand_document`：

- 输入契约为 `asr-document-understanding-input-v1`；正式 v2 上游是 `asr-raw-v2`，显式初始分析开发流程仍可使用 `asr-joined-transcript-v1`。
- 输出契约为 `asr-document-understanding-v1`，只生成全文摘要、内容脉络、说话风格、待核对候选和连续复查区块；不联网查询、不修改听写文本，也不写入字幕草稿。
- 输入、输出及其嵌套模型由 data-only
  `document_understanding_contracts.py` 所有；`asr_pipeline.py` 只负责执行。
  研究、画面、分段和全文复查模块直接依赖契约，不反向依赖
  ASR 总管线。
- 可持久化的模型调用记录由无 Provider 依赖的 `llm_contracts.py` 所有；
  `llm_observability.py` 只负责把运行时 trace 采集、汇总和投影到读者/调试视图。
- 开发单步只接受同项目已完成的 managed 初始分析任务；detail core、prepared artifact、逐次模型调用和 final manifest 共同组成受管权威。正式流程通过 `AsrPipeline.run_document_understanding` 调用同一领域实现。
- 所有 ASR 开发节点由 same-snapshot reader 校验 ledger/core/step/artifact 权威，不读取外部桌面快照。

`understand_document` 后面是独立的 `visual_evidence` 画面取证任务：

- 输入 `asr-visual-evidence-input-v1` 不暴露本地视频路径；应用服务按项目解析源视频并校验指纹。
- 输出 `asr-visual-evidence-v1` 只包含截图清单、直接可见文字、观察、搜索词和限制；不根据长相识别人、不决定规范名称、不修改听写或字幕。
- 电影、剧集、动画或游戏中若出现与全文主要文字体系不同的孤立对白，全文理解必须把它标为语言异常，并要求画面取证逐字抄录同一时间附近的原片字幕。画面字幕是剧情含义证据；不能用模型自行翻译音频冒充截图证据，也不能未经声学对齐直接替换源语言听写。
- 第一轮只检查 1 张关键帧；只有模型明确需要更多画面、没有读到目标信息或可信度低于 75% 时，第二轮才补充最多 3 张画面。特殊语言字幕问题会先收窄到对应台词区间并优先执行，补看点只落在该区间内，避免全局截图额度先被名称或场景问题占满。最多两轮，受单问题与全任务截图预算共同限制。
- 多张图片分别传给多模态模型，不默认拼成缩略图，避免字幕条和小字因拼图降清晰度。
- 无需看画面或视觉模型不可用时安全降级，后续资料查询继续走纯文字路径。
- 正式流程和开发单步统一调用 `AsrPipeline.run_visual_evidence`；开发单步以 managed frame/extraction/call/final artifact 为权威。

它后面的 `research` 是独立的资料查询原子任务：

- 纯文字输入为 `asr-research-evidence-input-v1`；带画面线索时使用 `asr-research-evidence-input-v2` 和 `asr-research-evidence-v2`。
- 查询候选只来自全文理解结果。画面线索必须绑定已有候选，只能补充查询关键词，不能直接成为网页证据。
- 每轮查询后由语言模型判断资料是否够下一步使用；只有存在明确缺口和新的安全查询时才继续。最多 3 轮、最多 12 次查询，重复查询、没有新增方向、模型不可用或达到上限都会停止。
- 搜索提供商、缓存、暂时错误重试、来源回退和相关性过滤由 `web_research.py` 统一提供；正式流程和开发单步都通过 `AsrPipeline.run_research_evidence` 使用同一实现。
- 新开发单步使用 `asr-research-evidence-development-workflow-v1`：detail core 锁定 managed 全文理解、可选 managed 画面证据、查询预算、模型/搜索配置和行为指纹；prepared/final 为本地步骤，每次搜索、查询改写和证据判断为独立 Provider 步骤。Tavily 未知结果禁止自动重发，免费搜索的暂时失败使用独立稳定 attempt 重试。
- 最终 same-snapshot reader 核对全部 search/call manifest、artifact bytes/schema/fingerprint、候选完备性、预算和证据来源；任务不读取 Project JSON、桌面研究快照或七天搜索缓存。
- 本步骤只收集证据，不确认人物真实身份、不决定规范名称、不替换文字。名称归一和文字修改属于后续独立原子任务。

`normalize_entities` 只统一有证据支持的专有名称：

- 模型负责提出规范名称与听写别名，本地规则负责最终安全应用；两者不能绕过同一领域入口各自改字幕。
- 新开发单步使用 `asr-entity-normalization-development-workflow-v1`：提交只接受 managed research final artifact，并把完整词表快照、实际模型配置与行为指纹锁入 detail core。prepare/final 是本地步骤，规范名判断与变体映射的每个实际模型 attempt 是独立 Provider 步骤；提交后修改 Draft 词表不会改变已排队任务。
- same-snapshot reader 会重建锁定输入并核对 research lineage、词表 fingerprint、片段 ID/顺序/时间/原始文本、证据引用、替换安全和全部 call artifact manifest。新任务不读取桌面 entity snapshot；远程未知结果禁止自动重发，空证据或只需词表时零模型调用。
- v2 输入会保留画面任务 ID，并把资料查询阶段已绑定的提示确定性投影为 `visual-name-evidence-v1`。每条证据只连接一个原听写专名、一个画面中完整出现的规范拼写、真实源片段和保存帧；低可信、无截图、数字冲突、无保守名称关联或一对多歧义全部拒绝。模型只能原样选择证据中的完整拼写，并返回同一条绑定听写变体；画面证据不能伪装成网页来源或跨候选项借用。
- `entity_variant_safety.py` 会拒绝把价格、百分比、参数量、倍数和其他事实数值当成名称别名，也拒绝自动改写冲突的型号版本号。
- 被拒绝的名称建议保留原文并进入后续复查，不得锁定错误修改或覆盖原始事实。

`normalize_entities` 后面是独立的 `section_review_r1` 分段复查任务：

- 输入 `asr-section-review-input-v3` 显式组合名称归一后的完整转写和复查计划，并校验音轨、音频指纹、片段 ID 和时间范围一致；第一轮覆盖全文，第二轮只允许有序且不重叠的目标范围，并最多携带 8 段定点重听候选。
- 新开发单步使用 `asr-section-review-development-workflow-v1`：提交只接受成功的 managed entity-normalization 与 document-understanding final artifact，并在一个只读数据库快照中核对它们的 research→document 血缘。detail core 锁定两个 final fingerprint、实际模型配置和行为指纹；prepare 从名称统一输入中复用提交时词表，不读取当前 Draft。
- 各区块并行，但每个 section/attempt 是独立 typed Provider step。成功响应先提交 call artifact；已知失败成为可重放的 failed step并保留当前区块的确定性问题；远程未知结果进入 `result_unknown` 并阻断自动重发。final manifest 必须完整引用所有成功或已知失败尝试。
- same-snapshot reader 会重建两份锁定上游和完整复查输入，核对 prepare/final、全部 Provider step、成功 call artifact、section run、issue/patch/evidence 引用与可重算质量指标；任务不读取桌面 section snapshot。
- 输出 `asr-section-review-v4` 只列出可能的听写问题、可信度、证据引用和失败区块；不接受修改、不改变字幕文字或时间码。一个疑点可以定位单片段，也可以定位两个严格相邻片段，并携带每段唯一锚定的修改补丁。
- 高把握的跨段问句衔接和小数拆分由 `transcript_boundary_issues.py` 的纯规则统一发现；模型仍检查其他语义衔接。相邻边界由左片段所在核心区块唯一负责，右片段可以是下一复查区块的第一条只读上下文。
- 各区块最多携带前后 6 个片段作为只读上下文，并行检查；单个区块失败时保留其他区块结果，不能把失败解释为“没有问题”。

`section_review_r1` 后面是独立的 `review_decisions_r1` 修改裁决任务：

- 输入 `asr-review-decisions-input-v4` 只接受上一任务明确给出的疑点、紧凑全文上下文、已有锁定修改、定点重听候选和必要证据；输出 `asr-review-decisions-v4`，包含逐条结论、实际修改、累计锁定修改、警告和完整更新后转写。
- 语言模型只能在当前文本和上一步给出的替换候选之间选择更可能接近原话的一项，不能新增疑点，也不能发明其他替换文字；流程不等待人工裁决。
- 真正应用修改由本地确定性规则负责：候选必须比当前文本更可能正确、原文定位唯一、数字和否定关系不变、名称修改有真实证据、已锁定修改不能被后续轮次倒改。无法安全应用时保留当前文本并记录建议复听，不中断后续流程。
- 本步骤只改文字，片段 ID、开始时间和结束时间保持不变。开始运行时发布完整基线字幕；每采纳一处修改后发布累计的完整字幕快照。
- 跨段疑点的全部补丁先在副本上校验，再整体提交并只发布一次完整预览；任一补丁定位失败、覆盖锁定结果或改变原词、数字字符和否定关系时，整组回滚，禁止只改一半。
- 正式流程、开发单步和 API 统一调用 `AsrPipeline.run_review_decisions`。开发单步读取外部快照并写回外部快照，不修改正式字幕草稿。

`review_decisions_r1` 后面是独立的 `whole_recheck_r1` 全文复核任务：

- 输入 `asr-whole-recheck-input-v3` 显式组合本轮修改后的完整字幕、逐条裁决、累计锁定修改和最初的全文理解结果；输出 `asr-whole-recheck-v3`。
- 该任务只决定“结束自动复查、进入下一轮或保留建议复听项”，不修改字幕文字、片段 ID 和时间码。
- 该任务会再次运行同一个跨段结构检查；高把握问题仍存在时不得静默通过，且复听位置保存完整的相邻片段范围。
- 进入下一轮时，下一轮分段必须按顺序、连续且完整覆盖全文；如果只剩无法自动确认的小问题，不强行增加付费轮次。
- 自动复查最多两轮：第一轮全面检查，第二轮只处理第一轮明确遗留的问题；第二轮后的收尾由本地规则验收，不再发起第三轮模型调用。上一步仍是部分结果时不能直接宣告完成；收尾验收失败时保留上一任务已确认的完整字幕快照并给出精确复听位置。
- 模型返回的字幕位置必须归一到已知问题保存的真实片段范围；冲突或坏编号只能降级为可追踪提醒，不能让最后一轮的非关键定位错误报废整条正式任务。
- 正式流程、开发单步和 API 统一调用 `AsrPipeline.run_whole_recheck`；开发快照仍位于正式项目数据之外。

ASR 的开发定点重放覆盖正式工作流与显式初始分析开发流程的原子任务并集：

- 显式开启开发子流程控制后，正式 `full` 仍从当前项目输入连续执行到最终提交，同时在系统临时根目录写入 `workflow_input`、每个已进入节点的版本化 `*_input` 和成功后的 `*_result`。正式编排只写这些旁路调试证据，从不读取它们。
- `review_decisions_r1/r2` 额外保存模型响应已经规范化、但尚未应用文字修改的 `*_prepared`。开发重放优先使用该边界，从而只验证确定性应用和后置不变量，不重复付费模型调用。
- typed ASR API 的 `execution_mode=development_target` 必须指定原流程 operation 与一个目标节点。执行器只读取同项目、同原 operation、指纹完整且能通过当前 Pydantic 契约的输入，调用正式流程使用的同一领域门面，并在目标节点结束；不会继续执行下游，也不会写正式 Draft。
- alignment、audio boundaries、boundary review 和 subtitle track 已有独立、版本化、可序列化的输入输出。原始 forced-alignment 时间始终保留；说话人分离只在一个超长低覆盖词内形成唯一连续声学区间时提供下游有效时间投影，多段、缺失或过宽证据都不猜测。已核实的画面对白/硬字幕含义必须带着实际显示该文字的源 cue 绑定进入创作与对齐；同一问题中仍未确认的说话人或其余残句保持不确定，不能导致已经读清的字幕被丢弃，也不能借已确认文字补写未知内容。截图只证明采样时刻可见的内容，精确起止仍由音频 cue 和逐词时间决定。画面帧和查询缓存的开发产物写入外部调试根目录，不混入项目正式媒体或缓存目录。
- 这是一种开发测试加速能力，不是正式断点续跑。正式任务默认仍一气执行；若前序逻辑、输入结构或实际输入改变，开发人员必须从受影响的更早节点刷新快照。

### 本土化正式 v3

本土化只有一个公开执行入口和一套生产算法：

- 正式任务提交时由服务端冻结源输入、媒体、要求配置、非秘密模型配置和节点行为指纹。worker 在执行前校验同一绑定，变化时不调用模型、不覆盖字幕；UI 状态不参与绑定。当前正式请求以该次读取的 Draft 构造来源快照，避免校验后再次读取另一版来源。历史无绑定任务保留既有读取兼容，不伪造它们提交时的输入证明。

- typed `operations/localization` 只接受语言、模型策略、本土化级别和世界观策略，
  始终提交 `workflow_id=localization-v3` 的完整流程。
- v3 的 17 个原子任务先锁定源输入与交付目标，再理解全文和人物、按需补证。
  全片理解先形成一份共享的剧情、人物、术语和证据上下文；中文创作再按程序规划的
  连续源范围分批输出，模型每次只能编辑当前范围，程序负责完整覆盖、原顺序和最终
  合并。原意复核与中文自然度盲测独立执行；自然度按整篇主导观感判断，系统性翻译腔
  直接停止，不能靠逐句补丁修成“勉强通过”。终审只修改能够精确定位且已经确认的
  中文句子，最多两轮，不重生成整段，也不把审核扩展成新的创作流程。通过后由 LaBSE 与
  单调动态规划确定全文跨语言路径；一个自然中文段可直接吸收最多 24 条短 ASR cue，
  避免长口语段受固定小窗口截断后把剩余原文挤给后续中文。随后再用逐词时间和停顿
  证据细化语义边界；ASR cue 不再是不可拆的最小单位。目标语言平均密度只用于多个
  合理边界间的低权重辅助。
  每个中文语义窗的开始贴合对应源语义开始，结束不晚于下一个语义开始。完整口播
  台词保持不变；同一段内的多张上屏语义卡通过一条联合单调路径映射到互不重叠的
  源逐词范围，不再逐卡贪心消耗剩余原文。生成双轨后，独立裁决节点只把相邻中文和
  2 至 5 个合法英文分界候选发送给模型；模型不能改文字或生成时间，漏选或越界直接
  阻断；相邻合法选择组合后若会倒序或产生空窗，只对该连续组件保留程序原边界并记录
  warning。单卡完整使用语义时间窗；字数、阅读速度和最长/最短显示时长都不能裁短
  或延长已确定的时间。之后复用正式导出的同一质量门并原子保存。
- AI 路由按职责分层：全文理解、中文创作、原意复核和自然度盲测各自从本次任务
  冻结的设置策略解析模型与思考档位，不在领域代码中写死模型。正式通过必须得到
  `original_chinese_transcript`，四项分数均达到门槛且不存在高或中等级问题，不能只
  依赖平均分或宽松的 `passed` 字段。
- 已删除 v2 `stop_after` 请求字段、开发结果路由、queue 分支、snapshot reader、
  领域实现和专属测试。typed API 对旧字段返回请求错误；generic operation 入口对
  任何不属于 v3 的参数返回明确的参数错误，不能静默改成一次完整付费 v3 任务。
- 开发调试快照只服务 v3 内部的定点节点或付费批次重放，不是正式任务的数据来源，也不修改正式流程默认。开发与正式执行复用同一节点实现；区别只在开发编排可以复用上游快照并停在指定节点。执行到带正式副作用的节点时仍执行该节点的真实副作用，例如目标为保存字幕轨时必须真实写入；正式模式始终从当前项目数据连续执行到结束
  从头执行的语义。

任务耗时以原子任务内部实测为准，并在原子任务完成时立即写入任务状态；后续父级进度只能追加当前阶段计时，不得覆盖已经完成的原子耗时。正式 ASR 只记录实际运行的原子步骤；显式初始分析开发任务中的听写和说话人区分仍分别保存耗时。整条任务总耗时使用墙钟时间，禁止把并行分支简单相加。

工作流定义位于 `backend/app/domains/video_localization/workflow_contracts.py`。新任务以后台摘要中的阶段与任务定义为准。
正式任务必须持久化完整的版本化步骤结果和最终汇总；读取接口不从当前项目状态补造旧任务结果。

视频本土化的源音提取、音轨分离、英语 ASR 和自动参考音候选只允许通过统一
operation 入口启动，页面和外部调用方读取同一任务生命周期。阻塞式 ffmpeg、
ffprobe、BS-RoFormer、导出或 provider 调用不得直接运行在 `async def` API 的事件
循环中。

音轨分离只有一套正式实现：领域门面调用 `services/stem_separation_engine.py`，固定
使用受管的 BS-RoFormer Viperx-1297 权重估计人声，再以逐样本残差
`original mix - vocals` 生成背景声。输出固定为 48 kHz float WAV；长视频按设置的
核心时长分段，每段带前后声学上下文，裁掉上下文后连续写入最终音轨。模型安装、
固定来源、SHA-256 校验、状态、空间占用和卸载由
`services/stem_separation_model.py` 唯一拥有，引擎管理页只调用该门面。模型不提交进
仓库；运行时临时文件只进入设置的 cache 目录并在任务结束时清理。旧 Demucs 运行
依赖和权重不再属于当前系统。

## 前端架构

- 页面负责组合工作台，不保存另一套业务真相。
- API 数据先在适配层标准化，再交给展示组件。
- `WorkspaceRevisionController` 统一持有已观察版本和工作区、时间线各自已应用的快照版本；
  局部保存回执不代表完整同步，按[版本消费契约](../domains/video_localization/domain-contract.md)处理。
- 任务父级阶段、原子任务和详情使用共享组件；进行中与历史记录不复制渲染逻辑。
- 时间线字幕、播放器字幕、字幕侧栏和字幕设置使用同一字幕状态来源。
- 时间线编辑由 `TimelineEditController` 持有；保存回执确认对应操作，并提供实际持久化的
  片段身份及时间，随后发生的操作保留并继续提交。字幕集合编辑也走时间线命令，不退回
  无权修改字幕的整稿工作区保存。丢响应重放拿到的旧回执只确认原保存 checkpoint；若页面
  已消费更新版本，先同步完整工作区并从新基线重放后续编辑，不用旧局部内容覆盖新快照。
  明确指定的轨道在重叠显示、删除和普通移动时不自动重排。
- 待提交操作记录不等于实际未保存差异。只有不存在未确认的保存请求，且编辑器拥有的
  所有数据与已知基线一致时，才可结清相互抵消的记录；撤销/重做历史仍保留。
  不支持的差异仍应报错，同批工作区设置继续按原保存路径处理。
- 缩小后的时间线概览保留命中选择与键盘编辑能力。页面偏好仍会保存，但单纯选择、面板
  与显示设置不被当作未保存的项目内容触发离开警告。
- 时间线选区只负责当前编辑与配音目标范围，不再提供旧“保存选区为项目音色”
  入口。现役配音门面按用户当前选择和人声轨生成受管参考音，不依赖页面维护的
  项目音色库。
- 语音合成页的参考音频时长、裁切范围和待重新识别状态由 `generateStore` 持有；
  编辑器直接读取草稿，不保留随路由销毁的第二份范围。播放地址由受管文件 ID 重建，
  浏览器 File 和临时 object URL 只属于页面资源；离开页面不清空草稿。
- 可编辑参考音频的播放、循环和选区拖动状态统一由
  `frontend/src/lib/audio/selection-playback-controller.ts` 持有；页面组件只把播放按钮、
  Space、播放指针、IN/OUT 拖动和媒体时钟转换成控制器事件并执行返回的 transport
  指令，不自行复制选区边界判断。
- 若视频本土化页面的首次项目加载因后端暂时离线而失败，页面监听全局 API 恢复事件，
  只重试这次失败的初始加载；已经持有 Draft 或用户工作区状态时不走该重载路径，
  避免服务恢复覆盖未保存编辑。
- 普通信息回答“做了什么、结果如何、要不要处理”；高级信息回答“输入、模型、契约和质量检查是什么”。
- 高级信息默认折叠，不展示任意本地路径和敏感值。

## 状态和数据所有权

| 状态 | 所有者 |
| --- | --- |
| 项目、字幕、说话人、参考音和本土化草稿 | 视频本土化领域与 Draft Store |
| 视频本土化 operation 命令和 payload-free 状态元数据 | Operation Ledger；已接受的 submit/cancel/retry 以 ledger command revision 为准 |
| 视频本土化 operation 状态读模型 | Operation Ledger；列表、详情、轮询和恢复读取 ledger-owned 字段 |
| 视频本土化完整任务 payload 和详情结果 | workflow-scoped reader；semantic grouping v2、source audio v1、stems v1、reference candidates v1、standalone diarization v1、raw-ASR development v1 与 initial-analysis development v1 从各自 ledger/core/registry/step/受管 artifact 组装；旧 workflow 通过命名清楚的 Project compatibility adapter 读取 |
| 视频本土化 operation 列表 core | Operation Summary Store；有界 `operation-feed-v2` 只读该投影和 ledger。未验证或损坏投影返回明确 409 |
| 视频本土化 detail-only 不可重建输入 | Operation Detail Core Store；`semantic-tts-grouping-workflow-v2` 的 immutable、path-free typed core 已接 submit/retry、fenced worker commit 和产品详情 reader |
| 视频本土化 worker 执行资格、attempt、lease、heartbeat 和 fencing token | Operation Attempt Store |
| 视频本土化 step 输入指纹、cost class、Provider 提交确定性 | Operation Step Attempt Store；`source_audio/extract_source_audio` 与 semantic v2 的 prepare/group/validate/write 已持久化，Provider rounds 继续保留独立 attempt |
| 视频本土化单次 Provider 调用生命周期与自动重放屏障 | Provider Step Lifecycle；提交前持久化、受管结果 artifact、崩溃恢复为 unknown，同一 operation 的跨 worker attempt 未决付费输入禁止自动重放 |
| 视频本土化 Provider 未知结果的最终裁决 | Step Adjudication Store + Provider Result Recovery；failed 可直接裁决，success 必须先提交匹配的受管 artifact，再在同一数据库事务完成 artifact metadata、step 和 adjudication，尚未开放 API/WebUI |
| 引擎能力和参数定义 | 引擎注册与参数 Schema |
| 用户设置 | 设置服务和设置 Store |
| 页面展开、选择、拖动等临时状态 | 前端页面或组件 |
| 高频播放位置和电平 | 播放控制器，不高频持久化 |

### 项目目录与项目索引

- 每次 SQLite `conn()` 在入口捕获一个不可变数据库路径，并用该路径完成目录创建、
  连接、schema 判定和 schema-applied 登记。测试、CLI 或嵌入式宿主切换全局
  `DB_PATH` 时，已经开始的连接不得把旧文件的初始化状态登记到新文件。
- `read_conn()` 只打开已经存在的 SQLite 文件，启用 `query_only` 并保持一个显式
  read transaction；它不执行 schema/index 初始化。需要证明“不修复、不迁移、不写
  data_version”的离线审计必须使用该入口，而不是把普通 `conn()` 上的 SELECT
  误称为只读。
- `projects.repository_revision` 是不进入公共 Project JSON 的仓储 revision。所有
  Project 创建和更新必须经过 `project_store`，由同一 `BEGIN IMMEDIATE` 事务比较
  读取时 revision 并写入下一 revision；陈旧快照返回 typed conflict，不能覆盖先提交
  的 Project。operation projection revision 只描述 operation feed 变化，不能替代
  这条全 Project CAS。显式兼容迁移若重写 Project JSON，也必须同步推进 repository
  revision，但不得因此推进用户可见的 `updated_at`。
- `projects/` 下的视频本土化项目包是该类项目出现在“历史项目”菜单中的依据；
  全局 SQLite 只负责索引、任务关联和跨页面查询，不是第二份可播放项目包。
- 视频本土化 operation 的完整 payload 仍只属于 Project JSON/Draft。SQLite
  `video_localization_operations` 是 payload-free command ledger，保存不可变身份、
  参数指纹、command/state revision、状态、取消意图和 workflow version；
  `video_localization_operation_outbox` 保存不含业务 payload 的命令镜像事件。
  `video_localization_operation_summaries` 按
  `(project_id, operation_id)` 保存 path-free `operation-summary-core-v1`，
  lifecycle/排序字段仍只取 ledger。Draft Store 从 typed operation 构建 core，
  repository 校验 exact ledger coverage、canonical JSON、row/core revision 和
  project/history fingerprint；每次 typed Draft 写先以 `BEGIN IMMEDIATE` 对当前
  ledger 做 shadow preflight，再应用下一状态。同事务不一致会连同 Project、ledger
  和 outbox 一起回滚，损坏 fingerprint 不会被正常写入静默覆盖。显式
  `backfill_video_localization_operation_summaries.py` 按 project 主键 keyset cursor
  分批执行，每个 project 独立 `BEGIN IMMEDIATE` transaction；克隆 identity
  规范化不修改业务 `updated_at`，单项目失败回滚局部写入并只标记
  `repair_required`，不会阻止后续 project。再次显式执行可从仍权威的 legacy mirror
  重建已标记项目，同时保持 projection/history revision 不倒退。
  `audit_video_localization_operation_summaries.py` 在一个真正只读的 SQLite snapshot
  内双向比较 legacy、ledger 和 summary，报告无 payload/路径的分类与复合 ID；
  additive `video_localization_operation_detail_cores` 不复制列表 core、step result 或
  Provider payload，只为完整详情保存其他权威无法重建的 typed 公共输入。当前
  `operation-detail-core-v1` 按 `(kind, workflow_version)` 选择参数 schema：semantic
  grouping 保存 profile ID、非密钥配置指纹和字数边界，standalone diarization 保存
  引擎/音轨/人数提示，raw-ASR development 保存引擎/音轨/语言，initial-analysis
  development 再保存 diarization 引擎与人数提示；canonical JSON 与 SHA-256 不一致、
  ledger identity 不匹配或 future schema 都 fail closed。submit/retry 和 fenced worker commit 已接入既有
  Project/ledger 事务：只写当前命令或 fence 指向的 operation，普通保存不批量回填；
  detail 冲突、失效 fence、Project CAS 或已纳入 typed detail 管理的 command workflow
  漂移会整体回滚；旧 workflow 暂保留原 summary-derived 兼容行为。
  `audit_video_localization_operation_details.py` 在一个 query-only snapshot 中核对
  ledger、core、step、committed managed artifact bytes/contracts 和 Project mirror，
  只返回有界状态、问题码和复合身份，不修复、不输出路径。
  `migrate_video_localization_operation_details.py` 默认只规划；只有显式 `--apply` 才按
  `(project_id, operation_id)` keyset 分批创建通过 ledger/mirror 契约校验的缺失 core，
  且不推进项目业务更新时间。产品详情 reader 为上述 managed workflow 使用新权威，
  在一个 read transaction 中读取 ledger、core、attempt、step 和受管 artifact bytes，
  不查询 `projects.data`；缺失、
  篡改、future schema 或复合身份错误返回明确 409 repair-required。旧 workflow
  继续通过命名清楚的 legacy adapter 读取 Project compatibility mirror，不伪装成新权威。
  limit 截断时 `--check` 必定失败。审计本身不提升 `verified` 状态。显式
  `promote_video_localization_operation_summaries.py` 在每个 project 的独立
  `BEGIN IMMEDIATE` transaction 内重新解码 legacy mirror、重建并核对投影，再将
  一致的 `shadow` 提升为 `verified`；非本土化项目跳过，失败项目保持可审计且不会
  阻塞后续游标。`close_video_localization_operation_summary_authority.py` 在一个
  全库 `BEGIN IMMEDIATE` transaction 内重新核对全部项目；被 limit 截断、存在
  未 verified 或损坏项目时零修改退出，否则把所有已验证投影和
  `video_localization_operation_summary_authority` marker 一起提交。新空库直接写入
  marker；marker 存在后新项目 writer 直接保持 `authoritative`。v2 有界 feed、v1
  feed 和 summaries reader 在 read transaction 内读取 ledger + core，不查询
  `projects.data`；
  v2 head 返回全部 active（硬上限 32）与默认 50、最大 100 条 terminal history，
  历史使用携带 `history_revision` 的 `(created_at, operation_id)` keyset cursor，
  cursor 页不重复 active。core、state 或
  fingerprint 损坏返回明确 repair error，不能按请求静默退回 Project mirror。
  `missing/shadow` 只允许显式迁移/审计命令读取 legacy mirror；runtime 返回
  authority-not-closed 或 repair error，普通 GET 不回填、不 promotion。
  ledger identity 以 `(project_id, operation_id)` 为键，兼容历史克隆项目中重复的
  operation ID；新 command 仍生成随机 ID。启动迁移会把旧克隆 operation 内残留的
  `project_id` 规范为所属 Project，不改变 operation ID、结果或项目更新时间。
  缺失或 JSON `null` 的 workflow schema 统一解释为 `operation-v1`，不会先经
  `str(None)` 变成伪版本。历史字面量 `"None"` 只由显式、默认 query-only 的有界迁移
  修正；apply 会在同一事务更新 ledger 与已有 step workflow identity，并在 Project
  mirror 漂移、detail core 冲突或 step 版本不一致时拒绝，不推进项目业务更新时间。
  submit/cancel/retry 使用 Project projection revision 做 CAS，并在同一个
  `BEGIN IMMEDIATE` transaction 内提交 ledger、outbox、Project compatibility
  mirror 和 `video_localization_operation_projection_state`；成功后 outbox 标记为
  `applied`。当前 outbox 是同库同步迁移边界，
  还不是文件快照的异步 consumer。
- 旧 `video_localization_operation_index` 及其 state 表已退役。兼容迁移先把原有
  `projection_revision` 复制到职责单一的
  `video_localization_operation_projection_state`，再删除 locator 表和索引；新数据库不再
  创建逐 operation locator 行。Project、ledger 状态元数据和 projection revision 仍在
  同一事务内同步；没有状态变化的普通 Project 保存不推进 ledger state revision。旧项目
  在 operation worker 启动时显式回填 legacy ledger identity，普通 GET 不执行持久化修复。
  历史数据若已有同项目同类重复 active operation，会保留并由只读审计报告；新的 command
  不允许继续创建冲突记录。旧的全局 operation-ID 主键和 outbox 唯一键由兼容迁移无损
  改为 project-scoped identity。
- submit/cancel/retry 命令由 ledger revision 和 Project projection CAS 授权，不继承
  当前 worker 调用栈中的 execution fence；取消命令因此可以原子终止持有该 fence 的
  worker。显式同时传入 command 与 worker fence 属于边界误用并立即拒绝。
- SQLite `video_localization_operation_attempts` 是真实 worker 的持久执行资格权威：
  worker 必须先成功 claim 才能执行，持有 60 秒 lease，并以 10 秒间隔 heartbeat；
  `60s > 3 × 10s + 5s SQLite busy timeout` 是启动时校验的不变量。并发 claim、有效租约
  拒绝、过期接管、heartbeat 和 finish 都条件校验 project/operation/runner + fencing
  token。DB 查询或 claim 结果不确定时不执行领域工作，只安排稍后重试。表中不保存输入
  输出 payload；产品列表、详情、轮询和恢复从 operation ledger 读取身份、状态、取消意图
  与生命周期时间，并从 Project compatibility mirror 补齐 progress、参数和结果 payload。
  删除项目时 step attempt、ledger、outbox、operation attempt 与 projection revision
  同事务清理。
- SQLite `video_localization_operation_step_attempts` 是
  `operation-step-attempt-v1` 的 additive 持久原语。它按 project/operation、
  operation attempt、step、workflow version 和 input fingerprint 保存
  `prepared/submitted/result_unknown/terminal` 状态、cost class、Provider 幂等键及
  只写一次的 request ID；不保存字幕正文、调试大对象、媒体路径或 Provider 原始响应。
  同一 worker attempt 的同一步同输入幂等复用，不同输入使用单调 step attempt number；
  Provider key 不能跨 step 复用。所有写入在 `BEGIN IMMEDIATE` 内复用同一
  `ExecutionFence` 校验，并拒绝失效 lease、取消任务、workflow version 冲突、终态倒退
  和普通路径改写 `result_unknown`。新的 live fence 可把同 operation 中较旧
  external step 的 `submitted` 原子转为 `result_unknown`；`external_paid` prepare
  会按 project/operation/step/workflow/input 检查其他 worker attempt 的
  `submitted/result_unknown`，存在未决结果时在创建新 row 和调用 Provider 前失败。
  `source-audio-workflow-v1` 的 `extract_source_audio` 是正式 `local_free` step：
  输入身份使用源视频 content SHA-256，输出以 path-free
  `source-audio-step-output-v1` 写入受管 artifact。artifact/step 成功后，Project
  media projection、operation terminal success 和 ledger projection 在同一次 Project
  保存中提交；该保存失败时 operation 不会成为 success，已验证的本地 step/artifact
  可由新 execution claim 重放复用。源视频变化、prepare、artifact 或最终 Project
  提交任一失败都保持确定的非成功状态。旧 `operation-v1` source-audio 历史仍只走
  legacy adapter，不会被静默升级。确定性本地 step 的 fenced prepare、artifact
  commit、success replay 和 path-free query-only detail state 已下沉到
  `managed_local_step.py` 与 `managed_local_detail.py`；workflow spec 只绑定 identity
  与 typed codec，具体门面只保留输入锁和 Project media saga。
  `stem-separation-workflow-v1` 同样使用该边界：输入锁定实际源音轨 SHA-256，
  两条 WAV 使用 operation-owned 稳定文件名，artifact 只保存 path-free SHA-256、
  引擎与质量状态；服务在 Project 提交前中断时，新 claim 先复用 hash-verified 文件，
  文件缺失才重跑本地分离且必须通过 artifact replay。媒体投影和 operation success
  仍在同一次 fenced Project 保存中提交，旧 `operation-v1` stems 不会被静默升级。
  `reference-candidates-workflow-v1` 把自动 cue 候选纳入同一原语：实际干净人声
  SHA-256、有序候选 cue 和 reference/cue/speaker revision 共同构成输入；operation-owned
  WAV 与 path-free typed manifest 可在崩溃后校验复用或受控重建。最终提交只合并
  reference/cue/speaker 字段，因此 UI/时间线并发状态不会丢失；这些相关字段变化则
  fail closed。用户手动选区继续走同步编辑事务，不共享自动 workflow 身份。
  `speaker-diarization-workflow-v1` 与 `asr-raw-development-workflow-v1` 把各自
  `stop_after` 开发断点接到同一 local-free 原语：typed detail core 保存不可从 ledger
  重建的请求参数，canonical step input 锁定 resolved track、实际音频 SHA-256 和引擎
  参数，path-free artifact 保存完整结果；新 claim 重放已提交结果而不再次推理。
  `asr-initial-analysis-development-workflow-v1` 在同一 prepared
  audio context 内先 prepare raw-ASR 与 diarization 两个 step，再由 `AsrPipeline`
  并行执行。raw-ASR 失败终止；普通 diarization 失败提交 typed degraded outcome，
  cancellation/fence loss/无效结果/持久化失败仍终止。第三个 deterministic join step
  只保存两条 branch artifact fingerprint、合并片段和稳定 warning code；same-snapshot
  reader 重新校验三份 artifact、共享音频身份与 join fingerprint，不查询 Project JSON
  或重复保存 branch payload，正式 Draft 的 transcription/cues 不变。
  Provider Step Lifecycle 是不依赖具体
  workflow 的内部应用门面：它先恢复旧 submitted，再 prepare/提交 submitted，之后才
  调用注入的 Provider callback；成功响应先补只写一次的 request ID，再写 committed
  managed artifact，最后以 artifact SHA-256 完成 step。明确拒绝进入 failed；超时、
  响应中断、未知异常或响应后的持久化失败进入 result_unknown。成功重入只读取并校验
  同一 committed artifact，不再次调用 Provider；新 worker 对同 operation、step、
  workflow 和 input 的既有 success 也只读复用旧 artifact，允许恢复“Provider 已成功、
  Draft 尚未提交”的崩溃窗口。Provider Result Recovery 接收查询
  已确认的 typed bytes，先按 exact unknown revision 暂存 artifact，再在一个
  `BEGIN IMMEDIATE` 中提交 artifact metadata、把 step 裁决为 success 并插入
  adjudication；三者使用同一 SHA-256。若文件 `os.replace` 后 transaction 回滚，
  staged metadata 保持不变，重放会验证已存在 final file 并补交数据库状态。
  `semantic_tts_grouping_execution` 已把 resolved LLM profile、字幕输入和两轮结果固定为
  版本化契约，并作为该能力唯一的 Provider 执行入口接入 service/operation queue。新任务
  在提交时保存不含凭据的 profile configuration fingerprint；执行前重新解析当前配置并
  比对，随后把同一 resolved profile 直接传入 transport，避免任务期间静默换 endpoint、
  model、protocol 或 reasoning。远程 OpenAI-compatible 与 Codex CLI 归为
  external-paid，loopback OpenAI-compatible 归为 external-free；每轮先持久 submitted，
  再把只含字幕 ID 分组与隐私安全调用记录的 canonical artifact 提交为 success。
  OpenAI-compatible transport 同时发送有界 `Idempotency-Key`。启动恢复只重入带
  `semantic-tts-grouping-workflow-v2` 和合法配置指纹的任务：旧 success artifact 直接
  复用，paid unknown 阻断自动重放，loopback unknown 可在新 attempt 重放；旧版任务仍按
  interrupted 收口。最终 Draft 写入继续执行字幕 source-fingerprint CAS 和 operation
  commit gate。Provider query port、用户裁决入口和 detail reader authority 仍按
  Operation Ledger RFC 5B 后续批次迁移。
- SQLite `video_localization_operation_step_adjudications` 保存不可变的
  `operation-step-adjudication-v1` 裁决记录。只有 `result_unknown` 可由该内部 typed
  command 转为 success/failed；它不继承或伪造 worker fence，而以复合
  project/operation/step identity 和 expected status revision 做 CAS，并在同一
  `BEGIN IMMEDIATE` transaction 内更新 step 与插入唯一 adjudication。记录只含来源
  `provider_query | human_review`、有界 reason/error code、Provider request ID 快照、
  output fingerprint、前后 revision 和 UTC 时间，不保存提示词、响应、用户正文、路径
  或自由文本。Provider 查询裁决必须已有 request ID；同一命令可幂等重放，不同决定或
  持久 step 漂移 fail closed。transaction-level 入口可由 Provider Result Recovery
  与 artifact metadata commit 在同一 caller-owned write transaction 中复用；命令
  预校验发生在文件副作用前。该能力尚未暴露 API/WebUI，也没有 Provider query port，
  且仍不负责同步 Project compatibility mirror。
- SQLite `video_localization_operation_artifacts` 与 project package 内的受管文件共同
  实现 `operation-artifact-v1`。表只保存 project/operation/step identity、payload
  schema、media type、相对 storage/staging key、SHA-256、size 和
  `staged/committed` 状态，不保存 payload、任意绝对路径或大型媒体；单文件上限
  16 MiB。普通 stage 先 fsync 唯一临时文件，再在写事务中重核 execution fence 并
  登记；result recovery 则校验复合 step identity、exact result_unknown revision 和
  external cost class。commit 在同一写事务保护下验证路径、size 和 fingerprint，
  使用 `os.replace` 后更新 metadata。恢复成功路径继续在该事务中写 step/adjudication。
  若文件 replace 后数据库回滚，下一次 commit 通过已存在的 final 文件恢复。
  读取只接受 committed 且逐次校验文件；缺失、篡改、未知 schema、路径越界或 symlink
  均 fail closed，不在普通 GET 隐式修复。metadata service 依赖中立
  `ManagedArtifactFileBackend` port，由 project-package adapter 注入，因而没有新增
  `services → domain` import。semantic Provider workflow 与正式 source-audio
  workflow 共用该存储原语；source-audio 只保存小型 typed runner report，不复制 WAV。
  workflow-scoped detail reader 在同一 query-only snapshot 中从 ledger、step 和
  artifact 组装公开详情且不查询 `projects.data`；统一只读 reconciliation 另行比较
  Project mirror，并按持久 size/SHA-256 验证受管文件。当前仍没有
  retention/quarantine 查询或其 speculative index；项目删除按 artifact metadata →
  adjudication → step rows 清理，正式文件仍由既有项目包生命周期删除。
- `ExecutionFence` 是未暴露到 HTTP 的内部提交 capability。Project/Draft/application
  写入口可显式接收该 capability；真实 worker 在 claim scope 中让同一调用栈内的嵌套
  Project 保存自动解析同一 fence。公共 transaction-level validator 由 operation
  store 与 step repository 复用；operation store 在同一个 `BEGIN IMMEDIATE`
  transaction 内校验 attempt/project/operation/runner/token、running 状态、UTC lease
  expiry，以及 operation ledger 中该任务仍为 active 且未请求取消，然后才更新 Project
  compatibility mirror、ledger state metadata 与 projection revision。失效、跨项目或跨进程取消
  分别以 `ExecutionFenceLost` 或 `ExecutionOperationCancelled` 停止迟到提交，不写
  Project、ledger、projection revision 或后续项目快照。
- worker、进程内排队、取消 token、commit gate 和恢复 timer 全部用
  `(project_id, operation_id)` 复合身份；不再按全局 operation ID 猜测所属项目。启动和
  重复入队恢复从 operation ledger 枚举项目并读取权威状态，再检查持久 lease：其他实例
  仍持有有效 lease 时保留 running 并在
  到期后复查；claim 不确定时不改状态。恢复者也必须通过同一个 durable claim transaction
  赢得新的 fencing token，才能按现有安全策略把遗留 running operation 标记为
  interrupted failed，避免“先查无 lease、后写终态”误伤同时启动的新 worker。每个
  operation 的延迟复查 timer 由 `OperationRuntime` 唯一持有和取消，不依赖全局盲扫。
  attempt 的唯一键、attempt number、fencing token、active lease、heartbeat、finish、
  list 和 latest inventory 同样按复合身份隔离；克隆项目保留相同 legacy operation ID
  时互不阻塞，`attempt_id` 仅作为全局执行事件 ID。
  列表、详情、轮询和恢复的状态 reader 已切换；缺少 ledger 的 legacy 项目可在显式启动
  backfill 后读取，普通 GET 仍只做兼容 fallback。Project 中 command-owned 任务历史不能
  被通用 Project 替换删除。Draft 内嵌任务快照仍未退役；step primitive 也尚未接入
  workflow，artifact authority 尚未接入 workflow，真实 Provider 边界和未知结果裁决
  尚未完成，因此这仍不是完整的 durable operation lifecycle。
- 进程内 operation 调度使用按项目公平的有界 worker pool：默认 2 个 worker，
  `VOICE_STUDIO_VIDEO_LOCALIZATION_WORKERS` 只接受 1–4。调度器按项目轮转、项目内
  FIFO，并保证同一项目最多占用一个执行槽；同项目后续任务不会阻塞其他项目取得
  空闲 worker。`OperationRuntime` 对 pending 与 active 统一去重，避免重复入队任务
  在并发 worker 中提前清除原任务的取消/提交 gate。该互斥只覆盖单进程；durable
  claim 仍按 operation 隔离，多进程项目级互斥和按 Provider/CPU/GPU 分类的资源配额
  仍是后续调度边界。项目隔离不代表算力隔离：分轨、ASR、对齐、普通 TTS 与批量
  TTS 的模型生命周期尚未由同一个资源所有者管理；不得把其中某个队列的串行或
  优先级解释为全机 GPU/内存保障。
- 视频本土化 worker 有 `embedded | external` 两种进程角色，默认配置是
  `embedded`。稳定启动时 worker 与 API 同进程；显式开发热重载时 API 使用
  `external`，由 `run_video_localization_worker.py` 通过 operation application port
  在不热重载的独立进程中执行同一
  operation queue。两种角色共用同一 application/domain 实现以及 durable
  claim/lease/fencing，不产生第二套业务队列。`start.sh --dev-reload` 负责组合这两个
  进程，普通 `start.sh` 不启用后端热重载。
- 启动恢复只扫描 `queued/running`。`cancel_requested` 只在任务仍 active 时转为
  cancelled；已经 terminal 的 cancelled 历史不会在每次服务重启时重复保存或推进
  projection revision。
- `scripts/audit_video_localization_operation_attempts.py` 是有界、只读的迁移对账入口。
  它只比较每个 operation 的最新 attempt 与 Project JSON 状态，不扫描或输出
  operation payload、本地路径和 runner ID；`--check` 在发现差异或结果被 limit 截断
  时非零退出。该脚本不属于请求路径，也不得自动修复状态。
- `scripts/audit_video_localization_operation_ledger.py` 是 command ledger 与 Project
  compatibility mirror 的有界、双向、只读对账入口。它检查项目/operation/类型/创建时间
  身份、状态、取消意图、完成时间、workflow version、参数指纹、缺失行、legacy
  active-kind 冲突和 pending outbox；不读取或输出完整参数、结果 payload、本地路径，
  `--check` 在不一致、pending 或截断时非零退出。
- 页面下拉菜单统一读取轻量 `ProjectSummary`，只返回项目 ID、名称、类型、时间和
  源媒体健康摘要等菜单所需信息；完整草稿、角色、片段和任务结果只在用户选中
  项目后按 ID 读取。
- 任务中心的完整 operation 详情缓存使用 project + operation 复合键；切换项目会清空
  详情、loading 和选中结果，异步详情响应提交前还必须匹配当前 project epoch。
- `TaskProgressPanel` 只发出加载详情 intent，不直接依赖 API client；页面的独立
  project request session 负责详情传输，并用 project ID + epoch 拒绝跨项目及
  A→B→A 的迟到响应。结果详情对话框组件的脚本也只在用户打开某一步或最终结果时
  按需加载；样式可由框架预载，组件加载失败不会改变已选 operation 或详情缓存。
- 检查器默认只加载任务视图。现役配音结果面板在用户进入“配音”且存在配音目标时
  按需加载；加载失败可以原地重试。旧项目音色库、保存选区、inline 参数组和候选
  声音生成分支已删除，配音生成、历史与时间线应用只保留现役页面入口和 typed
  controller 链路。
- `/video-localization` 的生产构建体积由
  `scripts/audit_video_localization_bundle.mjs` 从 Vite client/server manifest
  确定性计算。门禁限制页面客户端主块 raw/gzip、manifest 静态 import 闭包
  raw/gzip 和 SSR 页面主块 raw；动态入口闭包同时列为诊断证据。full 回归会在最新
  production build 后执行 `--check`，quick 回归只验证 analyzer fixture。该门禁衡量
  构建产物依赖，不替代真实浏览器的 preload、请求时序、解析或交互性能验收。
- operation 摘要列表、可见页轮询、历史分页和 submit/cancel/retry 后的列表合并统一由
  `OperationFeedController` 持有。controller 以 project ID + generation 拒绝加载、
  轮询和 mutation 的迟到响应，保证同一时刻最多一个轮询请求；活动任务与空闲发现
  使用不同间隔，页面隐藏时停止 timer、恢复可见时立即同步。每次成功读取保存服务端
  projection revision；后续 `operation-feed-v2` head 请求携带 `after_revision`，版本未变
  时服务端只查单行 revision 并返回空的 unchanged envelope，不读取 Project JSON。
  controller 分开持有 active 与已加载 terminal history；active-only poll 保留已加载
  页，history revision 改变或 stale cursor 时重新读取 head，页面只在用户明确点击后
  继续加载更早记录。
  同一进程内，应用门面按数据库运行代际 + project ID 保存最多 32 份最新 revision
  投影，并为同项目同 revision 的并发 changed 读取执行 single-flight；所有请求仍先查
  durable revision，缓存不是状态源，也不会跨数据库替换复用。缓存命中只复制响应列表
  容器，摘要对象按只读投影使用。摘要字段筛选、活动步骤压缩和终态裁剪由唯一纯
  `operation_summary_projection` 完成；同一模块已定义 path-free
  `operation-summary-core-v1`、ledger 字段重组和 canonical fingerprint，并由
  Draft writer 同事务写入 summary repository。应用 reader 先读取不含
  `projects.data` 的 projection descriptor；runtime 只使用 repository，
  `missing/shadow`、`repair_required` 或损坏 projection 返回明确 409。authority
  已关闭的新空项目可以返回经过 ledger/summary 空集校验的空 feed。缓存键包含数据库
  运行代际和 project；`projection_revision` 仍是
  保护整份 Project mirror 的保守令牌，普通 Project 保存可能造成额外失效。历史
  source/stem 指标作为 path-free durable core 保存；当前媒体健康只属于 workspace。
  开发 artifact 可用性按 operation ID 从受管理目录动态验证，detail 仍执行完整边界、
  schema 和 identity 校验。领域 core 与 API public
  contract 复用中立 schema 中同一递归 locator-removal helper；API 验证后直接输出
  Pydantic JSON，避免 FastAPI 对同一份
  public model 再做一轮 Python 对象编码。
  revision 只是保守失效令牌，完整摘要和详情仍是权威内容源；未来事件流也只能提示
  revision 可能变化，不能成为第二套任务状态。活动 TTS 任务进入终态后，controller
  只读取权威 timeline projection 并合并对应轨道，不重新传输完整 Draft；其他会改变
  ASR、本土化或媒体结构的 operation 终态才刷新完整 workspace。传输超时和其他错误
  由 typed client 归一化，页面只负责展示。
- `operations/feed-v2` 使用 active + terminal page 的有界版本化 envelope 和独立
  `VideoLocalizationOperationSummary` 契约。活动任务可携带有界实时步骤投影；终态
  历史只返回折叠列表需要的状态、时间、计数和来源元数据，不重复传输
  `task_step_results / task_stage_groups / task_final_result / error_detail / sample`。
  用户首次展开终态历史时，`TaskProgressPanel` 才经页面详情会话读取完整 operation；
  详情按 project + operation 缓存，项目切换 epoch 继续拒绝迟到响应。
- `video-localization/workspace` 只返回时间线工作台始终需要的可编辑项目状态、与该状态
  在同一数据库快照读取的 repository revision、随后计算的媒体健康投影和有界语义组摘要。
  完整工作区、时间线投影和局部 mutation 都服从同一单调版本边界：页面记录实际已接受
  响应的 revision，拒绝迟到的较旧响应；窗口重新聚焦先读取小 revision，未变化时禁止
  再传输、解析和重建完整工作区。ASR 逐词证据、参考音频候选、生成候选和完整
  配音生产计划是服务端详情区，不进入主工作区；对应检查器或逐词剪切首次需要时才从
  `workspace-details/{section}` 定点读取。工作区专用 PUT 只保存工作区拥有的字段，并从
  当前权威 Draft 保留所有省略详情，未加载字段永远不能被解释成删除。
  Project 与 `video_localization_workspace_projections` 在同一 SQLite transaction 提交；
  后者规范化保存项目目录、工作台、时间线和语义组摘要四个有界读取视图。正常页面打开、
  revision 变化和 timeline 合并只读这些视图，不对完整 Project JSON 执行 `json_extract`。
  旧项目由显式启动迁移一次性补齐；投影视图缺失时才使用只读兼容路径，GET 不暗中写库。
  完整 Project 仍是尚未完成 operation authority close 前的兼容存档，不能为了减小文件
  直接删除 operation detail 或每片段的后端审计证据。
  同一次读取会把可用且未禁用、未被已有编排占用的系统原音/人声/背景资产确定性
  投影为 path-free 默认片段；该投影不写回 Draft，因而任务刷新不会先清空片段再由
  页面晚一步补回。
  `operations` 与 `tts_tasks` 是服务端任务历史，在 workspace 中固定为空；页面分别从
  operation summary/detail reader 和 TTS task reader 加载，避免首次打开同时传输完整
  历史又立即从专用接口重复读取。工作区和普通 mutation 投影同时省略只供后端审计的
  timeline edit gate 与完整 CQC evidence；完整 Draft 写入会从当前权威状态保留这些字段。
  完整 Draft 兼容 reader 仍保留原字段，但普通工作台路径不调用它。
  完整 Draft 保存的成功响应使用可编辑 Draft 投影，不重复返回 `operations`、candidate
  input、候选内重复 CQC 镜像或 timeline audit evidence。轻量 UI-state 更新只返回本次
  规范化 patch 与 repository revision；源媒体导入只返回 mutation acknowledgement，
  随后由页面按需刷新一次 workspace。完整 Draft GET 只作为显式兼容 reader 保留。
- 时间线片段移动、裁切、切分、拼接、删除、换轨和配音轨控制走 `timeline-edit-patch-v2` typed command。
  浏览器只发送改变字段，并为每个片段操作携带当前媒体生成身份和发起操作时看到的完整可编辑状态；
  服务端既拒绝作用于同 ID 新生成声音的旧命令，也拒绝覆盖同一声音后来裁切、移动或换轨结果的
  旧命令。冲突刷新会丢弃对应旧事务；保存响应被更新的页面 mutation 作废时，待保存内容必须重新
  排队。时间线拥有的禁用轨和废弃任务状态随片段编辑原子提交；前端把集合
  替换投影为 typed 差量，不再因界面状态、撤销或结构性增删退回完整 Draft。成功响应只返回
  对应片段/轨道投影与 repository revision，避免长项目在主线程处理并回写整份 Draft。
- `video-localization/timeline-projection` 是后台 TTS、Agent 写入和多页面协作的有界实时
  读模型，只返回项目内容 revision 与持久化 timeline clips，并省略每片段的大型审计证据。
  页面轮询小 revision，变化时只合并这份投影；首次打开、项目切换或已确认改变
  ASR/本土化结构时才读取完整 workspace。局部字幕 mutation 在请求会话中推进 epoch，
  因而早于它发出的完整 workspace 响应不能在用户调时或开始播放后落地覆盖新出入点。
- 配音历史拖入和显式复用只返回受影响片段、其关联 cue/本土化字幕、轨道状态及 revision；
  浏览器在保存期间直接播放 history 媒体，不等待完整 Project 序列化或再次读取 workspace。
- 项目配音历史由 `TtsHistoryController` 按 active project 单一持有。初始只读取最新
  40 条，当前字幕定点读取最多 8 条，用户明确继续浏览时才按 offset 增量读取；总数由
  `history/page` 的同过滤条件 count 返回，不以浏览器已加载数组长度冒充。
  初始加载、
  focus/任务终态刷新和删除共用 generation、read epoch 与 mutation epoch；切换项目
  立即清空旧投影，迟到读取不能复活已删除记录。单条、当前字幕和全项目删除统一调用
  typed project-scoped bulk command，成功后重新读取权威列表；响应不确定时用不受
  500 条展示上限约束的项目列表确认结果。
- 后端 `tts_history` 门面校验 project/source/scope 后调用 history repository 的
  单次锁内库存选择。记录用一个 SQLite 事务批量删除，随后清理不再被其他历史引用的
  输出、波形和受管参考文件；文件清理失败不回滚已提交的记录删除，而通过 typed
  `cleanup_failures` 明确返回，避免客户端把成功提交误判成可安全重试的未知状态。
- 配音历史的完整 `history.data` 仍是不可变生成事实，交互查询不直接解析该 JSON。
  `history_metadata` 与 `history_scopes` 是同事务维护的规范化读索引，分别拥有项目/来源
  过滤与字幕、cue、组合生成范围。旧记录只在数据库升级时回填一次；当前片段切换、计数和
  分页必须走索引，不能在每次播放或选择时对全历史执行 `json_each`。
- 页面临时成功消息由单一清理控制器管理；新的定时消息会取消旧 timer，回调只有在
  generation 与当前消息都匹配时才能清空，页面卸载统一释放 timer。
- URL 已明确指定项目时，页面直接读取该项目草稿；项目目录扫描和索引修复属于
  独立的目录维护流程，不得阻塞或清空当前工作区。初始菜单先读取轻量摘要，
  磁盘 reconcile 允许后台执行并独立失败。
- 历史项目摘要列表由前端 `ProjectCatalogController` 单一持有。初始轻量读取、
  磁盘 reconcile、菜单同步以及 create/rename/auto-name/delete 都通过同一 typed
  client 边界；每次 mutation 在开始和成功提交时都使旧 catalog read 失效，同项目
  命令另以 entity generation 拒绝迟到响应。删除响应不确定时只用目录同步确认目标
  是否仍存在，不允许旧同步结果复活已删除项目或覆盖刚完成的名称和排序。
- Draft GET 只读取数据库权威草稿并生成内存投影；目录定位也只计算路径，不补写
  `video_localization_dir_name`、不移动旧目录、不写 `project.json`。数据库草稿无效，
  或草稿缺失但目录中已有恢复快照时，会返回明确的 repair-required 冲突，不伪装成
  空项目；尚无草稿也无快照的新项目仍可从契约默认值初始化。
  旧目录迁移、受管理路径重写和快照恢复只能由
  `POST /projects/{id}/video-localization/repair-storage` 显式执行；没有可恢复快照时
  拒绝创建空状态，修复不推进历史项目时间，重复执行不新增 autosave。
- `Project.parameters.video_localization` 与一条合并后的 snapshot projection 请求在
  同一 SQLite transaction 提交。`project.json`/autosave 是非权威文件镜像；事务提交
  后写文件失败不会把已成功的用户命令伪装成失败，而会保留目标 repository revision、
  autosave 意图、尝试次数和错误，并在后续写入或启动时有界重放。按项目的进程锁与
  文件锁串行 writer，writer 每次重新读取最新 Project，旧 revision 不能覆盖新快照。
  JSON 使用临时文件、文件 fsync、原子 replace 和目录 fsync。高频 runtime/workspace
  写只提交并合并待投影 revision，不同步重写完整项目包；content/repair 写与启动重放
  负责刷新最新镜像，避免已完成的 TTS 因大文件镜像写入而迟迟不能上轨。
- reset/delete 以 SQLite 为提交点：Project CAS、operation 相关表、delete 时的
  task/history/batch 行、snapshot 请求以及 cleanup job/tombstone 在一个 transaction
  内完成。提交后按稳定目录名把旧项目包原子移入项目根的 `.trash`，reset 再投影空
  snapshot，最后清理缓存、历史输出、受管参考音和 trash。任何文件步骤失败都保留
  可重放 job；delete tombstone 使目录同步忽略尚未移走的旧包，reset job 未完成时
  拒绝新的 Draft 写，避免旧清理误删新媒体。启动顺序固定为 cleanup replay →
  snapshot replay → workers。

### 视频本土化音频状态分层

- `source_media` 和 `stems` 归领域与媒体存储所有，回答资产是否真实存在。
- Draft 中的文件名和 locator 只回答“配置过什么”，不能证明文件仍然存在。
  `media_health.py` 在读取时统一检查可读普通文件、选择源音频可用候选，并生成
  不含本地路径的 `ProjectMediaHealth`。历史摘要、workspace、Preview、Timeline、
  readiness、quality gate、operation prerequisite、serving 和 export 共用这套语义。
- 页面通过 `GET /projects/{id}/video-localization/workspace` 同时取得 Draft 和
  `ProjectMediaHealth`，避免先加载草稿、后探测文件造成同一帧内状态分裂。健康状态
  是可重建读模型，不写入 Draft，也不推进项目“最后更新时间”。公开 Draft 的
  `source_media`/`stems` locator 固定返回 `null`；页面只根据 health 的
  `resource_id/status/revision` 判断可用性。系统原音/人声/背景时间线片段只保留
  `media_source_clip_id`，文件读取继续走 project-scoped 资源端点。
- 用户在 Finder 中移动仍位于受管理根目录内的完整项目包后，显式目录同步会按旧根到
  新根重写该项目已有的受管理绝对 locator；普通打开项目不扫描目录、不修复路径。
- `Project.name` 是可变显示名称，`video_localization_dir_name` 是首次内容保存时与
  Draft 一起提交的稳定存储 locator。项目重命名只更新显示名称和当前 snapshot，
  不搬动项目包、不改写媒体绝对路径，也不由 `media_assets.ensure_*` 反向保存 Project。
  只有显式目录同步/repair 命令可以迁移或重写 locator。
- `timeline_clips` 回答资产在时间线上的挂载与区间，不复制混音策略。
- 时间线标记功能已移除。人工复核属于配音生产状态：当前
  source/plan/group/candidate 的复核事实保存在 `dubbing_production.group_reviews`。
  它只把候选内容质量门降为 warning，不能掩盖缺失覆盖、重复片段、错说话人、
  过期修订、音频指纹不一致或非法时间等硬错误，也不会在时间线生成额外对象。
- 音频片段的时间线区间和源音频裁剪区间必须表示同一个帧数。
  `timeline_clip_timing.py` 是公开读模型、客户端写入和后端混音的统一规范化入口；
  前端拖拽只提交帧边界，片段宽度直接由该区间映射，不设置会改变可见时长的最小像素宽度。
- `ui_state.track_states` 只保存用户明确操作后的静音、独奏、增益和锁定状态；
  草稿读取和媒体投影不得自动改写它。
- Preview 与 Timeline 通过同一 `resolveAudibleMix` 纯函数解释可听轨道；只有真实挂载
  媒体的 solo 轨才会压制其他轨道，无媒体的遗留 solo 状态不制造全局静音。
- 波形、缓存覆盖率和播放器缓冲属于可重建运行时/展示状态。它们失败时可以降级
  展示，但不能改变资产或时间线片段的存在性。波形资源身份只由媒体版本决定，片段
  裁切窗口、抽样 bins 和可见区细化不是新资源；同一媒体的切分片段共享有界预览峰值，
  细化请求到达前保留现有 canvas。时间线按可见区加 overscan 挂载波形，局部编辑必须
  保留未变化 clip 的对象身份，不能通过全数组换引用让其他轨道重新加载。
- Preview 不直接调用 API 或 `fetch`：sprite 地址由纯资源 helper 构造，视频/音频
  代理准备通过页面注入的 typed media client intent 执行；组件只拥有播放生命周期与
  project/revision 迟到结果校验。`PreviewVideoProxyController` 单一持有浏览器源首帧
  探测、分段兼容视频的启动、轮询、重试与 generation；原片能产生首帧时固定使用
  `source`，否则首个目标 fMP4 片段完成即进入 `partial/playable`，不等待整片转码。
  `SegmentedVideoSourceController` 单一持有 MSE/SourceBuffer、全局时间映射和有界片段
  拉取窗口；窗口移动时同时撤销范围外请求并从 SourceBuffer 驱逐旧媒体，页面播放时长
  不能线性累积浏览器解码内存。音轨媒体节点独立于视频片段状态，准备画面时不得卸载音轨。
- original、vocals 和 background 的轻量音频预览代理是可重建缓存，不是音轨资产本体。
  单条代理在替换、清理或短暂 404 时，Preview 只把该音轨切回稳定的 `source`
  变体并保持其他音轨状态不变，同时后台重新准备代理；用户不需要刷新页面。
  只有 `source` 变体本身加载失败才按真实媒体不可用处理。
- 预览缓存的初始化、状态轮询、显式刷新和按时间分片补建由
  `PreviewCacheSessionController` 单一持有。页面只把当前项目、源视频可用性和播放头
  作为 intent 输入，并订阅缓存/刷新状态；typed client 统一映射 404、超时和未知错误。
  服务端已完整生成且 revision 未变时，初始化请求直接返回现有 ready 状态，不新建后台
  job；缩略图任务始终直接读取源视频，不准备或等待播放代理。Preview 内部的
  `preparePlaybackMedia` 是跳转、恢复和播放开始
  共用的目标时间准备入口；暂停状态跳转也会预取该时间实际可听的音轨。
  项目切换、关闭或页面卸载会推进 generation、清理 timer 与分片去重集合，所有异步
  响应提交前必须同时匹配 project ID 和 generation。状态查询暂时失败时保留 sprite
  元数据但撤销可播放覆盖；确定 404 时清空可重建缓存状态并停止轮询。
- 时间线项目选择由页面 `timelineSelectionItems` 单一持有；Timeline 只把该值投影为
  primary/multi-selection 视图并发出下一次选择 intent，不在组件内复制可变 session。
  cue/subtitle/clip/TTS/range/loop 的瞬态选择在项目载入、关闭和重置时走同一页面清理边界。
- 时间线编辑历史由 `TimelineEditController` 单一持有。cue、本土化字幕、音频 clip、
  配音 lane 和删除相关 UI 状态必须作为一个 typed transaction 提交；页面不再维护
  snapshot/redo/order 协调栈。单条与多条 ASR cue 调时使用同一 transaction/autosave
  路径，`cue.start_ms/end_ms` 同时是轨道位置和 TTS 自定义源参考范围，不复制第二套时间。
  接口已经持久化的编辑通过 `adoptPersisted` 只登记历史，
  保存失败不产生撤销项；待保存补丁按实体 ID 重放并保留后台新实体和 TTS 运行时字段。
- 页面为当前 Draft 保存最近一次服务器基线。后台刷新或 autosave 遇到 revision
  冲突时，以“服务器最新版、当前本地状态、修改前服务器基线”执行三方合并；cue、
  本土化字幕和术语表按稳定实体 ID、再按字段判定本地真实改动，场景文本只在本地
  确实修改时覆盖服务器值。无基线的旧调用继续使用保守兼容合并，不能把该兼容路径
  当作多窗口编辑的完整并发保证。页面离开、切换标签或显式提交触发的保存屏障必须
  有界：若一次请求未消除待保存状态且期间没有新的编辑意图，立即保留本地修改并返回
  失败，不得在浏览器主线程的微任务队列中无休止重试。
- TTS 工作流只通过配音片段影响时间线，不得因首次出现配音而在读路径静默切换
  原音轨混音。若提供“本土化预览”等预设，应通过显式命令应用并让用户可见。
- TTS 初始化片段是持久工作流任务在页面上的运行时投影；工作流身份必须先写入 Draft，
  再在后台准备参考媒体和注册共享生成任务。普通 workspace 刷新和项目 mutation 回包
  必须从同一任务读模型重建这些片段，不依赖页面临时内存；只有匹配的持久化成音片段、
  用户显式删除、项目 reset 或项目切换可以结束该投影。中断且始终未注册生成任务的
  初始化工作流由读模型收敛为可删除失败记录，不静默重放付费生成。
  新生成属于独立 take：初始化占位不得借用或移除现有成音片段；区间重叠时占位和最终
  结果都通过同一配音 lane 分配规则进入空闲分轨。只有独立的显式替换命令可以把已有
  clip ID 作为写入目标。
- `ProjectSummary.updated_at` 是历史菜单唯一的“最后更新时间”，只在项目内容、
  名称或用户明确提交、取消、重试等命令被接受时推进一次；后台任务进度、运行状态、
  工作区 UI、普通打开、导出读取、列表同步和兼容索引修复保留原时间。Draft 写入必须
  声明 `content/runtime/workspace/repair` intent；Draft 自身的 `updated_at` 暂时继续
  作为并发修订时间，不参与历史菜单排序。历史菜单按 Project 时间倒序排列，同一时间
  再按创建时间和项目 ID 稳定排序。
- JSON/EDL 导出是只读投影；视频、音频和独立 SRT 字幕只保留一个版本化 typed `POST /export/render`
  命令。页面先通过 typed 目录选择接口取得服务端生成的 opaque 授权 ID，不向渲染接口
  传任意本地路径；`POST /export/render` 只提交共享 `media_export` operation。
  `VideoLocalizationExportApplicationService` 在用户授权目录内写隐藏临时文件，
  渲染后在 Draft 写锁内重新执行所选交付质量门，并核对包含配音生产状态与实际媒体
  SHA-256 的当前输入指纹；视频/音频继续验证媒体时长，字幕验证非空 SRT，最后原子发布为用户可见文件。
  失败、取消或并发内容变化会清理临时文件，不把目录、临时路径或成品绝对路径写回
  Project/Draft/operation 公共结果。ffmpeg 的输出时间驱动弹窗和任务侧栏的同一份进度。
  弹窗通过独立只读接口取得统一命名策略生成的默认 basename，允许用户修改；提交后的
  `output_filename` 就是最终文件名，后端只校验 basename 与扩展名，不再追加序号或改名。
  同名文件已经存在时返回冲突，不覆盖用户文件。
- 成品导出使用版本化 typed request 显式声明音频轨、配音 lane、压制/独立字幕轨、
  当前中文字幕版本和基础编码参数。页面只负责根据当前可见、静音、solo 和字幕切换
  状态生成默认选择，并显示实际保存目录；用户确认后
  后端严格按请求把所选内容输出成一个视频、音频或 SRT 文件，不把每条轨道分别导出。
  独立 SRT 导出把 ASR、本土化、配音和双语字幕列为明确选项；配音字幕通过
  `localized_subtitle_variant=dub` 选择独立 `draft.dub_subtitles`，不依赖时间线
  当前切换状态。
  所有下载名称复用领域内唯一的纯命名策略，格式由项目名、导出大类、内容/关键设置、
  产物时间和六位内容/产物修订短码组成并作为弹窗默认值；项目名会先清理跨平台非法
  字符。用户确认或编辑后的完整 basename 直接写入授权目录，不二次重命名。
  旧的浏览器 Blob 下载入口不再作为页面导出路径。
- 完整时间线音频由 `timeline_audio_renderer.py` 唯一实现。它接受版本化的片段契约，
  轨道 ID 不限于当前原声、人声、背景音和合成配音；未放置片段的时间保持静音。
- 已经生成并放入合成配音轨的片段属于用户时间线编排，不再由字幕或 TTS 任务生命周期
  所有。字幕文字、口播文字、时间或正式本土化结果变化时，只清除“当前生成结果”镜像、
  取消未完成占位，并把旧片段的目标绑定标记为 `stale`；片段 ID、音频来源、裁切范围、
  时间线位置和分轨保持不变。显式删除片段/任务、替换源媒体或重置项目仍是允许的破坏性
  命令，普通播放、保存、缓存刷新、字幕编辑、字幕合并和本土化重跑不得借这些入口删片段。
  配音字幕工作流的临时单声道音频和用户选择的成品混音仅负责构造各自场景输入，
  全部复用该门面，禁止再维护独立的采样、重采样或混音算法。
- 成品音频和视频以真实源视频探测时长为时间线基准；渲染完成后再次探测输出时长，
  超出帧级容差就删除不完整产物并返回明确错误。字幕视觉换行不得截断文本。
- 内部 operation、开发快照和媒体 reader 可以保留受控文件定位信息，但 Project、
  Draft、operation 和开发结果的公共响应必须使用独立 public contract，不得把
  `artifact_path`、输入音频绝对路径等内部 locator 原样序列化给客户端。当前
  source/stem 与其系统时间线片段已收口；TTS、参考音和候选 locator 仍是
  明确的兼容迁移边界，不能把前一纵切误报成完整 Draft 已无路径。
- 脚本项目与视频本土化项目在项目目录接口中显式分类。脚本工作台和转写导入
  目标只显示脚本项目；视频本土化历史菜单只显示磁盘上仍有效的本土化项目包。
- 用户在文件管理器中移除本土化项目目录后，同步菜单会立即隐藏该项目，但不会
  猜测用户意图并静默删除全局任务或共享素材记录。需要完整清理时，必须使用页面
  的“删除项目”或对应 API，由应用服务统一检查运行中任务并清理项目包、记录和
  项目专属缓存。

## 文件、缓存和设置

- 正式数据遵循 `docs/VOICE_STUDIO_DATA_POLICY.md`。
- 开发快照放在正式产品数据目录之外，仅用于重放受影响的流程后缀。
- 文件创建、探测、转码和分离集中在媒体/存储模块。
- 启动维护默认只扫描和清理可重建缓存。参考音频与种子图片的孤立素材清理必须通过
  `VOICE_STUDIO_STARTUP_ORPHAN_ASSET_CLEANUP_ENABLED=1` 显式开启；开启后引用扫描按
  一次数据快照汇总，不得为每个候选素材重复解析全部任务、历史和项目。
- 按项目或产物键去重的进程内临界区统一使用 `KeyedLockRegistry`。同键工作串行、
  异键工作互不阻塞；注册表在持锁者和等待者全部离开后立即删除键，不得让访问过的
  项目 ID、媒体路径或波形缓存键随进程生命周期永久累积。
- 新增可配置目录、引擎或缓存时，需要同步设置 Schema、设置页面、默认值、校验和迁移。

## 新能力的标准落地顺序

1. 明确能力归属、状态所有者和唯一公开门面。
2. 定义版本化 typed 输入输出和错误语义。
3. 实现领域能力并加定点测试。
4. 在工作流中声明依赖、并行或汇合，不复制算法。
5. 暴露 OpenAPI，并提供中文大白话说明和安全样例。
6. 前端只做适配、展示和人工复核交互。
7. 验证定点、全量、构建和真实浏览器路径。
8. 同步本文件或领域 README，只更新当前事实。

## 当前边界

- 部分内部接口仍使用宽泛 `dict` 参数；新接口应逐步迁移到 typed schema。
- 部分页面和 service 文件仍较大；按稳定能力边界渐进提取，不做一次性重写。
- 现有计划、审计和 RFC 较多；以 `docs/architecture/README.md` 的当前依据为文档入口。

## 维护

架构边界、唯一入口、状态所有权、任务依赖或公共契约变化时，同一提交更新本文或对应领域 README。只保留当前事实和仍未解决的过渡边界。
