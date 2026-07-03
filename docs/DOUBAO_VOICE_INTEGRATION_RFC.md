# Doubao Voice Cloud Integration RFC

调研日期：2026-07-01

## 目标

把火山引擎豆包语音能力接入 Voice Studio，形成一套可解释、可测试、可持续扩展的云端语音能力层。

本 RFC 不把豆包简单视为一个 TTS 引擎。豆包语音覆盖语音合成、声音复刻、音色设计、音色管理、ASR、长文本合成、实时语音、播客、同传、妙记和机器翻译。首期只接入能直接服务当前 TTS / 配音 / 音色库 / ASR 校对工作流的能力，其余应用级能力后置。

首期目标：

- 官方音色 TTS：用豆包官方 speaker 合成短文本。
- 复刻音色合成：使用已训练好的豆包 `speaker_id` 合成文本。
- 声音复刻训练与查询：上传参考音频训练云端音色，并查询状态。
- ASR 极速识别：用于参考音频转写、字幕初稿和 TTS 结果校对。
- 设置、鉴权、日志和错误提示：不影响本地引擎，云端上传有明确提示。

非首期目标：

- 端到端实时语音。
- 语音播客。
- 同声传译。
- 语音妙记。
- 机器翻译。
- 旧版 V1 音色批量迁移工具。

## 已确认产品决策

- 豆包能力在 UI 中按能力拆成多个 engine/profile，不做一个“大杂烩”入口。
- 豆包声音复刻不是本地即时 reference audio TTS。它是“上传参考音频 -> 训练云端音色 -> 得到 `speaker_id` -> 后续用 `speaker_id` 合成”。
- 本地参考音频不会自动上传到豆包。只有用户明确选择豆包声音复刻训练或云端 ASR 时，才发送对应音频。
- 已训练的豆包云端音色应进入音色库，但它不是本地参考音频资产，而是外部 `speaker_id` 绑定。
- 旧 V1 音色升级只作为兼容工具，不出现在首期主流程。新接入默认走 V3 音色训练接口。
- 官方 TTS 2.0 音色可开放 `context_texts`。复刻音色对 `context_texts` 先保守禁用，等实测确认后再打开。
- 豆包 ASR 首期优先接极速版一次返回；标准版 submit/query 和闲时版队列后续补。
- API Key 不回显；请求必须记录 `X-Api-Request-Id`，响应应记录 `X-Tt-Logid` 便于排障。

## 参考资料

官方文档：

- 产品简介：https://www.volcengine.com/docs/6561/163032?lang=zh
- 产品动态：https://www.volcengine.com/docs/6561/113635?lang=zh
- 单向流式语音合成 HTTP：https://www.volcengine.com/docs/6561/2528925?lang=zh
- 单向流式语音合成 WebSocket：https://www.volcengine.com/docs/6561/2534913?lang=zh
- 双向流式语音合成 WebSocket：https://www.volcengine.com/docs/6561/2532486?lang=zh
- 异步长文本语音合成：https://www.volcengine.com/docs/6561/1829010?lang=zh
- 音色训练 HTTP：https://www.volcengine.com/docs/6561/2534906?lang=zh
- 音色查询 HTTP：https://www.volcengine.com/docs/6561/2535742?lang=zh
- 音色升级 HTTP：https://www.volcengine.com/docs/6561/2535751?lang=zh
- 声音复刻 2.0 最佳实践：https://www.volcengine.com/docs/6561/2298705?lang=zh
- 语音指令与标签：https://www.volcengine.com/docs/6561/1871062?lang=zh
- 音色设计 HTTP：https://www.volcengine.com/docs/6561/2277844?lang=zh
- 音色管理 HTTP：https://www.volcengine.com/docs/6561/2235883?lang=zh
- 流式语音识别 WebSocket：https://www.volcengine.com/docs/6561/1354869?lang=zh
- 录音文件识别标准版 HTTP：https://www.volcengine.com/docs/6561/1354868?lang=zh
- 录音文件极速版识别 HTTP：https://www.volcengine.com/docs/6561/1631584?lang=zh
- 录音文件识别闲时版 HTTP：https://www.volcengine.com/docs/6561/1840838?lang=zh
- 端到端实时语音大模型：https://www.volcengine.com/docs/6561/1594356?lang=zh
- 语音播客大模型：https://www.volcengine.com/docs/6561/1668014?lang=zh
- 同声传译 2.0：https://www.volcengine.com/docs/6561/1756902?lang=zh
- 语音妙记：https://www.volcengine.com/docs/6561/1798094?lang=zh
- 机器翻译大模型：https://www.volcengine.com/docs/6561/2306735?lang=zh

