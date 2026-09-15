from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_whole_recheck_operation_projection,
    operation_queue,
    whole_recheck,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationTranscriptSegment,
)
from app.services import llm_runtime  # noqa: E402


def _segments() -> list[VideoLocalizationTranscriptSegment]:
    return [
        VideoLocalizationTranscriptSegment(
            segment_id=f"asr_{index:04d}",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            raw_text=text,
            corrected_text=corrected,
            speaker_cluster_id="speaker_01",
        )
        for index, (text, corrected) in enumerate(
            [
                (
                    "AI less demand for hardware.",
                    "AI means less demand for hardware.",
                ),
                ("The model costs three dollars.", None),
                ("Memory pricing remains cyclical.", None),
                ("Investors should focus on fundamentals.", None),
            ],
            start=1,
        )
    ]


def _request() -> whole_recheck.AsrWholeRecheckInput:
    return whole_recheck.AsrWholeRecheckInput(
        upstream_operation_id="review-decisions-operation",
        understanding_operation_id="understanding-operation",
        source_track_id="vocals",
        source_audio_sha256="audio-sha256",
        language="en",
        profile_id="review-profile",
        round_index=1,
        document_summary="一段关于人工智能芯片和投资的访谈。",
        content_logic=["讨论芯片需求", "分析内存价格"],
        speaker_style="专业访谈",
        segments=_segments(),
        decisions=[
            {
                "issue_id": "issue-01",
                "section_id": "S1",
                "segment_id": "asr_0001",
                "current_excerpt": "AI less demand",
                "proposed_replacement": "AI means less demand",
                "outcome": "applied",
                "reason": "确认漏掉 means。",
                "confidence": 0.95,
                "before_text": "AI less demand for hardware.",
                "after_text": "AI means less demand for hardware.",
            },
            {
                "issue_id": "issue-02",
                "section_id": "S1",
                "segment_id": "asr_0002",
                "current_excerpt": "three dollars",
                "outcome": "needs_confirmation",
                "reason": "数字需要人工听音。",
                "confidence": 0.6,
            },
        ],
        locked_changes=[
            {
                "segment_id": "asr_0001",
                "before": "AI less demand for hardware.",
                "after": "AI means less demand for hardware.",
                "reason": "确认漏掉 means。",
                "confidence": 0.95,
                "issue_id": "issue-01",
                "source_task_id": "review_decisions_r1",
                "round_index": 1,
            }
        ],
        upstream_status="completed",
    )


def test_whole_recheck_closes_locally_and_keeps_review_reminders(
    monkeypatch,
):
    calls = 0

    def fail_if_called(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("local closure must not call the model")

    monkeypatch.setattr(llm_runtime, "complete_json", fail_if_called)
    request = _request()

    result = whole_recheck.DEFAULT_WHOLE_RECHECK_SERVICE.run(request)

    assert result.status == "completed"
    assert result.passed is True
    assert result.next_action == "finish"
    assert result.next_sections == []
    assert result.input.segments == request.segments
    assert result.quality_summary.source_text_unchanged is True
    assert result.quality_summary.segment_ids_unchanged is True
    assert result.quality_summary.source_timing_unchanged is True
    assert result.unresolved_items[0].issue_id == "issue-02"
    assert result.llm_calls == []
    assert calls == 0


def test_current_whole_recheck_summary_describes_local_closure() -> None:
    result = whole_recheck.DEFAULT_WHOLE_RECHECK_SERVICE.run(_request())

    summary = asr_whole_recheck_operation_projection.success_summary(
        result
    )
    stage = operation_queue._asr_workflow_stage_projection(
        "transcript_review",
        {"whole_recheck_r1"},
    )
    current_text = str(summary) + str(stage)

    assert summary["stage"] == "本地收尾检查已完成（开发单步）"
    assert "本地规则" in current_text
    assert "不调用模型" in current_text
    assert "进入下一轮" not in current_text
    assert "重新通读" not in current_text


def test_whole_recheck_marks_residual_cross_segment_issue_for_listening():
    request = _request().model_copy(
        update={
            "segments": [
                VideoLocalizationTranscriptSegment(
                    segment_id="asr_0011",
                    start_ms=54_449,
                    end_ms=59_385,
                    raw_text=(
                        "People ask what about those model companies because"
                    ),
                ),
                VideoLocalizationTranscriptSegment(
                    segment_id="asr_0012",
                    start_ms=59_385,
                    end_ms=64_876,
                    raw_text="Although most of them are not public.",
                ),
            ],
            "decisions": [],
            "locked_changes": [],
        }
    )

    result = whole_recheck.DEFAULT_WHOLE_RECHECK_SERVICE.run(request)

    assert result.passed is False
    assert result.next_action == "manual_review"
    assert result.next_sections == []
    assert result.unresolved_items[0].target_segment_ids == [
        "asr_0011",
        "asr_0012",
    ]


def test_whole_recheck_reader_restores_location_and_current_text():
    result = whole_recheck.DEFAULT_WHOLE_RECHECK_SERVICE.run(_request())
    item = result.unresolved_items[0]
    reader_item = whole_recheck.reader_unresolved_items(result)[0]

    assert item.segment_id == "asr_0002"
    assert reader_item["meta"] == "1秒 – 2秒"
    assert {
        fact["label"]: fact["value"] for fact in reader_item["facts"]
    } == {
        "当前听写原文": "The model costs three dollars.",
        "处理方式": "流程继续，建议完成后复听",
        "片段编号": "asr_0002",
    }


def test_whole_recheck_cannot_finish_after_partial_upstream():
    request = _request().model_copy(
        update={"upstream_status": "partial"}
    )

    result = whole_recheck.DEFAULT_WHOLE_RECHECK_SERVICE.run(request)

    assert result.passed is False
    assert result.next_action == "manual_review"
    assert result.status == "partial"
    assert any("不能直接结束" in item for item in result.warnings)


def test_whole_recheck_rejects_invalid_decisions_without_retrying():
    invalid = _request().decisions[1].model_copy(
        update={"outcome": "invalid"}
    )
    request = _request().model_copy(
        update={"decisions": [_request().decisions[0], invalid]}
    )

    result = whole_recheck.DEFAULT_WHOLE_RECHECK_SERVICE.run(request)

    assert result.passed is False
    assert result.next_action == "manual_review"
    assert result.llm_calls == []
