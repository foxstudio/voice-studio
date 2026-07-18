from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from app.errors import AppException
from app.services import audio_tools
from app.domains.video_localization.schemas import (
    BatchGenerateRequest,
    BatchSegmentInput,
    BatchSegmentResult,
    BatchTask,
    LicenseStatus,
    TaskStatus,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
    now_iso,
)


def build_batch_request(
    *,
    project_id: str,
    project_name: str,
    draft: VideoLocalizationDraft,
    output_dir: Path,
    engine_id: str = "indextts-v2",
) -> BatchGenerateRequest:
    expected_source = str(draft.localization_state.get("source_fingerprint") or "")
    if expected_source:
        from app.domains.video_localization import localization

        if localization.source_fingerprint(draft) != expected_source:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SOURCE_CHANGED",
                "原文字幕在本土化之后发生了变化，请重新生成本土化字幕初稿后再生成配音。",
            )
    reference_by_id = {clip.reference_clip_id: clip for clip in draft.reference_clips}
    if draft.localized_subtitles:
        segments = _localized_subtitle_segments(draft, reference_by_id)
    else:
        segments = _legacy_cue_segments(draft, reference_by_id)

    if not segments:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_CUES_EMPTY", "No ready clone-from-source cues can be submitted")

    return BatchGenerateRequest(
        project_name=f"{project_name} 中文本土化配音",
        engine_id=engine_id,
        language="zh",
        output_dir=str(output_dir),
        output_format="mp3",
        partial_success=True,
        segments=segments,
        parameters={
            "source": "video_localization",
            "project_id": project_id,
            "schema_version": draft.schema_version,
        },
    )


def _legacy_cue_segments(draft: VideoLocalizationDraft, reference_by_id: dict) -> list[BatchSegmentInput]:
    segments: list[BatchSegmentInput] = []

    for cue in draft.cues:
        if cue.review_status not in {"ready", "locked"}:
            continue
        if cue.audio_route != "clone_from_source":
            continue
        tts_text = (cue.tts_recommended_text or "").strip()
        if not tts_text or _is_placeholder_text(tts_text):
            raise AppException(400, "VIDEO_LOCALIZATION_TTS_TEXT_NOT_READY", f"Cue {cue.cue_id} does not have production-ready TTS text")
        reference = reference_by_id.get(cue.reference_clip_id or "")
        if not reference:
            raise AppException(400, "VIDEO_LOCALIZATION_TTS_REFERENCE_MISSING", f"Cue {cue.cue_id} does not have a reference clip")
        if reference.source_stem != "vocals_clean" or reference.cleanliness != "clean" or reference.asr_status != "verified":
            raise AppException(400, "VIDEO_LOCALIZATION_TTS_REFERENCE_NOT_READY", f"Reference {reference.reference_clip_id} is not verified clean vocals")
        if not reference.audio_path or not Path(reference.audio_path).exists():
            raise AppException(400, "VIDEO_LOCALIZATION_TTS_REFERENCE_FILE_MISSING", f"Reference audio is missing for {reference.reference_clip_id}")

        segments.append(
            BatchSegmentInput(
                segment_id=cue.cue_id,
                chapter=cue.speaker_id or "speaker",
                step=len(segments) + 1,
                text=tts_text,
                audio=f"{_safe_identifier(cue.speaker_id or 'speaker')}/{cue.cue_id}.mp3",
                reference_audio_path=reference.audio_path,
                reference_audio_license_status=LicenseStatus.localized,
                reference_audio_tags=["视频本土化", "本土化", cue.speaker_id or "unknown"],
                ref_text=reference.asr_text or cue.en_subtitle_text,
                language="zh",
                parameters={
                    "cue_id": cue.cue_id,
                    "speaker_id": cue.speaker_id,
                    "source_start_ms": cue.start_ms,
                    "source_end_ms": cue.end_ms,
                    "source_duration_ms": cue.source_duration_ms,
                    "en_subtitle_text": cue.en_subtitle_text,
                    "zh_localized_subtitle_text": cue.zh_localized_subtitle_text,
                    "reference_clip_id": reference.reference_clip_id,
                    "audio_route": cue.audio_route,
                },
            )
        )
    return segments


