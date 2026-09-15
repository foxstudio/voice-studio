from __future__ import annotations

from app.domains.video_localization.asr_uncertainty import project_asr_uncertainty

import math
import re
from dataclasses import dataclass

from app.domains.video_localization import cues as cue_tools
from app.domains.video_localization import subtitle_exit_timing
from app.domains.video_localization import subtitle_punctuation
from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
    VideoLocalizationBoundaryReview,
    VideoLocalizationCue,
    VideoLocalizationTranscriptionState,
)


MAX_WORDS = 18
TARGET_WORDS = 8
MAX_DURATION_MS = 7200
MIN_DURATION_MS = 450
MAX_SOURCE_CHARS = 52
HARD_AVOID_REVIEW_CONFIDENCE = 0.8
MAX_WORD_OVERFLOW = 8
MAX_DURATION_OVERFLOW_MS = 2400
ASR_CUE_ENTRY_LEAD_IN_MS = 80
TERMINAL_PUNCTUATION = re.compile(r"[.!?。！？][\"'”’)]*$")
CLAUSE_PUNCTUATION = re.compile(
    r"(?:[,;:，；：]|[—–－―]+)[\"'”’)]*$"
)
BAD_BREAK_ENDINGS = {
    "a",
    "an",
    "the",
    "to",
    "of",
    "for",
    "with",
    "from",
    "at",
    "in",
    "on",
    "by",
    "and",
    "or",
    "but",
    "if",
    "because",
    "although",
    "though",
    "not",
    "no",
    "never",
    "without",
    "am",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "can",
    "could",
    "will",
    "would",
    "shall",
    "should",
    "may",
    "might",
    "must",
    "this",
    "that",
    "these",
    "those",
    "my",
    "your",
    "our",
    "their",
    "his",
    "her",
    "its",
    "each",
    "every",
    "any",
    "some",
    "whole",
    "entire",
    "same",
}
INCOMPLETE_EXISTENTIAL_OPENERS = {
    "there's",
    "there're",
    "here's",
    "here're",
}
BAD_BREAK_STARTINGS = {
    "as",
    "at",
    "because",
    "by",
    "for",
    "from",
    "if",
    "in",
    "of",
    "on",
    "than",
    "that",
    "to",
    "unless",
    "until",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "whose",
    "with",
    "without",
}
NON_TERMINAL_ABBREVIATIONS = {
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "prof.",
    "sr.",
    "jr.",
    "st.",
    "vs.",
    "etc.",
    "e.g.",
    "i.e.",
    "u.s.",
    "u.k.",
    "no.",
}


@dataclass(frozen=True)
class SubtitleSegmentationProfile:
    profile_id: str
    max_words: int
    target_words: int
    max_duration_ms: int
    min_duration_ms: int
    max_source_chars: int
    target_duration_ms: int
    candidate_audio_pause_ms: int
    strong_audio_pause_ms: int


DEFAULT_PROFILE_ID = "generic_zh"
PROFILES = {
    "generic_zh": SubtitleSegmentationProfile("generic_zh", 18, 8, 7200, 450, 52, 3200, 180, 280),
    "short_video_large_text": SubtitleSegmentationProfile(
        "short_video_large_text", 12, 6, 6000, 700, 36, 2600, 160, 240
    ),
    "conservative_release": SubtitleSegmentationProfile("conservative_release", 16, 8, 7000, 830, 42, 3200, 220, 320),
}


def resolve_profile(profile_id: str | None) -> SubtitleSegmentationProfile:
    resolved = profile_id or DEFAULT_PROFILE_ID
    if resolved not in PROFILES:
        raise ValueError(f"Unknown subtitle segmentation profile: {resolved}")
    return PROFILES[resolved]


