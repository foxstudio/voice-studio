# 视频本土化配音工作台规划

## 目标

视频本土化配音工作台用于把外文视频生产成可审校的中文配音数据包：模型先生成英文字幕、说话人和参考音色候选，人工再校对每个 cue 的说话人、入点出点、中文字幕、TTS 台词和参考音色，最终导出 JSON，并把单条或批量任务发送到现有语音合成页生成中文同音色人声。

V1 不做完整剪辑软件，也不在本页面设计 TTS 高级参数。TTS 参数、预设和重生成能力继续复用 `/generate`。

## 产品基线

- 首屏是生产工作台，不是 landing page。
- cue 审校表是核心，视频预览、波形和说话人轨是辅助。
- 每个 cue 必须同时维护三轨文本：
  - `en_subtitle_text`：英文字幕，用于意义核对和参考音 ASR 匹配。
  - `zh_localized_subtitle_text`：观众看到的中文字幕，可使用 `1992 年` 等显示友好的写法。
  - `tts_recommended_text`：送入 TTS 的中文口播文本，需要把数字、年份、缩写和停顿改成更适合合成的读法，例如 `一九九二年`。
- 参考音色必须来自分离后的干净人声，不能默认使用原始混音。
- 一个说话人可以有多个参考音色候选；每个 cue 可以选择任意一个该说话人的干净候选。
- 参考音色本身必须独立 ASR，不能从文件名、speaker id 或附近字幕推断参考台词。
- 批量生成前先做质量门检查，不能把未校对、无参考音、混合说话或不干净参考音的 cue 静默提交。

## 页面结构

1. 顶部状态条
   - 导入视频、人声分离、英文 ASR、说话人分离、人工校对、TTS 生成、JSON 导出。
   - 展示当前项目名、素材时长、阻断项数量。

2. 左侧前处理与预览
   - 视频导入入口。
   - 视频预览和字幕 overlay。
   - `faster-whisper-turbo`、`qwen3-asr-mlx`、`mimo-v2.5-asr` 的 ASR 路线状态。
   - 原始音频、分离人声、背景声 stem 状态。

3. 中间 cue 审校区
   - 按源视频时间排序。
   - 每行显示时间码、speaker、英文字幕、中文字幕、TTS 台词、参考音色、状态。
   - 支持锁定 cue、标记待修、跳到视频时间点。

4. 右侧片段编辑器
   - speaker 选择。
   - 入点/出点。
   - 三轨文本编辑。
   - 参考音色候选池，显示干净度、ASR、授权和时长。
   - 单条发送到语音合成页。

5. 底部批量与导出
   - 批量发送到语音合成。
   - 导出项目 JSON。
   - 质量门明细。

## ASR 与模型路线

默认新增本地 ASR adapter：

- engine id：`faster-whisper-turbo`
- 推理库：`faster-whisper`
- 默认模型：`turbo`
- 原始模型：`openai/whisper-large-v3-turbo`
- 默认用途：英文字幕初稿、cue 时间初稿、参考音 ASR。

备用路线：

- `qwen3-asr-mlx`：本地兜底。
- `mimo-v2.5-asr`：云端兜底，需要保留云端上传提示。

## JSON 草案

```json
{
  "project_id": "video_loc_001",
  "status": "draft",
  "source_media": {
    "filename": "source.mp4",
    "duration_ms": 182000,
    "video_path": "media/source.mp4",
    "audio_path": "stems/source.wav"
  },
  "stems": {
    "vocals_clean_path": "stems/vocals.wav",
    "background_path": "stems/background.wav"
  },
  "speakers": [
    {
      "speaker_id": "speaker_01",
      "display_name": "A",
      "route": "clone_from_source",
      "reference_clip_ids": ["ref_001"],
      "review_status": "needs_review"
    }
  ],
  "reference_clips": [
    {
      "reference_clip_id": "ref_001",
      "speaker_id": "speaker_01",
      "source_stem": "vocals_clean",
      "start_ms": 12000,
      "end_ms": 42000,
      "audio_path": "references/speaker_01_ref_001.wav",
      "cleanliness": "clean",
      "asr_text": "This is the original reference line.",
      "asr_status": "verified",
      "license_status": "localized"
    }
  ],
  "cues": [
    {
      "cue_id": "cue_0001",
      "speaker_id": "speaker_01",
      "start_ms": 1200,
      "end_ms": 3400,
      "audio_route": "clone_from_source",
      "en_subtitle_text": "In 1992, this changed everything.",
      "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
      "tts_recommended_text": "一九九二年，这件事，改变了一切。",
      "reference_clip_id": "ref_001",
      "tts_result_id": null,
      "tts_audio_path": null,
      "review_status": "needs_review",
      "quality_flags": ["tts_text_needs_review"]
    }
  ]
}
```

## V1 后端接口

V1 不新增数据库表。视频本土化草稿保存在现有 `Project.parameters.video_localization` 命名空间中：

- `GET /api/projects/{project_id}/video-localization`：读取项目草稿；没有草稿时返回默认空草稿。
- `PUT /api/projects/{project_id}/video-localization`：保存草稿 JSON，只落库，不做转写、分离、裁切或 TTS。
- `GET /api/projects/{project_id}/video-localization/export`：导出带 `project_id` 和 `project_name` 的 production JSON。

后续单条生成继续组装 `GenerateRequest` 调用 `/api/generate`；批量生成后续再加转换层，把可提交 cue 转成现有 `/api/batches/generate` 兼容 payload。

## 质量门

批量发送前必须通过：

- ASR 覆盖：每个待生成 cue 有英文文本和时间码。
- 说话人：每个待生成 cue 有明确 `speaker_id`，混合说话必须先拆分或标记保留原声。
- 文本三轨：英文、中文字幕、TTS 台词都存在；TTS 台词不能直接复用未规范化字幕。
- 参考音：clone 路线必须绑定干净人声参考音，且参考音 ASR 已完成。
- 授权：参考音色必须可用于本土化或已明确人工确认。
- 批量一致性：导出 JSON cue 数、待生成任务数和可提交任务数必须可解释。

生成后必须记录：

- `tts_result_id`、`tts_audio_path`、生成时长。
- 原始时间窗与生成音频时长差。
- 内容 ASR 覆盖或人工校对状态。
- 是否允许进入最终混音或仅作为候选。

## 迭代计划

1. 新增静态 `/video-localization` 工作台页面和导航入口。
2. 定义前端类型、示例数据和 JSON 导出形状。
3. 增加后端 draft/project 保存接口。
4. 接入 `faster-whisper-turbo` ASR adapter。
5. 接入视频导入、抽音频和 stem 文件记录。
6. 接入人声分离和 speaker diarization 候选。
7. 打通单条和批量发送到语音合成页。
8. 回写 TTS 结果并支持原声/TTS 试听切换。