def _localized_subtitle_segments(draft: VideoLocalizationDraft, reference_by_id: dict) -> list[BatchSegmentInput]:
    cue_by_id = {cue.cue_id: cue for cue in draft.cues}
    segments: list[BatchSegmentInput] = []
    ordered_subtitles = sorted(
        draft.localized_subtitles,
        key=lambda subtitle: (subtitle.start_ms, subtitle.end_ms, subtitle.subtitle_id),
    )
    for subtitle in ordered_subtitles:
        tts_text = (subtitle.tts_text or "").strip()
        if not tts_text or _is_placeholder_text(tts_text):
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_TEXT_NOT_READY",
                f"Localized subtitle {subtitle.subtitle_id} does not have production-ready TTS text",
                {"subtitle_id": subtitle.subtitle_id},
            )
        source_cue_ids = list(dict.fromkeys(subtitle.source_cue_ids))
        if not source_cue_ids:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_SOURCE_CUES_MISSING",
                "本土化字幕缺少原文 cue 映射，无法确定人物和音色。",
                {"subtitle_id": subtitle.subtitle_id},
            )
        missing_cue_ids = [cue_id for cue_id in source_cue_ids if cue_id not in cue_by_id]
        if missing_cue_ids:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_SOURCE_CUE_NOT_FOUND",
                "本土化字幕引用的原文 cue 不存在。",
                {"subtitle_id": subtitle.subtitle_id, "source_cue_ids": missing_cue_ids},
            )
        source_cues = [cue_by_id[cue_id] for cue_id in source_cue_ids]
        speaker_ids = {cue.speaker_id for cue in source_cues}
        if len(speaker_ids) != 1:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_CROSS_SPEAKER_SUBTITLE",
                "一条本土化字幕不能跨越不同说话人，请先拆分字幕。",
                {
                    "subtitle_id": subtitle.subtitle_id,
                    "source_cue_ids": source_cue_ids,
                    "speaker_ids": sorted(speaker_id or "" for speaker_id in speaker_ids),
                },
            )
        speaker_id = source_cues[0].speaker_id
        if any(cue.review_status not in {"ready", "locked"} for cue in source_cues):
            continue
        audio_routes = {cue.audio_route for cue in source_cues}
        if len(audio_routes) != 1:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_AUDIO_ROUTE_AMBIGUOUS",
                "本土化字幕映射的原文 cue 使用了不同音频路线，请先统一或拆分字幕。",
                {"subtitle_id": subtitle.subtitle_id, "audio_routes": sorted(audio_routes)},
            )
        audio_route = source_cues[0].audio_route
        if audio_route != "clone_from_source":
            continue
        reference_ids = {cue.reference_clip_id for cue in source_cues if cue.reference_clip_id}
        if len(reference_ids) > 1:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_REFERENCE_AMBIGUOUS",
                "本土化字幕映射到多个参考音色，请先统一或拆分字幕。",
                {"subtitle_id": subtitle.subtitle_id, "reference_clip_ids": sorted(reference_ids)},
            )
        reference_id = next(iter(reference_ids), "")
        reference = reference_by_id.get(reference_id)
        if not reference:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_TTS_REFERENCE_MISSING",
                f"Localized subtitle {subtitle.subtitle_id} does not have a reference clip",
            )
        if reference.source_stem != "vocals_clean" or reference.cleanliness != "clean" or reference.asr_status != "verified":
            raise AppException(400, "VIDEO_LOCALIZATION_TTS_REFERENCE_NOT_READY", f"Reference {reference.reference_clip_id} is not verified clean vocals")
        if not reference.audio_path or not Path(reference.audio_path).exists():
            raise AppException(400, "VIDEO_LOCALIZATION_TTS_REFERENCE_FILE_MISSING", f"Reference audio is missing for {reference.reference_clip_id}")

        source_text = " ".join(
            text for cue in source_cues if (text := (cue.en_subtitle_text or "").strip())
        )
        segments.append(
            BatchSegmentInput(
                segment_id=subtitle.subtitle_id,
                chapter=speaker_id or "speaker",
                step=len(segments) + 1,
                text=tts_text,
                audio=f"{_safe_identifier(speaker_id or 'speaker')}/{subtitle.subtitle_id}.mp3",
                reference_audio_path=reference.audio_path,
                reference_audio_license_status=LicenseStatus.localized,
                reference_audio_tags=["视频本土化", "本土化", speaker_id or "unknown"],
                ref_text=reference.asr_text or source_text or None,
                language="zh",
                parameters={
                    "subtitle_id": subtitle.subtitle_id,
                    "cue_id": source_cue_ids[0],
                    "source_cue_ids": source_cue_ids,
                    "speaker_id": speaker_id,
                    "source_start_ms": subtitle.start_ms,
                    "source_end_ms": subtitle.end_ms,
                    "source_duration_ms": subtitle.end_ms - subtitle.start_ms,
                    "zh_localized_subtitle_text": subtitle.text,
                    "reference_clip_id": reference.reference_clip_id,
                    "audio_route": audio_route,
                },
            )
        )
    return segments


