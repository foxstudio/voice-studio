from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_targeted_relisten,
    section_review,
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
            speaker_cluster_id="speaker_01",
        )
        for index, text in enumerate(
            [
                "JoAnne introduces the topic.",
                "The model costs three dollars.",
                "Context before the second section.",
                "The company did not reduce demand.",
            ],
            start=1,
        )
    ]


def _request() -> section_review.AsrSectionReviewInput:
    segments = _segments()
    return section_review.AsrSectionReviewInput(
        upstream_contract_version="asr-entity-normalization-v1",
        upstream_operation_id="normalize-op",
        understanding_operation_id="understand-op",
        source_track_id="vocals",
        source_audio_sha256="audio-sha",
        language="en",
        profile_id="review-profile",
        document_summary="这是一段关于人工智能投资的访谈。",
        content_logic=["主持人提问", "嘉宾解释"],
        speaker_style="专业访谈",
        sections=[
            {
                "section_id": "S1",
                "start_ordinal": 1,
                "end_ordinal": 2,
                "start_segment_id": "asr_0001",
                "end_segment_id": "asr_0002",
                "role": "开场",
                "focus": ["核对名称和数字"],
            },
            {
                "section_id": "S2",
                "start_ordinal": 3,
                "end_ordinal": 4,
                "start_segment_id": "asr_0003",
                "end_segment_id": "asr_0004",
                "role": "结论",
                "focus": ["核对否定关系"],
            },
        ],
        segments=segments,
    )


def test_second_round_accepts_only_the_planned_target_range(monkeypatch):
    base = _request()
    request = base.model_copy(
        update={
            "round_index": 2,
            "sections": [base.sections[1]],
        },
        deep=True,
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {"issues": []},
    )

    result = section_review.DEFAULT_SECTION_REVIEW_SERVICE.run(request)

    assert [run.section_id for run in result.section_runs] == ["S2"]
    assert result.quality_summary.sections_cover_all_segments is False
    assert result.quality_summary.checked_section_count == 1


def test_second_round_exposes_short_audio_relisten_as_bounded_evidence(
    monkeypatch,
):
    payloads: list[dict] = []
    base = _request()
    request = base.model_copy(
        update={
            "round_index": 2,
            "sections": [base.sections[1]],
            "acoustic_candidates": [
                asr_targeted_relisten.AsrAcousticCandidate(
                    section_id="S2",
                    start_ms=2_200,
                    end_ms=4_000,
                    text=(
                        "The company did not reduce demand. "
                        "Now let's speed it up."
                    ),
                )
            ],
        },
        deep=True,
    )

    def fake_complete_json(*_args, **kwargs):
        payloads.append(kwargs["user_payload"])
        return {"issues": []}

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)

    result = section_review.DEFAULT_SECTION_REVIEW_SERVICE.run(request)

    assert result.quality_summary.acoustic_candidate_count == 1
    assert payloads[0]["short_audio_relisten"] == [
        {
            "issue_ids": [],
            "start_ms": 2_200,
            "end_ms": 4_000,
            "text": (
                "The company did not reduce demand. "
                "Now let's speed it up."
            ),
        }
    ]


