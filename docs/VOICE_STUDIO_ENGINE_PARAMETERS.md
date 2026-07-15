# Voice Studio 引擎参数说明

本文档给后台 agent、批量脚本和二次开发使用。原则是：只把当前引擎真正会消费的参数当成有效参数；`GenerateRequest` 里存在的通用字段，不代表每个引擎都会使用。

## 快速结论

- IndexTTS v2 是本地声音克隆主力，支持语速、采样、分段、情绪和扩散类参数。
- OmniVoice 是本地多语言/声音设计引擎，当前接入消费语言、参考音频或声音描述、语速、固定时长和官方生成采样参数；语速/固定时长在本项目里用生成后音频拉伸实现，优先保证文本完整覆盖。
- EmotiVoice 是本地中英情感 TTS，当前接入使用官方开源预训练说话人和情绪提示；不是上传音频即可复刻的路线。
- Confucius4-TTS MLX int8 是网易有道子曰4 TTS 的 Apple Silicon MLX 量化版本，当前接入使用音色库或自定义参考音频，支持中文、英文、越南语、日语、韩语、泰语 6 种语言的跨语种声音克隆和情绪迁移。未列出的语言在当前 MLX runtime 会错误回退为英文提示，页面已不再提供。
- F5-TTS 是本地参考音频 TTS，当前接入要求已授权参考音频和对应 `ref_text`，不会自动调用 Whisper 听写参考音频。
- CosyVoice SFT 是本地官方预训练音色 TTS，当前接入使用官方 SFT 预置 speaker。
- CosyVoice Zero-Shot 是官方 `CosyVoice-300M` 的本地参考音频复刻路径，使用音色库参考音频和对应 `ref_text`；本机未安装模型时会显示不可用，不会和 SFT 预置音色混用。
- Qwen3-TTS MLX 0.6B 是实验接入的本地千问 TTS：不选参考音色时用 CustomVoice 预置 speaker，选择本地音色时用 Base 模型做声音复刻。当前 MLX 运行时的 CustomVoice 路线支持 `instruct` 演绎指令；Base 复刻和 VoiceDesign 路线不会接收它。上游 MLX 运行时尚未原生实现 `speed`，本项目会在生成后用不变调处理兑现语速效果。
- F5-TTS 和 CosyVoice 默认使用外部持久 worker 复用已加载模型；排查问题时可分别设置 `VOICE_STUDIO_F5_PERSISTENT_WORKER=0` 或 `VOICE_STUDIO_COSYVOICE_PERSISTENT_WORKER=0` 回退一次性子进程。
- MiMo V2.5 TTS 是云端 OpenAI 兼容接口，已拆成 preset、voicedesign、voiceclone 三个引擎。官方 TTS 使用说明没有给出 `temperature`、`top_p` 的 TTS 专属默认值、范围或效果证据，因此本项目不会把它们做成可调滑块或发送给服务。
- MiMo 非流式 TTS 当前只开放官方示例明确覆盖的 `wav` 输出；等有效 Key 完成 MP3/FLAC 实测后，再考虑开放更多格式。
- MiMo voiceclone 没有独立的数值 `speed` API 参数。需要调语速时，把“语速稍慢/语速偏快”等写进 `style_instruction`，或在合成文本里使用官方音频标签。
- MiMo ASR 只做音频转文字，当前有效参数是 `language`。

官方依据：

- MiMo TTS V2.5 使用指南：https://platform.xiaomimimo.com/docs/zh-CN/usage-guide/speech-synthesis-v2.5
- MiMo 模型超参：https://platform.xiaomimimo.com/docs/zh-CN/quick-start/model-hyperparameters
- MiMo ASR API：https://platform.xiaomimimo.com/docs/zh-CN/api/audio/Speech-Recognition
- EmotiVoice README / voice wiki：https://github.com/netease-youdao/EmotiVoice
- Confucius4-TTS MLX int8：https://huggingface.co/beyoru/Confucius4-TTS-mlx-int8
- Confucius4 MLX runtime PR：https://github.com/Hert4/mlx-audio/pull/88
- F5-TTS README / CLI 参数：https://github.com/SWivid/F5-TTS
- CosyVoice README / inference API：https://github.com/FunAudioLLM/CosyVoice
- Qwen3-TTS Apple Silicon PoC：https://github.com/kapi2800/qwen3-tts-apple-silicon
- Qwen3-TTS MLX 8-bit models：https://huggingface.co/mlx-community

## 官方音色库和本地音色库

本项目把“音色库”分成两类：

- 本地音色库 `GET /api/voices`：保存用户上传或导入的参考音频文件，供 IndexTTS、OmniVoice、F5-TTS、CosyVoice Zero-Shot、MiMo VoiceClone 等参考音色路径使用。
- 官方/模型预置音色：保存为对应引擎的参数选项，例如 `emotivoice.speaker_id`、`cosyvoice-sft.speaker_id`、`mimo_voice`。这些不是本地参考音频，不会出现在 `VoiceAsset.reference_audio_ids` 里。

