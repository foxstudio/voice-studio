from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import boundary_review  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
    VideoLocalizationBoundaryReview,
)
from app.models.schemas import LlmProviderListResponse, LlmProviderProfile  # noqa: E402


def _word(index: int, text: str, start_ms: int, end_ms: int, segment_id: str = "asr_0001"):
    return VideoLocalizationAlignedWord(
        word_id=f"word_{index:06d}",
        segment_id=segment_id,
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        timing_confidence="high",
        timing_source="forced_aligner",
    )


def _configure_llm(monkeypatch):
    profile = LlmProviderProfile(
        profile_id="review",
        name="Review",
        base_url="https://llm.example.com/v1",
        model_id="review-model",
        enabled=True,
        api_key_configured=True,
    )
    monkeypatch.setattr(
        boundary_review.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id=profile.profile_id),
    )
    monkeypatch.setattr(boundary_review.settings_store, "llm_profile", lambda profile_id: profile)
    return profile


def test_boundary_review_uses_stable_word_ids_and_audio_as_supporting_evidence(monkeypatch):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(boundary_review, "MAX_REVIEW_ROUNDS", 1)
    words = [
        _word(1, "Each", 0, 250),
        _word(2, "one", 270, 500),
        _word(3, "bigger", 850, 1100),
        _word(4, "and", 1120, 1280),
        _word(5, "better.", 1300, 1700),
    ]
    evidence = VideoLocalizationAudioBoundaryEvidence(
        boundary_id="word_000002:word_000003",
        left_word_id="word_000002",
        right_word_id="word_000003",
        start_ms=500,
        end_ms=850,
        gap_ms=350,
        low_energy_ms=330,
        low_energy_ratio=0.9,
        gap_rms_dbfs=-48,
        speech_reference_dbfs=-18,
        noise_floor_dbfs=-55,
        energy_drop_db=30,
        confidence="high",
    )
    from app.services import llm_runtime

    captured = {}

    def complete(**kwargs):
        captured.update(kwargs)
        return {
            "boundaries": [
                {
                    "boundary_id": "word_000002:word_000003",
                    "decision": "avoid",
                    "confidence": 0.95,
                    "reason_code": "protected_span",
                }
            ]
        }

    # Add one following sentence so terminal punctuation is also a candidate.
    words.extend([_word(6, "Next", 2100, 2350, "asr_0002"), _word(7, "step.", 2370, 2700, "asr_0002")])
    monkeypatch.setattr(llm_runtime, "complete_json", complete)

    reviews, metadata = boundary_review.review_candidate_boundaries(words, [evidence], language="en")

    assert metadata["status"] == "completed"
    assert [(item.boundary_id, item.decision) for item in reviews] == [
        ("word_000002:word_000003", "avoid"),
    ]
    assert metadata["deterministic_boundary_count"] == 1
    candidates = captured["user_payload"]["candidates"]
    assert candidates[0]["left_word_id"] == "word_000002"
    assert candidates[0]["features"]["audio_pause"]["low_energy_ms"] == 330
    assert "timestamps" in captured["user_payload"]["policy"]["forbidden"]
    assert captured["timeout"] == boundary_review.REQUEST_TIMEOUT_SECONDS
    assert boundary_review.MIN_OUTPUT_TOKENS <= captured["max_tokens"] <= boundary_review.MAX_OUTPUT_TOKENS
    assert metadata["rounds"][0]["batches"] == [
        {
            "round": 1,
            "batch": 1,
            "candidate_count": 1,
            "duration_ms": metadata["rounds"][0]["batches"][0]["duration_ms"],
            "status": "success",
            "attempt_count": 1,
        }
    ]
    assert metadata["rounds"][0]["batches"][0]["duration_ms"] >= 0


def test_boundary_review_rejects_missing_or_reordered_ids(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "boundaries": [{"boundary_id": "wrong", "decision": "allow", "confidence": 0.5, "reason_code": "unclear"}]
        },
    )
    words = [_word(1, "Maybe", 0, 400), _word(2, "next", 800, 1100, "asr_0002")]
    monkeypatch.setattr(
        boundary_review,
        "_baseline_boundary_pairs",
        lambda words, audio_features, reviews=None, **_kwargs: {(words[0].word_id, words[1].word_id)},
    )

    reviews, metadata = boundary_review.review_candidate_boundaries(words, [], language="en")

    assert reviews == []
    assert metadata["status"] == "failed"
    assert metadata["quality_flags"] == ["boundary_review_failed"]
    failed_batch = metadata["rounds"][0]["batches"][0]
    assert failed_batch["round"] == 1
    assert failed_batch["batch"] == 1
    assert failed_batch["candidate_count"] == 1
    assert failed_batch["status"] == "failed"
    assert failed_batch["attempt_count"] == boundary_review.MAX_ATTEMPTS
    assert failed_batch["duration_ms"] >= 0


