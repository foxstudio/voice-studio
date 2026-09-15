from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


from app.domains.video_localization import asr_flow  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationTranscriptSegment,
)
from app.services import llm_runtime  # noqa: E402


def _segments(count: int) -> list[VideoLocalizationTranscriptSegment]:
    return [
        VideoLocalizationTranscriptSegment(
            segment_id=f"asr_{index:04d}",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            raw_text=f"Segment {index}.",
        )
        for index in range(1, count + 1)
    ]


def _brief(segment_count: int) -> dict:
    return {
        "summary": "创作者讲解一套视频制作流程。",
        "logic": ["先介绍目标", "再演示操作"],
        "speaker_style": "第一人称教程讲解。",
        "entities": [],
        "search_queries": [],
        "visual_questions": [],
        "sections": [
            {
                "id": "S1",
                "start_segment": 1,
                "end_segment": segment_count,
                "role": "完整教程",
                "focus": ["核对步骤和关键名词"],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _resolved_profile(monkeypatch):
    monkeypatch.setattr(
        llm_runtime,
        "resolve_profile",
        lambda _profile_id=None: SimpleNamespace(
            profile_id="review-profile",
            model_id="review-model",
        ),
    )


def test_gateway_receives_stable_logical_call_and_attempt_identity() -> None:
    requests: list[asr_flow.AsrDocumentUnderstandingCompletionRequest] = []

    def complete(
        request: asr_flow.AsrDocumentUnderstandingCompletionRequest,
    ) -> dict:
        requests.append(request)
        if request.attempt == 1:
            raise llm_runtime.LlmRuntimeError(
                "invalid JSON",
                code="llm_json_invalid",
                status_code=502,
            )
        return _brief(1)

    result = asr_flow.understand_document(
        _segments(1),
        language="en",
        scene_context="locked scene context",
        profile_id="review-profile",
        is_cancelled=None,
        completion_gateway=complete,
    )

    assert result.llm_call_count == 2
    assert result.retry_count == 1
    assert [request.call_id for request in requests] == [
        "understand_document:full_document",
        "understand_document:full_document",
    ]
    assert [request.attempt for request in requests] == [1, 2]
    assert requests[0].contract_version == ("asr-document-understanding-call-input-v1")
    assert requests[0].behavior_version == asr_flow.PROMPT_VERSION
    assert requests[0].disable_reasoning is False
    assert requests[1].disable_reasoning is True
    assert requests[1].max_tokens == 12_000
    assert requests[0].user_payload["scene_context"] == ("locked scene context")


def test_gateway_exposes_window_and_merge_calls_without_copying_algorithm() -> None:
    call_ids: list[str] = []

    def complete(
        request: asr_flow.AsrDocumentUnderstandingCompletionRequest,
    ) -> dict:
        call_ids.append(request.call_id)
        if request.call_id == "understand_document:full_document":
            raise llm_runtime.LlmRuntimeError(
                "input too long",
                code="llm_context_too_long",
                status_code=400,
            )
        return _brief(4)

    result = asr_flow.understand_document(
        _segments(4),
        language="en",
        scene_context="",
        profile_id="review-profile",
        is_cancelled=None,
        completion_gateway=complete,
    )

    assert result.execution_strategy == "windowed"
    assert call_ids == [
        "understand_document:full_document",
        "understand_document:window_1",
        "understand_document:window_merge",
    ]


def test_gateway_exposes_section_replan_as_independent_call() -> None:
    call_ids: list[str] = []

    def complete(
        request: asr_flow.AsrDocumentUnderstandingCompletionRequest,
    ) -> dict:
        call_ids.append(request.call_id)
        if request.call_id == "understand_document:section_replan":
            return {
                "sections": [
                    {
                        "id": "S1",
                        "start_segment": 1,
                        "end_segment": 35,
                        "role": "开场",
                        "focus": ["核对背景"],
                    },
                    {
                        "id": "S2",
                        "start_segment": 36,
                        "end_segment": 70,
                        "role": "主体",
                        "focus": ["核对步骤"],
                    },
                    {
                        "id": "S3",
                        "start_segment": 71,
                        "end_segment": 100,
                        "role": "收束",
                        "focus": ["核对结论"],
                    },
                ]
            }
        return _brief(100)

    result = asr_flow.understand_document(
        _segments(100),
        language="en",
        scene_context="",
        profile_id="review-profile",
        is_cancelled=None,
        completion_gateway=complete,
    )

    assert len(result.brief["sections"]) == 3
    assert call_ids == [
        "understand_document:full_document",
        "understand_document:section_replan",
    ]