当前状态：

- EmotiVoice 官方提供 2000+ voices；本项目已加载一组精选 `speaker_id` 到 `GET /api/engines` 的 `emotivoice.parameter_schema`，来源是本地 EmotiVoice 仓库的 `data/youdao/text/README.md`。
- CosyVoice SFT 官方预置 speaker 已加载为 `cosyvoice-sft.speaker_id`，包括 `中文女`、`中文男`、`粤语女`、`日语男`、`韩语女`。
- F5-TTS 没有固定官方 speaker 库；它主要使用 `ref_audio + ref_text` 做参考音色生成。
- Confucius4-TTS 没有固定官方 speaker 库；它使用 `reference_audio_path` 或音色库 `voice_id` 的参考音频做声音克隆和情绪迁移，不强制 `ref_text`。
- CosyVoice Zero-Shot 没有固定本地 speaker 选择；它使用音色库里的参考音频和对应台词。
- Qwen3-TTS CustomVoice 有模型预置 speaker；Base 模式使用音色库里的参考音频做本地声音复刻。
- MiMo preset 是云端官方预置音色目录；MiMo voiceclone 才使用本地参考音频并上传云端。

## 服务入口

启动后端：

```bash
cd /path/to/voice-studio
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
| `indextts-v2` | 本地 TTS | `voice_id`/`reference_audio_path`, `emotion_mode`, `emotion`, `emo_alpha`, `speed`, `temperature`, `top_p`, `top_k`, `max_text_tokens_per_segment`, `interval_silence`, `diffusion_steps`, `cfg_rate`, `output_format` | 当前 MLX 路线不消费 `language` 或 `ref_text`；MiMo 专属 `mimo_voice`、`style_instruction`、`voice_design_prompt`、`optimize_text_preview` 也无效 |
| `omnivoice` | 本地 TTS | `voice_id` 或 `emotion_text`, `ref_text`, `language`, `speed`, `duration`, `diffusion_steps`, `guidance_scale`, `audio_chunk_duration`, `audio_chunk_threshold`, `output_format` | `temperature`, `top_p`, `top_k`, IndexTTS 情绪向量、MiMo 专属参数 |
| `emotivoice` | 本地 TTS | `speaker_id`, `prompt`, `output_format`；中文/英文及混读由官方本地前端自动判断 | 本地 `voice_id`、数值 `speed`、F5 `nfe_step/cfg_strength`、MiMo 专属参数 |
| `confucius4-mlx-int8` | 本地 TTS | `voice_id`/`reference_audio_path`, `language`, `temperature`, `top_p`, `top_k`, `repetition_penalty`, `diffusion_steps`, `cfg_rate`, `seed`, `output_format` | `ref_text` 不强制；`speed`、F5/CosyVoice speaker、MiMo 专属参数无效 |
| `f5-tts` | 本地 TTS | `voice_id`/`reference_audio_path`, `ref_text`, `speed`, `nfe_step`, `cfg_strength`, `target_rms`, `cross_fade_duration`, `sway_sampling_coef`, `fix_duration`, `remove_silence`, `seed`, `output_format` | 没有参考文本时不要自动听写；MiMo 专属参数、IndexTTS 情绪向量无效 |
| `cosyvoice-sft` | 本地 TTS | `speaker_id`, `speed`, `output_format` | 本地 `voice_id`、参考文本、IndexTTS 情绪向量、MiMo 专属参数 |
| `cosyvoice-zero-shot` | 本地 TTS | `voice_id`/`reference_audio_path`（官方限制不超过 30 秒）, `ref_text`, `speed`, `output_format` | `speaker_id`、音量/音调/温度/CFG/采样步数、F5 采样参数、IndexTTS 情绪向量、MiMo 专属参数 |
| `qwen3-tts-mlx-0.6b` | 本地 TTS | `speaker_id`, `style_instruction`（仅预置音色）、`voice_id`/`reference_audio_path`, `ref_text`, `language`, `voice_design_prompt`, `speed`, `temperature`, `top_p`, `top_k`, `repetition_penalty`, `max_tokens`, `output_format` | `style_instruction` 不用于参考音色复刻或 VoiceDesign；`cfg_scale`、`ddpm_steps` 无效。预置音色、声音设计、参考音色复刻三条路线互斥 |
| `mimo-v2.5-tts-preset` | 云端 TTS | `mimo_voice`, `style_instruction`, `output_format=wav` | 本地 `voice_id`、`speed`、`temperature`、`top_p`、`top_k`、分段参数、扩散参数 |
| `mimo-v2.5-tts-voicedesign` | 云端 TTS | `voice_design_prompt`, `optimize_text_preview`, `output_format=wav` | 本地 `voice_id`、`speed`、`temperature`、`top_p`、`top_k`、分段参数、扩散参数 |
| `mimo-v2.5-tts-voiceclone` | 云端 TTS | `voice_id` 或 `reference_audio_path`, `style_instruction`, `output_format=wav` | `speed`、`temperature`、`top_p`、`top_k`、分段参数、扩散参数、`mimo_voice` |
| `doubao-tts-preset` | 云端 TTS | `speaker_id`, `language`, `style_instruction`, `speed`, `loudness_rate`, `pitch_rate`, `sample_rate`, `bit_rate`, `enable_subtitle`, `silence_duration`, `aigc_watermark` | 不能上传本地参考音频作为一次性音色；语音指令只用于官方预置音色 |
| `doubao-tts-voiceclone` | 云端 TTS | 已训练成功的云端 `speaker_id`, `speed`, `loudness_rate`, `pitch_rate`, `sample_rate`, `bit_rate`, `enable_subtitle`, `silence_duration`, `aigc_watermark` | 不支持直接上传本地参考音频做即时复刻，也不支持预置音色的 `style_instruction`；训练参考音频只支持中文或英文 |
| `doubao-seed-audio-1.0` | 云端音频生成 | `input_mode`, 文字提示、最多 3 个音频参考或 1 张图片参考，`format`, `sample_rate`, `speech_rate`, `loudness_rate`, `pitch_rate`, `enable_subtitle`, 显式/隐式来源标记 | 音频参考和图片参考不能混用；最长 120 秒；只支持单次任务，不支持批量或长文本拼接 |
| `mimo-v2.5-asr` | 云端 ASR | `language` | TTS 参数全部无效 |
| `qwen3-asr-mlx` | 本地 ASR | `language` | TTS 参数全部无效 |

## 声音来源与授权标签

TTS 调用方可以选择系统音色库、调用方提供的参考声音，或模型预设/声音设计。不要把某个固定 `voice_id` 写死成唯一入口。

- 系统音色库：传 `voice_id`。后端会从音色库解析参考音频和已登记的 `license_status`、`tags`。
- 调用方提供参考声音：传 `reference_audio_path`。如果同时传了 `voice_id`，本次生成优先使用 `reference_audio_path`，`voice_id` 只作为任务上下文保留。
- 模型预设声音：传模型自己的 `speaker_id` 或 `mimo_voice`。
- 声音设计：传 `emotion_text` 或 `voice_design_prompt`，由模型按文本描述生成声音。

外部参考声音可附带这些可选留痕字段：

- `voice_source`: `voice_library`、`reference_audio`、`model_preset`、`voice_design`。
- `reference_audio_license_status`: `self_voice`、`authorized`、`company_authorized`、`test_only`、`unknown` 等。
- `reference_audio_tags`: 标签数组，例如 `["agent:course-video", "授权"]`。

当前授权机制以音色库资产为中心：`license_status` 会影响音色库声音在云端 voiceclone 里的可用性；官方预设音色不依赖本地音色库授权；临时 `reference_audio_path` 由调用方声明授权并负责确认。涉及 MiMo voiceclone 这类云端上传时，调用方必须先确认参考声音授权和用户同意。

外部 agent 可以注册新声音到音色库：先 `POST /api/voices/upload` 再 `POST /api/voices`，或直接用 `POST /api/voices/register` 一步上传并创建音色资产。注册时建议写入 `reference_text`、`license_status` 和来源标签。

## 参数发现和预设

前端和后台 agent 都应该把 `GET /api/engines` 的 `parameter_schema` 当作有效参数来源。生成页也是按当前 `engine_id` 过滤参数控件、结果参数弹窗和合成预设。

常用参数组合使用 `GET /api/presets` 获取。返回的每个预设都带 `engine_id`，调用方必须只把同引擎预设展示给当前引擎。

当前生成页的“一键重置参数”会按当前引擎恢复下表默认值，但保留正文和已选参考音色，避免误操作丢失输入：

| 引擎 | 重置后的主要默认值 | 当前内置预设 |
| --- | --- | --- |
| `indextts-v2` | `speed=1.0`, `temperature=0.8`, `top_p=0.8`, `top_k=30`, `emotion=跟随参考音色`, `emo_alpha=0.6`, `max_text_tokens_per_segment=120`, `interval_silence=200`, `diffusion_steps=25`, `cfg_rate=0.7`, `max_mel_tokens=1500`, `repetition_penalty=10` | 贴近参考音色、轻微开心、强情绪短句、教程慢讲、信息流快讲、长文本剪辑 |
| `omnivoice` | `language=auto`, `speed=1.0`, `duration=0`, 声音描述为空 | OmniVoice 女青年设计 |
| `emotivoice` | `speaker_id=8051`, `prompt=开心` | 清晰女声开心、浑厚男声中立、活泼女声兴奋 |
| `confucius4-mlx-int8` | `language=zh`, `temperature=0.8`, `top_p=0.8`, `top_k=30`, `repetition_penalty=10`, `diffusion_steps=25`, `cfg_rate=0.7`, `seed=0` | 中文情绪迁移、英文跨语种、日文跨语种、稳定低随机 |
| `f5-tts` | `speed=1.0`, `nfe_step=32`, `cfg_strength=2.0`, `target_rms=0.1`, `cross_fade_duration=0.15`, `sway_sampling_coef=-1`, `fix_duration=0`, `remove_silence=false` | 官方默认复刻、快速试听、短句去静音 |
| `cosyvoice-sft` | `speaker_id=中文女`, `speed=1.0` | 中文女声、中文男声、粤语女声 |
| `cosyvoice-zero-shot` | `speed=1.0` | 参考音色复刻、慢速清晰 |
| `qwen3-tts-mlx-0.6b` | `speaker_id=Vivian`, `style_instruction=''`（Normal tone）, `language=chinese`, `speed=1.0`, `temperature=0.7`, `top_p=0.9`, `top_k=50`, `repetition_penalty=1.1`, `max_tokens=1200` | Qwen3 官方基准、Qwen3 课程慢讲、Qwen3 复刻讲述；安装 VoiceDesign 模型后显示 Qwen3 声音设计 |
| `mimo-v2.5-tts-preset` | `mimo_voice=mimo_default`, `style_instruction=''`, `output_format=wav` | MiMo 稳定口播、MiMo 温柔女声 |
| `mimo-v2.5-tts-voicedesign` | `voice_design_prompt=中年男性，声线沉稳偏正式，吐字工整，语速适中。`, `optimize_text_preview=false`, `output_format=wav` | MiMo 角色试音 |
| `mimo-v2.5-tts-voiceclone` | `style_instruction=''`, `output_format=wav` | MiMo 复刻讲述 |

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

Qwen3-TTS 内置预设来源口径：

- 官方基准：保留 Qwen3 Apple Silicon PoC 的 CustomVoice 路线、Vivian 预置音色、Normal `speed=1.0`；采样参数采用社区常见的 `temperature=0.7`, `top_p=1.0`, `top_k=30`, `repetition_penalty=1.15`, `max_tokens=512`，比 Hugging Face 模型默认 `temperature=0.9` 更稳。
- 课程慢讲：使用官方 PoC README 的 Slow `speed=0.8` 建议，并降低随机性到 `temperature=0.65`, `top_p=0.92`, `top_k=35`，适合解释型中文短句。
- 声音设计：只在本机存在 `models/Qwen3-TTS-12Hz-0.6B-VoiceDesign-8bit` 时展示；只填 `voice_design_prompt`，不混用 `speaker_id` 或参考音色，避免 VoiceDesign、CustomVoice、Base 三条路线互相覆盖。
- 复刻讲述：需要当前已选择本地音色库或自定义参考音色；Qwen3 Base 复刻路线不消费 `style_instruction`。

## 合成文本和提示词

文本处理工具分两类：

- 通用文本工具：`清洗文本`、`数字规范`、`分句预览` 只处理输入文本，不改变模型参数，适用于所有 TTS 引擎。
- 模型提示工具：根据当前引擎显示，例如 MiMo 的风格/停顿标签、IndexTTS 的拼音标注、OmniVoice 的非语言标签。后台 agent 不应跨模型复用这些提示。

推荐写法：

- IndexTTS v2：正文直接放 `text`。多音字或读音不稳时，可在正文里加拼音标注；语速、情绪和采样仍用独立参数。
- OmniVoice：正文放 `text`。未选择参考音色时，用 `emotion_text` 描述声音；短句可尝试官方非语言标签，例如 `[laughter]`、`[sigh]`、`[sniff]`、`[question-ah]`、`[surprise-wa]`、`[dissatisfaction-hnn]`。生成页同时保留 `[pause]`、`[cough]` 作为历史兼容快捷标签；耳语/小声属于声音设计描述。实测同格式非按钮标签也可能生效，例如 `[question-ha]`、`[surprise-huh]`、`[dissatisfaction-mm]`，但这类写法必须先试听确认，不应当作为稳定合同批量交付。
- OmniVoice 标签和正文拟声词二选一：写了 `[question-ah]` 就不要再写“啊”，写了 `[dissatisfaction-hnn]` 就不要再写“哼”，否则模型可能执行标签后又把正文拟声词读一遍。推荐 `[question-ah] 这句真的要这么说吗？`，不要写 `[question-ah] 啊，这句真的要这么说吗？`。
- EmotiVoice：正文放 `text`，官方预置说话人放 `speaker_id`，情绪/风格提示放 `prompt`。它不使用本地 `voice_id` 做复刻。
- Confucius4-TTS：正文放 `text`，参考音色用 `voice_id` 或 `reference_audio_path`，目标语言放 `language`。它会从参考音频迁移音色和情绪，不要求参考台词；长文本建议分段试听。
- F5-TTS：正文放 `text`，参考音色用 `voice_id` 或 `reference_audio_path`，必须提供准确 `ref_text`。`ref_text` 为空时官方会自动 ASR；本项目会拦截为 `REFERENCE_TEXT_REQUIRED`，避免后台 agent 触发额外下载和慢等待。
- CosyVoice SFT：正文放 `text`，官方预置音色放 `speaker_id`。它不使用本地 `voice_id`。
- CosyVoice Zero-Shot：正文放 `text`，参考音色用 `voice_id` 或 `reference_audio_path`，必须提供准确 `ref_text`。目标文本明显短于参考文本时，官方实现会提示效果可能下降。
- Qwen3-TTS：正文放 `text`。三条声音路线互斥：未选择本地/自定义参考音色且 `voice_design_prompt` 为空时，使用 `speaker_id` 走 CustomVoice 预置音色；这条路线可在 `style_instruction` 写一句演绎要求，例如“温柔耐心，像在讲解课程”，留空就是 Normal tone。填写 `voice_design_prompt` 时走 VoiceDesign 声音设计；选择 `voice_id` 或 `reference_audio_path` 时走 Base 参考音色复刻，必须提供准确 `ref_text`。后两条路线不提交演绎指令。当前 MLX 上游未原生实现语速，本项目会在成片后不变调处理，所以语速是项目已验证的后处理能力。
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
| `omnivoice` | 150 字 | 300 字 | `split_verify_merge` |
| `indextts-v2` | 300 字 | 600 字 | `split_verify_merge` |
| `confucius4-mlx-int8` | 24 字 | 48 字 | `split_verify_merge` |
| `cosyvoice-sft` | 80 字 | 320 字 | `split_verify_merge` |
| `cosyvoice-zero-shot` | 80 字 | 240 字 | `split_verify_merge` |
| `qwen3-tts-mlx-0.6b` | 120 字 | 360 字 | `split_verify_merge` |
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
- `language`：语言提示。ASR 可用 `auto`、`zh`、`en`；是否参与合成取决于具体引擎，不能当作所有本地 TTS 的通用生效参数（例如当前 IndexTTS MLX 路线不消费它）。

### IndexTTS v2

- `voice_id`：音色库里的本地参考音色。IndexTTS v2 生成必须有参考音频。
- `reference_audio_path`：直接指定参考音频路径；通常由 `voice_id` 自动解析。调用方显式提供时优先于 `voice_id`。
- `ref_text`：参考音频对应台词，目前用于音色库留档和 ASR 校对；当前 IndexTTS MLX 生成路线不会把它传给模型，因此不会改变这次合成效果。
- `emotion_mode`：`follow_reference` 表示不额外叠加情绪；`emotion_vector` 表示使用 `emotion` 和 `emo_alpha`。
- 当前 IndexTTS 接入不把自由文字（例如“压低声音、谨慎、喜悦”）当作情绪指令。请用页面提供的内置情绪，或选“跟随参考音色”；否则会明确提示不支持，避免看似设置了却悄悄回退。
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

### Confucius4-TTS MLX int8

- `voice_id`：音色库里的本地参考音频。Confucius4 需要参考音频做零样本声音克隆和情绪迁移。
- `reference_audio_path`：直接指定参考音频路径；显式提供时优先于 `voice_id`。
- `language`：目标文本语言代码。当前本机 runtime 只接受 `zh`、`en`、`vi`、`ja`、`ko`、`th`；其他语言会明确拒绝，避免悄悄按英文生成。
- `temperature`：T2S 采样随机性。官方默认 0.8。
- `top_p`：T2S 核采样范围。官方默认 0.8。
- `top_k`：T2S Top-K 候选数量。官方默认 30。
- `repetition_penalty`：T2S 重复惩罚。官方默认 10.0。
- `diffusion_steps`：S2A 声学采样步数，对应官方 `n_timesteps`。官方默认 25；更高通常更慢。
- `cfg_rate`：S2A classifier-free guidance，对应官方 `inference_cfg_rate`。官方默认 0.7。
- `seed`：同时影响 T2S 采样和 S2A 噪声初始化，便于参数对比复现。
- 当前不暴露官方 `num_beams`，因为 MLX runtime 没有 beam search 实现；不暴露官方 `max_length`，因为当前更可靠的做法是在 Voice Studio 层强制短分段，避免再次触发 10 秒左右的单窗截断。

### OmniVoice

- `voice_id`：使用音色库里的本地参考音频做声音克隆。
- `reference_audio_path`：直接指定参考音频路径；显式提供时优先于 `voice_id`。
- `emotion_text`：未选择参考音色时，作为声音设计/生成指令传给 OmniVoice。只使用支持词表里的组合，例如 `女，青年，中音调`、`女，青年，耳语`、`男，中年，低音调`；不要传 `自然口播`、`压低声音`、`谨慎` 等自由描述，后端会报 unsupported instruct items。
- `ref_text`：参考音频台词，可提升克隆稳定性。
- `language`：`auto` 或具体语言代码。
- `speed`：语速倍率。本项目不会把该值传入 OmniVoice 内部时长估算，而是在完整生成后做无变调时间拉伸；仍应先试听，尤其是长句。
- `duration`：固定输出时长秒数，`0` 表示自动时长。本项目同样在完整生成后拉伸/压缩到目标时长；如果同时传 `duration` 和 `speed`，`duration` 优先。目标时长和文字长度差距过大时，语速和自然度可能受影响。

### EmotiVoice

- `speaker_id`：官方开源模型里的预训练说话人 ID，例如 `8051`。
- `prompt`：中文情绪提示，例如 `开心`、`悲伤`、`愤怒`。
- 官方推理格式是 `speaker|style_prompt/emotion_prompt/content|phoneme|content`。本项目 runner 会自动调用 `frontend.py` 生成 phoneme。
- 当前官方本地前端会自动处理中英文和混读；生成页不需要额外设置语言。
- 官方的个人音色克隆需要单独训练，本项目目前没有接入这条训练流程，所以不能把本地上传参考音频用于 EmotiVoice 即时复刻。
- 当前精选 speaker 只是官方 2000+ voices 的子集；完整列表在本地 EmotiVoice `data/youdao/text/README.md` 和官方 voice wiki。
- 当前通过外部 venv 调用本地 EmotiVoice 仓库，可用 `VOICE_STUDIO_EMOTIVOICE_ROOT` 覆盖路径。

### F5-TTS

- `voice_id`：音色库里的本地参考音频。F5 需要参考音频做音色迁移。
- `reference_audio_path`：直接指定参考音频路径；显式提供时优先于 `voice_id`。
- `ref_text`：参考音频对应台词，当前接入要求必填；为空时后端会报 `REFERENCE_TEXT_REQUIRED`，避免自动下载/启动 Whisper。
- `speed`：语速倍率。
- `nfe_step`：采样步数。官方默认是 `32`，本地快速试听可降低到 `16`。
- `cfg_strength`：引导强度。官方默认是 `2.0`，过高可能让发音不自然。
- `target_rms`：低音量参考补偿阈值，官方默认是 `0.1`。它只帮助模型处理特别小声的参考音频，不是最终成品的音量旋钮。
- `cross_fade_duration`：分段交叉淡化秒数，官方默认是 `0.15`。
- `sway_sampling_coef`：采样时间步修正系数，官方默认是 `-1`；属于开发者参数，普通生成不建议改。
- `fix_duration`：单个模型推理块内的“参考音频加生成音频”总时长，`0` 表示自动估算，最大 30 秒。长文本会分块，所以它不能固定整段成品时长；属于开发者参数，填错容易让语速或停顿不自然。
- `remove_silence`：生成后移除较长静音。官方 basic 示例默认关闭。
- 当前通过外部 venv 调用本地 F5-TTS 仓库，默认使用本地 ModelScope 权重，可用 `VOICE_STUDIO_F5_TTS_ROOT` 覆盖路径。

### CosyVoice SFT

- `speaker_id`：官方 SFT 预置音色，例如 `中文女`、`中文男`、`粤语女`。
- `speed`：语速倍率。
- 当前通过外部 venv 调用本地 CosyVoice 仓库，可用 `VOICE_STUDIO_COSYVOICE_ROOT` 覆盖路径。

### CosyVoice Zero-Shot

- `voice_id`：音色库里的本地参考音频。CosyVoice Zero-Shot 使用它做参考音色生成。
- `reference_audio_path`：直接指定参考音频路径；通常由 `voice_id` 自动解析。调用方显式提供时优先于 `voice_id`。
- `ref_text`：参考音频对应台词，必填。缺失时后端会报 `REFERENCE_TEXT_REQUIRED`。
- `speed`：语速倍率。
- 官方实现会在目标文本明显短于 prompt text 时给出效果下降提醒；后台 agent 应先用较完整的测试句确认贴近度。
- 当前通过外部 venv 调用本地 CosyVoice 仓库，可用 `VOICE_STUDIO_COSYVOICE_ROOT` 覆盖路径。

### 豆包语音 TTS 2.0（seed-tts-2.0）

- `speaker_id`：豆包官方音色 ID。页面的“官方音色目录”来自火山引擎 `ListSpeakers` 接口缓存；官方返回的头像、试听链接、支持语言、情绪、分类和同款标签都会一并保存。同步官方目录需要火山引擎 AK/SK，真正能不能生成仍以当前账号授权为准。
- `language`：只对豆包官方预置音色生效，会映射为官方 `additions.explicit_language`。默认“自动”时不发送该字段；指定后只会朗读相应语种，混入其他语种可能被跳过或让请求失败。可用值包括 `zh-cn`（可中英混读）、`en`、`ja`、`es-mx`、`id`、`pt-br`、`pt`、`ko`、`it`、`de`、`fr`、`th`、`vi`、`ru`、`fil`、`ms`、`ar`、`pl`、`tr`、`sv`。
- `style_instruction`：只对官方预置音色生效，会映射为官方 `context_texts`。用一句白话描述“这次怎么说”，例如“像朋友耐心解释，重点前短暂停顿”。复刻音色官方暂不支持这个字段，所以页面不会把它发给声音复刻模型。
- `speed`：映射为官方 `audio_params.speech_rate`（-50 到 100），页面显示为更好理解的 0.5 到 2.0 倍速。数值越大读得越快。
- `loudness_rate`：官方范围 -50 到 100。0 保持原音量；向右更响、向左更轻。
- `pitch_rate`：映射为官方 `additions.post_process.pitch`，范围 -12 到 12。调高更尖亮，调低更厚沉。
- `sample_rate`：官方支持 8、16、22.05、24、32、44.1、48 kHz；更高通常更清晰、文件更大。
- `bit_rate`：只在 MP3 时生效，官方范围 64–160 kbps。
- `enable_subtitle`：TTS 2.0 的官方预置音色和已训练复刻音色都支持。当前仅中文、英文返回字级时间戳。
- `silence_duration`：官方 `additions` 参数，范围 0–30000 ms；在结尾加入静音，便于接转场或下一段素材。
- `aigc_watermark`：在音频结尾添加可识别的 AI 生成节奏标识。不是“防止音频被使用”的开关，而是来源识别标记。
- `max_length_to_filter_parenthesis`：页面显示为“不朗读圆括号内容”。开启后，脚本里的 `（人物备注、停顿说明）` 不会被当成台词念出来；普通正文没有括号说明时不用开。
- `disable_markdown_filter`：页面显示为“过滤 Markdown 排版标记”。开启后会尽量去掉 `**粗体**`、链接等常见符号，避免读出星号；复杂排版不保证全部自动清理，正式生成前仍建议先整理文本。
- `latex_parser_mode`：页面显示为“公式朗读”。基础模式把 LaTeX 数学写法按公式念出；增强模式使用官方 `latex_parser=v2`，会自动启用 Markdown 过滤且可能更慢。普通口播应保持关闭。
- `aigc_metadata_enable` 和来源字段：在 WAV、MP3 或 OGG Opus 成品中写入隐藏的制作/传播追溯信息，不会影响试听，也不会被朗读。PCM 和 FLAC 不发送这组字段，避免创建看似成功但官方不支持的请求。
- `tone_fidelity`：仅豆包云端复刻音色显示。它会更努力保留训练音频的说话习惯、情绪和口音；只适合同语种文本，打开后自由变化空间会变小。
- 云端声音复刻使用已训练成功的豆包 speaker ID；不支持把本地刚上传的参考音频直接作为一次性 TTS 输入。训练参考音频只支持中文或英文，会上传到豆包云端，先取得用户同意再提交。
- Voice Studio 现已把官方 `pcm`、`ogg_opus` 作为豆包 TTS 2.0 的可选直出格式：OGG Opus 可直接试听和分享；PCM 是没有文件头的原始音频数据，适合下载给剪辑、硬件或专业软件处理，浏览器不提供试听。其他本地/云端引擎仍只显示自己可安全交付的格式。

官方依据：[HTTP 单向流式 TTS V3](https://www.volcengine.com/docs/6561/2528925?lang=zh)、[ListSpeakers 音色查询](https://www.volcengine.com/docs/6561/2160690?lang=zh)。

### 豆包音频生成 Seed Audio 1.0（seed-audio-1.0）

这不是“把一段文字念出来”的普通 TTS，而是一次生成完整声音场景的模型：可以让它同时做对白、环境声、音效和配乐。因此提示词里写的“鸟鸣、脚步、背景音乐、人物声线”等说明，属于生成指令，不会被当作台词念出来。单次提示最多 3000 字、生成最长 120 秒；不适合用长文本批量拼接。

- `input_mode=text`：只根据文字提示生成，不需要上传素材。
- `input_mode=audio`：可放 1 到 3 个参考声音（本地音色库或已授权上传音频）。提示词用 `@音频1`、`@音频2`、`@音频3` 明确引用对应声音；它们会被上传到豆包云端。
- `input_mode=image`：只能放 1 张参考图片，用画面内容辅助决定声音场景；不能和音频参考混用。
- `format`：可选 WAV、MP3、PCM、OGG Opus。WAV 便于后期编辑，MP3 便于分享；PCM 是裸音频数据，OGG Opus 更适合支持它的播放器或传输流程。
- `sample_rate`：8、16、24、32、44.1、48 kHz。数值高通常保留更多细节、文件也更大；24 kHz 适合普通试听，48 kHz 更适合视频后期。
- `speech_rate`：整体语速，范围 -50 到 100。0 保持模型节奏，向右更快、向左更慢；这会影响场景中语言的节奏，不是“让整个音频播放加速”。
- `loudness_rate`：整体音量，范围 -50 到 100。0 不改，向右更响、向左更轻；不能修复原始生成中的爆音或杂音。
- `pitch_rate`：整体音调，范围 -12 到 12。0 不改，调高会更亮、更尖，调低会更厚、更沉；大幅调整容易失去自然感。
- `enable_subtitle`：返回生成时的字幕时间信息。需要后续对字幕、画面或口型时再开；纯音乐、环境音等没有明确台词时，ASR 覆盖率会自动跳过，不能拿来判断生成质量。
- `aigc_watermark`：在声音里写入可识别的 AI 生成标识，适合需要合规标记的交付。
- `aigc_metadata_enable` 及其四个来源字段：把制作方、制作编号、传播方、传播编号写成隐藏来源信息；正常试听听不到，也不会变成朗读内容。只有确实有自己的追溯规范时再填写。

官方依据：[Seed Audio 1.0 HTTP API](https://www.volcengine.com/docs/6561/2550782?lang=zh)。本地参数会先按模式、数量、素材类型、授权状态和格式校验，再构建官方请求；音频和图片参考不满足规则时会明确拦截，避免“看起来上传了、实际没用上”。

### MiMo V2.5 TTS Preset

- `mimo_voice`：官方预置音色。当前本地 catalog 包含 `mimo_default`、`冰糖`、`茉莉`、`苏打`、`白桦`、`Mia`、`Chloe`、`Milo`、`Dean`。
- `style_instruction`：自然语言风格指令，放入 MiMo 官方要求的 `user` message。可写语速、情绪、角色、方言等。
- `output_format`：当前固定 WAV。官方非流式示例明确返回 WAV，尚未用有效 Key 验证 MP3/FLAC。

### MiMo V2.5 TTS VoiceDesign

- `voice_design_prompt`：音色描述，必填，放入 `user` message。
- `optimize_text_preview`：官方可选项，放入 `audio.optimize_text_preview`。开启后会根据音色描述优化目标播报文本；需要严格保留原文时关闭。
- `output_format`：当前固定 WAV。官方非流式示例明确返回 WAV，尚未用有效 Key 验证 MP3/FLAC。

### MiMo V2.5 TTS VoiceClone

- `voice_id`：音色库里的本地参考音色。只有本次生成选择的参考音频会上传到 MiMo。
- `reference_audio_path`：直接指定 wav/mp3 参考音频路径；显式提供时优先于 `voice_id`。官方限制完整的 `data:{MIME};base64,...` 字符串不超过 10 MB，本地会在上传前按这个总长度检查。
- `style_instruction`：自然语言风格指令。要调语速时写在这里，例如“语速稍慢，停顿自然”。
- `output_format`：当前固定 WAV。官方非流式示例明确返回 WAV，尚未用有效 Key 验证 MP3/FLAC。

## MiMo payload 对照

MiMo TTS 的目标合成文本放在 `assistant` message；风格/音色描述放在 `user` message。voiceclone 的参考音频放在 `audio.voice`，格式是 `data:{MIME_TYPE};base64,...`。

本地 `mimo_client.build_tts_payload()` 只会向 MiMo 发送：

- `model`
- `messages`
- `audio.format`
- `audio.voice`，仅 preset 和 voiceclone 使用
- `audio.optimize_text_preview`，仅 voicedesign 按需使用

因此，即使外部 agent 在通用请求里传了 `speed`、`temperature`、`top_p`、`top_k` 或分段参数，MiMo 请求也不会消费这些字段。

## 推荐 agent 用法

1. 先调 `GET /api/engines`，读取目标引擎的 `parameter_schema`。
2. 调 `GET /api/presets`，只展示 `engine_id` 与当前引擎一致的预设。
3. 只展示和传入 schema 中存在的参数；滑块类参数同时允许精确数值输入，但仍要遵守 schema/后端校验范围。
4. 按引擎写合成文本提示。MiMo 的风格写 `style_instruction` 或 `voice_design_prompt`，不要给 MiMo 传本地 `speed/top_k/segment` 参数。
5. 写 OmniVoice 文本时，标签和正文拟声词不要重复；非按钮同格式标签可以作为试音探针，但批量生产前必须试听确认。
6. 调用 MiMo voiceclone 前，确认用户允许本次参考音频上传云端。
7. 生成后轮询任务状态，只有 `status: success` 且存在 `result_audio_id` 时才报告成功。
8. 批量任务优先使用 `scripts/voice_studio_batch.py`；每段可以覆盖 `engine_id`、`voice_id`、`speed`、`emotion`、`style_instruction` 等有效参数。
