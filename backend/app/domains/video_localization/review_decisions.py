"""Typed adjudication and deterministic application of ASR review issues."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization import (
    asr_uncertainty,
    asr_targeted_relisten,
    research_evidence,
    transcript_text_rules,
)
from app.domains.video_localization.llm_observability import (
    AsrLlmCallRecord,
    AsrLlmTraceCollector,
)
from app.domains.video_localization.review_contracts import (
    AsrLockedTranscriptChange,
    AsrTranscriptEditPatch,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationTranscriptSegment,
)
from app.services import llm_runtime
from app.schemas.asr_uncertainty import AsrReviewDecisionWarning, AsrUnconfirmedTextSpan

if TYPE_CHECKING:
    from app.domains.video_localization.section_review import (
        AsrSectionReviewResult,
    )


PROMPT_VERSION = "asr-review-decisions-v4"
BEST_GUESS_CONFIDENCE_FLOOR = 0.5
RETRYABLE_OUTPUT_CODES = frozenset(
    {"llm_json_invalid", "llm_json_not_object", "llm_output_truncated"}
)


@dataclass(frozen=True)
class AsrReviewDecisionsCompletionRequest:
    """One explicit model attempt in the review-decision workflow."""

    contract_version: str
    behavior_version: str
    call_group: Literal["primary", "coverage"]
    attempt: int
    round_index: int
    issue_ids: tuple[str, ...]
    system_prompt: str
    user_payload: dict
    profile_id: str
    max_tokens: int
    timeout: float
    disable_reasoning: bool
    trace_sink: llm_runtime.TraceSink | None = None


class AsrReviewDecisionsCompletionGateway(Protocol):
    """Replaceable boundary used by direct and managed execution."""

    def __call__(
        self,
        request: AsrReviewDecisionsCompletionRequest,
    ) -> dict: ...


class AsrReviewDecisionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    current_excerpt: str = Field(min_length=1)
    proposed_replacement: str = ""
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    needs_confirmation: bool = False
    evidence_source_ids: list[str] = Field(default_factory=list)
    scope: Literal["single_segment", "adjacent_segments"] = "single_segment"
    target_segment_ids: list[str] = Field(default_factory=list)
    origin: Literal[
        "llm_section_review",
        "deterministic_boundary_rule",
        "deterministic_text_rule",
    ] = "llm_section_review"
    patches: list[AsrTranscriptEditPatch] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_targets(self) -> AsrReviewDecisionIssue:
        if not self.target_segment_ids:
            self.target_segment_ids = [self.segment_id]
        if (
            len(self.target_segment_ids) not in {1, 2}
            or len(set(self.target_segment_ids))
            != len(self.target_segment_ids)
            or self.segment_id != self.target_segment_ids[0]
        ):
            raise ValueError("review decision targets are invalid")
        expected_scope = (
            "adjacent_segments"
            if len(self.target_segment_ids) == 2
            else "single_segment"
        )
        if self.scope != expected_scope:
            raise ValueError("review decision scope does not match targets")
        if self.patches:
            patch_ids = [item.segment_id for item in self.patches]
            if (
                len(patch_ids) != len(set(patch_ids))
                or set(patch_ids) != set(self.target_segment_ids)
            ):
                raise ValueError(
                    "review decision patches must cover each target once"
                )
        return self


class AsrReviewUpstreamSectionRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1)
    status: Literal["completed", "failed"]
    duration_ms: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    llm_call_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


class AsrReviewUpstreamWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["section_failed", "invalid_issue"]
    section_id: str = ""
    message: str = Field(min_length=1)


class AsrReviewDecisionsInput(BaseModel):
    """Complete immutable input for one review-decision round."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-review-decisions-input-v4"] = (
        "asr-review-decisions-input-v4"
    )
    upstream_contract_version: Literal["asr-section-review-v4"] = (
        "asr-section-review-v4"
    )
    upstream_operation_id: str = Field(min_length=1)
    source_track_id: str = Field(min_length=1)
    source_audio_sha256: str = Field(min_length=1)
    language: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    round_index: int = Field(default=1, ge=1, le=2)
    document_summary: str = Field(min_length=1)
    content_logic: list[str] = Field(default_factory=list)
    speaker_style: str = ""
    segments: list[VideoLocalizationTranscriptSegment] = Field(min_length=1)
    issues: list[AsrReviewDecisionIssue] = Field(default_factory=list)
    evidence: list[research_evidence.AsrResearchEvidenceItem] = Field(
        default_factory=list
    )
    locked_changes: list[AsrLockedTranscriptChange] = Field(
        default_factory=list
    )
    acoustic_candidates: list[
        asr_targeted_relisten.AsrAcousticCandidate
    ] = Field(default_factory=list)
    upstream_status: Literal["completed", "partial", "failed"]
    upstream_section_runs: list[AsrReviewUpstreamSectionRun] = Field(
        default_factory=list
    )
    upstream_warnings: list[AsrReviewUpstreamWarning] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_issues(self) -> AsrReviewDecisionsInput:
        segments = {item.segment_id: item for item in self.segments}
        seen: set[str] = set()
        for issue in self.issues:
            if issue.issue_id in seen:
                raise ValueError("review decision issue IDs must be unique")
            seen.add(issue.issue_id)
            target_ordinals: list[int] = []
            for target_id in issue.target_segment_ids:
                segment = segments.get(target_id)
                if segment is None:
                    raise ValueError(
                        "review decision issue does not match the transcript"
                    )
                target_ordinals.append(
                    next(
                        index
                        for index, item in enumerate(self.segments)
                        if item.segment_id == target_id
                    )
                )
            if any(
                right != left + 1
                for left, right in zip(
                    target_ordinals,
                    target_ordinals[1:],
                )
            ):
                raise ValueError(
                    "review decision target segments must be adjacent"
                )
            if issue.patches:
                for patch in issue.patches:
                    segment = segments[patch.segment_id]
                    text = segment.corrected_text or segment.raw_text
                    if text.count(patch.current_excerpt) != 1:
                        raise ValueError(
                            "review decision patch does not uniquely match"
                        )
            else:
                segment = segments.get(issue.segment_id)
                text = (
                    segment.corrected_text or segment.raw_text
                    if segment is not None
                    else ""
                )
                if issue.current_excerpt.casefold() not in text.casefold():
                    raise ValueError(
                        "review decision issue does not match the transcript"
                    )
        return self


class AsrReviewDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    current_excerpt: str = Field(min_length=1)
    proposed_replacement: str = ""
    outcome: Literal[
        "applied",
        "rejected",
        "needs_confirmation",
        "invalid",
    ]
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_source_ids: list[str] = Field(default_factory=list)
    scope: Literal["single_segment", "adjacent_segments"] = "single_segment"
    target_segment_ids: list[str] = Field(default_factory=list)
    origin: Literal[
        "llm_section_review",
        "deterministic_boundary_rule",
        "deterministic_text_rule",
    ] = "llm_section_review"
    patches: list[AsrTranscriptEditPatch] = Field(default_factory=list)
    before_text: str = ""
    after_text: str = ""


class AsrPreparedReviewDecision(BaseModel):
    """One normalized model decision before deterministic application."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    accept: bool
    replacement: str = ""
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    needs_confirmation: bool = False
    evidence_source_ids: list[str] = Field(default_factory=list)
    patches: list[AsrTranscriptEditPatch] = Field(default_factory=list)


class AsrReviewDecisionsPreparedSnapshot(BaseModel):
    """Development-only replay boundary captured before text mutation."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[
        "asr-review-decisions-prepared-snapshot-v1"
    ] = "asr-review-decisions-prepared-snapshot-v1"
    request: AsrReviewDecisionsInput
    decisions: list[AsrPreparedReviewDecision] = Field(
        default_factory=list
    )
    warnings: list[AsrReviewDecisionWarning] = Field(
        default_factory=list
    )


class AsrReviewAppliedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1)
    source_task_id: str = Field(min_length=1)
    round_index: int = Field(ge=1, le=2)
    segment_id: str = Field(min_length=1)
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_source_ids: list[str] = Field(default_factory=list)


class AsrReviewDecisionsQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning", "failed"]
    issue_count: int = Field(ge=0)
    decided_issue_count: int = Field(ge=0)
    applied_change_count: int = Field(ge=0)
    applied_issue_count: int = Field(default=0, ge=0)
    applied_patch_count: int = Field(default=0, ge=0)
    unresolved_issue_count: int = Field(ge=0)
    upstream_review_complete: bool
    segment_ids_unchanged: bool
    source_timing_unchanged: bool
    only_supplied_issues_considered: bool
    locked_changes_preserved: bool
    atomic_patch_sets_preserved: bool = True


class AsrReviewDecisionsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-review-decisions-v4"] = (
        "asr-review-decisions-v4"
    )
    input: AsrReviewDecisionsInput
    status: Literal["completed", "partial", "failed"]
    profile_id: str
    model_id: str | None = None
    prompt_version: Literal["asr-review-decisions-v4"] = PROMPT_VERSION
    decisions: list[AsrReviewDecisionRecord] = Field(default_factory=list)
    updated_segments: list[VideoLocalizationTranscriptSegment]
    changes: list[AsrReviewAppliedChange] = Field(default_factory=list)
    cumulative_locked_changes: list[AsrLockedTranscriptChange] = Field(
        default_factory=list
    )
    warnings: list[AsrReviewDecisionWarning] = Field(default_factory=list)
    llm_calls: list[AsrLlmCallRecord] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    quality_summary: AsrReviewDecisionsQualitySummary


