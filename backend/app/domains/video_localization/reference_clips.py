from __future__ import annotations

import re
from pathlib import Path

from app.domains.video_localization import media_assets
from app.errors import AppException
from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationReferenceClip,
    VideoLocalizationReferenceClipCreate,
    VideoLocalizationReferenceClipUpdate,
    VideoLocalizationTimeRange,
)
from app.services import audio_tools


def with_reference_clips_from_cues(project_id: str, draft: VideoLocalizationDraft, payload: VideoLocalizationReferenceClipCreate | None = None) -> VideoLocalizationDraft:
    if draft.stems.separation_status != "completed" or not draft.stems.vocals_clean_path:
        raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING", "Separate clean vocals before creating reference clips")

    vocals_path = Path(draft.stems.vocals_clean_path)
    if not vocals_path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_NOT_FOUND", "Clean vocals file is missing")

    if payload and payload.start_ms is not None and payload.end_ms is not None:
        return _with_reference_clip_from_selection(project_id, draft, vocals_path, payload)

    refs_dir = media_assets.project_video_localization_dir(project_id) / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    existing_refs = {clip.reference_clip_id: clip for clip in draft.reference_clips}
    next_refs = list(draft.reference_clips)
    next_cues = []
    speaker_updates = {speaker.speaker_id: speaker.model_copy(deep=True) for speaker in draft.speakers}
    target_cue_id = payload.cue_id if payload else None
    changed = False

    for cue in draft.cues:
        next_cue = cue
        if target_cue_id and cue.cue_id != target_cue_id:
            next_cues.append(next_cue)
            continue
        if not _cue_can_seed_reference(cue):
            next_cues.append(next_cue)
            continue
        reference_id = _reference_id_for_cue(cue)
        if reference_id not in existing_refs:
            clip_path = media_assets.unique_path(refs_dir / f"{reference_id}.wav")
            media_assets.cut_audio_clip(vocals_path, clip_path, cue.start_ms or 0, cue.end_ms or 0)
            meta = audio_tools.probe_audio(clip_path)
            reference = VideoLocalizationReferenceClip(
                reference_clip_id=reference_id,
                speaker_id=cue.speaker_id,
                source_stem="vocals_clean",
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                duration_ms=meta.get("duration_ms") or _cue_duration_ms(cue),
                audio_path=str(clip_path),
                cleanliness="needs_review",
                asr_text=cue.en_subtitle_text,
                asr_status="candidate" if cue.en_subtitle_text else "pending",
                quality_flags=["generated_from_cue", "needs_cleanliness_review"],
            )
            reference = _reference_clip_with_create_metadata(reference, payload)
            next_refs.append(reference)
            existing_refs[reference_id] = reference
            changed = True
        elif payload:
            updated_reference = _reference_clip_with_create_metadata(existing_refs[reference_id], payload)
            if updated_reference != existing_refs[reference_id]:
                next_refs = [updated_reference if clip.reference_clip_id == reference_id else clip for clip in next_refs]
                existing_refs[reference_id] = updated_reference
                changed = True
        if not next_cue.reference_clip_id:
            next_cue = next_cue.model_copy(update={"reference_clip_id": reference_id})
            changed = True
        speaker = speaker_updates.get(cue.speaker_id or "")
        if speaker:
            if reference_id not in speaker.reference_clip_ids:
                speaker.reference_clip_ids.append(reference_id)
                changed = True
            if not _has_reference_time_range(speaker.time_ranges, cue):
                speaker.time_ranges.append(VideoLocalizationTimeRange(start_ms=cue.start_ms, end_ms=cue.end_ms, source="reference_candidate"))
                changed = True
        next_cues.append(next_cue)

    if not changed:
        raise AppException(400, "VIDEO_LOCALIZATION_REFERENCE_CANDIDATES_EMPTY", "No cue has speaker and time range for reference clipping")

    next_speakers = [speaker_updates.get(speaker.speaker_id, speaker) for speaker in draft.speakers]
    return draft.model_copy(update={"reference_clips": next_refs, "cues": next_cues, "speakers": next_speakers})


