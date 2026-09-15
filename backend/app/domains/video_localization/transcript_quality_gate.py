"""Deterministic readiness gate between transcript review and alignment."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization.schemas import (
    VideoLocalizationTranscriptSegment,
)
from app.domains.video_localization.timeline_timecode import (
    format_timeline_range,
)
from app.domains.video_localization.whole_recheck import (
    AsrWholeRecheckQualitySummary,
    AsrWholeRecheckResult,
    AsrWholeRecheckUnresolvedItem,
)


class AsrTranscriptQualityGateInput(BaseModel):
    """Immutable terminal transcript snapshot and whole-recheck decision."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "asr-transcript-quality-gate-input-v1",
        "asr-transcript-quality-gate-input-v2",
    ] = "asr-transcript-quality-gate-input-v2"
    upstream_contract_version: Literal[
        "asr-whole-recheck-v3",
        "asr-transcript-review-v2",
    ] = "asr-whole-recheck-v3"
    upstream_operation_id: str = Field(min_length=1)
    source_track_id: str = Field(min_length=1)
    source_audio_sha256: str = Field(min_length=1)
    language: str = Field(min_length=1)
    round_index: int = Field(ge=1, le=2)
    segments: list[VideoLocalizationTranscriptSegment] = Field(min_length=1)
    upstream_status: Literal["completed", "partial", "failed"]
    upstream_passed: bool
    upstream_next_action: Literal[
        "finish",
        "review_next_round",
        "manual_review",
    ]
    upstream_warnings: list[str] = Field(default_factory=list)
    unresolved_items: list[AsrWholeRecheckUnresolvedItem] = Field(default_factory=list)
    upstream_quality_summary: AsrWholeRecheckQualitySummary

    @model_validator(mode="after")
    def validate_segments(self) -> AsrTranscriptQualityGateInput:
        segment_ids = [item.segment_id for item in self.segments]
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("transcript quality gate segment IDs must be unique")
        if any(not (item.corrected_text or item.raw_text).strip() for item in self.segments):
            raise ValueError("transcript quality gate requires complete segment text")
        if any(
            current.start_ms < previous.start_ms
            for previous, current in zip(
                self.segments,
                self.segments[1:],
            )
        ):
            raise ValueError("transcript quality gate segment timing must be ordered")
        known_segment_ids = set(segment_ids)
        if any(
            item.segment_id is not None and item.segment_id not in known_segment_ids for item in self.unresolved_items
        ):
            raise ValueError("transcript quality gate review items must reference segments")
        if any(
            any(
                segment_id not in known_segment_ids
                for segment_id in item.target_segment_ids
            )
            for item in self.unresolved_items
        ):
            raise ValueError(
                "transcript quality gate review spans must reference segments"
            )
        return self


class AsrTranscriptQualityGateIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "upstream_failed",
        "review_next_round_required",
        "manual_review_required",
        "review_recommended",
        "upstream_invariant_failed",
        "invalid_terminal_state",
        "keep_original",
        "upstream_warning",
    ]
    message: str = Field(min_length=1)
    issue_id: str | None = None
    segment_id: str | None = None
    target_segment_ids: list[str] = Field(default_factory=list)


class AsrTranscriptQualityGateReviewTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    location: str = ""
    detail: str = Field(min_length=1)
    excerpt: str = ""
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    issue_id: str | None = None
    segment_id: str | None = None
    target_segment_ids: list[str] = Field(default_factory=list)


class AsrTranscriptQualityGateQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning", "failed"]
    segment_count: int = Field(ge=1)
    complete_segments: bool
    source_matches_upstream: bool
    source_text_unchanged: bool
    segment_ids_unchanged: bool
    source_timing_unchanged: bool
    manual_review_count: int = Field(ge=0)
    review_recommended_count: int = Field(default=0, ge=0)
    keep_original_count: int = Field(ge=0)
    next_round_count: int = Field(ge=0)