class ReviewDecisionsService:
    """Single implementation shared by formal, development and API flows."""

    def run(
        self,
        request: AsrReviewDecisionsInput,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        on_segments_changed: Callable[
            [list[VideoLocalizationTranscriptSegment]],
            None,
        ]
        | None = None,
        completion_gateway: (
            AsrReviewDecisionsCompletionGateway | None
        ) = None,
        on_prepared_decisions: Callable[
            [AsrReviewDecisionsPreparedSnapshot],
            None,
        ]
        | None = None,
        prepared_snapshot: AsrReviewDecisionsPreparedSnapshot | None = None,
    ) -> AsrReviewDecisionsResult:
        started_at = time.perf_counter()
        if (
            prepared_snapshot is not None
            and prepared_snapshot.request.model_dump(mode="json")
            != request.model_dump(mode="json")
        ):
            raise ValueError(
                "prepared review decisions do not match the supplied input"
            )
        source_segments = [
            item.model_copy(deep=True) for item in request.segments
        ]
        if is_cancelled and is_cancelled():
            raise llm_runtime.LlmRuntimeError(
                "汇总复查修改已取消",
                code="llm_cancelled",
                status_code=409,
            )
        trace_collector = AsrLlmTraceCollector()
        if prepared_snapshot is not None:
            prepared = [
                item.model_dump(mode="json")
                for item in prepared_snapshot.decisions
            ]
            preparation_warnings = [
                item.model_copy(deep=True)
                for item in prepared_snapshot.warnings
            ]
        elif request.issues:
            raw = _complete_json(
                request,
                trace_collector=trace_collector,
                completion_gateway=completion_gateway,
            )
            missing_issue_ids = _missing_issue_ids(request, raw)
            if missing_issue_ids:
                retry_request = request.model_copy(
                    update={
                        "issues": [
                            item
                            for item in request.issues
                            if item.issue_id in missing_issue_ids
                        ]
                    },
                    deep=True,
                )
                retry_raw = _complete_json(
                    retry_request,
                    trace_collector=trace_collector,
                    call_group="coverage",
                    completion_gateway=completion_gateway,
                )
                raw = {
                    **raw,
                    "decisions": [
                        *(
                            raw.get("decisions")
                            if isinstance(raw.get("decisions"), list)
                            else []
                        ),
                        *(
                            retry_raw.get("decisions")
                            if isinstance(
                                retry_raw.get("decisions"),
                                list,
                            )
                            else []
                        ),
                    ],
                }
            prepared, preparation_warnings = _prepare_decisions(
                request,
                raw,
            )
        else:
            prepared = []
            preparation_warnings = []
        if on_prepared_decisions is not None:
            on_prepared_decisions(
                AsrReviewDecisionsPreparedSnapshot(
                    request=request.model_copy(deep=True),
                    decisions=[
                        AsrPreparedReviewDecision.model_validate(item)
                        for item in prepared
                    ],
                    warnings=[
                        item.model_copy(deep=True)
                        for item in preparation_warnings
                    ],
                )
            )
        updated, changes, apply_warnings = apply_decisions(
            source_segments,
            prepared,
            evidence=[
                {
                    "source_id": item.evidence_id,
                    "title": item.title,
                    "snippet": item.snippet,
                }
                for item in request.evidence
            ],
            locked_changes=[
                item.model_dump(mode="json")
                for item in request.locked_changes
            ],
            on_segments_changed=on_segments_changed,
        )
        locked_preserved = _preserves_locked_changes(
            source_segments,
            updated,
            request.locked_changes,
        )
        updated, orthography_changes = (
            transcript_text_rules.normalize_transcript_orthography(
                updated
            )
        )
        changes.extend(
            {
                "issue_id": (
                    f"orthography-{request.round_index}-"
                    f"{item.segment_id}"
                ),
                "segment_id": item.segment_id,
                "before": item.before,
                "after": item.after,
                "reason": item.reason,
                "confidence": 1.0,
                "evidence_source_ids": [],
                "replacement": item.after,
            }
            for item in orthography_changes
        )
        if orthography_changes and on_segments_changed is not None:
            on_segments_changed(
                [item.model_copy(deep=True) for item in updated]
            )
        typed_apply_warnings = [
            AsrReviewDecisionWarning.model_validate(item)
            for item in apply_warnings
        ]
        warnings = [
            *preparation_warnings,
            *typed_apply_warnings,
        ]
        applied_issue_ids = {
            str(item.get("issue_id") or "") for item in changes
        }
        warned_issue_ids = {
            item.issue_id for item in warnings if item.issue_id
        }
        for issue in request.issues:
            if (
                not issue.needs_confirmation
                or issue.issue_id in applied_issue_ids
                or issue.issue_id in warned_issue_ids
            ):
                continue
            warnings.append(
                AsrReviewDecisionWarning(
                    code="needs_confirmation",
                    issue_id=issue.issue_id,
                    segment_id=issue.segment_id,
                    excerpt=issue.current_excerpt,
                    message=(
                        "复查已确认当前文字仍不可靠，但现有证据不足以安全改写；"
                        "已保留当前最高概率文本，并把未解决状态传给后续流程。"
                    ),
                )
            )
        unresolved_issue_ids = {
            warning.issue_id
            for warning in warnings
            if warning.code == "needs_confirmation" and warning.issue_id
        }
        reconciled_segments = []
        uncertainty_changed = False
        for segment in updated:
            segment_issues = [
                issue
                for issue in request.issues
                if segment.segment_id in issue.target_segment_ids
            ]
            segment_issue_ids = {issue.issue_id for issue in segment_issues}
            existing_spans = list(segment.unconfirmed_text_spans or [])
            spans = [
                span
                for span in existing_spans
                if span.issue_id not in segment_issue_ids
            ]
            spans.extend(
                AsrUnconfirmedTextSpan(
                    issue_id=issue.issue_id,
                    segment_id=segment.segment_id,
                    excerpt=next(
                        (
                            patch.current_excerpt
                            for patch in issue.patches
                            if patch.segment_id == segment.segment_id
                        ),
                        issue.current_excerpt,
                    ),
                    segment_text_sha256=(
                        asr_uncertainty.transcript_segment_text_fingerprint(
                            segment.corrected_text or segment.raw_text
                        )
                    ),
                    source_task_id=request.upstream_operation_id,
                )
                for issue in segment_issues
                if issue.issue_id in unresolved_issue_ids
            )
            flags = set(segment.review_flags)
            if spans:
                flags.add("asr_unresolved_text")
            elif segment.unconfirmed_text_spans is not None and segment_issues:
                flags.discard("asr_unresolved_text")
                flags.discard("asr_unresolved_scope_unknown")
            manages_typed_uncertainty = (
                segment.unconfirmed_text_spans is not None
                or any(
                    issue.issue_id in unresolved_issue_ids
                    for issue in segment_issues
                )
            )
            reconciled = segment.model_copy(
                update={
                    "unconfirmed_text_spans": (
                        spans if manages_typed_uncertainty else None
                    ),
                    "review_flags": sorted(flags),
                }
            )
            uncertainty_changed = uncertainty_changed or reconciled != segment
            reconciled_segments.append(reconciled)
        if uncertainty_changed:
            updated = reconciled_segments
            if on_segments_changed is not None:
                on_segments_changed(
                    [item.model_copy(deep=True) for item in updated]
                )
        decisions = _decision_records(
            request,
            prepared,
            updated,
            changes,
            warnings,
        )
        unresolved = sum(
            item.outcome in {"needs_confirmation", "invalid"}
            for item in decisions
        )
        applied_issue_count = len(
            {item.issue_id for item in decisions if item.outcome == "applied"}
        )
        calls = trace_collector.records()
        timing_unchanged = _timing_snapshot(source_segments) == (
            _timing_snapshot(updated)
        )
        segment_ids_unchanged = [
            item.segment_id for item in source_segments
        ] == [item.segment_id for item in updated]
        if (
            not segment_ids_unchanged
            or not timing_unchanged
            or not locked_preserved
        ):
            raise ValueError(
                "review decisions violated immutable transcript invariants"
            )
        upstream_complete = request.upstream_status == "completed"
        status: Literal["completed", "partial", "failed"] = (
            "partial"
            if unresolved or not upstream_complete
            else "completed"
        )
        return AsrReviewDecisionsResult(
            input=request.model_copy(deep=True),
            status=status,
            profile_id=request.profile_id,
            model_id=calls[0].model_id if calls else None,
            decisions=decisions,
            updated_segments=updated,
            changes=[
                AsrReviewAppliedChange(
                    issue_id=str(item.get("issue_id") or ""),
                    source_task_id=f"review_decisions_r{request.round_index}",
                    round_index=request.round_index,
                    segment_id=str(item.get("segment_id") or ""),
                    before=str(item.get("before") or ""),
                    after=str(item.get("after") or ""),
                    reason=str(item.get("reason") or ""),
                    confidence=float(item.get("confidence") or 0),
                    evidence_source_ids=[
                        str(value)
                        for value in item.get(
                            "evidence_source_ids",
                            [],
                        )
                    ],
                )
                for item in changes
            ],
            cumulative_locked_changes=[
                *[
                    item.model_copy(deep=True)
                    for item in request.locked_changes
                ],
                *[
                    AsrLockedTranscriptChange(
                        segment_id=str(item.get("segment_id") or ""),
                        before=str(item.get("before") or ""),
                        after=str(item.get("after") or ""),
                        reason=str(item.get("reason") or ""),
                        confidence=float(item.get("confidence") or 0),
                        evidence_source_ids=[
                            str(value)
                            for value in item.get(
                                "evidence_source_ids",
                                [],
                            )
                        ],
                        issue_id=str(item.get("issue_id") or "") or None,
                        source_task_id=(
                            f"review_decisions_r{request.round_index}"
                        ),
                        round_index=request.round_index,
                    )
                    for item in changes
                ],
            ],
            warnings=warnings,
            llm_calls=calls,
            duration_ms=_elapsed_ms(started_at),
            quality_summary=AsrReviewDecisionsQualitySummary(
                status=(
                    "warning"
                    if unresolved or not upstream_complete
                    else "passed"
                ),
                issue_count=len(request.issues),
                decided_issue_count=len(decisions),
                applied_change_count=len(changes),
                applied_issue_count=applied_issue_count,
                applied_patch_count=len(changes),
                unresolved_issue_count=unresolved,
                upstream_review_complete=upstream_complete,
                segment_ids_unchanged=segment_ids_unchanged,
                source_timing_unchanged=timing_unchanged,
                only_supplied_issues_considered=True,
                locked_changes_preserved=locked_preserved,
                atomic_patch_sets_preserved=True,
            ),
        )

    def run_prepared(
        self,
        snapshot: AsrReviewDecisionsPreparedSnapshot,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        on_segments_changed: Callable[
            [list[VideoLocalizationTranscriptSegment]],
            None,
        ]
        | None = None,
    ) -> AsrReviewDecisionsResult:
        """Replay deterministic application without repeating the LLM call."""

        return self.run(
            snapshot.request,
            is_cancelled=is_cancelled,
            on_segments_changed=on_segments_changed,
            prepared_snapshot=snapshot,
        )


