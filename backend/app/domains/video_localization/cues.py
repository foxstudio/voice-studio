from __future__ import annotations

from pydantic import ValidationError

from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationCueUpdate, VideoLocalizationDraft


def from_asr_segments(
    *,
    segments: list,
    fallback_text: str,
    duration_ms: int | None,
    engine_id: str,
    existing_cue_ids: set[str],
) -> list[VideoLocalizationCue]:
    if segments:
        return [
            VideoLocalizationCue(
                cue_id=_next_cue_id(existing_cue_ids, index),
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                en_subtitle_text=segment.text,
                source_duration_ms=max(0, segment.end_ms - segment.start_ms),
                review_status="needs_review",
                quality_flags=["generated_by_asr", f"engine:{engine_id}", "needs_speaker_assignment", "needs_zh_localization"],
            )
            for index, segment in enumerate(segments, start=1)
            if segment.text.strip()
        ]
    if not fallback_text:
        return []
    return [
        VideoLocalizationCue(
            cue_id=_next_cue_id(existing_cue_ids, 1),
            start_ms=0,
            end_ms=duration_ms,
            en_subtitle_text=fallback_text,
            source_duration_ms=duration_ms,
            review_status="needs_review",
            quality_flags=["generated_by_asr", f"engine:{engine_id}", "needs_speaker_assignment", "needs_zh_localization", "segment_timing_missing"],
        )
    ]


def with_updated_cue(draft: VideoLocalizationDraft, cue_id: str, patch: VideoLocalizationCueUpdate) -> VideoLocalizationDraft:
    update = patch.model_dump(exclude_unset=True)
    updated = False
    next_cues: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        if cue.cue_id != cue_id:
            next_cues.append(cue)
            continue
        try:
            next_cues.append(_normalize_cue_flags(VideoLocalizationCue(**{**cue.model_dump(), **update})))
        except ValidationError as exc:
            raise AppException(400, "VIDEO_LOCALIZATION_CUE_INVALID", "Cue update is invalid", {"errors": exc.errors()}) from exc
        updated = True
    if not updated:
        raise AppException(404, "VIDEO_LOCALIZATION_CUE_NOT_FOUND", "Cue not found")
    return draft.model_copy(update={"cues": next_cues})


def is_replaceable_asr_candidate(cue: VideoLocalizationCue) -> bool:
    return cue.review_status == "needs_review" and "generated_by_asr" in cue.quality_flags


def add_flags(flags: list[str], additions: list[str]) -> list[str]:
    next_flags = list(flags)
    for flag in additions:
        if flag not in next_flags:
            next_flags.append(flag)
    return next_flags


def _normalize_cue_flags(cue: VideoLocalizationCue) -> VideoLocalizationCue:
    removable = {"needs_speaker_assignment", "needs_zh_localization", "segment_timing_missing"}
    flags = [flag for flag in cue.quality_flags if flag not in removable]

    if not cue.speaker_id:
        flags.append("needs_speaker_assignment")
    if not _localized_tracks_ready(cue):
        flags.append("needs_zh_localization")
    if cue.start_ms is None or cue.end_ms is None:
        flags.append("segment_timing_missing")

    return cue.model_copy(update={"quality_flags": flags})


def _localized_tracks_ready(cue: VideoLocalizationCue) -> bool:
    zh_text = (cue.zh_localized_subtitle_text or "").strip()
    tts_text = (cue.tts_recommended_text or "").strip()
    if not zh_text or not tts_text:
        return False
    return not zh_text.startswith("【待本土化】") and not tts_text.startswith("【待本土化】")


def _next_cue_id(existing_cue_ids: set[str], index: int) -> str:
    candidate_index = index
    while True:
        candidate = f"cue_{candidate_index:04d}"
        if candidate not in existing_cue_ids:
            existing_cue_ids.add(candidate)
            return candidate
        candidate_index += 1
