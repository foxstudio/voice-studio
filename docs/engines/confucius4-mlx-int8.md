# Confucius4-TTS MLX int8

> NetEase Youdao 子曰4 TTS 的 Apple Silicon MLX int8 版本，当前通过 Hert4 的 `mlx-audio` Confucius4 分支接入。

## 基本信息

| 项目 | 详情 |
|---|---|
| 引擎 ID | `confucius4-mlx-int8` |
| 开发者 | NetEase Youdao / MLX runtime by Hert4 |
| 模型 | `beyoru/Confucius4-TTS-mlx-int8` |
| 本地模型目录 | `~/VoiceStudio/models/confucius4-mlx-int8` |
| 本地运行时目录 | `~/VoiceStudio/engines/mlx-audio-confucius4` |
| 环境变量覆盖 | `VOICE_STUDIO_CONFUCIUS4_MODEL_DIR`, `VOICE_STUDIO_CONFUCIUS4_MLX_AUDIO_ROOT` |
| 采样率 | 22050 Hz |

## 核心能力

- 使用音色库或自定义参考音频做 zero-shot 声音克隆
- 支持跨语种生成：中文、英文、日语、韩语、德语、法语、西班牙语、印尼语、意大利语、泰语、葡萄牙语、俄语、马来语、越南语
- 从参考音频迁移音色和情绪表现
- 在 Apple Silicon 上用 MLX int8 权重本地推理

## 当前接入

- 生成页把它归为参考音色引擎，可选择“音色库”或“自定义”。
- 不强制参考台词 `ref_text`；自定义音色仍可保留 ASR 文本，方便音色库留档。
- 单条生成走隔离子进程；批量生成在同一个 batch 子进程内复用已加载模型。
- 健康检查同时检查模型文件和 MLX runtime 文件。

## 参数默认值

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `language` | `zh` | 目标文本语言，跨语种生成建议手动选择。 |
| `temperature` | `0.8` | 随机性，越低越稳定；官方默认 0.8。 |
| `top_p` | `0.8` | 核采样范围。 |
| `top_k` | `30` | 候选数量。 |
| `repetition_penalty` | `10.0` | 重复惩罚。 |
| `diffusion_steps` | `25` | 声学 S2A 采样步数；官方默认 25。 |
| `cfg_rate` | `0.7` | 声学 S2A classifier-free guidance；官方默认 0.7。 |
| `seed` | `0` | 固定默认种子，便于参数对比测试。 |

## 官方参数对照

网易有道官方 PyTorch `ConfuciusTTS.generate()` 默认包含 `temperature=0.8`、`top_p=0.8`、`top_k=30`、`repetition_penalty=10.0`、`n_timesteps=25`、`inference_cfg_rate=0.7`。当前 Voice Studio 对应为 `temperature`、`top_p`、`top_k`、`repetition_penalty`、`diffusion_steps`、`cfg_rate`。

官方 API 还有 `num_beams`、`max_length`、`max_text_tokens_per_segment` 和 cross/edge fade 类参数。当前 MLX runtime 没有 beam search 实现；`max_length` 在 MLX port 中对应内部 `max_new=512`，为避免长文本再次越界，Voice Studio 目前采用更保守的产品层分段。cross/edge fade 在当前 MLX runner 里没有按官方方式实现，因此暂不暴露为可调参数。

## 内置预设

- 中文情绪迁移
- 英文跨语种
- 日文跨语种
- 稳定低随机

## 验证建议

1. 先用音色库里已知干净的短参考音频生成中文短句。
2. 再用同一参考音频生成英文或日文短句，对比跨语种音色保持。
3. 切到自定义音色上传一段 5 到 15 秒清晰音频，确认 `reference_audio_path` 优先于 `voice_id`。
4. 长文本超过 24 字时用长文本规划拆段，逐段校对后再合并。

## 参考链接

- [Hugging Face: beyoru/Confucius4-TTS-mlx-int8](https://huggingface.co/beyoru/Confucius4-TTS-mlx-int8)
- [mlx-audio Confucius4 PR](https://github.com/Hert4/mlx-audio/pull/88)