def apply_decisions(
    segments: list[VideoLocalizationTranscriptSegment],
    decisions: list[dict],
    *,
    evidence: list[dict],
    locked_changes: list[dict] | None = None,
    on_segments_changed: Callable[
        [list[VideoLocalizationTranscriptSegment]],
        None,
    ]
    | None = None,
) -> tuple[list[VideoLocalizationTranscriptSegment], list[dict], list[dict]]:
    """Apply accepted candidates with deterministic safety rules."""

    output = copy.deepcopy(segments)
    by_id = {
        segment.segment_id: index for index, segment in enumerate(output)
    }
    evidence_ids = {
        str(item.get("source_id") or "") for item in evidence
    }
    locked_texts = locked_texts_by_segment(locked_changes or [])
    changes: list[dict] = []
    warnings: list[dict] = []
    for decision in decisions:
        segment_id = str(decision.get("segment_id") or "")
        excerpt = str(decision.get("excerpt") or "").strip()
        replacement = str(decision.get("replacement") or "").strip()
        confidence = normalize_confidence(decision.get("confidence"))
        cited = [
            str(value)
            for value in decision.get("evidence_source_ids", [])
            if str(value) in evidence_ids
        ]
        issue_id = str(decision.get("issue_id") or "")
        raw_patches = decision.get("patches")
        patches = (
            [item for item in raw_patches if isinstance(item, dict)]
            if isinstance(raw_patches, list)
            else []
        )
        if patches:
            if (
                not decision.get("accept")
                or confidence <= BEST_GUESS_CONFIDENCE_FLOOR
            ):
                continue
            planned: list[
                tuple[
                    int,
                    VideoLocalizationTranscriptSegment,
                    str,
                    str,
                ]
            ] = []
            invalid_message = ""
            seen_patch_segments: set[str] = set()
            for patch in patches:
                patch_segment_id = str(
                    patch.get("segment_id") or ""
                ).strip()
                patch_excerpt = str(
                    patch.get("current_excerpt") or ""
                )
                patch_replacement = str(
                    patch.get("proposed_replacement") or ""
                )
                if (
                    not patch_segment_id
                    or patch_segment_id in seen_patch_segments
                    or patch_segment_id not in by_id
                    or not patch_excerpt
                ):
                    invalid_message = (
                        "跨片段修改的目标不完整，整组修改已回滚。"
                    )
                    break
                seen_patch_segments.add(patch_segment_id)
                index = by_id[patch_segment_id]
                source = output[index]
                current = source.corrected_text or source.raw_text
                if current.count(patch_excerpt) != 1:
                    invalid_message = (
                        "跨片段建议无法唯一对应当前原文，整组修改已回滚。"
                    )
                    break
                candidate = current.replace(
                    patch_excerpt,
                    patch_replacement,
                    1,
                ).strip()
                planned.append((index, source, current, candidate))
            if (
                not invalid_message
                and any(not candidate for *_, candidate in planned)
            ):
                rebalanced = _rebalance_cross_segment_candidates(planned)
                if rebalanced is None:
                    invalid_message = (
                        "跨片段建议无法在原有片段间安全重分配，"
                        "整组修改已回滚。"
                    )
                else:
                    planned = rebalanced
            if not invalid_message and any(
                any(
                    current.count(locked_text)
                    > candidate.count(locked_text)
                    for locked_text in locked_texts.get(
                        source.segment_id,
                        [],
                    )
                )
                for _, source, current, candidate in planned
            ):
                invalid_message = (
                    "跨片段建议会覆盖已锁定修改，整组修改已回滚。"
                )
            before_span = " ".join(
                current for _, _, current, _ in planned
            )
            after_span = " ".join(
                candidate for _, _, _, candidate in planned
            )
            if (
                not invalid_message
                and (
                    len(planned) != len(patches)
                    or number_components(before_span)
                    != number_components(after_span)
                    or negations(before_span) != negations(after_span)
                )
            ):
                invalid_message = (
                    "跨片段建议改变了数字或否定关系，整组修改已回滚。"
                )
            if invalid_message:
                warnings.append(
                    _warning(
                        "unsafe_change",
                        issue_id,
                        segment_id,
                        excerpt,
                        invalid_message,
                    )
                )
                continue
            for index, source, current, candidate in planned:
                output[index] = source.model_copy(
                    update={
                        "corrected_text": candidate,
                        "review_confidence": confidence,
                        "review_flags": sorted(
                            set(
                                [
                                    *source.review_flags,
                                    "asr_flow_corrected",
                                    "asr_cross_segment_corrected",
                                ]
                            )
                        ),
                    }
                )
                changes.append(
                    {
                        "issue_id": issue_id,
                        "segment_id": source.segment_id,
                        "before": current,
                        "after": candidate,
                        "reason": str(
                            decision.get("reason") or ""
                        )[:500],
                        "confidence": confidence,
                        "evidence_source_ids": cited,
                        "replacement": candidate,
                    }
                )
            if on_segments_changed is not None:
                on_segments_changed(
                    [item.model_copy(deep=True) for item in output]
                )
            continue
        if (
            not decision.get("accept")
            or confidence <= BEST_GUESS_CONFIDENCE_FLOOR
            or segment_id not in by_id
            or not excerpt
            or not replacement
        ):
            continue
        index = by_id[segment_id]
        source = output[index]
        current = source.corrected_text or source.raw_text
        if current.count(excerpt) != 1:
            warnings.append(
                _warning(
                    "unsafe_change",
                    issue_id,
                    segment_id,
                    excerpt,
                    "建议无法唯一对应当前片段。",
                )
            )
            continue
        candidate = current.replace(excerpt, replacement, 1)
        if (
            number_components(current) != number_components(candidate)
            or negations(current) != negations(candidate)
        ):
            warnings.append(
                _warning(
                    "unsafe_change",
                    issue_id,
                    segment_id,
                    excerpt,
                    "建议会改变数字或否定关系，已保留原文。",
                )
            )
            continue
        if any(
            current.count(locked_text) > candidate.count(locked_text)
            for locked_text in locked_texts.get(segment_id, [])
        ):
            warnings.append(
                _warning(
                    "protected_change",
                    issue_id,
                    segment_id,
                    excerpt,
                    "这一处已经在前一轮确认修改，本轮不再覆盖。",
                )
            )
            continue
        if looks_like_entity_change(
            excerpt,
            replacement,
            str(decision.get("reason") or ""),
        ):
            if cited and not evidence_supports_replacement(
                replacement,
                cited,
                evidence,
            ):
                warnings.append(
                    _warning(
                        "unsafe_change",
                        issue_id,
                        segment_id,
                        excerpt,
                        "引用资料没有出现建议的新名称，已保留原文。",
                    )
                )
                continue
            if not cited:
                warnings.append(
                    _warning(
                        "needs_confirmation",
                        issue_id,
                        segment_id,
                        excerpt,
                        "这个名称没有查到可靠资料，已保留当前文本并继续；"
                        "建议结合原音复听。",
                    )
                )
                continue
        output[index] = source.model_copy(
            update={
                "corrected_text": candidate,
                "review_confidence": confidence,
                "review_flags": sorted(
                    set(
                        [
                            *source.review_flags,
                            "asr_flow_corrected",
                        ]
                    )
                ),
            }
        )
        changes.append(
            {
                "issue_id": issue_id,
                "segment_id": segment_id,
                "before": current,
                "after": candidate,
                "reason": str(decision.get("reason") or "")[:500],
                "confidence": confidence,
                "evidence_source_ids": cited,
                "replacement": replacement,
            }
        )
        if on_segments_changed is not None:
            on_segments_changed(
                [item.model_copy(deep=True) for item in output]
            )
    return output, changes, warnings


