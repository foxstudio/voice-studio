from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_transcript_quality_gate_operation_projection,
    transcript_quality_gate,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationTranscriptSegment,
)
from app.domains.video_localization.whole_recheck import (  # noqa: E402
    AsrWholeRecheckInput,
    AsrWholeRecheckQualitySummary,
    AsrWholeRecheckResult,
    AsrWholeRecheckUnresolvedItem,
)


def _segments() -> list[VideoLocalizationTranscriptSegment]:
    return [
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0001",
            start_ms=0,
            end_ms=1_000,
            raw_text="Hello.",
        ),
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0002",
            start_ms=1_000,
            end_ms=2_000,
            raw_text="World.",
            corrected_text="World!",
        ),
    ]


def _result(
    *,
    status: str = "completed",
    passed: bool = True,
    next_action: str = "finish",
    next_sections_cover_all_segments: bool = True,
    unresolved_items: list[AsrWholeRecheckUnresolvedItem] | None = None,
) -> AsrWholeRecheckResult:
    segments = _segments()
    request = AsrWholeRecheckInput(
        upstream_operation_id="decisions-operation",
        understanding_operation_id="understanding-operation",
        source_track_id="vocals",
        source_audio_sha256="audio-sha",
        language="en",
        profile_id="profile",
        round_index=1,
        document_summary="A short interview.",
        segments=segments,
        upstream_status="completed",
    )
    return AsrWholeRecheckResult(
        input=request,
        status=status,
        profile_id="profile",
        passed=passed,
        next_action=next_action,
        summary="Whole recheck result.",
        unresolved_items=unresolved_items or [],
        duration_ms=1,
        quality_summary=AsrWholeRecheckQualitySummary(
            status="passed" if passed else "warning",
            segment_count=len(segments),
            source_text_unchanged=True,
            segment_ids_unchanged=True,
            source_timing_unchanged=True,
            next_sections_cover_all_segments=(
                next_sections_cover_all_segments
            ),
            unresolved_items_reference_known_segments=True,
        ),
    )


def _input(
    result: AsrWholeRecheckResult,
) -> transcript_quality_gate.AsrTranscriptQualityGateInput:
    return transcript_quality_gate.build_input(
        result,
        upstream_operation_id="whole-recheck-operation",
        source_track_id="vocals",
        source_audio_sha256="audio-sha",
        segments=[item.model_copy(deep=True) for item in result.input.segments],
    )


def test_gate_allows_alignment_after_clean_completed_recheck():
    result = _result(
        unresolved_items=[
            AsrWholeRecheckUnresolvedItem(
                segment_id="asr_0001",
                summary="保留口语原文",
                reason="原音和上下文都没有足够证据支持修改。",
                recommended_action="keep_original",
            )
        ]
    )

    gate = transcript_quality_gate.DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE.run(_input(result))

    assert gate.decision == "ready_for_alignment"
    assert gate.can_start_alignment is True
    assert gate.blockers == []
    assert [item.code for item in gate.warnings] == ["keep_original"]
    assert gate.quality_summary.status == "warning"
    assert gate.quality_summary.keep_original_count == 1


def test_gate_keeps_manual_terminal_items_as_non_blocking_replay_advisories():
    result = _result(
        status="partial",
        passed=False,
        next_action="manual_review",
        unresolved_items=[
            AsrWholeRecheckUnresolvedItem(
                issue_id="issue-1",
                segment_id="asr_0002",
                summary="确认专名发音",
                reason="仅凭现有证据无法可靠确认。",
                recommended_action="manual_review",
            )
        ],
    )

    gate = transcript_quality_gate.DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE.run(_input(result))

    assert gate.decision == "ready_for_alignment"
    assert gate.can_start_alignment is True
    assert gate.blockers == []
    assert [item.code for item in gate.warnings] == [
        "review_recommended"
    ]
    assert gate.review_targets[0].segment_id == "asr_0002"
    assert gate.review_targets[0].location == "1秒 – 2秒"
    assert gate.review_targets[0].excerpt == "World!"
    assert gate.review_targets[0].start_ms == 1_000
    assert gate.review_targets[0].end_ms == 2_000
    assert gate.quality_summary.manual_review_count == 1
    assert gate.quality_summary.review_recommended_count == 1
    assert gate.quality_summary.status == "warning"


def test_gate_projects_adjacent_segment_review_span_as_one_target():
    result = _result(
        status="partial",
        passed=False,
        next_action="manual_review",
        unresolved_items=[
            AsrWholeRecheckUnresolvedItem(
                issue_id="boundary-1-2",
                segment_id="asr_0001",
                target_segment_ids=["asr_0001", "asr_0002"],
                summary="相邻片段仍有结构问题",
                reason="两段需要作为一个连续位置复听。",
                recommended_action="manual_review",
            )
        ],
    )

    gate = transcript_quality_gate.DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE.run(
        _input(result)
    )

    assert gate.can_start_alignment is True
    assert gate.review_targets[0].location == "0帧 – 2秒"
    assert gate.review_targets[0].excerpt == "Hello. | World!"
    assert gate.review_targets[0].start_ms == 0
    assert gate.review_targets[0].end_ms == 2_000
    assert gate.review_targets[0].target_segment_ids == [
        "asr_0001",
        "asr_0002",
    ]