def test_section_review_is_read_only_and_filters_ungrounded_issues(
    monkeypatch,
):
    payloads: list[dict] = []

    def fake_complete_json(*_args, **kwargs):
        payload = kwargs["user_payload"]
        payloads.append(payload)
        section_id = payload["section"]["section_id"]
        if section_id == "S1":
            return {
                "issues": [
                    {
                        "segment_id": "asr_0002",
                        "current_excerpt": "three dollars",
                        "replacement": "$3",
                        "reason": "数字表达可能需要结合原音确认。",
                        "confidence": 0.72,
                        "needs_confirmation": True,
                        "evidence_source_ids": ["missing-evidence"],
                    },
                    {
                        "segment_id": "asr_0004",
                        "current_excerpt": "not reduce",
                        "replacement": "",
                        "reason": "上下文片段不能由本区块提出修改。",
                        "confidence": 0.9,
                    },
                ]
            }
        return {
            "issues": [
                {
                    "segment_id": "asr_0004",
                    "current_excerpt": "invented excerpt",
                    "replacement": "increase",
                    "reason": "模型生成了原文中不存在的引用。",
                    "confidence": 0.8,
                }
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)
    request = _request()
    result = section_review.DEFAULT_SECTION_REVIEW_SERVICE.run(request)

    assert result.status == "completed"
    assert result.quality_summary.status == "warning"
    assert result.input == request
    assert len(result.section_runs) == 2
    assert result.quality_summary.checked_section_count == 2
    assert result.quality_summary.sections_cover_all_segments is True
    assert result.quality_summary.source_text_unchanged is True
    assert [item.segment_id for item in result.issues] == ["asr_0002"]
    assert result.issues[0].evidence_source_ids == []
    assert result.issues[0].needs_confirmation is True
    assert [item.code for item in result.warnings] == [
        "invalid_issue",
        "invalid_issue",
    ]
    assert len(payloads) == 2
    assert all(len(payload["context_before"]) <= 6 for payload in payloads)
    assert all(len(payload["context_after"]) <= 6 for payload in payloads)
    assert all(
        set(segment) == {"segment_id", "text", "speaker_cluster_id"}
        for payload in payloads
        for segment in payload["core_segments"]
    )
    assert set(payloads[0]["output"]["issues"][0]) == {
        "segment_id",
        "current_excerpt",
        "replacement",
        "reason",
        "confidence",
        "needs_confirmation",
        "evidence_source_ids",
        "patches",
    }


def test_section_review_keeps_successful_sections_when_one_fails(
    monkeypatch,
):
    def fake_complete_json(*_args, **kwargs):
        if kwargs["user_payload"]["section"]["section_id"] == "S1":
            raise llm_runtime.LlmRuntimeError(
                "provider unavailable",
                code="llm_provider_unavailable",
                status_code=503,
            )
        return {"issues": []}

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)
    result = section_review.DEFAULT_SECTION_REVIEW_SERVICE.run(_request())

    assert result.status == "partial"
    assert result.quality_summary.checked_section_count == 1
    assert [item.status for item in result.section_runs] == [
        "failed",
        "completed",
    ]
    assert [item.code for item in result.warnings] == ["section_failed"]


def test_section_review_requires_continuous_full_coverage():
    request = _request()
    payload = request.model_dump(mode="json")
    payload["sections"] = [
        {
            "section_id": "S1",
            "start_ordinal": 2,
            "end_ordinal": 4,
            "start_segment_id": "asr_0002",
            "end_segment_id": "asr_0004",
            "role": "不完整范围",
            "focus": [],
        }
    ]
    with pytest.raises(ValidationError):
        section_review.AsrSectionReviewInput.model_validate(payload)


def test_invalid_model_suggestion_is_debug_only_not_reader_actionable():
    assert (
        section_review.warning_needs_reader_review(
            code="invalid_issue",
            message="S2 返回了一条无法定位到原文的建议，已忽略。",
        )
        is False
    )
    assert (
        section_review.warning_needs_reader_review(
            code="section_failed",
            message="S2 没有完成检查。",
        )
        is True
    )


def test_section_review_keeps_high_confidence_cross_segment_issue_when_llm_misses_it(
    monkeypatch,
):
    segments = [
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0011",
            start_ms=54_449,
            end_ms=59_385,
            raw_text=(
                "But now people ask what about those model companies because"
            ),
        ),
        VideoLocalizationTranscriptSegment(
            segment_id="asr_0012",
            start_ms=59_385,
            end_ms=64_876,
            raw_text=(
                "Although most of them are not public, we see more of them."
            ),
        ),
    ]
    request = section_review.AsrSectionReviewInput(
        upstream_contract_version="asr-entity-normalization-v1",
        upstream_operation_id="normalize-op",
        understanding_operation_id="understand-op",
        source_track_id="vocals",
        source_audio_sha256="audio-sha",
        language="en",
        profile_id="review-profile",
        document_summary="一段关于模型公司的访谈。",
        sections=[
            {
                "section_id": "S1",
                "start_ordinal": 1,
                "end_ordinal": 2,
                "start_segment_id": "asr_0011",
                "end_segment_id": "asr_0012",
                "role": "模型公司",
                "focus": ["检查跨片段衔接"],
            }
        ],
        segments=segments,
    )
    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda *_args, **_kwargs: {"issues": []},
    )

    result = section_review.DEFAULT_SECTION_REVIEW_SERVICE.run(request)

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.origin == "deterministic_boundary_rule"
    assert issue.segment_id == "asr_0011"
    assert [item.segment_id for item in issue.patches] == [
        "asr_0011",
        "asr_0012",
    ]