def test_boundary_review_is_optional_without_llm(monkeypatch):
    monkeypatch.setattr(
        boundary_review.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[], default_profile_id=None),
    )
    words = [_word(1, "Maybe", 0, 400), _word(2, "next", 800, 1100, "asr_0002")]
    monkeypatch.setattr(
        boundary_review,
        "_baseline_boundary_pairs",
        lambda words, audio_features, reviews=None, **_kwargs: {(words[0].word_id, words[1].word_id)},
    )

    reviews, metadata = boundary_review.review_candidate_boundaries(words, [], language="en")

    assert reviews == []
    assert metadata["status"] == "not_configured"


def test_boundary_decision_normalizes_provider_boolean_without_accepting_unknown_values():
    preferred = boundary_review.BoundaryDecision(
        boundary_id="word_000001:word_000002",
        decision=True,
        confidence="high",
        reason_code="sentence_end",
    )
    avoided = boundary_review.BoundaryDecision(
        boundary_id="word_000001:word_000002",
        decision=False,
        confidence="medium",
        reason_code="protected_span",
    )

    assert preferred.decision == "prefer"
    assert avoided.decision == "avoid"


def test_boundary_decision_normalizes_common_provider_decision_aliases():
    preferred_aliases = ["accept", "include", "split", "recommended"]
    allowed_aliases = ["safe", "valid", "acceptable"]
    avoided_aliases = ["reject", "exclude", "merge", "do_not_split", "do not split", "unsafe", "invalid"]

    for decision in preferred_aliases:
        item = boundary_review.BoundaryDecision(
            boundary_id="word_000001:word_000002",
            decision=decision,
            confidence=0.8,
            reason_code="clause_end",
        )
        assert item.decision == "prefer"

    for decision in allowed_aliases:
        item = boundary_review.BoundaryDecision(
            boundary_id="word_000001:word_000002",
            decision=decision,
            confidence=0.8,
            reason_code="unclear",
        )
        assert item.decision == "allow"

    for decision in avoided_aliases:
        item = boundary_review.BoundaryDecision(
            boundary_id="word_000002:word_000003",
            decision=decision,
            confidence=0.8,
            reason_code="incomplete_syntax",
        )
        assert item.decision == "avoid"


def test_boundary_decision_prefers_structural_avoid_reason_over_safe_alias():
    item = boundary_review.BoundaryDecision(
        boundary_id="word_000001:word_000002",
        decision="safe",
        confidence=0.9,
        reason_code="incomplete_syntax",
    )

    assert item.decision == "avoid"


def test_boundary_review_can_move_a_cut_and_protect_the_minimum_phrase(monkeypatch):
    from app.services import llm_runtime

    context = [
        {"word_id": f"word_{index:06d}", "text": text}
        for index, text in enumerate(["Done.", "And", "the", "reason", "I'm"], start=1)
    ]
    candidate = {
        "boundary_id": "word_000003:word_000004",
        "left_word_id": "word_000003",
        "right_word_id": "word_000004",
        "left_context": context[:3],
        "right_context": context[3:],
        "nearby_boundaries": [
            {
                "boundary_id": "word_000001:word_000002",
                "left_word_id": "word_000001",
                "right_word_id": "word_000002",
            }
        ],
        "features": {"terminal_punctuation_after_left": False},
    }
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "boundaries": [
                {
                    "boundary_id": candidate["boundary_id"],
                    "decision": "avoid",
                    "confidence": 0.95,
                    "reason_code": "incomplete_syntax",
                    "recommended_boundary_id": "word_000001:word_000002",
                    "protected_start_word_id": "word_000002",
                    "protected_end_word_id": "word_000005",
                }
            ]
        },
    )

    reviews, error = boundary_review._review_batch(
        [candidate],
        language="en",
        profile_id="review",
        model_id="review-model",
    )

    assert error is None
    by_id = {item.boundary_id: item for item in reviews}
    assert by_id["word_000001:word_000002"].decision == "prefer"
    assert by_id["word_000002:word_000003"].decision == "avoid"
    assert by_id["word_000003:word_000004"].decision == "avoid"
    assert by_id["word_000004:word_000005"].decision == "avoid"


