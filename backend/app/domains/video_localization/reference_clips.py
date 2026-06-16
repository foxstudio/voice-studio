from __future__ import annotations

import re
from pathlib import Path

from app.domains.video_localization import media_assets
from app.errors import AppException
from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationReferenceClip,
    VideoLocalizationReferenceClipUpdate,
    VideoLocalizationTimeRange,
)
from app.services import audio_tools


def with_reference_clips_from_cues(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft:
    if draft.stems.separation_status != "completed" or not draft.stems.vocals_clean_path:
        raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING", "Separate clean vocals before creating reference clips")

    vocals_path = Path(draft.stems.vocals_clean_path)
    if not vocals_path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_CLEAN_VOCALS_NOT_FOUND", "Clean vocals file is missing")

    refs_dir = media_assets.project_video_localization_dir(project_id) / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    existing_refs = {clip.reference_clip_id: clip for clip in draft.reference_clips}
    next_refs = list(draft.reference_clips)
    next_cues = []
    speaker_updates = {speaker.speaker_id: speaker.model_copy(deep=True) for speaker in draft.speakers}

    for cue in draft.cues:
        next_cue = cue
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
            next_refs.append(reference)
            existing_refs[reference_id] = reference
        if not next_cue.reference_clip_id:
            next_cue = next_cue.model_copy(update={"reference_clip_id": reference_id})
        speaker = speaker_updates.get(cue.speaker_id or "")
        if speaker:
            if reference_id not in speaker.reference_clip_ids:
                speaker.reference_clip_ids.append(reference_id)
            speaker.time_ranges.append(VideoLocalizationTimeRange(start_ms=cue.start_ms, end_ms=cue.end_ms, source="reference_candidate"))
        next_cues.append(next_cue)

    if len(next_refs) == len(draft.reference_clips):
        raise AppException(400, "VIDEO_LOCALIZATION_REFERENCE_CANDIDATES_EMPTY", "No cue has speaker and time range for reference clipping")

    next_speakers = [speaker_updates.get(speaker.speaker_id, speaker) for speaker in draft.speakers]
    return draft.model_copy(update={"reference_clips": next_refs, "cues": next_cues, "speakers": next_speakers})


def with_updated_reference_clip(draft: VideoLocalizationDraft, reference_clip_id: str, patch: VideoLocalizationReferenceClipUpdate) -> VideoLocalizationDraft:
    updated = False
    next_refs: list[VideoLocalizationReferenceClip] = []

    for clip in draft.reference_clips:
        if clip.reference_clip_id != reference_clip_id:
            next_refs.append(clip)
            continue
        update = patch.model_dump(exclude_unset=True)
        next_clip = clip.model_copy(update=update)
        _validate_reference_clip_update(next_clip)
        next_refs.append(_reference_clip_with_review_flags(next_clip))
        updated = True

    if not updated:
        raise AppException(404, "VIDEO_LOCALIZATION_REFERENCE_CLIP_NOT_FOUND", "Reference clip not found")
    return draft.model_copy(update={"reference_clips": next_refs})


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


def _cue_can_seed_reference(cue: VideoLocalizationCue) -> bool:
    return bool(cue.speaker_id and cue.speaker_id != "mixed" and cue.start_ms is not None and cue.end_ms is not None and cue.end_ms > cue.start_ms)


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
