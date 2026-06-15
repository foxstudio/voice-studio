# 视频本土化领域契约

## 目标

视频本土化工作台的核心产物是一个可审校、可生成、可追溯的 `video_localization` 草稿。它不是最终视频文件，也不是 TTS 参数页的替代品；它负责记录源视频、分离音轨、说话人、参考音、三轨文本、TTS 交接和质量门。

V1 草稿继续保存在 `Project.parameters.video_localization`，不新增数据库表。

## 顶层字段

```json
{
  "project_type": "video_localization",
  "schema_version": "v1",
  "status": "draft",
  "source_media": {},
  "stems": {},
  "speakers": [],
  "reference_clips": [],
  "cues": [],
  "quality_gate": {},
  "exports": {},
  "updated_at": null
}
```

`status` 可取：

- `draft`：草稿阶段。
- `reviewing`：人工校对中。
- `ready_for_tts`：质量门允许进入 TTS。
- `tts_running`：已提交 TTS 队列。
- `candidate`：已有候选中文音频或候选导出。
- `blocked`：存在阻断项。

## Source Media

`source_media` 记录导入视频和抽取音频的元数据：

- `filename`
- `duration_ms`
- `video_path`
- `audio_path`
- `size_bytes`
- `width`
- `height`
- `frame_rate`
- `imported_at`
- `metadata`

V1 只要求能保存和导出。真实文件复制、抽音频和探测时长在后续导入阶段实现。

## Stems

`stems` 记录分离音轨：

- `vocals_clean_path`：干净人声路径。
- `background_path`：背景音乐/环境声路径。
- `original_audio_path`：源音频路径。
- `separation_engine_id`
- `separation_status`
- `quality_flags`

参考音默认必须来自 `vocals_clean_path` 对应的干净人声，不应直接使用原始混音。

## Speaker

每个说话人至少包含：

- `speaker_id`
- `display_name`
- `route`
- `reference_clip_ids`
- `time_ranges`
- `review_status`
- `notes`

`route` 可取：

- `clone_from_source`
- `preset_tts`
- `preserve_original_audio`
- `manual_review`

不要把不确定的真实人物身份写死。人物身份、截图证据和可见名牌应作为证据字段扩展保存，默认仍以稳定的 `speaker_id` 作为业务主键。

## Reference Clip

每个参考音至少包含：

- `reference_clip_id`
- `speaker_id`
- `source_stem`
- `start_ms`
- `end_ms`
- `duration_ms`
- `audio_path`
- `cleanliness`
- `asr_text`
- `asr_status`
- `license_status`
- `quality_flags`

规则：

- `source_stem` 默认是 `vocals_clean`。
- `cleanliness=clean` 才能作为克隆参考音。
- 每个被选中的参考音必须独立 ASR，不能从文件名、speaker id 或附近字幕推断参考文本。
- 混合说话、背景泄漏明显、多人重叠的片段不能静默进入生产。

## Cue

每句台词至少包含：

- `cue_id`
- `speaker_id`
- `start_ms`
- `end_ms`
- `audio_route`
- `en_subtitle_text`
- `zh_localized_subtitle_text`
- `tts_recommended_text`
- `reference_clip_id`
- `tts_result_id`
- `tts_audio_path`
- `source_duration_ms`
- `generated_duration_ms`
- `review_status`
- `quality_flags`
- `notes`

三轨文本必须分开：

- `en_subtitle_text`：英文/源语字幕，用于意义核对和参考音匹配。
- `zh_localized_subtitle_text`：观众看到的中文字幕。
- `tts_recommended_text`：送入 TTS 的中文口播文本。

例如：

- 中文字幕：`1992 年，这件事改变了一切。`
- TTS 台词：`一九九二年，这件事，改变了一切。`

最终中文字幕应从锁定后的中文口播文本同源重建或人工确认，不应长期保留一份和最终声音不一致的字幕。

## Quality Gate

`quality_gate` 记录是否可提交 TTS 或导出生产 JSON：

- `status`
- `pending_issues`
- `blockers`
- `warnings`
- `checked_at`

保存草稿和导出 production JSON 时，后端必须自动重算质量门，并覆盖客户端传入的旧 `quality_gate`。导出可以包含阻断明细；后续批量 TTS 提交必须在存在 blocker 时拒绝提交。

批量提交前的硬阻断：

- cue 缺少时间码。
- cue 缺少 `speaker_id`。
- cue 绑定的 `speaker_id` 不存在。
- cue 缺少英文字幕、中文字幕或 TTS 台词。
- `clone_from_source` 路线缺少干净参考音。
- 参考音未独立 ASR。
- 混合说话未拆分，也没有显式标记 `preserve_original_audio`。
- 云端 fallback 或云端 voiceclone 未经人工确认。

V1 issue code 使用稳定英文枚举，前端显示中文 `message`：

- `CUE_TIMECODE_MISSING`
- `CUE_SPEAKER_MISSING`
- `CUE_SPEAKER_NOT_FOUND`
- `MIXED_SPEAKER_NEEDS_SPLIT`
- `EN_SUBTITLE_MISSING`
- `ZH_SUBTITLE_MISSING`
- `TTS_TEXT_MISSING`
- `TTS_TEXT_NOT_NORMALIZED`
- `REFERENCE_CLIP_MISSING`
- `REFERENCE_CLIP_NOT_FOUND`
- `REFERENCE_NOT_FROM_CLEAN_VOCALS`
- `REFERENCE_NOT_CLEAN`
- `REFERENCE_ASR_NOT_VERIFIED`

## Export

`GET /api/projects/{project_id}/video-localization/export` 返回生产 JSON，并额外包含：

- `project_id`
- `project_name`
- `exported_at`
- `export_summary`

导出 JSON 必须保留：

- `schema_version`
- 源素材信息。
- 模型/引擎参数证据。
- 说话人、参考音和 cue。
- 三轨文本。
- TTS 结果回写字段。
- 质量门结果。

## V1 不做

- 不新增数据库表。
- 不替代 `/generate` 的高级参数和预设设计。
- 不自动提交 TTS。
- 不把 candidate 音频或字幕命名为 final。
- 不在缺少干净参考音时强行克隆。
- 不重启服务、不清理真实任务队列、不移动用户已有音频资产。
