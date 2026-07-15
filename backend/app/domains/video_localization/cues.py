from __future__ import annotations

from pydantic import ValidationError

from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationCue, VideoLocalizationCueUpdate, VideoLocalizationDraft, now_iso


def from_asr_segments(
    *,
    segments: list,
    fallback_text: str,
    duration_ms: int | None,
    engine_id: str,
    existing_cue_ids: set[str],
) -> list[VideoLocalizationCue]:
    normalized_segments = _normalized_asr_segments(segments)
    if normalized_segments:
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
            for index, segment in enumerate(normalized_segments, start=1)
        ]
    fallback_text = fallback_text.strip()
    if not fallback_text:
        return []
    fallback_end_ms = duration_ms if duration_ms is not None and duration_ms > 0 else None
    return [
        VideoLocalizationCue(
            cue_id=_next_cue_id(existing_cue_ids, 1),
            start_ms=0,
            end_ms=fallback_end_ms,
            en_subtitle_text=fallback_text,
            source_duration_ms=fallback_end_ms,
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
            cue_update = _with_manual_edit_provenance(cue, update)
            next_cues.append(_normalize_cue_flags(VideoLocalizationCue(**{**cue.model_dump(), **cue_update})))
        except ValidationError as exc:
            raise AppException(400, "VIDEO_LOCALIZATION_CUE_INVALID", "Cue update is invalid", {"errors": exc.errors()}) from exc
        updated = True
    if not updated:
        raise AppException(404, "VIDEO_LOCALIZATION_CUE_NOT_FOUND", "Cue not found")
    _assert_updated_cue_does_not_overlap(next_cues, cue_id)
    return draft.model_copy(update={"cues": next_cues})


def is_replaceable_asr_candidate(cue: VideoLocalizationCue) -> bool:
    return (
        cue.review_status == "needs_review"
        and "generated_by_asr" in cue.quality_flags
        and "protected_manual_edit" not in cue.quality_flags
    )


def add_flags(flags: list[str], additions: list[str]) -> list[str]:
    next_flags = list(flags)
    for flag in additions:
        if flag not in next_flags:
            next_flags.append(flag)
    return next_flags


def _with_manual_edit_provenance(cue: VideoLocalizationCue, update: dict) -> dict:
    timing_confirmed = bool(update.get("confirm_timing", False))
    expected_start_ms = update.get("expected_start_ms")
    expected_end_ms = update.get("expected_end_ms")
    confirmation_method = update.get("timing_confirmation_method", "auditioned")
    text_changed = "en_subtitle_text" in update and update["en_subtitle_text"] != cue.en_subtitle_text
    timing_changed = (
        ("start_ms" in update and update["start_ms"] != cue.start_ms)
        or ("end_ms" in update and update["end_ms"] != cue.end_ms)
    )
    next_update = dict(update)
    for action_field in ("confirm_timing", "expected_start_ms", "expected_end_ms", "timing_confirmation_method"):
        next_update.pop(action_field, None)
    requested_flags = update.get("quality_flags")
    flags = list(requested_flags if isinstance(requested_flags, list) else cue.quality_flags)
    managed_flags = {"manual_timing_verified", "timing_review_required"}
    flags = [flag for flag in flags if flag not in managed_flags]
    if manual_timing_confirmation_is_current(cue):
        flags.append("manual_timing_verified")
    elif cue.manual_timing_review_status == "required":
        flags.append("timing_review_required")

    if not text_changed and not timing_changed and not timing_confirmed:
        if requested_flags is not None:
            next_update["quality_flags"] = add_flags([], flags)
        return next_update

    additions: list[str] = []
    if text_changed or timing_changed:
        additions.append("protected_manual_edit")
    if text_changed:
        additions.append("manual_text_edit")
    if timing_changed:
        additions.append("manual_timing_edit")
        flags = [flag for flag in flags if flag != "manual_timing_verified"]
        additions.append("timing_review_required")
        next_update.update(
            {
                "timing_confidence": "low",
                "manual_timing_revision": cue.manual_timing_revision + 1,
                "manual_timing_review_status": "required",
            }
        )
    if timing_confirmed:
        confirmed_start_ms = next_update.get("start_ms", cue.start_ms)
        confirmed_end_ms = next_update.get("end_ms", cue.end_ms)
        if confirmed_start_ms is None or confirmed_end_ms is None or confirmed_end_ms <= confirmed_start_ms:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_CUE_TIMING_CONFIRMATION_INVALID",
                "确认前需要有效的字幕入点和出点",
            )
        if expected_start_ms is not None and expected_start_ms != confirmed_start_ms:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_CUE_TIMING_CHANGED",
                "字幕入点已变化，请重新试听后确认",
            )
        if expected_end_ms is not None and expected_end_ms != confirmed_end_ms:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_CUE_TIMING_CHANGED",
                "字幕出点已变化，请重新试听后确认",
            )
        confirmed_revision = next_update.get("manual_timing_revision", cue.manual_timing_revision)
        flags = [flag for flag in flags if flag not in {"timing_review_required", "segment_timing_interpolated"}]
        additions = [flag for flag in additions if flag != "timing_review_required"]
        additions.append("manual_timing_verified")
        next_update.update(
            {
                "manual_timing_review_status": "confirmed",
                "manual_timing_confirmed_revision": confirmed_revision,
                "manual_timing_confirmed_at": now_iso(),
                "manual_timing_confirmed_start_ms": confirmed_start_ms,
                "manual_timing_confirmed_end_ms": confirmed_end_ms,
                "manual_timing_confirmation_method": confirmation_method,
            }
        )
    next_update["quality_flags"] = add_flags(flags, additions)
    return next_update


