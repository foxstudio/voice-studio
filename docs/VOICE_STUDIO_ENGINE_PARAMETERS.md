# Voice Studio 引擎参数说明

本文档给后台 agent、批量脚本和二次开发使用。原则是：只把当前引擎真正会消费的参数当成有效参数；`GenerateRequest` 里存在的通用字段，不代表每个引擎都会使用。

## 快速结论

- IndexTTS v2 是本地声音克隆主力，支持语速、采样、分段、情绪和扩散类参数。
- OmniVoice 是本地多语言/声音设计引擎，当前接入只消费语言、参考音频或声音描述、语速。
- EmotiVoice 是本地中文情感 TTS，当前接入使用官方开源预训练说话人和中文情绪提示。
- F5-TTS 是本地参考音频 TTS，当前接入要求已授权参考音频和对应 `ref_text`，不会自动调用 Whisper 听写参考音频。
- CosyVoice SFT 是本地官方预训练音色 TTS，当前接入使用官方 SFT 预置 speaker。
- CosyVoice Zero-Shot 是本地参考音频复刻 TTS，使用音色库参考音频和对应 `ref_text`；不要和 SFT 预置音色混用。
- F5-TTS 和 CosyVoice 默认使用外部持久 worker 复用已加载模型；排查问题时可分别设置 `VOICE_STUDIO_F5_PERSISTENT_WORKER=0` 或 `VOICE_STUDIO_COSYVOICE_PERSISTENT_WORKER=0` 回退一次性子进程。
- MiMo V2.5 TTS 是云端 OpenAI 兼容接口，已拆成 preset、voicedesign、voiceclone 三个引擎；官方 TTS 超参是 `temperature` 和 `top_p`。
- MiMo voiceclone 没有独立的数值 `speed` API 参数。需要调语速时，把“语速稍慢/语速偏快”等写进 `style_instruction`，或在合成文本里使用官方音频标签。
- MiMo ASR 只做音频转文字，当前有效参数是 `language`。

官方依据：

- MiMo TTS V2.5 使用指南：https://platform.xiaomimimo.com/docs/zh-CN/usage-guide/speech-synthesis-v2.5
- MiMo 模型超参：https://platform.xiaomimimo.com/docs/zh-CN/quick-start/model-hyperparameters
- MiMo ASR API：https://platform.xiaomimimo.com/docs/zh-CN/api/audio/Speech-Recognition
- EmotiVoice README / voice wiki：https://github.com/netease-youdao/EmotiVoice
- F5-TTS README / CLI 参数：https://github.com/SWivid/F5-TTS
- CosyVoice README / inference API：https://github.com/FunAudioLLM/CosyVoice

## 官方音色库和本地音色库

本项目把“音色库”分成两类：

- 本地音色库 `GET /api/voices`：保存用户上传或导入的参考音频文件，供 IndexTTS、OmniVoice、F5-TTS、CosyVoice Zero-Shot、MiMo VoiceClone 等参考音色路径使用。
- 官方/模型预置音色：保存为对应引擎的参数选项，例如 `emotivoice.speaker_id`、`cosyvoice-sft.speaker_id`、`mimo_voice`。这些不是本地参考音频，不会出现在 `VoiceAsset.reference_audio_ids` 里。

当前状态：

- EmotiVoice 官方提供 2000+ voices；本项目已加载一组精选 `speaker_id` 到 `GET /api/engines` 的 `emotivoice.parameter_schema`，来源是本地 `/Users/foxmacstudio/Projects/tts-engine-lab/EmotiVoice/data/youdao/text/README.md`。
- CosyVoice SFT 官方预置 speaker 已加载为 `cosyvoice-sft.speaker_id`，包括 `中文女`、`中文男`、`粤语女`、`日语男`、`韩语女`。
- F5-TTS 没有固定官方 speaker 库；它主要使用 `ref_audio + ref_text` 做参考音色生成。
- CosyVoice Zero-Shot 没有固定本地 speaker 选择；它使用音色库里的参考音频和对应台词。
- MiMo preset 是云端官方预置音色目录；MiMo voiceclone 才使用本地参考音频并上传云端。

