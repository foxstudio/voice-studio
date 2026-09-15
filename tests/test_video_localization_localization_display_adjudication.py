from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    localization_alignment_adjudication,
    localization_display_adjudication,
    localization_dual_tracks,
)
from app.domains.video_localization.localization_source import (  # noqa: E402
    LocalizationSourceWord,
)
from app.services import llm_runtime  # noqa: E402
from app.services.localization_ai_policy import (  # noqa: E402
    LocalizationAiPhaseRoute,
)


class _KeywordEncoder:
    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for text in texts:
            first = float("第一" in text or "first" in text or "topic" in text)
            second = float("第二" in text or "second" in text or "detail" in text)
            vector = np.asarray([first, second], dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            vectors.append(vector / norm if norm else np.asarray([0.5, 0.5]))
        return np.vstack(vectors)


def _word(index: int, text: str) -> LocalizationSourceWord:
    return LocalizationSourceWord(
        word_id=f"word_{index:04d}",
        segment_id="segment_0001",
        text=text,
        start_ms=(index - 1) * 1_000,
        end_ms=index * 1_000,
        timing_confidence="high",
        timing_source="forced_aligner",
    )


def _dual_track() -> localization_dual_tracks.LocalizationDualTrackResult:
    display_cues = [
        localization_dual_tracks.LocalizationDisplaySubtitleCue(
            cue_id="localized_cue_0001",
            paragraph_id="paragraph_0001",
            text="第一件事",
            tts_text="第一件事",
            start_ms=0,
            end_ms=3_000,
            source_cue_ids=["source_cue_0001"],
            source_word_ids=["word_0001", "word_0002", "word_0003"],
        ),
        localization_dual_tracks.LocalizationDisplaySubtitleCue(
            cue_id="localized_cue_0002",
            paragraph_id="paragraph_0001",
            text="第二件事",
            tts_text="第二件事",
            start_ms=3_000,
            end_ms=4_000,
            source_cue_ids=["source_cue_0001"],
            source_word_ids=["word_0004"],
        ),
    ]
    return localization_dual_tracks.LocalizationDualTrackResult(
        spoken_script_fingerprint="a" * 64,
        alignment_fingerprint="b" * 64,
        result_fingerprint="c" * 64,
        spoken_segments=[
            localization_dual_tracks.LocalizationSpokenTrackSegment(
                segment_id="spoken_segment_0001",
                paragraph_id="paragraph_0001",
                text="第一件事，第二件事",
                tts_text="第一件事，第二件事",
                semantic_start_ms=0,
                semantic_end_ms=4_000,
                source_cue_ids=["source_cue_0001"],
                source_word_ids=[
                    "word_0001",
                    "word_0002",
                    "word_0003",
                    "word_0004",
                ],
            )
        ],
        display_cues=display_cues,
        quality_summary=(
            localization_dual_tracks.LocalizationDualTrackQualitySummary(
                status="passed",
                spoken_segment_count=1,
                display_cue_count=2,
                paragraph_coverage_complete=True,
                source_order_preserved=True,
                display_timing_ordered=True,
                overlong_cue_count=0,
            )
        ),
    )


def _request():
    return localization_display_adjudication.LocalizationDisplayAdjudicationInput(
        dual_tracks_operation_id="dual-track-operation",
        dual_tracks=_dual_track(),
        source_words=[
            _word(1, "first"),
            _word(2, "topic"),
            _word(3, "second"),
            _word(4, "detail"),
        ],
        route=LocalizationAiPhaseRoute(
            phase="alignment_adjudication",
            profile_id="profile",
            model_id="model",
            reasoning_effort="low",
            output_format="json",
            prompt_strategy="adaptive",
        ),
    )


def test_display_adjudication_lets_model_choose_only_a_bounded_split(monkeypatch):
    request = _request()
    packets = localization_display_adjudication.build_display_boundary_packets(
        request,
        encoder=_KeywordEncoder(),
    )
    assert len(packets) == 1
    selected = next(item for item in packets[0].candidates if item.split_after_word_id == "word_0002")
    captured = {}

    def complete_json(_system_prompt, payload, trace_sink, **_kwargs):
        captured["payload"] = payload
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=80,
                request_body_bytes=100,
                max_tokens=4_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=10,
                finish_reason="stop",
            )
        )
        return {
            "choices": [
                {
                    "boundary_id": packets[0].boundary_id,
                    "candidate_id": selected.candidate_id,
                    "reason_zh": "第二件事从 second 开始。",
                }
            ]
        }

    monkeypatch.setattr(
        localization_alignment_adjudication.llm_runtime,
        "complete_json",
        complete_json,
    )
    result = localization_display_adjudication.adjudicate_display_boundaries(
        request,
        encoder=_KeywordEncoder(),
    )

    assert [item.text for item in result.dual_tracks.display_cues] == [
        "第一件事",
        "第二件事",
    ]
    assert result.dual_tracks.display_cues[0].source_word_ids == [
        "word_0001",
        "word_0002",
    ]
    assert result.dual_tracks.display_cues[0].end_ms == 2_000
    assert result.dual_tracks.display_cues[1].source_word_ids == [
        "word_0003",
        "word_0004",
    ]
    assert result.dual_tracks.display_cues[1].start_ms == 2_000
    assert result.quality_summary.model_call_count == 1
    assert set(captured["payload"]) == {"boundary_packets"}
    serialized = str(captured["payload"])
    for forbidden in (
        "start_ms",
        "end_ms",
        "source_word_ids",
        "similarity",
        "fingerprint",
        "is_original",
    ):
        assert forbidden not in serialized


