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

## 参考链接

- [CosyVoice GitHub](https://github.com/FunAudioLLM/CosyVoice)
- [CosyVoice 官网](https://cosyvoice.org/)
