from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import dubbing_gap_adjudication as subject  # noqa: E402
from app.schemas.video_localization_dubbing_production import (  # noqa: E402
    DubbingAudioGapEvidence,
    DubbingCandidateAlignedWord,
    DubbingSemanticBoundaryAudit,
    DubbingSemanticBoundaryReview,
)


def _gap(
    gap_id: str,
    start_ms: int,
    end_ms: int,
    *,
    kind: str = "internal",
    safe: bool = True,
):
    return DubbingAudioGapEvidence(
        gap_id=gap_id,
        kind=kind,
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=end_ms - start_ms,
        evidence_sources=["energy", "word_alignment"],
        evidence_ids=[f"energy:{gap_id}", f"alignment:{gap_id}"],
        boundary_confidence="clear",
        edit_decision="retain",
        retained_duration_ms=end_ms - start_ms,
        decision_reason="待处理",
        safe_edit_boundary=safe,
    )


def test_safe_internal_gap_without_semantic_punctuation_is_removed():
    words = [
        DubbingCandidateAlignedWord(
            word_id="w1", text="因为", start_ms=100, end_ms=400
        ),
        DubbingCandidateAlignedWord(
            word_id="w2", text="所以", start_ms=720, end_ms=1_000
        ),
    ]

    processed = subject.process_gap_evidence(
        [_gap("g1", 400, 720)],
        aligned_words=words,
    )

    assert processed[0].edit_decision == "remove"
    assert processed[0].retained_duration_ms == 0
    assert processed[0].semantic_role == "continuous_phrase"


def test_punctuation_boundary_and_unsafe_gap_are_retained():
    words = [
        DubbingCandidateAlignedWord(
            word_id="w1", text="第一段。", start_ms=100, end_ms=400
        ),
        DubbingCandidateAlignedWord(
            word_id="w2", text="第二段", start_ms=720, end_ms=1_000
        ),
    ]

    punctuation, unsafe = subject.process_gap_evidence(
        [
            _gap("punctuation", 400, 720),
            _gap("unsafe", 1_000, 1_260, safe=False),
        ],
        aligned_words=words,
    )

    assert punctuation.edit_decision == "retain"
    assert punctuation.semantic_role == "semantic_boundary"
    assert unsafe.edit_decision == "retain"


def test_safe_outer_gaps_are_trimmed_without_model_review():
    processed = subject.process_gap_evidence(
        [
            _gap("leading", 0, 160, kind="leading"),
            _gap("trailing", 900, 1_050, kind="trailing"),
        ],
        aligned_words=[],
    )

    assert [item.edit_decision for item in processed] == ["shorten", "shorten"]
    assert [item.retained_duration_ms for item in processed] == [80, 80]
    assert all(
        subject.GAP_PROCESSING_EVIDENCE_ID in item.review_evidence_ids
        for item in processed
    )


def test_short_outer_gap_is_retained_as_required_safety_padding():
    processed = subject.process_gap_evidence(
        [_gap("leading", 0, 60, kind="leading")],
        aligned_words=[],
    )

    assert processed[0].edit_decision == "retain"
    assert processed[0].retained_duration_ms == 60


def test_outer_gap_retains_word_anchor_inside_the_measured_silence():
    word = DubbingCandidateAlignedWord(
        word_id="w1",
        text="早",
        start_ms=80,
        end_ms=240,
    )

    processed = subject.process_gap_evidence(
        [_gap("leading", 0, 100, kind="leading")],
        aligned_words=[word],
        speech_start_ms=100,
        speech_end_ms=900,
        audio_duration_ms=1_000,
    )

    assert processed[0].edit_decision == "retain"
    assert processed[0].retained_duration_ms == 100


def test_only_strongest_safe_gap_between_same_word_pair_is_removed():
    words = [
        DubbingCandidateAlignedWord(
            word_id="w1", text="拿了", start_ms=100, end_ms=400
        ),
        DubbingCandidateAlignedWord(
            word_id="w2", text="拿了", start_ms=1_200, end_ms=1_500
        ),
    ]

    weaker, stronger = subject.process_gap_evidence(
        [
            _gap("weaker", 520, 620),
            _gap("stronger", 820, 1_080),
        ],
        aligned_words=words,
    )

    assert weaker.edit_decision == "retain"
    assert weaker.semantic_role == "uncertain"
    assert stronger.edit_decision == "remove"
    assert stronger.semantic_role == "continuous_phrase"


def test_strong_punctuation_with_overlapping_words_requests_one_regeneration():
    words = [
        DubbingCandidateAlignedWord(
            word_id="w1", text="第一句。", start_ms=100, end_ms=520
        ),
        DubbingCandidateAlignedWord(
            word_id="w2", text="第二句", start_ms=500, end_ms=900
        ),
    ]

    assert subject.needs_single_regeneration([], aligned_words=words) is True