def test_display_adjudication_keeps_original_component_for_crossed_model_boundaries():
    request = _request()
    third = localization_dual_tracks.LocalizationDisplaySubtitleCue(
        cue_id="localized_cue_0003",
        paragraph_id="paragraph_0001",
        text="第三件事",
        tts_text="第三件事",
        start_ms=4_000,
        end_ms=5_000,
        source_cue_ids=["source_cue_0001"],
        source_word_ids=["word_0005"],
    )
    request = request.model_copy(
        update={
            "source_words": [
                *request.source_words,
                _word(5, "third"),
            ],
            "dual_tracks": request.dual_tracks.model_copy(
                update={
                    "display_cues": [
                        *request.dual_tracks.display_cues,
                        third,
                    ]
                }
            ),
        }
    )
    packets = [
        localization_display_adjudication.LocalizationAlignmentBoundaryPacket(
            boundary_id="boundary_0001",
            left_block_id="localized_cue_0001",
            right_block_id="localized_cue_0002",
            left_target_text="第一件事",
            right_target_text="第二件事",
            candidates=[
                localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                    candidate_id="candidate_0001_01",
                    split_after_word_id="word_0004",
                    left_tail_source="first topic second detail",
                    right_head_source="third",
                ),
                localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                    candidate_id="candidate_0001_02",
                    split_after_word_id="word_0002",
                    left_tail_source="first topic",
                    right_head_source="second detail third",
                    is_original=True,
                ),
            ],
        ),
        localization_display_adjudication.LocalizationAlignmentBoundaryPacket(
            boundary_id="boundary_0002",
            left_block_id="localized_cue_0002",
            right_block_id="localized_cue_0003",
            left_target_text="第二件事",
            right_target_text="第三件事",
            candidates=[
                localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                    candidate_id="candidate_0002_01",
                    split_after_word_id="word_0003",
                    left_tail_source="second",
                    right_head_source="detail third",
                ),
                localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                    candidate_id="candidate_0002_02",
                    split_after_word_id="word_0004",
                    left_tail_source="second detail",
                    right_head_source="third",
                    is_original=True,
                ),
            ],
        ),
    ]
    choices = [
        localization_display_adjudication.LocalizationAlignmentBoundaryChoice(
            boundary_id="boundary_0001",
            candidate_id="candidate_0001_01",
            reason_zh="测试",
        ),
        localization_display_adjudication.LocalizationAlignmentBoundaryChoice(
            boundary_id="boundary_0002",
            candidate_id="candidate_0002_01",
            reason_zh="测试",
        ),
    ]

    dual_tracks = (
        localization_display_adjudication.apply_display_boundary_choices(
            request,
            packets,
            choices,
        )
    )
    assert [
        item.source_word_ids
        for item in dual_tracks.display_cues
    ] == [
        ["word_0001", "word_0002"],
        ["word_0003", "word_0004"],
        ["word_0005"],
    ]

    result = localization_display_adjudication._build_result(
        request,
        packets=packets,
        choices=choices,
        calls=[],
    )
    assert result.quality_summary.status == "warning"
    assert all(not item.applied for item in result.choices)
    assert all(item.fallback_reason for item in result.choices)


