# Voice Studio 批量 TTS Agent 使用说明

本文档给其他 agent / skill 使用，用于通过本地 Voice Studio 服务批量生成旁白音频。

不同引擎的有效参数请先看 `docs/VOICE_STUDIO_ENGINE_PARAMETERS.md`。不要把通用请求字段都当成每个引擎都会生效；尤其是 MiMo 云端 voiceclone 没有独立的数值 `speed` 参数，语速要写进 `style_instruction` 或合成文本标签。

如果 agent 需要复用常用参数组合，先调 `GET /api/presets`，只使用 `engine_id` 与当前目标引擎一致的预设。自定义预设由前端和 API 共用，内置预设只读。

## 声音来源

后台 agent 调用 TTS 时不要写死系统音色库。当前支持三种声音来源：

| 来源 | 适用引擎 | 调用方式 |
| --- | --- | --- |
| 系统音色库 | `indextts-v2`、`omnivoice`、`f5-tts`、`cosyvoice-zero-shot`、`mimo-v2.5-tts-voiceclone` | 传 `voice_id`，系统会解析音色库中的参考音频；F5/CosyVoice Zero-Shot 还需要准确 `ref_text`。 |
| 调用方提供参考声音 | `indextts-v2`、`omnivoice`、`f5-tts`、`cosyvoice-zero-shot`、`mimo-v2.5-tts-voiceclone` | 直接传 `reference_audio_path`；如果同时传了 `voice_id`，本次生成优先使用 `reference_audio_path`。 |
| 模型预设声音/声音设计 | `emotivoice`、`cosyvoice-sft`、`mimo-v2.5-tts-preset`、`mimo-v2.5-tts-voicedesign` | 传模型自己的 `speaker_id`、`mimo_voice` 或 `voice_design_prompt`，不需要本地音色库。 |

可选留痕字段：

- `voice_source`: `voice_library`、`reference_audio`、`model_preset`、`voice_design`。
- `reference_audio_license_status`: 与音色库 `license_status` 相同，例如 `self_voice`、`authorized`、`company_authorized`、`test_only`。
- `reference_audio_tags`: 调用方对外部参考声音的标签，例如 `["agent:video-localization", "授权"]`。

这些字段用于任务参数留痕和授权审计，不会替代调用方对参考声音授权的确认。云端 voiceclone 会上传本次选择的参考音频，调用前必须确认声音授权和用户同意。

## 服务地址

先启动 Voice Studio 后端服务：

```bash
cd /path/to/voice-studio
PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

## 长文本前置规划

如果输入不是已经人工拆好的 `audio-segments.json`，agent 不应直接把整段长文本塞进单条生成。先调用：

```bash
curl -X POST http://127.0.0.1:8000/api/generate/plan \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "要评估的合成文本",
    "engine_id": "indextts-v2",
    "planner_mode": "auto",
    "target_format": "mp3"
  }'
```

当响应里的 `requires_user_confirmation` 为 `true` 时，agent 必须先向用户确认：

- 是否使用系统建议分段；
- 是否生成后自动校对；
- 是否允许失败段落自动重试；
- 是否在全部通过后合并；
- 若涉及 MiMo voiceclone、云端 ASR 或未来 LLM，是否允许相关数据离开本机。

当前 `/api/generate/plan` 只做规则规划，不提交任务，也不会把文本发送给 LLM。后续长文本编排会复用该接口返回的 `segments`。

## 推荐命令

如果是 `web-video-presentation` 项目，先在 `presentation` 目录运行：

```bash
npm run extract-narrations
```

这会生成 `audio-segments.json`。然后调用 Voice Studio 批处理：

```bash
python scripts/voice_studio_batch.py \
  audio-segments.json \
  --voice 819316179a4a \
  --engine indextts-v2 \
  --output-dir presentation/public/audio \
  --format mp3 \
  --wait \
  --manifest audio-manifest.json
