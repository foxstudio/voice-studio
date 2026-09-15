# OmniVoice 与 CosyVoice 迭代评估

评估日期：2026-08-25

## 结论

| 引擎 | 当前状态 | 建议 | 风险 |
| --- | --- | --- | --- |
| OmniVoice | 已升级到 Python 包 0.2.1；官方模型权重 0.6B、约 3.27 GB，原目录与固定校验版本不变 | 保持 0.2.x 版本上限；下一迭代再评估音色提示缓存和可选文字规范化，不默认开启新依赖 | 中低 |
| CosyVoice | CosyVoice-300M-SFT 与 CosyVoice-300M；22.05 kHz；Mac 上实际走 CPU | 不原地替换。新增独立的 `cosyvoice3-zero-shot` 实验引擎，保留现有两个 v1 引擎；只有 Mac 实测速度与质量达标后才考虑提高推荐级别 | 中高 |

一句话判断：OmniVoice 小版本运行时升级已完成并通过离线 MPS 验证；CosyVoice 按本轮决策保持不动。CosyVoice 3 仍只适合作为以后单独的质量 POC，不能覆盖现有模型。

## 当前集成事实

### OmniVoice

- Voice Studio 当前安装的是 `omnivoice 0.2.1`，项目依赖固定为 `omnivoice>=0.2.1,<0.3`。
- 推理链路使用 `OmniVoice.from_pretrained()`、`OmniVoiceGenerationConfig.from_dict()` 和 `model.generate()`；Apple Silicon 使用 MPS，并为稳定性强制 eager attention 与 float32。
- 模型下载固定到已校验 revision，并逐文件校验大小与大文件 SHA-256。
- 官方当前模型仓库的两个权重大文件、tokenizer 和音频 tokenizer 与本项目固定版本的大小及 SHA-256 一致。因此升级 Python 包不需要重新下载 3.27 GB 权重。
- 代码是 Apache-2.0；预训练权重仍是 CC-BY-NC，仅限非商业用途。这个限制不会因包升级而消失。

### CosyVoice

- 当前两个引擎分别固定到 `CosyVoice-300M-SFT` 和 `CosyVoice-300M`，每份本地目录约 5.4 GB，采样率 22.05 kHz。
- 外部 CosyVoice 运行时代码已经包含 `CosyVoice3` 和 `AutoModel` 的识别逻辑；因此主要工作不是再复制一套仓库，而是新增模型目录、版本化引擎契约和适配器。
- 当前 Voice Studio worker 只认识两个旧模型目录，并按 v1 约定直接传入参考台词。CosyVoice 3 要求 prompt 带 `<|endofprompt|>` 协议前缀，且使用 `cosyvoice3.yaml`、`speech_tokenizer_v3.onnx` 和 24 kHz 输出，不能直接替换目录名。
- 官方运行时的设备选择是 CUDA，否则 CPU；没有 MPS 分支。Mac 上可以运行，但没有官方 Apple GPU 加速路径。

## OmniVoice 0.1.5 → 0.2.1（已实施）

### 实施与验证结果

- 只更新了约 0.17 MB 的 Python 运行库；没有下载、移动或改写 3.27 GB 模型权重。
- 单条和批处理统一复用同一套 MPS 加载规则：eager attention + float32，避免批处理绕开稳定性保护。
- 在 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 下使用本机现有权重完成真实 MPS 生成：8 步短试听输出 2.76 秒、24 kHz 有效音频；含冷加载总耗时 5.11 秒，RTF 1.85，RMS 0.112、峰值 0.5，所有采样有限且非空。
- 真实短试听证明升级后的包、现有权重和 MPS 路径可以协同工作；它不是 0.1.5/0.2.1 的主观音质 A/B，因此不把运行库升级描述成“模型音质升级”。

### 收益

官方 0.2.1 增加了跨进程保存/加载 `VoiceClonePrompt`、延迟 ASR 加载修复、ASR 设备选择、参考音频时长读取优化和可选文字规范化；0.2.0 还包含标点修复与输出 padding/fade 控制。公开入口与本项目正在使用的三类 API 保持存在。

对 Voice Studio 最有价值的不是“模型音质突然升级”，而是：

1. 参考音色特征可以预计算并跨任务复用，长批次首段开销有机会下降。
2. 数字、日期、货币的可选规范化可减少朗读错误。
3. ASR 延迟加载与参考音频读取修复能减少不必要的启动和 I/O 成本。

### 风险

