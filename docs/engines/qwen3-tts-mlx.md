# Qwen3-TTS MLX 0.6B

> 千问 TTS 的 Apple Silicon MLX 实验接入，当前使用社区 8-bit 模型作为本地推理 PoC。

## 基本信息

| 项目 | 详情 |
|---|---|
| 引擎 ID | `qwen3-tts-mlx-0.6b` |
| 运行方式 | 本地外部子进程 |
| 模型 | `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit` 和 `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit` |
| 推荐运行时目录 | `~/VoiceStudio/engines/qwen3-tts-mlx`（可为软链接） |
| 采样率 | 24000 Hz |
| 状态 | 实验接入，适合短句 Pilot 和音色比较 |

## 三种互斥使用模式

### 预置音色

不选择本地音色、自定义音色，且不填写声音描述时，Voice Studio 使用 CustomVoice 模型。前端可选 `speaker_id`，默认是 `Vivian`。

### 本地参考音色复刻

选择音色库 `voice_id` 或传入 `reference_audio_path` 时，Voice Studio 使用 Base 模型做参考音色复刻，必须给参考音频填写准确 `ref_text`；缺失会直接提示补充，不再用占位文字硬生成。

### 声音设计

只有本机存在 `models/Qwen3-TTS-12Hz-0.6B-VoiceDesign-8bit` 时，Voice Studio 才展示 `voice_design_prompt` 和“Qwen3 声音设计”预设。填写 `voice_design_prompt` 时，Voice Studio 使用 VoiceDesign 模型，把这段描述作为官方 `instruct` 参数；它会接管预置音色和风格指令。

## 当前参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `speaker_id` | `Vivian` | CustomVoice 预置音色。 |
| `style_instruction` | 空（Normal tone） | 只在 CustomVoice 预置音色路线生效的演绎指令，例如“温柔、耐心，像在讲解课程”。参考音色与 VoiceDesign 路线不提交它，避免和各自的声音控制冲突。 |
| `language` | `chinese` | 目标语言提示，可选自动、中文、英文、日文、韩文、德文、意大利文、葡萄牙文、西班牙文、法文、俄文。页面会把旧的 `zh/en/ja/ko` 写法转换为模型实际识别的语言 ID。 |
| `voice_design_prompt` | 空 | VoiceDesign 声音描述。仅在本机安装 VoiceDesign 模型后展示；填写后使用 VoiceDesign 模型。支持中文或英文，例如“年轻中文女声，声线温暖，吐字清晰”。 |
| `speed` | `1.0` | 本项目在生成后做不变调时间拉伸来兑现语速；它已实际生效，但不是当前 MLX 上游的原生生成参数。 |
| `temperature` | `0.7` | 采样随机度，默认偏稳定。 |
| `top_p` | `0.9` | 官方 top_p 参数，控制核采样范围。 |
| `top_k` | `50` | 官方 top_k 参数，控制候选 token 数量。 |
| `repetition_penalty` | `1.1` | 官方 repetition_penalty 参数，抑制重复发音或片段。 |
| `max_tokens` | `1200` | 官方 max_tokens 参数。 |

## 使用建议

- 先用 20-40 秒以内短句试听，不要直接上长稿。
- 做知识视频旁白时，优先和 IndexTTS v2、MiMo、Confucius4 做同文本 Pilot 对比。
- 如果要复刻本地音色，先确认音色授权和参考台词；生成后用 Qwen3-ASR 或人工复听检查漏句。
- 本地音色库或自定义音色一旦生效，`speaker_id`、`style_instruction` 和 `voice_design_prompt` 不再参与本次生成。
- 当前不是主力稳定引擎，定位是“可选候选”和“社区新模型试验位”。

## 内置合成预设

| 预设 | 路线 | 关键参数 |
|---|---|---|
| Qwen3 官方基准 | CustomVoice 预置音色 | `speaker_id=Vivian`, `speed=1.0`, `temperature=0.7`, `top_p=1.0`, `top_k=30`, `repetition_penalty=1.15`, `max_tokens=512` |
| Qwen3 课程慢讲 | CustomVoice 预置音色 | `speed=0.8`, `temperature=0.65`, `top_p=0.92`, `top_k=35` |
| Qwen3 声音设计 | VoiceDesign | 仅安装 `Qwen3-TTS-12Hz-0.6B-VoiceDesign-8bit` 后显示；`voice_design_prompt=温柔的中文女声...`, `temperature=0.65`, `top_p=0.92`, `top_k=35` |
| Qwen3 复刻讲述 | Base 参考音色复刻 | 需要当前选择本地音色或自定义参考音色；不提交 `speaker_id` / `style_instruction` / `voice_design_prompt` |

这些预设不是“官方最佳值”。官方 Hugging Face `generation_config.json` 更偏模型默认值，Apple Silicon PoC 主要给出三种语速，社区 voice clone 示例更常见 `temperature=0.7`, `top_p=1.0`, `top_k=30`, `repetition_penalty=1.15`, `max_new_tokens=512`。Voice Studio 预设选择了偏稳、适合短句试听的折中值。

## 参考链接

- [Qwen3-TTS Apple Silicon PoC](https://github.com/kapi2800/qwen3-tts-apple-silicon)
- [MLX Community Hugging Face](https://huggingface.co/mlx-community)
- [Qwen3-TTS CustomVoice generation_config](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice/blob/main/generation_config.json)