def locked_texts_by_segment(changes: list[dict]) -> dict[str, list[str]]:
    locked: dict[str, list[str]] = {}
    for change in changes:
        segment_id = str(change.get("segment_id") or "")
        if not segment_id:
            continue
        values = locked.setdefault(segment_id, [])
        for fragment in changed_after_fragments(
            str(change.get("before") or ""),
            str(change.get("after") or ""),
        ):
            if fragment not in values:
                values.append(fragment)
    return locked


def changed_after_fragments(before: str, after: str) -> list[str]:
    fragments: list[str] = []
    for (
        tag,
        _before_start,
        _before_end,
        after_start,
        after_end,
    ) in SequenceMatcher(None, before, after).get_opcodes():
        if tag == "equal":
            continue
        fragment = after[after_start:after_end].strip()
        if fragment and any(character.isalnum() for character in fragment):
            fragments.append(fragment)
    return fragments


def looks_like_entity_change(
    excerpt: str,
    replacement: str,
    reason: str,
) -> bool:
    description = f"{reason} {excerpt} {replacement}"
    if re.search(
        r"\b(?:product|proper name|brand|model|platform|software|tool|name)\b|"
        r"产品|专名|品牌|型号|平台|软件|工具|名称",
        description,
        re.IGNORECASE,
    ):
        return True
    changed = changed_replacement_tokens(excerpt, replacement)
    return any(
        token[:1].isupper() and len(token) >= 4 for token in changed
    )