1. 官方 Apple Silicon 安装说明固定 PyTorch/torchaudio 2.8.0，而当前项目环境是 2.10.0；现状虽然已运行，但升级验收必须覆盖 MPS，不能只在 CPU 导入成功。
2. MPS 历史上出现过噪声输出问题。本项目的 eager + float32 是稳定性保护，升级时不能顺手删掉。
3. `OmniVoiceGenerationConfig` 从包内部模块导入，不是最稳定的顶层公共入口；需要用契约测试锁住。
4. `normalize_text=True` 在 Apple Silicon 上需要额外的 WeTextProcessing/pynini 安装条件。首轮升级不应默认开启，避免扩大环境依赖。
5. 官方宣传的极低 RTF 来自 NVIDIA/H100 等基准；FlashInfer 也是 CUDA 路径，不能据此推算 Mac 速度。

### 后续可选迭代

1. 固定四组短样本：中文克隆、英文克隆、无参考声音设计、非语言标签/拼音纠音；再加一组长文本分段。
2. 若要判断主观音质是否变化，用同一权重、同一输入分别运行 0.1.5 与 0.2.1，记录冷启动、热启动、峰值内存和盲听结果。
3. 单独评估 `VoiceClonePrompt.save()/load()`，再决定是否把音色提示缓存接入正式音色库。
4. 首轮继续不启用文字规范化；只有 WeTextProcessing/pynini 的 Mac 安装门验证通过后再增加开关。

验收门：所有固定样本无噪声/空音/截断；现有 API 与批处理测试通过；MPS 速度不明显退化；人工听感不低于 0.1.5。

## 其他引擎升级审计

| 引擎/运行库 | 当前状态 | 官方变化与判断 | 本轮动作 |
| --- | --- | --- | --- |
| MLX-Audio | 0.4.4 | 0.4.6 明确修复 Qwen3-ASR 批处理 logits processor、改善音频读取和 Qwen3-TTS 缓存，并补充 Metal 精度测试；0.4.7 开始抬高 Transformers 依赖，0.5.0 又改为内置 MLX-LM 组件 | 已升级并限制为 `>=0.4.6,<0.4.7`；真实 Qwen3-ASR 离线短样本 2.14 秒音频耗时 4.16 秒，返回非空文字与 1 个有效分段 |
| IndexTTS | 当前 IndexTTS 2 | 官方已发布 IndexTTS 2.5，新增日语/西语/阿语、语速控制并宣称更快；但它使用新的 `infer_v2_5.py`、配置和权重，不兼容当前 2.0 目录 | 不原地升级；建议以后新增 `indextts-2.5` POC，引入新模型前先验证现有 MLX/自定义适配边界 |
| F5-TTS | 独立环境 1.1.20 | 1.1.22 修复 `fix_duration` 在长文本分块时被重复计算、导致成品时长成倍膨胀的问题，和当前长文本/定时配音高度相关 | 明确的高优先候选，但本轮不直接改：当前是外部 editable checkout，先补受管版本与回滚契约，再升 1.1.22 |
| Faster Whisper | large-v3-turbo 模型入口存在；主环境未安装运行库 | 官方当前运行库 1.2.1 有时间戳恢复等修复；当前问题首先是缺运行库，不是旧版本升级 | 单列为“补齐运行环境”，不要和模型升级混在一起；需确认安装体积与跨平台依赖后实施 |
| Qwen3-TTS | 0.6B 8-bit 社区 PoC | 官方/MLX-Audio 已提供 1.7B 与更完整的 Base、CustomVoice、VoiceDesign 路径，但需要新模型与适配验证 | 不直接替换；保留为模型质量 POC 候选 |
| VibeVoice ASR | 已有 MLX 4bit/8bit | 微软新增 1.58 GB BitNet CPU 运行时，定位是无 GPU 的新后端，不是现有 MLX 量化权重的小升级 | 不替换现有 4/8bit；如要覆盖低内存或 Windows/CPU 场景，单独做 BitNet POC |
| EmotiVoice | 外部仓库与 0.2.0 环境 | 官方没有可确认的同代新模型/稳定 release 提升 | 不动 |
| Confucius4 MLX | 本机受管分支含必要本地兼容修正 | 上游运行库继续演进，但直接覆盖会丢失当前本机修正 | 不动，先把修正变成可复现补丁与版本契约 |
| MiMo / 豆包云端 | 当前 API 模型版本 | 云端换版本会改变费用、接口和用户可见结果，不能按 Python 小包升级处理 | 不动；只有官方迁移通知或固定对比证据时再切换 |

本轮实际升级只有两项：OmniVoice 0.2.1 和 MLX-Audio 0.4.6。CosyVoice 未改；其他项目均未下载新模型、未覆盖外部运行环境。

## CosyVoice 300M → Fun-CosyVoice 3

### 潜在收益

官方把 Fun-CosyVoice 3 定位为 0.5B 多语种零样本 TTS，支持 9 种常用语言、18+ 中文方言/口音、发音修正、文字规范化、双向流式和自然语言指令。官方自报评测中，基础版相较 CosyVoice2 在中文/英文说话人相似度与 hard 集内容一致性上更好；这些数据说明它值得测试，但不能替代本机实测。

### 下载与存储

