from __future__ import annotations

from pydantic import ValidationError

from app.errors import AppException
from app.domains.video_localization.schemas import (
    VideoLocalizationAutomaticTimingConfirmationEvidence,
    VideoLocalizationAsrVadInterval,
    VideoLocalizationAsrVadSourceTimingCorrection,
    VideoLocalizationAsrVadTimingCorrectionRequest,
    VideoLocalizationCue,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationSourceTimingIssue,
    VideoLocalizationSourceTimingWord,
    VideoLocalizationSupersededAcousticSupport,
    VideoLocalizationTranscriptionState,
    now_iso,
)

GENERATED_ASR_REVIEW_REQUIRED_FLAGS = frozenset(
    {
        "needs_speaker_assignment",
        "speaker_overlap_detected",
        "speaker_review_required",
        "timing_review_required",
        "segment_timing_interpolated",
        "media_end_clipped",
        "terminal_fragment_review_required",
        "segmentation_review_required",
    }
)


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
        generated = []
        for index, segment in enumerate(normalized_segments, start=1):
            flags = [
                "generated_by_asr",
                f"engine:{engine_id}",
                "needs_zh_localization",
            ]
            generated.append(VideoLocalizationCue(
                cue_id=_next_cue_id(existing_cue_ids, index),
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                en_subtitle_text=segment.text,
                source_duration_ms=max(0, segment.end_ms - segment.start_ms),
                review_status=generated_asr_review_status(flags),
                quality_flags=flags,
            ))
        return generated
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
            quality_flags=["generated_by_asr", f"engine:{engine_id}", "needs_zh_localization", "segment_timing_missing"],
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
            cue_update = with_manual_edit_provenance(cue, update)
            candidate = VideoLocalizationCue(
                **{**cue.model_dump(), **cue_update}
            )
            if (
                patch.confirm_timing
                and patch.timing_confirmation_method == "asr_vad_verified"
            ):
                validate_automatic_timing_confirmation(draft, candidate)
            next_cues.append(
                _normalize_cue_flags(
                    candidate,
                    speaker_assignment_required=(
                        draft_requires_speaker_assignment(draft)
                    ),
                )
            )
        except ValidationError as exc:
            raise AppException(400, "VIDEO_LOCALIZATION_CUE_INVALID", "Cue update is invalid", {"errors": exc.errors()}) from exc
        updated = True
    if not updated:
        raise AppException(404, "VIDEO_LOCALIZATION_CUE_NOT_FOUND", "Cue not found")
    _assert_updated_cue_does_not_overlap(next_cues, cue_id)
    return draft.model_copy(update={"cues": next_cues})