def evidence_supports_replacement(
    replacement: str,
    cited: list[str],
    evidence: list[dict],
) -> bool:
    replacement_tokens = [
        token.casefold()
        for token in re.findall(
            r"[A-Za-z][A-Za-z0-9'-]{3,}",
            replacement,
        )
    ]
    if not replacement_tokens:
        return False
    cited_set = set(cited)
    evidence_text = " ".join(
        f"{item.get('title', '')} {item.get('snippet', '')}".casefold()
        for item in evidence
        if str(item.get("source_id") or "") in cited_set
    )
    return any(
        re.search(
            rf"(?<!\w){re.escape(token)}(?!\w)",
            evidence_text,
        )
        for token in replacement_tokens
    )


def changed_replacement_tokens(
    excerpt: str,
    replacement: str,
) -> list[str]:
    left = re.findall(r"[A-Za-z0-9'-]+", excerpt)
    right = re.findall(r"[A-Za-z0-9'-]+", replacement)
    output: list[str] = []
    for tag, _ls, _le, rs, re_ in SequenceMatcher(
        None,
        [item.casefold() for item in left],
        [item.casefold() for item in right],
    ).get_opcodes():
        if tag != "equal":
            output.extend(right[rs:re_])
    return output


def number_components(value: str) -> Counter:
    """Preserve spoken digits while allowing punctuation-only repair."""

    return Counter(re.findall(r"\d+", value))


def negations(value: str) -> Counter:
    return Counter(
        re.findall(
            r"\b(?:no|not|never|cannot|can't|won't|don't|doesn't|"
            r"didn't|isn't|aren't|wasn't|weren't)\b",
            value.casefold(),
        )
    )


def normalize_confidence(value: object) -> float:
    if isinstance(value, str):
        aliases = {"high": 0.9, "medium": 0.7, "low": 0.4}
        if value.casefold() in aliases:
            return aliases[value.casefold()]
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _rebalance_cross_segment_candidates(
    planned: list[
        tuple[int, VideoLocalizationTranscriptSegment, str, str]
    ],
) -> (
    list[tuple[int, VideoLocalizationTranscriptSegment, str, str]]
    | None
):
    """Keep adjacent transcript IDs by redistributing one merged correction."""

    if len(planned) != 2:
        return None
    combined_words = " ".join(
        candidate for *_, candidate in planned if candidate
    ).split()
    if len(combined_words) < 2:
        return None
    source_word_counts = [
        max(1, len(current.split()))
        for _, _, current, _ in planned
    ]
    first_word_count = round(
        len(combined_words)
        * source_word_counts[0]
        / sum(source_word_counts)
    )
    first_word_count = max(
        1,
        min(len(combined_words) - 1, first_word_count),
    )
    redistributed = [
        " ".join(combined_words[:first_word_count]),
        " ".join(combined_words[first_word_count:]),
    ]
    return [
        (index, source, current, redistributed[position])
        for position, (index, source, current, _) in enumerate(planned)
    ]


def _complete_json(
    request: AsrReviewDecisionsInput,
    *,
    trace_collector: AsrLlmTraceCollector,
    call_group: Literal["primary", "coverage"] = "primary",
    completion_gateway: (
        AsrReviewDecisionsCompletionGateway | None
    ) = None,
) -> dict:
    prompt = (
        "你负责对上一任务已经发现的 ASR 疑点做最终判断。只能处理输入中的"
        " issue_id，不得增加新的修改。结合完整上下文、查证资料和已锁定修改，"
        "在当前文本和 proposed_replacement 之间选择更可能接近原话的一项；"
        "scope=adjacent_segments 的疑点包含 patches，必须把整组补丁作为"
        "一个决定整体接受或整体拒绝，不能只接受其中一处。"
        "流程不会停下来等待人工确认，因此不要返回 needs_confirmation。"
        "只做最小听写修正，不要翻译、润色或调整文风。规范名称必须引用确实"
        "包含该拼写的资料。前面确认的修改不得撤销。confidence 表示所选结果"
        "相对另一候选更可能正确的把握；short_audio_relisten 是只重听疑点"
        "音频得到的第二份 ASR 候选，需作为声音证据参与选择，但不是绝对真值。"
        "reason 使用简体中文。返回 JSON。"
    )
    payload = {
        "document": {
            "summary": request.document_summary,
            "content_logic": request.content_logic,
            "speaker_style": request.speaker_style,
            "transcript": _compact_transcript(request.segments),
        },
        "candidate_issues": [
            _compact_issue(item) for item in request.issues
        ],
        "research_evidence": [
            {
                "id": item.evidence_id,
                "title": item.title,
                "snippet": item.snippet,
            }
            for item in request.evidence
        ],
        "locked_changes": [
            {
                "segment_id": item.segment_id,
                "before": item.before,
                "after": item.after,
            }
            for item in request.locked_changes[-100:]
        ],
        "short_audio_relisten": [
            {
                "section_id": item.section_id,
                "issue_ids": list(item.issue_ids),
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "text": item.text,
            }
            for item in request.acoustic_candidates
        ],
        "output": {
            "decisions": [
                {
                    "issue_id": "",
                    "accept": False,
                    "reason": "",
                    "confidence": 0.0,
                    "evidence_source_ids": [],
                }
            ]
        },
    }

    def call(attempt: int, *, disable_reasoning: bool) -> dict:
        trace_sink = trace_collector.sink(
            call_id=(
                f"review-decisions-r{request.round_index}-"
                f"{call_group}-a{attempt:02d}"
            ),
            purpose="review_decisions",
            round_index=request.round_index,
            candidate_ids=[
                item.issue_id for item in request.issues
            ],
        )
        completion_request = AsrReviewDecisionsCompletionRequest(
            contract_version="asr-review-decisions-call-input-v1",
            behavior_version=PROMPT_VERSION,
            call_group=call_group,
            attempt=attempt,
            round_index=request.round_index,
            issue_ids=tuple(
                item.issue_id for item in request.issues
            ),
            system_prompt=prompt,
            user_payload=payload,
            profile_id=request.profile_id,
            max_tokens=8_000 if attempt == 1 else 12_000,
            timeout=240,
            disable_reasoning=disable_reasoning,
            trace_sink=trace_sink,
        )
        if completion_gateway is not None:
            return completion_gateway(completion_request)
        return llm_runtime.complete_json(
            system_prompt=completion_request.system_prompt,
            user_payload=completion_request.user_payload,
            profile_id=completion_request.profile_id,
            max_tokens=completion_request.max_tokens,
            timeout=completion_request.timeout,
            disable_reasoning=completion_request.disable_reasoning,
            trace_sink=completion_request.trace_sink,
        )

    try:
        raw = call(1, disable_reasoning=False)
    except llm_runtime.LlmRuntimeError as exc:
        if exc.code not in RETRYABLE_OUTPUT_CODES:
            raise
        raw = call(2, disable_reasoning=True)
    if not isinstance(raw, dict):
        raise llm_runtime.LlmRuntimeError(
            "语言模型没有返回对象",
            code="llm_json_not_object",
            status_code=502,
        )
    return raw