本地参考：

- `docs/MIMO_V2_5_CLOUD_API_RFC.md`
- `docs/ARCHITECTURE_REFACTOR_ROADMAP.md`
- `backend/app/services/engine_manifests.py`
- `backend/app/services/engine_policy.py`
- `backend/app/services/engine_request_builder.py`
- `backend/app/services/mimo_client.py`
- `frontend/src/routes/settings/+page.svelte`

## 官方能力摘要

### 语音合成 TTS

豆包语音合成支持中、英、日、西等多语种与多种方言口音。官方主推豆包语音合成模型 2.0，强调上下文情绪理解、自然韵律和个性化表达。

可用接口形态：

| 接口 | 路径 | 适合场景 | 首期建议 |
| --- | --- | --- | --- |
| 单向流式 HTTP | `/api/v3/tts/unidirectional` | 一次输入文本，流式返回音频 | 首期接入 |
| 单向流式 WebSocket | `/api/v3/tts/unidirectional/stream` | 一次输入文本，WebSocket 收音频 | 后续 |
| 双向流式 WebSocket | `/api/v3/tts/bidirection` | 流式输入文本、低延迟返回音频 | 后续 |
| 异步长文本 | `/api/v3/tts/submit` + `/api/v3/tts/query` | 10 万字符长文本，服务端保存 7 天 | P6 |

常用参数：

- `speaker`：官方音色或复刻音色 ID。
- `model`：复刻音色时使用，默认 `seed-tts-2.0-standard`。
- `audio_params.format`：`mp3`、`pcm`、`ogg_opus`、`wav`。
- `audio_params.sample_rate`：`8000`、`16000`、`22050`、`24000`、`32000`、`44100`、`48000`。
- `audio_params.speech_rate`：语速，范围 `[-50, 100]`，`100` 约等于 2 倍速，`-50` 约等于 0.5 倍速。
- `audio_params.loudness_rate`：音量，范围 `[-50, 100]`。
- `post_process.pitch`：音调，范围 `[-12, 12]`。
- `context_texts`：语音指令；官方 TTS 2.0 音色可用，复刻音色页面口径不一致，首期先禁用。
- `context_texts` 通过 `req_params.additions` 传入；实测服务端要求 `additions` 是 JSON 字符串，例如 `{"context_texts":["语速慢一点"]}`，不能直接传对象。
- 语音标签：可在正文句前写方括号自由描述，例如 `[怒目圆睁，冲着你大声怒吼]`。它不是 OmniVoice 那种固定 `[surprise-ah]` / `[sigh]` 标签清单，首期不做快捷按钮；停顿、惊讶、哭腔等先通过 `context_texts` 或自然语言标签表达。
- `section_id`：跨包语义保持或多轮上下文。

资源 ID：

- 官方 TTS 2.0：`seed-tts-2.0`。
- 声音复刻 2.0：`seed-icl-2.0`。

### 声音复刻

声音复刻链路：

```text
本地参考音频 -> voice_clone 训练 -> get_voice 查询 -> 保存 speaker_id -> 用 TTS 合成
```

训练接口：

- 路径：`POST https://openspeech.bytedance.com/api/v3/tts/voice_clone`
- 鉴权：新版控制台使用 `X-Api-Key`。
- 请求体：
  - `speaker_id`：必填。自定义音色时固定传 `custom_speaker_id`。
  - `custom_speaker_id`：后付费自定义音色名称。
  - `audio.data`：base64 音频。
  - `audio.format`：`wav`、`mp3`、`ogg`、`m4a`、`aac`、`pcm`。
  - `text`：参考文本，可用于校验音频与文本差异。
  - `language`：语种枚举。
  - `extra_params.demo_text`：试听文本，4 到 300 字。
  - `extra_params.enable_audio_denoise`：是否降噪。
  - `extra_params.disable_volume_normalization`：是否关闭音量归一化。
- 限制：上传文件最大 10MB。

查询接口：

- 路径：`POST https://openspeech.bytedance.com/api/v3/tts/get_voice`
- 用途：查询已训练音色状态。