def test_task_summary_formats_non_blocking_advisory_location_as_timecode(
):
    result = _result(
        status="partial",
        passed=False,
        next_action="manual_review",
        unresolved_items=[
            AsrWholeRecheckUnresolvedItem(
                issue_id="issue-1",
                segment_id="asr_0002",
                summary="确认专名发音",
                reason="仅凭现有证据无法可靠确认。",
                recommended_action="manual_review",
            )
        ],
    )
    gate = (
        transcript_quality_gate.DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE.run(
            _input(result)
        )
    )

    summary = (
        asr_transcript_quality_gate_operation_projection
        .success_summary(gate)
    )

    assert gate.review_targets[0].segment_id == "asr_0002"
    advisory = summary["task_step_results"]["transcript_quality_gate"][
        "sections"
    ][0]["items"][0]
    assert advisory["meta"] == "1秒 – 2秒"
    assert advisory["facts"][0] == {
        "label": "当前听写原文",
        "value": "World!",
    }
    assert summary["task_step_results"]["transcript_quality_gate"][
        "review_targets"
    ] == [
        {
            "title": "确认专名发音",
            "location": "1秒 – 2秒",
            "detail": "仅凭现有证据无法可靠确认。",
            "excerpt": "World!",
            "start_ms": 1_000,
            "end_ms": 2_000,
        }
    ]
    assert summary["can_start_alignment"] is True
    assert (
        summary["task_step_results"]["transcript_quality_gate"]["summary"]
        == "已自动继续校时，建议复听 1 处。"
    )
    assert len(
        summary["task_step_results"]["transcript_quality_gate"]["sections"]
    ) == 1


def test_gate_rejects_inconsistent_next_round_item_in_terminal_result():
    result = _result(
        status="completed",
        passed=True,
        next_action="finish",
        unresolved_items=[
            AsrWholeRecheckUnresolvedItem(
                issue_id="issue-next",
                segment_id="asr_0001",
                summary="仍需下一轮",
                reason="上游留下了下一轮检查项。",
                recommended_action="next_round",
            )
        ],
    )

    gate = transcript_quality_gate.DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE.run(
        _input(result)
    )

    assert gate.decision == "failed"
    assert gate.can_start_alignment is False
    assert "invalid_terminal_state" in {
        item.code for item in gate.blockers
    }


@pytest.mark.parametrize(
    ("status", "next_action", "expected_code"),
    [
        ("failed", "manual_review", "upstream_failed"),
        ("partial", "review_next_round", "review_next_round_required"),
    ],
)
def test_gate_fails_when_recheck_failed_or_requests_next_round(
    status: str,
    next_action: str,
    expected_code: str,
):
    gate = transcript_quality_gate.DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE.run(
        _input(
            _result(
                status=status,
                passed=False,
                next_action=next_action,
            )
        )
    )

    assert gate.decision == "failed"
    assert gate.can_start_alignment is False
    assert expected_code in {item.code for item in gate.blockers}
    assert gate.quality_summary.status == "failed"


def test_targeted_next_round_is_not_mislabeled_as_an_invariant_failure():
    gate = transcript_quality_gate.DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE.run(
        _input(
            _result(
                status="partial",
                passed=False,
                next_action="review_next_round",
                next_sections_cover_all_segments=False,
            )
        )
    )

    assert [item.code for item in gate.blockers] == [
        "review_next_round_required"
    ]


def test_build_input_rejects_crossed_source_and_changed_segments():
    result = _result()

    with pytest.raises(ValueError, match="same source audio"):
        transcript_quality_gate.build_input(
            result,
            upstream_operation_id="whole-recheck-operation",
            source_track_id="original",
            source_audio_sha256="audio-sha",
            segments=result.input.segments,
        )

    changed_segments = [item.model_copy(deep=True) for item in result.input.segments]
    changed_segments[0] = changed_segments[0].model_copy(update={"start_ms": 1})
    with pytest.raises(ValueError, match="complete unchanged segments"):
        transcript_quality_gate.build_input(
            result,
            upstream_operation_id="whole-recheck-operation",
            source_track_id="vocals",
            source_audio_sha256="audio-sha",
            segments=changed_segments,
        )


def test_gate_preserves_complete_segment_source_id_and_timing_snapshot():
    result = _result()
    request = _input(result)
    before = [item.model_dump(mode="json") for item in request.segments]

    gate = transcript_quality_gate.DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE.run(request)

    assert [item.model_dump(mode="json") for item in request.segments] == before
    assert [item.model_dump(mode="json") for item in gate.input.segments] == before
    assert gate.quality_summary.complete_segments is True
    assert gate.quality_summary.source_matches_upstream is True
    assert gate.quality_summary.source_text_unchanged is True
    assert gate.quality_summary.segment_ids_unchanged is True
    assert gate.quality_summary.source_timing_unchanged is True