def cues_from_transcription(
    transcription: VideoLocalizationTranscriptionState,
    *,
    existing_cue_ids: set[str],
    profile_id: str | None = None,
    frame_rate: float | None = None,
    media_duration_ms: int | None = None,
) -> list[VideoLocalizationCue]:
    words = [word for word in transcription.words if word.text.strip() and word.end_ms > word.start_ms]
    if not words:
        return []
    profile = resolve_profile(profile_id or transcription.segmentation_profile_id)
    audio_boundaries = {(item.left_word_id, item.right_word_id): item for item in transcription.audio_boundary_features}
    audio_analysis_available = transcription.audio_boundary_status == "completed"
    boundary_reviews = {(item.left_word_id, item.right_word_id): item for item in transcription.boundary_reviews}
    uncertainty = project_asr_uncertainty(transcription)
    boundaries = _optimal_boundaries(
        words,
        profile,
        audio_boundaries=audio_boundaries,
        audio_analysis_available=audio_analysis_available,
        boundary_reviews=boundary_reviews,
    )
    segment_by_id = {segment.segment_id: segment for segment in transcription.segments}
    terminal_review_segment_ids = {
        segment.segment_id
        for segment in transcription.segments
        if "terminal_fragment_review_required" in segment.review_flags
    }
    forced_boundaries = {
        index
        for index, word in enumerate(words)
        if index > 0
        and word.segment_id in terminal_review_segment_ids
        and words[index - 1].segment_id != word.segment_id
    }
    boundaries = sorted({*boundaries, *forced_boundaries, len(words)})
    cues: list[VideoLocalizationCue] = []
    speaker_assignment_required = (
        cue_tools.transcription_requires_speaker_assignment(transcription)
    )
    start_index = 0
    for cue_index, end_index in enumerate(boundaries, start=1):
        selected = words[start_index:end_index]
        if not selected:
            continue
        cue_id = _next_cue_id(existing_cue_ids, cue_index)
        text = _join_words(selected)
        raw_segment_ids = {word.segment_id for word in selected}
        speaker_cluster_ids = {
            word.speaker_cluster_id for word in selected if word.speaker_cluster_id
        }
        speaker_cluster_id = next(iter(speaker_cluster_ids)) if len(speaker_cluster_ids) == 1 else None
        raw_text = " ".join(
            segment.raw_text for segment in transcription.segments if segment.segment_id in raw_segment_ids
        ).strip()
        flags = [
            "generated_by_asr",
            f"engine:{transcription.engine_id}",
            f"timing:{_cue_timing_confidence(selected)}",
            f"segmentation:{profile.profile_id}",
            "needs_zh_localization",
        ]
        if speaker_cluster_id:
            flags.append(f"speaker-cluster:{speaker_cluster_id}")
        elif speaker_assignment_required:
            flags.append("needs_speaker_assignment")
        if any(word.has_speaker_overlap for word in selected):
            flags.extend(["speaker_overlap_detected", "speaker_review_required"])
        if transcription.review_status in {"completed", "partial"}:
            flags.append("llm_transcript_reviewed")
        flags = uncertainty.cue_flags(flags, [word.word_id for word in selected])
        if audio_analysis_available:
            flags.append(f"boundary-analysis:{transcription.audio_boundary_analysis_version or 'energy-pause-v1'}")
            if end_index < len(words):
                boundary_evidence = audio_boundaries.get((selected[-1].word_id, words[end_index].word_id))
                if boundary_evidence and boundary_evidence.confidence != "none":
                    flags.append(f"boundary:audio-pause-{boundary_evidence.confidence}")
                semantic_review = boundary_reviews.get((selected[-1].word_id, words[end_index].word_id))
                if semantic_review:
                    flags.append(f"boundary-review:{semantic_review.decision}")
        if any(word.timing_confidence == "low" for word in selected):
            flags.extend(["timing_review_required", "segment_timing_interpolated"])
        selected_segment_flags = {
            flag
            for segment_id in raw_segment_ids
            for flag in (segment_by_id.get(segment_id).review_flags if segment_by_id.get(segment_id) else [])
            if flag
            in {
                "media_end_clipped",
                "terminal_fragment_review_required",
            }
        }
        flags.extend(selected_segment_flags)
        display_text, _ = _normalize_subtitle_punctuation(text)
        if _segment_exceeds_profile(selected, profile, display_text=display_text):
            flags.append("segmentation_review_required")
        cues.append(
            VideoLocalizationCue(
                cue_id=cue_id,
                speaker_cluster_id=speaker_cluster_id,
                start_ms=selected[0].start_ms,
                end_ms=selected[-1].end_ms,
                en_subtitle_text=text,
                source_duration_ms=max(0, selected[-1].end_ms - selected[0].start_ms),
                source_word_ids=[word.word_id for word in selected],
                source_text_raw=raw_text or text,
                timing_confidence=_cue_timing_confidence(selected),
                transcription_revision_id=transcription.revision_id,
                review_status=cue_tools.generated_asr_review_status(flags),
                quality_flags=sorted(set(flags)),
            )
        )
        start_index = end_index
    subtitle_entries = getattr(
        transcription,
        "subtitle_entry_by_word_id",
        {},
    )
    subtitle_entries = (
        subtitle_entries if isinstance(subtitle_entries, dict) else {}
    )
    return postprocess_generated_cues(
        cues,
        subtitle_entry_by_word_id=subtitle_entries,
        frame_rate=frame_rate,
        media_duration_ms=media_duration_ms,
    )