def with_batch_submitted(draft: VideoLocalizationDraft, batch_task_id: str, cue_ids: list[str], *, attempted_at: str) -> VideoLocalizationDraft:
    segment_id_set = set(cue_ids)
    if not segment_id_set:
        return draft
    updated = False
    next_subtitles: list[VideoLocalizationSubtitleCue] = []
    for subtitle in draft.localized_subtitles:
        if subtitle.subtitle_id in segment_id_set:
            next_subtitles.append(
                _subtitle_with_tts_batch_status(
                    subtitle,
                    batch_task_id,
                    TaskStatus.queued,
                    None,
                    attempted_at=attempted_at,
                )
            )
            updated = True
        else:
            next_subtitles.append(subtitle)

    next_cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        if cue.cue_id in segment_id_set:
            next_cues.append(_cue_with_tts_batch_status(cue, batch_task_id, TaskStatus.queued, None, attempted_at=attempted_at))
            updated = True
        else:
            next_cues.append(cue)
    if not updated:
        return draft
    return draft.model_copy(update={"cues": next_cues, "localized_subtitles": next_subtitles})


def with_synced_batch_results(draft: VideoLocalizationDraft, batch: BatchTask) -> VideoLocalizationDraft:
    segments_by_id = {segment.segment_id: segment for segment in batch.segments}
    if not segments_by_id:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_RESULTS_EMPTY", "Batch task has no segment results")

    updated = False
    successful_subtitles: list[tuple[VideoLocalizationSubtitleCue, BatchSegmentResult]] = []
    next_subtitles: list[VideoLocalizationSubtitleCue] = []
    for subtitle in draft.localized_subtitles:
        segment = segments_by_id.get(subtitle.subtitle_id)
        if not segment:
            next_subtitles.append(subtitle)
            continue
        if _batch_segment_has_audio(segment):
            next_subtitles.append(_subtitle_with_tts_result(subtitle, batch.batch_task_id, segment))
            successful_subtitles.append((subtitle, segment))
        else:
            status, error = _batch_segment_failure(segment)
            next_subtitles.append(_subtitle_with_tts_batch_status(subtitle, batch.batch_task_id, status, error))
        updated = True

    next_cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        segment = segments_by_id.get(cue.cue_id)
        if not segment:
            next_cues.append(cue)
            continue
        if _batch_segment_has_audio(segment):
            next_cues.append(_cue_with_tts_result(cue, batch.batch_task_id, segment))
        else:
            status, error = _batch_segment_failure(segment)
            next_cues.append(_cue_with_tts_batch_status(cue, batch.batch_task_id, status, error))
        updated = True

    if not updated:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_CUES_UNMATCHED", "Batch task results do not match any cue ids")
    next_clips = draft.timeline_clips
    for subtitle, segment in successful_subtitles:
        next_clips = _with_localized_timeline_clip(
            next_clips,
            subtitle,
            output_path=str(segment.output_path),
            duration_ms=segment.duration_ms,
            generation_id=batch.batch_task_id,
        )
    return draft.model_copy(
        update={
            "cues": next_cues,
            "localized_subtitles": next_subtitles,
            "timeline_clips": next_clips,
        }
    )


