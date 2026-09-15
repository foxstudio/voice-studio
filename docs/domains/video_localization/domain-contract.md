# 视频本土化领域契约

## 有证据的局部 ASR 剔除

`POST /api/projects/{project_id}/video-localization/transcription/source-repairs` 接收
`asr-source-repair-v1`，只剔除独立音频证据覆盖的完整误识别 segment，不执行模型调用、
任意改写或重新计时。请求绑定项目版本、听写版本、分离人声 SHA-256 和稳定 request ID。
应用服务一次保存剩余词、受影响英文 cue 及 `transcription.asr_source_repairs` 回执；
回执保留剔除前的文本、词、删除 cue、输入版本和证据，不覆盖原操作的历史产物。
混合 cue 保留原 ID，中文正文、时间和已有配音不变；被中文、参考或配音使用的剔除来源
无法安全保留时拒绝整笔修改。旧项目缺少回执字段时按空列表读取。
相同请求重放不重复写入；同 ID 改参数、版本冲突、音轨变化或运行中任务均不能覆盖项目。
旧质量绑定失效，不把有证据的局部剔除当作新一轮整片审核通过。

## 目标

视频本土化工作台的核心产物是一个可审校、可生成、可追溯的 `video_localization` 草稿。它不是最终视频文件，也不是 TTS 参数页的替代品；它负责记录源视频、分离音轨、说话人、参考音、三轨文本、TTS 交接和质量门。

V1 草稿继续只保存在 `Project.parameters.video_localization`，不新增第二份权威草稿表。

## 当前 ASR 唯一流程

正式 ASR 只使用当前契约，不读取或适配旧 ASR 结果。原始听写输出
`asr-raw-v2`：从项目词表和源视频文件名括号内容自动选择最多 8 个名称提示，
以一行纯文本交给支持上下文提示的引擎；提示仅影响可能听到的词的拼写，不能强制
写入音频中不存在的词。名称证据在听写后由研究、画面和名称归一任务合并成唯一的
typed 名称表，不再创建第二份同义事实表。

正式工作流版本为 `video-localization-workflow-v2`。默认链路从 `asr-raw-v2`
直接进入全文理解，不运行说话人区分或听写/说话人汇合。只有没有有效文字/片段、
时间顺序不可用或整份结果只有不可验证的合成时间戳时才停止；个别长音频分块没有
返回文字或有效时间戳时，其余有效片段继续处理。正式 transcription 持久化
`raw_asr_warning_codes` 与 typed `raw_asr_incomplete_ranges`
（`start_ms`、`end_ms`、`reason`），任务刷新与最终质量门继续显示这些范围。
这些范围不会生成源字幕、中文本土化字幕或 TTS；当前导出混音策略不自动补回原声。

说话人能力仍由 `speaker-diarization-workflow-v1` 和显式
`asr-initial-analysis-development-workflow-v1` 提供；后者继续并行生成 raw-ASR 与
typed diarization outcome，再执行确定性 join。缺少说话人只形成提醒，不阻断使用
预设音色的本土化/TTS；非空但不存在的 speaker、明确选择克隆却缺参考音等真实配置
错误仍由质量门阻断。

校对固定为“第一轮完整检查 → 应用明确修改 → 规划剩余目标 → 第二轮仅检查目标
范围 → 应用明确修改 → 本地收尾验收”。第二轮不重新扫描已经通过的全文，只对目标
范围重新识别最多 8 段短音频，把声音候选交给同一轮判断；收尾不再调用模型，也不
存在第三轮任务。逐词对齐后的本地规则会识别连续 1–5ms 的塌缩词组，在相邻可靠
锚点之间按词长重排时间；它只改时间，不猜测或改写字幕文字。长字幕上下文使用紧凑
逐行文本；只有程序必须精确
定位和验证的问题、决定、证据引用使用最小 JSON。最终质量门同时检查结构完整性、
未关闭问题和确定性边界问题；通过后才允许写回正式字幕与时间轴。

SQLite 的 operation ledger 拥有任务身份、状态、取消和生命周期字段；summary store
保存可从兼容镜像重建的 path-free 列表 core，供有界 `operation-feed-v2` 使用。全局
`video_localization_operation_summary_authority` marker 关闭 runtime legacy summary
reader。WP-04E 的 `operation-detail-core-v1` typed repository 只保存无法从 ledger、
step 或 artifact 重建的有界公共输入：semantic grouping 保存 profile 指纹与字数边界，
standalone speaker diarization 保存请求引擎、音轨和可选人数提示，raw-ASR
development 保存请求引擎、音轨和语言，initial-analysis development 再保存
diarization 引擎与可选人数范围，document-understanding development 保存受管上游
operation ID、profile ID/无秘密配置指纹、行为指纹和提交时 scene context；它不保存
step/result/locator、prompt、密钥或运行时路径。visual-evidence development 再保存
受管 document-understanding authority、当前视频 SHA-256/时长/帧率、有界截图策略、
profile 配置指纹与行为指纹；它同样不保存截图路径、prompt、回答正文或密钥。
submit/retry 和持有有效 execution fence 的 worker commit 会在既有 Project/ledger
事务中同步创建或复用目标 operation 的 core；普通草稿保存不回填历史。
query-only 对账在一个 SQLite snapshot 内核对 ledger/core/step/受管 artifact bytes/
Project mirror，只返回有界状态与问题码，不修复数据。产品详情读取已经按 workflow
分流：`semantic-tts-grouping-workflow-v2` 从 ledger、detail core、versioned registry、
step store 和重新校验的 committed artifact bytes 组装；
`source-audio-workflow-v1`、`stem-separation-workflow-v1` 和
`reference-candidates-workflow-v1` 从 ledger、versioned registry、单一 local-free
step 和 path-free artifact 组装；`speaker-diarization-workflow-v1` 额外读取 typed
detail core，并从 `speaker-diarization-step-output-v1` 返回不含 `audio_path` 的完整
开发结果；`asr-raw-development-workflow-v1` 同样读取 typed detail core，并从
`asr-raw-step-output-v1` 返回未经下游校对的完整 path-free 听写结果。
`asr-initial-analysis-development-workflow-v1` 在同一实际音频身份上并行
提交 raw-ASR 与 typed diarization outcome，再由第三个 join step 校验两条 branch
artifact fingerprint 并保存合并片段与稳定 warning code；公开结果从三个 artifact
重建，不复制完整 branch payload。
`asr-document-understanding-development-workflow-v1` 从同一只读 SQLite snapshot
校验并读取上述三个 artifact，提交 path-free prepared input；全文、JSON 修复、窗口、
汇合和重规划的每个模型 attempt 都通过同一个 typed gateway 成为独立 Provider step。
最终 local step 只保存确定性业务结果和所消费 call artifact 指纹。远程付费调用结果
未知时不会自动重发，新 operation retry 也返回 409；loopback/free Provider 可按既有
策略安全重放。所有健康 managed 路径都不查询 `projects.data`，
任一新权威缺失或损坏都返回
明确 repair-required。旧 workflow 仍通过
命名清楚的 Project compatibility adapter 读取，不能伪装成同一权威。历史
semantic-v2 缺失 core 只能先用显式、有界、默认 query-only 的迁移命令处理；旧
`operation-v1` source-audio 只读保留，不隐式改版。
Operation 的 workflow identity 对缺失字段和 JSON `null` 使用唯一默认值
`operation-v1`；字面量 `"None"` 不是合法版本。历史 sentinel 只能通过默认只读规划、
显式 apply 的迁移修正，且 ledger、已有 step 和 Project mirror 必须一致。
SQLite 还可保存不含业务 payload 的 worker attempt 记录，但 attempt 不参与草稿读取。
确定性本地 workflow 的 fence、step/artifact 提交、成功重放和零输入详情校验统一由
`managed_local_step.py`、`managed_local_detail.py` 与 typed spec 提供；具体 workflow
只拥有输入 fingerprint、typed output、Project 合并策略和用户文案，不得复制这套状态机。
`stem-separation-workflow-v1` 以实际源音轨 SHA-256 为输入，输出 artifact 只保存
双轨 SHA-256、引擎和质量状态，不保存本地路径。媒体文件使用 operation-owned 稳定名称；
崩溃恢复先校验并复用，缺失时允许本地重建，但重建结果必须与已提交 artifact 完全一致。
`reference-candidates-workflow-v1` 只代表后台自动候选：输入锁定实际干净人声 SHA-256、
有序候选 cue identity 以及会影响 reference/cue/speaker 合并的 Draft revision；输出
artifact 保存候选 ID、时间范围、时长和媒体 SHA-256，不保存路径或字幕文本。相关并发
编辑必须冲突，无关 UI/时间线状态通过最新 Draft 合并保留。用户明确选区仍是同步编辑命令。
`speaker-diarization-workflow-v1` 只代表不修改正式 Draft 的开发单步。运行时先解析实际
音轨并计算音频 SHA-256，再 prepare canonical local-free step；artifact 保存匿名片段、
cluster、质量、人数量化提示、实际引擎和耗时，不保存音频路径。服务在 artifact commit
后中断时直接重放，输入音频或参数变化时拒绝在同一 operation 内升级输入。
`asr-raw-development-workflow-v1` 只代表 `stop_after=asr` 的开发断点，不代表正式 ASR。
提交 core 保存请求参数，prepare 后的 step input 再锁定 resolved track、实际音频
SHA-256、请求引擎/语言和时长；artifact 保存完整原始字幕、片段、质量、耗时和使用量，
不保存音频路径。artifact commit 后中断时复用已校验结果，不重复推理；输入变化
fail closed，正式 Draft 的 transcription/cues 保持不变。原始听写开发结果只接受
当前 managed artifact。
`asr-initial-analysis-development-workflow-v1` 只代表
`stop_after=initial_analysis`。两条分支在推理前共同锁定 resolved track、实际音频
SHA-256 与时长；raw-ASR 失败终止任务，普通 diarization 失败提交 typed degraded
outcome 并作为 warning 进入确定性 join，取消、fence 丢失、无效结果或 artifact
失败仍终止。三个 success artifact 可在新 worker claim 下重放，不重复推理。其余模型驱动
`asr-document-understanding-development-workflow-v1` 只代表
`stop_after=understand_document`。它锁定 B2a 上游三 artifact 的 lineage、提交时场景、
profile 配置和会影响 prompt/request/归一化/契约的行为指纹；worker 不重新读取可变
Draft 决定模型输入。成功结果由 prepared input、动态 call artifacts 和 final manifest
在同一快照重建；任一引用缺失、损坏或漂移均 repair-required。
`asr-visual-evidence-development-workflow-v1` 只代表
`stop_after=visual_evidence`。提交必须引用成功且可完整校验的 managed
document-understanding final artifact，并锁定当前源视频真实身份；worker 按问题和轮次
把原始尺寸 JPEG 及 path-free extraction manifest 先提交为 managed artifacts，再用
稳定 call identity 调用多模态 Provider。成功 call artifact 不保存 prompt、回答正文、
图片字节或路径；最终 local step 只引用已提交的 frame/extraction/call fingerprints。
同一只读 SQLite snapshot 会复核 ledger、core、所有 step、artifact bytes 与完整 lineage
后才返回公开结果。远程未知结果禁止自动重发和新 operation retry；普通已知失败可形成
有界 skipped/partial 结果。受限 frame API 只返回再次校验过指纹的 operation-owned
JPEG。其余
`asr-research-evidence-development-workflow-v1` 把 managed document/可选 visual
final artifact、查询预算、模型/搜索配置和行为指纹锁入 detail core；逐次 search/LLM
Provider step 与 final manifest 是新任务的唯一权威，空候选零外部调用。
`asr-entity-normalization-development-workflow-v1` 只接受成功的 managed research
上游，并在提交时保存完整词表快照及 fingerprint；worker 不重新读取可变 Draft 词表。
规范名判断和变体映射的每个模型 attempt 独立提交，final step 校验片段身份、证据引用、
安全替换和 call manifest。
`asr-section-review-development-workflow-v1` 只接受成功的 managed entity-normalization
和 document-understanding 上游；detail core 锁定两份 final artifact、实际 profile 配置
和行为指纹。prepare 从 entity artifact 中重建提交时词表和完整分段输入，每个 section/
attempt 独立落为 Provider step，final manifest 同时覆盖成功和已知失败尝试；区块并行不
改变 section 顺序、片段身份或部分成功语义。三种 workflow 的远程未知结果都禁止自动
重发。所有 ASR development 结果只从当前 ledger/core/step/managed-artifact
权威读取，只接受各原子任务当前声明的 workflow version。