def _normalize_cue_flags(cue: VideoLocalizationCue) -> VideoLocalizationCue:
    removable = {"needs_speaker_assignment", "needs_zh_localization", "segment_timing_missing"}
    flags = [
        flag
        for flag in cue.quality_flags
        if flag not in removable and flag not in {"manual_timing_verified", "timing_review_required"}
    ]

    if manual_timing_confirmation_is_current(cue):
        flags.append("manual_timing_verified")
    elif cue.manual_timing_review_status == "required":
        flags.append("timing_review_required")

    if not cue.speaker_id:
        flags.append("needs_speaker_assignment")
    if not _localized_tracks_ready(cue):
        flags.append("needs_zh_localization")
    if cue.start_ms is None or cue.end_ms is None:
        flags.append("segment_timing_missing")

    return cue.model_copy(update={"quality_flags": flags})


def manual_timing_confirmation_is_current(cue: VideoLocalizationCue) -> bool:
    return (
        cue.manual_timing_review_status == "confirmed"
        and cue.manual_timing_confirmed_revision == cue.manual_timing_revision
        and cue.manual_timing_confirmed_start_ms == cue.start_ms
        and cue.manual_timing_confirmed_end_ms == cue.end_ms
        and cue.manual_timing_confirmation_method == "auditioned"
        and cue.manual_timing_confirmed_at is not None
    )


