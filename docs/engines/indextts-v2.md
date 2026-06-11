# IndexTTS v2

> Bilibili IndexTeam 出品的工业级零样本文本转语音系统，支持情绪控制和声音克隆。

## 基本信息

| 项目 | 详情 |
|---|---|
| 开发者 | Bilibili IndexTeam (IndexTeam) |
| 开源时间 | 2025 年 6 月首次开源，9 月正式发布 |
| 架构 | 基于 Transformer 的自回归零样本 TTS 基座模型 |
| 许可证 | 开源 |
| 仓库 | [github.com/index-tts/index-tts](https://github.com/index-tts/index-tts) |
| 论文 | [arXiv 2601.03888](https://arxiv.org/html/2601.03888v3) |

## 核心能力

- **零样本声音克隆**：仅需一段参考音频即可复刻任意说话人音色
- **情绪控制**：通过多模态输入实现 8 种精细情绪控制（开心、悲伤、愤怒、恐惧、惊讶、厌恶、中性等）
- **高表现力**：被广泛认为是 2025 年最具表现力的开源 TTS 模型之一
- **时长精确控制**：支持精确的时间对齐，适合配音和视频旁白
- **拼音控制**：支持拼音标注以精确控制发音

## 在本项目中的适配

- 基于 MLX 框架移植到 Apple Silicon (M1–M4) 本地运行
- 声码器路径：S2Mel → BigVGAN2
- 采样率：22050 Hz
- 最大 token 数：1815（支持较长文本）
- 支持语言：中文、英文

## 适用场景

- 中文口播内容制作
- 情绪化配音和角色配音
- 视频旁白和有声读物
- 已授权音色的声音克隆

## 参考链接

- [IndexTTS GitHub](https://github.com/index-tts/index-tts)
- [IndexTTS 2.5 技术报告](https://arxiv.org/html/2601.03888v3)