## 读取与恢复语义

- `Project.parameters.video_localization` 是项目草稿内容普通读取的唯一权威来源。
  Project 行的 `repository_revision` 随读取结果绑定到内部 Draft 私有元数据，不进入可编辑 JSON。
  保存必须比较生成该 Draft 时的版本，不得用保存前重新读取的版本替旧草稿背书；发生冲突时，
  原子更新入口重读最新草稿并重新应用本次更新。任何 Project 写都必须比较读取时 revision，不能通过通用 database upsert
  绕过。operation feed revision 与 Draft `updated_at` 各自保持原有职责，均不能替代
  全 Project CAS。
  semantic grouping v2、source audio v1、stem separation v1、automatic reference
  candidates v1、standalone speaker diarization v1、raw-ASR development v1 与
  initial-analysis development v1、document-understanding development v1、
  visual-evidence development v1、research-evidence development v1 与
  entity-normalization development v1、section-review development v1 的
  operation 详情是明确例外，
  由上述 typed operation authorities 组装；旧 operation 详情仍使用兼容镜像。GET 可以在内存中
  兼容旧草稿字段或生成派生质量投影，但不得写数据库、写项目快照或移动目录。
- 数据库草稿无法解析，或草稿缺失但项目目录中已有 `project.json`/autosave 恢复证据时，
  GET 返回 `VIDEO_LOCALIZATION_DRAFT_REPAIR_REQUIRED`，不得把快照或空草稿伪装成
  当前数据库状态。尚无草稿也无快照的新项目可以从契约默认空草稿开始首次导入。
- 旧目录迁移、受管理路径重写、草稿规范化持久化和 `project.json` 快照恢复只能通过
  显式 `repair-storage` command 执行。修复使用 `repair` 写入意图，不推进项目历史
  更新时间；相同状态重复修复不新增 autosave。
- `Project.name` 只用于展示；`video_localization_dir_name` 是稳定 locator。首次 Draft
  保存必须把二者中的 locator 与 Draft 放进同一个 Project CAS，后续改名不得移动包
  或重写媒体路径。存储 helper 不拥有 Project 写权限。
- Draft 中的文件路径是后台持久化细节，不是访问授权。公共 source/stem/cue/reference/
  candidate/timeline/export reader 必须通过当前项目稳定 locator 解析受管普通文件，
  并拒绝项目根外、跨项目、项目根软链接和子路径软链接逃逸。
- Draft 的 source/stem locator 为空但对应 SHA-256 仍存在时，内部 Draft reader 可以只在
  当前项目固定的 `source/`、`audio/`、`stems/` 目录中按完整指纹恢复唯一匹配。该恢复只
  丰富本次内存读结果，不在 GET 中写数据库或快照；零匹配或多匹配保持缺失。显式
  `repair-storage` 或下一次正常内容保存才可把已经验证的 locator 持久化。
- 完整 Draft PUT 只拥有用户可编辑内容。已有 source/stem locator 由后台保留；首次兼容
  写入仅接受真实存在且位于当前项目约定 `source/`、`audio/`、`stems/` 子目录的文件。
  reference、cue、localized subtitle、generated candidate、timeline clip 和 export
  locator 均为后台所有，新客户端值必须被剥离。该兼容过滤是 resource ID/专用请求 DTO
  迁移完成前的过渡边界，不能扩展为任意本地路径能力。
- 片段移动、入出点裁切、切分、拼接、删除、换配音轨和配音轨静音/独奏/音量使用
  `timeline-edit-patch-v2` 专用命令。请求只携带已改变的可编辑字段；每个片段编辑或删除
  同时携带当前媒体生成身份和发起操作时看到的完整可编辑状态；这两项为必填安全栅栏，旧客户端
  若未升级会被拒绝，不允许降级成无保护写入。服务端只在片段 ID、生成身份、时间、源裁切、媒体来源
  和分轨均匹配时执行，既不让旧命令碰新生成声音，也不让同一声音的旧裁切覆盖后来结果。
  响应只返回受影响片段、轨道状态与这些数据所属已提交 Draft 的 repository revision；
  该 revision 必须直接取自同一提交回执，不得在提交后另读项目当前版本，避免给旧快照
  标注后来写入的新版本。从现有媒体切分出的新增片段和精确删除也走该命令。工作区只读
  投影出的默认原声、人声或背景片段首次被该命令编辑或切分时，服务端从同一受管媒体
  解析中只物化本次引用的系统片段，再执行相同的生成身份与可编辑状态校验；删除这类
  投影片段必须在同一请求中明确禁用对应媒体轨，不能返回刷新后复活的假成功。删除片段时需要同步更新的禁用媒体轨与废弃 TTS 任务状态和片段变更在同一事务提交；浏览器中的片段集合替换先投影成新增、修改、删除三类差量，不因撤销记录或界面状态退回完整 Draft 保存。ASR cue 和本土化字幕集合的撤销、重做、插入、删除使用该命令的可选集合字段，不回退 workspace 保存。页面不得为一次指针拖动序列化、上传、
  解析并合并整份 Draft。
- Ctrl+S / Cmd+S 只立即排空自动保存、字幕编辑与历史采用的同一组待提交所有者，
  不制造新的整份 workspace 写入；没有内容修改时不改写项目，不物化未编辑的系统轨。
- `timeline-edit-patch-v2` 的 `request_id` 对旧客户端可选；新客户端对一次逻辑保存分配
  稳定 ID，网络结果不确定时用原 ID 和原参数重试。服务端在同一 Draft 事务保存参数
  指纹及原局部回执；同 ID 同参数返回原 revision/结果且不重放写入，同 ID 不同参数
  返回 `VIDEO_LOCALIZATION_TIMELINE_REQUEST_CONFLICT`。回执是服务器所有状态，workspace
  不下发、完整客户端保存不能伪造或清空；按最近 64 笔和总序列化 2 MiB 双重预算淘汰，
  不使用会让离线重试按墙钟失效的 TTL，并至少保留最新一笔。已淘汰请求重新进入原生成
  身份与 expected editable fields 栅栏，不能因缺少回执而降级覆盖当前状态。
  客户端在请求结果不确定时必须保留同一 request ID、原 payload 和原保存 checkpoint，
  先重放原包并确认原回执，再提交该 checkpoint 之后的编辑。明确拒绝仍保留本地编辑
  与具体错误；重新发起的命令也必须通过原有身份与版本校验，不得以换 ID 或改参数
  规避真实冲突。重放取得的原回执若早于客户端已经消费的完整 workspace/timeline
  快照，只用于确认原 checkpoint 已提交；客户端必须先读取并应用不早于已观察版本的
  完整 workspace，再从该新基线移除已确认操作并重放后续本地编辑，不得用旧局部回执
  覆盖新基线。
- `timeline-edit-patch-v2` 可选扩展 `cue_collection_change` 与
  `localized_subtitle_collection_change`，各含 typed `expected`、`desired` 数组。
  服务端按稳定 ID 推导插入、删除和实际字段差量，仅比较受影响字段的旧值；无关字段、
  并发新增行和未修改行的并发删除保留。同字段冲突返回 409
  `VIDEO_LOCALIZATION_TIMELINE_COLLECTION_CHANGED`，细节含 `collection`、`item_id`、
  `fields`，整包不提交。新增行不接受 TTS locator/result、人工试听确认等后台证据。
  cues/subtitles 的关联更新复用所属领域规则；删除最后一个 subtitle 成员时保留原
  spoken segment 的身份及 paragraph/source 元数据以支持撤销，并使正式绑定失效，
  正式重建时再清理。响应的 `timeline_clips`、`dub_lane_states` 仅包含受影响项；
  可选 `cues`、`localized_subtitles` 有值时为各自完整权威集合，未请求的集合为 null。
  本土化字幕更新同时返回关联更新后的 cues。旧 v2 客户端可继续省略新增字段。
- `editorial` 仅用于专用时间线和纯字幕时间编辑，`workspace` 仅用于 UI 状态保存；
  它们读取未经全稿修复的持久状态，经原有 Project CAS 原子提交，不触发全稿文本
  rebase、音频区间规范化或质量审核。明确的源编辑直接把已有配音字幕标为需复核，
  质量证据保留原检查时间，不代表本次保存重新审核。编辑成功的音频片段由后台写入
  `timeline_timing_version: editorial-v1`，共享读取规范化保留其精确毫秒区间；
  未标记旧片段沿用兼容行为。客户端不得伪造该标记。正式生成、content 和 repair
  写入继续执行原有业务检查。
- 局部 mutation/UI-state/timeline-edit 回执的 `revision` 是服务端版本提示，不表示客户端
  已读取该版本全部内容。它可用于识别过时响应，不推进完整工作区或完整时间线的同步水位；
  两种读模型分别在成功应用各自完整快照后推进水位，局部回执不替代后续增量检测和完整读取。
- `timeline_clips[].clip_id` 是时间线实体的唯一身份；`result_id`、`task_id` 与
  `generation_id` 记录媒体来源。片段 ID 可在“用新生成结果替换原片段”时保持稳定，
  因此客户端积压的移动、裁切、换轨或删除命令必须绑定发起时的媒体生成身份；刷新发现
  同 ID 已换成新声音时丢弃旧命令和相关撤销记录。从配音历史新增片段时，客户端在乐观展示前分配最终 `new_clip_id`，服务端
  原样持久化；`new_clip_id` 只新增，只有显式 `clip_id` 才替换指定片段，不按共享字幕推断替换目标。新增和替换历史音频的请求均须提供稳定 `request_id`；同一次重试复用 ID，
  新的用户动作使用新 ID。后端 `history_placement_receipts` 把请求 ID 与参数指纹随片段同事务保存，
  不随裁剪或删除片段移除；同 ID 同参数只读取现状，同 ID 不同参数返回冲突。
  该集合不下发到工作区，也不接受客户端修改；显式重置项目时清空。旧项目以空集合读取，
  不猜测过去未记录的请求；客户端必须随本契约更新，不保留无请求 ID 的写入口。