def postprocess_generated_cues(
    cues: list[VideoLocalizationCue],
    *,
    subtitle_entry_by_word_id: dict[str, int] | None = None,
    frame_rate: float | None = None,
    media_duration_ms: int | None = None,
) -> list[VideoLocalizationCue]:
    """Apply conservative timing and punctuation cleanup without changing cue order."""

    refined: list[VideoLocalizationCue] = []
    entry_by_word = subtitle_entry_by_word_id or {}
    for cue in cues:
        if cue.start_ms is None or cue.end_ms is None:
            refined.append(cue)
            continue
        start_ms = cue.start_ms
        flags = list(cue.quality_flags)
        first_word_id = cue.source_word_ids[0] if cue.source_word_ids else None
        detected_entry_ms = (
            entry_by_word.get(first_word_id)
            if first_word_id
            else None
        )
        if (
            isinstance(detected_entry_ms, int)
            and start_ms < detected_entry_ms < cue.end_ms
        ):
            start_ms = detected_entry_ms
            flags.append("timing:acoustic-entry-refined")
        if first_word_id:
            entry_lead_in_floor_ms = (
                int(refined[-1].end_ms or 0)
                if refined and refined[-1].end_ms is not None
                else 0
            )
            entry_anchor_ms = (
                detected_entry_ms
                if isinstance(detected_entry_ms, int)
                else int(cue.start_ms)
            )
            start_ms = max(
                entry_lead_in_floor_ms,
                start_ms - ASR_CUE_ENTRY_LEAD_IN_MS,
            )
            if start_ms < entry_anchor_ms:
                flags.append("timing:entry-lead-in")
        text, punctuation_changed = _normalize_subtitle_punctuation(cue.en_subtitle_text or "")
        if punctuation_changed:
            flags.append("punctuation:minimal-style-normalized")
        refined.append(
            cue.model_copy(
                update={
                    "start_ms": start_ms,
                    "source_duration_ms": max(0, cue.end_ms - start_ms),
                    "en_subtitle_text": text,
                    "quality_flags": sorted(set(flags)),
                }
            )
        )

    timed_indices = [
        index
        for index, cue in enumerate(refined)
        if cue.start_ms is not None
        and cue.end_ms is not None
        and cue.end_ms > cue.start_ms
    ]
    resolved_spans = subtitle_exit_timing.resolve_display_exit_times(
        [
            subtitle_exit_timing.SubtitleTimingSpan(
                start_ms=int(refined[index].start_ms or 0),
                end_ms=int(refined[index].end_ms or 0),
            )
            for index in timed_indices
        ],
        frame_rate=frame_rate,
        media_duration_ms=media_duration_ms,
    )
    output = list(refined)
    for index, span in zip(timed_indices, resolved_spans):
        cue = output[index]
        if span.end_ms == cue.end_ms:
            continue
        output[index] = cue.model_copy(
            update={
                "end_ms": span.end_ms,
                "quality_flags": sorted(
                    set(
                        [
                            *cue.quality_flags,
                            "timing:display-exit-extended",
                        ]
                    )
                ),
            }
        )
    return output


