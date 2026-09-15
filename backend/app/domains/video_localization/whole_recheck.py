"""Typed, read-only whole-document recheck after one review round."""

from __future__ import annotations

import time
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization.document_understanding_contracts import (
    AsrDocumentUnderstandingResult,
)
from app.domains.video_localization.llm_observability import (
    AsrLlmCallRecord,
)
from app.domains.video_localization.review_contracts import (
    AsrLockedTranscriptChange,
)
from app.domains.video_localization.review_decisions import (
    AsrReviewDecisionRecord,
    AsrReviewDecisionsResult,
)
from app.domains.video_localization import transcript_boundary_issues
from app.domains.video_localization.schemas import (
    VideoLocalizationTranscriptSegment,
)
from app.domains.video_localization.timeline_timecode import (
    format_timeline_range,
)
from app.errors import AppException

PROMPT_VERSION = "asr-whole-recheck-v3"
class AsrWholeRecheckSection(BaseModel):
    """One continuous section planned for the next focused review round."""

    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1)
    start_ordinal: int = Field(ge=1)
    end_ordinal: int = Field(ge=1)
    start_segment_id: str = Field(min_length=1)
    end_segment_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    focus: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> AsrWholeRecheckSection:
        if self.end_ordinal < self.start_ordinal:
            raise ValueError("end_ordinal must not be before start_ordinal")
        return self


class AsrWholeRecheckInput(BaseModel):
    """Complete immutable input for one whole-document recheck."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-whole-recheck-input-v3"] = (
        "asr-whole-recheck-input-v3"
    )
    upstream_contract_version: Literal["asr-review-decisions-v4"] = (
        "asr-review-decisions-v4"
    )
    upstream_operation_id: str = Field(min_length=1)
    understanding_operation_id: str = Field(min_length=1)
    source_track_id: str = Field(min_length=1)
    source_audio_sha256: str = Field(min_length=1)
    language: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    round_index: int = Field(default=1, ge=1, le=2)
    document_summary: str = Field(min_length=1)
    content_logic: list[str] = Field(default_factory=list)
    speaker_style: str = ""
    segments: list[VideoLocalizationTranscriptSegment] = Field(min_length=1)
    decisions: list[AsrReviewDecisionRecord] = Field(default_factory=list)
    locked_changes: list[AsrLockedTranscriptChange] = Field(
        default_factory=list
    )
    upstream_status: Literal["completed", "partial", "failed"]

    @model_validator(mode="after")
    def validate_sources(self) -> AsrWholeRecheckInput:
        segment_ids = {item.segment_id for item in self.segments}
        if len(segment_ids) != len(self.segments):
            raise ValueError("whole recheck segment IDs must be unique")
        if any(
            item.segment_id not in segment_ids for item in self.decisions
        ):
            raise ValueError(
                "whole recheck decisions must reference current segments"
            )
        if any(
            any(
                segment_id not in segment_ids
                for segment_id in item.target_segment_ids
            )
            for item in self.decisions
        ):
            raise ValueError(
                "whole recheck decision spans must reference current segments"
            )
        if any(
            item.segment_id not in segment_ids
            for item in self.locked_changes
        ):
            raise ValueError(
                "whole recheck locks must reference current segments"
            )
        return self


class AsrWholeRecheckUnresolvedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str | None = None
    segment_id: str | None = None
    target_segment_ids: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    recommended_action: Literal[
        "next_round",
        "manual_review",
        "keep_original",
    ]

    @model_validator(mode="after")
    def normalize_targets(self) -> AsrWholeRecheckUnresolvedItem:
        if not self.target_segment_ids and self.segment_id:
            self.target_segment_ids = [self.segment_id]
        if self.target_segment_ids and self.segment_id is None:
            self.segment_id = self.target_segment_ids[0]
        if (
            len(self.target_segment_ids) > 2
            or len(set(self.target_segment_ids))
            != len(self.target_segment_ids)
            or (
                self.segment_id is not None
                and self.target_segment_ids
                and self.segment_id != self.target_segment_ids[0]
            )
        ):
            raise ValueError("whole recheck unresolved targets are invalid")
        return self


class AsrWholeRecheckQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning", "failed"]
    segment_count: int = Field(ge=1)
    source_text_unchanged: bool
    segment_ids_unchanged: bool
    source_timing_unchanged: bool
    next_sections_cover_all_segments: bool
    unresolved_items_reference_known_segments: bool


class AsrWholeRecheckResult(BaseModel):
    """Read-only decision about finishing or planning another review round."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-whole-recheck-v3"] = (
        "asr-whole-recheck-v3"
    )
    input: AsrWholeRecheckInput
    status: Literal["completed", "partial", "failed"]
    profile_id: str
    model_id: str | None = None
    prompt_version: Literal["asr-whole-recheck-v3"] = PROMPT_VERSION
    passed: bool
    next_action: Literal["finish", "review_next_round", "manual_review"]
    summary: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    next_sections: list[AsrWholeRecheckSection] = Field(
        default_factory=list
    )
    unresolved_items: list[AsrWholeRecheckUnresolvedItem] = Field(
        default_factory=list
    )
    llm_calls: list[AsrLlmCallRecord] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    quality_summary: AsrWholeRecheckQualitySummary