响应字段：

- `speaker_id`：云端音色 ID。
- `status`：
  - `0` NotFound
  - `1` Training
  - `2` Success
  - `3` Failed
  - `4` Active
- `status = 2` 或 `4` 时可调用 TTS 合成。
- `available_training_times`：剩余训练次数。
- `speaker_status[].model_type`：复刻 2.0 为 `5`。
- `speaker_status[].demo_audio`：试听音频，有效期 1 小时，建议下载保存。

最佳实践：

- 训练音频建议 14 到 30 秒，wav 格式。
- 低噪声、单人、清晰、单轨。
- 情绪尽量稳定，除非目标是高表现力角色音。
- 对中英混合场景，prompt 中最好同时覆盖中英文。

### 音色升级

接口：

- 路径：`POST https://openspeech.bytedance.com/api/v3/tts/upgrade_voice`
- 用途：把旧 V1 训练接口生成的音色升级到 V3 通用音色。
- 说明：如果已经使用 V3 音色训练接口，无需调用升级接口。

首期处理：

- 不放进主流程。
- 作为未来“旧账号/旧音色迁移工具”处理。
- 如果用户导入的云端音色只有旧 V1 信息，可在音色详情页提示可升级。

### 音色设计

接口：

- 路径：`POST https://openspeech.bytedance.com/api/v3/tts/voice_design`
- 鉴权：新版控制台使用 `X-Api-Key`。

用途：

- 不上传参考音频。
- 用文字或图片描述音色，生成可试听的定制音色。

关键参数：

- `speaker_id`：唯一音色代号，控制台购买。
- `text`：试听文本，限制 300 字。
- `prompt.text_prompt`：文字音色描述，例如“女性，语速中等偏快，语调低沉有力”。
- `prompt.image_prompt.image_url` 或 `image_bytes`：图片提示。
- `language`：中文或英文。

首期处理：

- 可以先不做。
- 做时应独立为 `doubao-voice-design`，不要混入普通 TTS 或声音复刻。
- 建议只做短句试听，不直接进入批量生产。

### 音色管理

音色管理接口与 `openspeech.bytedance.com` 的新版 API Key 鉴权不同。它走：

- 域名：`open.volcengineapi.com`
- Service：`speech_saas_prod`
- Version：`2023-11-07`
- 鉴权：火山 AK/SK 签名。

主要能力：

- 分页查询 SpeakerID 状态：`BatchListMegaTTSTrainStatus`
- 查询状态、到期时间、剩余训练次数、实例信息、别名、模型类型详情。

首期处理：

- 不做完整 AK/SK 管理。
- 训练/查询先用 `voice_clone` 与 `get_voice`。
- 音色管理后续作为“云端音色库同步”能力单独实现。

### 语音指令与标签

官方能力：

- 语音指令：控制整体情绪、方言、语气、语速、音调等。
- 引用上文：输入只理解不朗读的上文，让模型承接语境情绪。
- 语音标签：在句子前加表情、心理、动作描述，例如 `[怒目圆睁，冲着你大声怒吼]`。

注意：

- 文档页说明语音标签可用于特定官方音色或声音复刻 2.0 模型复刻后的音色。
- 语音标签是自由描述式标签，不是当前可稳定枚举的一组按钮。除非后续官方给出稳定标签表并完成真实账号 smoke test，否则不要照 OmniVoice 的方式加“停顿/惊讶/叹气”等快捷标签按钮。
- TTS API 文档又说明复刻音色暂不支持 `context_texts`。
- 因此首期策略是：官方 TTS 2.0 音色开放 `context_texts`；复刻音色先禁用，待实测后再调整。

### ASR 语音识别

豆包 ASR 主要接口：

| 能力 | 接口 | 适合场景 | 首期建议 |
| --- | --- | --- | --- |
| 流式识别 WebSocket | `/api/v3/sauc/bigmodel*` | 实时字幕、语音输入 | 后续 |
| 录音文件标准版 | `/api/v3/auc/bigmodel/submit` + query | 长音频、稳定离线转写 | P5.2 |
| 录音文件极速版 | `/api/v3/auc/bigmodel/recognize/flash` | 2 小时内、100MB 内、一次返回 | 首期 |
| 录音文件闲时版 | `/api/v3/auc/bigmodel/idle/submit` + query | 大批量低优先级离线任务 | 后续 |