- 时间线渲染只在 `composeTimelineRuntimeDraft` 合并持久片段和临时上轨显示。
  同一 `clip_id` 的持久片段优先，临时显示最多保留一份；收到同 ID 的持久片段后，
  `reconcileTimelineRuntimeClips` 立即清除对应临时显示，不等待上轨命令结束，避免
  后续删除又显出旧占位。不同 `clip_id` 即使共享音频来源也必须保留，不能按音频去重。
- 紧凑时间线保存若因同页面另一项 mutation 推进请求代次而拿不到有效回包，必须保留待保存事务并
  重新排队。服务端报告片段编辑状态冲突时，客户端只丢弃触碰该片段的旧事务和撤销记录，再刷新
  权威时间线；不得重放旧的绝对时间覆盖新结果，也不得连带丢弃其他片段尚未保存的编辑。
  时间线 mutation 只返回
  `affected_clip_ids` 指定的实体，未操作的同源片段不得进入响应或发生刷新。
- 删除 TTS workflow 按当前音频的 result/task 来源确定所属片段；同一片段 ID 已采用其他声音时，
  旧任务的删除不能移除新声音。实际媒体来源优先于旧位置绑定和残留的运行时标记。
  编辑器保存只删除明确指定且生成身份匹配的片段；`discarded_tts_task_ids` 用于阻止任务自动回填，不能当作删除同源已落轨片段的指令。
- 结果放轨在首次投递和后台恢复中使用同一失效处理：当前计划版本或生成组明确过期时，
  持有有效投递租约的 runner 立即结束该事件并保留历史音频；其他临时错误继续沿用有上限的重试。
- 历史声音的采用目标与侧栏展示共用选中片段解析；普通字幕、分组及切分后的片段均优先使用
  当前选中片段 ID，未选中匹配配音时才按字幕查找，不得把切分子片段回退成同字幕第一块。
- 工作区刷新、历史采用回执和保存结果在接纳新媒体前，共用同一换代检查并失效旧媒体的
  待提交操作与撤销记录；更新本地基线不能绕过这条边界，同代剪辑继续保留正常撤销。
- 没有可恢复快照时返回 `VIDEO_LOCALIZATION_DRAFT_RECOVERY_NOT_FOUND`，保留数据库
  现状；只有用户明确的新建、重置或保存命令可以创建空草稿。
- Draft/Project 写入把 coalesced snapshot projection 与 Project CAS 放在同一个
  SQLite transaction。数据库是提交点，`project.json` 和 autosave 是可重建镜像；
  镜像失败时命令仍按数据库提交结果成功，pending revision 会在后续写入或进程启动时
  有界重放。重放必须读取最新 Project，并通过按项目串行 writer 与 revision 条件清理
  防止旧快照覆盖新状态。只有 `content`/`repair` 意图要求 autosave并在请求返回前刷新
  镜像；高频 runtime、workspace 写只合并待投影的主 snapshot revision，由后续前台
  快照写或进程启动重放，不能阻塞 TTS 结果进入时间线。`editorial` 与
  `interactive_content` 请求 autosave，但同样只登记待投影 revision，不在交互回执前
  重写完整项目包。
- reset/delete 是显式生命周期 command。reset 在一个 SQLite transaction 中提交空
  Draft、清除全部 operation authority、登记空 snapshot 与媒体 cleanup job；delete
  同时删除 Project、operation、task/history/batch 行并登记 tombstone。事务失败前
  不移动项目包；事务成功后旧包先原子移入 `.trash`，再做可重放清理。delete cleanup
  未完成时目录同步不得重新导入旧包，reset cleanup 未完成时不得接受新的 Draft 写。
  active operation/TTS/batch 必须在事务内再次校验，避免检查后启动的任务被误删。

## 顶层字段

```json
{
  "project_type": "video_localization",
  "schema_version": "v1",
  "status": "draft",
  "source_media": {},
  "stems": {},
  "speakers": [],
  "reference_clips": [],
  "cues": [],
  "localized_subtitles": [],
  "dub_subtitles": [],
  "dub_subtitle_source_revision": null,
  "quality_gate": {},
  "exports": {},
  "updated_at": null
}
```

`status` 可取：

- `draft`：草稿阶段。
- `reviewing`：人工校对中。
- `ready_for_tts`：质量门允许进入 TTS。
- `tts_running`：已提交 TTS 队列。
- `candidate`：已有候选中文音频或候选导出。
- `blocked`：存在阻断项。

## Source Media

`source_media` 记录导入视频和抽取音频的元数据：

- `filename`
- `duration_ms`
- `video_path`
- `audio_path`
- `size_bytes`
- `width`
- `height`
- `frame_rate`
- `imported_at`
- `metadata`

V1 只要求能保存和导出。真实文件复制、抽音频和探测时长在后续导入阶段实现。

## Stems

`stems` 记录分离音轨：

- `vocals_clean_path`：干净人声路径。
- `background_path`：背景音乐/环境声路径。
- `original_audio_path`：源音频路径。
- `separation_engine_id`
- `separation_status`
- `quality_flags`

参考音默认必须来自 `vocals_clean_path` 对应的干净人声，不应直接使用原始混音。

## 时间线媒体轨与混音

浏览器工作区拥有字幕、时间线、混音和页面设置等直接编辑状态；ASR 逐词证据、参考
音频候选、生成候选和完整配音生产计划由服务端拥有并通过按需详情读取。工作区保存
必须从同一事务内的当前 Draft 保留这些省略区，空数组或 `null` 只表示“未加载”，不能
表示删除。真正清空这些领域数据只能调用对应的显式领域命令。

源媒体、时间线编排、混音和波形是四个不同层次，不能互相代替：

- `source_media` / `stems` 是媒体资产是否存在的权威来源。
- `timeline_clips` 只描述资产是否挂载到某条轨道，以及时间线和源区间。workspace
  在同一次媒体健康读取中，把可用且没有被用户禁用或重新编排的
  `media_original`、`media_vocals`、`media_background` 确定性投影到返回草稿；
  该投影不持久化，也不得修改静音、独奏或音量。
- 非阻断的人工复核问题保存在配音生产状态的分组复核记录中，并绑定当前 source
  revision、配音计划、分组和已放置候选；它允许明显可听但反复无法自动修好的内容问题
  继续后续分组，同时保留 warning。缺音、重复覆盖、错人、过期修订、文件指纹不一致和
  非法时间仍然阻断。时间线不再保存或展示独立标记对象。
- 已有明确源裁切范围的正式音频片段以毫秒级语义锚点定位，
  `source_start_ms/source_end_ms` 保留音频精度，`end_ms` 由同一可播放时长得到；
  不得把时间线起点或源音频末尾各自吸附到视频帧，从而制造相邻重叠或截掉低能量
  收音。只有没有源裁切范围的历史片段才继续把可见起止投影到视频帧。公开草稿、客户端保存和成品混音复用同一
  规范化规则；时间线不得用 CSS 最小宽度伪造片段时长，视觉边界必须对应实际播放
  与渲染边界。
- `ui_state.track_states` 记录用户明确设置的混音控制。读取草稿、刷新任务、
  加载波形或第一条配音完成都不得静默改写混音；未来若提供混音预设，必须通过
  明确命令应用，并把结果作为普通可见的轨道控制状态。
- 浏览器保存控制器统一保有正在提交和等待提交的 UI 字段；远端刷新及保存回执只更新
  已确认状态，尚未确认的精确字段继续覆盖展示，更晚的点击优先。紧凑时间线保存与
  同批 UI 修改都成功后才显示已保存；UI 修改只同步混音/界面状态，不复制整份时间线。
- 波形是可重建的展示数据。波形请求失败只允许显示无波形片段或重试提示，
  不能卸载时间线片段，也不能据此宣告源音频文件缺失。

旧字段 `automatic_dub_mix_configured` 和 `initial_track_mix_configured` 仅作为
历史草稿兼容输入，不再接受客户端写入，并在后续正常保存时移除。媒体是否可用、
是否挂载和是否可听必须分别判断。

`dub_subtitles` 是从最终合成配音反向识别得到的独立 typed 字幕轨。每条字幕保存时间、文字、来源 clip、
dub lane、音频 SHA-256 和已有 speaker 归属；不保存本地音频路径，也不
替代 `localized_subtitles` 或源语 `cues`。`dub_subtitle_source_revision` 保存最近
一次成功识别的来源版本。revision 的文字权威是正式采用 clip 冻结的 `tts_target_text`；
实时任务队列只兼容没有冻结台词的历史 clip，不能改变当前正式 revision。后续配音片段、有效裁切、lane 静音状态、冻结台词或人物归属
变化时，最近一次成功结果继续保留，只对关联变更 clip 或与变更前后时间范围相交的字幕标记 `stale:source-changed`；独听及无关 UI 变化不触发标记。其旧 revision
仍会阻止把它当成当前成品交付；时间线和侧栏可继续显示旧结果供对照。任一听写或
声学对齐音频块失败时，整次重建失败且不提交部分字幕。

重建任务开始时只更新任务状态，不修改 `dub_subtitles`。只有正式提交步骤成功后，
才以一次原子保存替换旧字幕和 source revision，避免运行中闪空或失败后丢失上一版。
唯一流程由六个版本化原子任务组成：准备完整音频、整轨 ASR、文字校对、逐字声学
对齐、语义断句、提交。开发模式执行到指定目标即停止，正式模式才执行提交；两种模式调用同一
组领域 action。配音片段按现有时间线位置渲染为与视频严格等长的临时 WAV，中间空隙
保持静音，任何片段越过视频末尾都直接失败。

整轨 ASR 是声音文字事实：它决定听到的文字和顺序。Qwen 长音频返回的块只按
`audio_window_start_ms/audio_window_end_ms` 记录计算范围，不能作为字幕时间或上屏预览。
完整、有序的本土化上屏文字只作为无时间的校对参考，不按配音片段关联裁掉正文。它只
用于纠正有对应 ASR 文字锚点的小范围同音字、专名、数字和写法；任何仅参考稿存在的
文字不得插入，未匹配差异必须在详情中暴露。若一次有据校正跨越相邻 ASR 计算块，
校对节点最多合并三个相邻块重新归属；不得把同一修改拆给错误的块或自由重拼全文。

