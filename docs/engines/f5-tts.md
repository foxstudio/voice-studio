# F5-TTS

> SWivid 团队基于 Flow Matching + DiT 的非自回归零样本语音合成模型。

## 基本信息

| 项目 | 详情 |
|---|---|
| 开发者 | SWivid |
| 架构 | Flow Matching + Diffusion Transformer (DiT)，非自回归 |
| 最新版本 | v1 Base (2025 年 3 月) |
| 许可证 | 开源 |
| 仓库 | [github.com/swivid/f5-tts](https://github.com/swivid/f5-tts) |
| 论文 | [OpenReview](https://openreview.net/forum?id=JiX2DuTkeU) |

## 核心能力

- **零样本声音克隆**：仅需 5 秒参考音频即可克隆音色
- **非自回归架构**：基于 Flow Matching 的 DiT，推理效率高
- **长文本支持**：支持整本书/长脚本的连续语音生成
- **可微调新语言**：支持针对新语种进行微调训练
- **高质量自然度**：被社区认为是最逼真的开源零样本 TTS 之一

## 在本项目中的适配

- 本地运行
- 采样率：24000 Hz
- 提供高级参数：NFE 采样步数、CFG 引导强度、RMS 响度、交叉淡化、静音移除
- 支持语言：中文、英文

## 适用场景

- 已授权参考音色的跨文本复刻研究
- 长篇有声读物和脚本生成
- 音色迁移实验

## 参考链接

- [F5-TTS GitHub](https://github.com/swivid/f5-tts)
- [Hugging Face](https://huggingface.co/SWivid/F5-TTS)