def _with_reference_clip_from_selection(
    project_id: str,
    draft: VideoLocalizationDraft,
    vocals_path: Path,
    payload: VideoLocalizationReferenceClipCreate,
) -> VideoLocalizationDraft:
    start_ms = int(payload.start_ms or 0)
    end_ms = int(payload.end_ms or 0)
    if end_ms <= start_ms:
        raise AppException(400, "VIDEO_LOCALIZATION_REFERENCE_RANGE_INVALID", "Reference selection requires a valid start and end time")
    if draft.source_media.duration_ms and end_ms > draft.source_media.duration_ms:
        raise AppException(400, "VIDEO_LOCALIZATION_REFERENCE_RANGE_OUT_OF_BOUNDS", "Reference selection exceeds source duration")

    selected_cue = next((cue for cue in draft.cues if cue.cue_id == payload.cue_id), None)
    speaker_id = _clean_text(payload.speaker_id) or (selected_cue.speaker_id if selected_cue else None)
    reference_id = f"ref_{_safe_identifier(speaker_id or 'selection')}_{start_ms}_{end_ms}"
    existing = next((clip for clip in draft.reference_clips if clip.reference_clip_id == reference_id), None)

    if existing:
        reference = _reference_clip_with_create_metadata(existing, payload)
        next_refs = [reference if clip.reference_clip_id == reference_id else clip for clip in draft.reference_clips]
    else:
        refs_dir = media_assets.project_video_localization_dir(project_id) / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        clip_path = media_assets.unique_path(refs_dir / f"{reference_id}.wav")
        media_assets.cut_audio_clip(vocals_path, clip_path, start_ms, end_ms)
        meta = audio_tools.probe_audio(clip_path)
        reference = VideoLocalizationReferenceClip(
            reference_clip_id=reference_id,
            speaker_id=speaker_id,
            source_stem="vocals_clean",
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=meta.get("duration_ms") or end_ms - start_ms,
            audio_path=str(clip_path),
            cleanliness="needs_review",
            asr_text=_clean_text(payload.asr_text) or (selected_cue.en_subtitle_text if selected_cue else None),
            asr_status="candidate" if (_clean_text(payload.asr_text) or (selected_cue and selected_cue.en_subtitle_text)) else "pending",
            quality_flags=["generated_from_selection", "needs_cleanliness_review"],
        )
        reference = _reference_clip_with_create_metadata(reference, payload)
        next_refs = [*draft.reference_clips, reference]

    if not reference.cover_frame_path:
        cover_path = _capture_selection_cover(project_id, draft, reference_id, start_ms, end_ms)
        if cover_path:
            reference = reference.model_copy(update={"cover_frame_path": str(cover_path)})
            next_refs = [reference if clip.reference_clip_id == reference_id else clip for clip in next_refs]

    next_cues = [
        cue.model_copy(update={"reference_clip_id": reference_id}) if payload.cue_id and cue.cue_id == payload.cue_id else cue
        for cue in draft.cues
    ]
    next_speakers = []
    for speaker in draft.speakers:
        if not speaker_id or speaker.speaker_id != speaker_id:
            next_speakers.append(speaker)
            continue
        next_speaker = speaker.model_copy(deep=True)
        if reference_id not in next_speaker.reference_clip_ids:
            next_speaker.reference_clip_ids.append(reference_id)
        if not any(item.source == "manual_selection" and item.start_ms == start_ms and item.end_ms == end_ms for item in next_speaker.time_ranges):
            next_speaker.time_ranges.append(VideoLocalizationTimeRange(start_ms=start_ms, end_ms=end_ms, source="manual_selection"))
        next_speakers.append(next_speaker)
    return draft.model_copy(update={"reference_clips": next_refs, "cues": next_cues, "speakers": next_speakers})