def with_single_tts_result(
    draft: VideoLocalizationDraft,
    cue_id: str,
    *,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
    task_id: str | None = None,
    generation_id: str | None = None,
) -> VideoLocalizationDraft:
    path = Path(output_path)
    if not path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_AUDIO_NOT_FOUND", "TTS audio file not found")

    subtitle = next((item for item in draft.localized_subtitles if item.subtitle_id == cue_id), None)
    if subtitle:
        next_subtitles = [
            _subtitle_with_tts_audio(
                item,
                result_id=result_id,
                generation_id=generation_id or task_id,
                output_path=str(path),
                duration_ms=duration_ms,
            )
            if item.subtitle_id == cue_id
            else item
            for item in draft.localized_subtitles
        ]
        primary_cue_id = next(iter(subtitle.source_cue_ids), subtitle.linked_cue_id or "")
        update: dict = {
            "localized_subtitles": next_subtitles,
            "timeline_clips": _with_localized_timeline_clip(
                draft.timeline_clips,
                subtitle,
                output_path=str(path),
                duration_ms=duration_ms,
                generation_id=generation_id or task_id,
            ),
        }
        if task_id:
            update["generated_candidates"] = _with_synced_generated_candidates(
                draft.generated_candidates,
                cue_id=primary_cue_id,
                task_id=task_id,
                result_id=result_id,
                output_path=str(path),
                duration_ms=duration_ms,
                subtitle_id=subtitle.subtitle_id,
            )
        return draft.model_copy(update=update)

    updated = False
    next_cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        if cue.cue_id != cue_id:
            next_cues.append(cue)
            continue
        next_cues.append(
            _cue_with_tts_audio(
                cue,
                result_id=result_id,
                generation_id=generation_id or task_id,
                output_path=str(path),
                duration_ms=duration_ms,
            )
        )
        updated = True

    if not updated:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_CUE_NOT_FOUND", "Cue not found in video localization draft")
    update: dict = {"cues": next_cues}
    if task_id:
        update["generated_candidates"] = _with_synced_generated_candidates(
            draft.generated_candidates,
            cue_id=cue_id,
            task_id=task_id,
            result_id=result_id,
            output_path=str(path),
            duration_ms=duration_ms,
        )
        update["timeline_clips"] = _with_synced_timeline_clips(
            draft.timeline_clips,
            cue_id=cue_id,
            task_id=task_id,
            output_path=str(path),
            duration_ms=duration_ms,
            generation_id=generation_id or task_id,
            target_start_ms=next(
                (item.start_ms for item in draft.cues if item.cue_id == cue_id),
                None,
            ),
        )
    return draft.model_copy(update=update)


def tts_audio_path(draft: VideoLocalizationDraft, cue_id: str) -> Path | None:
    subtitle = next((item for item in draft.localized_subtitles if item.subtitle_id == cue_id), None)
    if subtitle and subtitle.tts_audio_path:
        path = Path(subtitle.tts_audio_path)
        return path if path.exists() else None
    cue = next((item for item in draft.cues if item.cue_id == cue_id), None)
    if not cue or not cue.tts_audio_path:
        return None
    path = Path(cue.tts_audio_path)
    return path if path.exists() else None


def generated_candidate_audio_path(draft: VideoLocalizationDraft, candidate_id: str) -> Path | None:
    candidate = next((dict(item) for item in draft.generated_candidates if dict(item).get("candidate_id") == candidate_id), None)
    if not candidate or not candidate.get("audio_path"):
        return None
    path = Path(str(candidate["audio_path"]))
    return path if path.exists() else None