def test_baseline_boundary_selection_uses_requested_profile_and_audio_state(monkeypatch):
    from app.domains.video_localization import subtitle_segmentation

    words = [_word(1, "one", 0, 200), _word(2, "two", 220, 420)]
    captured = {}

    def optimal(_words, profile, **kwargs):
        captured["profile_id"] = profile.profile_id
        captured["audio_analysis_available"] = kwargs["audio_analysis_available"]
        return [2]

    monkeypatch.setattr(subtitle_segmentation, "_optimal_boundaries", optimal)

    boundary_review._baseline_boundary_pairs(
        words,
        [],
        profile_id="short_video_large_text",
        audio_analysis_available=True,
    )

    assert captured == {"profile_id": "short_video_large_text", "audio_analysis_available": True}


def test_truncated_reasoning_batch_splits_without_discarding_valid_boundaries(monkeypatch):
    from app.services import llm_runtime

    candidates = [
        {
            "boundary_id": f"word_{index:06d}:word_{index + 1:06d}",
            "left_word_id": f"word_{index:06d}",
            "right_word_id": f"word_{index + 1:06d}",
            "left_context": [],
            "right_context": [],
            "features": {},
        }
        for index in range(1, 5)
    ]
    batch_sizes = []

    def complete_json(**kwargs):
        batch = kwargs["user_payload"]["candidates"]
        batch_sizes.append(len(batch))
        if len(batch) > 1:
            raise llm_runtime.LlmRuntimeError(
                "truncated",
                code="llm_output_truncated",
                status_code=502,
            )
        return {
            "boundaries": [
                {
                    "boundary_id": batch[0]["boundary_id"],
                    "decision": "allow",
                    "confidence": 0.5,
                    "reason_code": "unclear",
                }
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)

    reviews, error = boundary_review._review_batch(
        candidates,
        language="en",
        profile_id="review",
        model_id="review-model",
    )

    assert error is None
    assert [item.boundary_id for item in reviews] == [item["boundary_id"] for item in candidates]
    assert batch_sizes.count(4) == 1
    assert batch_sizes.count(1) == 4


def test_compact_review_batch_deduplicates_shared_context_words():
    batch = [
        {
            "boundary_id": "word_1:word_2",
            "left_word_id": "word_1",
            "right_word_id": "word_2",
            "left_context": [{"word_id": "word_1", "text": "Seedance"}],
            "right_context": [{"word_id": "word_2", "text": "works"}],
            "nearby_boundaries": [],
            "features": {"word_gap_ms": 100},
        },
        {
            "boundary_id": "word_2:word_3",
            "left_word_id": "word_2",
            "right_word_id": "word_3",
            "left_context": [{"word_id": "word_2", "text": "works"}],
            "right_context": [{"word_id": "word_3", "text": "well"}],
            "nearby_boundaries": [],
            "features": {"word_gap_ms": 120},
        },
    ]

    candidates, words = boundary_review._compact_review_batch(batch)

    assert [item["boundary_id"] for item in candidates] == ["word_1:word_2", "word_2:word_3"]
    assert words == [
        {"word_id": "word_1", "text": "Seedance"},
        {"word_id": "word_2", "text": "works"},
        {"word_id": "word_3", "text": "well"},
    ]
    assert all("left_context" not in item and "right_context" not in item for item in candidates)


def test_repeated_timeout_stops_without_recursive_call_amplification(monkeypatch):
    from app.services import llm_runtime

    candidates = [
        {
            "boundary_id": f"word_{index:06d}:word_{index + 1:06d}",
            "left_word_id": f"word_{index:06d}",
            "right_word_id": f"word_{index + 1:06d}",
            "left_context": [],
            "right_context": [],
            "features": {},
        }
        for index in range(1, 3)
    ]
    batch_sizes = []

    def complete_json(**kwargs):
        batch = kwargs["user_payload"]["candidates"]
        batch_sizes.append(len(batch))
        raise llm_runtime.LlmRuntimeError("timeout", code="llm_timeout", status_code=504)

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)

    reviews, error = boundary_review._review_batch(
        candidates,
        language="en",
        profile_id="review",
        model_id="review-model",
    )

    assert reviews == []
    assert error == "timeout"
    assert batch_sizes.count(2) == boundary_review.MAX_ATTEMPTS
    assert batch_sizes.count(1) == 0


