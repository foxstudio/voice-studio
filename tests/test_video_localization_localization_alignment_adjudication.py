from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_alignment_adjudication,
    localization_alignment_request,
    localization_semantic_alignment,
)
from app.services import llm_runtime  # noqa: E402
from app.services.localization_ai_policy import (  # noqa: E402
    LocalizationAiPhaseRoute,
)
from tests.test_video_localization_localization_semantic_alignment_v3 import (  # noqa: E402
    _FixtureEncoder,
    _request,
)


def _ambiguous_fixture():
    request = _request(
        [("paragraph_0001", "中文甲"), ("paragraph_0002", "中文乙")],
        [
            ("cue_0001", "source a"),
            ("cue_0002", "bridge"),
            ("cue_0003", "source b"),
            ("cue_0004", "source b detail"),
        ],
    )
    encoder = _FixtureEncoder(
        {
            "中文甲": [1.0, 0.0, 0.0],
            "中文乙": [0.0, 1.0, 0.0],
            "source a": [1.0, 0.0, 0.0],
            "bridge": [0.0, 0.0, 1.0],
            "source b": [0.0, 1.0, 0.0],
            "source b detail": [0.0, 0.9, 0.1],
        }
    )
    alignment = localization_semantic_alignment.align_localized_script(
        request,
        encoder=encoder,
    )
    alignment = alignment.model_copy(
        update={
            "blocks": [
                alignment.blocks[0].model_copy(
                    update={"needs_adjudication": True}
                ),
                *alignment.blocks[1:],
            ]
        }
    )
    route = LocalizationAiPhaseRoute(
        phase="alignment_adjudication",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    return localization_alignment_adjudication.LocalizationAlignmentAdjudicationInput(
        alignment_operation_id="alignment_operation",
        alignment=alignment,
        source_cues=request.source_cues,
        target_paragraphs=request.target_paragraphs,
        route=route,
    )


def test_alignment_adjudication_only_accepts_bounded_split_candidates(
    monkeypatch,
):
    request = _ambiguous_fixture()
    packets = (
        localization_alignment_adjudication
        .build_alignment_boundary_packets(request)
    )
    assert len(packets) == 1
    selected = packets[0].candidates[1]

    captured = {}

    def complete_json(system_prompt, payload, trace_sink, **_kwargs):
        captured["system_prompt"] = system_prompt
        captured["payload"] = payload
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=100,
                request_body_bytes=120,
                max_tokens=4_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=20,
                finish_reason="stop",
            )
        )
        return {
            "choices": [
                {
                    "boundary_id": packets[0].boundary_id,
                    "candidate_id": selected.candidate_id,
                    "reason_zh": "这里开始进入右侧语义。",
                }
            ]
        }

    monkeypatch.setattr(
        localization_alignment_adjudication.llm_runtime,
        "complete_json",
        complete_json,
    )
    result = (
        localization_alignment_adjudication
        .adjudicate_localization_alignment(request)
    )

    assert [
        cue_id for block in result.blocks for cue_id in block.source_cue_ids
    ] == [
        "cue_0001",
        "cue_0002",
        "cue_0003",
        "cue_0004",
    ]
    assert [
        paragraph_id
        for block in result.blocks
        for paragraph_id in block.paragraph_ids
    ] == ["paragraph_0001", "paragraph_0002"]
    assert result.quality_summary.model_call_count == 1
    assert result.quality_summary.source_order_preserved is True
    projection = (
        localization_alignment_adjudication
        .project_localization_alignment_adjudication_result(result)
    )
    assert projection["sections"] == [
        {
            "title": "边界复核结果",
            "open_by_default": True,
            "items": [
                {
                    "title": "语义边界 0001",
                    "text": "这里开始进入右侧语义。",
                    "before_label": "左侧语义及对应英文结尾",
                    "before": "中文甲\n\nsource a bridge",
                    "after_label": "右侧语义及对应英文开头",
                    "after": "中文乙\n\nsource b source b detail",
                    "meta": "已采用模型选择",
                    "facts": [],
                    "links": [],
                    "tone": "positive",
                }
            ],
        }
    ]
    assert captured["system_prompt"] == (
        localization_alignment_request
        .ALIGNMENT_ADJUDICATION_SYSTEM_PROMPT
    )
    assert captured["payload"] == {
        "boundary_packets": [
            {
                "boundary_id": packets[0].boundary_id,
                "left_target_text": packets[0].left_target_text,
                "right_target_text": packets[0].right_target_text,
                "candidates": [
                    {
                        "candidate_id": item.candidate_id,
                        "left_source": item.left_tail_source,
                        "right_source": item.right_head_source,
                        "boundary_hint": item.boundary_hint,
                    }
                    for item in packets[0].candidates
                ],
            }
        ]
    }
    serialized_payload = str(captured["payload"])
    for forbidden in (
        "split_after_cue_id",
        "is_original",
        "left_block_id",
        "right_block_id",
        "source_cue_ids",
        "source_word_ids",
        "start_ms",
        "end_ms",
        "similarity",
        "fingerprint",
    ):
        assert forbidden not in serialized_payload


def test_alignment_request_contract_forbids_unlisted_fields():
    request = _ambiguous_fixture()
    packets = (
        localization_alignment_adjudication
        .build_alignment_boundary_packets(request)
    )

    model_request = (
        localization_alignment_request
        .build_alignment_adjudication_model_request(packets)
    )

    assert set(model_request.model_payload()) == {"boundary_packets"}
    boundary = model_request.model_payload()["boundary_packets"][0]
    assert set(boundary) == {
        "boundary_id",
        "left_target_text",
        "right_target_text",
        "candidates",
    }
    assert set(boundary["candidates"][0]) == {
        "candidate_id",
        "left_source",
        "right_source",
        "boundary_hint",
    }


