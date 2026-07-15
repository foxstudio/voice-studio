# MiMo V2.5 TTS / ASR

> 小米 MiMo 系列云端语音模型，包含语音合成（预置音色 / 音色设计 / 音色复刻）和语音识别。

## 基本信息

| 项目 | 详情 |
|---|---|
| 开发者 | 小米 (Xiaomi) |
| 类型 | 云端 API 服务 |
| 版本 | V2.5 |
| Token Plan | 2026 年 3 月全球发布，4 档定价 |
| 官网 | [mimo.mi.com](https://mimo.mi.com/) |

## 引擎变体

### MiMo V2.5 TTS Preset（预置音色合成）

- 使用官方预置精品音色
- 支持自然语言风格控制（如"温柔地读"、"用新闻播音腔"）
- 支持唱歌标签
- 适合中文/英文口播、配音和快速合成

### MiMo V2.5 TTS VoiceDesign（音色设计）

- 通过一段文字描述（如"30岁女性，声音温柔略带沙哑"）生成全新声音
- 支持润色播报文本选项
- 适合探索角色音色、一次性生成定制声音

### MiMo V2.5 TTS VoiceClone（音色复刻）

- 上传 wav/mp3 参考音频样本
- 云端零样本复刻任意说话人音色
- 支持自然语言风格指令
- 适合使用已授权参考音色做云端复刻

### MiMo V2.5 ASR（语音识别）

- 将 wav/mp3 音频转写为文本
- 支持自动语言检测、中文、英文
- 适合会议录音、素材转写

## 定价

MiMo Token Plan 采用 Credit 积分制，4 档定价方案。V2.5-TTS 系列模型上下文窗口 8k，输出最大 16k token。

## 隐私说明

所有 MiMo 引擎均为云端服务，音频数据需上传至小米服务器处理。

## 当前参数与默认值

MiMo V2.5 在本项目中拆成三种 TTS 入口。正文统一放合成文本框；风格、语速、情绪、角色感等用自然语言写进风格指令或音色描述。MiMo voiceclone 没有独立数值 `speed` 参数。

| 引擎 | 参数 | 默认值 | 大白话说明 |
|---|---|---:|---|
| Preset | `mimo_voice` | `mimo_default` | 官方预置音色，当前本地可选：MiMo 默认、冰糖、茉莉、苏打、白桦、Mia、Chloe、Milo、Dean。 |
| Preset / VoiceClone | `style_instruction` | 空 | 描述怎么读，例如“温柔、语速稍慢、重点句停顿”。 |
| VoiceDesign | `voice_design_prompt` | 中年男性，声线沉稳偏正式，吐字工整，语速适中。 | 描述要生成的声音本身，不是正文。 |
| VoiceDesign | `optimize_text_preview` | `false` | 是否让云端先润色播报文本。 |
| 全部 TTS | 输出格式 | `wav` | 当前只开放官方非流式示例明确覆盖的 WAV；有效 Key 完成 MP3/FLAC 实测前不承诺其他格式。 |

`temperature`、`top_p` 虽是 MiMo 通用聊天接口可能接受的字段，但官方 TTS 使用说明没有给出其对语音的专属默认值、范围或听感效果。本项目不把它们展示为 TTS 控件，也不发送，避免出现“滑块能调但不知道是否真的影响声音”的假参数。

内置预设：MiMo 稳定口播、MiMo 温柔女声、MiMo 角色试音、MiMo 复刻讲述。生成页“一键重置参数”会恢复当前 MiMo 变体的默认值。

## 参考链接

- [MiMo 官网](https://mimo.mi.com/)
- [MiMo API 定价](https://pricepertoken.com/pricing-page/provider/xiaomi)
