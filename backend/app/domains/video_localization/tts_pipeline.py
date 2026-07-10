from __future__ import annotations

import re
from pathlib import Path

from app.errors import AppException
from app.domains.video_localization.schemas import (
    BatchGenerateRequest,
    BatchSegmentInput,
    BatchSegmentResult,
    BatchTask,
    LicenseStatus,
    TaskStatus,
    VideoLocalizationCue,
    VideoLocalizationDraft,
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
    reference_by_id = {clip.reference_clip_id: clip for clip in draft.reference_clips}
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


def with_batch_submitted(draft: VideoLocalizationDraft, batch_task_id: str, cue_ids: list[str], *, attempted_at: str) -> VideoLocalizationDraft:
    cue_id_set = set(cue_ids)
    if not cue_id_set:
        return draft
    updated = False
    next_cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        if cue.cue_id in cue_id_set:
            next_cues.append(_cue_with_tts_batch_status(cue, batch_task_id, TaskStatus.queued, None, attempted_at=attempted_at))
            updated = True
        else:
            next_cues.append(cue)
    if not updated:
        return draft
    return draft.model_copy(update={"cues": next_cues})


def with_synced_batch_results(draft: VideoLocalizationDraft, batch: BatchTask) -> VideoLocalizationDraft:
    segments_by_id = {segment.segment_id: segment for segment in batch.segments}
    if not segments_by_id:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_RESULTS_EMPTY", "Batch task has no segment results")

    updated = False
    next_cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        segment = segments_by_id.get(cue.cue_id)
        if not segment:
            next_cues.append(cue)
            continue
        if segment.status == TaskStatus.success and segment.output_path and Path(segment.output_path).exists():
            next_cues.append(_cue_with_tts_result(cue, batch.batch_task_id, segment))
        else:
            status = TaskStatus.failed if segment.status == TaskStatus.success else segment.status
            error = segment.error_message
            if segment.status == TaskStatus.success and segment.output_path and not Path(segment.output_path).exists():
                error = "生成标记成功，但输出音频文件不存在"
            next_cues.append(_cue_with_tts_batch_status(cue, batch.batch_task_id, status, error))
        updated = True

    if not updated:
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_BATCH_CUES_UNMATCHED", "Batch task results do not match any cue ids")
    return draft.model_copy(update={"cues": next_cues})


def with_single_tts_result(
    draft: VideoLocalizationDraft,
    cue_id: str,
    *,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
    task_id: str | None = None,
) -> VideoLocalizationDraft:
    path = Path(output_path)
    if not path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_TTS_AUDIO_NOT_FOUND", "TTS audio file not found")

    updated = False
    next_cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        if cue.cue_id != cue_id:
            next_cues.append(cue)
            continue
        next_cues.append(_cue_with_tts_audio(cue, result_id=result_id, output_path=str(path), duration_ms=duration_ms))
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
        )
    return draft.model_copy(update=update)


def tts_audio_path(draft: VideoLocalizationDraft, cue_id: str) -> Path | None:
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
    next_cues = [
        _cue_with_tts_audio(item, result_id=result_id, output_path=str(audio_path), duration_ms=duration_ms) if item.cue_id == cue_id else item
        for item in draft.cues
    ]
    next_candidates = []
    for item in draft.generated_candidates:
        next_item = dict(item)
        if next_item.get("cue_id") == cue_id:
            next_item["selected"] = next_item.get("candidate_id") == candidate_id
        next_candidates.append(next_item)
    clip_start = cue.start_ms or 0
    clip_end = cue.end_ms or clip_start + (duration_ms or cue.source_duration_ms or 1800)
    next_clip = {
        "clip_id": f"clip_{cue_id}",
        "cue_id": cue_id,
        "candidate_id": candidate_id,
        "track_id": "dub",
        "start_ms": clip_start,
        "end_ms": clip_end,
        "source_start_ms": 0,
        "source_end_ms": duration_ms,
        "audio_path": str(audio_path),
        "status": "ready",
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


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _cue_with_tts_result(cue: VideoLocalizationCue, batch_task_id: str, segment: BatchSegmentResult) -> VideoLocalizationCue:
    updated = _cue_with_tts_audio(
        cue,
        result_id=f"{batch_task_id}:{segment.segment_id}",
        output_path=segment.output_path,
        duration_ms=segment.duration_ms,
    )
    return _cue_with_tts_batch_status(updated, batch_task_id, TaskStatus.success, None)


def _cue_with_tts_audio(cue: VideoLocalizationCue, *, result_id: str, output_path: str | None, duration_ms: int | None) -> VideoLocalizationCue:
    flags = [flag for flag in cue.quality_flags if flag != "tts_generated"]
    flags.append("tts_generated")
    update = {
        "tts_result_id": result_id,
        "tts_audio_path": output_path,
        "generated_duration_ms": duration_ms,
        "quality_flags": flags,
    }
    if cue.source_duration_ms is None:
        duration_ms = _cue_duration_ms(cue)
        if duration_ms is not None:
            update["source_duration_ms"] = duration_ms
    return cue.model_copy(update=update)


def _with_synced_generated_candidates(
    candidates: list[dict],
    *,
    cue_id: str,
    task_id: str,
    result_id: str,
    output_path: str,
    duration_ms: int | None,
) -> list[dict]:
    next_candidates = []
    for candidate in candidates:
        item = dict(candidate)
        if item.get("task_id") == task_id:
            item.update(
                {
                    "cue_id": item.get("cue_id") or cue_id,
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
) -> list[dict]:
    next_clips = []
    for clip in clips:
        item = dict(clip)
        candidate_id = str(item.get("candidate_id") or "")
        if item.get("cue_id") == cue_id and (item.get("task_id") == task_id or candidate_id.endswith(task_id)):
            item.update(
                {
                    "audio_path": output_path,
                    "status": "ready",
                }
            )
            if duration_ms is not None and item.get("source_end_ms") in (None, ""):
                item["source_end_ms"] = duration_ms
        next_clips.append(item)
    return next_clips


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


def _cue_duration_ms(cue: VideoLocalizationCue) -> int | None:
    if cue.start_ms is None or cue.end_ms is None:
        return None
    return max(0, cue.end_ms - cue.start_ms)


def _is_placeholder_text(value: str | None) -> bool:
    return bool(value and value.strip().startswith("【待本土化】"))


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "item"