## 服务入口

启动后端：

```bash
cd /Users/foxmacstudio/Projects/mlx-indextts
PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

常用接口：

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/engines
curl http://127.0.0.1:8000/api/voices
curl http://127.0.0.1:8000/api/presets
```

长文本生成前建议先做规划：

```bash
curl -X POST http://127.0.0.1:8000/api/generate/plan \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "要评估的合成文本",
    "engine_id": "indextts-v2",
    "planner_mode": "auto",
    "target_format": "mp3"
  }'
```

规划接口只返回建议，不提交生成任务。后台 agent 应根据 `recommended_action` 和 `requires_user_confirmation` 决定下一步；需要用户确认时，必须先解释分段、校对、合并或云端上传风险。

单条生成：

```bash
curl -X POST http://127.0.0.1:8000/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "要合成的文本",
    "engine_id": "indextts-v2",
    "voice_id": "819316179a4a",
    "emotion_mode": "follow_reference",
    "language": "zh",
    "output_format": "mp3"
  }'
```

## 参数矩阵

| 引擎 | 类型 | 有效参数 | 无效或不要传的重点 |
| --- | --- | --- | --- |
| `indextts-v2` | 本地 TTS | `voice_id`/`reference_audio_path`, `ref_text`, `language`, `emotion_mode`, `emotion`, `emo_alpha`, `speed`, `temperature`, `top_p`, `top_k`, `max_text_tokens_per_segment`, `interval_silence`, `diffusion_steps`, `cfg_rate`, `output_format` | MiMo 专属 `mimo_voice`, `style_instruction`, `voice_design_prompt`, `optimize_text_preview` |
| `omnivoice` | 本地 TTS | `voice_id` 或 `emotion_text`, `ref_text`, `language`, `speed`, `output_format` | `temperature`, `top_p`, `top_k`, 分段参数、IndexTTS 情绪向量、MiMo 专属参数 |
| `emotivoice` | 本地 TTS | `speaker_id`, `prompt`, `output_format` | 本地 `voice_id`、F5 `nfe_step/cfg_strength`、MiMo 专属参数 |
| `f5-tts` | 本地 TTS | `voice_id`/`reference_audio_path`, `ref_text`, `speed`, `nfe_step`, `cfg_strength`, `target_rms`, `cross_fade_duration`, `remove_silence`, `seed`, `output_format` | 没有参考文本时不要自动听写；MiMo 专属参数、IndexTTS 情绪向量无效 |
| `cosyvoice-sft` | 本地 TTS | `speaker_id`, `speed`, `output_format` | 本地 `voice_id`、参考文本、IndexTTS 情绪向量、MiMo 专属参数 |
| `cosyvoice-zero-shot` | 本地 TTS | `voice_id`/`reference_audio_path`, `ref_text`, `speed`, `output_format` | `speaker_id`、F5 采样参数、IndexTTS 情绪向量、MiMo 专属参数 |
| `mimo-v2.5-tts-preset` | 云端 TTS | `mimo_voice`, `style_instruction`, `temperature`, `top_p`, `output_format` | 本地 `voice_id`、`speed`、`top_k`、分段参数、扩散参数 |
| `mimo-v2.5-tts-voicedesign` | 云端 TTS | `voice_design_prompt`, `optimize_text_preview`, `temperature`, `top_p`, `output_format` | 本地 `voice_id`、`speed`、`top_k`、分段参数、扩散参数 |
| `mimo-v2.5-tts-voiceclone` | 云端 TTS | `voice_id` 或 `reference_audio_path`, `style_instruction`, `temperature`, `top_p`, `output_format` | `speed`、`top_k`、分段参数、扩散参数、`mimo_voice` |
| `mimo-v2.5-asr` | 云端 ASR | `language` | TTS 参数全部无效 |
| `qwen3-asr-mlx` | 本地 ASR | `language` | TTS 参数全部无效 |