def test_provider_http_failures_are_not_retried_by_boundary_layer(monkeypatch):
    from app.services import llm_runtime

    candidate = {
        "boundary_id": "word_000001:word_000002",
        "left_word_id": "word_000001",
        "right_word_id": "word_000002",
        "left_context": [],
        "right_context": [],
        "features": {},
    }
    calls = 0

    def complete_json(**_kwargs):
        nonlocal calls
        calls += 1
        raise llm_runtime.LlmRuntimeError(
            "provider unavailable",
            code="llm_provider_unavailable",
            status_code=503,
        )

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)

    reviews, error = boundary_review._review_batch(
        [candidate],
        language="en",
        profile_id="review",
        model_id="review-model",
    )

    assert reviews == []
    assert error == "provider unavailable"
    assert calls == 1


def test_boundary_review_records_split_recovery_as_batch_fallback(monkeypatch):
    from app.services import llm_runtime

    candidates = [
        {
            "boundary_id": f"word_{index:06d}:word_{index + 1:06d}",
            "left_word_id": f"word_{index:06d}",
            "right_word_id": f"word_{index + 1:06d}",
            "left_context": [],
            "right_context": [],
            "features": {},
        }
        for index in range(1, 3)
    ]

    def complete_json(**kwargs):
        batch = kwargs["user_payload"]["candidates"]
        if len(batch) > 1:
            raise llm_runtime.LlmRuntimeError("truncated", code="llm_output_truncated", status_code=502)
        return {
            "boundaries": [
                {
                    "boundary_id": batch[0]["boundary_id"],
                    "decision": "allow",
                    "confidence": 0.5,
                    "reason_code": "unclear",
                }
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)

    reviews, error, timing = boundary_review._review_batch_with_timing(
        candidates,
        round_number=2,
        batch_number=3,
        language="en",
        profile_id="review",
        model_id="review-model",
    )

    assert error is None
    assert len(reviews) == 2
    assert timing == {
        "round": 2,
        "batch": 3,
        "candidate_count": 2,
        "duration_ms": timing["duration_ms"],
        "status": "fallback",
        "attempt_count": 3,
    }
    assert timing["duration_ms"] >= 0


def test_boundary_review_includes_length_driven_boundary_without_pause_or_punctuation(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    words = [
        _word(1, "every", 0, 200),
        _word(2, "prompt", 200, 500),
    ]
    pair = (words[0].word_id, words[1].word_id)
    monkeypatch.setattr(
        boundary_review,
        "_baseline_boundary_pairs",
        lambda words, audio_features, reviews=None, **_kwargs: {pair},
    )
    requested_ids = []

    def complete_json(**kwargs):
        requested_ids.extend(item["boundary_id"] for item in kwargs["user_payload"]["candidates"])
        return {
            "boundaries": [
                {
                    "boundary_id": f"{pair[0]}:{pair[1]}",
                    "decision": "avoid",
                    "confidence": 0.95,
                    "reason_code": "protected_span",
                }
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)

    reviews, metadata = boundary_review.review_candidate_boundaries(words, [], language="en")

    assert requested_ids == [f"{pair[0]}:{pair[1]}"]
    assert reviews[0].decision == "avoid"
    assert metadata["candidate_count"] == 1
    assert metadata["deterministic_boundary_count"] == 0


def test_boundary_review_rechecks_new_boundary_selected_after_semantic_avoid(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    words = [
        _word(1, "something", 0, 300),
        _word(2, "behind", 500, 800),
        _word(3, "me", 800, 1000),
    ]

    def selected_pairs(words, audio_features, reviews=None, **_kwargs):
        if not reviews:
            return {(words[0].word_id, words[1].word_id)}
        return {(words[1].word_id, words[2].word_id)}

    monkeypatch.setattr(boundary_review, "_baseline_boundary_pairs", selected_pairs)
    requested_ids = []

    def complete_json(**kwargs):
        candidates = kwargs["user_payload"]["candidates"]
        requested_ids.extend(item["boundary_id"] for item in candidates)
        return {
            "boundaries": [
                {
                    "boundary_id": item["boundary_id"],
                    "decision": "avoid" if item["boundary_id"].endswith("000002") else "allow",
                    "confidence": 0.9,
                    "reason_code": "incomplete_syntax" if item["boundary_id"].endswith("000002") else "unclear",
                }
                for item in candidates
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)

    reviews, metadata = boundary_review.review_candidate_boundaries(words, [], language="en")

    assert requested_ids == [
        "word_000001:word_000002",
        "word_000002:word_000003",
    ]
    assert [item.decision for item in reviews] == ["avoid", "allow"]
    assert metadata["candidate_count"] == 2
    assert metadata["review_count"] == 2
    assert metadata["review_round_count"] == 2


def test_boundary_review_only_checks_newly_selected_boundary_in_followup_round(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    words = [_word(index, f"word{index}", index * 300, index * 300 + 200) for index in range(1, 8)]
    initial_pair = (words[2].word_id, words[3].word_id)
    replacement_pair = (words[4].word_id, words[5].word_id)

    def selected_pairs(_words, _audio_features, reviews=None, **_kwargs):
        return {replacement_pair} if reviews else {initial_pair}

    monkeypatch.setattr(boundary_review, "_baseline_boundary_pairs", selected_pairs)
    requested_ids = []

    def complete_json(**kwargs):
        candidates = kwargs["user_payload"]["candidates"]
        requested_ids.extend(item["boundary_id"] for item in candidates)
        return {
            "boundaries": [
                {
                    "boundary_id": item["boundary_id"],
                    "decision": "allow",
                    "confidence": 0.9,
                    "reason_code": "unclear",
                }
                for item in candidates
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)

    _reviews, metadata = boundary_review.review_candidate_boundaries(words, [], language="en")

    assert requested_ids[0] == f"{initial_pair[0]}:{initial_pair[1]}"
    assert f"{replacement_pair[0]}:{replacement_pair[1]}" in requested_ids
    assert requested_ids == [
        f"{initial_pair[0]}:{initial_pair[1]}",
        f"{replacement_pair[0]}:{replacement_pair[1]}",
    ]
    assert metadata["review_round_count"] == 2
    assert [item["candidate_count"] for item in metadata["rounds"]] == [1, 1]
    assert [item["batch_count"] for item in metadata["rounds"]] == [1, 1]
    assert metadata["stop_reason"] == "stable_boundary_set"


def test_boundary_review_does_not_rescan_large_unchanged_deterministic_set(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    words = [
        _word(1, "one", 0, 200),
        _word(2, "two", 220, 420),
        _word(3, "three", 440, 640),
        *[
            _word(index, f"Done{index}.", index * 220, index * 220 + 180, f"asr_{index:04d}")
            for index in range(4, 106)
        ],
    ]
    initial_pair = (words[0].word_id, words[1].word_id)
    replacement_pair = (words[1].word_id, words[2].word_id)
    deterministic_pairs = {
        (words[index].word_id, words[index + 1].word_id)
        for index in range(3, len(words) - 1)
    }

    def selected_pairs(_words, _audio_features, reviews=None, **_kwargs):
        return ({replacement_pair} if reviews else {initial_pair}) | deterministic_pairs

    monkeypatch.setattr(boundary_review, "_baseline_boundary_pairs", selected_pairs)
    requested_ids = []

    def complete_json(**kwargs):
        candidates = kwargs["user_payload"]["candidates"]
        requested_ids.extend(item["boundary_id"] for item in candidates)
        return {
            "boundaries": [
                {
                    "boundary_id": item["boundary_id"],
                    "decision": "avoid" if item["boundary_id"].endswith("000002") else "allow",
                    "confidence": 0.9,
                    "reason_code": "incomplete_syntax" if item["boundary_id"].endswith("000002") else "unclear",
                }
                for item in candidates
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)

    _reviews, metadata = boundary_review.review_candidate_boundaries(words, [], language="en")

    assert requested_ids == [
        f"{initial_pair[0]}:{initial_pair[1]}",
        f"{replacement_pair[0]}:{replacement_pair[1]}",
    ]
    assert metadata["candidate_count"] == 2
    assert metadata["deterministic_boundary_count"] == len(deterministic_pairs)
    assert [item["candidate_count"] for item in metadata["rounds"]] == [1, 1]


def test_boundary_review_reuses_matching_persisted_decision(monkeypatch):
    profile = _configure_llm(monkeypatch)
    from app.services import llm_runtime

    words = [
        _word(1, "Complete.", 0, 300),
        _word(2, "Next", 500, 800),
        _word(3, "line.", 800, 1100),
    ]
    pair = (words[0].word_id, words[1].word_id)
    existing = VideoLocalizationBoundaryReview(
        boundary_id=f"{pair[0]}:{pair[1]}",
        left_word_id=pair[0],
        right_word_id=pair[1],
        decision="prefer",
        confidence=0.95,
        reason="sentence_end",
        prompt_version=boundary_review.PROMPT_VERSION,
        model_id=profile.model_id,
    )
    monkeypatch.setattr(boundary_review, "_baseline_boundary_pairs", lambda *_args, **_kwargs: {pair})
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("matching review should be reused")),
    )

    reviews, metadata = boundary_review.review_candidate_boundaries(
        words,
        [],
        language="en",
        existing_reviews=[existing],
    )

    assert reviews == [existing]
    assert metadata["status"] == "completed"
    assert metadata["candidate_count"] == 0
    assert metadata["reused_review_count"] == 1


def test_boundary_review_reuses_partial_history_but_still_reviews_unseen_selected_boundary(monkeypatch):
    profile = _configure_llm(monkeypatch)
    from app.services import llm_runtime

    words = [
        _word(1, "one", 0, 200),
        _word(2, "two", 220, 420),
        _word(3, "three", 440, 640),
    ]
    pairs = {
        (words[0].word_id, words[1].word_id),
        (words[1].word_id, words[2].word_id),
    }
    existing = VideoLocalizationBoundaryReview(
        boundary_id=f"{words[0].word_id}:{words[1].word_id}",
        left_word_id=words[0].word_id,
        right_word_id=words[1].word_id,
        decision="allow",
        confidence=0.8,
        reason="unclear",
        prompt_version=boundary_review.PROMPT_VERSION,
        model_id=profile.model_id,
    )
    monkeypatch.setattr(boundary_review, "_baseline_boundary_pairs", lambda *_args, **_kwargs: pairs)
    requested = []

    def complete_json(**kwargs):
        candidate = kwargs["user_payload"]["candidates"][0]
        requested.append(candidate["boundary_id"])
        return {
            "boundaries": [
                {
                    "boundary_id": candidate["boundary_id"],
                    "decision": "allow",
                    "confidence": 0.8,
                    "reason_code": "unclear",
                }
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)

    reviews, metadata = boundary_review.review_candidate_boundaries(
        words,
        [],
        language="en",
        existing_reviews=[existing],
    )

    assert requested == [f"{words[1].word_id}:{words[2].word_id}"]
    assert len(reviews) == 2
    assert metadata["reused_review_count"] == 1
    assert metadata["candidate_count"] == 1


def test_boundary_review_marks_final_unreviewed_boundary_partial_at_round_limit(monkeypatch):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(boundary_review, "MAX_REVIEW_ROUNDS", 1)
    from app.services import llm_runtime

    words = [
        _word(1, "something", 0, 300),
        _word(2, "behind", 500, 800),
        _word(3, "me", 800, 1000),
    ]

    def selected_pairs(words, audio_features, reviews=None, **_kwargs):
        if not reviews:
            return {(words[0].word_id, words[1].word_id)}
        return {(words[1].word_id, words[2].word_id)}

    monkeypatch.setattr(boundary_review, "_baseline_boundary_pairs", selected_pairs)
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "boundaries": [
                {
                    "boundary_id": item["boundary_id"],
                    "decision": "avoid",
                    "confidence": 0.95,
                    "reason_code": "incomplete_syntax",
                }
                for item in kwargs["user_payload"]["candidates"]
            ]
        },
    )
    progress = []

    _reviews, metadata = boundary_review.review_candidate_boundaries(
        words,
        [],
        language="en",
        progress_callback=lambda round_index, max_rounds, review_count: progress.append(
            (round_index, max_rounds, review_count)
        ),
    )

    assert metadata["status"] == "partial"
    assert metadata["unresolved_candidate_count"] == 1
    assert metadata["quality_flags"] == ["boundary_review_incomplete"]
    assert progress == [(1, 1, 1)]


def test_boundary_review_cancel_check_runs_before_each_llm_batch(monkeypatch):
    from app.errors import AppException
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cancelled batch must not call LLM")),
    )
    candidate = {
        "boundary_id": "word_000001:word_000002",
        "left_word_id": "word_000001",
        "right_word_id": "word_000002",
        "left_context": [],
        "right_context": [],
        "features": {},
    }

    with pytest.raises(AppException) as exc_info:
        boundary_review._review_batch(
            [candidate],
            language="en",
            profile_id="review",
            model_id="review-model",
            is_cancelled=lambda: True,
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_OPERATION_CANCELLED"