def with_applied_generated_candidate(draft: VideoLocalizationDraft, candidate_id: str) -> VideoLocalizationDraft:
    candidate = next((dict(item) for item in draft.generated_candidates if dict(item).get("candidate_id") == candidate_id), None)
    if not candidate:
        raise AppException(404, "VIDEO_LOCALIZATION_CANDIDATE_NOT_FOUND", "Generated candidate not found")
    audio_path = generated_candidate_audio_path(draft, candidate_id)
    if not audio_path:
        raise AppException(400, "VIDEO_LOCALIZATION_CANDIDATE_AUDIO_MISSING", "Generated candidate audio is not available")
    cue_id = str(candidate.get("cue_id") or "")
    cue = next((item for item in draft.cues if item.cue_id == cue_id), None)
    if not cue:
        raise AppException(400, "VIDEO_LOCALIZATION_CANDIDATE_CUE_MISSING", "Generated candidate is not linked to a cue")
    duration_ms = _int_or_none(candidate.get("duration_ms"))
    result_id = str(candidate.get("result_id") or candidate_id)
    generation_id = str(candidate.get("generation_id") or candidate.get("task_id") or "") or None
    next_cues = [
        _cue_with_tts_audio(
            item,
            result_id=result_id,
            generation_id=generation_id,
            output_path=str(audio_path),
            duration_ms=duration_ms,
        )
        if item.cue_id == cue_id
        else item
        for item in draft.cues
    ]
    next_candidates = []
    for item in draft.generated_candidates:
        next_item = dict(item)
        if next_item.get("cue_id") == cue_id:
            next_item["selected"] = next_item.get("candidate_id") == candidate_id
        next_candidates.append(next_item)
    alignment = _speech_onset_alignment(str(audio_path), duration_ms, cue.start_ms)
    next_clip = {
        "clip_id": f"clip_{cue_id}",
        "cue_id": cue_id,
        "candidate_id": candidate_id,
        "track_id": "dub",
        "generation_id": generation_id,
        "audio_path": str(audio_path),
        "status": "ready",
        **alignment,
    }
    replaced = False
    next_clips = []
    for item in draft.timeline_clips:
        clip = dict(item)
        if clip.get("track_id", "dub") == "dub" and clip.get("cue_id") == cue_id:
            next_clips.append({**clip, **next_clip, "clip_id": clip.get("clip_id") or next_clip["clip_id"]})
            replaced = True
        else:
            next_clips.append(clip)
    if not replaced:
        next_clips.append(next_clip)
    return draft.model_copy(update={"cues": next_cues, "generated_candidates": next_candidates, "timeline_clips": next_clips})


def with_applied_history_result(
    draft: VideoLocalizationDraft,
    clip_id: str,
    *,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
    generation_id: str | None = None,
    placement_start_ms: int | None = None,
    dub_lane: int | None = None,
) -> VideoLocalizationDraft:
    """Apply an explicitly chosen historical render to one dub clip only."""
    clip = next((dict(item) for item in draft.timeline_clips if item.get("clip_id") == clip_id), None)
    if not clip:
        raise AppException(404, "VIDEO_LOCALIZATION_TIMELINE_CLIP_NOT_FOUND", "Timeline clip not found")
    if clip.get("track_id", "dub") != "dub":
        raise AppException(400, "VIDEO_LOCALIZATION_TIMELINE_CLIP_NOT_DUB", "Only dub clips can use TTS history")
    if not Path(output_path).exists():
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_AUDIO_NOT_FOUND", "TTS audio file not found")

    subtitle_id = str(clip.get("subtitle_id") or "")
    cue_id = str(clip.get("cue_id") or "")
    subtitle = next((item for item in draft.localized_subtitles if item.subtitle_id == subtitle_id), None)
    cue = next((item for item in draft.cues if item.cue_id == cue_id), None)
    if not subtitle and not cue:
        raise AppException(400, "VIDEO_LOCALIZATION_TIMELINE_CLIP_UNBOUND", "Dub clip is not linked to a subtitle")

    target_start_ms = subtitle.start_ms if subtitle else cue.start_ms
    alignment = _speech_onset_alignment(output_path, duration_ms, target_start_ms)
    if placement_start_ms is not None:
        placed_start_ms = max(0, int(placement_start_ms))
        placed_duration_ms = max(1, alignment["source_end_ms"])
        alignment.update(
            {
                "start_ms": placed_start_ms,
                "end_ms": placed_start_ms + placed_duration_ms,
                "source_start_ms": 0,
                "source_end_ms": placed_duration_ms,
                "alignment_lead_ms": 0,
            }
        )
    candidate = next(
        (
            dict(item)
            for item in draft.generated_candidates
            if str(item.get("result_id") or "") == result_id
            and (not subtitle_id or str(item.get("subtitle_id") or "") == subtitle_id)
        ),
        None,
    )
    candidate_id = str(candidate.get("candidate_id") or "") if candidate else None
    source_cue_ids = list(dict.fromkeys(subtitle.source_cue_ids)) if subtitle else []
    primary_cue_id = next(iter(source_cue_ids), subtitle.linked_cue_id if subtitle else cue_id)

    next_subtitles = [
        _subtitle_with_tts_audio(
            item,
            result_id=result_id,
            generation_id=generation_id,
            output_path=output_path,
            duration_ms=duration_ms,
        )
        if subtitle and item.subtitle_id == subtitle.subtitle_id
        else item
        for item in draft.localized_subtitles
    ]
    next_cues = [
        _cue_with_tts_audio(
            item,
            result_id=result_id,
            generation_id=generation_id,
            output_path=output_path,
            duration_ms=duration_ms,
        )
        if not subtitle and item.cue_id == cue_id
        else item
        for item in draft.cues
    ]
    next_candidates = []
    for item in draft.generated_candidates:
        next_item = dict(item)
        if subtitle_id and next_item.get("subtitle_id") == subtitle_id:
            next_item["selected"] = str(next_item.get("result_id") or "") == result_id
        elif not subtitle_id and next_item.get("cue_id") == cue_id:
            next_item["selected"] = str(next_item.get("result_id") or "") == result_id
        next_candidates.append(next_item)

    next_clips = []
    for item in draft.timeline_clips:
        next_item = dict(item)
        if next_item.get("clip_id") == clip_id:
            next_item.update(
                {
                    "track_id": "dub",
                    "cue_id": next_item.get("cue_id") or primary_cue_id,
                    "subtitle_id": subtitle_id or next_item.get("subtitle_id"),
                    "source_cue_ids": source_cue_ids or next_item.get("source_cue_ids") or [],
                    "candidate_id": candidate_id,
                    "result_id": result_id,
                    "generation_id": generation_id,
                    "audio_path": output_path,
                    "status": "ready",
                    **({"dub_lane": max(0, int(dub_lane))} if dub_lane is not None else {}),
                    **alignment,
                }
            )
        next_clips.append(next_item)
    return draft.model_copy(
        update={
            "cues": next_cues,
            "localized_subtitles": next_subtitles,
            "generated_candidates": next_candidates,
            "timeline_clips": next_clips,
        }
    )


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _cue_with_tts_result(cue: VideoLocalizationCue, batch_task_id: str, segment: BatchSegmentResult) -> VideoLocalizationCue:
    updated = _cue_with_tts_audio(
        cue,
        result_id=f"{batch_task_id}:{segment.segment_id}",
        generation_id=batch_task_id,
        output_path=segment.output_path,
        duration_ms=segment.duration_ms,
    )
    return _cue_with_tts_batch_status(updated, batch_task_id, TaskStatus.success, None)