def _missing_issue_ids(
    request: AsrReviewDecisionsInput,
    raw: dict,
) -> set[str]:
    expected = {item.issue_id for item in request.issues}
    raw_items = raw.get("decisions")
    if not isinstance(raw_items, list):
        return expected
    returned = {
        str(item.get("issue_id") or "").strip()
        for item in raw_items
        if isinstance(item, dict)
    }
    return expected - returned


def _prepare_decisions(
    request: AsrReviewDecisionsInput,
    raw: dict,
) -> tuple[list[dict], list[AsrReviewDecisionWarning]]:
    issues = {item.issue_id: item for item in request.issues}
    raw_items = raw.get("decisions")
    raw_items = raw_items if isinstance(raw_items, list) else []
    prepared: list[dict] = []
    warnings: list[AsrReviewDecisionWarning] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        issue_id = str(item.get("issue_id") or "").strip()
        issue = issues.get(issue_id)
        if issue is None or issue_id in seen:
            warnings.append(
                AsrReviewDecisionWarning(
                    code="invalid_decision",
                    issue_id=issue_id,
                    message="模型返回了不属于本轮疑点的决定，已忽略。",
                )
            )
            continue
        seen.add(issue_id)
        replacement = str(item.get("replacement") or "").strip()
        accepted = bool(item.get("accept"))
        invalid_replacement = (
            accepted
            and (
                not issue.proposed_replacement
                or (
                    replacement
                    and replacement != issue.proposed_replacement
                )
            )
        )
        if invalid_replacement:
            warnings.append(
                AsrReviewDecisionWarning(
                    code="invalid_decision",
                    issue_id=issue_id,
                    segment_id=issue.segment_id,
                    excerpt=issue.current_excerpt,
                    message="接受结果超出了上一任务提出的修改，已保留原文。",
                )
            )
            prepared.append(
                _prepared_decision(
                    issue,
                    item,
                    accept=False,
                )
            )
            continue
        prepared.append(_prepared_decision(issue, item))
    for issue in request.issues:
        if issue.issue_id in seen:
            continue
        warnings.append(
            AsrReviewDecisionWarning(
                code="missing_decision",
                issue_id=issue.issue_id,
                segment_id=issue.segment_id,
                excerpt=issue.current_excerpt,
                message="模型没有返回这一条疑点的判断，已保留原文。",
            )
        )
        prepared.append(
            {
                "issue_id": issue.issue_id,
                "segment_id": issue.segment_id,
                "excerpt": issue.current_excerpt,
                "accept": False,
                "replacement": issue.proposed_replacement,
                "reason": "模型没有返回这一条疑点的判断。",
                "confidence": 0.0,
                "needs_confirmation": False,
                "evidence_source_ids": [],
            }
        )
    return prepared, warnings


def _prepared_decision(
    issue: AsrReviewDecisionIssue,
    item: dict,
    *,
    accept: bool | None = None,
) -> dict:
    confidence = normalize_confidence(item.get("confidence"))
    accepted = bool(item.get("accept")) if accept is None else accept
    reason = str(item.get("reason") or issue.reason).strip()
    if accepted and confidence <= BEST_GUESS_CONFIDENCE_FLOOR:
        accepted = False
        reason = "建议文本并不比当前文本更可能正确，保留当前最高概率文本。"
    return {
        "issue_id": issue.issue_id,
        "segment_id": issue.segment_id,
        "excerpt": issue.current_excerpt,
        "accept": accepted,
        "replacement": (
            issue.proposed_replacement
            if accepted
            else str(item.get("replacement") or "").strip()
        ),
        "reason": reason,
        "confidence": confidence,
        "needs_confirmation": False,
        "evidence_source_ids": [
            str(value)
            for value in item.get("evidence_source_ids", [])
        ],
        "patches": [
            patch.model_dump(mode="json")
            for patch in issue.patches
        ],
    }


def _compact_transcript(
    segments: list[VideoLocalizationTranscriptSegment],
) -> str:
    return "\n".join(
        (
            f"[{item.segment_id}]"
            + (
                f" {item.speaker_cluster_id}:"
                if item.speaker_cluster_id
                else ""
            )
            + f" {item.corrected_text or item.raw_text}"
        )
        for item in segments
    )


def _compact_issue(item: AsrReviewDecisionIssue) -> dict:
    payload = {
        "issue_id": item.issue_id,
        "segment_id": item.segment_id,
        "current": item.current_excerpt,
        "candidate": item.proposed_replacement,
        "reason": item.reason,
        "confidence": item.confidence,
        "evidence_ids": list(item.evidence_source_ids),
    }
    if item.patches:
        payload["patches"] = [
            patch.model_dump(mode="json") for patch in item.patches
        ]
    return payload