## 参数发现和预设

前端和后台 agent 都应该把 `GET /api/engines` 的 `parameter_schema` 当作有效参数来源。生成页也是按当前 `engine_id` 过滤参数控件、结果参数弹窗和合成预设。

常用参数组合使用 `GET /api/presets` 获取。返回的每个预设都带 `engine_id`，调用方必须只把同引擎预设展示给当前引擎。

自定义预设接口：

```bash
curl -X POST http://127.0.0.1:8000/api/presets \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "课程慢讲",
    "scene": "教程 / 长文旁白",
    "description": "降低语速，停顿更清楚。",
    "engine_id": "indextts-v2",
    "sample_text": "这是一段预设示例文本。",
    "parameters": {
      "speed": 0.9,
      "temperature": 0.8,
      "top_p": 0.8,
      "output_format": "wav"
    },
    "tags": ["课程", "慢讲"]
  }'
```

内置预设是只读示例；自定义预设的 `preset_id` 以 `custom_` 开头，可用 `PATCH /api/presets/{preset_id}` 和 `DELETE /api/presets/{preset_id}` 管理。

## 合成文本和提示词

文本处理工具分两类：

- 通用文本工具：`清洗文本`、`数字规范`、`分句预览` 只处理输入文本，不改变模型参数，适用于所有 TTS 引擎。
- 模型提示工具：根据当前引擎显示，例如 MiMo 的风格/停顿标签、IndexTTS 的拼音标注、OmniVoice 的非语言标签。后台 agent 不应跨模型复用这些提示。

推荐写法：

- IndexTTS v2：正文直接放 `text`。多音字或读音不稳时，可在正文里加拼音标注；语速、情绪和采样仍用独立参数。
- OmniVoice：正文放 `text`。未选择参考音色时，用 `emotion_text` 描述声音；短句可尝试非语言标签。
- EmotiVoice：正文放 `text`，官方预置说话人放 `speaker_id`，情绪/风格提示放 `prompt`。它不使用本地 `voice_id` 做复刻。
- F5-TTS：正文放 `text`，参考音色用 `voice_id` 或 `reference_audio_path`，必须提供准确 `ref_text`。`ref_text` 为空时官方会自动 ASR；本项目会拦截为 `REFERENCE_TEXT_REQUIRED`，避免后台 agent 触发额外下载和慢等待。
- CosyVoice SFT：正文放 `text`，官方预置音色放 `speaker_id`。它不使用本地 `voice_id`。
- CosyVoice Zero-Shot：正文放 `text`，参考音色用 `voice_id` 或 `reference_audio_path`，必须提供准确 `ref_text`。目标文本明显短于参考文本时，官方实现会提示效果可能下降。
- MiMo preset：正文放 `text`，官方预置音色放 `mimo_voice`，整体风格、语速、情绪写到 `style_instruction`。
- MiMo VoiceDesign：正文放 `text`，音色描述写到 `voice_design_prompt`，需要官方润色播报文本时打开 `optimize_text_preview`。
- MiMo VoiceClone：正文放 `text`，参考音色用 `voice_id` 或 `reference_audio_path`，语速和表演方式写到 `style_instruction`；没有独立数值 `speed`。

MiMo 支持自然语言控制和音频标签，但这些标签只应写进 MiMo 合成文本或风格指令，不应当成 IndexTTS/OmniVoice 的通用参数。

## 长文本规划与校对

详细规划见 `docs/LONGFORM_TTS_VERIFICATION_RFC.md`。

当前第一阶段已提供 `POST /api/generate/plan`，使用规则 planner 判断文本是否适合直接生成或建议分段。该接口预留了 LLM 字段，但当前不接入 LLM：