- 官方 Hugging Face 完整仓库约 9.75 GB，包含 base/RL 两套 LLM 和部分替代运行文件。
- 只保留基础推理必需文件约 5.43 GB：`cosyvoice3.yaml`、`llm.pt`、`flow.pt`、`hift.pt`、`campplus.onnx`、`speech_tokenizer_v3.onnx` 与 `CosyVoice-BlankEN/`。
- 国内可使用官方 ModelScope 仓库 `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`。正式接入应像其他受管模型一样固定 revision、文件清单和校验值，不能运行时静默联网。

### 为什么不能原地替换

1. 新模型为 24 kHz，旧引擎契约是 22.05 kHz。
2. 新模型使用 v3 tokenizer 和 `cosyvoice3.yaml`，文件协议不同。
3. Zero-shot prompt 必须带官方协议前缀；现有 worker 直接传参考台词会触发断言或产生错误行为。
4. 现有 SFT 引擎依赖 `spk2info.pt` 的官方预置 speaker；CosyVoice 3 主模型以 zero-shot/instruct 为主，不能保证原 SFT 音色列表等价迁移。
5. 老任务、预设和音色库引用的是现有 engine ID。覆盖会改变历史任务的可重放性。

### Mac 风险

1. 官方代码只选择 CUDA 或 CPU，没有 MPS。新模型即使能跑，也可能比当前本地 TTS 慢很多。
2. vLLM、TensorRT-LLM 和官方低延迟流式优化主要面向 NVIDIA；官方还明确建议 vLLM 使用独立环境，避免破坏原环境。
3. 可选 `ttsfrd` 的官方 wheel 是 Linux cp310，Mac 需要走 WeText fallback。
4. 最小模型仍需新增约 5.43 GB；完整仓库约 9.75 GB。下载、校验和磁盘清理必须纳入模型中心，而不是散落在外部运行时目录。

### 推荐实施顺序

1. 新增 `cosyvoice3-zero-shot`，不要改动 `cosyvoice-sft` 和 `cosyvoice-zero-shot`。
2. 在 provider/worker 边界增加 v3 适配：独立模型目录、24 kHz、prompt 协议、必需文件清单和健康检查。
3. 首轮只做 base 模型、非流式 zero-shot，不引入 vLLM、TensorRT、RL 权重或 instruct UI。
4. 用相同参考音频、参考台词和中英文目标句，对比现有 CosyVoice-300M、CosyVoice 3 与当前主力 IndexTTS v2；记录冷启动、热生成、峰值内存和人工听感。
5. 只有 CPU 速度可接受且质量稳定，再增加 instruct 与方言能力；否则保留为“高质量但慢速”的实验引擎，不进入自动推荐。
6. 回滚只需隐藏/移除新 engine ID；旧模型、旧任务和旧预设不受影响。

验收门：参考音色相似度和中文自然度至少有一项稳定优于旧 CosyVoice；无错读/漏读/空音；24 kHz 契约贯穿试听、批处理和项目导出；Mac 生成耗时有明确可接受范围。

## 本轮链接与按钮审计

- 本地 TTS：启动/停止、环境检查、生成试听、模型来源。
- 本地 ASR：启动/停止、环境检查、模型来源；不显示“生成试听”。
- 云端 TTS：连接检查、生成试听、官方说明；不显示虚假的本地“启动/停止”。
- 云端 ASR：连接检查、官方说明；不显示“生成试听”。
- BS-RoFormer：它是共享模型资源，不是引擎，只显示安装/删除和来源详情。
- 所有本地可运行引擎都有模型或运行时来源；云端引擎没有本体模型下载是正常设计，并均提供官方说明。
- 已把 CosyVoice 仓库、Faster Whisper 权重、MiMo 文档和火山引擎文档换成当前规范地址；旧地址虽可跳转，但不再作为页面主链接。

## 主要来源

- [OmniVoice 官方仓库](https://github.com/k2-fsa/OmniVoice)
- [OmniVoice 官方 Releases](https://github.com/k2-fsa/OmniVoice/releases)
- [OmniVoice 官方模型页](https://huggingface.co/k2-fsa/OmniVoice)
- [MLX-Audio 官方 Releases](https://github.com/Blaizzy/mlx-audio/releases)
- [IndexTTS 2.5 官方说明](https://github.com/index-tts/index-tts/blob/main/docs/README_zh.md)
- [F5-TTS 官方 Releases](https://github.com/SWivid/F5-TTS/releases)
- [Faster Whisper 官方 Releases](https://github.com/SYSTRAN/faster-whisper/releases)
- [Microsoft VibeVoice 官方仓库](https://github.com/microsoft/VibeVoice)
- [CosyVoice 官方仓库](https://github.com/QwenAudio/CosyVoice)
- [Fun-CosyVoice3 官方模型页](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)
- [Fun-CosyVoice3 ModelScope](https://modelscope.cn/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)