def test_comma_or_safe_strong_break_does_not_request_regeneration():
    words = [
        DubbingCandidateAlignedWord(
            word_id="w1", text="因为，", start_ms=100, end_ms=400
        ),
        DubbingCandidateAlignedWord(
            word_id="w2", text="所以", start_ms=400, end_ms=720
        ),
    ]

    assert (
        subject.needs_single_regeneration(
            [_gap("safe", 400, 520, safe=True)],
            aligned_words=words,
        )
        is False
    )


def test_unmatched_alignment_does_not_turn_punctuation_into_removable_silence():
    words = [
        DubbingCandidateAlignedWord(word_id="a", text="开始", start_ms=0, end_ms=500),
        DubbingCandidateAlignedWord(word_id="b", text="结束", start_ms=900, end_ms=1400),
        DubbingCandidateAlignedWord(word_id="c", text="完城", start_ms=1400, end_ms=1900),
    ]
    gap = subject.process_gap_evidence(
        [_gap("pause", 500, 900)], aligned_words=words,
        expected_spoken_text="开始，结束完成。",
    )[0]
    assert gap.edit_decision == "retain"
    assert gap.semantic_role == "uncertain"


def test_requested_sentence_boundary_with_zero_gap_requires_recovery():
    words = [
        DubbingCandidateAlignedWord(word_id="a", text="开始", start_ms=0, end_ms=500),
        DubbingCandidateAlignedWord(word_id="b", text="结束", start_ms=500, end_ms=1000),
    ]
    assert subject.needs_single_regeneration(
        [], aligned_words=words, expected_spoken_text="开始。结束"
    )
    assert not subject.needs_single_regeneration(
        [], aligned_words=words, expected_spoken_text="开始结束"
    )


def test_semantic_boundary_audit_keeps_all_edges_and_word_intruding_energy():
    words = [
        DubbingCandidateAlignedWord(word_id="a", text="甲", start_ms=0, end_ms=100),
        DubbingCandidateAlignedWord(word_id="b", text="乙", start_ms=100, end_ms=220),
        DubbingCandidateAlignedWord(word_id="c", text="丙", start_ms=200, end_ms=320),
    ]
    audit = subject.build_semantic_boundary_audit(
        source_revision="a" * 64,
        plan_revision=1,
        candidate_id="candidate_1",
        audio_sha256="b" * 64,
        candidate_evidence_fingerprint="c" * 64,
        candidate_clip_projection_fingerprint="d" * 64,
        expected_spoken_text="甲乙丙",
        aligned_words=words,
        gaps=[_gap("energy-inside", 80, 180, safe=False)],
        clips=[{
            "source_start_ms": 0,
            "source_end_ms": 320,
            "start_ms": 1_000,
        }],
    )

    assert audit.status == "pending_agent"
    assert [edge.boundary_id for edge in audit.boundaries] == ["a:b", "b:c"]
    assert audit.boundaries[0].source_relation == "touching"
    assert audit.boundaries[1].source_relation == "overlapping"
    assert audit.boundaries[0].left_text == "甲"
    assert audit.boundaries[0].right_final_fragments == [(1_100, 1_220)]
    assert audit.boundaries[0].low_energy_evidence[0].gap_id == "energy-inside"
    assert audit.boundaries[0].safe_edit_boundary is False


def _reviewed_boundary_audit(
    *,
    words: list[DubbingCandidateAlignedWord],
    clips: list[dict],
    audio_sha256: str = "b" * 64,
    candidate_evidence_fingerprint: str = "c" * 64,
    candidate_clip_projection_fingerprint: str = "d" * 64,
) -> DubbingSemanticBoundaryAudit:
    audit = subject.build_semantic_boundary_audit(
        source_revision="a" * 64,
        plan_revision=1,
        candidate_id="candidate_1",
        audio_sha256=audio_sha256,
        candidate_evidence_fingerprint=candidate_evidence_fingerprint,
        candidate_clip_projection_fingerprint=(
            candidate_clip_projection_fingerprint
        ),
        expected_spoken_text="".join(word.text for word in words),
        aligned_words=words,
        gaps=[],
        clips=clips,
    )
    reviews = [
        DubbingSemanticBoundaryReview(
            boundary_id=boundary.boundary_id,
            semantic_role="continuous_phrase",
            disposition="acceptable",
            reason=f"已核对 {boundary.left_text}{boundary.right_text} 连续表达。",
            evidence_id=f"review:{boundary.boundary_id}",
        )
        for boundary in audit.boundaries
    ]
    return audit.model_copy(update={"status": "accepted", "agent_reviews": reviews})


