"""Bounded semantic adjudication for display-subtitle word boundaries.

The dual-track builder remains deterministic and owns the initial monotonic
path.  This node only sends adjacent target meanings and a few source-text
split candidates to the configured adjudication model.  The model never sees
timestamps or internal identifiers and cannot rewrite subtitle text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization import quality_gate
from app.domains.video_localization.development_llm_batches import DevelopmentLlmBatchReplay
from app.domains.video_localization.llm_observability import (
    VideoLocalizationLlmCallRecord,
)
from app.domains.video_localization.localization_alignment_adjudication import (
    LocalizationAlignmentBoundaryCandidate,
    LocalizationAlignmentBoundaryChoice,
    LocalizationAlignmentBoundaryPacket,
    adjudicate_alignment_boundary_packets,
)
from app.domains.video_localization.localization_alignment_request import (
    ALIGNMENT_ADJUDICATION_PROMPT_VERSION,
    ALIGNMENT_ADJUDICATION_REQUEST_CONTRACT_VERSION,
)
from app.domains.video_localization.localization_dual_tracks import (
    DEFAULT_STRONG_SOURCE_PAUSE_MS,
    LocalizationDisplaySubtitleCue,
    LocalizationDualTrackQualitySummary,
    LocalizationDualTrackResult,
    source_word_ids_span_strong_pause,
)
from app.domains.video_localization.localization_semantic_alignment import (
    LocalizationAlignmentSourceCue,
    MultilingualTextEncoder,
)
from app.domains.video_localization.localization_source import (
    LocalizationSourceWord,
)
from app.domains.video_localization.source_boundary_evidence import (
    SourceBoundaryEvidence,
    join_source_words,
)
from app.services.localization_ai_policy import LocalizationAiPhaseRoute


DISPLAY_ADJUDICATION_VERSION = "localization-display-adjudication-v1"


class LocalizationDisplayAdjudicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_similarity_threshold: float = Field(default=0.55, ge=-1, le=1)
    minimum_semantic_gain: float = Field(default=0.025, ge=0, le=0.2)
    maximum_share_difference: float = Field(default=0.30, ge=0.1, le=0.6)
    maximum_candidates: int = Field(default=5, ge=2, le=5)
    maximum_packets_per_call: int = Field(default=20, ge=1, le=20)


class LocalizationDisplayAdjudicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-display-adjudication-input-v1"] = (
        "localization-display-adjudication-input-v1"
    )
    dual_tracks_operation_id: str = Field(min_length=1)
    dual_tracks: LocalizationDualTrackResult
    source_cues: list[LocalizationAlignmentSourceCue] = Field(default_factory=list)
    source_words: list[LocalizationSourceWord] = Field(min_length=1)
    source_boundaries: list[SourceBoundaryEvidence] = Field(default_factory=list)
    route: LocalizationAiPhaseRoute
    policy: LocalizationDisplayAdjudicationPolicy = Field(default_factory=LocalizationDisplayAdjudicationPolicy)


class LocalizationDisplayAdjudicationBatchCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-display-adjudication-batches-v1"] = (
        "localization-display-adjudication-batches-v1"
    )
    input_fingerprint: str = Field(min_length=64, max_length=64)
    completed_packet_ids: list[str] = Field(default_factory=list)
    choices: list[LocalizationAlignmentBoundaryChoice] = Field(default_factory=list)
    llm_calls: list[VideoLocalizationLlmCallRecord] = Field(default_factory=list)


class LocalizationDisplayAdjudicationQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_needed", "passed", "warning"]
    reviewed_boundary_count: int = Field(ge=0)
    changed_boundary_count: int = Field(ge=0)
    fallback_boundary_count: int = Field(default=0, ge=0)
    source_order_preserved: bool
    display_text_unchanged: bool
    model_call_count: int = Field(ge=0)


class LocalizationDisplayAdjudicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["localization-display-adjudication-v1"] = DISPLAY_ADJUDICATION_VERSION
    dual_tracks_fingerprint: str = Field(min_length=64, max_length=64)
    result_fingerprint: str = Field(min_length=64, max_length=64)
    packets: list[LocalizationAlignmentBoundaryPacket] = Field(default_factory=list)
    choices: list[LocalizationAlignmentBoundaryChoice] = Field(default_factory=list)
    dual_tracks: LocalizationDualTrackResult
    route: LocalizationAiPhaseRoute
    llm_calls: list[VideoLocalizationLlmCallRecord] = Field(default_factory=list)
    quality_summary: LocalizationDisplayAdjudicationQualitySummary


class _BoundaryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    boundary_number: int
    left_cue: LocalizationDisplaySubtitleCue
    right_cue: LocalizationDisplaySubtitleCue
    positions: list[int]
    original_position: int
    candidate_start: int
    candidate_end: int


def build_display_boundary_packets(
    request: LocalizationDisplayAdjudicationInput,
    *,
    encoder: MultilingualTextEncoder,
) -> list[LocalizationAlignmentBoundaryPacket]:
    """Route uncertain boundaries, while leaving the semantic verdict to LLM."""

    _validate_input(request)
    words = request.source_words
    word_index = {item.word_id: index for index, item in enumerate(words)}
    evidence_by_position = {item.position_after_word: item for item in request.source_boundaries}
    plans = _build_boundary_plans(request, word_index=word_index)
    if not plans:
        return []

    candidate_texts: dict[tuple[int, int], tuple[str, str]] = {}
    texts: list[str] = []
    for plan in plans:
        texts.extend([plan.left_cue.text, plan.right_cue.text])
        for position in plan.positions:
            left_text = join_source_words([item.text for item in words[plan.candidate_start : position]])
            right_text = join_source_words([item.text for item in words[position : plan.candidate_end]])
            candidate_texts[(plan.boundary_number, position)] = (
                left_text,
                right_text,
            )
            texts.extend([left_text, right_text])
    vectors = _encode_unique(texts, encoder)

    packets = []
    for plan in plans:
        left_target = vectors[plan.left_cue.text]
        right_target = vectors[plan.right_cue.text]
        scored = []
        for position in plan.positions:
            left_text, right_text = candidate_texts[(plan.boundary_number, position)]
            left_score = float(np.dot(left_target, vectors[left_text]))
            right_score = float(np.dot(right_target, vectors[right_text]))
            scored.append((left_score + right_score, position, left_score, right_score))
        original = next(item for item in scored if item[1] == plan.original_position)
        best_score = max(item[0] for item in scored)
        timing_uncertain = _timing_share_is_uncertain(
            plan,
            words,
            maximum_share_difference=(request.policy.maximum_share_difference),
        )
        if not (
            min(original[2], original[3]) < request.policy.review_similarity_threshold
            or best_score - original[0] >= request.policy.minimum_semantic_gain
            or timing_uncertain
        ):
            continue

        selected_positions = _select_candidate_positions(
            scored,
            original_position=plan.original_position,
            evidence_by_position=evidence_by_position,
            maximum_candidates=request.policy.maximum_candidates,
        )
        if len(selected_positions) < 2:
            raise ValueError("待复核的上屏字幕边界没有足够的安全候选。")
        candidates = []
        for candidate_number, position in enumerate(
            selected_positions,
            start=1,
        ):
            left_text, right_text = candidate_texts[(plan.boundary_number, position)]
            evidence = evidence_by_position.get(position)
            candidates.append(
                LocalizationAlignmentBoundaryCandidate(
                    candidate_id=(f"candidate_{plan.boundary_number:04d}_{candidate_number:02d}"),
                    split_after_word_id=words[position - 1].word_id,
                    left_tail_source=_tail(left_text),
                    right_head_source=_head(right_text),
                    boundary_hint=("当前程序边界" if position == plan.original_position else _boundary_hint(evidence)),
                    is_original=position == plan.original_position,
                )
            )
        packets.append(
            LocalizationAlignmentBoundaryPacket(
                boundary_id=f"boundary_{plan.boundary_number:04d}",
                left_block_id=plan.left_cue.cue_id,
                right_block_id=plan.right_cue.cue_id,
                left_target_text=plan.left_cue.text,
                right_target_text=plan.right_cue.text,
                candidates=candidates,
            )
        )
    return packets


def adjudicate_display_boundaries(
    request: LocalizationDisplayAdjudicationInput,
    *,
    encoder: MultilingualTextEncoder,
    resume_checkpoint: LocalizationDisplayAdjudicationBatchCheckpoint | None = None,
    on_batch_checkpoint: Callable[[LocalizationDisplayAdjudicationBatchCheckpoint], None] | None = None,
    batch_journal: DevelopmentLlmBatchReplay | None = None,
) -> LocalizationDisplayAdjudicationResult:
    if request.route.phase != "alignment_adjudication":
        raise ValueError("上屏字幕语义裁决收到的模型路由阶段不正确。")
    packets = build_display_boundary_packets(request, encoder=encoder)
    input_fingerprint = _input_fingerprint(request, packets)
    if not packets:
        return _build_result(request, packets=[], choices=[], calls=[])

    choices: list[LocalizationAlignmentBoundaryChoice] = []
    calls: list[VideoLocalizationLlmCallRecord] = []
    completed_packet_ids: list[str] = []
    if (
        resume_checkpoint is not None
        and resume_checkpoint.input_fingerprint == input_fingerprint
    ):
        _validate_checkpoint(resume_checkpoint, packets, input_fingerprint)
        choices.extend(resume_checkpoint.choices)
        calls.extend(resume_checkpoint.llm_calls)
        completed_packet_ids.extend(resume_checkpoint.completed_packet_ids)

    batch_size = request.policy.maximum_packets_per_call
    for batch_number, start in enumerate(
        range(len(completed_packet_ids), len(packets), batch_size),
        start=len(completed_packet_ids) // batch_size + 1,
    ):
        batch = packets[start : start + batch_size]
        batch_choices, batch_calls = adjudicate_alignment_boundary_packets(
            batch,
            route=request.route,
            call_id_prefix=(f"localization-display-adjudication-{batch_number:03d}"),
            maximum_packets_per_call=batch_size,
            batch_journal=batch_journal,
        )
        choices.extend(batch_choices)
        calls.extend(batch_calls)
        completed_packet_ids.extend(item.boundary_id for item in batch)
        if on_batch_checkpoint is not None:
            on_batch_checkpoint(
                LocalizationDisplayAdjudicationBatchCheckpoint(
                    input_fingerprint=input_fingerprint,
                    completed_packet_ids=list(completed_packet_ids),
                    choices=list(choices),
                    llm_calls=list(calls),
                )
            )
    return _build_result(
        request,
        packets=packets,
        choices=choices,
        calls=calls,
    )


def apply_display_boundary_choices(
    request: LocalizationDisplayAdjudicationInput,
    packets: list[LocalizationAlignmentBoundaryPacket],
    choices: list[LocalizationAlignmentBoundaryChoice],
) -> LocalizationDualTrackResult:
    choices = _resolve_crossed_display_boundary_choices(
        request,
        packets,
        choices,
    )
    packet_by_left = {item.left_block_id: item for item in packets}
    choice_by_id = {item.boundary_id: item for item in choices}
    if set(choice_by_id) != {item.boundary_id for item in packets}:
        raise ValueError("上屏字幕语义裁决没有覆盖全部待判断边界。")
    words = request.source_words
    word_index = {item.word_id: index for index, item in enumerate(words)}
    cue_by_word = {word_id: cue.cue_id for cue in request.source_cues for word_id in cue.source_word_ids}
    rebuilt_by_id = {
        cue.cue_id: cue.model_copy(deep=True)
        for cue in request.dual_tracks.display_cues
    }
    for paragraph_cues in _paragraph_groups(request.dual_tracks.display_cues):
        routed_boundaries = [
            index
            for index, cue in enumerate(paragraph_cues[:-1])
            if cue.cue_id in packet_by_left
        ]
        for component_start, component_end in _consecutive_runs(
            routed_boundaries
        ):
            component_cues = paragraph_cues[
                component_start : component_end + 2
            ]
            positions = []
            for cue in component_cues[:-1]:
                packet = packet_by_left[cue.cue_id]
                choice = choice_by_id[packet.boundary_id]
                candidate = next(
                    (
                        item
                        for item in packet.candidates
                        if item.candidate_id == choice.candidate_id
                    ),
                    None,
                )
                if (
                    candidate is None
                    or candidate.split_after_word_id is None
                ):
                    raise ValueError(
                        "上屏字幕语义裁决选择了程序未提供的候选。"
                    )
                positions.append(
                    word_index[candidate.split_after_word_id] + 1
                )
            boundaries = [
                word_index[component_cues[0].source_word_ids[0]],
                *positions,
                word_index[component_cues[-1].source_word_ids[-1]] + 1,
            ]
            if any(
                left >= right
                for left, right in zip(boundaries, boundaries[1:])
            ):
                raise ValueError(
                    "上屏字幕语义裁决产生了倒序或空的逐词时间窗。"
                )
            for cue_number, (cue, left, right) in enumerate(
                zip(
                    component_cues,
                    boundaries,
                    boundaries[1:],
                )
            ):
                cue_words = words[left:right]
                source_cue_ids = list(
                    dict.fromkeys(
                        cue_by_word[item.word_id]
                        for item in cue_words
                        if item.word_id in cue_by_word
                    )
                )
                if not source_cue_ids:
                    source_cue_ids = list(cue.source_cue_ids)
                rebuilt_by_id[cue.cue_id] = cue.model_copy(
                    update={
                        "start_ms": (
                            cue.start_ms
                            if cue_number == 0
                            else _display_entry_ms(cue_words[0])
                        ),
                        "end_ms": (
                            cue.end_ms
                            if cue_number == len(component_cues) - 1
                            else cue_words[-1].end_ms
                        ),
                        "source_cue_ids": source_cue_ids,
                        "source_word_ids": [
                            item.word_id for item in cue_words
                        ],
                    }
                )
    rebuilt = [
        rebuilt_by_id[item.cue_id]
        for item in request.dual_tracks.display_cues
    ]
    evidence_by_left_word = {
        item.left_word_id: item
        for item in request.source_boundaries
    }
    rebuilt = [
        cue.model_copy(
            update={
                "quality_flags": [
                    flag
                    for flag in cue.quality_flags
                    if flag
                    != "display_strong_pause_review_required"
                ]
                + (
                    ["display_strong_pause_review_required"]
                    if source_word_ids_span_strong_pause(
                        cue.source_word_ids,
                        evidence_by_left_word=(
                            evidence_by_left_word
                        ),
                        minimum_pause_ms=(
                            DEFAULT_STRONG_SOURCE_PAUSE_MS
                        ),
                    )
                    else []
                )
            }
        )
        for cue in rebuilt
    ]
    if [item.text for item in rebuilt] != [item.text for item in request.dual_tracks.display_cues]:
        raise ValueError("上屏字幕语义裁决不能修改字幕文字。")
    if any(right.start_ms < left.end_ms for left, right in zip(rebuilt, rebuilt[1:])):
        raise ValueError("上屏字幕语义裁决产生了重叠时间。")
    payload = {
        "input": request.dual_tracks.result_fingerprint,
        "display_cues": [item.model_dump(mode="json") for item in rebuilt],
    }
    overlong = sum(
        "display_text_overlong" in item.quality_flags
        for item in rebuilt
    )
    manual_review = sum(
        "display_strong_pause_review_required"
        in item.quality_flags
        for item in rebuilt
    )
    return request.dual_tracks.model_copy(
        update={
            "result_fingerprint": _fingerprint(payload),
            "display_cues": rebuilt,
            "quality_summary": LocalizationDualTrackQualitySummary(
                **{
                    **request.dual_tracks.quality_summary.model_dump(
                        mode="python"
                    ),
                    "status": (
                        "warning"
                        if overlong or manual_review
                        else "passed"
                    ),
                    "overlong_cue_count": overlong,
                    "manual_review_cue_count": manual_review,
                }
            ),
        }
    )


def project_localization_display_adjudication_result(
    result: LocalizationDisplayAdjudicationResult,
) -> dict:
    quality = result.quality_summary
    return {
        "label": "复核上屏字幕时间",
        "order": 120,
        "status": (
            "warning"
            if quality.status == "warning"
            else "success"
        ),
        "purpose": ("只把相邻两句中文和少量英文分界候选交给模型；模型不能改字幕文字、生成时间或越过程序候选。"),
        "summary": (
            "所有字幕边界都足够明确，无需调用模型。"
            if quality.status == "not_needed"
            else (f"复核 {quality.reviewed_boundary_count} 个边界，调整 {quality.changed_boundary_count} 个。")
        ),
        "metrics": [
            {"label": "复核边界", "value": str(quality.reviewed_boundary_count)},
            {"label": "调整边界", "value": str(quality.changed_boundary_count)},
            {"label": "保留原边界", "value": str(quality.fallback_boundary_count)},
            {"label": "模型请求", "value": str(quality.model_call_count)},
        ],
        "sections": [],
        "notes": [
            "无效或漏选结果会阻断；相邻选择互相冲突时，程序保留该组原边界并明确记录。"
        ],
        "debug": {
            "description": "核对模型只收到语义判断所需的最小文字。",
            "metrics": [
                {"label": "提示词版本", "value": ALIGNMENT_ADJUDICATION_PROMPT_VERSION},
                {
                    "label": "请求契约",
                    "value": ALIGNMENT_ADJUDICATION_REQUEST_CONTRACT_VERSION,
                },
            ],
            "sections": [],
        },
    }


def _resolve_crossed_display_boundary_choices(
    request: LocalizationDisplayAdjudicationInput,
    packets: list[LocalizationAlignmentBoundaryPacket],
    choices: list[LocalizationAlignmentBoundaryChoice],
) -> list[LocalizationAlignmentBoundaryChoice]:
    packet_by_left = {item.left_block_id: item for item in packets}
    packet_by_id = {item.boundary_id: item for item in packets}
    choice_by_id = {item.boundary_id: item for item in choices}
    if set(choice_by_id) != set(packet_by_id):
        raise ValueError("上屏字幕语义裁决没有覆盖全部待判断边界。")
    word_index = {
        item.word_id: index
        for index, item in enumerate(request.source_words)
    }
    resolved_by_id = dict(choice_by_id)
    for paragraph_cues in _paragraph_groups(
        request.dual_tracks.display_cues
    ):
        routed_boundaries = [
            index
            for index, cue in enumerate(paragraph_cues[:-1])
            if cue.cue_id in packet_by_left
        ]
        for component_start, component_end in _consecutive_runs(
            routed_boundaries
        ):
            component_cues = paragraph_cues[
                component_start : component_end + 2
            ]
            component_packets = [
                packet_by_left[cue.cue_id]
                for cue in component_cues[:-1]
            ]
            positions = []
            for packet in component_packets:
                choice = choice_by_id[packet.boundary_id]
                candidate = next(
                    (
                        item
                        for item in packet.candidates
                        if item.candidate_id == choice.candidate_id
                    ),
                    None,
                )
                if (
                    candidate is None
                    or candidate.split_after_word_id is None
                ):
                    raise ValueError(
                        "上屏字幕语义裁决选择了程序未提供的候选。"
                    )
                positions.append(
                    word_index[candidate.split_after_word_id] + 1
                )
            boundaries = [
                word_index[component_cues[0].source_word_ids[0]],
                *positions,
                word_index[
                    component_cues[-1].source_word_ids[-1]
                ]
                + 1,
            ]
            if all(
                left < right
                for left, right in zip(
                    boundaries,
                    boundaries[1:],
                )
            ):
                continue
            for packet in component_packets:
                original = next(
                    (
                        item
                        for item in packet.candidates
                        if item.is_original
                    ),
                    None,
                )
                if original is None:
                    raise ValueError(
                        "上屏字幕语义裁决缺少程序原边界。"
                    )
                choice = choice_by_id[packet.boundary_id]
                resolved_by_id[packet.boundary_id] = (
                    choice.model_copy(
                        update={
                            "candidate_id": original.candidate_id,
                            "applied": False,
                            "fallback_reason": (
                                "相邻模型选择会造成倒序或空时间窗，"
                                "已保留程序原边界。"
                            ),
                        }
                    )
                )
    return [
        resolved_by_id[item.boundary_id]
        for item in choices
    ]


def _build_result(
    request: LocalizationDisplayAdjudicationInput,
    *,
    packets: list[LocalizationAlignmentBoundaryPacket],
    choices: list[LocalizationAlignmentBoundaryChoice],
    calls: list[VideoLocalizationLlmCallRecord],
) -> LocalizationDisplayAdjudicationResult:
    choices = _resolve_crossed_display_boundary_choices(
        request,
        packets,
        choices,
    )
    dual_tracks = apply_display_boundary_choices(request, packets, choices)
    original_by_boundary = {
        packet.boundary_id: next(item.candidate_id for item in packet.candidates if item.is_original)
        for packet in packets
    }
    changed = sum(
        item.applied
        and item.candidate_id
        != original_by_boundary[item.boundary_id]
        for item in choices
    )
    payload = {
        "dual_tracks": dual_tracks.result_fingerprint,
        "packets": [item.model_dump(mode="json") for item in packets],
        "choices": [item.model_dump(mode="json") for item in choices],
    }
    return LocalizationDisplayAdjudicationResult(
        dual_tracks_fingerprint=request.dual_tracks.result_fingerprint,
        result_fingerprint=_fingerprint(payload),
        packets=packets,
        choices=choices,
        dual_tracks=dual_tracks,
        route=request.route,
        llm_calls=calls,
        quality_summary=LocalizationDisplayAdjudicationQualitySummary(
            status=(
                "warning"
                if any(not item.applied for item in choices)
                else ("passed" if packets else "not_needed")
            ),
            reviewed_boundary_count=len(packets),
            changed_boundary_count=changed,
            fallback_boundary_count=sum(
                not item.applied
                for item in choices
            ),
            source_order_preserved=True,
            display_text_unchanged=True,
            model_call_count=len(calls),
        ),
    )


def _build_boundary_plans(
    request: LocalizationDisplayAdjudicationInput,
    *,
    word_index: dict[str, int],
) -> list[_BoundaryPlan]:
    plans = []
    boundary_number = 0
    for paragraph_cues in _paragraph_groups(request.dual_tracks.display_cues):
        if len(paragraph_cues) < 2:
            continue
        for left, right in zip(paragraph_cues, paragraph_cues[1:]):
            boundary_number += 1
            original_position = word_index[left.source_word_ids[-1]] + 1
            lower = word_index[left.source_word_ids[0]] + 1
            upper = word_index[right.source_word_ids[-1]]
            positions = list(range(lower, upper + 1))
            if len(positions) < 2:
                continue
            plans.append(
                _BoundaryPlan(
                    boundary_number=boundary_number,
                    left_cue=left,
                    right_cue=right,
                    positions=positions,
                    original_position=original_position,
                    candidate_start=(word_index[left.source_word_ids[0]]),
                    candidate_end=(word_index[right.source_word_ids[-1]] + 1),
                )
            )
    return plans


def _select_candidate_positions(
    scored: list[tuple[float, int, float, float]],
    *,
    original_position: int,
    evidence_by_position: dict[int, SourceBoundaryEvidence],
    maximum_candidates: int,
) -> list[int]:
    selected = [original_position]
    for _score, position, _left, _right in sorted(scored, reverse=True):
        if position not in selected:
            selected.append(position)
        if len(selected) >= maximum_candidates - 1:
            break
    supported = sorted(
        (
            item
            for item in evidence_by_position.values()
            if item.position_after_word in {value[1] for value in scored} and item.eligible
        ),
        key=lambda item: item.score,
        reverse=True,
    )
    if supported and supported[0].position_after_word not in selected:
        selected.append(supported[0].position_after_word)
    return sorted(selected[:maximum_candidates])


def _timing_share_is_uncertain(
    plan: _BoundaryPlan,
    words: list[LocalizationSourceWord],
    *,
    maximum_share_difference: float,
) -> bool:
    total_duration = max(
        1,
        words[plan.candidate_end - 1].end_ms - words[plan.candidate_start].start_ms,
    )
    source_share = (words[plan.original_position - 1].end_ms - words[plan.candidate_start].start_ms) / total_duration
    left_units = quality_gate.visible_subtitle_units(plan.left_cue.text)
    right_units = quality_gate.visible_subtitle_units(plan.right_cue.text)
    target_share = left_units / max(1, left_units + right_units)
    return abs(source_share - target_share) > maximum_share_difference


def _validate_input(request: LocalizationDisplayAdjudicationInput) -> None:
    word_ids = [item.word_id for item in request.source_words]
    if len(word_ids) != len(set(word_ids)):
        raise ValueError("上屏字幕裁决收到重复的英文逐词 ID。")
    word_index = {item: index for index, item in enumerate(word_ids)}
    for paragraph_cues in _paragraph_groups(request.dual_tracks.display_cues):
        actual = [word_id for cue in paragraph_cues for word_id in cue.source_word_ids]
        if not actual or any(item not in word_index for item in actual):
            raise ValueError("上屏字幕裁决缺少对应的英文逐词时间。")
        expected = word_ids[word_index[actual[0]] : word_index[actual[-1]] + 1]
        if actual != expected:
            raise ValueError("上屏字幕裁决输入没有保持逐词完整覆盖和原顺序。")


def _validate_checkpoint(
    checkpoint: LocalizationDisplayAdjudicationBatchCheckpoint,
    packets: list[LocalizationAlignmentBoundaryPacket],
    input_fingerprint: str,
) -> None:
    if checkpoint.input_fingerprint != input_fingerprint:
        raise ValueError("上屏字幕裁决批次快照与当前输入不匹配。")
    expected_prefix = [item.boundary_id for item in packets[: len(checkpoint.completed_packet_ids)]]
    if checkpoint.completed_packet_ids != expected_prefix:
        raise ValueError("上屏字幕裁决批次快照不是当前任务的连续前缀。")
    if [item.boundary_id for item in checkpoint.choices] != expected_prefix:
        raise ValueError("上屏字幕裁决批次快照缺少已完成选择。")


def _input_fingerprint(
    request: LocalizationDisplayAdjudicationInput,
    packets: list[LocalizationAlignmentBoundaryPacket],
) -> str:
    return _fingerprint(
        {
            "dual_tracks": request.dual_tracks.result_fingerprint,
            "source_words": [item.model_dump(mode="json") for item in request.source_words],
            "packets": [item.model_dump(mode="json") for item in packets],
            "route": request.route.model_dump(mode="json"),
            "policy": request.policy.model_dump(mode="json"),
        }
    )


def _paragraph_groups(
    cues: list[LocalizationDisplaySubtitleCue],
) -> list[list[LocalizationDisplaySubtitleCue]]:
    groups: list[list[LocalizationDisplaySubtitleCue]] = []
    for cue in cues:
        if not groups or groups[-1][-1].paragraph_id != cue.paragraph_id:
            groups.append([])
        groups[-1].append(cue)
    return groups


def _consecutive_runs(values: list[int]) -> list[tuple[int, int]]:
    if not values:
        return []
    runs = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        runs.append((start, previous))
        start = previous = value
    runs.append((start, previous))
    return runs


def _encode_unique(
    texts: list[str],
    encoder: MultilingualTextEncoder,
) -> dict[str, np.ndarray]:
    unique = list(dict.fromkeys(item.strip() for item in texts if item.strip()))
    matrix = np.asarray(encoder.encode(unique), dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != len(unique):
        raise ValueError("跨语言向量模型返回了无效结果。")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)
    return dict(zip(unique, matrix))


def _boundary_hint(evidence: SourceBoundaryEvidence | None) -> str:
    if evidence is None:
        return "无额外停顿证据"
    parts = []
    if evidence.pause_ms >= 250:
        parts.append(f"约 {evidence.pause_ms}ms 停顿")
    if evidence.punctuation == "terminal":
        parts.append("源文句末")
    elif evidence.punctuation == "clause":
        parts.append("源文分句")
    if evidence.cue_boundary:
        parts.append("ASR 字幕边界")
    return "；".join(parts) or "无额外停顿证据"


def _tail(text: str) -> str:
    return join_source_words(text.split()[-8:])


def _head(text: str) -> str:
    return join_source_words(text.split()[:8])


def _display_entry_ms(word: LocalizationSourceWord) -> int:
    candidate = word.display_entry_ms
    if candidate is None or not word.start_ms <= candidate < word.end_ms:
        return word.start_ms
    return candidate


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "LocalizationAlignmentBoundaryCandidate",
    "LocalizationAlignmentBoundaryChoice",
    "LocalizationAlignmentBoundaryPacket",
    "LocalizationDisplayAdjudicationBatchCheckpoint",
    "LocalizationDisplayAdjudicationInput",
    "LocalizationDisplayAdjudicationPolicy",
    "LocalizationDisplayAdjudicationResult",
    "adjudicate_display_boundaries",
    "apply_display_boundary_choices",
    "build_display_boundary_packets",
    "project_localization_display_adjudication_result",
]
