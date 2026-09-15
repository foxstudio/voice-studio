# Qwen3-ASR MLX

> 基于 Qwen3-ASR 的 Apple Silicon 本地语音识别模型，通过 MLX 框架实现 Metal GPU 加速推理。

## 基本信息

| 项目 | 详情 |
|---|---|
| 开发者 | Qwen 团队 + MLX 社区 |
| 模型 | Qwen3-ASR 1.7B |
| 量化 | 8-bit MLX 量化 |
| 架构 | 纯 MLX 实现，无需 PyTorch/Transformers |
| 许可证 | 开源 |
| 仓库 | [github.com/moona3k/mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr/) |
| 国内模型 | [ModelScope: mlx-community/Qwen3-ASR-1.7B-8bit](https://modelscope.cn/models/mlx-community/Qwen3-ASR-1.7B-8bit) |
| PyPI | [qwen3-asr-mlx](https://pypi.org/project/qwen3-asr-mlx/) |

## 核心能力

- **超越 Whisper-large-v3**：在多语言基准测试中超过 Whisper-large-v3
- **Metal GPU 加速**：通过 MLX 框架利用 Apple Silicon GPU 推理
- **纯本地运行**：无需网络，数据不离开设备
- **多语言支持**：自动语言检测、中文、英文
- **量化支持**：4-bit 和 8-bit 量化，降低显存占用

## 在本项目中的适配

- 本地 MLX 推理，1.7B 8-bit 量化
- 国内下载优先使用 ModelScope 上同格式的 MLX Community 8-bit 权重
- 作为云端 ASR（MiMo V2.5 ASR）的离线备选
- 预留后续更完整的离线转写能力

## 适用场景

- 离线音频转写
- 隐私敏感场景的语音识别
- 云端 ASR 不可用时的备选方案

## 参考链接

- [mlx-qwen3-asr GitHub](https://github.com/moona3k/mlx-qwen3-asr/)
- [ModelScope 国内模型](https://modelscope.cn/models/mlx-community/Qwen3-ASR-1.7B-8bit)
- [PyPI: qwen3-asr-mlx](https://pypi.org/project/qwen3-asr-mlx/)
