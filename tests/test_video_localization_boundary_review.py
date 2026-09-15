from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import boundary_review
from app.domains.video_localization.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationBoundaryReview,
)


def _word(index: int, text: str, start_ms: int, end_ms: int) -> VideoLocalizationAlignedWord:
    return VideoLocalizationAlignedWord(
        word_id=f"word_{index:06d}",
        segment_id=f"asr_{index:04d}",
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        timing_confidence="high",
    )


def test_boundary_stage_uses_local_optimizer_without_calling_llm(monkeypatch):
    from app.services import llm_runtime

    words = [
        _word(1, "This", 0, 250),
        _word(2, "works.", 260, 650),
        _word(3, "Next", 900, 1200),
        _word(4, "part.", 1210, 1600),
    ]
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("boundary stage must not call an LLM")),
    )
    reviews, metadata = boundary_review.review_candidate_boundaries(
        words,
        [],
        language="en",
    )

    assert reviews == []
    assert metadata["status"] == "completed"
    assert metadata["review_batch_count"] == 0
    assert metadata["review_round_count"] == 0
    assert metadata["stop_reason"] == "local_segmentation"
    assert metadata["model_id"] is None
    assert metadata["quality_flags"] == ["boundary_review_local_rules"]


def test_boundary_stage_drops_persisted_llm_decisions(monkeypatch):
    words = [
        _word(1, "Complete.", 0, 300),
        _word(2, "Next", 500, 800),
        _word(3, "line.", 810, 1100),
    ]
    old_review = VideoLocalizationBoundaryReview(
        boundary_id="word_000001:word_000002",
        left_word_id="word_000001",
        right_word_id="word_000002",
        decision="avoid",
        confidence=0.9,
        reason="incomplete_syntax",
        prompt_version="boundary-review-v4",
        model_id="old-model",
    )
    observed = {}

    def baseline(_words, _audio_features, reviews=None, **_kwargs):
        observed["reviews"] = reviews
        return {("word_000001", "word_000002")}

    monkeypatch.setattr(boundary_review, "_baseline_boundary_pairs", baseline)

    reviews, metadata = boundary_review.review_candidate_boundaries(
        words,
        [],
        language="en",
        existing_reviews=[old_review],
    )

    assert reviews == []
    assert observed["reviews"] is None
    assert metadata["reused_review_count"] == 0
    assert metadata["deterministic_boundary_count"] == 1


def test_boundary_stage_checks_cancellation_before_local_work(monkeypatch):
    monkeypatch.setattr(
        boundary_review,
        "_baseline_boundary_pairs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cancelled work must not start")),
    )

    with pytest.raises(Exception) as exc_info:
        boundary_review.review_candidate_boundaries([], [], language="en", is_cancelled=lambda: True)

    assert getattr(exc_info.value, "code", None) == "VIDEO_LOCALIZATION_OPERATION_CANCELLED"


def test_baseline_boundary_pairs_delegates_to_shared_optimizer(monkeypatch):
    from app.domains.video_localization import subtitle_segmentation

    words = [
        _word(1, "One.", 0, 300),
        _word(2, "Two.", 500, 800),
        _word(3, "Three.", 1000, 1300),
    ]
    captured = {}

    def optimal(received_words, profile, **kwargs):
        captured["words"] = received_words
        captured["profile"] = profile.profile_id
        captured["kwargs"] = kwargs
        return [1, 3]

    monkeypatch.setattr(subtitle_segmentation, "_optimal_boundaries", optimal)

    pairs = boundary_review._baseline_boundary_pairs(words, [], profile_id="generic_zh")

    assert pairs == {("word_000001", "word_000002")}
    assert captured["words"] == words
    assert captured["profile"] == "generic_zh"
    assert captured["kwargs"]["boundary_reviews"] == {}