def _subtitle_with_tts_result(
    subtitle: VideoLocalizationSubtitleCue,
    batch_task_id: str,
    segment: BatchSegmentResult,
) -> VideoLocalizationSubtitleCue:
    updated = _subtitle_with_tts_audio(
        subtitle,
        result_id=f"{batch_task_id}:{segment.segment_id}",
        generation_id=batch_task_id,
        output_path=segment.output_path,
        duration_ms=segment.duration_ms,
    )
    return _subtitle_with_tts_batch_status(updated, batch_task_id, TaskStatus.success, None)


def _cue_with_tts_audio(
    cue: VideoLocalizationCue,
    *,
    result_id: str,
    generation_id: str | None = None,
    output_path: str | None,
    duration_ms: int | None,
) -> VideoLocalizationCue:
    flags = [flag for flag in cue.quality_flags if flag != "tts_generated"]
    flags.append("tts_generated")
    update = {
        "tts_result_id": result_id,
        "tts_generation_id": generation_id,
        "tts_audio_path": output_path,
        "generated_duration_ms": duration_ms,
        "quality_flags": flags,
    }
    if cue.source_duration_ms is None:
        duration_ms = _cue_duration_ms(cue)
        if duration_ms is not None:
            update["source_duration_ms"] = duration_ms
    return cue.model_copy(update=update)


def _subtitle_with_tts_audio(
    subtitle: VideoLocalizationSubtitleCue,
    *,
    result_id: str,
    generation_id: str | None = None,
    output_path: str | None,
    duration_ms: int | None,
) -> VideoLocalizationSubtitleCue:
    flags = [flag for flag in subtitle.quality_flags if flag != "tts_generated"]
    flags.append("tts_generated")
    return subtitle.model_copy(
        update={
            "tts_result_id": result_id,
            "tts_generation_id": generation_id,
            "tts_audio_path": output_path,
            "generated_duration_ms": duration_ms,
            "quality_flags": flags,
        }
    )