def test_display_adjudication_preserves_unreviewed_single_cue_paragraph_windows():
    request = _request()
    shared_word_ids = ["word_0005", "word_0006"]
    untouched = [
        localization_dual_tracks.LocalizationDisplaySubtitleCue(
            cue_id="localized_cue_0003",
            paragraph_id="paragraph_0002",
            text="啊！",
            tts_text="啊！",
            start_ms=4_000,
            end_ms=5_000,
            source_cue_ids=["source_cue_0002"],
            source_word_ids=shared_word_ids,
        ),
        localization_dual_tracks.LocalizationDisplaySubtitleCue(
            cue_id="localized_cue_0004",
            paragraph_id="paragraph_0003",
            text="啊！",
            tts_text="啊！",
            start_ms=5_000,
            end_ms=6_000,
            source_cue_ids=["source_cue_0002"],
            source_word_ids=shared_word_ids,
        ),
    ]
    request = request.model_copy(
        update={
            "source_words": [
                *request.source_words,
                _word(5, "ah"),
                _word(6, "ah"),
            ],
            "dual_tracks": request.dual_tracks.model_copy(
                update={
                    "display_cues": [
                        *request.dual_tracks.display_cues,
                        *untouched,
                    ]
                }
            ),
        }
    )
    packet = localization_display_adjudication.LocalizationAlignmentBoundaryPacket(
        boundary_id="boundary_0001",
        left_block_id="localized_cue_0001",
        right_block_id="localized_cue_0002",
        left_target_text="第一件事",
        right_target_text="第二件事",
        candidates=[
            localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                candidate_id="candidate_0001_01",
                split_after_word_id="word_0003",
                left_tail_source="first topic second",
                right_head_source="detail",
                is_original=True,
            ),
            localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                candidate_id="candidate_0001_02",
                split_after_word_id="word_0002",
                left_tail_source="first topic",
                right_head_source="second detail",
            ),
        ],
    )
    choice = localization_display_adjudication.LocalizationAlignmentBoundaryChoice(
        boundary_id=packet.boundary_id,
        candidate_id="candidate_0001_01",
        reason_zh="保留当前边界。",
    )

    result = localization_display_adjudication.apply_display_boundary_choices(
        request,
        [packet],
        [choice],
    )

    assert [
        (item.start_ms, item.end_ms, item.source_word_ids)
        for item in result.display_cues[-2:]
    ] == [
        (4_000, 5_000, shared_word_ids),
        (5_000, 6_000, shared_word_ids),
    ]
    assert all(
        left.end_ms <= right.start_ms
        for left, right in zip(result.display_cues, result.display_cues[1:])
    )


def test_display_adjudication_skips_clear_balanced_boundary():
    request = _request()
    request = request.model_copy(
        update={
            "dual_tracks": request.dual_tracks.model_copy(
                update={
                    "display_cues": [
                        request.dual_tracks.display_cues[0].model_copy(
                            update={
                                "end_ms": 2_000,
                                "source_word_ids": [
                                    "word_0001",
                                    "word_0002",
                                ],
                            }
                        ),
                        request.dual_tracks.display_cues[1].model_copy(
                            update={
                                "start_ms": 2_000,
                                "source_word_ids": [
                                    "word_0003",
                                    "word_0004",
                                ],
                            }
                        ),
                    ]
                }
            )
        }
    )

    packets = localization_display_adjudication.build_display_boundary_packets(
        request,
        encoder=_KeywordEncoder(),
    )

    assert packets == []