def _normalize_subtitle_punctuation(text: str) -> tuple[str, bool]:
    """Apply the shared display-subtitle punctuation policy."""

    normalized = text.strip()
    cleaned = (
        subtitle_punctuation
        .normalize_display_subtitle_punctuation(normalized)
    )
    return cleaned, cleaned != normalized


def _optimal_boundaries(
    words: list[VideoLocalizationAlignedWord],
    profile: SubtitleSegmentationProfile | None = None,
    *,
    audio_boundaries: dict[tuple[str, str], VideoLocalizationAudioBoundaryEvidence] | None = None,
    audio_analysis_available: bool = False,
    boundary_reviews: dict[tuple[str, str], VideoLocalizationBoundaryReview] | None = None,
) -> list[int]:
    profile = profile or PROFILES[DEFAULT_PROFILE_ID]
    relaxed_boundaries = _dynamic_boundaries(
        words,
        profile,
        audio_boundaries=audio_boundaries,
        audio_analysis_available=audio_analysis_available,
        boundary_reviews=boundary_reviews,
        enforce_profile_limits=False,
    )
    boundaries = relaxed_boundaries or _fallback_boundaries(
        words,
        profile,
        boundary_reviews,
    )
    return _repair_overlimit_windows(
        words,
        boundaries,
        profile,
        audio_boundaries=audio_boundaries,
        audio_analysis_available=audio_analysis_available,
        boundary_reviews=boundary_reviews,
    )


def _dynamic_boundaries(
    words: list[VideoLocalizationAlignedWord],
    profile: SubtitleSegmentationProfile,
    *,
    audio_boundaries: dict[tuple[str, str], VideoLocalizationAudioBoundaryEvidence] | None,
    audio_analysis_available: bool,
    boundary_reviews: dict[tuple[str, str], VideoLocalizationBoundaryReview] | None,
    enforce_profile_limits: bool,
) -> list[int] | None:
    count = len(words)
    search_max_words = profile.max_words + (0 if enforce_profile_limits else MAX_WORD_OVERFLOW)
    search_max_duration_ms = profile.max_duration_ms + (
        0 if enforce_profile_limits else MAX_DURATION_OVERFLOW_MS
    )
    costs = [math.inf] * (count + 1)
    previous = [-1] * (count + 1)
    costs[0] = 0.0

    for end in range(1, count + 1):
        speaker_change = end < count and _speaker_changes_at(words, end)
        if end < count and not speaker_change and _boundary_splits_atomic_token(words, end):
            continue
        if end < count and not speaker_change and _boundary_forbidden_by_review(words, end, boundary_reviews):
            continue
        for start in range(max(0, end - search_max_words), end):
            if not math.isfinite(costs[start]):
                continue
            segment = words[start:end]
            if _segment_crosses_speakers(segment):
                continue
            duration_ms = segment[-1].end_ms - segment[0].start_ms
            if (
                duration_ms > search_max_duration_ms
                and len(segment) > 1
                and not _allows_semantically_atomic_long_segment(
                    words,
                    start=start,
                    end=end,
                    profile=profile,
                )
            ):
                continue
            if (
                enforce_profile_limits
                and len(segment) > 1
                and _segment_exceeds_profile(segment, profile)
            ):
                continue
            score = costs[start] + _segment_cost(
                words,
                start,
                end,
                profile,
                audio_boundaries=audio_boundaries,
                audio_analysis_available=audio_analysis_available,
                boundary_reviews=boundary_reviews,
            )
            if enforce_profile_limits:
                score += _strict_fragment_cost(segment, has_following=end < count)
            if score < costs[end]:
                costs[end] = score
                previous[end] = start

    if previous[count] < 0:
        return None
    boundaries: list[int] = []
    cursor = count
    while cursor > 0:
        boundaries.append(cursor)
        cursor = previous[cursor]
    return list(reversed(boundaries))


def _allows_semantically_atomic_long_segment(
    words: list[VideoLocalizationAlignedWord],
    *,
    start: int,
    end: int,
    profile: SubtitleSegmentationProfile,
) -> bool:
    """Prefer one flagged long cue over breaking a required phrase in half."""

    selected = words[start:end]
    if (
        not selected
        or len(selected) > profile.max_words
        or len(_join_words(selected)) > profile.max_source_chars
        or not TERMINAL_PUNCTUATION.search(selected[-1].text)
    ):
        return False
    return any(
        _boundary_splits_atomic_token(words, boundary)
        for boundary in range(start + 1, end)
    )