def _with_localized_timeline_clip(
    clips: list[dict],
    subtitle: VideoLocalizationSubtitleCue,
    *,
    output_path: str,
    duration_ms: int | None,
    generation_id: str | None = None,
) -> list[dict]:
    source_cue_ids = list(dict.fromkeys(subtitle.source_cue_ids))
    primary_cue_id = next(iter(source_cue_ids), subtitle.linked_cue_id)
    clip_id = f"clip_{subtitle.subtitle_id}"
    alignment = _speech_onset_alignment(output_path, duration_ms, subtitle.start_ms)
    next_clips: list[dict] = []
    replaced = False
    for clip in clips:
        item = dict(clip)
        if item.get("subtitle_id") == subtitle.subtitle_id or item.get("clip_id") == clip_id:
            item.update(
                {
                    "subtitle_id": subtitle.subtitle_id,
                    "source_cue_ids": source_cue_ids,
                    "cue_id": item.get("cue_id") or primary_cue_id,
                    "track_id": "dub",
                    "audio_path": output_path,
                    "status": "ready",
                    "generation_id": generation_id,
                    **alignment,
                }
            )
            next_clips.append(item)
            replaced = True
        else:
            next_clips.append(item)
    if not replaced:
        next_clips.append(
            {
                "clip_id": clip_id,
                "subtitle_id": subtitle.subtitle_id,
                "source_cue_ids": source_cue_ids,
                "cue_id": primary_cue_id,
                "track_id": "dub",
                "start_ms": subtitle.start_ms,
                "end_ms": subtitle.end_ms,
                "source_start_ms": 0,
                "source_end_ms": duration_ms,
                "audio_path": output_path,
                "status": "ready",
                "generation_id": generation_id,
                **alignment,
            }
        )
    return next_clips


def _batch_segment_has_audio(segment: BatchSegmentResult) -> bool:
    return bool(
        segment.status == TaskStatus.success
        and segment.output_path
        and Path(segment.output_path).exists()
    )


def _batch_segment_failure(segment: BatchSegmentResult) -> tuple[TaskStatus | str, str | None]:
    status: TaskStatus | str = TaskStatus.failed if segment.status == TaskStatus.success else segment.status
    error = segment.error_message
    if segment.status == TaskStatus.success and segment.output_path and not Path(segment.output_path).exists():
        error = "生成标记成功，但输出音频文件不存在"
    return status, error


def _with_synced_generated_candidates(
    candidates: list[dict],
    *,
    cue_id: str,
    task_id: str,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
    subtitle_id: str | None = None,
) -> list[dict]:
    next_candidates = []
    for candidate in candidates:
        item = dict(candidate)
        if item.get("task_id") == task_id:
            item.update(
                {
                    "cue_id": item.get("cue_id") or cue_id,
                    "subtitle_id": item.get("subtitle_id") or subtitle_id,
                    "result_id": result_id,
                    "audio_path": output_path,
                    "duration_ms": duration_ms,
                    "status": "success",
                    "error": None,
                    "updated_at": now_iso(),
                }
            )
        next_candidates.append(item)
    return next_candidates


def _with_synced_timeline_clips(
    clips: list[dict],
    *,
    cue_id: str,
    task_id: str,
    output_path: str,
    duration_ms: int | None,
    generation_id: str | None = None,
    target_start_ms: int | None = None,
) -> list[dict]:
    alignment = _speech_onset_alignment(output_path, duration_ms, target_start_ms)
    next_clips = []
    for clip in clips:
        item = dict(clip)
        candidate_id = str(item.get("candidate_id") or "")
        if item.get("cue_id") == cue_id and (item.get("task_id") == task_id or candidate_id.endswith(task_id)):
            item.update(
                {
                    "audio_path": output_path,
                    "status": "ready",
                    "generation_id": generation_id,
                    **alignment,
                }
            )
        next_clips.append(item)
    return next_clips