逐字声学对齐步骤对各 ASR 音频块调用严格 Forced Aligner，并把块内相对字词时间加回完整 WAV
绝对偏移。每个最终字词必须唯一覆盖、单调、不越过块或视频边界，且
`timing_source=forced_aligner`；任一块失败、token 不匹配、倒序或缺失时，任务失败且
不提交。Qwen 受 80 ms 粒度影响时，单个字词可以保留模型直接返回的零宽时间点；不得
人为插值，最终字幕仍必须具有正时长。禁止使用源 ASR、本土化字幕、TTS 分组时间、VAD
时长分配、按字数分配、固定偏移或插值。断句只在真实字词时间之后按 ASR 语义标点选择
边界；说话人变化必定断开，只有同一说话人且实际时长短于 800 ms、与相邻字幕的实际间隔不超过 320 ms 的短片段才合并。
相邻语义片段若共享或交叠同一真实字词时间，说明声学上无法安全分开，必须保持为一条字幕，
不能伪造两个互不重叠的时间区间。
每条基础入点取首字 FA 时间；源 ASR / 本土化字幕与合成配音字幕统一调用同一声学校准，
仅在真实视频帧率已知时于 FA 右侧 `1–3` 帧内选择首个稳定短时能量峰，没有可靠峰或
帧率未知时回退到持续起音或 FA。该校准只改变字幕显示入点，不移动 TTS 音频；出点仍
取末字时间，不改写正文。只有完成严格对齐后的 cue 预览可以
同时驱动时间线和播放器；最终成功后才一次性写入正式 `dub_subtitles`。
整轨 ASR 漏掉某个可听片段时，提交节点只允许在该正式候选的冻结 CQC 同时证明
目标 token 全匹配、无漏字多字且存在正时长真实发声区间时补齐；字幕文字取该候选冻结
朗读文本，时间取 CQC 发声区间，并保留 `needs_review` 与时间轴自动复核标记。缺任一证据时
不得用参考稿补字，交付覆盖诊断继续暴露问题。

所有上屏字幕类型统一调用 `subtitle_punctuation.py`。逗号、顿号、句号、分号、冒号、
问号、省略号、排版破折号和不必要的引号转换为单空格；冒号和破折号可以在格式化前
作为语义断句证据，但不得出现在最终上屏文字中。包含英文字母的通用英文/数字词组与
相邻中文之间自动补单空格，不写死具体产品名；保留必要感叹号、小数、千分位、标识符、
书名号、括号和单词内部的撇号。TTS/朗读文本不调用该规则。

## Speaker

每个说话人至少包含：

- `speaker_id`
- `display_name`
- `route`
- `reference_clip_ids`
- `time_ranges`
- `review_status`
- `notes`

`route` 可取：

- `clone_from_source`
- `preset_tts`
- `preserve_original_audio`
- `manual_review`

不要把不确定的真实人物身份写死。人物身份、截图证据和可见名牌应作为证据字段扩展保存，默认仍以稳定的 `speaker_id` 作为业务主键。

## Reference Clip

每个参考音至少包含：

- `reference_clip_id`
- `speaker_id`
- `source_stem`
- `start_ms`
- `end_ms`
- `duration_ms`
- `audio_path`
- `cleanliness`
- `asr_text`
- `asr_status`
- `license_status`
- `quality_flags`

规则：

- `source_stem` 默认是 `vocals_clean`。
- `cleanliness=clean` 才能作为克隆参考音。
- 每个被选中的参考音必须独立 ASR，不能从文件名、speaker id 或附近字幕推断参考文本。
- 混合说话、背景泄漏明显、多人重叠的片段不能静默进入生产。

## Cue

每句台词至少包含：

- `cue_id`
- `speaker_id`
- `start_ms`
- `end_ms`
- `audio_route`
- `en_subtitle_text`
- `zh_localized_subtitle_text`
- `tts_recommended_text`
- `reference_clip_id`
- `tts_result_id`
- `tts_audio_path`
- `source_duration_ms`
- `generated_duration_ms`
- `review_status`
- `quality_flags`
- `notes`

ASR 自动生成不等于必须人工审核：`generated_by_asr` cue
只有在说话人、重叠讲话、低可信时间、媒体尾部或断句限制等明确风险存在时，
`review_status` 才为 `needs_review`；其余为 `ready`。每次正式重跑都会用本次
结果完整替换源语字幕轨，不保留旧的自动字幕、人工改写字幕或兼容数据。

ASR 验收遵循“结构问题阻断、质量问题提示”的统一原则。空文本、无效或重叠时间、
音源已变化、旧听写修订、词级来源缺失/重复/顺序错误，以及字幕文字与词级来源不一致，
会阻断交付；低可信或插值时间、偏长字幕、不理想断句和仍建议复听的文字，只记录提示并
允许导出和进入本土化。审核建议无法安全应用时保留当前最高概率文本并继续，不把单个
听写疑点升级成整个任务失败。

任务面板把终态统一显示为“已完成”“已完成，有建议”或“失败在：具体步骤”。建议复听项
在结果允许时携带当前字幕和精确的 `start_ms`/`end_ms`，WebUI 可以直接播放该段或跳到
时间轴；片段 ID、错误代码和模型调用信息只放在默认折叠的调试区。失败详情只展示真正的
失败原因，不把任务输入正文误当成错误；已经完成的产物和未写入的正式轨道必须分别说明。

三轨文本必须分开：

- `en_subtitle_text`：英文/源语字幕，用于意义核对和参考音匹配。
- `zh_localized_subtitle_text`：观众看到的中文字幕。
- `tts_recommended_text`：送入 TTS 的中文口播文本。

例如：

- 中文字幕：`1992 年，这件事改变了一切。`
- TTS 台词：`一九九二年，这件事，改变了一切。`

最终中文字幕应从锁定后的中文口播文本同源重建或人工确认，不应长期保留一份和最终声音不一致的字幕。

## 本土化 v3 契约

本土化只有一个公开执行流程：`localization-v3`。它的第一步
`localization-source-lock-v1` 由 `LocalizationPipeline.lock_source`
生成，固定 ASR 最终字幕、逐词时间、声音停顿、说话人、术语、场景说明和组件
指纹。契约不包含视频、音频或缓存的本地路径；缺少核心字幕、逐词时间、有效
时间或来源关系时直接失败。

### 通用化边界

- 当前经过正式验证的语言方向是英语到简体中文；`source_language`、
  `target_language`、`target_locale` 和 requirements profile 仍属于版本化契约，
  领域节点不得根据当前默认语言绕过这些字段。以后扩展语言时应替换语言 profile
  和目标语言适配规则，不复制整条工作流。
- 核心全文理解、证据汇合、完整创作请求、原意复核和语义映射默认不假设具体人物、
  产品、题材或发布平台。固定提示词不得写入测试视频中的专名、误译案例或行业专属
  操作示例；内容专属事实和术语只从当前 source lock、requirements profile 和动态
  创作上下文进入。
- 动态证据已确认品牌、产品、平台、模型、功能名或版本的官方英文写法时，显示字幕与
  朗读台词保持同一英文身份；没有正式中文名证据时不得音译、意译或自造中文名。数字
  可以按目标语言自然朗读，但不得改变型号或版本。
- 默认 profile 的稳定 ID `zh_cn_native_creator_v1` 是现有项目兼容标识，不代表
  输入必须是“创作者视频”。自然度判断以中文母语者在当前内容场景下是否自然为准，
  不得把正式讲解、纪录片旁白、专业表达或克制语气强行改成网络口语。
- 已锁定目标译法的项目术语可进入创作请求现有 `must_preserve`；没有目标译法的
  候选术语、内部备注和全部术语表不得为了“保险”一并发送给模型。
- 当前单人连续讲述是主要验收基准。source lock 继续保存说话人字段，为多人内容
  保留扩展入口；在多人台词归属和配音轨道映射得到独立契约前，不得用提示词补丁
  宣称已经完整支持多人对白。
- 提示词或模型输入变更必须同时使用已验收基准和不同内容类型的固定快照做回归。
  先重放受影响原子任务，基准不退化且跨内容结果有明确收益后，才进行一次不读取
  开发快照的正式网页端全流程验收。

后续契约按以下边界传递，不能重新解析 SRT 或读取执行中变化的草稿字段：

- `localization-context-intent-v2`：解析
  `config/localization_requirements.json` 中的一份版本化要求，固定目标观众、
  “中文字幕与配音台词”交付物、中文表达目标和按画面语义时间对应的规则。
  API 只传 `localization_requirements_id`；省略时使用配置文件声明的默认项，
  未来设置页通过同一个公开目录接口列出和选择配置。ASR 全文概述来自更早的
  ASR“理解全文并规划复查”模型任务，在这里仅作参考；本节点不调用模型，
  下一节点仍须重新通读完整英文原文。
- `localization-document-brief-v3`：全文结构、人物、情绪、事实、术语关系、
  结构化创作策略草案和按内容动态产生的资料/画面问题。创作策略包含内容类型、
  中文语言尺度、叙述声音、推荐中文术语和会造成重大误解的重点语义；固定提示词
  不得写入某一个视频的产品、工具或误译案例。Demo、片中片和示范镜头不是免译
  范围：其中能识别的真实语言仍进入本土化；模型只提出疑似笑、哭、喘息、拖长惊呼
  等非语言表演候选，程序不得用关键词表猜测内容类别。候选必须绑定精确 source cue，
  再由分离人声波形、声音活动范围和连续画面证据裁决。
- `localization-creation-context-v6`：在资料与画面结论之后，用程序把本土化要求、
  全文提纲、动态创作策略和证据约束汇合成唯一的创作上下文包。本节点不调用模型；
  证据不足时保存保守警告，不自由生成另一份完整提示词。一个问题即使整体仍不确定，
  其中由画面直接确认的连续对白或硬字幕也必须单独绑定到实际显示该文字的源 cue；
  未确认的说话人、上下文或其余残句不得随锚点一起升级为已确认事实。连续长句的
  截图预算先覆盖每个相关 cue 的中段，再按时长补靠后的画面和只读前后文，避免
  只截开头、结尾而漏掉字幕在句中切换后的完整文字。只有画面与人声证据共同支持的
  `preserve_non_language` 候选才从下游源输入中排除；不确定候选继续翻译，同时生成
  可定位的人工复核标记。程序还要保证每个 source cue / word 只归属一个本土化段，
  不能靠模型自由复用来源制造重复台词。