def _capture_selection_cover(project_id: str, draft: VideoLocalizationDraft, reference_id: str, start_ms: int, end_ms: int) -> Path | None:
    video_path = Path(draft.source_media.video_path) if draft.source_media.video_path else None
    if not video_path or not video_path.exists():
        return None
    destination = media_assets.project_video_localization_dir(project_id) / "references" / f"{reference_id}.jpg"
    try:
        return media_assets.extract_video_frame(video_path, destination, start_ms + (end_ms - start_ms) // 2)
    except AppException:
        return None


def with_updated_reference_clip(draft: VideoLocalizationDraft, reference_clip_id: str, patch: VideoLocalizationReferenceClipUpdate) -> VideoLocalizationDraft:
    updated = False
    next_refs: list[VideoLocalizationReferenceClip] = []

    for clip in draft.reference_clips:
        if clip.reference_clip_id != reference_clip_id:
            next_refs.append(clip)
            continue
        update = patch.model_dump(exclude_unset=True)
        update.update(_reference_metadata_update(update))
        next_clip = clip.model_copy(update=update)
        _validate_reference_clip_update(next_clip)
        next_refs.append(_reference_clip_with_review_flags(next_clip))
        updated = True

    if not updated:
        raise AppException(404, "VIDEO_LOCALIZATION_REFERENCE_CLIP_NOT_FOUND", "Reference clip not found")
    return draft.model_copy(update={"reference_clips": next_refs})


def without_reference_clip(draft: VideoLocalizationDraft, reference_clip_id: str) -> VideoLocalizationDraft:
    if not any(clip.reference_clip_id == reference_clip_id for clip in draft.reference_clips):
        raise AppException(404, "VIDEO_LOCALIZATION_REFERENCE_CLIP_NOT_FOUND", "Reference clip not found")

    next_refs = [clip for clip in draft.reference_clips if clip.reference_clip_id != reference_clip_id]
    next_cues = [
        cue.model_copy(update={"reference_clip_id": None}) if cue.reference_clip_id == reference_clip_id else cue
        for cue in draft.cues
    ]
    next_speakers = []
    for speaker in draft.speakers:
        if reference_clip_id in speaker.reference_clip_ids:
            next_speakers.append(
                speaker.model_copy(update={"reference_clip_ids": [item for item in speaker.reference_clip_ids if item != reference_clip_id]})
            )
        else:
            next_speakers.append(speaker)
    return draft.model_copy(update={"reference_clips": next_refs, "cues": next_cues, "speakers": next_speakers})


def _validate_reference_clip_update(clip: VideoLocalizationReferenceClip) -> None:
    if clip.cleanliness == "clean":
        if clip.source_stem != "vocals_clean":
            raise AppException(400, "VIDEO_LOCALIZATION_REFERENCE_NOT_FROM_CLEAN_VOCALS", "Clean reference clips must come from separated clean vocals")
        if not clip.audio_path or not Path(clip.audio_path).exists():
            raise AppException(400, "VIDEO_LOCALIZATION_REFERENCE_FILE_MISSING", "Reference audio file is missing")
    if clip.asr_status == "verified" and not (clip.asr_text or "").strip():
        raise AppException(400, "VIDEO_LOCALIZATION_REFERENCE_ASR_TEXT_MISSING", "Verified reference clips require ASR text")


def _reference_clip_with_review_flags(clip: VideoLocalizationReferenceClip) -> VideoLocalizationReferenceClip:
    flags = [flag for flag in clip.quality_flags if flag not in {"needs_cleanliness_review", "human_verified_reference", "reference_blocked"}]
    if clip.cleanliness == "clean" and clip.asr_status == "verified":
        flags.append("human_verified_reference")
    elif clip.cleanliness == "blocked" or clip.asr_status == "failed":
        flags.append("reference_blocked")
    else:
        flags.append("needs_cleanliness_review")
    return clip.model_copy(update={"quality_flags": flags})


def _reference_clip_with_create_metadata(clip: VideoLocalizationReferenceClip, payload: VideoLocalizationReferenceClipCreate | None) -> VideoLocalizationReferenceClip:
    if not payload:
        return clip
    update = _reference_metadata_update(payload.model_dump(exclude_unset=True))
    return clip.model_copy(update=update) if update else clip


def _reference_metadata_update(data: dict) -> dict:
    update: dict = {}
    for field in ("title", "person_name", "emotion", "description", "cover_frame_path"):
        if field in data:
            update[field] = _clean_text(data.get(field))
    if "tags" in data:
        update["tags"] = _clean_tags(data.get("tags"))
    return update


def _clean_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _clean_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    seen = set()
    tags: list[str] = []
    for item in value:
        tag = str(item).strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags


def _cue_can_seed_reference(cue: VideoLocalizationCue) -> bool:
    return bool(cue.speaker_id and cue.speaker_id != "mixed" and cue.start_ms is not None and cue.end_ms is not None and cue.end_ms > cue.start_ms)


def _has_reference_time_range(ranges: list[VideoLocalizationTimeRange], cue: VideoLocalizationCue) -> bool:
    return any(item.source == "reference_candidate" and item.start_ms == cue.start_ms and item.end_ms == cue.end_ms for item in ranges)


def _cue_duration_ms(cue: VideoLocalizationCue) -> int | None:
    if cue.start_ms is None or cue.end_ms is None:
        return None
    return max(0, cue.end_ms - cue.start_ms)


def _reference_id_for_cue(cue: VideoLocalizationCue) -> str:
    speaker_id = _safe_identifier(cue.speaker_id or "speaker")
    cue_id = _safe_identifier(cue.cue_id)
    return f"ref_{speaker_id}_{cue_id}"


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "item"