_RECOMMENDED_ACTION_LABELS = {
    "next_round": "进入下一轮自动复查",
    "manual_review": "流程继续，建议完成后复听",
    "keep_original": "保留当前最高概率文字",
}


def reader_unresolved_items(
    result: AsrWholeRecheckResult,
    *,
    frame_rate: float = 30.0,
) -> list[dict]:
    """Project unresolved items with their saved text and media position."""

    segment_by_id = {
        item.segment_id: item for item in result.input.segments
    }
    output = []
    for item in result.unresolved_items:
        target_segments = [
            segment_by_id[segment_id]
            for segment_id in item.target_segment_ids
            if segment_id in segment_by_id
        ]
        segment = (
            target_segments[0]
            if target_segments
            else segment_by_id.get(item.segment_id or "")
        )
        current_text = (
            " | ".join(
                (value.corrected_text or value.raw_text).strip()
                for value in target_segments
            )
            if target_segments
            else (segment.corrected_text or segment.raw_text).strip()
            if segment is not None
            else ""
        )
        output.append(
            {
                "title": item.summary,
                "text": item.reason,
                "meta": (
                    format_timeline_range(
                        segment.start_ms,
                        (
                            target_segments[-1].end_ms
                            if target_segments
                            else segment.end_ms
                        ),
                        frame_rate=frame_rate,
                    )
                    if segment is not None
                    else "整篇转写"
                ),
                "tone": "warning",
                "facts": [
                    *(
                        [
                            {
                                "label": "当前听写原文",
                                "value": current_text,
                            }
                        ]
                        if current_text
                        else []
                    ),
                    {
                        "label": "处理方式",
                        "value": _RECOMMENDED_ACTION_LABELS[
                            item.recommended_action
                        ],
                    },
                    *(
                        [
                            {
                                "label": "片段编号",
                                "value": "、".join(
                                    item.target_segment_ids
                                )
                                or item.segment_id,
                            }
                        ]
                        if item.segment_id
                        else []
                    ),
                ],
                "links": [],
            }
        )
    return output


