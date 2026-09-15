from __future__ import annotations

import time
from typing import Any, Callable

from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
    VideoLocalizationBoundaryReview,
)
from app.errors import AppException


PROMPT_VERSION = "boundary-review-local-v1"


def review_candidate_boundaries(
    words: list[VideoLocalizationAlignedWord],
    audio_features: list[VideoLocalizationAudioBoundaryEvidence],
    *,
    language: str,
    segmentation_profile_id: str = "generic_zh",
    audio_analysis_available: bool | None = None,
    profile_id: str | None = None,
    existing_reviews: list[VideoLocalizationBoundaryReview] | None = None,
    progress_callback: Callable[[float, int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[list[VideoLocalizationBoundaryReview], dict[str, Any]]:
    """Describe the deterministic subtitle split without asking an LLM to redo it.

    Transcript wording is reviewed as a whole before forced alignment. Subtitle
    geometry then belongs to the local optimizer, which has exact word timing,
    punctuation, speaker changes, acoustic pauses, and display constraints. Old
    persisted semantic decisions are intentionally discarded so they cannot
    keep changing a newly reviewed transcript.
    """

    del language, profile_id, existing_reviews, progress_callback
    started_at = time.perf_counter()
    _ensure_active(is_cancelled)
    analysis_available = bool(audio_features) if audio_analysis_available is None else audio_analysis_available
    boundary_pairs = _baseline_boundary_pairs(
        words,
        audio_features,
        profile_id=segmentation_profile_id,
        audio_analysis_available=analysis_available,
    )
    _ensure_active(is_cancelled)
    return [], {
        "status": "completed",
        "candidate_count": len(boundary_pairs),
        "review_count": 0,
        "reused_review_count": 0,
        "deterministic_boundary_count": len(boundary_pairs),
        "review_round_count": 0,
        "review_batch_count": 0,
        "review_duration_ms": _elapsed_ms(started_at),
        "rounds": [],
        "stop_reason": "local_segmentation",
        "unresolved_candidate_count": 0,
        "profile_id": None,
        "model_id": None,
        "prompt_version": PROMPT_VERSION,
        "segmentation_profile_id": segmentation_profile_id,
        "error": None,
        "quality_flags": ["boundary_review_local_rules"],
    }


def _baseline_boundary_pairs(
    words: list[VideoLocalizationAlignedWord],
    audio_features: list[VideoLocalizationAudioBoundaryEvidence],
    reviews: list[VideoLocalizationBoundaryReview] | None = None,
    profile_id: str = "generic_zh",
    audio_analysis_available: bool | None = None,
) -> set[tuple[str, str]]:
    from app.domains.video_localization import subtitle_segmentation

    audio_by_pair = {(item.left_word_id, item.right_word_id): item for item in audio_features}
    boundaries = subtitle_segmentation._optimal_boundaries(
        words,
        subtitle_segmentation.resolve_profile(profile_id),
        audio_boundaries=audio_by_pair,
        audio_analysis_available=bool(audio_features) if audio_analysis_available is None else audio_analysis_available,
        boundary_reviews={(item.left_word_id, item.right_word_id): item for item in (reviews or [])},
    )
    return {(words[index - 1].word_id, words[index].word_id) for index in boundaries if 0 < index < len(words)}


def _ensure_active(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled and is_cancelled():
        raise AppException(409, "VIDEO_LOCALIZATION_OPERATION_CANCELLED", "字幕听写任务已取消")


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))