def test_section_review_keeps_grounded_text_rule_candidates_when_llm_misses_them(
    monkeypatch,
):
    request = _request().model_copy(
        update={
            "segments": [
                VideoLocalizationTranscriptSegment(
                    segment_id="asr_0001",
                    start_ms=0,
                    end_ms=1_000,
                    raw_text="The the price is high.",
                ),
                VideoLocalizationTranscriptSegment(
                    segment_id="asr_0002",
                    start_ms=1_000,
                    end_ms=2_000,
                    raw_text="The price may fall.",
                ),
                *_segments()[2:],
            ]
        },
        deep=True,
    )
    payloads: list[dict] = []

    def fake_complete_json(*_args, **kwargs):
        payloads.append(kwargs["user_payload"])
        return {"issues": []}

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)

    result = section_review.DEFAULT_SECTION_REVIEW_SERVICE.run(request)

    issue = next(
        item
        for item in result.issues
        if item.origin == "deterministic_text_rule"
    )
    assert issue.current_excerpt == "The the"
    assert issue.proposed_replacement == "The"
    assert issue.segment_id == "asr_0001"
    assert any(
        payload["deterministic_text_candidates"]
        for payload in payloads
        if payload["section"]["section_id"] == "S1"
    )


def test_section_review_accepts_grounded_llm_issue_across_adjacent_segments(
    monkeypatch,
):
    request = section_review.AsrSectionReviewInput(
        upstream_contract_version="asr-entity-normalization-v1",
        upstream_operation_id="normalize-op",
        understanding_operation_id="understand-op",
        source_track_id="vocals",
        source_audio_sha256="audio-sha",
        language="en",
        profile_id="review-profile",
        document_summary="一段连续讲话。",
        sections=[
            {
                "section_id": "S1",
                "start_ordinal": 1,
                "end_ordinal": 1,
                "start_segment_id": "asr_0001",
                "end_segment_id": "asr_0001",
                "role": "前半句",
                "focus": ["检查与下一片段的连接"],
            },
            {
                "section_id": "S2",
                "start_ordinal": 2,
                "end_ordinal": 2,
                "start_segment_id": "asr_0002",
                "end_segment_id": "asr_0002",
                "role": "后半句",
                "focus": ["检查句子结尾"],
            },
        ],
        segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0001",
                start_ms=0,
                end_ms=1_000,
                raw_text="We should stop and",
            ),
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0002",
                start_ms=1_000,
                end_ms=2_000,
                raw_text="Then continue.",
            ),
        ],
    )

    def fake_complete_json(*_args, **kwargs):
        if kwargs["user_payload"]["section"]["section_id"] == "S2":
            return {"issues": []}
        return {
            "issues": [
                {
                    "segment_id": "asr_0001",
                    "scope": "adjacent_segments",
                    "target_segment_ids": ["asr_0001", "asr_0002"],
                    "current_excerpt": "and | Then",
                    "replacement": "and then |",
                    "patches": [
                        {
                            "segment_id": "asr_0001",
                            "current_excerpt": "and",
                            "proposed_replacement": "and then",
                        },
                        {
                            "segment_id": "asr_0002",
                            "current_excerpt": "Then",
                            "proposed_replacement": "",
                        },
                    ],
                    "reason": "同一个连接词被片段边界拆成了重复结构。",
                    "confidence": 0.9,
                }
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", fake_complete_json)

    result = section_review.DEFAULT_SECTION_REVIEW_SERVICE.run(request)

    assert len(result.issues) == 1
    assert result.issues[0].origin == "llm_section_review"
    assert result.issues[0].scope == "adjacent_segments"
    assert result.issues[0].target_segment_ids == [
        "asr_0001",
        "asr_0002",
    ]


def test_parallel_section_call_ids_do_not_leak_between_similar_ids(
    monkeypatch,
):
    request = _request()
    payload = request.model_dump(mode="json")
    payload["sections"][0]["section_id"] = "S"
    payload["sections"][1]["section_id"] = "S-child"
    request = section_review.AsrSectionReviewInput.model_validate(
        payload
    )

    def fake_complete_json(*_args, **kwargs):
        section_id = kwargs["user_payload"]["section"]["section_id"]
        kwargs["trace_sink"](
            llm_runtime.LlmCompletionTrace(
                profile_id="review-profile",
                model_id="review-model",
                provider_host="127.0.0.1",
                request_chars=10,
                request_body_bytes=20,
                max_tokens=kwargs["max_tokens"],
                timeout_seconds=kwargs["timeout"],
                reasoning_effort_requested=None,
                reasoning_control_applied=True,
                duration_ms=5,
                finish_reason="stop",
                content_chars=2,
                reasoning_chars=0,
                response_id=section_id,
            )
        )
        return {"issues": []}

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        fake_complete_json,
    )
    result = section_review.DEFAULT_SECTION_REVIEW_SERVICE.run(
        request
    )

    assert [item.llm_call_ids for item in result.section_runs] == [
        ["section-review-r1-S-a01"],
        ["section-review-r1-S-child-a01"],
    ]
    assert [item.call_id for item in result.llm_calls] == [
        "section-review-r1-S-a01",
        "section-review-r1-S-child-a01",
    ]
