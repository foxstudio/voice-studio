# Voice Studio

Apple Silicon 多引擎 TTS 平台。通过 WebUI 和 REST API 运行本地声音克隆（IndexTTS v2）、多语言合成（OmniVoice、F5-TTS、CosyVoice）、情感 TTS（EmotiVoice）和云端合成（小米 MiMo V2.5）。

基于 [MLX](https://github.com/ml-explore/mlx) 实现 Apple Silicon 原生性能。

## 功能

- **WebUI** — SvelteKit 仪表盘（`localhost:5173`），包含引擎中心、音色库、批量生成、历史记录和设置
- **REST API** — FastAPI 后端（`localhost:8000`），17 个路由组（生成、长文本、批量、任务、音色、引擎、ASR 等）
- **6 个本地引擎** — IndexTTS v2（声音克隆 + 情感）、OmniVoice（多语言）、EmotiVoice（中文情感 TTS）、F5-TTS、CosyVoice SFT/Zero-Shot
- **云端引擎** — 小米 MiMo V2.5 预置音色 / 音色设计 / 声音复刻
- **长文本编排** — 自动分段、逐段生成 + ASR 校对 + 合并
- **批量处理** — JSON 驱动的批量合成，输出结果清单

## 架构

```
mlx_indextts/       MLX 推理核心（IndexTTS v2、模型加载、分词器）
backend/app/        FastAPI 服务端（API 路由、业务逻辑、任务队列）
frontend/src/       SvelteKit 前端（10 个页面、侧边栏导航）
scripts/            音色导入、批量处理、质量校验
```

## 快速开始

### 前置要求

- macOS Apple Silicon（M1/M2/M3/M4）
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) 包管理器
- [pnpm](https://pnpm.io/)（前端）

### 安装

```bash
git clone https://github.com/foxstudio/voice-studio.git
cd voice-studio

# 安装 Python 依赖
uv sync

# 模型转换支持
uv sync --extra convert

# 本地 ASR（Qwen3-ASR MLX）
uv sync --extra asr

# 服务端依赖
uv sync --extra server

# 安装前端
cd frontend && pnpm install && cd ..
```

### 模型获取

本项目**不包含模型权重**（文件过大，不适合放 Git）。需要自行下载并转换：

```bash
# 安装转换依赖
uv sync --extra convert

# 从 HuggingFace 下载 PyTorch 模型，然后转换为 MLX 格式
# 1. 下载原始模型到本地（示例）
git lfs install
git clone https://huggingface.co/IndexTeam/IndexTTS2 models/IndexTTS2-pt

# 2. 转换为 MLX 格式
uv run voice-studio convert \
  --model-dir models/IndexTTS2-pt \
  --output models/mlx-indexTTS-2.0
```

转换完成后，`models/mlx-indexTTS-2.0/` 约 4 GB。

## 音色库

声音克隆引擎（IndexTTS v2、F5-TTS、CosyVoice Zero-Shot）需要参考音频。通过 WebUI 的**音色库**页面上传管理：

1. 启动服务后打开 http://localhost:5173
2. 进入「音色库」页面
3. 上传 wav/mp3 参考音频，填写名称和台词文本
4. 生成时选择对应音色即可

CLI 也可指定参考音频路径：`-r reference.wav`

## 云端引擎配置

MiMo V2.5 云端引擎需要 API key：

1. 前往 [小米 MiMo 平台](https://platform.xiaomimimo.com) 注册并获取 API key
2. 在 WebUI「设置」页面填入 key，或设置环境变量 `MIMO_API_KEY`

## 启动

```bash
./start.sh
```

启动后端（uvicorn :8000）和前端（vite :5173）。打开 http://localhost:5173。

```bash
# 强制重启
./start.sh --force
```

### 语音合成（API）

```bash
# 健康检查
curl http://localhost:8000/api/health

# 查看可用引擎
curl http://localhost:8000/api/engines

# 生成语音
curl -X POST http://localhost:8000/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "你好，这是一个语音合成测试。",
    "engine_id": "indextts-v2",
    "voice_id": "YOUR_VOICE_ID",
    "output_format": "mp3"
  }'
```

## 引擎一览

| 引擎 | 类型 | 核心能力 |
|------|------|----------|
| `indextts-v2` | 本地 | 声音克隆、8 种情感、分段控制、扩散参数 |
| `omnivoice` | 本地 | 多语言、声音描述、语速控制 |
| `emotivoice` | 本地 | 中文情感 TTS、预置说话人 |
| `f5-tts` | 本地 | 参考音频 TTS，需提供 `ref_text` |
| `cosyvoice-sft` | 本地 | 官方 SFT 预置音色 |
| `cosyvoice-zero-shot` | 本地 | 参考音频声音复刻 |
| `mimo-v2.5-tts-preset` | 云端 | 小米官方预置音色 |
| `mimo-v2.5-tts-voicedesign` | 云端 | 文本描述音色设计 |
| `mimo-v2.5-tts-voiceclone` | 云端 | 云端参考音频声音复刻 |

引擎参数详解：[docs/VOICE_STUDIO_ENGINE_PARAMETERS.md](docs/VOICE_STUDIO_ENGINE_PARAMETERS.md)

批量合成指南：[docs/VOICE_STUDIO_BATCH_AGENT.md](docs/VOICE_STUDIO_BATCH_AGENT.md)

## CLI 使用

```bash
# 基本生成
uv run voice-studio generate \
  -m models/mlx-indexTTS-2.0 \
  -r reference.wav \
  -t "你好，世界！" \
  -o output.wav

# 情感控制
uv run voice-studio generate \
  -m models/mlx-indexTTS-2.0 \
  -r reference.wav \
  -t "今天真是太开心了！" \
  -o output.wav \
  --emotion happy --emo-alpha 0.6

# 预计算 speaker embedding（加速加载）
uv run voice-studio speaker \
  -m models/mlx-indexTTS-2.0 \
  -r reference.wav \
  -o speaker.npz
```

### Python API

```python
from mlx_indextts.generate_v2 import IndexTTSv2

tts = IndexTTSv2("models/mlx-indexTTS-2.0")
audio = tts.generate(
    text="你好",
    reference_audio="reference.wav",
    output_path="output.wav",
    emotion="happy",
    emo_alpha=0.6,
)
```

## 性能

| 指标 | IndexTTS v2 |
|------|-------------|
| RTF（M2 Max） | ~1.3 |
| 加载时间（.wav） | ~9s |
| 加载时间（.npz） | ~1.5s |

## 支持的情感（IndexTTS v2）

| 英文 | 中文 |
|------|------|
| happy | 高兴 |
| angry | 愤怒 |
| sad | 悲伤 |
| afraid | 恐惧 |
| disgusted | 反感 |
| melancholic | 低落 |
| surprised | 惊讶 |
| calm | 自然 |

混合情感：`--emotion "happy:0.6,sad:0.4"`

## 许可证

MIT License

## 致谢

- [IndexTTS](https://github.com/index-tts/index-tts) — 原始 PyTorch 实现
- [MLX](https://github.com/ml-explore/mlx) — Apple 机器学习框架
- [OmniVoice](https://github.com/user/omnivoice) — 多语言 TTS 引擎
- [EmotiVoice](https://github.com/netease-youdao/EmotiVoice) — 中文情感 TTS
- [F5-TTS](https://github.com/SWivid/F5-TTS) — 参考音频 TTS
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) — 多风格 TTS