def _segment_exceeds_profile(
    words: list[VideoLocalizationAlignedWord],
    profile: SubtitleSegmentationProfile,
    *,
    display_text: str | None = None,
) -> bool:
    if not words:
        return False
    if display_text is None:
        display_text, _ = _normalize_subtitle_punctuation(_join_words(words))
    return (
        len(words) > profile.max_words
        or words[-1].end_ms - words[0].start_ms > profile.max_duration_ms
        or len(display_text) > profile.max_source_chars
    )


def _strict_fragment_cost(
    words: list[VideoLocalizationAlignedWord],
    *,
    has_following: bool,
) -> float:
    if not words or not has_following:
        return 0.0
    has_terminal_punctuation = bool(TERMINAL_PUNCTUATION.search(words[-1].text))
    cost = 3.0 if len(words) <= 2 and not has_terminal_punctuation else 0.0
    if (
        len(words) >= 2
        and words[-2].text.casefold().strip(".,!?;:，。！？；：\"'“”‘’()[]{}")
        in {"a", "an", "the"}
        and words[-1].text.casefold().strip(".,!?;:，。！？；：\"'“”‘’()[]{}")
        .endswith(("ed", "ing"))
        and not has_terminal_punctuation
    ):
        cost += 8.0
    return cost


def _repair_overlimit_windows(
    words: list[VideoLocalizationAlignedWord],
    boundaries: list[int],
    profile: SubtitleSegmentationProfile,
    *,
    audio_boundaries: dict[tuple[str, str], VideoLocalizationAudioBoundaryEvidence] | None,
    audio_analysis_available: bool,
    boundary_reviews: dict[tuple[str, str], VideoLocalizationBoundaryReview] | None,
) -> list[int]:
    """Repartition only a failing cue and one neighbour under strict limits."""

    repaired = list(boundaries)
    index = 0
    while index < len(repaired):
        start = repaired[index - 1] if index else 0
        end = repaired[index]
        if not _segment_exceeds_profile(words[start:end], profile):
            index += 1
            continue

        window_left = index
        window_right = index
        if index + 1 < len(repaired):
            window_right += 1
        elif index > 0:
            window_left -= 1
        window_start = repaired[window_left - 1] if window_left else 0
        window_end = repaired[window_right]
        local_boundaries = _dynamic_boundaries(
            words[window_start:window_end],
            profile,
            audio_boundaries=audio_boundaries,
            audio_analysis_available=audio_analysis_available,
            boundary_reviews=boundary_reviews,
            enforce_profile_limits=True,
        )
        if local_boundaries is None:
            index += 1
            continue

        absolute_boundaries = [
            window_start + boundary
            for boundary in local_boundaries
        ]
        existing_window = repaired[window_left : window_right + 1]
        if absolute_boundaries == existing_window:
            index += 1
            continue
        repaired[window_left : window_right + 1] = absolute_boundaries
        index = max(0, window_left - 1)
    return repaired