def detect_first_effective_speech_ms(audio_path: str | Path) -> int:
    """Locate sustained speech energy while ignoring isolated codec/noise spikes."""
    try:
        audio, sample_rate = audio_tools.read_audio(audio_path)
    except Exception:
        return 0
    if sample_rate <= 0 or audio.size == 0:
        return 0
    frame_size = max(1, round(sample_rate * 0.01))
    frame_count = int(np.ceil(audio.size / frame_size))
    padded = np.pad(audio, (0, frame_count * frame_size - audio.size))
    frames = padded.reshape(frame_count, frame_size)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    peak = float(np.percentile(rms, 95)) if rms.size else 0.0
    if peak < 1e-5:
        return 0
    leading_count = max(1, min(len(rms), round(0.3 / 0.01)))
    noise_floor = float(np.percentile(rms[:leading_count], 20))
    threshold = max(0.0015, peak * 0.08, noise_floor * 3.0)
    active = rms >= threshold
    for index in range(len(active)):
        window = active[index : index + 3]
        if len(window) >= 2 and int(np.count_nonzero(window)) >= 2:
            return round(index * frame_size / sample_rate * 1000)
    return 0


def _speech_onset_alignment(
    output_path: str | Path,
    duration_ms: int | None,
    target_start_ms: int | None,
    *,
    leading_silence_ms: int = 80,
) -> dict[str, int]:
    effective_duration = duration_ms
    if effective_duration is None:
        try:
            effective_duration = int(audio_tools.probe_audio(output_path)["duration_ms"])
        except Exception:
            effective_duration = 0
    onset_ms = min(max(0, detect_first_effective_speech_ms(output_path)), max(0, effective_duration))
    target_ms = max(0, int(target_start_ms or 0))
    source_start_ms = max(0, onset_ms - leading_silence_ms)
    clip_start_ms = target_ms - (onset_ms - source_start_ms)
    if clip_start_ms < 0:
        source_start_ms = min(onset_ms, source_start_ms - clip_start_ms)
        clip_start_ms = 0
    source_end_ms = max(source_start_ms, int(effective_duration or 0))
    clip_end_ms = clip_start_ms + max(0, source_end_ms - source_start_ms)
    return {
        "start_ms": clip_start_ms,
        "end_ms": clip_end_ms,
        "source_start_ms": source_start_ms,
        "source_end_ms": source_end_ms,
        "speech_onset_ms": onset_ms,
        "alignment_lead_ms": onset_ms - source_start_ms,
    }


def _cue_with_tts_batch_status(
    cue: VideoLocalizationCue,
    batch_task_id: str,
    status: TaskStatus | str,
    error: str | None,
    *,
    attempted_at: str | None = None,
) -> VideoLocalizationCue:
    status_value = status.value if isinstance(status, TaskStatus) else str(status)
    flags = [flag for flag in cue.quality_flags if flag not in {"tts_batch_submitted", "tts_failed"}]
    if status_value in {"queued", "running", "postprocessing", "retrying"}:
        flags.append("tts_batch_submitted")
    if status_value in {"failed", "cancelled"}:
        flags.append("tts_failed")
    return cue.model_copy(
        update={
            "tts_batch_task_id": batch_task_id,
            "tts_batch_status": status_value,
            "tts_batch_error": error,
            "tts_attempted_at": attempted_at or cue.tts_attempted_at or now_iso(),
            "quality_flags": flags,
        }
    )


def _subtitle_with_tts_batch_status(
    subtitle: VideoLocalizationSubtitleCue,
    batch_task_id: str,
    status: TaskStatus | str,
    error: str | None,
    *,
    attempted_at: str | None = None,
) -> VideoLocalizationSubtitleCue:
    status_value = status.value if isinstance(status, TaskStatus) else str(status)
    flags = [flag for flag in subtitle.quality_flags if flag not in {"tts_batch_submitted", "tts_failed"}]
    if status_value in {"queued", "running", "postprocessing", "retrying"}:
        flags.append("tts_batch_submitted")
    if status_value in {"failed", "cancelled"}:
        flags.append("tts_failed")
    return subtitle.model_copy(
        update={
            "tts_batch_task_id": batch_task_id,
            "tts_batch_status": status_value,
            "tts_batch_error": error,
            "tts_attempted_at": attempted_at or subtitle.tts_attempted_at or now_iso(),
            "quality_flags": flags,
        }
    )


def _cue_duration_ms(cue: VideoLocalizationCue) -> int | None:
    if cue.start_ms is None or cue.end_ms is None:
        return None
    return max(0, cue.end_ms - cue.start_ms)


def _is_placeholder_text(value: str | None) -> bool:
    return bool(value and value.strip().startswith("【待本土化】"))


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "item"
