from __future__ import annotations

import hashlib
from pathlib import Path

from app.domains.video_localization import media_assets
from app.domains.video_localization import tts_pipeline
from app.errors import AppException
from app.domains.video_localization.schemas import BatchGenerateRequest, VideoLocalizationDraft, now_iso
from app.schemas.voice_studio import GenerateRequest, LicenseStatus, VideoLocalizationCue
from app.services import batch_queue, voice_store


def build_batch_request(
    *,
    project_id: str,
    project_name: str,
    draft: VideoLocalizationDraft,
    engine_id: str = "indextts-v2",
) -> BatchGenerateRequest:
    return tts_pipeline.build_batch_request(
        project_id=project_id,
        project_name=project_name,
        draft=draft,
        output_dir=media_assets.project_video_localization_dir(project_id) / "tts",
        engine_id=engine_id,
    )


def mark_batch_submitted(draft: VideoLocalizationDraft, batch_task_id: str, cue_ids: list[str]) -> VideoLocalizationDraft:
    return tts_pipeline.with_batch_submitted(draft, batch_task_id, cue_ids, attempted_at=now_iso())


def sync_batch_results(project_id: str, draft: VideoLocalizationDraft, batch_task_id: str) -> VideoLocalizationDraft:
    batch = batch_queue.get_batch(batch_task_id)
    if not batch:
        raise AppException(404, "VIDEO_LOCALIZATION_TTS_BATCH_NOT_FOUND", "TTS batch task not found")
    request_parameters = batch.parameters.get("parameters") if isinstance(batch.parameters, dict) else None
    if not isinstance(request_parameters, dict) or request_parameters.get("source") != "video_localization" or request_parameters.get("project_id") != project_id:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_PROJECT_MISMATCH", "Batch task does not belong to this video localization project")
    return tts_pipeline.with_synced_batch_results(draft, batch)


def sync_single_result(
    draft: VideoLocalizationDraft,
    cue_id: str,
    *,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
    task_id: str | None = None,
    generation_id: str | None = None,
) -> VideoLocalizationDraft:
    return tts_pipeline.with_single_tts_result(
        draft,
        cue_id,
        result_id=result_id,
        output_path=output_path,
        duration_ms=duration_ms,
        task_id=task_id,
        generation_id=generation_id,
    )


def build_single_handoff(project_id: str, draft: VideoLocalizationDraft, segment_id: str) -> GenerateRequest:
    subtitle = next((item for item in draft.localized_subtitles if item.subtitle_id == segment_id), None)
    cue = next((item for item in draft.cues if item.cue_id == segment_id), None)
    if subtitle is None and cue is None:
        raise AppException(404, "VIDEO_LOCALIZATION_TTS_SEGMENT_NOT_FOUND", "没有找到要生成配音的字幕片段")
    vocals_path = draft.stems.vocals_clean_path
    if not vocals_path:
        raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING", "请先准备人声轨，再生成字幕配音")

    start_ms = subtitle.start_ms if subtitle else cue.start_ms
    end_ms = subtitle.end_ms if subtitle else cue.end_ms
    if start_ms is None or end_ms is None or end_ms <= start_ms:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_RANGE_INVALID", "字幕片段的出入点无效")
    text = ((subtitle.tts_text or subtitle.text) if subtitle else (cue.tts_recommended_text or cue.zh_localized_subtitle_text or "")).strip()
    if not text:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_TEXT_MISSING", "当前字幕还没有配音台词")

    source_cue_ids = (
        list(dict.fromkeys([*subtitle.source_cue_ids, *([subtitle.linked_cue_id] if subtitle.linked_cue_id else [])]))
        if subtitle
        else [cue.cue_id]
    )
    source_cues = [item for cue_id in source_cue_ids if (item := next((candidate for candidate in draft.cues if candidate.cue_id == cue_id), None))]
    source_text = _source_text_for_range(draft, source_cues, start_ms, end_ms)
    speaker_id = next((item.speaker_id for item in source_cues if item.speaker_id), None) or (cue.speaker_id if cue else None) or "unknown"

    source_identity = _source_identity(vocals_path)
    source_file_id = _managed_id("vl_source", project_id, source_identity)
    source_file = voice_store.ensure_managed_audio_file(
        vocals_path,
        file_id=source_file_id,
        original_name=f"{project_id}-vocals.wav",
    )
    clip_file_id = _managed_id("vl_clip", project_id, segment_id, str(start_ms), str(end_ms), source_identity)
    clip = voice_store.create_audio_clip(source_file.file_id, start_ms, end_ms, clip_file_id=clip_file_id)

    return GenerateRequest(
        text=text,
        engine_id="indextts-v2",
        source="video_localization",
        project_id=project_id,
        segment_id=segment_id,
        localized_subtitle_id=subtitle.subtitle_id if subtitle else None,
        cue_id=source_cue_ids[0] if source_cue_ids else (cue.cue_id if cue else None),
        bind_to_video_localization=True,
        voice_source="reference_audio",
        reference_audio_path=clip["path"],
        reference_audio_license_status=LicenseStatus.localized,
        reference_audio_tags=["视频本土化", "本土化", "本土化字幕" if subtitle else "ASR 字幕", speaker_id],
        ref_text=source_text or None,
        custom_reference_source_audio_path=source_file.path,
        custom_reference_source_duration_ms=source_file.duration_ms,
        custom_reference_trim_start_ms=start_ms,
        custom_reference_trim_end_ms=end_ms,
        language="zh",
        emotion_mode="follow_reference",
        output_format="wav",
    )


def _managed_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _source_identity(source_path: str) -> str:
    path = Path(source_path)
    try:
        stat = path.stat()
    except OSError:
        return str(path)
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"


def _source_text_for_range(
    draft: VideoLocalizationDraft,
    source_cues: list[VideoLocalizationCue],
    start_ms: int,
    end_ms: int,
) -> str:
    if source_cues:
        cue_start = min((item.start_ms if item.start_ms is not None else start_ms) for item in source_cues)
        cue_end = max((item.end_ms if item.end_ms is not None else end_ms) for item in source_cues)
        if abs(cue_start - start_ms) <= 250 and abs(cue_end - end_ms) <= 250:
            return " ".join((item.source_text_raw or item.en_subtitle_text or "").strip() for item in source_cues).strip()
    source_word_ids = {word_id for item in source_cues for word_id in item.source_word_ids}
    words = [] if draft.transcription is None else sorted(
        (
            word
            for word in draft.transcription.words
            if word.word_id in source_word_ids or (word.end_ms > start_ms and word.start_ms < end_ms)
        ),
        key=lambda word: (word.start_ms, word.end_ms),
    )
    if words:
        return " ".join(word.text for word in words).strip()
    return " ".join((item.source_text_raw or item.en_subtitle_text or "").strip() for item in source_cues).strip()