def apply_asr_vad_source_timing_correction(
    draft: VideoLocalizationDraft,
    request: VideoLocalizationAsrVadTimingCorrectionRequest,
) -> VideoLocalizationDraft:
    """Atomically apply one verified vocals ASR/VAD word-timing correction.

    This command is deliberately separate from an audition action: it keeps the
    immutable source tokens and speaker identity, updates only the supplied
    source timings and their display anchors, and records the raw VAD interval
    evidence that supports the resulting automatic cue confirmations.
    """

    transcription = draft.transcription
    if (
        transcription is None
        or transcription.revision_id != request.transcription_revision_id
        # The batch itself supplies complete, immutable word snapshots for its
        # bounded range.  It may repair that range while unrelated source words
        # leave the document-level aligner in its honest partial state.
        or transcription.alignment_status not in {"completed", "partial"}
        or transcription.audio_boundary_status != "completed"
        or transcription.alignment_source_track_id != request.source_track_id
        or transcription.alignment_audio_sha256 != request.audio_sha256
        or draft.stems.separation_status != "completed"
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SOURCE_TIMING_EVIDENCE_STALE",
            "源听写版本、分离人声音轨或 VAD 状态已变化，请重新核对。",
        )

    current_words_by_id = {word.word_id: word for word in transcription.words}
    requested_word_ids = [item.word_id for item in request.word_timings]
    current_words = [current_words_by_id.get(word_id) for word_id in requested_word_ids]
    if not all(current_words):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_SOURCE_TIMING_WORD_INVALID",
            "校正包含当前听写中不存在的词。",
        )
    source_indexes = {word.word_id: index for index, word in enumerate(transcription.words)}
    if requested_word_ids != sorted(requested_word_ids, key=source_indexes.__getitem__):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_SOURCE_TIMING_WORD_ORDER_INVALID",
            "校正词必须保持当前源听写的原始顺序。",
        )
    if any(
        current.text != requested.text
        for current, requested in zip(current_words, request.word_timings)
    ):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_SOURCE_TIMING_TEXT_CHANGED",
            "源词文本或标点不能在时间校正中改写。",
        )

    cues_by_id = {cue.cue_id: cue for cue in draft.cues}
    correction_word_ids: list[str] = []
    for correction in request.cue_corrections:
        cue = cues_by_id.get(correction.cue_id)
        if cue is None:
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_CUE_NOT_FOUND",
                "要校正的源字幕不存在。",
                {"cue_id": correction.cue_id},
            )
        if (
            cue.transcription_revision_id != transcription.revision_id
            or cue.start_ms != correction.expected_start_ms
            or cue.end_ms != correction.expected_end_ms
            or cue.source_word_ids != correction.source_word_ids
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_SOURCE_TIMING_CUE_STALE",
                "源字幕范围、版本或词级归属已变化，请重新读取后校正。",
                {"cue_id": correction.cue_id},
            )
        correction_word_ids.extend(correction.source_word_ids)
    if correction_word_ids != requested_word_ids:
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_SOURCE_TIMING_WORD_COVERAGE_INVALID",
            "校正必须完整且仅覆盖本批源字幕原有的词级归属。",
        )

    interval_ids = {item.interval_id for item in request.vad_intervals}
    for correction in request.cue_corrections:
        if not set(correction.vad_interval_ids).issubset(interval_ids):
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_SOURCE_TIMING_VAD_REFERENCE_INVALID",
                "源字幕引用了本次 VAD 证据中不存在的区间。",
                {"cue_id": correction.cue_id},
            )

    changed_words: dict[str, object] = {}
    historical_support: list[VideoLocalizationSupersededAcousticSupport] = []
    for current, requested in zip(current_words, request.word_timings):
        zero_width = requested.start_ms == requested.end_ms
        support_is_current = (
            current.acoustic_support_start_ms is None
            or current.acoustic_support_end_ms is None
            or (
                current.acoustic_support_start_ms >= requested.start_ms
                and current.acoustic_support_end_ms <= requested.end_ms
            )
        )
        if not support_is_current:
            historical_support.append(
                VideoLocalizationSupersededAcousticSupport(
                    word_id=current.word_id,
                    start_ms=current.acoustic_support_start_ms,
                    end_ms=current.acoustic_support_end_ms,
                    source=current.acoustic_support_source,
                )
            )
        changed_words[current.word_id] = current.model_copy(
            update={
                "start_ms": requested.start_ms,
                "end_ms": requested.end_ms,
                # A point anchor is retained for source fidelity but remains
                # explicitly low-confidence and cannot auto-confirm a cue.
                "timing_confidence": "low" if zero_width else "high",
                "timing_source": (
                    current.timing_source if zero_width else "asr_vad_verified"
                ),
                "acoustic_support_start_ms": (
                    current.acoustic_support_start_ms if support_is_current else None
                ),
                "acoustic_support_end_ms": (
                    current.acoustic_support_end_ms if support_is_current else None
                ),
                "acoustic_support_source": (
                    current.acoustic_support_source if support_is_current else None
                ),
            }
        )

    correction_record = VideoLocalizationAsrVadSourceTimingCorrection(
        transcription_revision_id=transcription.revision_id,
        source_track_id=request.source_track_id,
        audio_sha256=request.audio_sha256,
        analysis_protocol=request.analysis_protocol,
        analysis_start_ms=request.analysis_start_ms,
        analysis_end_ms=request.analysis_end_ms,
        word_ids=requested_word_ids,
        word_timings=[
            VideoLocalizationSourceTimingWord(
                word_id=item.word_id,
                text=item.text,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
            )
            for item in request.word_timings
        ],
        vad_intervals=[
            VideoLocalizationAsrVadInterval(
                interval_id=item.interval_id,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
            )
            for item in request.vad_intervals
        ],
        issues=[
            VideoLocalizationSourceTimingIssue(
                code=item.code,
                result=item.result,
                word_ids=item.word_ids,
            )
            for item in request.issues
        ],
        superseded_acoustic_support=historical_support,
    )
    next_transcription = transcription.model_copy(
        update={
            "words": [changed_words.get(word.word_id, word) for word in transcription.words],
            # An entry cannot precede its verified word start.  Replacing the
            # affected anchors with that start removes stale prior-alignment
            # onsets without inventing a new diarization measurement.
            "subtitle_entry_by_word_id": {
                **{
                    word_id: entry_ms
                    for word_id, entry_ms in transcription.subtitle_entry_by_word_id.items()
                    if word_id not in changed_words
                },
                **{
                    word_id: word.start_ms
                    for word_id, word in changed_words.items()
                    if word.start_ms < word.end_ms
                },
            },
            "asr_vad_source_timing_corrections": [
                *transcription.asr_vad_source_timing_corrections,
                correction_record,
            ],
        }
    )

    next_cues_by_id: dict[str, VideoLocalizationCue] = dict(cues_by_id)
    for correction in request.cue_corrections:
        cue = cues_by_id[correction.cue_id]
        has_zero_width_word = any(
            changed_words[word_id].end_ms == changed_words[word_id].start_ms
            for word_id in correction.source_word_ids
        )
        if correction.confirm_timing:
            evidence = VideoLocalizationAutomaticTimingConfirmationEvidence(
                transcription_revision_id=transcription.revision_id,
                alignment_source_track_id=request.source_track_id,
                alignment_audio_sha256=request.audio_sha256,
                source_word_ids=correction.source_word_ids,
                source_timing_correction_id=correction_record.correction_id,
                vad_interval_ids=correction.vad_interval_ids,
            )
            update = VideoLocalizationCueUpdate(
                start_ms=correction.start_ms,
                end_ms=correction.end_ms,
                confirm_timing=True,
                timing_confirmation_method="asr_vad_verified",
                timing_confirmation_evidence=evidence,
            )
            candidate = VideoLocalizationCue(
                **{**cue.model_dump(), **with_manual_edit_provenance(cue, update.model_dump(exclude_unset=True))}
            )
            next_cues_by_id[cue.cue_id] = candidate.model_copy(
                update={
                    "quality_flags": add_flags(
                        candidate.quality_flags,
                        ["source_timing_zero_width"] if has_zero_width_word else [],
                    )
                }
            )
        else:
            update = VideoLocalizationCueUpdate(
                start_ms=correction.start_ms,
                end_ms=correction.end_ms,
            )
            candidate = VideoLocalizationCue(
                **{**cue.model_dump(), **with_manual_edit_provenance(cue, update.model_dump(exclude_unset=True))}
            )
            candidate = _invalidate_source_timing_confirmation(candidate)
            next_cues_by_id[cue.cue_id] = candidate.model_copy(
                update={
                    "quality_flags": add_flags(
                        candidate.quality_flags,
                        ["source_timing_zero_width"] if has_zero_width_word else [],
                    )
                }
            )

    next_draft = draft.model_copy(
        update={
            "transcription": next_transcription,
            "cues": [next_cues_by_id[cue.cue_id] for cue in draft.cues],
        }
    )
    for correction in request.cue_corrections:
        if correction.confirm_timing:
            validate_automatic_timing_confirmation(
                next_draft, next_cues_by_id[correction.cue_id]
            )
    _assert_cues_do_not_overlap(next_draft.cues)
    return next_draft.model_copy(
        update={
            "cues": [
                _normalize_cue_flags(
                    cue,
                    speaker_assignment_required=draft_requires_speaker_assignment(next_draft),
                )
                for cue in next_draft.cues
            ]
        }
    )