- `localization-spoken-script-v4` 及其复核/终审契约：先锁定全文理解，再按连续的
  稳定分块输出中文母稿并由程序保序合并，然后做原意复核、
  中文自然度盲测和终审。创作与终审复用创作模型配置；
  `localization_generation_request.py` 是唯一允许进入创作模型的请求投影：
  system prompt 是版本化的跨领域固定文本；user payload 只包含锁定的全文目的、
  受众、人物、表达方式、不能改错的内容、重点语义、带稳定说话人 ID 的当前连续 cue
  范围，以及只读的前后文。模型只能编辑当前范围；相邻 cue 的说话人变化是中文段落
  硬边界，短接话也不得并入其他人物；程序规划稳定分块 ID、完整覆盖、章节归属、原顺序
  和合并。证据约束只进入包含其源 cue 的分块；画面直接确认的目标文字同时作为
  可追溯锚点。模型不接收时间戳、内部指纹、旧中文稿或后置审核规则，也不得重新
  猜测术语、人物或证据结论。每个付费分块响应在解析前保存开发快照；只重放输入
  指纹变化的分块，其他分块复用。完整度由程序覆盖不变量、独立原意复核和自然度
  盲测共同判断，不按英文 cue 数量强迫中文生成固定段落数。
  源文本中有叙事作用的姓名、咒语、外语和拟声词继续保留；孤立且不构成任何可识别
  语言、姓名、术语或有意义发声的 ASR 乱码，在没有动态证据支持时由生成阶段省略，
  不依赖后置审核逐条删除，也不得伪造为舞台提示。
  原意复核必须同时读取完整英文、全文提纲、已核实的资料/画面证据约束和完整
  中文台词；不得因为 ASR 没有写出画面文字或界面动作，就把证据已经确认的内容
  误判为中文新增。自然度盲测只读取完整中文，不读取英文；详情必须分别显示输入
  范围、检查范围、整篇结论和局部问题，不能只列问题而隐藏判定逻辑。
  自然度盲测按整篇的主导观感分类：少量中低等级措辞建议不把自然中文误判成翻译稿；
  如果整篇仍有系统性翻译腔，则当前母稿直接停止，不用逐句补丁掩盖生成问题。
  终审只允许修复可唯一定位的事实、原意或局部自然度问题。审核编号、编辑目标和
  移动锚点共用引号闭合的结构单元；跨句引语不会被拆成边界不完整的独立编辑目标。
  原始问题引用决定获准修改的范围；单元内其余文字以显式只读片段保留原文、顺序和
  次数，局部删除不得升级为删除整段引语。所有修改在同一份冻结原文的字符位置上
  一次应用，不用首次文字匹配寻找目标。源引号结构不明确时保留原文并提示，不猜测修补。
  每轮章节计划共用冻结原文，跨章节移动不会重新解释后续章节的旧编号。
  `localization-spoken-script-final-checkpoint-v4` 显式保存 `round_edit_plans`，恢复时从
  冻结正文重建并核对结果；旧 v3 不猜测迁移，已有同输入模型候选仍由独立 journal 复用。
  每轮修改后只验收本轮已经确认的问题是否落实且自然，
  旧 issue 闭合后还要对本轮实际修改章节做一次独立回归复核：原意复核读取该章节
  完整源文和修改后中文，自然度复核读取修改后完整中文，只接受修改章节里的无依据
  新增、有效内容遗漏、关系改变、相邻语义重复或新病句。未修改章节不进入请求，也不
  重新打开全文产生一批新意见；发现新问题时才进入第二轮定点修订。
  正式通过的自然度结论必须是 `original_chinese_transcript`；整体分数达标不能覆盖仍存在的
  高或中等级局部问题，低等级措辞建议可以放过；但原意复核已经
  判定为无依据新增、并明确要求删除整句时，终审不得仅因严重度为低而跳过。
  当前 `auto` 策略只把需要同时核对源文与目标全文的原意复核固定为高思考；
  其他阶段继续按所选模型配置解析，`cost` 与 `quality` 预设语义保持不变。
  内容质检采用有限次数的定点修订；超过重试上限后，能播放且来源完整的结果以
  warning 和时间轴标记继续，不阻断后续配音。只有正文缺失、来源重复、顺序破坏、
  时间非法或媒体损坏等通用数据不变量可以阻断受影响分支。
- `localization-development-llm-batch-v1`：全文理解及其字段修复、终审章节编辑及后置原意/自然度闭合与回归复核
  共用的外部开发证据。调用前保存
  `prepared`，返回后先保存 `response_received` 与解析后的 JSON 候选，再记录
  `validation_passed/failed`；调用失败记录 `call_failed`。候选不是 HTTP 原始响应。
  同一开发会话按稳定批次、尝试次数、真实提示词/输入/调用参数和已解析提供方配置
  指纹复用候选；只改本地校验时不重调模型。已有调用意图但候选缺失、损坏或结果
  不确定时不得自动重发。正式流程仅在显式诊断回调启用时只写不读，不作为生产缓存；
  当前只覆盖上述调用，不代表其他工作流节点也已接入。全文理解使用稳定的
  `brief-outline`、`brief-details-chunk_NNNN` 及各自的 `-repair` 批次。
  原始 outline 与 details 分开保存，本地汇合不伪装成模型响应；完整来源与引用校验
  通过前不保存已通过节点。新输出为 `localization-document-brief-v3`，使用
  prompt v14；v2/v13 历史结果原标签只读兼容，不伪装成新分阶段结果。
- 全文理解的唯一门面先生成全局提纲，再复用公共 cue 规划器顺序分析有界核心范围；
  相邻原文和全局提纲只读。程序校验核心完整、唯一、保序及局部引用，并统一重编号；
  同一 cue 可以支持多项不同事实，不按文本相似度删项。v3 以完整 content 的紧凑
  JSON UTF-8 序列化 1 MiB 作为工程存储边界，替代事实、关系、术语和重点语义
  四个列表的 100 条限制；其它字段、结构、引用和 ID 约束保留，v2 回读不追溯应用
  新字节预算。超出时保留所有批次并明确失败，不截断。确定性汇合和后置校验记录独立指纹与
  通过/失败证据，不把某批通过当成整个节点通过。
- 初稿每次实际模型调用前，完整 system prompt 加 attempt payload JSON（沿用运行时
  `ensure_ascii=False` 默认分隔符，含修复附加说明）独立检查 256 KiB UTF-8 逻辑请求
  工程预算。合格请求保持原样，超限不调用、不删事实、不自动摘要；该预算不含 provider
  包装，不是模型 token 窗口保证，也不能用源文分块长度或输出 token 参数替代。
- 全文理解的模型输入不重复携带每条字幕的引擎、显示时间、分段配置及处理版本标记；
  完整正文、顺序、人物、声学停顿提示、不确定性和未知标记仍保留。原始锁定证据不变，
  这只是该阶段的请求投影，不改变后续逐词对齐或来源校验。
- `localization-semantic-alignment-v14`：先把模型偶尔放在同一字段中的空行段落恢复为
  独立语义单元，再把纯对话段中的独立引号台词拆成可单独校时的
  语义单元，再用 LaBSE 与单调动态规划确定全文单调
  路径，再用逐词时间、停顿和标点证据细化相邻边界。一个 ASR cue 可以在真实语义
  边界处由相邻中文段共享，但 word ID 必须完整、唯一、保序；目标语言平均密度只作
  低权重辅助，不能覆盖语义证据。画面已核实的对白、歌词或硬字幕文字必须连同其
  源 cue 绑定进入对齐；截图采样时间只证明当时可见，不取代音频逐词起止。相邻语义窗的目标语言密度出现
  四倍以上的局部悬殊失衡时，无论原文本相似度高低、也不依赖全片平均密度，都沿用已有的有限边界候选复核，
  不直接拉伸时间。中等把握的相邻段还会分别比较目标左尾/右头与附近源词左尾/右头；
  若非原边界显著提高两侧联合语义匹配，只标记为待裁决并交给同一个有限候选复核，
  不由本地向量直接挪动边界。每个中文语义窗的开始
  贴合对应英文语义开始。以冒号结束的引导段和它紧接着引出的下一段内容，在进入
  对齐器前合并为一个语义时间单元，避免把“原台词变成：”之类引导语单独压进极短
  时间窗；连续的“原来是 A、后来变成 B”引导组保持为同一个对比单元。这只改变
  对齐粒度，不修改终审中文正文。每个语义窗的结束
  不得晚于下一个中文语义窗的开始；中文和英文 cue 数量不要求一致。
  已知说话人变化是路径硬边界：候选分组、逐词细化和模型裁决都不得让一个中文
  语义窗跨人物。目标稿允许出现没有独立源时间的短连接句；这类句子会并入相邻
  已匹配语义窗，不能为了强行给它单独分配时间而把后续人物回合整体错位。
  只有说话人未知时才保持原有兼容路径。
  单个中文段最多可覆盖 128 条连续短 ASR cue，以兼容同一人物长回答被 ASR 切成
  大量微小 cue 的情况；这个窗口仍有固定上限，也不能跨越已知人物回合。
  低把握边界的
  模型裁决使用独立的最小请求白名单：只发送相邻两段目标语言语义、2 至 5 个附近
  源语言文字候选和简短停顿提示；全文、时间戳、完整逐词列表、cue ID、相似度、
  内部块 ID、指纹和程序原候选标记均留在程序侧。相邻语义窗出现严重时长密度失衡时，
  即使文本相似度本身较高也必须进入这条有限候选复核，避免“文字像是对的”掩盖错边界。
- `localization-dual-tracks-v7`：适合配音的台词段和适合阅读的上屏字幕；已知说话人变化是
  台词硬边界，任何跨说话人的中文段落都会在提交正式轨道前被阻断；上屏语义卡
  复用本地跨语言向量映射到各自的英文逐词范围，停顿辅助切分。单卡使用完整语义
  时间窗，多卡使用各自映射到的逐词边界；字幕卡起止必须等于所持首尾源词时间，
  禁止为了阅读时长移动时间却保留旧的源词/cue 归属。不同换行对白不合并成同一
  说话轮次。后端不在中文完整词内部写死换行，也不按固定字数强切没有可靠语义边界的
  中文长句；两轨同源，但不要求与英文 ASR cue 逐条对齐。若同一语义窗内部存在至少
  1.2 秒且有客观证据的源声音停顿，程序只保留已经明确存在的中文句末边界，随后仍由
  同一语义映射与有限候选裁决确定两侧各自对应的英文逐词范围。裁决后仍跨越这种停顿的
  少量字幕不会再由程序猜测时间，而是带 `display_strong_pause_review_required` 警告进入
  人工抽查；该警告不阻断其余已经通过通用不变量的本土化结果。
  上屏文本使用定时字幕标点，配音文本独立保留语气标点并把数字规范成适合 TTS
  朗读的形式。
- `localization-display-adjudication-v1`：位于双轨生成与质量门之间。程序用同一跨语言
  向量批量筛出语义或目标/源时长占比不确定的相邻卡片，并从相邻卡片完整合法逐词范围
  中保留最多五个候选；这一步只负责路由和不变量，不把相似度当语义终审。模型请求仍
  只含左右中文、候选两侧英文和简短声学提示，不含时间、ID、分数或全文。每 20 个边界
  保存一个外部开发批次快照；漏选、非法候选或文字变化仍阻断。若相邻的合法模型选择
  组合后会造成空窗或倒序，程序只对该连续组件保留原有确定性边界，并把回退数量作为
  warning 输出；不得采用冲突结果，也不得影响其他已验证边界。
- `localization-tracks-quality-gate-v1` 与
  `localization-formal-dual-track-write-v1`：检查覆盖、顺序、时间、数字和
  源指纹；这里必须复用正式字幕导出的硬门槛，不能维护第二套宽松
  标准。通过后原子写入中文双轨，并让过期 TTS 结果失效。正式写入后只要通过
  公共编辑入口修改任一上屏字幕或配音台词，即保留来源指纹、清除台词/双轨/质量门
  指纹和旧语义分组，并把 `localization_state.status` 标为 `edited`；后续计划必须先对
  当前实际双轨重新完成质量门，不能用修改前的成功任务或相同条数冒充当前验收。

正式任务每次从当前项目数据重新计算，不读取开发快照。付费子任务在开发时可以
把版本化输入、批次、输出和模型记录写到产品数据目录之外；只修改某个子任务时
复用其入口快照；当前 ASR、逐词时间或前序契约变化时，源指纹必须让受影响快照
自动失效并从变化点续跑。子任务通过后仍需做一次不读取开发快照的完整网页端到端
验收。