class WholeRecheckService:
    """Single whole-recheck implementation for formal and development flows."""

    def run(
        self,
        request: AsrWholeRecheckInput,
        *,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> AsrWholeRecheckResult:
        started_at = time.perf_counter()
        if is_cancelled and is_cancelled():
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_OPERATION_CANCELLED",
                "全文复核已取消",
            )
        source_snapshot = _transcript_snapshot(request.segments)
        raw = _targeted_closure_payload(request)
        passed = bool(raw.get("passed"))
        next_sections: list[AsrWholeRecheckSection] = []
        unresolved_items = _normalize_unresolved_items(
            raw.get("unresolved_items"),
            request,
        )
        warnings = _plain_strings(raw.get("warnings"), limit=6)
        residual_boundary_issues = (
            transcript_boundary_issues.find_transcript_boundary_issues(
                request.segments,
                language=request.language,
            )
        )
        if residual_boundary_issues:
            passed = False
            known_unresolved_ids = {
                item.issue_id for item in unresolved_items
            }
            for item in residual_boundary_issues:
                if item.issue_id in known_unresolved_ids:
                    continue
                unresolved_items.append(
                    AsrWholeRecheckUnresolvedItem(
                        issue_id=item.issue_id,
                        segment_id=item.primary_segment_id,
                        target_segment_ids=list(
                            item.related_segment_ids
                        ),
                        summary="相邻片段仍有结构问题",
                        reason=item.reason,
                        recommended_action="manual_review",
                    )
                )
            warnings.append(
                f"仍发现 {len(residual_boundary_issues)} 处高把握的"
                "跨片段结构问题，已保留原文并标记复听。"
            )
        if passed:
            next_action = "finish"
        else:
            next_action = "manual_review"
            if not warnings:
                warnings.append(
                    "仍有内容拿不准，已保留当前文字并标记复听位置。"
                )
        output_snapshot = _transcript_snapshot(request.segments)
        source_text_unchanged = source_snapshot == output_snapshot
        known_segment_ids = {
            item.segment_id for item in request.segments
        }
        unresolved_known = all(
            (
                item.segment_id is None
                or item.segment_id in known_segment_ids
            )
            and all(
                segment_id in known_segment_ids
                for segment_id in item.target_segment_ids
            )
            for item in unresolved_items
        )
        if (
            not source_text_unchanged
            or not unresolved_known
        ):
            raise ValueError(
                "whole recheck violated read-only output invariants"
            )
        quality_status: Literal["passed", "warning", "failed"] = (
            "passed" if passed else "warning"
        )
        return AsrWholeRecheckResult(
            input=request.model_copy(deep=True),
            status="completed" if passed else "partial",
            profile_id=request.profile_id,
            model_id=None,
            passed=passed,
            next_action=next_action,
            summary=str(
                raw.get("summary")
                or (
                    "全文复核通过。"
                    if passed
                    else "全文复核后仍有内容需要处理。"
                )
            ).strip()[:800],
            warnings=warnings,
            next_sections=next_sections,
            unresolved_items=unresolved_items,
            llm_calls=[],
            duration_ms=_elapsed_ms(started_at),
            quality_summary=AsrWholeRecheckQualitySummary(
                status=quality_status,
                segment_count=len(request.segments),
                source_text_unchanged=source_text_unchanged,
                segment_ids_unchanged=True,
                source_timing_unchanged=True,
                next_sections_cover_all_segments=True,
                unresolved_items_reference_known_segments=unresolved_known,
            ),
        )


def _targeted_closure_payload(
    request: AsrWholeRecheckInput,
) -> dict:
    """Close one fixed issue set without reopening a second review round."""

    unresolved = [
        {
            "issue_id": item.issue_id,
            "segment_id": item.segment_id,
            "target_segment_ids": list(item.target_segment_ids),
            "summary": "定点问题仍未关闭",
            "reason": item.reason,
            "recommended_action": "manual_review",
        }
        for item in request.decisions
        if item.outcome in {"needs_confirmation", "invalid"}
    ]
    blocking = any(
        item.outcome == "invalid" for item in request.decisions
    )
    passed = request.upstream_status == "completed" and not blocking
    return {
        "passed": passed,
        "summary": (
            "本轮疑点已完成定点判断和本地收尾。"
            if passed
            else "本轮仍有结构无效的决定，已保留原文。"
        ),
        "warnings": (
            []
            if passed
            else [
                (
                    "上一轮还有未完成或未确认的问题，本次不能直接结束。"
                    if request.upstream_status != "completed"
                    else "未关闭的问题已保留当前文字并标记具体复听位置。"
                )
            ]
        ),
        "unresolved_items": unresolved,
    }


def build_whole_recheck_input(
    decisions: AsrReviewDecisionsResult,
    understanding: AsrDocumentUnderstandingResult,
    *,
    upstream_operation_id: str,
    understanding_operation_id: str,
    profile_id: str | None = None,
) -> AsrWholeRecheckInput:
    """Build one fixed recheck input and reject crossed source snapshots."""

    if (
        decisions.input.source_track_id
        != understanding.input.source_track_id
        or decisions.input.source_audio_sha256
        != understanding.input.source_audio_sha256
    ):
        raise ValueError(
            "whole recheck inputs must come from the same source audio"
        )
    understanding_ids = [
        item.segment_id for item in understanding.input.segments
    ]
    decision_ids = [
        item.segment_id for item in decisions.updated_segments
    ]
    if understanding_ids != decision_ids:
        raise ValueError(
            "whole recheck inputs must use the same segment order"
        )
    resolved_profile_id = (
        str(profile_id or "").strip()
        or decisions.profile_id
        or understanding.profile_id
    )
    if not resolved_profile_id:
        raise ValueError("whole recheck requires an LLM profile")
    return AsrWholeRecheckInput(
        upstream_contract_version=decisions.contract_version,
        upstream_operation_id=upstream_operation_id,
        understanding_operation_id=understanding_operation_id,
        source_track_id=decisions.input.source_track_id,
        source_audio_sha256=decisions.input.source_audio_sha256,
        language=decisions.input.language,
        profile_id=resolved_profile_id,
        round_index=decisions.input.round_index,
        document_summary=understanding.brief.summary,
        content_logic=list(understanding.brief.content_logic),
        speaker_style=understanding.brief.speaker_style,
        segments=[
            item.model_copy(deep=True)
            for item in decisions.updated_segments
        ],
        decisions=[
            item.model_copy(deep=True) for item in decisions.decisions
        ],
        locked_changes=[
            item.model_copy(deep=True)
            for item in decisions.cumulative_locked_changes
        ],
        upstream_status=decisions.status,
    )


