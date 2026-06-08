# Voice Studio

Multi-engine TTS platform for Apple Silicon. Run local voice cloning (IndexTTS v2), multi-language synthesis (OmniVoice, F5-TTS, CosyVoice), emotion TTS (EmotiVoice), and cloud synthesis (Xiaomi MiMo V2.5) through a WebUI and REST API.

Built on [MLX](https://github.com/ml-explore/mlx) for native Apple Silicon performance.

## Features

- **Voice Studio WebUI** — SvelteKit dashboard at `localhost:5173` with engine hub, voice library, batch generation, history, and settings
- **REST API** — FastAPI backend at `localhost:8000` with 17 route groups (generate, longform, batches, tasks, voices, engines, ASR, etc.)
- **6 local engines** — IndexTTS v2 (voice cloning + emotion), OmniVoice (multi-language), EmotiVoice (Chinese emotion TTS), F5-TTS, CosyVoice SFT/Zero-Shot
- **Cloud engines** — Xiaomi MiMo V2.5 preset / voice design / voice clone
- **Long-form orchestration** — Auto-split, segment-by-segment generation with ASR verification and merge
- **Batch processing** — JSON-driven batch synthesis with manifest output

## Architecture

```
mlx_indextts/       MLX inference core (IndexTTS v2, model loading, tokenizers)
backend/app/        FastAPI server (API routes, services, task queues)
frontend/src/       SvelteKit WebUI (10 pages, sidebar navigation)
scripts/            Voice import, batch processing, quality verification
```

## Quick Start

### Prerequisites

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- [pnpm](https://pnpm.io/) for frontend

### Install

```bash
git clone https://github.com/foxmacstudio/voice-studio.git
cd voice-studio

# Install Python dependencies
uv sync

# With model conversion support
uv sync --extra convert

# With local ASR (Qwen3-ASR MLX)
uv sync --extra asr

# With server dependencies
uv sync --extra server

# Install frontend
cd frontend && pnpm install && cd ..
```

### Start

```bash
./start.sh
```

This starts the backend (uvicorn :8000) and frontend (vite :5173). Open http://localhost:5173.

```bash
# Force restart
./start.sh --force
```

### Generate Speech (API)

```bash
# Check service health
curl http://localhost:8000/api/health

# List available engines
curl http://localhost:8000/api/engines

# Generate speech
curl -X POST http://localhost:8000/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "你好，这是一个语音合成测试。",
    "engine_id": "indextts-v2",
    "voice_id": "YOUR_VOICE_ID",
    "output_format": "mp3"
  }'
```

## Engine Support

| Engine | Type | Key Feature |
|--------|------|-------------|
| `indextts-v2` | Local | Voice cloning, 8 emotions, segment control, diffusion params |
| `omnivoice` | Local | Multi-language, voice description, speed control |
| `emotivoice` | Local | Chinese emotion TTS, preset speakers |
| `f5-tts` | Local | Reference audio TTS, requires `ref_text` |
| `cosyvoice-sft` | Local | Official SFT preset speakers |
| `cosyvoice-zero-shot` | Local | Reference audio voice cloning |
| `mimo-v2.5-tts-preset` | Cloud | Xiaomi official preset voices |
| `mimo-v2.5-tts-voicedesign` | Cloud | Text-based voice design |
| `mimo-v2.5-tts-voiceclone` | Cloud | Cloud voice cloning from reference audio |

Engine parameter details: [docs/VOICE_STUDIO_ENGINE_PARAMETERS.md](docs/VOICE_STUDIO_ENGINE_PARAMETERS.md)

Batch processing guide: [docs/VOICE_STUDIO_BATCH_AGENT.md](docs/VOICE_STUDIO_BATCH_AGENT.md)

## CLI Usage

Voice Studio also provides a CLI for quick generation:

```bash
# Basic generation
uv run voice-studio generate \
  -m models/mlx-indexTTS-2.0 \
  -r reference.wav \
  -t "你好，世界！" \
  -o output.wav

# With emotion control
uv run voice-studio generate \
  -m models/mlx-indexTTS-2.0 \
  -r reference.wav \
  -t "今天真是太开心了！" \
  -o output.wav \
  --emotion happy --emo-alpha 0.6

# Pre-compute speaker embedding for faster loading
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

## Performance

| Metric | IndexTTS v2 |
|--------|-------------|
| RTF (M2 Max) | ~1.3 |
| Load time (.wav) | ~9s |
| Load time (.npz) | ~1.5s |

## Supported Emotions (IndexTTS v2)

| English | 中文 |
|---------|------|
| happy | 高兴 |
| angry | 愤怒 |
| sad | 悲伤 |
| afraid | 恐惧 |
| disgusted | 反感 |
| melancholic | 低落 |
| surprised | 惊讶 |
| calm | 自然 |

Mixed emotions: `--emotion "happy:0.6,sad:0.4"`

## License

MIT License

## Acknowledgments

- [IndexTTS](https://github.com/index-tts/index-tts) — Original PyTorch implementation
- [MLX](https://github.com/ml-explore/mlx) — Apple's ML framework
- [OmniVoice](https://github.com/user/omnivoice) — Multi-language TTS engine
- [EmotiVoice](https://github.com/netease-youdao/EmotiVoice) — Chinese emotion TTS
- [F5-TTS](https://github.com/SWivid/F5-TTS) — Reference audio TTS
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) — Multi-style TTS
