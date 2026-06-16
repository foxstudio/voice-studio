from __future__ import annotations

from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationQualityGate,
    VideoLocalizationQualityIssue,
    VideoLocalizationReferenceClip,
    now_iso,
)


def evaluate_quality_gate(draft: VideoLocalizationDraft) -> VideoLocalizationQualityGate:
    blockers: list[VideoLocalizationQualityIssue] = []
    warnings: list[VideoLocalizationQualityIssue] = []
    reference_by_id = {clip.reference_clip_id: clip for clip in draft.reference_clips}
    speaker_ids = {speaker.speaker_id for speaker in draft.speakers}

    if not draft.cues:
        return VideoLocalizationQualityGate(status="unknown", pending_issues=0, checked_at=now_iso())

    if not (draft.source_media.filename or draft.source_media.video_path):
        warnings.append(_issue("SOURCE_MEDIA_MISSING", "尚未记录源视频素材", "warning"))

    if draft.stems.separation_status != "completed":
        warnings.append(_issue("STEMS_NOT_READY", "人声/背景声分离尚未完成", "warning"))

    for cue in draft.cues:
        _check_cue_basics(cue, speaker_ids, blockers, warnings)
        _check_cue_reference(cue, reference_by_id, blockers, warnings)
        _check_cue_duration(cue, warnings)

    status = "blocked" if blockers else "warning" if warnings else "pass"
    return VideoLocalizationQualityGate(
        status=status,
        pending_issues=len(blockers) + len(warnings),
        blockers=blockers,
        warnings=warnings,
        checked_at=now_iso(),
    )


def _check_cue_basics(
    cue: VideoLocalizationCue,
    speaker_ids: set[str],
    blockers: list[VideoLocalizationQualityIssue],
    warnings: list[VideoLocalizationQualityIssue],
) -> None:
    if cue.start_ms is None or cue.end_ms is None:
        blockers.append(_issue("CUE_TIMECODE_MISSING", "cue 缺少入点或出点", "blocker", cue_id=cue.cue_id))
    if not cue.speaker_id:
        blockers.append(_issue("CUE_SPEAKER_MISSING", "cue 缺少说话人", "blocker", cue_id=cue.cue_id))
    elif cue.speaker_id != "mixed" and cue.speaker_id not in speaker_ids:
        blockers.append(_issue("CUE_SPEAKER_NOT_FOUND", "cue 绑定的说话人不存在", "blocker", cue_id=cue.cue_id, speaker_id=cue.speaker_id))
    if cue.speaker_id == "mixed" and cue.audio_route != "preserve_original_audio":
        blockers.append(_issue("MIXED_SPEAKER_NEEDS_SPLIT", "混合说话需拆分或标记保留原声", "blocker", cue_id=cue.cue_id))
    if not _has_text(cue.en_subtitle_text):
        blockers.append(_issue("EN_SUBTITLE_MISSING", "cue 缺少英文字幕", "blocker", cue_id=cue.cue_id))
    if not _has_text(cue.zh_localized_subtitle_text):
        blockers.append(_issue("ZH_SUBTITLE_MISSING", "cue 缺少中文字幕", "blocker", cue_id=cue.cue_id))
    elif _is_localization_placeholder(cue.zh_localized_subtitle_text):
        blockers.append(_issue("ZH_SUBTITLE_PLACEHOLDER", "中文字幕仍是待本土化占位稿", "blocker", cue_id=cue.cue_id))
    if not _has_text(cue.tts_recommended_text):
        blockers.append(_issue("TTS_TEXT_MISSING", "cue 缺少 TTS 台词", "blocker", cue_id=cue.cue_id))
    elif _is_localization_placeholder(cue.tts_recommended_text):
        blockers.append(_issue("TTS_TEXT_PLACEHOLDER", "TTS 台词仍是待本土化占位稿", "blocker", cue_id=cue.cue_id))
    elif cue.tts_recommended_text.strip() == (cue.zh_localized_subtitle_text or "").strip():
        warnings.append(_issue("TTS_TEXT_NOT_NORMALIZED", "TTS 台词与中文字幕相同，可能未做口播规范化", "warning", cue_id=cue.cue_id))
    if cue.review_status == "blocked":
        blockers.append(_issue("CUE_REVIEW_BLOCKED", "cue 被人工标记为阻断", "blocker", cue_id=cue.cue_id))
    elif cue.review_status == "needs_review":
        warnings.append(_issue("CUE_NEEDS_REVIEW", "cue 仍待人工校对", "warning", cue_id=cue.cue_id))