```

说明：

- `--voice` 是 Voice Studio 音色库里的 `voice_id`。当前“狐狸 Fox - 通俗剪辑版”的 `voice_id` 是 `819316179a4a`。
- 如果 agent 已经有本次任务的参考声音，用 `--ref-audio /path/to/ref.wav --ref-text "参考音频台词"`，不要为了调用而强行写死 `--voice`。
- `--engine indextts-v2` 使用本地 IndexTTS v2。
- `--engine mimo-v2.5-tts-preset` 使用小米 MiMo 官方预置音色。
- `--engine mimo-v2.5-tts-voicedesign` 使用小米 MiMo 文本音色设计。
- `--engine mimo-v2.5-tts-voiceclone` 使用小米 MiMo 参考音频复刻；只有这次任务选中的参考音频会上传到云端。
- `--engine emotivoice` 使用 EmotiVoice 官方预置说话人，可配 `--speaker-id` 和 `--prompt`。
- `--engine f5-tts` 使用本地参考音频复刻，必须有 `--voice`/`--ref-audio` 和准确 `--ref-text`；可配 `--nfe-step`、`--cfg-strength`、`--target-rms`、`--cross-fade-duration`、`--remove-silence`。
- `--engine cosyvoice-sft` 使用 CosyVoice 官方 SFT 预置音色，可配 `--speaker-id`。
- `--engine cosyvoice-zero-shot` 使用本地参考音频复刻，必须有 `--voice`/`--ref-audio` 和准确 `--ref-text`。
- `--output-dir presentation/public/audio` 会把音频输出到视频项目的音频目录。
- `--manifest audio-manifest.json` 会写入批处理结果清单。
- 模型专属公共参数会写入批量请求的 `parameters`；单段需要不同参数时，仍然在该段 JSON 的 `parameters` 中覆盖。

## 输入 JSON

CLI 和 HTTP API 都兼容 `web-video-presentation` 默认导出的 `audio-segments.json`：

```json
[
  {
    "chapter": "intro",
    "step": 1,
    "text": "第一段旁白。",
    "audio": "intro/1.mp3"
  }
]
```

每段也可以覆盖部分参数：

```json
{
  "chapter": "intro",
  "step": 2,
  "text": "第二段旁白。",
  "emotion": "calm",
  "speed": 1.0,
  "voice_id": "819316179a4a",
  "engine_id": "indextts-v2"
}
```

MiMo 示例：

```json
{
  "chapter": "intro",
  "step": 3,
  "text": "这是一段云端音色复刻测试。",
  "engine_id": "mimo-v2.5-tts-voiceclone",
  "voice_id": "819316179a4a",
  "style_instruction": "语速稍慢，语气自然，停顿清楚。",
  "parameters": {
    "temperature": 0.6,
    "top_p": 0.95
  }
}
```

MiMo 文本提示注意：

- `text` 是最终播报正文。
- `style_instruction` 是整体语气、语速、情绪和停顿要求。
- `voice_design_prompt` 只用于 `mimo-v2.5-tts-voicedesign` 的音色描述。
- MiMo 不消费本地 `speed/top_k/segment` 参数；不要把 IndexTTS 的参数模板直接套给 MiMo。

MiMo VoiceDesign 示例：

```json
{
  "chapter": "intro",
  "step": 4,
  "text": "欢迎收听今天的内容。",
  "engine_id": "mimo-v2.5-tts-voicedesign",
  "voice_design_prompt": "中年男性，声线沉稳，吐字清晰，语速适中。",
  "parameters": {
    "optimize_text_preview": false,
    "temperature": 0.6,
    "top_p": 0.95
  }
}
```

使用调用方提供的参考声音：

```json
{
  "project_name": "外部 Agent 旁白",
  "engine_id": "f5-tts",
  "reference_audio_path": "/absolute/path/to/ref.wav",
  "ref_text": "参考音频里实际说出的台词。",
  "voice_source": "reference_audio",
  "reference_audio_license_status": "authorized",
  "reference_audio_tags": ["agent:demo", "授权参考"],
  "segments": [
    {
      "chapter": "intro",
      "step": 1,
      "text": "这段会直接使用调用方提供的参考声音。",
      "audio": "intro/1.mp3"
    }
  ]
}
```

每段也可以单独指定参考声音或音色库声音：

```json
{
  "engine_id": "cosyvoice-zero-shot",
  "segments": [
    {
      "segment_id": "role-a-001",
      "text": "角色 A 的台词。",
      "reference_audio_path": "/absolute/path/to/role-a.wav",
      "ref_text": "角色 A 参考音频台词。"
    },
    {
      "segment_id": "role-b-001",
      "text": "角色 B 的台词。",
      "voice_id": "voice-id-from-library",
      "ref_text": "角色 B 音色库参考台词。"
    }
  ]
}
```

输出的 `audio-manifest.json` 会记录每段的状态、音频路径、时长和错误信息。

## 注册新声音

如果外部 agent 希望把新参考声音加入系统音色库，调用一步式注册接口：

```bash
curl -X POST http://127.0.0.1:8000/api/voices/register \
  -F 'name=外部 Agent 授权音色' \
  -F 'reference_text=参考音频里实际说出的台词。' \
  -F 'license_status=authorized' \
  -F 'tags=["agent:demo","授权","参考声音"]' \
  -F 'recommended_engine_id=indextts-v2' \
  -F 'file=@/absolute/path/to/ref.wav;type=audio/wav'
```

响应里的 `voice_id` 可在后续 `voice_id` 调用中复用。也可以继续使用旧的两步流程：先 `POST /api/voices/upload` 得到 `file_id`，再 `POST /api/voices` 创建音色资产。

## HTTP API

提交批处理：

```bash
curl -X POST http://127.0.0.1:8000/api/batches/generate \
  -H 'Content-Type: application/json' \
  --data-binary @audio-segments.json
```

查询批处理状态：

```bash
curl http://127.0.0.1:8000/api/batches/<batch_task_id>
```

## 成功标准

agent 只有在最终批处理结果满足以下条件时，才能说“音频已经生成成功”：

- 批处理整体 `status` 是 `success`。
- 每个必须生成的段落都有 `output_path`。
- 每个必须生成的段落都有 `status: "success"`。

如果有任何段落失败，必须报告失败段落的 `segment_id`、文本片段和 `error_message`，不能假装已经成功。

## 常见错误

- `REFERENCE_AUDIO_REQUIRED`：IndexTTS v2 需要 `--voice <voice_id>` 或参考音频路径。
- `REFERENCE_AUDIO_NOT_FOUND`：传入了 `reference_audio_path`，但文件不存在；系统不会悄悄退回默认 `voice_id`。
- `MIMO_API_KEY_MISSING`：MiMo API key 没有配置。
- `MIMO_CLOUD_DISABLED`：设置页没有启用云端引擎。
- `failed`：某个段落生成失败，需要查看该段的 `error_message`。