所有实际调用语言模型的原子任务都必须保存不含提示词和回答正文的调用记录，并在
任务详情调试区显示真实 `model_id`、调用方式、模型配置、Token、费用和停止原因。
未调用模型的步骤不得根据默认配置猜测或补写模型名称。配音语义分组也遵守同一
观测契约，其整理、校验和保存等本地步骤保持为零模型调用。
每个新任务在开始时读取设置页最新的阶段模型配置，并把解析后的路由固定到本次任务；
任务运行途中修改设置只影响后续新任务，不得让同一次任务前后阶段静默换模型。语义配音
分组在提交时保存不含 API Key 的配置指纹，覆盖 profile、protocol、endpoint、model、
provider model 和 reasoning；执行或重启恢复时若当前配置不再匹配，必须在写 step 和调用
Provider 之前失败，用户显式重试才按新配置创建新 operation。匹配后 transport 必须复用
同一个 resolved profile，不得在网络提交前再次读取一份可能变化的设置。

## 配音生产计划与当前片段气口处理

`dubbing_production` 保存当前 source revision 对应的生成计划，以及当前候选的文件、逐词和气口证据。
其中 `scheduling_policies` 是当前计划组的普通 TTS 排队策略，键为 source revision、
plan revision 与 group ID；旧项目缺省为空，重建计划清空。执行请求省略
`resource_priority` 时保留已有策略，显式值只更新选定范围，运行中的推理不抢占。
计划是策略权威，任务参数用于排队和历史追溯；派发时重新读取计划，不能因任务行
尚未同步或旧完成回调而降回旧优先级。production-run 按组回显有效优先级。
该契约不改变音频参数、组内顺序或最终落位规则，也不表示全机资源已统一调度。
确定性 snapshot 只提供字幕、说话人、源语义锚点、已知场景证据和相邻边界；
未知场景、未知语义关系和未知语音资格必须保持未知，不能从片段盒、当前时间线位置或静音
自行反推。计划提交由服务端重新建立 snapshot 并锁定单元/字幕/来源词映射、说话人、文本、
时间、语义锚点和客观边界；Agent 只能补语音资格、场景与语义关系等编辑判断，改写任何
服务器事实都会以 snapshot mismatch 拒绝。提交后得到严格的 `dubbing-generation-plan-v1`：说话人变化、场景变化、
中间存在其他说话、未知边界或同说话人的自适应长空白都切开 speech island。speech island
是连续讲解的宏观时间包络，generation group 只是生成与当前片段气口处理单元。分组同时受语义完整、
估算有效发声时长、目标窗口文本压力和安全可剪边界约束，不按固定字幕条数切分；默认单组
估算有效发声不超过 12 秒，普通文本压力不超过 1.3。超限时只能在有声学或自然停顿证据的
完整分句处拆分；没有安全断点时计划阻断并要求先拆本土化字幕或调整等义台词，来源内容、
顺序和字幕所有权不得丢失。任一组只允许一个确定说话人；仍为 `mixed` 或跨说话人的组必须
阻断，不能提交给 TTS。页面和 worker 不得另写一套分组规则。

生产读模型同时返回 `existing_timeline_clip_ids`、`uncovered_target_subtitle_ids` 和 `timeline_requires_reconciliation`。这些是当前主轨绑定事实，不是人工确认或质量通过：手动拆分、多候选并存和部分覆盖不能被视作空白生成位置。分组与现有片段不一致时，由 Agent 先核对绑定与受管范围，执行器不擅自重生成或重新收口覆盖已有素材；仍使用原有状态和动作枚举，新增字段对旧读客户端保持兼容。

`GET /api/projects/{project_id}/video-localization/dubbing/production-run` 是逐组生产的可恢复读模型。它只从当前计划、共享 TTS 任务、当前候选的内部处理记录和当前时间轴片段推导下一组与下一动作。`single_group` 与 `all_remaining` 调用同一执行器；后者只是自动继续。连续执行请求可冻结起止组、并发上限和常规语速基线，这些值随受管任务、重试和完成回调传递；常规组只允许在冻结基线 ±0.05 内变化，分离人声的原声快慢只作为判断证据，不能自行授权越界。`supervised` 与全自动执行相同的逐词/VAD 气口处理；监工模式展示本组处理结果但不设确认门，全自动只省略展示。相邻组可以同时处于准备、生成或本地处理阶段，但最终采用严格按字幕顺序进行；后组先完成时只保留历史候选，前组终态后才基于最新时间线处理并采用后组。安全剪辑不合格时，两种执行方式都允许按冻结原参数整组重试一次；仍失败则保存明确原因并继续独立组。检查暂不可用不是重生成理由。最后一组终态后读模型直接返回完成，不启动全轨复审。

TTS 任务列表和增量 feed 将工作流回执与同一生产读模型合并。generation 已成功、placement 仍开放且当前进程没有真实收口 owner 时，任务返回 `status=needs_attention` 和机器可读 `required_action`；它表示 Agent 继续既有自动流程，不增加用户逐段确认，也不计作运行进度。进程内收口 owner 存活期间仍返回 `running`。只有时间线中存在该 result 的正式主轨片段才能按成功落轨收口；同一字幕已有别的成功结果不能把未采用候选投影为成功。正式采用一个当前组候选时，同组、同当前计划、完整 target/cue/text 绑定且 generation 已成功的其他开放 placement 在同一原子提交中标记为 `cancelled/superseded`，生成历史和音频继续保留；仍在生成的任务及其他组不受影响。

当前候选缺少独立内容观察时，`POST /dubbing/candidates/{candidate_id}/content-evidence` 通过当前 source/plan/group/candidate/result 绑定解析实际 history 音频。无提示词整文件 ASR 在 Project 条件写入之外执行；`use_current_timeline_projection` 优先使用当前精确时间线裁切，未采用的候选只在语义审计、staged clip 指纹和其目标时间线指纹仍一致时使用其 typed staged projection。只有 repository revision、当前文本、候选身份和实际音频 SHA-256 在调用后仍一致，结果才写入冻结候选。完整的相同字节、引擎和协议证据可缓存复用，并返回稳定 `evidence_id`。该证据只记录听到的内容，不证明语义边界、自然度或主轨验收。

`POST /dubbing/candidates/{candidate_id}/current-projection` 接受版本化 `DubbingCurrentProjectionRequest`，在 repository revision、source/plan、候选结果和实际片段指纹一致时，按已保存主轨重新投影字词、气口和语义边界。该入口不生成、不执行 ASR、不移动或裁切片段；缺少冻结证据、音频变化、丢字、重复字音或超出可用窗口时明确返回问题并保留原片段。相同音频、字词与边界关系的已有判断可复用；改变的接缝必须重新判断。语义提交可只带缺失边界，但与保留判断合并后必须完整覆盖当前全部边界。

内容观察只更新证据，不清空同一已采用几何的语义判断。计划刷新仅在本组来源、冻结内容、实际音频和几何均有相同指纹证明时重绑定原检查；无来源证明的旧结果仍保持未检查，不能以刷新代替验收。当前候选报告是当前快照，不是完整的历史尝试日志。

安全本地气口处理完成后，服务按实际 protected-word 投影核对当前组、下一锚点和已有主轨邻段形成的窗口。明确的物理超限通过执行响应 `status=needs_attention`、`required_action=resolve_capacity` 交给 Agent；所有自动收口入口都保存音频和 durable capacity-decision failure，不会先消耗整组重试。Agent 明确提交单组 `regenerate_existing=true` 后，才可使用已有冻结重试契约。

完整候选经过有证据的容量恢复仍超出当前组到下一锚点的实际窗口时，`POST /dubbing/production-run/manual-timing-deferral` 可把已有成功 history 结果原子停放到 `dub_lane=1`。请求绑定当前 Project repository revision、source/plan/group/candidate/result、音频 SHA-256、由内容观察接口返回的稳定证据引用、Agent 断句结论、独立的自然度试听声明，以及窗口核对、安全气口、允许语速、整组重试、语义拆分和等义压缩六类处置证据；等义压缩最多记录两个变体。服务端从当前计划派生完整 target subtitle 和 source cue 绑定，核对压缩后的当前朗读文本及真实文件头时长，并复用受管媒体采用和冻结结果落轨，不信任调用者提供任意绑定或窗口。该命令只接受完整、未裁掉语音的候选；非连续编辑结果必须先通过既有受管媒体路径渲染，不能把原文件中已删除的内容重新带回。选中候选仍在等待 placement 时，该 workflow 的 placement stage 会在同一 Project 事务中以次轨片段收口；generation/result 身份保持不变，且不会产生主轨 CQC 通过。

次轨准入由服务端根据实际时间线判定：除总时长超限，也接受受保护声音与其他目标主轨片段冲突。首音落位锚点与可安全裁切边界分开，裁切保护逐词和 VAD 的并集；只有前后空白重叠、其他轨道或本组已有版本不构成此类兜底理由。请求不再要求候选时长大于名义可用时长，但仍校验真实窗口、完整内容和全部恢复记录。回执新增 `placement_failure_reason` 与 `conflicting_clip_ids`；旧回执默认解释为时长超限、无已记录冲突，新旧请求字段及幂等指纹算法不变。

成功停放保存 `manual_timing_deferrals`，保留原始证据 revision，不写 CQC 通过，也不改已有主轨和字幕 TTS 镜像。相同 request 与相同参数幂等返回；相同 request 的不同参数和其他过期 repository revision 都冲突。读模型仅在次轨片段仍存在、可读、哈希一致、保留全长 source range 且 target/cue/result/projection 全部一致时返回 `deferred_manual_timing`。用户移动、裁切、删掉或损坏片段后改为 `needs_timeline_edit`，自动继续与恢复入口均不得重生成覆盖用户处置。未改变的分组可随新计划重绑定该 disposition，但任何文本、相邻来源上下文、音频或投影变化都会重新进入人工处理。

相邻上下文变化导致回执失效后，可用当前候选、内容和恢复证据提交新的命令，复核同一受管次轨片段。公共校验器要求原片段身份、绑定、音频哈希与预期全长位置全部一致，才保存新回执；不复制媒体、不更改任何时间线字段。已有片段被编辑或 ID 重复时仍拒绝。旧请求幂等重放不恢复已删除片段，也不冒充新的证据。

时间轴保存实际放置的声音，历史记录保存生成版本。普通生成（含复用参数、单个或多个字幕）新增独立片段并分配空闲配音分轨，不删除任何旧片段；同一结果的重复回调按生成/结果身份幂等处理。用户明确采用历史声音替换时，只更新指定片段 ID，保留共享字幕的其他片段。受管重做是明确授权的替换操作。受管任务生成成功时先保存历史与候选记录，不提前修改时间线；逐词/VAD 气口处理在内存投影中完成，最终提交才一次替换旧当前片段。任一步失败、进程中断或目标片段被并发修改，旧当前结果保持不变。候选完成逐词/VAD 气口处理后，只要内部处理记录非失败、当前候选可播放且在主轨精确覆盖整组，读模型才投影为 `accepted`；这只是读模型阶段，不是持久标签。活动路径不再写入或依赖 `selected`、`accepted`、`not_reviewed`、分组认领、人工复核或 `timeline_edit_gate` 等第二套状态。旧项目中的这些字段只作为兼容输入读取，正常重生成或重新采用后自然消失。

