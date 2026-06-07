# MLX-IndexTTS

IndexTTS for Apple Silicon using MLX. Zero-shot text-to-speech with voice cloning capabilities.

## Features

- Run IndexTTS 2.0 natively on Apple Silicon
- RTF ~0.5 (2x faster than real-time on M2 Max)
- Voice cloning from reference audio
- **v2.0**: Emotion control (8 emotions)
- Voice Studio WebUI uses IndexTTS v2 as the main local engine

## Requirements

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/user/mlx-indextts.git
cd mlx-indextts

# Basic install (generation only)
uv sync

# With model conversion support (requires torch)
uv sync --extra convert

# With local ASR support (Qwen3-ASR MLX via mlx-audio)
uv sync --extra asr
```

## Quick Start

### 1. Convert Model (auto-detects version)

```bash
# Convert IndexTTS 2.0
uv run mlx-indextts convert \
    --model-dir /path/to/indexTTS-2 \
    -o models/mlx-indexTTS-2.0
```

### 2. Generate Speech (auto-detects version)

```bash
# v2.0
uv run mlx-indextts generate \
    -m models/mlx-indexTTS-2.0 \
    -r reference.wav \
    -t "你好，这是一个语音合成测试。" \
    -o output.wav

# v2.0 with emotion control
uv run mlx-indextts generate \
    -m models/mlx-indexTTS-2.0 \
    -r reference.wav \
    -t "今天真是太开心了！" \
    -o output.wav \
    --emotion happy --emo-alpha 0.6
```

### 3. Pre-compute Speaker (Faster Inference)

Pre-compute speaker conditioning to skip audio preprocessing on subsequent generations.

```bash
# v2.0
uv run mlx-indextts speaker \
    -m models/mlx-indexTTS-2.0 \
    -r reference.wav \
    -o speaker_v20.npz

# Use pre-computed speaker (much faster loading)
uv run mlx-indextts generate \
    -m models/mlx-indexTTS-2.0 \
    -r speaker_v20.npz \
    -t "你好，世界！" \
    -o output.wav
```

## Python API

```python
# v2.0
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

## CLI Options

```
mlx-indextts generate [OPTIONS]

Required:
  -m, --model        Model directory
  -r, --ref-audio    Reference audio (.wav or .npz)
  -t, --text         Text to synthesize
  -o, --output       Output file

Common options:
  --max-tokens       Max mel tokens (default: 1500 for v2.0)
  --temperature      Sampling temperature (default: 0.8 for v2.0)
  --seed, -s         Random seed for reproducibility
  -v, --verbose      Verbose output
  -p, --play         Play audio after generation
  --quantize, -q     Runtime quantization: 4, 8, or fp32

v2.0 only:
  --emotion          Emotion: happy/sad/angry/afraid/disgusted/melancholic/surprised/calm
  --emo-alpha        Emotion intensity 0.0-1.0 (default: 0.6, recommend ≤ 0.8)
  --diffusion-steps  Diffusion steps (default: 25)
  --cfg-rate         CFG rate (default: 0.7)
```

## Current Engine Policy

Voice Studio exposes IndexTTS v2 as the main IndexTTS engine. IndexTTS v2 covers
the voice-cloning workflow used by v1.5 and adds emotion control, longer text
handling, and the S2Mel/BigVGAN2 pipeline. Legacy v1.5 source files may remain
for conversion or benchmark reference, but the WebUI and API no longer expose it
as a production engine.

For speech recognition, Voice Studio now separates cloud and local engines:

- `mimo-v2.5-asr`: Xiaomi MiMo cloud speech recognition
- `qwen3-asr-mlx`: local Qwen3-ASR MLX slot, backed by `mlx-audio` when installed

The local Qwen engine checks model files and runtime availability in Engine Hub
before you run a transcription job.

Current subtitle policy:

- MiMo public ASR is treated as transcript-first
- Qwen local ASR provides native segment timestamps and SRT export
- MiMo transcript records that retain their source audio can now call
  `POST /api/asr/{transcription_id}/timestamps` to supplement timestamps locally
  through the Qwen path and unlock SRT export

## Supported Emotions (v2.0)

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

## Performance

| Metric | v2.0 |
|--------|------|
| RTF (M2 Max) | ~1.3 |
| Load time (.wav) | ~9s |
| Load time (.npz) | ~1.5s |

## License

MIT License

## Acknowledgments

- [IndexTTS](https://github.com/index-tts/index-tts) - Original PyTorch implementation
- [MLX](https://github.com/ml-explore/mlx) - Apple's ML framework
