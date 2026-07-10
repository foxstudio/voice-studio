# 视频本土化剪辑台重构任务清单

## 目标

把当前 `/video-localization` 从流程型页面重构为剪辑台型工作台：

- 支持无项目状态下导入视频并创建/恢复项目。
- 导入后自动抽取原音轨，并在时间轴显示。
- 支持人声/背景音乐分离，形成原音轨、人声轨、背景音乐轨。
- 支持字幕轨片段的选择、入出点调整、删除、编辑和侧栏同步。
- 支持项目音色库、保存当前选区为音色、音色参数组、生成版本。
- 支持字幕显示开关、字幕轨选择和字幕样式预览。
- 支持实时保存、刷新恢复、服务/电脑重启后继续编辑。

## 执行原则

- 先做真实数据模型和最小竖切，再做完整交互。
- 不一次性重写整个页面；按批次交付、验证、再推进。
- 不新增数据库表作为第一选择；沿用 `Project.parameters.video_localization`，必要字段先进入 draft schema。
- 所有媒体文件落到项目媒体目录，draft 只保存路径、状态和元数据。
- 前端状态可以乐观更新，但后端保存结果是最终事实。
- 复杂任务优先评估子代理；主 Codex 负责最终判断和落地修改。

## 模型分工建议

| 模型 | 适合任务 |
| --- | --- |
| GPT-5.5 | 跨模块架构决策、数据模型定稿、复杂状态机、最终代码审查 |
| GPT-5.4 | 中等复杂实现、前后端接口联调、交互方案取舍 |
| GPT-5.4-Mini | 样式细化、组件搬迁、文档同步、测试补齐 |
| GPT-5.3-Codex-Spark | 只读调研、文件盘点、小 helper、低风险单文件改造 |

## 批次 0：开工前基线

- [x] 记录当前 `/video-localization` 页面能力和已存在接口。
- [x] 记录当前静态设计稿入口：
  - `frontend/static/design/video-localization-cutting-studio-v2.html`
  - `frontend/static/design/video-localization-cutting-studio-v3-options.html`
- [x] 确认现有 draft schema 与新剪辑台字段差距。
- [x] 确认当前脏工作区，避免误动已有改动。

验收标准：

- 形成字段差距列表。
- 明确第一批真实实现只覆盖哪些功能。

委派建议：

- GPT-5.3-Codex-Spark：只读盘点前端组件、API、schema。
- GPT-5.4-Mini：整理字段差距表。

## 批次 1：项目数据模型和实时保存

- [x] 扩展 `VideoLocalizationDraft` 支持剪辑台需要的 UI 状态：
  - selected cue id
  - timeline zoom / scroll / playhead
  - sidebar mode / collapsed
  - subtitle display mode
  - subtitle style preset
- [x] 扩展项目资产字段：
  - project voice samples
  - voice recipes
  - generated candidates
  - timeline clips
- [x] 设计项目目录结构：
  - [x] `project.json`：每次保存 draft 后同步写入项目目录快照
  - [x] `source/`：源视频
  - [x] `audio/`：源音轨抽取结果
  - [x] `stems/`：人声/背景音乐分离结果
  - [x] `references/`：当前项目音色样音
  - [x] `tts/`：视频本土化批量 TTS 输出
  - [x] `cue-source-audio/`：字幕片段源音频缓存
  - [x] `exports/`：EDL、音频包、合成视频输出
  - [x] `autosave/`：最近项目快照，保留最近 24 个
- [x] 实现前端保存节流：
  - 普通编辑 2-3 秒防抖保存
  - 导入、删除、生成、移动 clip 立即保存
  - 保存状态显示为 `已保存 / 保存中 / 保存失败`
- [x] 刷新页面后恢复当前项目和上次编辑位置。

验收标准：

- 修改字幕文本后刷新页面仍保留。
- 选中的字幕片段、侧栏状态和字幕显示设置能恢复。
- 保存失败时界面有明确提示。

委派建议：

- GPT-5.5：定数据模型和保存策略。
- GPT-5.4：实现 store/API 调用。
- GPT-5.4-Mini：补充类型和测试。