逐词与 VAD 证据仍用于精确剪辑，不作为用户可见标签。候选没有逐词证据、切片伪造/遗漏/打乱 word ID，或切片发声边界不等于首尾对齐词时，收口返回类型化失败。Qwen 对短词返回零宽点锚时，候选证据保留这个真实点及其 word ID，正时长的整体发声头尾仍由同一音频的 VAD 负责；不得把点锚插值成伪造的词内时间。真正没有任何对齐词时，收口返回类型化组失败而不是抛出未处理的 schema 异常。新候选落位时，首个正时长强制对齐词是首音锚点；只在没有这种词级证据时才回退 VAD，调度器不得为了尾端容量把它提前。自动裁首尾空白仍保护 VAD 发声范围与全部真实逐词锚的并集，不能因词级首音与 VAD 不同或 VAD 较早结束而裁掉受保护音频。服务端从最终 source crop 与相邻切片的时间线间隔重新计算每个原始气口的实际呈现时长；已判定为 `remove/shorten` 的气口若未在正式投影中真实生效，收口返回类型化失败，不能改写为 `retain` 后继续采用。这里仅证明当前片段气口处理已经执行并可恢复，不建立内容审核或人工复核状态。

同一对逐词锚之间若被能量分析报告为多个安全气口，自动流程只选择其中范围最大的一个执行，其他区间保留并标为不确定；不得为了同时删除多个检测区间而制造没有逐词证据的孤立切片，也不得连带删除这些区间之间尚未证明为空白的音频。

Agent 可以为已经采用的候选补充或修正逐字边界处置。审计提交入口必须先核对当前计划、音频 SHA-256、完整目标归属和实际正式裁切与原审计投影一致，再原子更新审计记录；不得再次采用音频或改写时间线。重复提交同一处置也必须核对这些条件，不能用幂等返回绕过已变化的音频或计划。提交期间用户调整了目标片段时，保留用户编辑并返回冲突。

首尾安全空白只缩短到统一的 80 ms 音素保护余量；测得的首尾空白本来就不超过该余量时，从气口裁决开始即记录为保留，不先声明删除再由投影静默改写。

首尾气口的实际保留量与裁切写入共同使用 VAD 和逐词锚的保护并集；逐词锚进入能量检测的边缘静音区时，重叠部分继续作为真实音频保留，不能仍标记为可删除空白。

生成任务失败时，服务端保存绑定当前 source/plan/group 的失败事实；读模型把该组投影为 `failed/complete` 后继续寻找下一个非终态组。它不自动重试、不自动切换模型。全部组进入终态后直接结束；存在失败组时状态为 `completed_with_failures`。

source revision 必须覆盖规划读取的逐字时间与声学边界。每次成功提交计划都会推进项目内
单调递增的 `plan_revision`；生成任务在正式提交时冻结该 revision 和完整 group，旧计划的
worker 即使遇到相同字幕与相同 group ID 也不能回填新计划。重新规划同时取消并 tombstone
所有未完成的旧 workflow/task；放轨入口在采纳媒体前重验当前 plan revision/group，旧结果
不能创建候选、时间线片段或恢复 workflow。history 与放轨 outbox 同事务提交时也冻结同一
plan revision、group 与目标字幕集合；进程在首次投影前退出后，重放仍使用这份原始血缘，
既不会把合法当前任务误判为 legacy，也不会从当前项目补造旧事件缺失的身份。

兼容的完整 Draft PUT 会无条件保留服务端当前 `dubbing_production`，并剥离客户端伪造的
处理记录；首次保存也不能注入这些权威字段。重新规划默认使旧内部报告和冻结输入失效，
但可以在同一原子更新中延续已证明不变的完成证据：分组、目标文本、局部来源及相邻上下文、
受管音频指纹和实际裁切必须一致，并保留证据原始 revision。缺少来源依赖指纹时不推测旧时状态。
延续校验与生成落轨共用词级首音和 VAD/词尾保护边界，不因两者的测量差异误判已有音频被移动。
旧逐字语义处置不冒充新计划的审计；已有音频可通过公开恢复入口重新取证，无需重新合成。
计划计数器不回退。当前时间线只认可同一 candidate/group/source/plan revision
的服务端冻结输入与内部处理记录，并重新核对当前受管音频 SHA-256；片段自带的 `passed`
或其他历史标签不参与采用判断。

用户明确要求重做一个已有结果的组时，`production-run/execute` 只允许
`scope=single_group`、明确 `group_id` 和 `regenerate_existing=true` 的受管命令。新结果仍走
同一生成、气口剪辑和目标字幕所有权提交；队列完成时只保存历史候选，组内收口完成时才原子替换该组当前片段，旧声音保留在
历史记录中，不再靠额外时间轴分轨等待第二次“正式采用”。不得先调用 reset 删除整段时间轴。

一个已经完成当前片段气口处理的候选可以通过 `dubbing-timeline-split-request-v1` 投影成多个正式
时间线切片。命令必须携带并匹配当前 source revision、plan revision 和 timeline
revision，同时再次校验候选冻结输入、气口处理记录以及受管音频 SHA-256。每个切片
只引用同一候选媒体的一个源裁切范围；所有切片必须按目标字幕原顺序精确分区，并用连续、
无重叠的源裁切范围完整覆盖原候选。强制对齐字词证据不得在切片间重复。时间线整理把同一
候选的切片默认视为一个保持内部相对时间的音频块。只有同一组绑定的相邻切片在词级证据和
多阈值 VAD 都确认安全语义边界时，才可通过 typed `timeline_gap_before_ms` 小幅增加该边界
的时间线停顿；默认值为 0，不能用它掩盖字音内部黏连。除此之外，声学切点不得额外拉长、
压缩、删除或重复候选内部停顿；可分配的剩余时间只放在不同候选音频块之间。原子放轨按
目标字幕所有权接受一个候选的多切片覆盖，并拒绝目标字幕重复、源裁切重叠或候选内部停顿失真。

旧版 `dubbing-timeline-edit-gate-v1` 只保留读取兼容，不再是活动生产流程的写入或完成条件。
当前收口在同一服务事务内核对 source/plan revision、受管音频、真实发声头尾、归一化生成
倍速和每个真实气口的处置，先保存 typed 候选裁切投影，不替换正式时间线。
Agent 通过候选 `/semantic-boundaries` 读取完整台词、全部相邻字词（包括相接、重叠、被裁切）及字内低能量证据，再通过 `/semantic-boundaries/review` 提交 `dubbing-candidate-review-command-v2`。
命令绑定 source/plan revision、候选 ID、音频 SHA-256、声学证据与最终裁切指纹，完整且唯一地处置每个边界；缺项拒绝，不确定留待 Agent 接续，明确异常进入现有恢复阶梯。
全部可接受后才在同一原子更新中复核当前版本、目标所有权和媒体并采用。重复同一已采用命令不重复落轨；手动素材和旧成品仍保留读取与播放兼容，不伪造历史语义检查。
`needs_semantic_review` 是 Agent 的自动交接，不要求人工逐段审核，也不允许把技术安全或对齐间隔为零当作自然度通过。
普通内容以 `speaking_rate_ratio` 1.0–1.3、全轨普通候选最大最小差不超过 0.3 为检测目标。
执行请求显式冻结普通速度基线时，各普通组直接沿用该值。尚未冻结时，生成速度使用最近三个非例外正式主轨片段的真实提交速度中位数初始化稳定
基线，并读取紧邻正式片段或同一有界并行窗口中紧邻已冻结任务的速度维持听感连续。普通
调整不得超过 `±0.05`。单组经分离人声词级时间证明的节奏例外只对该组有效，不进入后续
普通片段的稳定基线。来源节奏至少变化 20% 且原因与证据 ID 已写入同一生成请求时，才可
放宽相邻限制；该证据只许可当前中文按自身文本压力作必要变速，不按原文速度比例强制缩放。
目标窗口有充足空间时，来源较快本身不能造成不必要加速。逐词/VAD 与内部处理记录只用于当前片段气口处理，
不会触发内容复审或全轨扫描；只保留上述“强断句黏连”单次重生成窄兜底。

时间线容量修复是另一个显式、单组、受管的窄入口：必须同时提交
`scope=single_group`、明确 `group_id`、`regenerate_existing=true` 和
`repair_timeline_capacity=true`。传入 `ordinary_speed_baseline` 时，容量候选也必须遵守冻结基线，最多增加 0.05；没有正式落轨的完整候选同样可以走此入口。旧候选已经生成、仅等待落轨时，执行器通过内部 reservation 意图精确引用旧 workflow；公共服务在写锁内核对当前计划、完整冻结身份、耐久容量失败和候选结果后，只放行该旧 receipt 的占用。非法引用、真实生成和第二次并发 reservation 仍拒绝；旧音频不删除，旧 placement 在新候选成功采用时统一收口。
未传入冻结基线的兼容路径中，只有当前已核实候选无法装入同一来源连续链时，第一轮可用
1.35；该组已有耐久化的第一轮容量修复证据但仍无法装入时，只允许再用一次不超过 1.8 的
第二级修复。两者都写入当前候选的速度例外原因和证据 ID，不进入普通速度基线；新候选仍通过
同一气口处理和目标字幕所有权提交。不得把容量修复扩散为全片提速或无界重试。

容量失败不属于重试耗尽，工作流终态同步不得因此关闭 placement。明确的同参整组重试（`regenerate_existing=true` 且不启用容量变速）对当前容量候选复用原冻结请求，只允许既有的一次 attempt 2，并将精确旧 workflow 身份传给同一 reservation。旧版本误写成 `TTS_PLACEMENT_REGENERATION_EXHAUSTED` 的 receipt 仅在当前容量证据与全部冻结身份仍成立时兼容参与；不会复活或重写旧状态。

时间线规划以 speech island 为连续链，但一次候选采用是局部事务：第一块优先守大段落首部，
最后一块约束大段落尾部，中间 generation group 不逐个钉死到 ASR 入点；实际写回只允许当前组
和与其新位置直接冲突的相邻组改变。其他组必须从采用前快照逐字段恢复，不能因为生成当前一句
而重排后续整条时间线。局部空间不足时保留当前片段，不用全轨级联位移掩盖冲突。

全轨收口时，调度器和审计器共用同一套“已证明连续”判断：明确的无断句关系，或同场景、
同说话人、无中间语音且具有中高置信低能量自然停顿证据，才可进入同一连续链；只有时间锚
接近、而剪辑关系未知时不能猜成连续。连续链仍有未填满的空窗只记 warning，不阻断交付；
组间未知重叠、越出场景、覆盖缺失和非法时间仍是 blocker。

正式 rebalance 只接受候选整体真实发声位置的统一平移，以及完全由已声明
`alignment_lead_ms/alignment_trail_ms` 抵扣、不会触及对齐字词的首尾静音裁切；其他源裁切、
时长或切片相对结构变化全部回退。静音裁边后必须按当前投影重算气口保留量。
历史正式片段缺少 `dubbing_group_id` 时，可用项目内唯一 candidate identity 和精确目标字幕
覆盖恢复所属组；不能因为旧字段缺失而回退已验证的整组调整，也不能只凭相近时间猜归属。