def _invalidate_source_timing_confirmation(cue: VideoLocalizationCue) -> VideoLocalizationCue:
    """Keep the old audit record but prevent a changed source timing from going blue."""

    flags = [
        item for item in cue.quality_flags
        if item not in {"manual_timing_verified", "timing_review_required"}
    ]
    return cue.model_copy(
        update={
            "timing_confidence": "low",
            "manual_timing_revision": cue.manual_timing_revision + 1,
            "manual_timing_review_status": "required",
            "quality_flags": add_flags(
                flags,
                ["protected_manual_edit", "manual_timing_edit", "timing_review_required"],
            ),
        }
    )


def is_replaceable_asr_candidate(cue: VideoLocalizationCue) -> bool:
    return (
        "generated_by_asr" in cue.quality_flags
        and "protected_manual_edit" not in cue.quality_flags
    )


def generated_asr_review_status(flags: list[str]) -> str:
    return (
        "needs_review"
        if GENERATED_ASR_REVIEW_REQUIRED_FLAGS.intersection(flags)
        else "ready"
    )


def without_stale_generated_asr_review_status(
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Migrate the old blanket review flag without touching human-owned cues."""

    changed = False
    normalized: list[VideoLocalizationCue] = []
    for cue in draft.cues:
        if (
            cue.review_status == "needs_review"
            and "generated_by_asr" in cue.quality_flags
            and "protected_manual_edit" not in cue.quality_flags
        ):
            expected = generated_asr_review_status(cue.quality_flags)
            if expected != cue.review_status:
                cue = cue.model_copy(update={"review_status": expected})
                changed = True
        normalized.append(cue)
    return draft.model_copy(update={"cues": normalized}) if changed else draft


def add_flags(flags: list[str], additions: list[str]) -> list[str]:
    next_flags = list(flags)
    for flag in additions:
        if flag not in next_flags:
            next_flags.append(flag)
    return next_flags


def with_manual_edit_provenance(cue: VideoLocalizationCue, update: dict) -> dict:
    """Apply the shared manual-edit audit rules to one cue's changed fields."""
    timing_confirmed = bool(update.get("confirm_timing", False))
    expected_start_ms = update.get("expected_start_ms")
    expected_end_ms = update.get("expected_end_ms")
    confirmation_method = update.get("timing_confirmation_method", "auditioned")
    confirmation_evidence = update.get("timing_confirmation_evidence")
    text_changed = "en_subtitle_text" in update and update["en_subtitle_text"] != cue.en_subtitle_text
    timing_changed = (
        ("start_ms" in update and update["start_ms"] != cue.start_ms)
        or ("end_ms" in update and update["end_ms"] != cue.end_ms)
    )
    next_update = dict(update)
    for action_field in (
        "confirm_timing", "expected_start_ms", "expected_end_ms",
        "timing_confirmation_method", "timing_confirmation_evidence",
    ):
        next_update.pop(action_field, None)
    requested_flags = update.get("quality_flags")
    flags = list(requested_flags if isinstance(requested_flags, list) else cue.quality_flags)
    managed_flags = {"manual_timing_verified", "timing_review_required"}
    flags = [flag for flag in flags if flag not in managed_flags]
    if manual_timing_confirmation_is_current(cue):
        flags.append("manual_timing_verified")
    elif (
        cue.manual_timing_review_status == "required"
        or "timing_review_required" in cue.quality_flags
        or "segment_timing_interpolated" in cue.quality_flags
    ):
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
        if confirmation_method == "asr_vad_verified" and confirmation_evidence is None:
            raise AppException(400, "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_REQUIRED", "自动时间校核需要当前 ASR、VAD 和音轨证据。")
        if confirmation_method == "auditioned" and confirmation_evidence is not None:
            raise AppException(400, "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_INVALID", "人工试听确认不能附带自动校核证据。")
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
                "manual_timing_confirmation_evidence": confirmation_evidence,
            }
        )
    next_update["quality_flags"] = add_flags(flags, additions)
    return next_update


