# Voice Studio｜官方资料核验附录

> 核验日期：2026-06-05

## IndexTTS / IndexTTS2

- 官方仓库：https://github.com/index-tts/index-tts
- 核验结论：
  - IndexTTS2 官方描述包含情绪化语音合成、多输入模态情绪控制。
  - 官方描述包含音色与情绪解耦。
  - 精准时长控制作为模型贡献被描述，但 release 说明中标注当前 release 尚未启用，因此 PRD 中作为预留能力。

## OmniVoice

- 官方仓库：https://github.com/k2-fsa/OmniVoice
- 核验结论：
  - 官方定位为 600+ 语言 zero-shot TTS。
  - 支持 voice cloning 和 voice design。
  - 支持非语言符号和 pinyin/phoneme 发音修正。
  - 安装说明包含 Apple Silicon 的 PyTorch 安装方式。

## Xiaomi MiMo-Skills

- 官方仓库：https://github.com/XiaomiMiMo/MiMo-Skills
- 核验结论：
  - `mimo-v2-5-tts` 支持预设音色、声音设计、声音克隆、情绪风格、方言、唱歌、自然语言控制、Director Mode、音频标签控制。
  - 需要 `MIMO_API_KEY`，因此归类为云端/API 引擎预留，不作为首期本地模型实现。

## MLX

- 官方仓库：https://github.com/ml-explore/mlx
- 核验结论：
  - MLX 是 Apple Silicon 机器学习 array framework。
  - 支持 CPU/GPU、统一内存等特性。
  - 作为技术方案候选，不在 PRD 中写死。

## 参考项目

- F5-TTS：https://github.com/swivid/f5-tts
- GPT-SoVITS：https://github.com/RVC-Boss/GPT-SoVITS
- CosyVoice：https://github.com/FunAudioLLM/CosyVoice
- TTS Audio Suite：https://github.com/diodiogod/TTS-Audio-Suite
