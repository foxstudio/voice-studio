# Voice Studio

Voice Studio 是面向 Apple Silicon 的多引擎语音工作台。它把本地 TTS、声音克隆、声音设计、云端合成、长文本分段、批量生成、ASR 校对和视频本土化流程放在同一个 WebUI 和 REST API 里。

开发或使用 AI Agent 修改本仓库前，请先阅读 [AGENTS.md](AGENTS.md) 和 [架构文档导航](docs/architecture/README.md)。

> 重要说明：仓库只包含代码、文档和测试；不包含模型权重、音色库音频、生成结果、本地数据库或 API Key。模型和音色需要使用者自行下载、转换、上传或配置。

![Voice Studio WebUI](docs/assets/voice-studio-webui.png)

早期功能介绍视频：[Bilibili：Voice Studio 早期 WebUI 介绍](https://www.bilibili.com/video/BV1cdLd6TEUA)

## 核心能力

- **WebUI 工作台**：引擎管理、音色管理、语音合成、视频本土化与设置。
- **REST API**：FastAPI 后端提供生成、长文本、批量、任务、音色、引擎、ASR、历史记录等接口。
- **本地引擎**：IndexTTS v2、OmniVoice、EmotiVoice、F5-TTS、CosyVoice、Qwen3-TTS MLX、Confucius4 MLX 等。
- **云端引擎**：小米 MiMo V2.5、豆包 / 火山引擎 TTS 与声音复刻相关流程。
- **音色来源灵活**：可以使用音色库里的参考音频，也可以临时传入参考音频，还可以使用模型预置音色或声音设计。
- **声音设计**：部分引擎支持用文字描述音色，例如“温暖、清晰、适合知识视频旁白的中文女声”，不一定要先准备真人参考音频。
- **长文本与批量**：支持自动分段、逐段生成、失败重试、ASR 校对、合并输出和 JSON 批处理。
- **视频本土化**：围绕视频源、参考片段、TTS 结果和多段草稿进行本土化配音工作流编排。

## 目录结构

```text
backend/app/        FastAPI 后端、任务队列、引擎路由、音色库、设置与数据库访问
frontend/src/       SvelteKit WebUI
mlx_indextts/       MLX IndexTTS 推理核心与模型结构
scripts/            音色导入、批量处理、质量校验和迁移辅助脚本
docs/               引擎参数、批量生成、架构说明和 RFC
tests/              自动化测试，给开发者和 CI 验证项目是否被改坏
```

运行时数据默认放在 `~/VoiceStudio`，模型权重统一放在 `~/VoiceStudio/models/`。仓库中只保存模型来源和完整性规则，不提交权重。旧版仓库 `models/` 目录仍可兼容读取。

本地数据的保留、自动清理、模型与引擎目录规则见 [Voice Studio 本地数据与模型规则](docs/VOICE_STUDIO_DATA_POLICY.md)。
完整应用、Python wheel、支持平台和模型分发的发布边界见 [开源发布边界](docs/OPEN_SOURCE_RELEASE.md)。

## 快速开始

### 前置要求

- macOS Apple Silicon：原生启动
- Linux：原生启动，默认 CPU；具体引擎能力见[运行能力说明](docs/architecture/RUNTIME_CAPABILITIES.md)
- Windows：通过 WSL 2 启动，不提供 Windows 原生 MLX 后端
- Python 3.10+；Windows 用户只需在 WSL 2 发行版内安装，不要求 Windows 本机另装 Python
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 / pnpm 11.19.0（与前端构建工作区一致）
- 可选：`ffmpeg`，用于音频转码、视频抽音频和部分本土化流程

### 安装依赖

Windows 用户请先进入 WSL 2，并在 WSL 终端中执行这一节的仓库克隆和依赖安装命令。

```bash
git clone https://github.com/foxstudio/voice-studio.git
cd voice-studio

# 仅使用 MLX IndexTTS 核心 CLI 时选择这一条
uv sync --locked

# 运行完整 Voice Studio 应用时选择这一条；所有需要的 extra 必须在同一次同步中声明
uv sync --locked --extra server --extra convert --extra indextts2 --extra asr --extra video_localization

# 前端依赖
cd frontend
pnpm install --frozen-lockfile
cd ..
```

上面两条 `uv sync` 是二选一的安装档位。`uv sync` 默认会精确同步环境；如果分多次执行
不同的 `--extra`，后一次可能移除前一次没有声明的可选依赖。需要自定义能力组合时，也应
把所需的多个 `--extra` 写在同一条命令中。

### 启动服务

启动前可先检查系统、Python 和本机命令依赖：

```bash
./start.sh --doctor
```

```bash
./start.sh
```

首次启动会初始化本机数据和可选能力；后端健康检查默认最多等待 120 秒。需要调整时可设置 `VOICE_STUDIO_BACKEND_STARTUP_TIMEOUT`。

Windows PowerShell 会自动转交给 WSL 2，并在 WSL 内检查 Python、uv、pnpm 等实际运行依赖：

```powershell
.\start.ps1 -Doctor
.\start.ps1
```

启动成功后打开：

- WebUI: http://localhost:5173
- API: http://localhost:8000
- 健康检查: http://localhost:8000/api/health
- OpenAPI 接口说明: http://localhost:8000/docs

外部工具需要语音转写时，先通过 `/api/asr/tasks` 上传音频并轮询任务；需要制作严格字幕
时间轴时，再调用 `/api/asr/{transcription_id}/subtitle-evidence` 获取逐字时间、停顿和
字幕入点。接口不会替调用方决定最终字幕文字，也不会向外暴露本机文件路径。自动化工具
应明确选择已安装的本地 ASR 引擎；没有用户当次确认时，不应改用云端 ASR。

如果端口被旧的 Voice Studio 服务占用，可以强制重启：

```bash
./start.sh --force
```

普通启动使用不热重载的稳定后端，适合运行 ASR、视频处理等长任务。需要修改后端代码并
自动重载时，使用：

```bash
./start.sh --dev-reload
```

这个模式只热重载 Web/API 进程；视频本土化 operation worker 会在独立的稳定进程中运行，
避免代码保存动作中断正在执行的长任务。

准备长期本机运行或检查开源发布包时，可以构建静态 WebUI，并由 FastAPI 在同一地址提供
页面和 API：

```bash
./start-production.sh
```

此模式以前台单进程运行，打开 http://127.0.0.1:8000；按 `Ctrl+C` 可完整停止。
Windows PowerShell 使用 `./start.ps1 -Production`，仍会自动转交 WSL 2。默认的
`./start.sh` 开发启动方式保持不变，继续使用 5173 和 8000 两个端口。

## 模型准备

首次打开时没有模型和音色是正常状态。例如输入文字后点击生成，若提示“尚未安装模型”，
请先到“引擎与模型”查看对应来源与安装说明，再准备参考音色；安装应用不等于模型也已安装。

本仓库不提供模型权重。软件管理的模型统一放在 `~/VoiceStudio/models/`；如果设置了
`VOICE_STUDIO_MODELS_DIR`，则使用你指定的目录。源码目录只放代码，不建议再把新模型
下载到仓库内。

引擎管理页只会对来源、版本、完整性和许可均已确认的模型显示“下载模型”按钮；下载完成后
会自动从上述目录加载。需要手动安装的模型会显示官方来源和建议位置，不会在生成时偷偷联网。

### IndexTTS v2

默认管理目录：

```text
~/VoiceStudio/models/mlx-indexTTS-2.0/
```

IndexTTS v2 官方发布的是 PyTorch 权重，Apple Silicon 的 MLX 版本需要在本机转换一次；
这不是每次运行都转换。转换命令会把声音克隆所需的 MaskGCT、W2V-BERT 和 CAM++ 固定
版本一起放进同一个输出目录，之后生成时完全离线。旧版仓库内
`models/mlx-indexTTS-2.0/` 仍可兼容读取，但不再作为新安装位置。

国内优先从 [IndexTTS-2 ModelScope](https://modelscope.cn/models/IndexTeam/IndexTTS-2)
下载原始模型；国际用户可使用
[IndexTTS-2 Hugging Face](https://huggingface.co/IndexTeam/IndexTTS-2)。下面以 Hugging Face
命令为例。配套 MaskGCT 权重是 CC BY-NC 4.0，仅限符合该许可的用途；运行转换命令前请先确认。

示例流程：

```bash
# 沿用上面已安装的完整应用环境，不再次缩减 extras

export VOICE_STUDIO_MODELS_DIR="${VOICE_STUDIO_MODELS_DIR:-$HOME/VoiceStudio/models}"
mkdir -p "$VOICE_STUDIO_MODELS_DIR"

# 按 IndexTTS 官方方式下载原始模型
uv tool install "huggingface-hub[cli,hf_xet]"
hf download IndexTeam/IndexTTS-2 \
  --revision 740dcaff396282ffb241903d150ac011cd4b1ede \
  --local-dir "$VOICE_STUDIO_MODELS_DIR/IndexTTS-2-pytorch"

# 转换为 MLX 格式，并把 WAV 声音克隆的固定版本配套模型收进同一目录
uv run --no-sync voice-studio convert \
  --model-dir "$VOICE_STUDIO_MODELS_DIR/IndexTTS-2-pytorch" \
  --output "$VOICE_STUDIO_MODELS_DIR/mlx-indexTTS-2.0"
```

转换后的模型目录通常较大，不应提交到 Git。转换完成后，原始 PyTorch 目录可以在确认
MLX 模型可用后自行移到回收站；Voice Studio 运行时只需要转换后的目录。

### 其他本地引擎

不同引擎的模型来源和目录不同，建议先看对应文档：

- [IndexTTS v2](docs/engines/indextts-v2.md)
- [OmniVoice](docs/engines/omnivoice.md)
- [EmotiVoice](docs/engines/emotivoice.md)
- [F5-TTS](docs/engines/f5-tts.md)
- [CosyVoice](docs/engines/cosyvoice.md)
- [Qwen3-TTS MLX](docs/engines/qwen3-tts-mlx.md)
- [Qwen3-ASR MLX](docs/engines/qwen3-asr-mlx.md)
- [Confucius4 MLX INT8](docs/engines/confucius4-mlx-int8.md)

通用原则：

- 新下载的本地权重统一放在 `~/VoiceStudio/models/` 或 `VOICE_STUDIO_MODELS_DIR`。
- 单个模型需要放在别处时可使用对应的 `VOICE_STUDIO_*_MODEL_DIR`；它只改变读取位置，
  新的一键下载仍写入统一模型目录。
- 仓库内 `models/` 仅用于兼容旧安装，不属于开源发布物。
- 外部引擎仓库可以用环境变量指定，例如 `VOICE_STUDIO_COSYVOICE_ROOT`、`VOICE_STUDIO_F5_TTS_ROOT`、`VOICE_STUDIO_QWEN3_TTS_ROOT`。
- 缺少模型时，WebUI 会尽量隐藏或禁用相关入口，并在引擎管理页显示状态。

## 音色库与声音设计

仓库不包含音色库。音色库是每个使用者自己的本地数据，默认位于：

```text
~/VoiceStudio/voices/
```

本地数据库默认位于：

```text
~/VoiceStudio/config/voice_studio.db
```

这些文件不会随 Git 仓库上传。

### 使用音色库

适合有参考音频的声音克隆场景：

1. 启动服务并打开 http://localhost:5173
2. 进入「音色管理」
3. 上传 `wav` / `mp3` 等参考音频
4. 填写音色名称、参考台词、授权状态和标签
5. 在「语音合成」页面选择该音色

IndexTTS v2、F5-TTS、CosyVoice Zero-Shot、OmniVoice、MiMo voiceclone、豆包声音复刻等流程会按各自能力使用参考音频。

### 使用临时参考音频

如果不想把声音保存进音色库，可以在 API 或部分工作流里直接传入 `reference_audio_path`。这种方式适合一次性任务、外部 Agent 调用或项目级配音。

### 使用模型预置音色

部分引擎自带官方预置音色，例如 CosyVoice SFT、EmotiVoice、Qwen3-TTS 或云端 TTS 的官方 speaker。此时不一定需要本地音色库。

### 使用声音设计

如果没有参考音频，可以优先尝试支持声音设计的引擎。声音设计通过文字描述目标声音，例如：

```text
温暖、清晰、语速适中，适合知识视频旁白的中文女声。
```

是否可用取决于具体引擎和本地模型是否已安装。WebUI 会根据引擎能力展示对应参数。

## 云端引擎

云端能力不会把 API Key 写进仓库。你可以在 WebUI「设置」页面保存，或使用环境变量。

### 小米 MiMo

MiMo V2.5 支持预置音色、声音设计和声音复刻。配置方式：

```bash
export MIMO_API_KEY="your-api-key"
```

### 豆包 / 火山引擎

豆包相关能力包括 TTS 预置音色、声音复刻训练和复刻音色合成。配置方式：

```bash
export VOLCENGINE_API_KEY="your-api-key"
```

云端声音复刻通常会上传参考音频到服务商。Voice Studio 在相关流程里保留确认开关，请确保你拥有音频授权。

## API 示例

```bash
curl http://localhost:8000/api/health

curl http://localhost:8000/api/engines

curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "你好，这是一个语音合成测试。",
    "engine_id": "indextts-v2",
    "voice_id": "YOUR_VOICE_ID",
    "output_format": "mp3"
  }'
```

更多参数说明：

- [引擎参数手册](docs/VOICE_STUDIO_ENGINE_PARAMETERS.md)
- [批量合成指南](docs/VOICE_STUDIO_BATCH_AGENT.md)
- [豆包集成 RFC](docs/DOUBAO_VOICE_INTEGRATION_RFC.md)

## CLI 示例

```bash
uv run voice-studio generate \
  -m "${VOICE_STUDIO_MODELS_DIR:-$HOME/VoiceStudio/models}/mlx-indexTTS-2.0" \
  -r reference.wav \
  -t "你好，世界！" \
  -o output.wav
```

情感控制：

```bash
uv run voice-studio generate \
  -m "${VOICE_STUDIO_MODELS_DIR:-$HOME/VoiceStudio/models}/mlx-indexTTS-2.0" \
  -r reference.wav \
  -t "今天真是太开心了！" \
  -o output.wav \
  --emotion happy \
  --emo-alpha 0.6
```

Python 调用：

```python
from pathlib import Path

from mlx_indextts.generate_v2 import IndexTTSv2

model_dir = Path.home() / "VoiceStudio" / "models" / "mlx-indexTTS-2.0"
tts = IndexTTSv2(model_dir)
audio = tts.generate(
    text="你好",
    reference_audio="reference.wav",
    output_path="output.wav",
    emotion="happy",
    emo_alpha=0.6,
)
```

## 开发与验证

`tests/` 是开发验证目录，保留在远端仓库里是有用的。它不会参与普通运行，但可以帮助开发者确认改动没有破坏功能。

```bash
uv run pytest tests/ -q
uv run ruff check
pnpm --dir frontend run check
```

GitHub Actions 也会运行测试：

```text
.github/workflows/test.yml
```

## 本仓库不会提交的内容

这些内容默认属于本机数据或大文件资产，不应进入 Git：

- `models/` 模型权重
- `~/VoiceStudio/voices/` 音色库音频
- `~/VoiceStudio/outputs/` 生成结果
- `~/VoiceStudio/config/voice_studio.db` 本地数据库
- `.env`、API Key、私钥、token
- 临时视频、音频、日志、缓存和前端构建产物

如果你 fork 或二次开发，提交前建议检查：

```bash
git status --short --ignored
git ls-files -o --exclude-standard
git ls-files -ci --exclude-standard
```

## 反馈问题

如果遇到问题，建议优先提交 GitHub Issue：

[https://github.com/foxstudio/voice-studio/issues](https://github.com/foxstudio/voice-studio/issues)

反馈时请尽量带上：

- macOS 版本和芯片型号
- Python、uv、Node、pnpm 版本
- 使用的引擎名称
- 模型目录是否存在，以及是否能在「引擎管理」里看到状态
- WebUI 或 API 的报错文本
- 后端日志：默认 `~/VoiceStudio/logs/backend.log`
- 前端日志：默认 `~/VoiceStudio/logs/frontend.log`
- 最小复现步骤

如果问题和云端引擎有关，请不要公开粘贴 API Key、完整鉴权头或私人音频。可以只提供错误码、request id、logid 和脱敏后的请求参数。

## 许可证

项目自有代码采用 MIT License；仓库内的上游兼容实现、内嵌源码、外部运行时和模型权重
适用各自条款。`mlx_indextts` 的文件级来源已经按官方 IndexTTS 1.5 和 2.0 固定版本完成
映射：v1 映射部分保留 Apache-2.0，v2 映射部分适用 Bilibili Model Use License
Agreement，MLX 上游和内嵌第三方源码继续保留各自通知。因此整个仓库和 wheel 不能简化
描述成“全部无条件 MIT”。源码包不含模型权重；下载、转换和使用权重仍按对应模型来源
单独判断。完整来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，具体分发要求见
[IndexTTS 分发条款核对表](docs/engines/indextts-distribution-terms.md)。

### IndexTTS 2.0 衍生作品声明

`mlx_indextts` 包含基于 IndexTTS 2.0 的映射实现，适用 Bilibili Model Use License
Agreement。按该协议第 4.1(a) 条要求声明：

> Any modifications made to the original model in this Derivative Work are not endorsed,
> warranted, or guaranteed by the original right-holder of the original model, and the
> original right-holder disclaims all liability related to this Derivative Work.

即：本作品对原始模型所做的任何修改，均未获得原始权利人的认可、担保或保证，原始权利人对本
衍生作品不承担任何责任。

## 致谢

- [IndexTTS](https://github.com/index-tts/index-tts)
- [MLX](https://github.com/ml-explore/mlx)
- [OmniVoice](https://github.com/k2-fsa/OmniVoice)
- [EmotiVoice](https://github.com/netease-youdao/EmotiVoice)
- [F5-TTS](https://github.com/SWivid/F5-TTS)
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice)