def transcription_requires_speaker_assignment(
    transcription: VideoLocalizationTranscriptionState | None,
) -> bool:
    """Require per-cue speaker ownership only for a usable multi-voice result."""

    return bool(
        transcription is not None
        and transcription.diarization_status in {"completed", "partial"}
        and len(transcription.speaker_clusters) >= 2
    )


def draft_requires_speaker_assignment(
    draft: VideoLocalizationDraft,
) -> bool:
    """Include an explicit business-speaker workflow when one already exists."""

    return bool(draft.speakers) or transcription_requires_speaker_assignment(
        draft.transcription
    )


def _normalize_cue_flags(
    cue: VideoLocalizationCue,
    *,
    speaker_assignment_required: bool,
) -> VideoLocalizationCue:
    removable = {"needs_speaker_assignment", "needs_zh_localization", "segment_timing_missing"}
    flags = [
        flag
        for flag in cue.quality_flags
        if flag not in removable and flag not in {"manual_timing_verified", "timing_review_required"}
    ]

    if manual_timing_confirmation_is_current(cue):
        flags.append("manual_timing_verified")
    elif (
        cue.manual_timing_review_status == "required"
        or "timing_review_required" in cue.quality_flags
        or "segment_timing_interpolated" in cue.quality_flags
    ):
        flags.append("timing_review_required")

    if speaker_assignment_required and not cue.speaker_id:
        flags.append("needs_speaker_assignment")
    if not _localized_tracks_ready(cue):
        flags.append("needs_zh_localization")
    if cue.start_ms is None or cue.end_ms is None:
        flags.append("segment_timing_missing")

    return cue.model_copy(update={"quality_flags": flags})