def test_alignment_adjudication_rejects_model_declared_fallback():
    request = _ambiguous_fixture()
    packets = (
        localization_alignment_adjudication
        .build_alignment_boundary_packets(request)
    )

    with pytest.raises(ValueError, match="回退|跳过"):
        localization_alignment_adjudication._parse_choices(
            {
                "choices": [
                    {
                        "boundary_id": packets[0].boundary_id,
                        "candidate_id": packets[0].candidates[0].candidate_id,
                        "reason_zh": "尝试绕过程序校验。",
                        "applied": False,
                    }
                ]
            },
            packets,
        )


def test_alignment_adjudication_does_not_offer_cross_speaker_boundaries():
    semantic_request = _request(
        [("paragraph_0001", "中文甲"), ("paragraph_0002", "中文乙")],
        [
            ("cue_0001", "source a"),
            ("cue_0002", "source a detail"),
            ("cue_0003", "source b"),
            ("cue_0004", "source b detail"),
        ],
        source_speakers=[
            "speaker_01",
            "speaker_01",
            "speaker_02",
            "speaker_02",
        ],
    )
    encoder = _FixtureEncoder(
        {
            "中文甲": [1.0, 0.0],
            "中文乙": [0.0, 1.0],
            "source a": [1.0, 0.0],
            "source a detail": [1.0, 0.0],
            "source b": [0.0, 1.0],
            "source b detail": [0.0, 1.0],
        }
    )
    alignment = localization_semantic_alignment.align_localized_script(
        semantic_request,
        encoder=encoder,
    )
    alignment = alignment.model_copy(
        update={
            "blocks": [
                alignment.blocks[0].model_copy(
                    update={"needs_adjudication": True}
                ),
                alignment.blocks[1],
            ]
        }
    )
    request = localization_alignment_adjudication.LocalizationAlignmentAdjudicationInput(
        alignment_operation_id="alignment_operation",
        alignment=alignment,
        source_cues=semantic_request.source_cues,
        target_paragraphs=semantic_request.target_paragraphs,
        route=LocalizationAiPhaseRoute(
            phase="alignment_adjudication",
            profile_id="profile",
            model_id="model",
            reasoning_effort="low",
            output_format="json",
            prompt_strategy="adaptive",
        ),
    )

    assert (
        localization_alignment_adjudication
        .build_alignment_boundary_packets(request)
    ) == []


def test_alignment_adjudication_cannot_move_verified_anchor_to_other_block():
    semantic_request = _request(
        [("paragraph_0001", "中文甲"), ("paragraph_0002", "中文乙")],
        [
            ("cue_0001", "source a"),
            ("cue_0002", "unrecognized speech"),
            ("cue_0003", "source b"),
            ("cue_0004", "source b detail"),
        ],
    ).model_copy(
        update={
            "evidence_anchors": [
                localization_semantic_alignment
                .LocalizationSemanticAlignmentAnchor(
                    anchor_id="question_0001",
                    target_paragraph_id="paragraph_0001",
                    source_cue_ids=["cue_0002"],
                )
            ]
        }
    )
    encoder = _FixtureEncoder(
        {
            "中文甲": [1.0, 0.0, 0.0],
            "中文乙": [0.0, 1.0, 0.0],
            "source a": [1.0, 0.0, 0.0],
            "unrecognized speech": [0.0, 0.0, 1.0],
            "source b": [0.0, 1.0, 0.0],
            "source b detail": [0.0, 0.9, 0.1],
        }
    )
    alignment = localization_semantic_alignment.align_localized_script(
        semantic_request,
        encoder=encoder,
    )
    alignment = alignment.model_copy(
        update={
            "blocks": [
                alignment.blocks[0].model_copy(
                    update={"needs_adjudication": True}
                ),
                *alignment.blocks[1:],
            ]
        }
    )
    route = LocalizationAiPhaseRoute(
        phase="alignment_adjudication",
        profile_id="profile",
        model_id="model",
        reasoning_effort="low",
        output_format="json",
        prompt_strategy="adaptive",
    )
    request = (
        localization_alignment_adjudication
        .LocalizationAlignmentAdjudicationInput(
            alignment_operation_id="alignment_operation",
            alignment=alignment,
            source_cues=semantic_request.source_cues,
            target_paragraphs=semantic_request.target_paragraphs,
            route=route,
        )
    )

    packets = (
        localization_alignment_adjudication
        .build_alignment_boundary_packets(request)
    )

    assert [
        item.split_after_cue_id for item in packets[0].candidates
    ] == ["cue_0002", "cue_0003"]


def test_alignment_adjudication_skips_model_when_boundaries_are_clear():
    request = _ambiguous_fixture()
    request = request.model_copy(
        update={
            "alignment": request.alignment.model_copy(
                update={
                    "blocks": [
                        block.model_copy(
                            update={"needs_adjudication": False}
                        )
                        for block in request.alignment.blocks
                    ]
                }
            )
        }
    )

    result = (
        localization_alignment_adjudication
        .adjudicate_localization_alignment(request)
    )

    assert result.quality_summary.status == "not_needed"
    assert result.quality_summary.model_call_count == 0
    projection = (
        localization_alignment_adjudication
        .project_localization_alignment_adjudication_result(result)
    )
    assert projection["sections"] == []