def _check_cue_reference(
    cue: VideoLocalizationCue,
    reference_by_id: dict[str, VideoLocalizationReferenceClip],
    blockers: list[VideoLocalizationQualityIssue],
    warnings: list[VideoLocalizationQualityIssue],
) -> None:
    if cue.audio_route != "clone_from_source":
        if cue.audio_route == "manual_review":
            warnings.append(_issue("AUDIO_ROUTE_NEEDS_REVIEW", "音频路线仍待人工确认", "warning", cue_id=cue.cue_id))
        return

    if not cue.reference_clip_id:
        blockers.append(_issue("REFERENCE_CLIP_MISSING", "克隆路线缺少参考音色", "blocker", cue_id=cue.cue_id))
        return

    reference = reference_by_id.get(cue.reference_clip_id)
    if not reference:
        blockers.append(_issue("REFERENCE_CLIP_NOT_FOUND", "cue 绑定的参考音不存在", "blocker", cue_id=cue.cue_id, reference_clip_id=cue.reference_clip_id))
        return

    if reference.source_stem != "vocals_clean":
        blockers.append(_issue("REFERENCE_NOT_FROM_CLEAN_VOCALS", "参考音必须来自分离后的干净人声", "blocker", cue_id=cue.cue_id, reference_clip_id=reference.reference_clip_id))
    if reference.cleanliness != "clean":
        blockers.append(_issue("REFERENCE_NOT_CLEAN", "参考音未标记为干净人声", "blocker", cue_id=cue.cue_id, reference_clip_id=reference.reference_clip_id))
    if reference.asr_status != "verified":
        blockers.append(_issue("REFERENCE_ASR_NOT_VERIFIED", "参考音尚未完成独立 ASR 校验", "blocker", cue_id=cue.cue_id, reference_clip_id=reference.reference_clip_id))
    if reference.speaker_id and cue.speaker_id and reference.speaker_id != cue.speaker_id:
        warnings.append(_issue("REFERENCE_SPEAKER_MISMATCH", "参考音说话人与 cue 说话人不一致", "warning", cue_id=cue.cue_id, reference_clip_id=reference.reference_clip_id))


def _check_cue_duration(cue: VideoLocalizationCue, warnings: list[VideoLocalizationQualityIssue]) -> None:
    if not cue.source_duration_ms or not cue.generated_duration_ms:
        return
    diff = abs(cue.generated_duration_ms - cue.source_duration_ms)
    if diff / max(cue.source_duration_ms, 1) > 0.2:
        warnings.append(_issue("TTS_DURATION_MISMATCH", "生成音频与原时间窗时长差超过 20%", "warning", cue_id=cue.cue_id))


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _is_localization_placeholder(value: str | None) -> bool:
    return bool(value and value.strip().startswith("【待本土化】"))


def _issue(
    code: str,
    message: str,
    severity: str,
    cue_id: str | None = None,
    speaker_id: str | None = None,
    reference_clip_id: str | None = None,
) -> VideoLocalizationQualityIssue:
    return VideoLocalizationQualityIssue(
        code=code,
        message=message,
        severity=severity,  # type: ignore[arg-type]
        cue_id=cue_id,
        speaker_id=speaker_id,
        reference_clip_id=reference_clip_id,
    )