极速版限制：

- 音频时长不超过 2 小时。
- 音频大小不超过 100MB。
- 支持 `WAV`、`MP3`、`OGG OPUS`。
- 可传 `audio.url` 或 `audio.data`。
- 资源 ID：`volc.bigasr.auc_turbo`。

标准版常用能力：

- 自动标点。
- ITN 数字规整。
- 语义顺滑。
- 说话人聚类。
- 双声道识别。
- 多语种与方言识别。

首期用途：

- 参考音频自动转写，补 `reference_text`。
- 视频本土化字幕初稿。
- TTS 生成结果 ASR 反校验。
- 长文本合成抽检。

### 异步长文本 TTS

接口：

- Submit：`POST https://openspeech.bytedance.com/api/v3/tts/submit`
- Query：`POST https://openspeech.bytedance.com/api/v3/tts/query`

能力：

- 单次最大 10 万字符。
- 合成音频服务端保存 7 天。
- 查询返回音频 URL，URL 有效期 1 小时。
- 支持官方音色和复刻音色。
- 支持 mp3、ogg_opus、pcm、wav。
- 支持语速、音量、多情感、多语种、句级时间戳、mix 音色。

首期处理：

- 不接入普通生成按钮。
- 后续作为 `doubao-longform-tts`，接入长文本任务队列。

### 应用级能力

这些能力应作为独立业务域或工作台，不应塞进普通 TTS 引擎：

- 端到端实时语音：低延迟语音到语音对话，WebSocket，支持 O/O2.0/SC/SC2.0 版本。
- 语音播客：输入长文、URL、文件或主题，生成双人播客音频。
- 同声传译：实时 S2S/S2T 翻译，支持说话人音色复刻。
- 语音妙记：音视频转写、翻译、待办、问答、总结、章节。
- 机器翻译：文本翻译能力，可服务视频本土化文本环节，但不是语音生成引擎。

## 当前本地实现

当前 Voice Studio 已经有多引擎基础：

- 引擎注册：`backend/app/services/engine_manifests.py`
- 引擎策略：`backend/app/services/engine_policy.py`
- 请求构造：`backend/app/services/engine_request_builder.py`
- 引擎 facade：`backend/app/services/engine_provider.py`
- 云端 MiMo client：`backend/app/services/mimo_client.py`
- 设置页：`frontend/src/routes/settings/+page.svelte`
- 引擎中心：`frontend/src/routes/engine-hub/+page.svelte`

现有 MiMo 云端能力已经拆成：

- `mimo-v2.5-tts-preset`
- `mimo-v2.5-tts-voicedesign`
- `mimo-v2.5-tts-voiceclone`
- `mimo-v2.5-asr`

豆包应复用这种拆分思路，但不能照搬 MiMo 的 OpenAI-compatible payload。豆包的 API 是火山自有协议，需要单独 `doubao_client.py`。

## 完成后与当前有什么不同

| 维度 | 当前 | 完成后 |
| --- | --- | --- |
| 豆包能力 | 无 | 云端 TTS、复刻、ASR 可用 |
| 引擎列表 | 本地引擎 + MiMo | 增加豆包 profiles |
| 云端音色 | 仅 MiMo voice/prompt | 支持豆包官方 speaker 与云端 `speaker_id` |
| 声音复刻 | 本地 reference audio 即时生成为主 | 豆包走训练/查询/合成三段式 |
| 音色库 | 本地参考音频为主 | 可保存豆包云端音色绑定 |
| ASR | 本地/计划中 MiMo ASR | 增加豆包极速 ASR |
| 隐私提示 | MiMo voiceclone 上传提醒 | 豆包训练/ASR/云端合成分别提示 |
| 错误定位 | 本地异常为主 | 记录 request id 和 logid |

## 建议 engine/profile 设计

### `doubao-tts-preset`

用途：官方 speaker 短文本 TTS。

能力：

- `cloud_api`
- `preset_voice`
- `emotion_control`
- `natural_language_control`
- `multilingual`
- `subtitle_timestamp`

参数：

- `doubao_speaker`
- `audio_format`
- `sample_rate`
- `speech_rate`
- `loudness_rate`
- `pitch`
- `explicit_language`
- `context_texts`
- `section_id`
- `aigc_watermark`
- `aigc_metadata_enabled`