def _normalize_unresolved_items(
    raw: object,
    request: AsrWholeRecheckInput,
) -> list[AsrWholeRecheckUnresolvedItem]:
    if not isinstance(raw, list):
        return []
    known_issue_ids = {item.issue_id for item in request.decisions}
    known_segment_ids = {item.segment_id for item in request.segments}
    segment_id_by_issue_id = {
        item.issue_id: item.segment_id
        for item in request.decisions
        if item.issue_id and item.segment_id in known_segment_ids
    }
    segment_ids_by_issue_id = {
        item.issue_id: list(item.target_segment_ids)
        or [item.segment_id]
        for item in request.decisions
        if item.issue_id and item.segment_id in known_segment_ids
    }
    output: list[AsrWholeRecheckUnresolvedItem] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not summary or not reason:
            continue
        issue_id = str(item.get("issue_id") or "").strip() or None
        segment_id = str(item.get("segment_id") or "").strip() or None
        if issue_id not in known_issue_ids:
            issue_id = None
        if segment_id not in known_segment_ids:
            segment_id = None
        if segment_id is None and issue_id is not None:
            segment_id = segment_id_by_issue_id.get(issue_id)
        raw_target_ids = item.get("target_segment_ids")
        target_segment_ids = (
            [
                str(value)
                for value in raw_target_ids
                if str(value) in known_segment_ids
            ]
            if isinstance(raw_target_ids, list)
            else []
        )
        if not target_segment_ids and issue_id is not None:
            target_segment_ids = segment_ids_by_issue_id.get(
                issue_id,
                [],
            )
        if not target_segment_ids and segment_id is not None:
            target_segment_ids = [segment_id]
        ordinals = [
            next(
                index
                for index, segment in enumerate(request.segments)
                if segment.segment_id == target_id
            )
            for target_id in target_segment_ids
        ]
        if (
            len(target_segment_ids) > 2
            or len(set(target_segment_ids)) != len(target_segment_ids)
            or any(
                right != left + 1
                for left, right in zip(ordinals, ordinals[1:])
            )
        ):
            target_segment_ids = [segment_id] if segment_id else []
        if target_segment_ids:
            segment_id = target_segment_ids[0]
        action = str(
            item.get("recommended_action") or "manual_review"
        ).strip()
        if action not in {
            "next_round",
            "manual_review",
            "keep_original",
        }:
            action = "manual_review"
        output.append(
            AsrWholeRecheckUnresolvedItem(
                issue_id=issue_id,
                segment_id=segment_id,
                target_segment_ids=target_segment_ids,
                summary=summary[:300],
                reason=reason[:800],
                recommended_action=action,
            )
        )
    return output[:20]


def _plain_strings(raw: object, *, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [
        str(item).strip()[:800]
        for item in raw
        if str(item).strip()
    ][:limit]


def _transcript_snapshot(
    segments: list[VideoLocalizationTranscriptSegment],
) -> list[tuple[str, int, int, str]]:
    return [
        (
            item.segment_id,
            item.start_ms,
            item.end_ms,
            item.corrected_text or item.raw_text,
        )
        for item in segments
    ]


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


DEFAULT_WHOLE_RECHECK_SERVICE = WholeRecheckService()


def failed_result(
    request: AsrWholeRecheckInput,
    *,
    message: str,
) -> AsrWholeRecheckResult:
    """Return a typed failed result while preserving the reviewed snapshot."""

    return AsrWholeRecheckResult(
        input=request.model_copy(deep=True),
        status="failed",
        profile_id=request.profile_id,
        passed=False,
        next_action="manual_review",
        summary="全文复核没有完成，已保留上一任务确认的完整字幕。",
        warnings=[message.strip()[:800]],
        duration_ms=0,
        quality_summary=AsrWholeRecheckQualitySummary(
            status="failed",
            segment_count=len(request.segments),
            source_text_unchanged=True,
            segment_ids_unchanged=True,
            source_timing_unchanged=True,
            next_sections_cover_all_segments=True,
            unresolved_items_reference_known_segments=True,
        ),
    )