def _fallback_boundaries(
    words: list[VideoLocalizationAlignedWord],
    profile: SubtitleSegmentationProfile,
    boundary_reviews: dict[tuple[str, str], VideoLocalizationBoundaryReview] | None,
) -> list[int]:
    """Relax cue size without ever violating an atomic or hard semantic boundary."""
    count = len(words)
    valid = [
        end
        for end in range(1, count)
        if (
            _speaker_changes_at(words, end)
            or (
                not _boundary_splits_atomic_token(words, end)
                and not _boundary_forbidden_by_review(words, end, boundary_reviews)
            )
        )
    ]
    valid_set = {*valid, count}
    boundaries: list[int] = []
    start = 0
    while _segment_exceeds_profile(words[start:], profile):
        target = start + profile.max_words
        feasible = [
            end
            for end in range(
                start + 1,
                min(count, target + MAX_WORD_OVERFLOW) + 1,
            )
            if (
                end in valid_set
                and not _segment_exceeds_profile(words[start:end], profile)
            )
        ]
        before = [end for end in feasible if end <= target]
        after = [end for end in feasible if end > target]
        if before:
            selected = before[-1]
        elif after:
            selected = after[0]
        else:
            relaxed = [
                end
                for end in range(
                    start + 1,
                    min(count, target + MAX_WORD_OVERFLOW) + 1,
                )
                if end in valid_set
            ]
            if not relaxed:
                break
            terminal = [
                end
                for end in relaxed
                if TERMINAL_PUNCTUATION.search(words[end - 1].text)
            ]
            clause = [
                end
                for end in relaxed
                if CLAUSE_PUNCTUATION.search(words[end - 1].text)
            ]
            selected = min(
                terminal or clause or relaxed,
                key=lambda end: _segment_cost(
                    words,
                    start,
                    end,
                    profile,
                    boundary_reviews=boundary_reviews,
                ),
            )
        boundaries.append(selected)
        start = selected
    if not boundaries or boundaries[-1] != count:
        boundaries.append(count)
    return boundaries


def _speaker_changes_at(words: list[VideoLocalizationAlignedWord], end: int) -> bool:
    if end <= 0 or end >= len(words):
        return False
    left = words[end - 1].speaker_cluster_id
    right = words[end].speaker_cluster_id
    return bool(left and right and left != right)


def _segment_crosses_speakers(words: list[VideoLocalizationAlignedWord]) -> bool:
    return len({word.speaker_cluster_id for word in words if word.speaker_cluster_id}) > 1


def _segment_cost(
    words: list[VideoLocalizationAlignedWord],
    start: int,
    end: int,
    profile: SubtitleSegmentationProfile | None = None,
    *,
    audio_boundaries: dict[tuple[str, str], VideoLocalizationAudioBoundaryEvidence] | None = None,
    audio_analysis_available: bool = False,
    boundary_reviews: dict[tuple[str, str], VideoLocalizationBoundaryReview] | None = None,
) -> float:
    profile = profile or PROFILES[DEFAULT_PROFILE_ID]
    selected = words[start:end]
    word_count = len(selected)
    duration_ms = max(1, selected[-1].end_ms - selected[0].start_ms)
    text = _join_words(selected)
    cost = abs(word_count - profile.target_words) * 0.65 + abs(duration_ms - profile.target_duration_ms) / 1400
    if word_count > profile.max_words:
        cost += (word_count - profile.max_words) * 18
    if duration_ms > profile.max_duration_ms:
        cost += (duration_ms - profile.max_duration_ms) / 120
    if duration_ms < profile.min_duration_ms:
        cost += 12
    if len(text) > profile.max_source_chars:
        cost += (len(text) - profile.max_source_chars) * 1.8
    if selected[-1].text.casefold().strip(".,!?;:，。！？；：\"'”)") in BAD_BREAK_ENDINGS:
        cost += 10
    if end < len(words):
        has_terminal_punctuation = bool(TERMINAL_PUNCTUATION.search(selected[-1].text))
        right_token = words[end].text.casefold().strip(".,!?;:，。！？；：\"'“”‘’()[]{}")
        same_source_segment = selected[-1].segment_id == words[end].segment_id
        if (
            same_source_segment
            and not TERMINAL_PUNCTUATION.search(selected[-1].text)
            and right_token in BAD_BREAK_STARTINGS
        ):
            cost += 18
        gap_ms = max(0, words[end].start_ms - selected[-1].end_ms)
        if has_terminal_punctuation:
            cost -= 9
        elif CLAUSE_PUNCTUATION.search(selected[-1].text):
            cost -= 4
        boundary_evidence = (audio_boundaries or {}).get((selected[-1].word_id, words[end].word_id))
        if boundary_evidence is not None:
            cost += _audio_boundary_cost(boundary_evidence, profile)
        elif audio_analysis_available:
            # A long aligner gap remains useful, but absence of a matching
            # low-energy pause keeps it weaker than confirmed audio evidence.
            if gap_ms >= 700:
                cost -= 2
            elif gap_ms >= 350:
                cost -= 0.75
        elif gap_ms >= 700:
            cost -= 8
        elif gap_ms >= 350:
            cost -= 4
        elif gap_ms >= 180:
            cost -= 1.5
        if selected[-1].segment_id != words[end].segment_id:
            cost -= 2
        semantic_review = (boundary_reviews or {}).get((selected[-1].word_id, words[end].word_id))
        if semantic_review is not None:
            cost += _semantic_boundary_cost(semantic_review)
    return cost