### `doubao-tts-voiceclone`

用途：用已训练成功的豆包云端 `speaker_id` 合成。

能力：

- `cloud_api`
- `voice_clone`
- `multilingual`
- `subtitle_timestamp`

参数：

- `doubao_speaker_id`
- `custom_speaker_id`
- `audio_format`
- `sample_rate`
- `speech_rate`
- `loudness_rate`
- `pitch`
- `explicit_language`
- `section_id`

限制：

- 只接受已保存且状态为 `Success` 或 `Active` 的豆包云端音色。
- 首期不开放 `context_texts`。
- 默认 resource id 为 `seed-icl-2.0`。

### `doubao-voice-clone-train`

用途：上传参考音频训练云端音色。

它不是普通 TTS engine，更像音色库动作或 workflow。

参数：

- `speaker_id`
- `custom_speaker_id`
- `reference_audio_path`
- `audio_format`
- `reference_text`
- `language`
- `demo_text`
- `enable_audio_denoise`
- `disable_volume_normalization`

输出：

- `speaker_id`
- `status`
- `available_training_times`
- `demo_audio`
- `model_type`
- `language`
- `create_time`
- `logid`

### `doubao-asr-flash`

用途：极速录音文件识别。

能力：

- `cloud_api`
- `speech_recognition`
- `transcription`
- `timestamp`

参数：

- `audio_path` 或 `audio_url`
- `language`
- `enable_itn`
- `enable_punc`
- `enable_ddc`
- `enable_speaker_info`
- `enable_channel_split`

### `doubao-longform-tts`

用途：异步长文本合成。

首期后置，接入 `longform_queue`，不走普通单句生成。

### `doubao-voice-design`

用途：文字/图片音色设计。

首期后置，建议先做短句试听。

## 数据模型建议

### 云端音色绑定

现有音色库需要能表达外部云端音色。建议新增或扩展 engine binding：

```python
class ExternalVoiceBinding(BaseModel):
    provider: Literal["doubao", "mimo"]
    engine_id: str
    mode: Literal["preset_voice", "voice_clone", "voice_design", "reference_audio"]
    external_voice_id: str | None = None
    custom_voice_id: str | None = None
    resource_id: str | None = None
    status: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
```

豆包云端音色建议保存：

- `provider = "doubao"`
- `external_voice_id = speaker_id`
- `custom_voice_id = custom_speaker_id`
- `resource_id = seed-icl-2.0`
- `status = Training | Success | Failed | Active`
- `metadata.demo_audio`
- `metadata.model_type`
- `metadata.language`
- `metadata.available_training_times`
- `metadata.create_time`
- `metadata.last_logid`

### 隐私与授权

需要区分会上传什么：

| 操作 | 上传内容 |
| --- | --- |
| `doubao-tts-preset` | 合成文本、speaker、控制参数 |
| `doubao-tts-voiceclone` | 合成文本、云端 speaker_id、控制参数 |
| `doubao-voice-clone-train` | 参考音频、可选参考文本、试听文本 |
| `doubao-asr-flash` | 待识别音频 |
| `doubao-voice-design` | 试听文本、文字提示词或图片 |

默认策略：

- 设置页总开关：启用豆包云端能力。
- API Key 单独保存，不回显。
- 训练参考音频和 ASR 音频默认二次确认。
- 音色库中标明“云端音色”和“本地参考音频”的区别。

## 功能规划

### P0：RFC 与契约基线

目标：

- 确认豆包能力拆分。
- 固化首期范围。
- 定义引擎 ID、参数、隐私提示和验收标准。

产物：

- `docs/DOUBAO_VOICE_INTEGRATION_RFC.md`
- 后续实现时补充 contract tests。

验收：

- RFC 被确认。
- 首期范围不再扩大到实时语音、播客、同传。

### P1：设置与通用 client

后端：

- 新增 `backend/app/services/doubao_client.py`。
- 新增设置字段：
  - `doubao_enabled`
  - `doubao_base_url`
  - `doubao_api_key_configured`
  - `doubao_default_tts_resource_id`
  - `doubao_default_icl_resource_id`
  - `doubao_upload_confirm`
- 新增 secret 存储：
  - `doubao_api_key`
- 统一请求头：
  - `Content-Type: application/json`
  - `X-Api-Key`
  - `X-Api-Resource-Id`
  - `X-Api-Request-Id`