def sanitize_client_draft_timing_provenance(
    current: VideoLocalizationDraft | None,
    incoming: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Keep timing-confirmation audit fields under backend ownership."""
    current_by_id = {cue.cue_id: cue for cue in (current.cues if current else [])}
    sanitized: list[VideoLocalizationCue] = []
    for cue in incoming.cues:
        previous = current_by_id.get(cue.cue_id)
        flags = [flag for flag in cue.quality_flags if flag not in {"manual_timing_verified", "timing_review_required"}]
        if previous is None:
            sanitized.append(
                _normalize_cue_flags(
                    cue.model_copy(
                        update={
                            "manual_timing_revision": 0,
                            "manual_timing_review_status": "not_reviewed",
                            "manual_timing_confirmed_revision": None,
                            "manual_timing_confirmed_at": None,
                            "manual_timing_confirmed_start_ms": None,
                            "manual_timing_confirmed_end_ms": None,
                            "manual_timing_confirmation_method": None,
                            "quality_flags": flags,
                        }
                    )
                )
            )
            continue

        audit = {
            "manual_timing_revision": previous.manual_timing_revision,
            "manual_timing_review_status": previous.manual_timing_review_status,
            "manual_timing_confirmed_revision": previous.manual_timing_confirmed_revision,
            "manual_timing_confirmed_at": previous.manual_timing_confirmed_at,
            "manual_timing_confirmed_start_ms": previous.manual_timing_confirmed_start_ms,
            "manual_timing_confirmed_end_ms": previous.manual_timing_confirmed_end_ms,
            "manual_timing_confirmation_method": previous.manual_timing_confirmation_method,
        }
        timing_changed = cue.start_ms != previous.start_ms or cue.end_ms != previous.end_ms
        if timing_changed:
            audit.update(
                {
                    "manual_timing_revision": previous.manual_timing_revision + 1,
                    "manual_timing_review_status": "required",
                }
            )
            flags = add_flags(flags, ["protected_manual_edit", "manual_timing_edit", "timing_review_required"])
            cue = cue.model_copy(update={"timing_confidence": "low"})
        sanitized.append(_normalize_cue_flags(cue.model_copy(update={**audit, "quality_flags": flags})))
    return incoming.model_copy(update={"cues": sanitized})


def _localized_tracks_ready(cue: VideoLocalizationCue) -> bool:
    zh_text = (cue.zh_localized_subtitle_text or "").strip()
    tts_text = (cue.tts_recommended_text or "").strip()
    if not zh_text or not tts_text:
        return False
    return not zh_text.startswith("【待本土化】") and not tts_text.startswith("【待本土化】")


def _assert_updated_cue_does_not_overlap(cues: list[VideoLocalizationCue], updated_cue_id: str) -> None:
    timed = sorted(
        (cue for cue in cues if cue.start_ms is not None and cue.end_ms is not None),
        key=lambda cue: (cue.start_ms or 0, cue.end_ms or 0, cue.cue_id),
    )
    for previous, current in zip(timed, timed[1:]):
        if current.start_ms is None or previous.end_ms is None or current.start_ms >= previous.end_ms:
            continue
        if updated_cue_id not in {previous.cue_id, current.cue_id}:
            continue
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_CUE_OVERLAP",
            "字幕时间不能重叠，请将当前字幕限制在相邻字幕的出入点之间。",
            {
                "cue_id": updated_cue_id,
                "previous_cue_id": previous.cue_id,
                "current_cue_id": current.cue_id,
                "overlap_start_ms": current.start_ms,
                "overlap_end_ms": previous.end_ms,
            },
        )


def _next_cue_id(existing_cue_ids: set[str], index: int) -> str:
    candidate_index = index
    while True:
        candidate = f"cue_{candidate_index:04d}"
        if candidate not in existing_cue_ids:
            existing_cue_ids.add(candidate)
            return candidate
        candidate_index += 1


def _normalized_asr_segments(segments: list) -> list:
    ordered = sorted(
        (segment for segment in segments if segment.text.strip()),
        key=lambda segment: (segment.start_ms, segment.end_ms, segment.text),
    )
    normalized = []
    previous_end_ms = 0
    for segment in ordered:
        start_ms = max(0, int(segment.start_ms))
        end_ms = int(segment.end_ms)
        if end_ms <= start_ms:
            continue
        start_ms = max(start_ms, previous_end_ms)
        if end_ms <= start_ms:
            continue
        normalized.append(segment.model_copy(update={"start_ms": start_ms, "end_ms": end_ms}))
        previous_end_ms = end_ms
    return normalized
