# CosyVoice SFT / Zero-Shot

> 阿里巴巴 FunAudioLLM 团队出品的多语种大规模语音生成模型，支持 SFT 预置音色和零样本声音克隆。

## 基本信息

| 项目 | 详情 |
|---|---|
| 开发者 | 阿里巴巴 FunAudioLLM |
| 当前本地接入 | SFT：`CosyVoice-300M-SFT`；Zero-Shot：`CosyVoice-300M`（已安装并通过健康检查） |
| 架构 | 官方 SFT 预置音色；官方 Zero-Shot 参考音色路径 |
| 页面可用语言 | 中文、英文、日语、粤语、韩语（以已装模型为准） |
| 许可证 | 开源 |
| 仓库 | [github.com/FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) |
| 论文 | [CosyVoice v1](https://funaudiollm.github.io/pdf/CosyVoice_v1.pdf) / [arXiv 2407.05407](https://arxiv.org/abs/2407.05407) |

## 两种使用模式

### SFT 模式 (CosyVoice SFT)

- 使用官方预训练 SFT 音色
- 模型：CosyVoice-300M-SFT
- 预置音色覆盖中文、粤语、日语、韩语、英文
- 无需参考音频，开箱即用

### Zero-Shot 参考音色模式

- 提供参考音频 + 对应台词文本
- 使用官方 `CosyVoice-300M`
- 当前本机已安装该模型；不会回退到 SFT 模型或假装能够复刻
- 官方 v1 Base 限制参考音频不超过 30 秒；建议裁取 3–30 秒清晰人声，并填写与音频完全对应的台词

## 核心能力

- **SFT 预置音色**：直接选择已安装模型提供的 7 个官方说话人
- **Zero-Shot 参考音色**：提供不超过 30 秒的参考音频和准确台词，走已安装的官方 `CosyVoice-300M` 路径

## 在本项目中的适配

- 本地运行
- 原生采样率：22050 Hz（MP3/FLAC 等是 Voice Studio 的导出后处理格式）
- SFT 模式：预置音色选择 + 语速控制
- Zero-Shot 模式：使用本地音色库参考音频，参考音频必须不超过 30 秒并配准确台词
- 官方推理接口会把长文本按约 60-80 个中文字符自动切分并逐段 yield；Voice Studio 会消费所有分段输出并合并保存，避免只保留第一段。

## 当前参数与默认值

CosyVoice 在生成页拆成两个引擎。SFT 模式只用官方预置 speaker；Zero-Shot 模式使用音色库里的参考音频和准确参考台词。

| 引擎 | 参数 | 默认值 | 大白话说明 |
|---|---|---:|---|
| CosyVoice SFT | `speaker_id` | `中文女` | 官方预置音色，不需要本地参考音频。 |
| CosyVoice SFT | `speed` | `1.0` | 控制朗读速度。 |
| CosyVoice Zero-Shot | `voice_id/ref_text` | 用户选择 | 需要不超过 30 秒的本地参考音频和准确参考台词。 |
| CosyVoice Zero-Shot | `speed` | `1.0` | 官方非流式推理语速参数，页面范围 `0.5–2.0`。 |

CosyVoice-300M v1 Base Zero-Shot 不提供音量、音调、温度、CFG、采样步数或用户可选采样率等生成参数；这些不显示在当前面板。跨语种复刻、音色转换和 Instruct 是不同的官方调用模式或模型，后续接入时会作为独立模式实现，不与当前 Zero-Shot 参数混用。

内置预设：中文女声、中文男声、粤语女声、参考音色复刻、慢速清晰。生成页“一键重置参数”会按当前 CosyVoice 模式恢复默认参数。

## 参考链接

- [CosyVoice GitHub](https://github.com/FunAudioLLM/CosyVoice)
- [CosyVoice 官网](https://cosyvoice.org/)