- 统一响应记录：
  - `X-Tt-Logid`

前端：

- 设置页增加豆包云端设置卡片。
- 引擎中心展示豆包能力但缺 key 时标为不可用。

测试：

- settings schema 兼容测试。
- secret 不回显测试。
- client payload builder 单元测试。

验收：

- 没配置豆包 Key 时，本地引擎和 MiMo 不受影响。
- 设置页能保存豆包开关、base URL、默认 resource id。

### P2：官方音色 TTS

新增：

- `doubao-tts-preset`

后端：

- `engine_manifests.py` 注册引擎。
- `engine_policy.py` 标记为 cloud engine。
- `engine_request_builder.py` 构造豆包 TTS 参数。
- `engine_runner.py` 或云端 runner 调用 `doubao_client.generate_tts_unidirectional_http(...)`。
- 解码 HTTP chunk / base64 音频并保存输出文件。

前端：

- 生成页根据 manifest 显示 speaker、格式、采样率、语速、音量、音调、语音指令。
- 参数 label、placeholder、tooltip 必须优先来自 manifest 或同一处参数定义；不要在页面里为同一字段再写一份硬编码说明。
- 短说明默认放进 `ⓘ` tooltip；页面上只保留真正需要常驻的信息，避免高级参数区域变成说明文档。
- 初始 speaker 可放少量官方常用音色，后续接音色列表。

测试：

- `tests/test_doubao_payloads.py`
- `tests/test_engine_policy.py`
- `tests/test_engine_parameter_contract.py`

验收：

- 可用官方 speaker 合成一段 mp3/wav。
- 失败时提示火山错误码、request id、logid。

### P3：声音复刻训练与查询

新增：

- `doubao-voice-clone-train` workflow。
- `doubao_client.train_voice_clone(...)`
- `doubao_client.get_voice(...)`

当前落地：

- 已在音色库单个音色上提供“训练豆包云端音色”和“刷新豆包状态”入口。
- 已新增 `POST /api/voices/{voice_id}/doubao/clone-train` 和 `POST /api/voices/{voice_id}/doubao/status`。
- 已新增 `GET /api/voices/doubao/cloud`、`POST /api/voices/doubao/cloud/refresh` 和 `DELETE /api/voices/{voice_id}/doubao/binding`，用于查看本地已绑定的豆包云端音色、批量刷新状态和解除本地绑定。
- 已把训练出的豆包 `speaker_id` 保存到音色资产的 `external_provider / external_voice_id / external_status / external_metadata`，并暴露 `doubao-tts-voiceclone` 绑定给 P4 使用。
- 上传前确认由后端强制校验：开启 `doubao_upload_confirm` 时，未传 `confirm_upload=true` 会拒绝请求。
- 训练 payload 中的 `language` 按官方枚举发送；前端和本地音色仍可使用 `zh/en/...` 这类易读值，由 `doubao_client` 统一映射。
- 当前只支持 Voice Studio 本地绑定管理。云端 SpeakerID 删除、续费、订单和资源包管理属于火山控制台相关接口，不能用合成/复刻 API Key 假装完成删除。

后端：

- 校验参考音频存在、格式、大小。
- 支持 `speaker_id` 与 `custom_speaker_id` 两种模式。
- 保存训练结果到音色库。
- 支持刷新训练状态。
- 支持列出和批量刷新本地已绑定的豆包云端音色。
- 支持解除本地绑定；解除绑定不会删除火山云端 SpeakerID。
- `demo_audio` 有效期短，查询成功后可选择下载到本地缓存。

前端：

- 音色库增加“训练豆包云端音色”动作。
- 音色库增加“豆包云端音色”管理区，可集中查看已绑定 speaker、批量刷新状态、单个刷新、解除本地绑定，并提供官方管理入口。
- 云端音色显示状态：训练中、可用、失败、已激活。
- 显示剩余训练次数。

测试：

- 音频格式/大小校验。
- 训练 payload。
- 查询状态映射。
- 音色库保存 external binding。
- 云端音色列表、批量刷新、解除绑定契约测试。

验收：