候选记录冻结任务成功状态、产物指纹、逐词对齐、首尾发声边界和内部停顿证据。
历史音频是可编辑素材。普通生成、手动拖入和指定替换不调用 ASR，也不要求内容批准；
受管对齐与气口处理不因原素材夹杂外语或缺少独立转写而停止。项目归属、文件可读、
有效裁切、任务身份和并发版本检查保持不变。
`tts-content-evidence-v1` 只保存可选的整文件独立转写，以音频 SHA-256、引擎和协议标识。
已有转写差异只提示，不触发重生成；缺少转写不伪造内容覆盖证据。
最终内容质量依据剪辑后实际保留的源范围与拼接听感判断，已删内容不参与成品判定。
工具没有额外的最终 ASR 审批状态；自动字词覆盖保护仍防止错误裁切，不用于阻止人工导入。
单组与连续执行共享冻结参数的一次整组重试；失败后返回需要语义拆分或附近参考音决策，不自动猜分句或切换参考。
流程不增加音色或主观自然度审批，也不调用 `phase=timeline` 或 `phase=delivery`。最后一个语义组完成后流程直接结束。配音字幕、导出
和人工核对仍是各自独立、由用户明确启动的能力。

本土化字幕的 `text` 是上屏与字幕导出的最终文本，`tts_text` 是预览、生成、重试时的最终
朗读台词；两者允许分别修改，并由同一个局部 mutation 原子持久化。新记录不得用上屏文本
覆盖已明确保存的朗读台词，只有从未存过 `tts_text` 的旧记录才回退到 `text`。

只修改本土化显示文本、朗读文本或目标时间，且语义单元、来源字幕/词、说话人与源语义锚点
身份完全不变时，持久层会把既有语音资格与场景判断保守重绑定到当前 snapshot，推进
`plan_revision`，并使用当前客观边界重新分组。只有来源版本、整组输入、当前音频字节及裁切投影均未变的已完成组保留证据并重绑定计划版本；其余旧证据失效，不补造缺失报告。新 handoff 会把单条字幕选择扩展为当前计划的完整生成组。
修改原文、来源映射、语音资格、说话人、源语义锚点、语义单元结构或场景证据则清空当前计划，
旧 revision 只读，不使用当前项目状态补写过去不存在的判断。`enforcement_mode=legacy` 且从未创建计划的旧项目
只读保留原查看和已有结果导出行为，但新的 TTS 提交和结果放轨必须先建立当前计划；项目一旦
创建计划就永久进入 `planned` 模式，即使内容变化使当前计划失效，
也必须重新规划，不能退回旧兼容路径。该要求约束新的计划内生成/采用，不把已有 dub
音频、单独配音 SRT 或组合媒体中的配音字幕锁在额外审核之后。

本土化专用的旧 `/video-localization/tts/batch` 提交与回填接口已经删除，不再暴露路由。
历史 Draft 中的 batch 状态字段仍可读取，通用批量
TTS 服务也继续独立存在；但二者都不能作为本土化生产写入口。新的本土化语音只能通过当前
计划分组和共享 `/generate` handoff 提交，worker 回填必须携带提交时冻结的 plan/group 血缘。

开发增量执行不是第二套本土化算法。`development_target` 只在后台开发控制显式
开启时可用，并要求稳定的开发会话 ID 和一个非正式写入节点作为目标。执行器根据
工作流 DAG 计算目标所需的最小上游切片：契约、节点实现、提示词/模型等安全行为
上下文和上游物化血缘都一致时读取已有结果；任一项变化时重算该节点，并使后续血缘
自然失效。目标节点完成后停止，且不得执行 `commit_localization_tracks`。最终
`full` 验收必须从当前正式项目数据重新计算并原子写入，不得读取开发物化结果。

## Media Export

成品视频、音频和独立 SRT 字幕只使用一套 `media_export` operation。调用方先选择
默认目录或打开
系统目录选择器，取得临时 opaque `destination_id`；渲染命令只接受该授权和
`VideoLocalizationMediaExportRequest`，不接受任意路径。只读文件名预览接口复用
统一命名策略生成默认 basename；用户确认或修改后，命令中的 `output_filename`
就是最终落盘名称。后端只校验它不含目录、控制字符且扩展名匹配，不自动加序号、
不覆盖同名文件，也不在渲染后再次改名。请求必须明确音频轨、
配音 lane、压制/独立字幕、尺寸、质量和音频格式，后端不得在执行时重新猜测页面显示状态。
字幕导出只保留与所选 ASR、本土化、配音或双语字幕有关的字段，输出扩展名固定为
`.srt`；它和媒体成品一样由后台任务直接写入授权目录，不再走浏览器 Blob 下载。

operation 的公共结果只包含文件名、文件大小、导出类型，以及媒体导出适用的混合轨道
数量；绝对目录仅在
用户主动选择目录的响应和导出弹窗中显示。渲染先在授权目录写隐藏 partial 文件；发布前同时
持有进程内 Draft 写锁与 SQLite `BEGIN IMMEDIATE` 跨进程发布边界，重新读取权威 Project，
再执行所选字幕的结构与时间质量门，并核对只包含实际渲染内容和媒体字节的 Draft 输入指纹，
通过后才原子发布。CQC/计划/审计元数据不参与该指纹，自动质检写回不能打断导出；音频字节、
片段位置、源裁切或字幕投影变化仍会阻止发布。单独配音 SRT 走同一发布边界，并在响应前再次核对字幕投影。
失败、取消或输入变化不保留 partial 文件。视频进度来自 ffmpeg 的实际输出时间，
弹窗与任务侧边栏读取同一个
operation 状态；不得维护独立的前端估算进度。

## Quality Gate

`quality_gate` 记录是否可提交 TTS 或导出生产 JSON：

- `status`
- `pending_issues`
- `blockers`
- `warnings`
- `checked_at`

保存草稿和导出 production JSON 时，后端必须自动重算质量门，并覆盖客户端传入的旧 `quality_gate`。导出可以包含阻断明细；新的计划内 TTS 提交必须在存在 blocker 时拒绝提交。
本土化来源是否仍然有效，必须用当前草稿重新建立完整 source-lock 指纹，并与提交本土化
时保存的同契约指纹比较；不得拿更低层的时间线指纹与 source-lock 指纹交叉比较。
视频烧录所选字幕轨时，必须先执行与对应 SRT 导出相同的硬门槛，并且该检查发生在
缓存命中判断之前；不能出现字幕文件因来源过期被拒绝、同一字幕却仍能烧进视频的
两套标准。

计划内 TTS 提交前的硬阻断：

- cue 缺少时间码。
- cue 缺少 `speaker_id`。
- cue 绑定的 `speaker_id` 不存在。
- cue 缺少英文字幕、中文字幕或 TTS 台词。
- `clone_from_source` 路线缺少干净参考音。
- 参考音未独立 ASR。
- 混合说话未拆分，也没有显式标记 `preserve_original_audio`。
- 云端 fallback 或云端 voiceclone 未经人工确认。

V1 issue code 使用稳定英文枚举，前端显示中文 `message`：

- `CUE_TIMECODE_MISSING`
- `CUE_SPEAKER_MISSING`
- `CUE_SPEAKER_NOT_FOUND`
- `MIXED_SPEAKER_NEEDS_SPLIT`
- `EN_SUBTITLE_MISSING`
- `ZH_SUBTITLE_MISSING`
- `TTS_TEXT_MISSING`
- `TTS_TEXT_NOT_NORMALIZED`
- `REFERENCE_CLIP_MISSING`
- `REFERENCE_CLIP_NOT_FOUND`
- `REFERENCE_NOT_FROM_CLEAN_VOCALS`
- `REFERENCE_NOT_CLEAN`
- `REFERENCE_ASR_NOT_VERIFIED`

## Export

`GET /api/projects/{project_id}/video-localization/export` 返回生产 JSON，并额外包含：

- `project_id`
- `project_name`
- `exported_at`
- `export_summary`

导出 JSON 必须保留：

- `schema_version`
- 源素材信息。
- 模型/引擎参数证据。
- 说话人、参考音和 cue。
- 三轨文本。
- TTS 结果回写字段。
- 质量门结果。

## 当前非目标与兼容边界

早期 V1 计划曾把“不新增数据库表、不重启服务、不移动资产”列为实施限制；这些是当时
单批开发边界，不是当前领域契约。当前实现已经使用 Project repository revision、
operation/step/attempt/artifact ledger、snapshot projection 和 lifecycle cleanup job，
并通过显式 reset/delete command 管理项目包。因此不得再用旧 V1 清单判断当前架构。

当前仍成立的非目标是：

- 不建立第二套与 Project Draft 竞争的当前业务权威；ledger、projection 和 snapshot
  必须各自声明运行状态、读模型或恢复副本职责。
- 不用页面或 queue 私有分支复制 `/generate` 的高级参数和预设规则；TTS 通过明确
  handoff contract 复用共享能力。
- TTS preview 只服务“打开语音合成页后继续调整”的 handoff，不创建 workflow。
  视频本土化页直接复用历史参数时，不经过 preview：页面使用稳定 submission ID 调用正式
  handoff reserve；服务端完成选择校验并登记 workflow 后，页面立即显示“准备中”，再继续
  prepare 参考音，随后 `/generate` 复用同一身份入队。选择校验未通过时不显示假任务。
  workflow 登记后的准备、提交或队列错误统一写回服务端失败任务，排队、运行、失败、取消、
  删除和刷新恢复也都以服务端 workflow 为准。响应超时只按该身份读取后台事实，不重试提交。
- TTS 任务列表是 Project Draft 内 `tts_tasks` 的只读轻量投影。轮询不得加载或保存完整
  Draft；生成完成后，音频采用、时间线片段与 workflow 成功状态必须一次原子写入，
  不保留单独的“正在放轨”持久化阶段。
- 不把 candidate 音频或字幕命名为 final。
- 不在缺少干净、来源明确的参考音时强行克隆。
- 不自动重放结果未知的付费 Provider 请求；必须查询、裁决或由用户明确重试。
- 不在普通读取、目录同步或后台进度中移动/删除用户资产；只有显式 lifecycle command
  可以执行，并必须遵守 durable cleanup、tombstone 和重启恢复语义。


### Dubbing completion scope

The read-only `GET /api/projects/{project_id}/video-localization/dubbing/completion` returns `dubbing-completion-v1`. Optional millisecond bounds describe the requested delivery range; omitted bounds cover the source video. Execution completion and modern readiness consume the same physical projection. `complete_with_warnings` distinguishes material coverage from missing quality evidence; it never asserts human acceptance. Valid lane-1 capacity deferrals are reported separately and keep delivery `incomplete`; `automated_production_status=resolved` means the remaining gap is manual timing, so continuation must not generate another take. Unfinished material returns explicit target, clip and workflow identifiers for Agent continuation. See the domain README for evidence retention and incremental dubbing-caption ownership.