## 批次 2：新版页面骨架和无项目状态

- [x] 将真实 `/video-localization` 页面重构为剪辑台布局骨架。
- [x] 增加顶部项目管理：
  - 新建项目
  - 导入项目
  - 另存为
  - 关闭当前项目
  - 保存状态
- [x] 无项目/无视频时，视频预览区显示导入入口。
- [x] 侧边栏可收起和展开。
- [x] 所有按钮改为紧凑应用式按钮，避免大块按钮。
- [x] 保留现有已可用功能入口，不让真实能力倒退。

验收标准：

- 没有项目时，用户能直接看到导入视频入口。
- 页面刷新无控制台错误。
- 侧栏收起/展开不破坏时间轴宽度。

委派建议：

- GPT-5.4：主实现。
- GPT-5.4-Mini：样式细化和响应式检查。
- GPT-5.3-Codex-Spark：无状态组件拆分。

## 批次 3：导入视频和原音轨时间轴

- [x] 导入视频后创建/更新项目 draft。
- [x] 自动抽取原音轨。
- [x] 原音轨出现在时间轴第一轨。
- [x] 时间轴支持播放指针、缩放、时间刻度。
- [x] 视频播放和时间轴 playhead 同步。

验收标准：

- 导入视频后能看到视频预览和原音轨。
- 刷新页面后原音轨仍可恢复。
- 播放时 playhead 同步移动。

委派建议：

- GPT-5.4：前后端联调。
- GPT-5.3-Codex-Spark：盘点已有 waveform helper 和可复用代码。

## 批次 4：人声轨和背景音乐轨

- [x] 人声轨提供 `生成` 按钮。
- [x] 点击后调用现有人声/背景分离流程。
- [x] 完成后显示人声轨和背景音乐轨。
- [x] 每个轨道支持静音、独奏和音量。
- [x] 播放模式支持：
  - [x] 原音轨
  - [x] 人声 + 背景音乐
  - [x] 人声独奏
  - [x] 背景音乐独奏

验收标准：

- 分离完成后路径写入 draft。
- 三轨时间尺度对齐。
- 静音/独奏状态刷新后可恢复。

委派建议：

- GPT-5.4：实现轨道控制。
- GPT-5.4-Mini：轨道 UI 和状态标签。

## 批次 5：字幕轨编辑

- [x] ASR 结果生成字幕轨。
- [x] 字幕片段可选中。
- [x] 选中字幕后，右侧字幕编辑面板同步显示：
  - 原文/ASR
  - 本土化字幕
  - TTS 文本
- [x] 支持导入外部本土化 SRT，并按时间码更新字幕轨。
- [x] 字幕片段支持：
  - [x] 调整入点/出点
  - [x] 删除
  - [x] 拆分
  - [x] 合并
  - [x] 编辑文本
- [x] 编辑后自动保存。

验收标准：

- 点击时间线字幕片段，侧栏内容准确切换。
- 修改字幕后 timeline chip 和视频预览同步。
- 删除字幕不会破坏 cue 顺序和时间码。

委派建议：

- GPT-5.5：确认 cue 编辑规则和边界。
- GPT-5.4：实现交互。
- GPT-5.4-Mini：补测试和样式。

## 批次 6：视频字幕显示和样式

- [x] 视频预览支持字幕开关。
- [x] 字幕来源可选：
  - 原文/ASR
  - 本土化
  - TTS
  - 多行对照
- [x] 默认策略：
  - 有本土化字幕时显示本土化
  - 没有本土化时显示原文/ASR
- [x] 支持字幕样式预设：
  - 黄字黑描边
  - 半透明黑底
  - 白字轻阴影
  - 白字强描边
- [x] 支持字号、文字颜色、描边颜色、背景透明度、位置。

验收标准：

- 字幕随视频当前时间变化。
- 切换字幕来源立即反映到视频预览。
- 字幕样式设置刷新后可恢复。

委派建议：

- GPT-5.4-Mini：预设样式和 CSS 实现。
- GPT-5.4：字幕显示状态接入。

## 批次 7：项目音色库和保存当前选区

- [x] 侧栏 tabs：
  - 项目音色库
  - 保存当前选区
