# OmniVoice

> k2-fsa 团队出品的大规模多语言零样本 TTS，支持 600+ 语言声音克隆与声音设计。

## 基本信息

| 项目 | 详情 |
|---|---|
| 开发者 | k2-fsa 团队 |
| 架构 | 扩散模型 (Diffusion) |
| 语言覆盖 | 600+ 语言 (646 种) |
| 训练数据 | 581k 小时，全部来自开源数据 |
| 许可证 | Apache 2.0 |
| 仓库 | [github.com/k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice) |
| 论文 | [arXiv 2604.00688](https://arxiv.org/html/2604.00688v1) |

## 核心能力

- **600+ 语言零样本 TTS**：世界上覆盖语言最多的开源 TTS 模型
- **声音克隆**：零样本声音克隆，即使是小语种也能获得高质量的克隆效果
- **声音设计**：通过自然语言描述创建全新声音
- **非语言标签**：支持笑声、叹气、吸鼻、疑问、惊讶、不满等正文内标签

## 在本项目中的适配

- 本地运行，基于 MLX 推理
- 采样率：24000 Hz
- 支持语言：自动检测、中文、英文、日语、韩语、法语、德语、西班牙语
- 集成声音设计（通过情绪/音色文本描述生成声音）

## 适用场景

- 多语言内容制作
- 小语种配音
- 声音设计和角色音色探索
- 已授权参考音色的跨语言复刻

## 当前参数与默认值

OmniVoice 当前接入只消费语言、参考音色或声音描述、语速。选了本地音色时走参考音频；没选本地音色时，可以用“声音描述/指令”描述想要的声音。

正文内可插入的官方非语言标签包括：`[laughter]`、`[sigh]`、`[sniff]`、`[confirmation-en]`、`[question-en]`、`[question-ah]`、`[question-oh]`、`[question-ei]`、`[question-yi]`、`[surprise-ah]`、`[surprise-oh]`、`[surprise-wa]`、`[surprise-yo]`、`[dissatisfaction-hnn]`。生成页也保留 `[pause]`、`[cough]` 作为历史兼容快捷标签；耳语/小声属于声音设计属性。

实测同格式但不在按钮里的标签也可能被模型接受，例如 `[question-ha]`、`[question-mm]`、`[surprise-huh]`、`[surprise-ai]`、`[dissatisfaction-mm]` 能正常生成音频。它们应当视为“可用探针”而不是稳定官方合同：用于试音可以，交付前要人工听一遍，确认没有被直接读出、忽略或产生不合适的语气。

标签和正文拟声词不要重复。比如写了 `[question-ah]`，后面正文不要再写“啊”；写了 `[dissatisfaction-hnn]`，后面不要再写“哼”。否则模型可能先执行标签，再把正文里的“啊 / 嗯 / 哼”等字再读一遍，听起来像重复两次。推荐写法是：`[question-ah] 这句真的要这么说吗？`；不推荐：`[question-ah] 啊，这句真的要这么说吗？`。

`emotion_text` 不是任意自然语言提示词，而是受支持词表的声音设计组合。可用示例：`女，青年，中音调`、`女，青年，耳语`、`男，中年，低音调`。不要传 `自然口播`、`压低声音`、`谨慎` 这类不在词表里的自由描述；后端会报 unsupported instruct items。

| 参数 | 默认值 | 大白话说明 |
|---|---:|---|
| 语言 `language` | `auto` | 自动判断，或手动选中文/英文/日文等。 |
| 声音描述 `emotion_text` | 空 | 未选本地音色时，用文字描述声音，例如“女，青年，中音调”。 |
| 语速 `speed` | `1.0` | 控制朗读速度。 |
| 长文本分段目标 `audio_chunk_duration` | `15s` | 官方内置长文本切分目标，每段约 15 秒。 |
| 长文本切分阈值 `audio_chunk_threshold` | `30s` | 官方默认预计音频超过 30 秒时启动内部切分。 |

内置预设：OmniVoice 女青年设计。生成页“一键重置参数”会恢复语言和语速默认值。

## 参考链接

- [OmniVoice GitHub](https://github.com/k2-fsa/OmniVoice)
- [OmniVoice 官网](https://omnivoice.app/)