- `planner`: 当前为 `rules`，未来可为 `llm`
- `llm_available`: 当前为 `false`
- `recommended_action`: 推荐的生成策略
- `requires_user_confirmation`: 为 `true` 时，前端和 agent 必须向用户确认
- `segments`: 系统建议分段
- `privacy_notice`: 本地/云端处理提醒

默认建议：

| 引擎 | 提示阈值 | 强提醒阈值 | 推荐动作 |
| --- | ---: | ---: | --- |
| `omnivoice` | 120 字 | 220 字 | `split_generate` |
| `indextts-v2` | 300 字 | 600 字 | `split_verify_merge` |
| `mimo-v2.5-tts-preset` | 600 字 | 1200 字 | `split_verify_merge` |
| `mimo-v2.5-tts-voiceclone` | 400 字 | 800 字 | `split_verify_merge` |
| `mimo-v2.5-tts-voicedesign` | 400 字 | 800 字 | `split_generate` |

当前第二阶段已提供单条结果校对接口：

```bash
curl -X POST http://127.0.0.1:8000/api/evaluations/tts-verification \
  -H 'Content-Type: application/json' \
  -d '{
    "result_id": "生成结果 result_id",
    "expected_text": "原始合成文本",
    "asr_engine_id": "qwen3-asr-mlx",
    "language": "zh"
  }'
```

如果 agent 已经有转录文本，也可以不触发 ASR，直接比较：

```bash
curl -X POST http://127.0.0.1:8000/api/evaluations/tts-verification \
  -H 'Content-Type: application/json' \
  -d '{
    "expected_text": "原始合成文本",
    "transcript_text": "ASR 转录文本",
    "language": "zh"
  }'
```

返回 `status`：

- `passed`：转录内容覆盖原文，可以继续使用或进入合并流程。
- `warning`：基本覆盖，但建议人工复听。
- `failed`：存在缺句或漏段风险，agent 应报告 `missing_segments` 并建议重试或分段生成。
- `skipped`：缺少必要文本，无法校对。

当前第三阶段已提供长文本父任务接口：

```bash
curl -X POST http://127.0.0.1:8000/api/longform/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "generate_request": {
      "text": "完整长文本",
      "engine_id": "indextts-v2",
      "voice_id": "本地音色 voice_id",
      "language": "zh",
      "output_format": "mp3"
    },
    "segments": [
      {"index": 1, "text": "第一段。", "char_count": 4, "segment_reason": "sentence_boundary"}
    ],
    "verify_enabled": true,
    "merge_enabled": true,
    "max_retries": 2,
    "stop_merge_on_verification_failed": true
  }'
```

查询与重试：

- `GET /api/longform`：列出长文本父任务。
- `GET /api/longform/{longform_task_id}`：查看父任务、每段子任务、校对结果和合并状态。
- `POST /api/longform/{longform_task_id}/retry-failed`：只重试失败段。
- `GET /api/longform/{longform_task_id}/download`：下载合并音频。

agent 规则：

1. 文本超过阈值时，先调 `/api/generate/plan`。
2. `requires_user_confirmation=true` 时，先问用户是否分段生成。
3. MiMo voiceclone 上传参考音频仍需按设置确认。
4. 不要把明显长文本直接塞进 `/api/generate` 后报告成功。
5. 对正式输出或长文本结果，生成成功后调用 `/api/evaluations/tts-verification` 校对内容完整性。
6. 用户同意分段时，优先使用 `/api/longform/generate`，不要由 agent 自己循环拼多个 `/api/generate`。
7. 长文本编排完成后，只有段落生成和校对都满足条件，才能报告最终成功。

## 参数含义

### 通用输出

- `output_format`：最终导出的格式，支持 `wav`、`mp3`、`flac`。单条生成会先拿到模型音频，再按需转码。
- `language`：语言提示。ASR 可用 `auto`、`zh`、`en`；TTS 本地引擎也会用它辅助多语言生成。