class AsrTranscriptQualityGateResult(BaseModel):
    """Deterministic decision about whether alignment may start."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "asr-transcript-quality-gate-v1",
        "asr-transcript-quality-gate-v2",
    ] = "asr-transcript-quality-gate-v2"
    input: AsrTranscriptQualityGateInput
    decision: Literal[
        "ready_for_alignment",
        "manual_review_required",
        "failed",
    ]
    can_start_alignment: bool
    blockers: list[AsrTranscriptQualityGateIssue] = Field(default_factory=list)
    warnings: list[AsrTranscriptQualityGateIssue] = Field(default_factory=list)
    review_targets: list[AsrTranscriptQualityGateReviewTarget] = Field(default_factory=list)
    quality_summary: AsrTranscriptQualityGateQualitySummary
    duration_ms: int = Field(ge=0)


def hard_alignment_blockers(
    result: AsrTranscriptQualityGateResult,
) -> list[AsrTranscriptQualityGateIssue]:
    """Return blockers that represent technical or structural failures."""

    return [
        item
        for item in result.blockers
        if item.code != "manual_review_required"
    ]


def blocks_alignment(result: AsrTranscriptQualityGateResult) -> bool:
    """Keep old manual-review artifacts readable without restoring old blocking."""

    return (
        result.decision == "failed"
        or bool(hard_alignment_blockers(result))
        or (
            not result.can_start_alignment
            and result.decision != "manual_review_required"
        )
    )


class TranscriptQualityGateService:
    """Pure local gate; it never calls a model or edits transcript content."""

    def run(
        self,
        request: AsrTranscriptQualityGateInput,
    ) -> AsrTranscriptQualityGateResult:
        started_at = time.perf_counter()
        source_snapshot = _segment_snapshot(request.segments)
        blockers: list[AsrTranscriptQualityGateIssue] = []
        warnings: list[AsrTranscriptQualityGateIssue] = []
        review_targets: list[AsrTranscriptQualityGateReviewTarget] = []

        manual_items = [item for item in request.unresolved_items if item.recommended_action == "manual_review"]
        keep_original_items = [item for item in request.unresolved_items if item.recommended_action == "keep_original"]
        next_round_items = [item for item in request.unresolved_items if item.recommended_action == "next_round"]

        invariant_ok = all(
            [
                request.upstream_quality_summary.source_text_unchanged,
                request.upstream_quality_summary.segment_ids_unchanged,
                request.upstream_quality_summary.source_timing_unchanged,
                request.upstream_quality_summary.unresolved_items_reference_known_segments,
            ]
        )
        if not invariant_ok:
            blockers.append(
                AsrTranscriptQualityGateIssue(
                    code="upstream_invariant_failed",
                    message="全文复核的只读或完整性检查未通过。",
                )
            )
        if (
            next_round_items
            and request.upstream_next_action != "review_next_round"
        ):
            blockers.append(
                AsrTranscriptQualityGateIssue(
                    code="invalid_terminal_state",
                    message=(
                        "全文复核仍标记了下一轮检查项，但终态没有要求继续"
                        "下一轮；请先修复上游结果结构。"
                    ),
                )
            )

        for item in keep_original_items:
            warnings.append(_issue_from_unresolved(item, code="keep_original"))
        for item in manual_items:
            warnings.append(
                _issue_from_unresolved(
                    item,
                    code="review_recommended",
                )
            )
        for message in request.upstream_warnings:
            if message.strip():
                warnings.append(
                    AsrTranscriptQualityGateIssue(
                        code="upstream_warning",
                        message=message.strip()[:800],
                    )
                )
        segments_by_id = {
            item.segment_id: item for item in request.segments
        }
        review_targets = [
            _review_target(
                item,
                segments_by_id=segments_by_id,
            )
            for item in manual_items
        ]

        if request.upstream_status == "failed" or request.upstream_next_action == "review_next_round":
            blockers.append(
                AsrTranscriptQualityGateIssue(
                    code=("upstream_failed" if request.upstream_status == "failed" else "review_next_round_required"),
                    message=(
                        "全文复核未成功完成。"
                        if request.upstream_status == "failed"
                        else "全文复核要求继续下一轮，当前不能开始时间对齐。"
                    ),
                )
            )
            decision: Literal[
                "ready_for_alignment",
                "manual_review_required",
                "failed",
            ] = "failed"
        elif request.upstream_next_action == "manual_review" or manual_items:
            if not manual_items:
                warnings.append(
                    AsrTranscriptQualityGateIssue(
                        code="review_recommended",
                        message=(
                            "全文复核仍有低把握内容；自动流程将采用当前"
                            "最高概率文本继续，并在结果中保留复听提醒。"
                        ),
                    )
                )
            decision = (
                "ready_for_alignment"
                if invariant_ok and not blockers
                else "failed"
            )
        elif (
            request.upstream_status == "completed"
            and request.upstream_passed
            and request.upstream_next_action == "finish"
            and invariant_ok
            and not blockers
        ):
            decision = "ready_for_alignment"
        else:
            blockers.append(
                AsrTranscriptQualityGateIssue(
                    code="invalid_terminal_state",
                    message="全文复核没有形成可进入下一步的终态。",
                )
            )
            decision = "failed"

        output_snapshot = _segment_snapshot(request.segments)
        source_text_unchanged = source_snapshot == output_snapshot
        segment_ids_unchanged = [item[0] for item in source_snapshot] == [item[0] for item in output_snapshot]
        source_timing_unchanged = [item[1:3] for item in source_snapshot] == [item[1:3] for item in output_snapshot]
        if not (source_text_unchanged and segment_ids_unchanged and source_timing_unchanged):
            raise ValueError("transcript quality gate violated read-only invariants")
        can_start_alignment = decision == "ready_for_alignment" and not blockers
        quality_status: Literal["passed", "warning", "failed"] = (
            "failed"
            if decision == "failed"
            else "warning"
            if review_targets or warnings
            else "passed"
        )
        return AsrTranscriptQualityGateResult(
            input=request.model_copy(deep=True),
            decision=decision,
            can_start_alignment=can_start_alignment,
            blockers=blockers,
            warnings=warnings,
            review_targets=review_targets,
            quality_summary=AsrTranscriptQualityGateQualitySummary(
                status=quality_status,
                segment_count=len(request.segments),
                complete_segments=True,
                source_matches_upstream=True,
                source_text_unchanged=source_text_unchanged,
                segment_ids_unchanged=segment_ids_unchanged,
                source_timing_unchanged=source_timing_unchanged,
                manual_review_count=len(manual_items),
                review_recommended_count=len(manual_items),
                keep_original_count=len(keep_original_items),
                next_round_count=len(next_round_items),
            ),
            duration_ms=_elapsed_ms(started_at),
        )


def build_input(
    result: AsrWholeRecheckResult,
    *,
    upstream_operation_id: str,
    source_track_id: str,
    source_audio_sha256: str,
    segments: list[VideoLocalizationTranscriptSegment],
) -> AsrTranscriptQualityGateInput:
    """Build a gate input and reject crossed or stale transcript snapshots."""

    if source_track_id != result.input.source_track_id or source_audio_sha256 != result.input.source_audio_sha256:
        raise ValueError("transcript quality gate input must use the same source audio")
    upstream_segments = [item.model_dump(mode="json") for item in result.input.segments]
    current_segments = [item.model_dump(mode="json") for item in segments]
    if current_segments != upstream_segments:
        raise ValueError("transcript quality gate requires the complete unchanged segments")
    return AsrTranscriptQualityGateInput(
        upstream_contract_version=result.contract_version,
        upstream_operation_id=upstream_operation_id,
        source_track_id=source_track_id,
        source_audio_sha256=source_audio_sha256,
        language=result.input.language,
        round_index=result.input.round_index,
        segments=[item.model_copy(deep=True) for item in segments],
        upstream_status=result.status,
        upstream_passed=result.passed,
        upstream_next_action=result.next_action,
        upstream_warnings=list(result.warnings),
        unresolved_items=[item.model_copy(deep=True) for item in result.unresolved_items],
        upstream_quality_summary=result.quality_summary.model_copy(deep=True),
    )


def build_degraded_review_input(
    *,
    upstream_operation_id: str,
    source_track_id: str,
    source_audio_sha256: str,
    language: str,
    segments: list[VideoLocalizationTranscriptSegment],
    warning: str,
) -> AsrTranscriptQualityGateInput:
    """Build a local advisory gate when model review cannot be confirmed.

    The uncertain provider result is never applied. The gate validates the
    unchanged ASR snapshot and records the failed review as an advisory before
    alignment continues with the current highest-probability text.
    """

    return AsrTranscriptQualityGateInput(
        upstream_contract_version="asr-transcript-review-v2",
        upstream_operation_id=upstream_operation_id,
        source_track_id=source_track_id,
        source_audio_sha256=source_audio_sha256,
        language=language,
        round_index=1,
        segments=[item.model_copy(deep=True) for item in segments],
        upstream_status="partial",
        upstream_passed=False,
        upstream_next_action="manual_review",
        upstream_warnings=[warning.strip()[:800]],
        unresolved_items=[],
        upstream_quality_summary=AsrWholeRecheckQualitySummary(
            status="warning",
            segment_count=len(segments),
            source_text_unchanged=True,
            segment_ids_unchanged=True,
            source_timing_unchanged=True,
            next_sections_cover_all_segments=True,
            unresolved_items_reference_known_segments=True,
        ),
    )


def _issue_from_unresolved(
    item: AsrWholeRecheckUnresolvedItem,
    *,
    code: Literal[
        "manual_review_required",
        "review_recommended",
        "keep_original",
    ],
) -> AsrTranscriptQualityGateIssue:
    return AsrTranscriptQualityGateIssue(
        code=code,
        message=item.reason,
        issue_id=item.issue_id,
        segment_id=item.segment_id,
        target_segment_ids=list(item.target_segment_ids),
    )


def _review_target(
    item: AsrWholeRecheckUnresolvedItem,
    *,
    segments_by_id: dict[str, VideoLocalizationTranscriptSegment],
) -> AsrTranscriptQualityGateReviewTarget:
    target_segments = [
        segments_by_id[segment_id]
        for segment_id in item.target_segment_ids
        if segment_id in segments_by_id
    ]
    segment = (
        target_segments[0]
        if target_segments
        else segments_by_id.get(item.segment_id or "")
    )
    last_segment = target_segments[-1] if target_segments else segment
    return AsrTranscriptQualityGateReviewTarget(
        title=item.summary,
        location=(
            format_timeline_range(
                segment.start_ms,
                last_segment.end_ms,
            )
            if segment is not None and last_segment is not None
            else "整篇转写"
        ),
        detail=item.reason,
        excerpt=(
            " | ".join(
                (value.corrected_text or value.raw_text).strip()
                for value in target_segments
            )
            if target_segments
            else (segment.corrected_text or segment.raw_text).strip()
            if segment is not None
            else ""
        ),
        start_ms=segment.start_ms if segment is not None else None,
        end_ms=last_segment.end_ms if last_segment is not None else None,
        issue_id=item.issue_id,
        segment_id=item.segment_id,
        target_segment_ids=list(item.target_segment_ids),
    )


def _segment_snapshot(
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


DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE = TranscriptQualityGateService()


__all__ = [
    "AsrTranscriptQualityGateInput",
    "AsrTranscriptQualityGateIssue",
    "AsrTranscriptQualityGateQualitySummary",
    "AsrTranscriptQualityGateResult",
    "AsrTranscriptQualityGateReviewTarget",
    "DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE",
    "TranscriptQualityGateService",
    "blocks_alignment",
    "build_degraded_review_input",
    "build_input",
    "hard_alignment_blockers",
]