def with_normalized_quality_flags(
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Restore derived cue flags when loading older persisted drafts."""

    speaker_assignment_required = draft_requires_speaker_assignment(draft)
    normalized = [
        _normalize_cue_flags(
            cue,
            speaker_assignment_required=speaker_assignment_required,
        )
        for cue in draft.cues
    ]
    if normalized == draft.cues:
        return draft
    return draft.model_copy(update={"cues": normalized})


def manual_timing_confirmation_is_current(cue: VideoLocalizationCue) -> bool:
    method = cue.manual_timing_confirmation_method
    evidence = cue.manual_timing_confirmation_evidence
    return (
        cue.manual_timing_review_status == "confirmed"
        and cue.manual_timing_confirmed_revision == cue.manual_timing_revision
        and cue.manual_timing_confirmed_start_ms == cue.start_ms
        and cue.manual_timing_confirmed_end_ms == cue.end_ms
        and (
            (method == "auditioned" and evidence is None)
            or (
                method == "asr_vad_verified"
                and evidence is not None
                and evidence.transcription_revision_id == cue.transcription_revision_id
                and evidence.source_word_ids == cue.source_word_ids
            )
        )
        and cue.manual_timing_confirmed_at is not None
    )


def timing_confirmation_is_current_for_draft(
    draft: VideoLocalizationDraft,
    cue: VideoLocalizationCue,
) -> bool:
    """Check persisted source-timing evidence rather than trusting a blue flag."""

    if not manual_timing_confirmation_is_current(cue):
        return False
    evidence = cue.manual_timing_confirmation_evidence
    if not evidence or not evidence.source_timing_correction_id:
        return True
    transcription = draft.transcription
    if transcription is None:
        return False
    correction = next(
        (
            item
            for item in transcription.asr_vad_source_timing_corrections
            if item.correction_id == evidence.source_timing_correction_id
        ),
        None,
    )
    words_by_id = {word.word_id: word for word in transcription.words}
    words = [words_by_id.get(word_id) for word_id in evidence.source_word_ids]
    corrected_words = {
        item.word_id: item for item in correction.word_timings
    } if correction else {}
    return bool(
        correction
        and correction.transcription_revision_id == transcription.revision_id
        and correction.source_track_id == evidence.alignment_source_track_id
        and correction.audio_sha256 == evidence.alignment_audio_sha256
        and set(evidence.source_word_ids).issubset(correction.word_ids)
        and set(evidence.vad_interval_ids).issubset(
            {item.interval_id for item in correction.vad_intervals}
        )
        and all(
            word
            and (snapshot := corrected_words.get(word.word_id)) is not None
            and snapshot.text == word.text
            and snapshot.start_ms == word.start_ms
            and snapshot.end_ms == word.end_ms
            for word in words
        )
        and cue.start_ms is not None
        and cue.end_ms is not None
        and cue.start_ms >= correction.analysis_start_ms
        and cue.end_ms <= correction.analysis_end_ms
        and bool(words)
        and words[0] is not None
        and words[-1] is not None
        and words[0].end_ms > words[0].start_ms
        and words[-1].end_ms > words[-1].start_ms
        and all(
            word
            and (
                word.end_ms == word.start_ms
                or (
                    word.timing_source == "asr_vad_verified"
                    and word.timing_confidence != "low"
                    and word.end_ms > word.start_ms
                )
            )
            for word in words
        )
    )


def validate_automatic_timing_confirmation(
    draft: VideoLocalizationDraft,
    cue: VideoLocalizationCue,
) -> None:
    """Require exact current ASR/VAD evidence before a cue can turn verified."""

    evidence: VideoLocalizationAutomaticTimingConfirmationEvidence | None = (
        cue.manual_timing_confirmation_evidence
    )
    transcription = draft.transcription
    if (
        cue.manual_timing_confirmation_method != "asr_vad_verified"
        or evidence is None
        or transcription is None
        or transcription.revision_id != evidence.transcription_revision_id
        or cue.transcription_revision_id != transcription.revision_id
        or (
            transcription.alignment_status != "completed"
            and not evidence.source_timing_correction_id
        )
        or transcription.audio_boundary_status != "completed"
        or transcription.alignment_source_track_id != evidence.alignment_source_track_id
        or transcription.alignment_audio_sha256 != evidence.alignment_audio_sha256
        or list(cue.source_word_ids) != list(evidence.source_word_ids)
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_STALE",
            "自动校核证据不属于当前分离人声听写、音轨或字幕范围，请重新核对。",
        )
    words_by_id = {word.word_id: word for word in transcription.words}
    words = [words_by_id.get(word_id) for word_id in evidence.source_word_ids]
    if (
        not all(words)
        or (
            not evidence.source_timing_correction_id
            and any(word.timing_source != "forced_aligner" for word in words)
        )
        or cue.start_ms is None
        or cue.end_ms is None
        or cue.start_ms > words[0].start_ms
        or cue.end_ms < words[-1].end_ms
    ):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_INVALID",
            "自动校核必须覆盖当前 cue 的全部逐词对齐时间。",
        )
    if evidence.source_timing_correction_id:
        correction = next(
            (
                item
                for item in transcription.asr_vad_source_timing_corrections
                if item.correction_id == evidence.source_timing_correction_id
            ),
            None,
        )
        interval_ids = {item.interval_id for item in correction.vad_intervals} if correction else set()
        corrected_words = {
            item.word_id: item for item in correction.word_timings
        } if correction else {}
        if (
            correction is None
            or correction.transcription_revision_id != transcription.revision_id
            or correction.source_track_id != evidence.alignment_source_track_id
            or correction.audio_sha256 != evidence.alignment_audio_sha256
            or not set(evidence.source_word_ids).issubset(correction.word_ids)
            or not set(evidence.vad_interval_ids).issubset(interval_ids)
            or any(
                (snapshot := corrected_words.get(word.word_id)) is None
                or snapshot.text != word.text
                or snapshot.start_ms != word.start_ms
                or snapshot.end_ms != word.end_ms
                for word in words
            )
            or cue.start_ms < correction.analysis_start_ms
            or cue.end_ms > correction.analysis_end_ms
            or not words
            or words[0].end_ms <= words[0].start_ms
            or words[-1].end_ms <= words[-1].start_ms
            or any(
                word.end_ms > word.start_ms
                and (
                    word.timing_source != "asr_vad_verified"
                    or word.timing_confidence == "low"
                )
                for word in words
            )
        ):
            raise AppException(
                422,
                "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_INVALID",
                "自动校核需要当前范围内的可靠 ASR/VAD 逐词时间和原始 VAD 引用。",
            )
        return
    boundaries = {
        item.boundary_id: item
        for item in transcription.audio_boundary_features
    }
    selected_words = set(evidence.source_word_ids)
    referenced = [boundaries.get(boundary_id) for boundary_id in evidence.vad_boundary_ids]
    if (
        not all(referenced)
        or any(
            item.left_word_id not in selected_words
            and item.right_word_id not in selected_words
            for item in referenced
        )
    ):
        raise AppException(
            422,
            "VIDEO_LOCALIZATION_CUE_TIMING_EVIDENCE_INVALID",
            "自动校核引用的 VAD 边界不属于当前 cue 的逐词范围。",
        )


def sanitize_client_draft_timing_provenance(
    current: VideoLocalizationDraft | None,
    incoming: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Keep timing-confirmation audit fields under backend ownership."""
    current_by_id = {cue.cue_id: cue for cue in (current.cues if current else [])}
    speaker_assignment_required = draft_requires_speaker_assignment(incoming)
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
                            "manual_timing_confirmation_evidence": None,
                            "quality_flags": flags,
                        }
                    ),
                    speaker_assignment_required=speaker_assignment_required,
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
            "manual_timing_confirmation_evidence": previous.manual_timing_confirmation_evidence,
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
        sanitized.append(
            _normalize_cue_flags(
                cue.model_copy(
                    update={**audit, "quality_flags": flags}
                ),
                speaker_assignment_required=speaker_assignment_required,
            )
        )
    return incoming.model_copy(update={"cues": sanitized})


def _localized_tracks_ready(cue: VideoLocalizationCue) -> bool:
    zh_text = (cue.zh_localized_subtitle_text or "").strip()
    tts_text = (cue.tts_recommended_text or "").strip()
    if not zh_text or not tts_text:
        return False
    return not zh_text.startswith("【待本土化】") and not tts_text.startswith("【待本土化】")


def _assert_updated_cue_does_not_overlap(cues: list[VideoLocalizationCue], updated_cue_id: str) -> None:
    _assert_cues_do_not_overlap(cues, updated_cue_id=updated_cue_id)


def _assert_cues_do_not_overlap(
    cues: list[VideoLocalizationCue],
    *,
    updated_cue_id: str | None = None,
) -> None:
    timed = sorted(
        (cue for cue in cues if cue.start_ms is not None and cue.end_ms is not None),
        key=lambda cue: (cue.start_ms or 0, cue.end_ms or 0, cue.cue_id),
    )
    for previous, current in zip(timed, timed[1:]):
        if current.start_ms is None or previous.end_ms is None or current.start_ms >= previous.end_ms:
            continue
        if updated_cue_id is not None and updated_cue_id not in {previous.cue_id, current.cue_id}:
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