### IndexTTS v2

- `voice_id`：音色库里的本地参考音色。IndexTTS v2 生成必须有参考音频。
- `reference_audio_path`：直接指定参考音频路径；通常由 `voice_id` 自动解析。
- `ref_text`：参考音频对应台词，能帮助模型更准地理解参考音频。
- `emotion_mode`：`follow_reference` 表示不额外叠加情绪；`emotion_vector` 表示使用 `emotion` 和 `emo_alpha`。
- `emotion`：8 种情绪之一：`happy`、`sad`、`angry`、`afraid`、`disgusted`、`melancholic`、`surprised`、`calm`。
- `emo_alpha`：情绪强度，0 到 1。越高表演感越强，长文本通常不宜过高。
- `speed`：语速倍率。低于 1 更慢，高于 1 更快。
- `temperature`：采样随机性。低值更稳，高值变化更多。
- `top_p`：核采样范围。默认 0.8 偏稳。
- `top_k`：每一步保留候选数量。过大更自由，过小更保守。
- `max_text_tokens_per_segment`：长文本自动切段长度。
- `interval_silence`：切段之间补的静默毫秒数。
- `diffusion_steps`：扩散步数。更多步通常更慢。
- `cfg_rate`：引导强度，控制生成贴合条件的力度。

### OmniVoice

- `voice_id`：使用音色库里的本地参考音频做声音克隆。
- `emotion_text`：未选择参考音色时，作为声音设计/生成指令传给 OmniVoice。
- `ref_text`：参考音频台词，可提升克隆稳定性。
- `language`：`auto` 或具体语言代码。
- `speed`：语速倍率。

### EmotiVoice

- `speaker_id`：官方开源模型里的预训练说话人 ID，例如 `8051`。
- `prompt`：中文情绪提示，例如 `开心`、`悲伤`、`愤怒`。
- 官方推理格式是 `speaker|style_prompt/emotion_prompt/content|phoneme|content`。本项目 runner 会自动调用 `frontend.py` 生成 phoneme。
- 当前精选 speaker 只是官方 2000+ voices 的子集；完整列表在本地 EmotiVoice `data/youdao/text/README.md` 和官方 voice wiki。
- 当前通过外部 venv 调用 `/Users/foxmacstudio/Projects/tts-engine-lab/EmotiVoice`，可用 `VOICE_STUDIO_EMOTIVOICE_ROOT` 覆盖路径。

### F5-TTS

- `voice_id`：音色库里的本地参考音频。F5 需要参考音频做音色迁移。
- `ref_text`：参考音频对应台词，当前接入要求必填；为空时后端会报 `REFERENCE_TEXT_REQUIRED`，避免自动下载/启动 Whisper。
- `speed`：语速倍率。
- `nfe_step`：采样步数。官方默认是 `32`，本地快速试听可降低到 `16`。
- `cfg_strength`：引导强度。官方默认是 `2.0`，过高可能让发音不自然。
- `target_rms`：响度目标，官方默认是 `0.1`。
- `cross_fade_duration`：分段交叉淡化秒数，官方默认是 `0.15`。
- `remove_silence`：生成后移除较长静音。官方 basic 示例默认关闭。
- 当前通过外部 venv 调用 `/Users/foxmacstudio/Projects/tts-engine-lab/F5-TTS`，默认使用本地 ModelScope 权重，可用 `VOICE_STUDIO_F5_TTS_ROOT` 覆盖路径。

### CosyVoice SFT

- `speaker_id`：官方 SFT 预置音色，例如 `中文女`、`中文男`、`粤语女`。
- `speed`：语速倍率。
- 当前通过外部 venv 调用 `/Users/foxmacstudio/Projects/tts-engine-lab/CosyVoice`，可用 `VOICE_STUDIO_COSYVOICE_ROOT` 覆盖路径。

### CosyVoice Zero-Shot