def _audio_boundary_cost(
    evidence: VideoLocalizationAudioBoundaryEvidence,
    profile: SubtitleSegmentationProfile,
) -> float:
    if (
        evidence.confidence == "high"
        and evidence.low_energy_ms >= profile.strong_audio_pause_ms
        and evidence.low_energy_ratio >= 0.65
        and evidence.energy_drop_db >= 6.0
    ):
        return -8.0
    if (
        evidence.confidence in {"medium", "high"}
        and evidence.low_energy_ms >= profile.candidate_audio_pause_ms
        and evidence.low_energy_ratio >= 0.45
        and evidence.energy_drop_db >= 3.0
    ):
        return -4.5
    if evidence.confidence == "low":
        return -1.5
    if evidence.gap_ms >= 700:
        return -2.0
    if evidence.gap_ms >= 350:
        return -0.75
    return 0.0


def _semantic_boundary_cost(review: VideoLocalizationBoundaryReview) -> float:
    if review.decision == "avoid":
        return 32.0 * review.confidence
    if review.decision == "prefer":
        return -6.0 * review.confidence
    return 0.0


def _boundary_forbidden_by_review(
    words: list[VideoLocalizationAlignedWord],
    end: int,
    reviews: dict[tuple[str, str], VideoLocalizationBoundaryReview] | None,
) -> bool:
    if not reviews or end <= 0 or end >= len(words):
        return False
    if TERMINAL_PUNCTUATION.search(words[end - 1].text):
        return False
    review = reviews.get((words[end - 1].word_id, words[end].word_id))
    return bool(review and review.decision == "avoid" and review.confidence >= HARD_AVOID_REVIEW_CONFIDENCE)


def _boundary_splits_atomic_token(words: list[VideoLocalizationAlignedWord], end: int) -> bool:
    if end <= 0 or end >= len(words):
        return False
    left = words[end - 1].text.strip()
    right = words[end].text.strip()
    previous = words[end - 2].text.strip() if end >= 2 else ""
    normalized_left = left.casefold().replace("’", "'").strip(
        ".,!?;:，。！？；：\"“”()[]{}"
    )
    if normalized_left in INCOMPLETE_EXISTENTIAL_OPENERS:
        return True
    if right and right[0].isdigit():
        if re.search(r"\d\.$", left) or (left == "." and previous and previous[-1].isdigit()):
            return True
    joined_left = _join_words(words[max(0, end - 4) : end]).casefold()
    if any(joined_left.endswith(abbreviation) for abbreviation in NON_TERMINAL_ABBREVIATIONS):
        return True
    return bool(re.search(r"(?:\b[A-Za-z]\.){2,}$", joined_left) and re.match(r"^[A-Za-z]", right))


def _join_words(words: list[VideoLocalizationAlignedWord]) -> str:
    result = ""
    for word in words:
        token = word.text.strip()
        if not token:
            continue
        if not result:
            result = token
        elif token[0].isdigit() and re.search(r"\d[.,]$", result):
            result += token
        elif re.fullmatch(r"[\u3400-\u9fff].*", token) or re.fullmatch(r"[^\w\u3400-\u9fff].*", token):
            result += token
        elif token[0] in ".,!?;:，。！？；：)]}”’":
            result += token
        else:
            result += " " + token
    return result.strip()


def _cue_timing_confidence(words: list[VideoLocalizationAlignedWord]) -> str:
    levels = {word.timing_confidence for word in words}
    if levels == {"high"}:
        return "high"
    if "high" in levels or "medium" in levels:
        return "medium"
    return "low"


def _next_cue_id(existing: set[str], index: int) -> str:
    candidate_index = index
    while True:
        candidate = f"cue_{candidate_index:04d}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate
        candidate_index += 1