def _decision_records(
    request: AsrReviewDecisionsInput,
    prepared: list[dict],
    updated: list[VideoLocalizationTranscriptSegment],
    changes: list[dict],
    warnings: list[AsrReviewDecisionWarning],
) -> list[AsrReviewDecisionRecord]:
    prepared_by_issue = {
        str(item.get("issue_id") or ""): item for item in prepared
    }
    changes_by_issue: dict[str, list[dict]] = {}
    for item in changes:
        changes_by_issue.setdefault(
            str(item.get("issue_id") or ""),
            [],
        ).append(item)
    warning_by_issue: dict[str, AsrReviewDecisionWarning] = {}
    for item in warnings:
        if item.issue_id:
            warning_by_issue.setdefault(item.issue_id, item)
    updated_by_id = {item.segment_id: item for item in updated}
    records: list[AsrReviewDecisionRecord] = []
    for issue in request.issues:
        decision = prepared_by_issue.get(issue.issue_id, {})
        issue_changes = changes_by_issue.get(issue.issue_id, [])
        warning = warning_by_issue.get(issue.issue_id)
        if issue_changes:
            outcome = "applied"
            before_text = " | ".join(
                str(change.get("before") or "")
                for change in issue_changes
            )
            after_text = " | ".join(
                str(change.get("after") or "")
                for change in issue_changes
            )
        elif warning is not None:
            outcome = (
                "invalid"
                if warning.code
                in {"invalid_decision", "missing_decision"}
                else "needs_confirmation"
            )
            segment = updated_by_id.get(issue.segment_id)
            before_text = (
                segment.corrected_text or segment.raw_text
                if segment is not None
                else ""
            )
            after_text = before_text
        else:
            outcome = "rejected"
            segment = updated_by_id.get(issue.segment_id)
            before_text = (
                segment.corrected_text or segment.raw_text
                if segment is not None
                else ""
            )
            after_text = before_text
        records.append(
            AsrReviewDecisionRecord(
                issue_id=issue.issue_id,
                section_id=issue.section_id,
                segment_id=issue.segment_id,
                current_excerpt=issue.current_excerpt,
                proposed_replacement=issue.proposed_replacement,
                outcome=outcome,
                reason=(
                    warning.message
                    if warning is not None
                    else str(decision.get("reason") or issue.reason)
                ),
                confidence=normalize_confidence(
                    decision.get("confidence")
                ),
                evidence_source_ids=[
                    str(value)
                    for value in decision.get(
                        "evidence_source_ids",
                        [],
                    )
                ],
                scope=issue.scope,
                target_segment_ids=list(issue.target_segment_ids),
                origin=issue.origin,
                patches=[
                    item.model_copy(deep=True)
                    for item in issue.patches
                ],
                before_text=before_text,
                after_text=after_text,
            )
        )
    return records


def _warning(
    code: Literal[
        "needs_confirmation",
        "unsafe_change",
        "protected_change",
    ],
    issue_id: str,
    segment_id: str,
    excerpt: str,
    message: str,
) -> dict:
    return {
        "code": code,
        "issue_id": issue_id,
        "segment_id": segment_id,
        "excerpt": excerpt,
        "message": (
            message
            or "这句话可能有识别错误，但现有资料还不能确认。"
        ),
    }


def _timing_snapshot(
    segments: list[VideoLocalizationTranscriptSegment],
) -> list[tuple[str, int, int]]:
    return [
        (item.segment_id, item.start_ms, item.end_ms)
        for item in segments
    ]


def build_review_decisions_input(
    review: AsrSectionReviewResult,
    *,
    upstream_operation_id: str,
    profile_id: str | None = None,
) -> AsrReviewDecisionsInput:
    """Build one atomic decision input from one immutable review result."""

    if review.status == "failed":
        raise ValueError(
            "review decisions cannot use a failed section review"
        )
    selected_profile = (
        str(profile_id or "").strip()
        or str(review.profile_id or "").strip()
        or str(review.input.profile_id or "").strip()
    )
    if not selected_profile:
        raise ValueError("review decisions require an LLM profile")
    return AsrReviewDecisionsInput(
        upstream_contract_version=review.contract_version,
        upstream_operation_id=upstream_operation_id,
        source_track_id=review.input.source_track_id,
        source_audio_sha256=review.input.source_audio_sha256,
        language=review.input.language,
        profile_id=selected_profile,
        round_index=review.input.round_index,
        document_summary=review.input.document_summary,
        content_logic=list(review.input.content_logic),
        speaker_style=review.input.speaker_style,
        segments=[
            item.model_copy(deep=True)
            for item in review.input.segments
        ],
        issues=[
            AsrReviewDecisionIssue(
                **item.model_dump(mode="json")
            )
            for item in review.issues
        ],
        evidence=[
            item.model_copy(deep=True)
            for item in review.input.evidence
        ],
        locked_changes=[
            item.model_copy(deep=True)
            for item in review.input.locked_changes
        ],
        acoustic_candidates=[
            item.model_copy(deep=True)
            for item in review.input.acoustic_candidates
        ],
        upstream_status=review.status,
        upstream_section_runs=[
            AsrReviewUpstreamSectionRun.model_validate(
                item.model_dump(mode="json")
            )
            for item in review.section_runs
        ],
        upstream_warnings=[
            AsrReviewUpstreamWarning.model_validate(
                item.model_dump(mode="json")
            )
            for item in review.warnings
        ],
    )


def _preserves_locked_changes(
    source_segments: list[VideoLocalizationTranscriptSegment],
    updated_segments: list[VideoLocalizationTranscriptSegment],
    locked_changes: list[AsrLockedTranscriptChange],
) -> bool:
    source_text_by_id = {
        item.segment_id: item.corrected_text or item.raw_text
        for item in source_segments
    }
    updated_text_by_id = {
        item.segment_id: item.corrected_text or item.raw_text
        for item in updated_segments
    }
    locked = locked_texts_by_segment(
        [item.model_dump(mode="json") for item in locked_changes]
    )
    return all(
        all(
            (
                updated_text_by_id.get(segment_id, "").count(fragment)
                >= source_text_by_id.get(segment_id, "").count(fragment)
            )
            for fragment in values
            if source_text_by_id.get(segment_id, "").count(fragment) > 0
        )
        for segment_id, values in locked.items()
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, int(round((time.perf_counter() - started_at) * 1000)))


DEFAULT_REVIEW_DECISIONS_SERVICE = ReviewDecisionsService()