- `voice_id`：音色库里的本地参考音频。CosyVoice Zero-Shot 使用它做参考音色生成。
- `reference_audio_path`：直接指定参考音频路径；通常由 `voice_id` 自动解析。
- `ref_text`：参考音频对应台词，必填。缺失时后端会报 `REFERENCE_TEXT_REQUIRED`。
- `speed`：语速倍率。
- 官方实现会在目标文本明显短于 prompt text 时给出效果下降提醒；后台 agent 应先用较完整的测试句确认贴近度。
- 当前通过外部 venv 调用 `/Users/foxmacstudio/Projects/tts-engine-lab/CosyVoice`，可用 `VOICE_STUDIO_COSYVOICE_ROOT` 覆盖路径。

### MiMo V2.5 TTS Preset

- `mimo_voice`：官方预置音色。当前本地 catalog 包含 `mimo_default`、`冰糖`、`茉莉`、`苏打`、`白桦`、`Mia`、`Chloe`、`Milo`、`Dean`。
- `style_instruction`：自然语言风格指令，放入 MiMo 官方要求的 `user` message。可写语速、情绪、角色、方言等。
- `temperature`：官方超参，默认 0.6，范围 0 到 1.5。
- `top_p`：官方超参，默认 0.95，范围 0.01 到 1.0。

### MiMo V2.5 TTS VoiceDesign

- `voice_design_prompt`：音色描述，必填，放入 `user` message。
- `optimize_text_preview`：官方可选项，放入 `audio.optimize_text_preview`。开启后会根据音色描述优化目标播报文本；需要严格保留原文时关闭。
- `temperature`：官方超参，默认 0.6，范围 0 到 1.5。
- `top_p`：官方超参，默认 0.95，范围 0.01 到 1.0。

### MiMo V2.5 TTS VoiceClone

- `voice_id`：音色库里的本地参考音色。只有本次生成选择的参考音频会上传到 MiMo。
- `reference_audio_path`：直接指定 wav/mp3 参考音频路径。官方限制 Base64 后不超过 10 MB。
- `style_instruction`：自然语言风格指令。要调语速时写在这里，例如“语速稍慢，停顿自然”。
- `temperature`：官方超参，默认 0.6，范围 0 到 1.5。
- `top_p`：官方超参，默认 0.95，范围 0.01 到 1.0。

## MiMo payload 对照

MiMo TTS 的目标合成文本放在 `assistant` message；风格/音色描述放在 `user` message。voiceclone 的参考音频放在 `audio.voice`，格式是 `data:{MIME_TYPE};base64,...`。

本地 `mimo_client.build_tts_payload()` 只会向 MiMo 发送：

- `model`
- `messages`
- `audio.format`
- `audio.voice`，仅 preset 和 voiceclone 使用
- `audio.optimize_text_preview`，仅 voicedesign 按需使用
- `temperature`
- `top_p`

因此，即使外部 agent 在通用请求里传了 `speed`、`top_k` 或分段参数，MiMo 请求也不会消费这些字段。

## 推荐 agent 用法

1. 先调 `GET /api/engines`，读取目标引擎的 `parameter_schema`。
2. 调 `GET /api/presets`，只展示 `engine_id` 与当前引擎一致的预设。
3. 只展示和传入 schema 中存在的参数；滑块类参数同时允许精确数值输入，但仍要遵守 schema/后端校验范围。
4. 按引擎写合成文本提示。MiMo 的风格写 `style_instruction` 或 `voice_design_prompt`，不要给 MiMo 传本地 `speed/top_k/segment` 参数。
5. 调用 MiMo voiceclone 前，确认用户允许本次参考音频上传云端。
6. 生成后轮询任务状态，只有 `status: success` 且存在 `result_audio_id` 时才报告成功。
7. 批量任务优先使用 `scripts/voice_studio_batch.py`；每段可以覆盖 `engine_id`、`voice_id`、`speed`、`emotion`、`style_instruction` 等有效参数。