def test_display_adjudication_resumes_after_the_last_completed_paid_batch(
    monkeypatch,
):
    request = _request()
    third = localization_dual_tracks.LocalizationDisplaySubtitleCue(
        cue_id="localized_cue_0003",
        paragraph_id="paragraph_0001",
        text="第三件事",
        tts_text="第三件事",
        start_ms=4_000,
        end_ms=5_000,
        source_cue_ids=["source_cue_0001"],
        source_word_ids=["word_0005"],
    )
    request = request.model_copy(
        update={
            "source_words": [*request.source_words, _word(5, "third")],
            "dual_tracks": request.dual_tracks.model_copy(
                update={
                    "display_cues": [
                        *request.dual_tracks.display_cues,
                        third,
                    ]
                }
            ),
            "policy": (
                localization_display_adjudication.LocalizationDisplayAdjudicationPolicy(maximum_packets_per_call=1)
            ),
        }
    )
    packets = [
        localization_display_adjudication.LocalizationAlignmentBoundaryPacket(
            boundary_id="boundary_0001",
            left_block_id="localized_cue_0001",
            right_block_id="localized_cue_0002",
            left_target_text="第一件事",
            right_target_text="第二件事",
            candidates=[
                localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                    candidate_id="candidate_0001_01",
                    split_after_word_id="word_0002",
                    left_tail_source="first topic",
                    right_head_source="second detail",
                ),
                localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                    candidate_id="candidate_0001_02",
                    split_after_word_id="word_0003",
                    left_tail_source="first topic second",
                    right_head_source="detail",
                    is_original=True,
                ),
            ],
        ),
        localization_display_adjudication.LocalizationAlignmentBoundaryPacket(
            boundary_id="boundary_0002",
            left_block_id="localized_cue_0002",
            right_block_id="localized_cue_0003",
            left_target_text="第二件事",
            right_target_text="第三件事",
            candidates=[
                localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                    candidate_id="candidate_0002_01",
                    split_after_word_id="word_0003",
                    left_tail_source="second",
                    right_head_source="detail third",
                ),
                localization_display_adjudication.LocalizationAlignmentBoundaryCandidate(
                    candidate_id="candidate_0002_02",
                    split_after_word_id="word_0004",
                    left_tail_source="second detail",
                    right_head_source="third",
                    is_original=True,
                ),
            ],
        ),
    ]
    monkeypatch.setattr(
        localization_display_adjudication,
        "build_display_boundary_packets",
        lambda *_args, **_kwargs: packets,
    )
    checkpoints = []
    first_run_calls = []

    def fail_second_batch(_system_prompt, payload, trace_sink, **_kwargs):
        boundary = payload["boundary_packets"][0]
        first_run_calls.append(boundary["boundary_id"])
        if len(first_run_calls) == 2:
            raise RuntimeError("simulated provider interruption")
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=80,
                request_body_bytes=100,
                max_tokens=4_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=10,
                finish_reason="stop",
            )
        )
        return {
            "choices": [
                {
                    "boundary_id": boundary["boundary_id"],
                    "candidate_id": boundary["candidates"][1]["candidate_id"],
                    "reason_zh": "保留当前边界。",
                }
            ]
        }

    monkeypatch.setattr(
        localization_alignment_adjudication.llm_runtime,
        "complete_json",
        fail_second_batch,
    )
    with pytest.raises(RuntimeError, match="interruption"):
        localization_display_adjudication.adjudicate_display_boundaries(
            request,
            encoder=_KeywordEncoder(),
            on_batch_checkpoint=checkpoints.append,
        )
    assert checkpoints[-1].completed_packet_ids == ["boundary_0001"]

    resumed_calls = []

    def finish_remaining(_system_prompt, payload, trace_sink, **_kwargs):
        boundary = payload["boundary_packets"][0]
        resumed_calls.append(boundary["boundary_id"])
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=80,
                request_body_bytes=100,
                max_tokens=4_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=10,
                finish_reason="stop",
            )
        )
        return {
            "choices": [
                {
                    "boundary_id": boundary["boundary_id"],
                    "candidate_id": boundary["candidates"][1]["candidate_id"],
                    "reason_zh": "保留当前边界。",
                }
            ]
        }

    monkeypatch.setattr(
        localization_alignment_adjudication.llm_runtime,
        "complete_json",
        finish_remaining,
    )
    result = localization_display_adjudication.adjudicate_display_boundaries(
        request,
        encoder=_KeywordEncoder(),
        resume_checkpoint=checkpoints[-1],
    )

    assert resumed_calls == ["boundary_0002"]
    assert result.quality_summary.model_call_count == 2


def test_display_adjudication_discards_stale_batch_checkpoint(monkeypatch):
    request = _request()
    packets = localization_display_adjudication.build_display_boundary_packets(
        request,
        encoder=_KeywordEncoder(),
    )
    calls = []

    def complete_current(_system_prompt, payload, trace_sink, **_kwargs):
        boundary = payload["boundary_packets"][0]
        calls.append(boundary["boundary_id"])
        trace_sink(
            llm_runtime.LlmCompletionTrace(
                profile_id="profile",
                model_id="model",
                provider_host="local",
                request_chars=80,
                request_body_bytes=100,
                max_tokens=4_000,
                timeout_seconds=300,
                reasoning_effort_requested="low",
                reasoning_control_applied=True,
                duration_ms=10,
                finish_reason="stop",
            )
        )
        return {
            "choices": [
                {
                    "boundary_id": boundary["boundary_id"],
                    "candidate_id": boundary["candidates"][0]["candidate_id"],
                    "reason_zh": "使用当前输入重新判断。",
                }
            ]
        }

    stale = localization_display_adjudication.LocalizationDisplayAdjudicationBatchCheckpoint(
        input_fingerprint="f" * 64,
        completed_packet_ids=[packets[0].boundary_id],
        choices=[
            localization_alignment_adjudication.LocalizationAlignmentBoundaryChoice(
                boundary_id=packets[0].boundary_id,
                candidate_id=packets[0].candidates[-1].candidate_id,
                reason_zh="旧输入的选择。",
            )
        ],
        llm_calls=[],
    )
    monkeypatch.setattr(
        localization_alignment_adjudication.llm_runtime,
        "complete_json",
        complete_current,
    )

    result = localization_display_adjudication.adjudicate_display_boundaries(
        request,
        encoder=_KeywordEncoder(),
        resume_checkpoint=stale,
    )

    assert calls == [packets[0].boundary_id]
    assert result.choices[0].reason_zh == "使用当前输入重新判断。"