- [x] 选择人声轨片段后，可保存为项目音色。
- [x] 项目音色字段：
  - title
  - speaker/person
  - emotion
  - tags
  - description
  - source stem
  - start/end/duration
  - audio path
  - cover frame path
  - ASR text / status
- [x] 音色库支持：
  - [x] 试听
  - [x] 查看详情
  - [x] 编辑
  - [x] 删除
  - [x] 搜索/过滤
- [x] 选中音色后，同步到音色生成面板。

验收标准：

- 保存后的音色刷新页面仍存在。
- 点击音色后，下方生成面板显示对应音色。
- 删除音色时引用 cue 有明确处理策略。

委派建议：

- GPT-5.5：确认数据模型和引用规则。
- GPT-5.4：实现保存/编辑/删除。
- GPT-5.3-Codex-Spark：只读整理现有 reference clip 逻辑。

## 批次 8：音色参数组和生成版本

- [x] 每个项目音色支持多个参数组 `voice recipes`。
- [x] 每个参数组支持多个生成版本 `candidates`。
- [x] 参数组字段：
  - name
  - description
  - engine id
  - parameter snapshot
  - tags
  - created from task id
- [x] 生成版本字段：
  - candidate id
  - audio path
  - duration
  - text used
  - task id
  - notes
  - status
- [x] 支持三种生成入口：
  - [x] 一键生成当前台词
  - [x] 带参数去调整
  - [x] 仅带样音去生成
- [x] 生成结果可放入中文配音轨。

验收标准：

- 同一音色可保存多组参数。
- 同一参数组可试听多个版本。
- 一键生成不会复用旧台词，只复用参数。

委派建议：

- GPT-5.5：生成契约和跨页面传参方案。
- GPT-5.4：实现 draft/restore/generate 联动。
- GPT-5.4-Mini：候选列表 UI。

## 批次 9：中文配音轨和 clip 编辑

- [x] 生成音频作为 clip 放入中文配音轨。
- [x] clip 支持移动、裁切、删除。
- [x] 支持撤销/重做。
- [x] clip 入点、出点、源音频裁切范围写入 draft。
- [x] 时间轴状态可导出为 EDL JSON。
- [x] 按 EDL 导出分段音频包：
  - `manifest.json`
  - `dub-track.wav`
  - `segments/*.wav`
- [x] 按 EDL 渲染最终合成视频：
  - [x] 按轨道静音、独奏和音量生成 `mixdown-track.wav`
  - [x] `preserve_original_audio` cue 从人声轨按时间范围回填原声
  - [x] 最终视频复用视频流并写入工作台混音结果

验收标准：

- 移动 clip 后刷新页面保持位置。
- 删除 clip 不删除源生成音频，除非用户明确清理。
- 时间轴状态能导出为可渲染 edit decision list。

委派建议：

- GPT-5.5：时间轴 edit model。
- GPT-5.4：交互实现。
- GPT-5.4-Mini：撤销/重做测试。

## 批次 10：验证和收口

- [x] 单元测试：
  - [x] draft migration / default contract
  - [x] cue edit / validation
  - [x] reference voice save / delete
  - [x] recipe/candidate mapping
- [x] EDL JSON export
- [x] EDL audio package export
- [x] EDL localized video export
- [x] project manifest / autosave snapshot
- [x] 前端 smoke：
  - [x] 页面打开无业务控制台错误（当前仅 favicon.ico 404）
  - [x] 导入入口存在
  - [x] 字幕编辑同步
  - [x] 侧栏收起/展开
  - [x] 时间线高频编辑按钮组：拆分、合并、删除、保存选区为音色、生成到选区
  - [x] 选中字幕/时间线动作会聚焦右侧对应检查器
  - [x] 预览窗口播放模式标签与轨道 M/S 状态同步
  - [x] 视频本土化到语音合成的带参跳转可恢复请求并显示来源
  - [x] 导出音频包入口存在且前端检查通过
  - [x] 导出合成视频入口存在且前端检查通过
  - [x] 真实页面 smoke：Chrome headless 打开 `/video-localization`，关键控件可见，无 page error
