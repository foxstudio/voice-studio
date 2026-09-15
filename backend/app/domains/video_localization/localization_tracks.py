"""Local quality gate and atomic materialization of localization-v3 dual tracks."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.video_localization import (
    localization_source,
    quality_gate,
    subtitle_exit_timing,
)
from app.domains.video_localization.localization_dual_tracks import (
    LocalizationDualTrackResult,
)
from app.domains.video_localization.localization_spoken_script import (
    LocalizationSpokenScriptFinalResult,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationSpokenSegment,
    VideoLocalizationSubtitleCue,
    now_iso,
)
from app.domains.video_localization.timeline_clip_ownership import (
    mark_tts_target_binding_stale,
)
from app.errors import AppException


QUALITY_GATE_VERSION = "localization-tracks-quality-gate-v1"
FORMAL_WRITE_VERSION = "localization-formal-dual-track-write-v1"
_TTS_RESULT_CLEARING = {
    "tts_result_id": None,
    "tts_generation_id": None,
    "tts_audio_path": None,
    "tts_batch_task_id": None,
    "tts_batch_status": None,
    "tts_batch_error": None,
    "tts_attempted_at": None,
    "generated_duration_ms": None,
}

_FORMAL_QUALITY_BINDING_FIELDS = {
    "final_script_fingerprint",
    "dual_tracks_fingerprint",
    "quality_gate_fingerprint",
    "spoken_segment_count",
    "subtitle_count",
    "semantic_tts_grouping",
}


def invalidate_formal_quality_binding(
    draft: VideoLocalizationDraft,
) -> VideoLocalizationDraft:
    """Make a formal localization gate stale after either dual track changes."""

    if not draft.localization_state:
        return draft
    state = {
        key: value
        for key, value in draft.localization_state.items()
        if key not in _FORMAL_QUALITY_BINDING_FIELDS
    }
    state["status"] = "edited"
    return draft.model_copy(update={"localization_state": state})


class LocalizationTracksQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: Literal["blocker", "warning"]
    count: int = Field(default=1, ge=1)


class LocalizationTracksQualityGateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-tracks-quality-gate-input-v1"
    ] = "localization-tracks-quality-gate-input-v1"
    dual_tracks_operation_id: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    final_script: LocalizationSpokenScriptFinalResult
    dual_tracks: LocalizationDualTrackResult


class LocalizationTracksQualityGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-tracks-quality-gate-v1"
    ] = QUALITY_GATE_VERSION
    source_fingerprint: str = Field(min_length=64, max_length=64)
    final_script_fingerprint: str = Field(min_length=64, max_length=64)
    dual_tracks_fingerprint: str = Field(min_length=64, max_length=64)
    result_fingerprint: str = Field(min_length=64, max_length=64)
    decision: Literal["passed", "warning", "blocked"]
    blockers: list[LocalizationTracksQualityIssue] = Field(
        default_factory=list,
    )
    warnings: list[LocalizationTracksQualityIssue] = Field(
        default_factory=list,
    )
    diagnostics: dict


class LocalizationFormalDualTrackWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "localization-formal-dual-track-write-v1"
    ] = FORMAL_WRITE_VERSION
    source_fingerprint: str = Field(min_length=64, max_length=64)
    quality_gate_fingerprint: str = Field(min_length=64, max_length=64)
    result_fingerprint: str = Field(min_length=64, max_length=64)
    spoken_segment_count: int = Field(ge=1)
    display_subtitle_count: int = Field(ge=1)
    detached_dub_clip_count: int = Field(ge=0)
    cleared_source_tts_result_count: int = Field(ge=0)


def validate_localization_tracks(
    request: LocalizationTracksQualityGateInput,
    *,
    current_source_fingerprint: str,
) -> LocalizationTracksQualityGateResult:
    blockers = []
    warnings = []
    if request.source_fingerprint != current_source_fingerprint:
        blockers.append(
            LocalizationTracksQualityIssue(
                code="source_changed",
                message="英文 ASR 或逐词时间已经变化，需要重新生成本土化结果。",
                severity="blocker",
            )
        )
    if (
        request.dual_tracks.spoken_script_fingerprint
        != request.final_script.result_fingerprint
    ):
        blockers.append(
            LocalizationTracksQualityIssue(
                code="script_alignment_mismatch",
                message="中文台词与语义时间映射不是同一版本。",
                severity="blocker",
            )
        )
    if request.final_script.quality_summary.status == "warning":
        warnings.append(
            LocalizationTracksQualityIssue(
                code="post_review_failed",
                message="中文终审经过限定重试后仍有问题；已标记供人工抽查，不阻止后续流程。",
                severity="warning",
            )
        )
    (
        duplicate_sentence_count,
        repeated_dialogue_count,
    ) = _adjacent_duplicate_sentence_counts(
        [
            paragraph
            for section in request.final_script.content.sections
            for paragraph in section.paragraphs
        ]
    )
    if duplicate_sentence_count:
        warnings.append(
            LocalizationTracksQualityIssue(
                code="duplicate_chinese_sentence",
                message="中文台词中出现相邻重复句；已标记供人工抽查，不阻止后续流程。",
                severity="warning",
                count=duplicate_sentence_count,
            )
        )
    if repeated_dialogue_count:
        warnings.append(
            LocalizationTracksQualityIssue(
                code="repeated_dialogue_turn",
                message=(
                    "相邻对白出现相同短句，可能是人物回应；已保留并提示抽查。"
                ),
                severity="warning",
                count=repeated_dialogue_count,
            )
        )
    if not request.dual_tracks.quality_summary.paragraph_coverage_complete:
        blockers.append(
            LocalizationTracksQualityIssue(
                code="paragraph_coverage_incomplete",
                message="中文台词没有完整映射到双轨结果。",
                severity="blocker",
            )
        )
    if not request.dual_tracks.quality_summary.display_timing_ordered:
        blockers.append(
            LocalizationTracksQualityIssue(
                code="display_timing_invalid",
                message="中文字幕时间存在重叠或倒序。",
                severity="blocker",
            )
        )
    formal_track_gate = quality_gate.evaluate_localized_subtitle_track(
        _formal_subtitles(request.dual_tracks)
    )
    blockers.extend(
        LocalizationTracksQualityIssue(
            code=item.code,
            message=item.message,
            severity="blocker",
        )
        for item in formal_track_gate.blockers
    )
    warnings.extend(
        LocalizationTracksQualityIssue(
            code=item.code,
            message=item.message,
            severity="warning",
        )
        for item in formal_track_gate.warnings
    )
    blockers = _aggregate_quality_issues(blockers)
    warnings = _aggregate_quality_issues(warnings)
    decision: Literal["passed", "warning", "blocked"] = (
        "blocked" if blockers else "warning" if warnings else "passed"
    )
    payload = {
        "source": request.source_fingerprint,
        "script": request.final_script.result_fingerprint,
        "dual_tracks": request.dual_tracks.result_fingerprint,
        "decision": decision,
        "blockers": [item.model_dump(mode="json") for item in blockers],
        "warnings": [item.model_dump(mode="json") for item in warnings],
    }
    return LocalizationTracksQualityGateResult(
        source_fingerprint=request.source_fingerprint,
        final_script_fingerprint=request.final_script.result_fingerprint,
        dual_tracks_fingerprint=request.dual_tracks.result_fingerprint,
        result_fingerprint=_fingerprint(payload),
        decision=decision,
        blockers=blockers,
        warnings=warnings,
        diagnostics={
            "model_call_count": 0,
            "spoken_segment_count": len(
                request.dual_tracks.spoken_segments
            ),
            "display_subtitle_count": len(
                request.dual_tracks.display_cues
            ),
            "source_order_preserved": (
                request.dual_tracks.quality_summary.source_order_preserved
            ),
        },
    )


def _aggregate_quality_issues(
    issues: list[LocalizationTracksQualityIssue],
) -> list[LocalizationTracksQualityIssue]:
    aggregated: dict[
        tuple[str, str, Literal["blocker", "warning"]],
        LocalizationTracksQualityIssue,
    ] = {}
    for issue in issues:
        key = (issue.code, issue.message, issue.severity)
        current = aggregated.get(key)
        if current is None:
            aggregated[key] = issue
            continue
        aggregated[key] = current.model_copy(
            update={"count": current.count + issue.count}
        )
    return list(aggregated.values())


def build_formal_localization_dual_tracks(
    draft: VideoLocalizationDraft,
    *,
    final_script: LocalizationSpokenScriptFinalResult,
    dual_tracks: LocalizationDualTrackResult,
    gate: LocalizationTracksQualityGateResult,
) -> tuple[VideoLocalizationDraft, LocalizationFormalDualTrackWriteResult]:
    current_source = (
        localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(
            localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(
                draft
            )
        ).source_fingerprint
    )
    repeated_gate = validate_localization_tracks(
        LocalizationTracksQualityGateInput(
            dual_tracks_operation_id="precommit-repeat",
            source_fingerprint=gate.source_fingerprint,
            final_script=final_script,
            dual_tracks=dual_tracks,
        ),
        current_source_fingerprint=current_source,
    )
    if gate.decision == "blocked" or repeated_gate.decision == "blocked":
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TRACK_QUALITY_GATE_BLOCKED",
            "本土化双轨质量门未通过，因此没有覆盖当前结果。",
        )
    if (
        gate.final_script_fingerprint != final_script.result_fingerprint
        or gate.dual_tracks_fingerprint != dual_tracks.result_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_TRACK_QUALITY_GATE_STALE",
            "质量门与当前双轨结果不一致，请重新检查。",
        )
    spoken_segments = [
        VideoLocalizationSpokenSegment(
            segment_id=item.segment_id,
            paragraph_id=item.paragraph_id,
            text=item.tts_text,
            start_ms=item.semantic_start_ms,
            end_ms=item.semantic_end_ms,
            source_cue_ids=list(item.source_cue_ids),
            source_word_ids=list(item.source_word_ids),
        )
        for item in dual_tracks.spoken_segments
    ]
    spoken_by_paragraph = {
        item.paragraph_id: item for item in spoken_segments
    }
    subtitles = _formal_subtitles(
        dual_tracks,
        spoken_segment_id_by_paragraph={
            key: value.segment_id for key, value in spoken_by_paragraph.items()
        },
        frame_rate=draft.source_media.frame_rate,
        media_duration_ms=draft.source_media.duration_ms,
    )
    outputs_by_primary: dict[str, list[VideoLocalizationSubtitleCue]] = (
        defaultdict(list)
    )
    spoken_by_primary: dict[str, list[VideoLocalizationSpokenSegment]] = (
        defaultdict(list)
    )
    for subtitle in subtitles:
        if subtitle.source_cue_ids:
            outputs_by_primary[subtitle.source_cue_ids[0]].append(subtitle)
    for segment in spoken_segments:
        if segment.source_cue_ids:
            spoken_by_primary[segment.source_cue_ids[0]].append(segment)
    cleared_source_tts_result_count = sum(
        1
        for cue in draft.cues
        if any(
            (
                cue.tts_result_id,
                cue.tts_generation_id,
                cue.tts_audio_path,
                cue.tts_batch_task_id,
            )
        )
    )
    next_cues = []
    for cue in draft.cues:
        display_items = outputs_by_primary.get(cue.cue_id, [])
        spoken_items = spoken_by_primary.get(cue.cue_id, [])
        localization_flags = [
            flag
            for flag in cue.quality_flags
            if not flag.startswith("localization")
        ]
        next_cues.append(
            cue.model_copy(
                update={
                    "zh_localized_subtitle_text": (
                        "\n".join(item.text for item in display_items)
                        if display_items
                        else None
                    ),
                    "tts_recommended_text": (
                        "\n".join(item.text for item in spoken_items)
                        if spoken_items
                        else None
                    ),
                    "quality_flags": (
                        sorted(
                            {
                                *localization_flags,
                                "localization_v3_dual_track",
                            }
                        )
                        if display_items or spoken_items
                        else localization_flags
                    ),
                    **_TTS_RESULT_CLEARING,
                }
            )
        )
    detached_dub_clip_count = sum(
        1
        for item in draft.timeline_clips
        if dict(item).get("track_id", "dub") == "dub"
    )
    state = {
        key: value
        for key, value in draft.localization_state.items()
        if key != "semantic_tts_grouping"
    }
    state.update(
        {
            "status": "draft",
            "workflow_id": "localization-v3",
            "source_fingerprint": current_source,
            "final_script_fingerprint": final_script.result_fingerprint,
            "dual_tracks_fingerprint": dual_tracks.result_fingerprint,
            "quality_gate_fingerprint": gate.result_fingerprint,
            "spoken_segment_count": len(spoken_segments),
            "subtitle_count": len(subtitles),
            "created_at": now_iso(),
        }
    )
    next_draft = draft.model_copy(
        update={
            "cues": next_cues,
            "localized_spoken_segments": spoken_segments,
            "localized_subtitles": subtitles,
            "localization_state": state,
            "timeline_clips": [
                (
                    mark_tts_target_binding_stale(draft, item)
                    if dict(item).get("track_id", "dub") == "dub"
                    else dict(item)
                )
                for item in draft.timeline_clips
            ],
        }
    )
    payload = {
        "source": current_source,
        "gate": gate.result_fingerprint,
        "spoken": len(spoken_segments),
        "display": len(subtitles),
        "detached": detached_dub_clip_count,
        "cleared": cleared_source_tts_result_count,
    }
    return next_draft, LocalizationFormalDualTrackWriteResult(
        source_fingerprint=current_source,
        quality_gate_fingerprint=gate.result_fingerprint,
        result_fingerprint=_fingerprint(payload),
        spoken_segment_count=len(spoken_segments),
        display_subtitle_count=len(subtitles),
        detached_dub_clip_count=detached_dub_clip_count,
        cleared_source_tts_result_count=cleared_source_tts_result_count,
    )


def project_localization_tracks_quality_gate_result(
    result: LocalizationTracksQualityGateResult,
) -> dict:
    return {
        "label": "检查本土化结果",
        "order": 120,
        "status": (
            "failed"
            if result.decision == "blocked"
            else "warning"
            if result.decision == "warning"
            else "success"
        ),
        "purpose": (
            "本地检查台词覆盖、语义时间、字幕可读性和输入版本；"
            "不调用模型，也不修改结果。"
        ),
        "summary": (
            f"发现 {len(result.blockers)} 个阻断项、"
            f"{len(result.warnings)} 个提示项。"
        ),
        "metrics": [
            {"label": "阻断项", "value": str(len(result.blockers))},
            {"label": "提示项", "value": str(len(result.warnings))},
            {
                "label": "中文台词",
                "value": str(
                    result.diagnostics.get("spoken_segment_count", 0)
                ),
            },
            {
                "label": "上屏字幕",
                "value": str(
                    result.diagnostics.get("display_subtitle_count", 0)
                ),
            },
        ],
        "sections": [
            {
                "title": "需要处理",
                "items": [
                    {
                        "title": item.message,
                        "text": item.code,
                        "meta": f"{item.count} 项",
                    }
                    for item in [*result.blockers, *result.warnings]
                ],
            }
        ]
        if result.blockers or result.warnings
        else [],
    }


def project_localization_formal_dual_track_result(
    result: LocalizationFormalDualTrackWriteResult,
) -> dict:
    return {
        "label": "保存正式本土化双轨",
        "order": 130,
        "status": "success",
        "purpose": (
            "保存独立中文台词轨和上屏字幕轨，英文 ASR 与逐词时间保持不变。"
        ),
        "summary": (
            f"已保存 {result.spoken_segment_count} 段中文台词和 "
            f"{result.display_subtitle_count} 条上屏字幕。"
        ),
        "metrics": [
            {
                "label": "中文台词",
                "value": str(result.spoken_segment_count),
            },
            {
                "label": "上屏字幕",
                "value": str(result.display_subtitle_count),
            },
            {"label": "原文时间", "value": "未修改"},
        ],
        "sections": [],
    }


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _adjacent_duplicate_sentence_counts(
    paragraphs: list[str],
) -> tuple[int, int]:
    sentences = [
        _normalized_sentence(item)
        for paragraph in paragraphs
        for item in re.findall(
            r"[^。！？!?；;]+[。！？!?；;]?",
            paragraph,
        )
        if _normalized_sentence(item)
    ]

    blocker_count = 0
    dialogue_warning_count = 0
    for left, right in zip(sentences, sentences[1:]):
        if left != right:
            continue
        if len(left) <= 8:
            dialogue_warning_count += 1
        else:
            blocker_count += 1
    return blocker_count, dialogue_warning_count


def _normalized_sentence(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _formal_subtitles(
    dual_tracks: LocalizationDualTrackResult,
    *,
    spoken_segment_id_by_paragraph: dict[str, str] | None = None,
    frame_rate: float | None = None,
    media_duration_ms: int | None = None,
) -> list[VideoLocalizationSubtitleCue]:
    spoken_ids = spoken_segment_id_by_paragraph or {
        item.paragraph_id: item.segment_id
        for item in dual_tracks.spoken_segments
    }
    subtitles = [
        VideoLocalizationSubtitleCue(
            subtitle_id=item.cue_id,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            text=item.text,
            tts_text=item.tts_text,
            linked_cue_id=(
                item.source_cue_ids[0]
                if item.source_cue_ids
                else None
            ),
            source_cue_ids=list(item.source_cue_ids),
            source_word_ids=list(item.source_word_ids),
            spoken_segment_id=spoken_ids.get(item.paragraph_id),
            quality_flags=list(item.quality_flags),
        )
        for item in dual_tracks.display_cues
    ]
    resolved = subtitle_exit_timing.resolve_display_exit_times(
        [
            subtitle_exit_timing.SubtitleTimingSpan(
                start_ms=item.start_ms,
                end_ms=item.end_ms,
            )
            for item in subtitles
        ],
        frame_rate=frame_rate,
        media_duration_ms=media_duration_ms,
    )
    return [
        item.model_copy(
            update={
                "end_ms": span.end_ms,
                "quality_flags": (
                    list(
                        dict.fromkeys(
                            [
                                *item.quality_flags,
                                "timing:display-exit-extended",
                            ]
                        )
                    )
                    if span.end_ms != item.end_ms
                    else item.quality_flags
                ),
            }
        )
        for item, span in zip(subtitles, resolved)
    ]


__all__ = [
    "LocalizationTracksQualityGateInput",
    "LocalizationTracksQualityGateResult",
    "build_formal_localization_dual_tracks",
    "project_localization_formal_dual_track_result",
    "project_localization_tracks_quality_gate_result",
    "validate_localization_tracks",
]