- 能上传参考音频发起训练。
- 能查询状态。
- 成功后音色库出现豆包云端音色记录。
- 可在音色管理页查看本地已绑定的豆包云端音色；可刷新状态；可解除本地绑定。
- 真正删除云端 SpeakerID 前，必须确认并接入官方控制台相关接口或在火山控制台处理。
- 合同测试覆盖 payload、上传确认、绑定写回和可用状态映射；真实账号 smoke test 需要在有训练额度时执行。

### P4：复刻音色合成

新增：

- `doubao-tts-voiceclone`

当前落地：

- 已注册 `doubao-tts-voiceclone` 合成引擎，生成页会显示为“豆包语音 TTS 2.0 · 声音复刻”。
- 生成页只让该引擎选择已训练可用的豆包云端音色；不展示自定义参考音频上传入口。
- 后端合成时从音色资产读取 `external_voice_id` 作为豆包 `speaker`，默认使用 `doubao_default_icl_resource_id`。
- 后端拒绝本地 `reference_audio_path` 直接走豆包复刻合成，避免用户误以为是即时克隆。

后端：

- 只允许选择 `Success` 或 `Active` 的豆包云端音色。
- 默认 resource id 为 `seed-icl-2.0`。
- 合成时传 `speaker_id`，不再上传参考音频。

前端：

- 生成页的音色选择只显示豆包云端音色。
- 清楚标注“使用已训练的豆包云端音色”。

测试：

- 不允许本地 reference audio 误走 `doubao-tts-voiceclone`。
- 不允许 Training/Failed 音色合成。
- 合成 payload 正确使用 `speaker_id`。

验收：

- 用训练好的 speaker_id 合成新文本。
- 用户不会误以为这是本地即时克隆。

### P5：ASR 极速识别

新增：

- `doubao-asr-flash`
- `doubao_client.transcribe_flash(...)`

后端：

- 支持本地文件转 base64 或音频 URL。
- 返回全文、utterances、words、时间戳。
- 存入 ASR 历史或现有转写任务结构。

前端：

- ASR/参考音频页面可选豆包 ASR。
- 音色库可用豆包 ASR 补 `reference_text`。

测试：

- payload builder。
- 结果解析。
- ASR 任务错误状态。

验收：

- 本地音频可转写。
- 可用于参考音频自动填充文本。

### P6：异步长文本 TTS

新增：

- `doubao-longform-tts`

后端：

- 接入 `longform_queue`。
- submit 后轮询 query。
- 下载 query 返回的音频 URL。
- 处理 URL 1 小时有效和服务端保存 7 天的语义。

前端：

- 长文本生成页提供豆包长文本选项。
- 展示 submit/query 状态。

验收：

- 10k+ 字符长文本可提交并最终下载结果。
- query 并发不会压垮普通 TTS 并发。

### P7：音色设计

新增：

- `doubao-voice-design`

后端：

- 支持 `text_prompt`。
- 图片 prompt 后续再接。

前端：

- 独立短句试听入口。
- 不直接进入批量生成。

验收：

- 输入音色描述和试听文本，生成 demo。

### P8：应用级能力专项

后续单独 RFC：

- Realtime Voice Workbench：端到端实时语音。
- Podcast Generator：语音播客。
- AST / Video Localization：同声传译。
- Minutes / Meeting Notes：语音妙记。
- MT / Localization Text Pipeline：机器翻译。

## 风险与处理

### 官方文档口径不一致

风险：

- 复刻音色是否支持 `context_texts`，不同页面表述不完全一致。

处理：

- 首期保守禁用。
- 写实测脚本，用真实账号验证后再开放。

### 豆包云端复刻和本地复刻语义不同

风险：

- 用户把本地参考音频误选给豆包复刻合成，以为会即时克隆。

处理：

- 训练和合成拆成两个流程。
- 音色库明确标注“豆包云端 speaker_id”。
- `doubao-tts-voiceclone` 不接受 `reference_audio_path`。

### 鉴权体系有多套

风险：

- TTS/复刻/ASR 新版接口多用 `X-Api-Key`，音色管理走 AK/SK 签名。

处理：

- 首期只做 `X-Api-Key` 体系。
- AK/SK 音色管理后续单独实现。
- 页面上的“官方管理”只作为控制台相关接口入口，不把云端删除误实现成本地删除。

### 云端上传合规

风险：

- 参考音频和 ASR 音频会离开本机。

处理：

- 设置页云端总开关。
- 上传前确认。
- 音色资产保留授权状态。

### 长文本并发

风险：