- [x] E2E 手工验收：
  - [x] 导入视频
  - [x] 抽取音轨
  - [x] 生成字幕
  - [x] 编辑字幕
  - [x] 保存音色
  - [x] 生成配音候选
    - 真实 smoke 项目：`0f5e97a59300`
    - 真实生成任务：`7e47cc0fd98f`
    - cue / generated candidate / timeline clip 均回填到 `/Users/foxmacstudio/VoiceStudio/outputs/7e47cc0fd98f.wav`
    - ffprobe：2.24s, pcm_s16le, 24kHz, mono
    - UI smoke：`/video-localization?project_id=0f5e97a59300` 可见候选和时间线状态，无 page error
  - [x] 刷新恢复
  - [x] 真实服务导出音频包 zip：
    - `manifest.json`
    - `dub-track.wav`
    - `segments/001_clip_001.wav`
  - [x] 真实服务导出合成视频：
    - ffprobe 验证包含 video stream
    - ffprobe 验证包含 audio stream
  - [x] 真实服务保存项目快照：
    - `/Users/foxmacstudio/VoiceStudio/projects/1c0e4438050e/video_localization/project.json`
    - `/Users/foxmacstudio/VoiceStudio/projects/1c0e4438050e/video_localization/autosave/20260709-110412-737076-project.json`
  - [x] 真实服务保存当前选区为项目音色：
    - `/Users/foxmacstudio/VoiceStudio/projects/1c0e4438050e/video_localization/references/ref_speaker_01_cue_0001.wav`

验收标准：

- 所有已实现批次有明确测试或手工验证记录。
- 文档同步到实际实现。
- 未完成能力在 UI 中显示为禁用或清晰的待实现状态。

## 批次 11：剪辑台交互和闭环加固

- [x] 时间尺改为剪辑软件式主刻度、次刻度和贯穿式播放指针。
- [x] 当前时间与动态电平合并到轨道标题顶部，时间居中、dB 刻度作为背景显示。
- [x] 轨道顺序统一为字幕、原音、人声、背景声音、中文配音，标题列与内容列同步排列。
- [x] 轨道名称默认显示为文本，点击后才进入编辑模式。
- [x] 音量改为紧凑扬声器按钮、滑杆和 dB 读数。
- [x] 音频电平仅在播放时动态显示，暂停后立即归零。
- [x] 时间线支持鼠标滚轮锚点缩放、`+` / `-` 快捷键和 `Shift + 滚轮` 横向滚动。
- [x] 当前工作台所有可见按钮均有“名称 + 结果/影响”悬浮说明。
- [x] 自由音频选区与字幕时间解耦，保存项目音色时传递真实入点和出点。
- [x] 保存项目音色时自动抽取选区中点视频帧作为封面。
- [x] TTS 候选支持独立试听和采用，采用后同步替换 cue 与中文配音 clip。
- [x] 中文配音轨进入视频同步预览。
- [x] 波形和爆音判断使用绝对峰值，爆音阈值按 `-1 dBFS` 判断。
- [x] 自动保存增加 UI 增量更新、草稿版本冲突检测和冲突合并重试。
- [x] 数据库草稿缺失或损坏时，从 `project.json` / autosave 快照恢复。
- [x] 最终导出尊重轨道状态和 cue 音频路线。
- [x] 浏览器真实播放采样：播放中电平连续变化约 `38.5% - 49.1%`，暂停或播放结束后归零，播放指针同步推进。
- [x] 验证：`svelte-check` 0 error / 0 warning，前端 production build 通过，视频本土化测试 `64 passed`。

## 历史：第一轮开工范围（已完成）

第一轮只做批次 0-3：

1. 数据模型差距盘点。
2. 新版页面骨架。
3. 无项目导入状态。
4. 导入视频后显示原音轨。
5. ASR 字幕轨只做显示和选中，不做完整拖拽编辑。

不在第一轮做：

- 完整 clip 剪辑器。
- 音色参数组。
- 生成版本管理。
- 最终视频渲染。
- 完整字幕样式导出。

第一轮完成后，页面就有可验证的真实底座；后续再逐批叠加人声分离、字幕编辑、项目音色库和生成版本。