def test_unchanged_words_and_audio_reuse_reviews_after_uniform_timeline_shift():
    words = [
        DubbingCandidateAlignedWord(word_id="a", text="甲", start_ms=0, end_ms=100),
        DubbingCandidateAlignedWord(word_id="b", text="乙", start_ms=100, end_ms=200),
        DubbingCandidateAlignedWord(word_id="c", text="丙", start_ms=200, end_ms=300),
    ]
    previous = _reviewed_boundary_audit(
        words=words,
        clips=[{"source_start_ms": 0, "source_end_ms": 300, "start_ms": 1_000}],
    )
    current = subject.build_semantic_boundary_audit(
        source_revision=previous.source_revision,
        plan_revision=previous.plan_revision,
        candidate_id=previous.candidate_id,
        audio_sha256=previous.audio_sha256,
        candidate_evidence_fingerprint="e" * 64,
        candidate_clip_projection_fingerprint="f" * 64,
        expected_spoken_text=previous.expected_spoken_text,
        aligned_words=words,
        gaps=[],
        clips=[{"source_start_ms": 0, "source_end_ms": 300, "start_ms": 2_000}],
    )

    reused = subject.reuse_unchanged_boundary_reviews(previous, current)

    assert reused.status == "accepted"
    assert reused.agent_reviews == previous.agent_reviews
    assert reused.candidate_evidence_fingerprint == "e" * 64
    assert reused.candidate_clip_projection_fingerprint == "f" * 64


def test_changed_join_invalidates_only_that_boundary_review():
    words = [
        DubbingCandidateAlignedWord(word_id="a", text="甲", start_ms=0, end_ms=100),
        DubbingCandidateAlignedWord(word_id="b", text="乙", start_ms=100, end_ms=200),
        DubbingCandidateAlignedWord(word_id="c", text="丙", start_ms=200, end_ms=300),
    ]
    previous = _reviewed_boundary_audit(
        words=words,
        clips=[{"source_start_ms": 0, "source_end_ms": 300, "start_ms": 1_000}],
    )
    current = subject.build_semantic_boundary_audit(
        source_revision=previous.source_revision,
        plan_revision=previous.plan_revision,
        candidate_id=previous.candidate_id,
        audio_sha256=previous.audio_sha256,
        candidate_evidence_fingerprint=previous.candidate_evidence_fingerprint,
        candidate_clip_projection_fingerprint="e" * 64,
        expected_spoken_text=previous.expected_spoken_text,
        aligned_words=words,
        gaps=[],
        clips=[
            {"source_start_ms": 0, "source_end_ms": 100, "start_ms": 1_000},
            {"source_start_ms": 100, "source_end_ms": 300, "start_ms": 1_150},
        ],
    )

    reused = subject.reuse_unchanged_boundary_reviews(previous, current)

    assert reused.status == "pending_agent"
    assert [review.boundary_id for review in reused.agent_reviews] == ["b:c"]
    assert current.boundaries[0].final_gap_ms == 50
    assert current.boundaries[1].final_relation == "touching"


def test_changed_audio_or_aligned_words_cannot_reuse_reviews():
    words = [
        DubbingCandidateAlignedWord(word_id="a", text="甲", start_ms=0, end_ms=100),
        DubbingCandidateAlignedWord(word_id="b", text="乙", start_ms=100, end_ms=200),
    ]
    clips = [{"source_start_ms": 0, "source_end_ms": 220, "start_ms": 1_000}]
    previous = _reviewed_boundary_audit(words=words, clips=clips)

    changed_audio = subject.build_semantic_boundary_audit(
        source_revision=previous.source_revision,
        plan_revision=previous.plan_revision,
        candidate_id=previous.candidate_id,
        audio_sha256="9" * 64,
        candidate_evidence_fingerprint="e" * 64,
        candidate_clip_projection_fingerprint="f" * 64,
        expected_spoken_text=previous.expected_spoken_text,
        aligned_words=words,
        gaps=[],
        clips=clips,
    )
    changed_words = [
        words[0],
        words[1].model_copy(update={"start_ms": 110, "end_ms": 210}),
    ]
    changed_alignment = subject.build_semantic_boundary_audit(
        source_revision=previous.source_revision,
        plan_revision=previous.plan_revision,
        candidate_id=previous.candidate_id,
        audio_sha256=previous.audio_sha256,
        candidate_evidence_fingerprint="e" * 64,
        candidate_clip_projection_fingerprint="f" * 64,
        expected_spoken_text=previous.expected_spoken_text,
        aligned_words=changed_words,
        gaps=[],
        clips=clips,
    )

    assert subject.reuse_unchanged_boundary_reviews(
        previous, changed_audio
    ) == changed_audio
    assert subject.reuse_unchanged_boundary_reviews(
        previous, changed_alignment
    ) == changed_alignment


def test_empty_boundary_set_does_not_invent_historical_review_acceptance():
    words = [
        DubbingCandidateAlignedWord(word_id="a", text="甲", start_ms=0, end_ms=100),
    ]
    clips = [{"source_start_ms": 0, "source_end_ms": 100, "start_ms": 1_000}]
    previous = _reviewed_boundary_audit(words=words, clips=clips)
    current = subject.build_semantic_boundary_audit(
        source_revision=previous.source_revision,
        plan_revision=previous.plan_revision,
        candidate_id=previous.candidate_id,
        audio_sha256=previous.audio_sha256,
        candidate_evidence_fingerprint="e" * 64,
        candidate_clip_projection_fingerprint="f" * 64,
        expected_spoken_text=previous.expected_spoken_text,
        aligned_words=words,
        gaps=[],
        clips=clips,
    )

    reused = subject.reuse_unchanged_boundary_reviews(previous, current)

    assert reused.agent_reviews == []
    assert reused.status == "pending_agent"