- submit/query 与其他合成接口共享并发。

处理：

- 长文本后置。
- 接入时走队列和限流，不直接用普通生成按钮轮询。

## 建议首期验收清单

- `GET /api/engines` 包含 `doubao-tts-preset`、`doubao-tts-voiceclone`、`doubao-asr-flash`。
- 设置页可配置豆包云端能力和 API Key，Key 不回显。
- 未配置 Key 时豆包引擎显示不可用，本地引擎不受影响。
- 官方 speaker 可生成一段音频文件。
- 可上传 10 到 30 秒参考音频发起音色训练。
- 可查询训练状态。
- 成功后音色库保存豆包云端音色。
- 可用豆包云端 `speaker_id` 合成新文本。
- 可用豆包 ASR 极速版转写本地音频。
- 所有云端上传路径都有明确提示。
- 失败时能看到 request id/logid 或友好错误说明。

## 推荐实施顺序

1. P1：设置与 `doubao_client.py` 骨架。
2. P2：官方音色短文本 TTS。
3. P3：声音复刻训练与查询。
4. P4：复刻音色合成。
5. P5：ASR 极速识别。
6. P6/P7：长文本与音色设计。
7. P8：实时语音、播客、同传、妙记、机器翻译专项。

这样做能让 Voice Studio 先获得最实用的豆包语音生产能力，同时避免把完全不同的实时/播客/同传工作流过早塞进 TTS 页面。

## 每阶段 CQC 执行规则

后续每个阶段都必须按 CQC 走完再进入下一阶段：

1. Check：开发前重新查阅对应官方文档，确认当前日期下接口、字段、限制、资源 ID、示例 payload 和能力边界；若官方文档口径不一致，先记录差异并选择保守实现。
2. Query：用最小请求或沙盒脚本验证关键字段是否真实可用，例如官方 TTS 的 `context_texts`、复刻音色训练参数、ASR 返回结构；不能只凭字段名猜。
3. Copy：开发前先确定 UI 文案、tooltip、示例和错误提示。凡是解释性文字，优先放 `ⓘ` tooltip；只有会影响当前操作判断的信息才常驻显示。
4. Code：只实现已被官方文档或实测确认的能力；未确认的功能先隐藏在计划里，不进入 UI。UI 字段必须消费 manifest/参数定义，避免后端文案和前端硬编码不一致。
5. Verify：完成后跑自动化测试、前端检查、后端编译；涉及云端调用的阶段再做一次真实账号最小探活，并保存 request id / logid / 输出文件信息。
6. Confirm：提交前对照用户截图或需求逐项复核，确认可见文案、tooltip、按钮显隐、结果卡片和 git 状态都符合预期。

阶段化核对表：

| 阶段 | 开发前官方核对 | 完成后验证 |
| --- | --- | --- |
| P1 设置与 client | 鉴权头、resource id、request id、logid、Key 安全要求 | settings schema、secret 不回显、无 Key 时本地引擎不受影响 |
| P2 官方音色 TTS | `/api/v3/tts/unidirectional`、`audio_params`、`context_texts`、支持的官方 speaker | payload 单测、引擎 manifest 检查、真实短句生成 mp3/wav、结果卡片显示官方音色名 |
| P3 训练与查询 | `voice_clone`、`get_voice`、音频格式/时长/次数限制、训练状态码 | 上传前确认、训练请求单测、状态查询单测、真实查询返回可解释 |
| P4 复刻音色合成 | 已训练 `speaker_id` 合成协议、是否支持 `context_texts`、复刻模型 resource id | 禁止误用本地 reference audio、云端 speaker 入库、真实复刻音色短句生成 |
| P5 ASR 极速识别 | 极速版音频格式、大小、语言参数、返回字段 | ASR payload 单测、真实短音频转写、TTS 校对链路不回归 |
| P6 长文本 TTS | submit/query、最大字符、结果保存期、轮询与失败码 | 长文本队列单测、超时/重试测试、真实小长文分段或异步任务探活 |
| P7 音色设计 | 音色设计接口、输入限制、输出 speaker/试听音频结构 | 设计任务单测、试听音频保存、失败提示含 request id/logid |
| P8 应用级能力 | 实时语音、播客、同传、妙记、翻译各自官方产品边界 | 独立 RFC、独立 UI 入口、端到端 smoke test 后再并入主流程 |
