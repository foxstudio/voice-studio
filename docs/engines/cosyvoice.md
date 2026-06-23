# CosyVoice SFT / Zero-Shot

> 阿里巴巴 FunAudioLLM 团队出品的多语种大规模语音生成模型，支持 SFT 预置音色和零样本声音克隆。

## 基本信息

| 项目 | 详情 |
|---|---|
| 开发者 | 阿里巴巴 FunAudioLLM |
| 最新版本 | CosyVoice 3 (2025) |
| 架构 | 多语种零样本 TTS |
| 语言覆盖 | 9+ 语言（中文、英文、日语、粤语、韩语等） |
| 许可证 | 开源 |
| 仓库 | [github.com/FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) |
| 论文 | [arXiv 2505.17589](https://arxiv.org/html/2505.17589v2) |

## 两种使用模式

### SFT 模式 (CosyVoice SFT)

- 使用官方预训练 SFT 音色
- 模型：CosyVoice-300M-SFT
- 预置音色覆盖中文、粤语、日语、韩语、英文
- 无需参考音频，开箱即用

### Zero-Shot 模式 (CosyVoice Zero-Shot)

- 提供参考音频 + 对应台词文本
- 零样本复刻任意说话人音色
- 支持跨语言克隆（如用中文参考音频生成英文）

## 核心能力

- **零样本多语种合成**：提供短参考音频即可克隆任意声音
- **跨语言克隆**：一种语言的参考音频可生成其他语言的语音
- **流式推理**：CosyVoice 3 支持流式输出，延迟低至 150ms
- **口音保留**：声音转换时保留原始口音特征

## 在本项目中的适配

- 本地运行
- 采样率：22050 Hz
- SFT 模式：预置音色选择 + 语速控制
- Zero-Shot 模式：使用本地音色库参考音频
- 官方推理接口会把长文本按约 60-80 个中文字符自动切分并逐段 yield；Voice Studio 会消费所有分段输出并合并保存，避免只保留第一段。

## 当前参数与默认值

CosyVoice 在生成页拆成两个引擎。SFT 模式只用官方预置 speaker；Zero-Shot 模式使用音色库里的参考音频和准确参考台词。

| 引擎 | 参数 | 默认值 | 大白话说明 |
|---|---|---:|---|
| CosyVoice SFT | `speaker_id` | `中文女` | 官方预置音色，不需要本地参考音频。 |
| CosyVoice SFT | `speed` | `1.0` | 控制朗读速度。 |
| CosyVoice Zero-Shot | `voice_id/ref_text` | 用户选择 | 需要本地参考音频和准确参考台词。 |
| CosyVoice Zero-Shot | `speed` | `1.0` | 控制目标文本速度。 |

内置预设：中文女声、中文男声、粤语女声、参考音色复刻、慢速清晰。生成页“一键重置参数”会按当前 CosyVoice 模式恢复默认参数。

## 参考链接

- [CosyVoice GitHub](https://github.com/FunAudioLLM/CosyVoice)
- [CosyVoice 官网](https://cosyvoice.org/)
