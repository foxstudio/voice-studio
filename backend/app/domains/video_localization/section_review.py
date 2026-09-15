"""Evidence-aware, read-only review of one transcript section plan."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization import (
    asr_targeted_relisten,
    entity_normalization,
    research_evidence,
    transcript_boundary_issues,
    transcript_text_rules,
)
from app.domains.video_localization.document_understanding_contracts import (
    AsrDocumentUnderstandingResult,
)
from app.domains.video_localization.llm_observability import (
    AsrLlmCallRecord,
    AsrLlmTraceCollector,
)
from app.domains.video_localization.review_contracts import (
    AsrTranscriptEditPatch,
)
from app.domains.video_localization.review_warning_policy import (
    warning_needs_reader_review,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationGlossaryEntry,
    VideoLocalizationTranscriptSegment,
)
from app.services import llm_runtime

PROMPT_VERSION = "asr-section-review-v5"
REVIEW_OVERLAP_SEGMENTS = 6
RETRYABLE_OUTPUT_CODES = frozenset(
    {"llm_json_invalid", "llm_json_not_object", "llm_output_truncated"}
)


@dataclass(frozen=True)
class AsrSectionReviewCompletionRequest:
    """One explicit model attempt for one immutable transcript section."""

    contract_version: Literal[
        "asr-section-review-call-input-v1"
    ]
    behavior_version: Literal["asr-section-review-v5"]
    call_id: str
    section_id: str
    section_start_ordinal: int
    core_segment_ids: tuple[str, ...]
    attempt: int
    round_index: int
    system_prompt: str
    user_payload: dict
    profile_id: str
    max_tokens: int
    timeout: float
    disable_reasoning: bool
    trace_sink: llm_runtime.TraceSink | None = None


CompletionGateway = Callable[
    [AsrSectionReviewCompletionRequest],
    dict,
]


class AsrSectionReviewSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1)
    start_ordinal: int = Field(ge=1)
    end_ordinal: int = Field(ge=1)
    start_segment_id: str = Field(min_length=1)
    end_segment_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    focus: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self) -> AsrSectionReviewSection:
        if self.end_ordinal < self.start_ordinal:
            raise ValueError("end_ordinal must not be before start_ordinal")
        return self


class AsrSectionReviewInput(BaseModel):
    """Complete, serializable input for one read-only section review round."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-section-review-input-v3"] = (
        "asr-section-review-input-v3"
    )
    upstream_contract_version: Literal[
        "asr-entity-normalization-v1",
        "asr-transcript-review-v2",
    ]
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
    sections: list[AsrSectionReviewSection] = Field(min_length=1)
    segments: list[VideoLocalizationTranscriptSegment] = Field(min_length=1)
    evidence: list[research_evidence.AsrResearchEvidenceItem] = Field(
        default_factory=list
    )
    resolved_entities: list[entity_normalization.AsrEntityResolution] = Field(
        default_factory=list
    )
    glossary: list[VideoLocalizationGlossaryEntry] = Field(default_factory=list)
    locked_changes: list[
        entity_normalization.AsrLockedTranscriptChange
    ] = Field(default_factory=list)
    acoustic_candidates: list[
        asr_targeted_relisten.AsrAcousticCandidate
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sections(self) -> AsrSectionReviewInput:
        segment_count = len(self.segments)
        expected_start = 1
        section_ids: set[str] = set()
        for section in self.sections:
            if section.section_id in section_ids:
                raise ValueError("review section IDs must be unique")
            section_ids.add(section.section_id)
            if section.end_ordinal > segment_count:
                raise ValueError("review section exceeds transcript length")
            if (
                self.round_index == 1
                and section.start_ordinal != expected_start
            ):
                raise ValueError(
                    "review sections must continuously cover the transcript"
                )
            if (
                self.round_index == 2
                and section.start_ordinal < expected_start
            ):
                raise ValueError(
                    "targeted review sections must be ordered and disjoint"
                )
            if (
                self.segments[
                    section.start_ordinal - 1
                ].segment_id != section.start_segment_id
                or self.segments[
                    section.end_ordinal - 1
                ].segment_id != section.end_segment_id
            ):
                raise ValueError(
                    "review section boundaries do not match transcript order"
                )
            expected_start = section.end_ordinal + 1
        if (
            self.round_index == 1
            and expected_start != segment_count + 1
        ):
            raise ValueError(
                "review sections must continuously cover the transcript"
            )
        candidate_section_ids = [
            item.section_id for item in self.acoustic_candidates
        ]
        if (
            len(candidate_section_ids) != len(set(candidate_section_ids))
            or any(
                section_id not in section_ids
                for section_id in candidate_section_ids
            )
        ):
            raise ValueError(
                "acoustic candidates must uniquely match review sections"
            )
        return self


class AsrSectionReviewIssue(BaseModel):
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
    def validate_targets(self) -> AsrSectionReviewIssue:
        if not self.target_segment_ids:
            self.target_segment_ids = [self.segment_id]
        if (
            len(self.target_segment_ids) not in {1, 2}
            or len(set(self.target_segment_ids))
            != len(self.target_segment_ids)
            or self.segment_id != self.target_segment_ids[0]
        ):
            raise ValueError("review issue target segments are invalid")
        expected_scope = (
            "adjacent_segments"
            if len(self.target_segment_ids) == 2
            else "single_segment"
        )
        if self.scope != expected_scope:
            raise ValueError("review issue scope does not match its targets")
        if self.patches:
            patch_ids = [item.segment_id for item in self.patches]
            if (
                len(patch_ids) != len(set(patch_ids))
                or set(patch_ids) != set(self.target_segment_ids)
            ):
                raise ValueError(
                    "review issue patches must cover each target once"
                )
        return self


class AsrSectionReviewWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["section_failed", "invalid_issue"]
    section_id: str = ""
    message: str = Field(min_length=1)


class AsrSectionReviewSectionRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1)
    status: Literal["completed", "failed"]
    duration_ms: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    llm_call_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


class AsrSectionReviewQualitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "warning", "failed"]
    section_count: int = Field(ge=1)
    checked_section_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    sections_cover_all_segments: bool
    source_text_unchanged: bool
    adjacent_boundary_count: int = Field(default=0, ge=0)
    checked_adjacent_boundary_count: int = Field(default=0, ge=0)
    cross_segment_issue_count: int = Field(default=0, ge=0)
    all_adjacent_boundaries_checked: bool = True
    acoustic_candidate_count: int = Field(default=0, ge=0)


class AsrSectionReviewResult(BaseModel):
    """Read-only issue list; it never applies transcript edits."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-section-review-v4"] = (
        "asr-section-review-v4"
    )
    input: AsrSectionReviewInput
    status: Literal["completed", "partial", "failed"]
    profile_id: str
    model_id: str | None = None
    prompt_version: Literal["asr-section-review-v5"] = PROMPT_VERSION
    section_runs: list[AsrSectionReviewSectionRun] = Field(default_factory=list)
    issues: list[AsrSectionReviewIssue] = Field(default_factory=list)
    warnings: list[AsrSectionReviewWarning] = Field(default_factory=list)
    llm_calls: list[AsrLlmCallRecord] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    quality_summary: AsrSectionReviewQualitySummary


class SectionReviewService:
    """Single atomic implementation shared by formal and development flows."""

    def run(
        self,
        request: AsrSectionReviewInput,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        completion_gateway: CompletionGateway | None = None,
    ) -> AsrSectionReviewResult:
        started_at = time.perf_counter()
        source_segments = [item.model_copy(deep=True) for item in request.segments]
        jobs = self._jobs(request)
        boundary_issues = (
            transcript_boundary_issues.find_transcript_boundary_issues(
                request.segments,
                language=request.language,
            )
        )
        text_candidates = (
            transcript_text_rules.find_transcript_text_candidates(
                request.segments,
                language=request.language,
            )
        )

        def run_job(
            job: tuple[
                AsrSectionReviewSection,
                list[VideoLocalizationTranscriptSegment],
                list[VideoLocalizationTranscriptSegment],
                list[VideoLocalizationTranscriptSegment],
            ],
        ) -> tuple[
            list[AsrSectionReviewIssue],
            list[AsrSectionReviewWarning],
            AsrSectionReviewSectionRun,
            list[AsrLlmCallRecord],
        ]:
            job_started_at = time.perf_counter()
            trace_collector = AsrLlmTraceCollector()
            if is_cancelled and is_cancelled():
                raise llm_runtime.LlmRuntimeError(
                    "分段复查已取消",
                    code="llm_cancelled",
                    status_code=409,
                )
            section, before, core, after = job
            core_ids = {item.segment_id for item in core}
            local_boundary_issues = [
                item
                for item in boundary_issues
                if item.primary_segment_id in core_ids
            ]
            local_text_candidates = [
                item
                for item in text_candidates
                if item.segment_id in core_ids
            ]
            deterministic_issues = [
                _section_issue_from_boundary(item, section=section)
                for item in local_boundary_issues
            ]
            deterministic_issues.extend(
                _section_issue_from_text_candidate(
                    item,
                    section=section,
                )
                for item in local_text_candidates
                if item.proposed_replacement
            )
            prompt = (
                "你正在复查一段连续的 ASR 原文。结合全文概述、当前区块重点、"
                "前后文、已查资料和已锁定修改，逐条检查核心片段里可能的听错词、"
                "数字、否定关系、指代、句意和相邻片段衔接问题。不要翻译或润色"
                "文风；普通装饰性标点不必修改，但标点、大小写或分段造成句意、"
                "问答关系或语法结构错误时必须报告。"
                "本地规则给出的句内候选必须逐条判断：它们只是待核对信号，"
                "不能因为规则命中就直接删词；若确属真实口语重复应保留。"
                "前后文只能帮助判断，不能提出修改。已确认的规范名称和修改不得倒改。"
                "证据不够时 replacement 留空并标记 needs_confirmation=true。"
                "short_audio_relisten 是只对当前疑点音频重新识别得到的第二份"
                "声音候选，不是绝对真值；它与现文一致时可提高把握，不一致时"
                "要结合语法和上下文判断最可能原话。"
                "只返回确实值得下一步判断的问题，reason 必须使用简体中文。返回 JSON。"
            )
            local_acoustic_candidates = [
                item
                for item in request.acoustic_candidates
                if item.section_id == section.section_id
            ]
            payload = {
                "document": {
                    "summary": request.document_summary,
                    "content_logic": request.content_logic,
                    "speaker_style": request.speaker_style,
                },
                "section": section.model_dump(mode="json"),
                "context_before": _segment_payload(before),
                "core_segments": _segment_payload(core),
                "context_after": _segment_payload(after),
                "deterministic_boundary_candidates": [
                    item.model_dump(mode="json")
                    for item in local_boundary_issues
                ],
                "deterministic_text_candidates": [
                    item.model_dump(mode="json")
                    for item in local_text_candidates
                ],
                "resolved_entities": [
                    {
                        "name": item.canonical_name,
                        "variants": list(item.variants),
                        "confidence": item.confidence,
                        "evidence_ids": list(
                            item.evidence_source_ids
                        ),
                    }
                    for item in request.resolved_entities
                ],
                "research_evidence": [
                    {
                        "id": item.evidence_id,
                        "title": item.title,
                        "snippet": item.snippet,
                    }
                    for item in request.evidence
                ],
                "glossary": [
                    {
                        "source": item.source_text,
                        "corrected": item.corrected_source_text,
                        "note": item.notes,
                    }
                    for item in request.glossary[:100]
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
                        "issue_ids": list(item.issue_ids),
                        "start_ms": item.start_ms,
                        "end_ms": item.end_ms,
                        "text": item.text,
                    }
                    for item in local_acoustic_candidates
                ],
                "output": {
                    "issues": [
                        {
                            "segment_id": "",
                            "current_excerpt": "",
                            "replacement": "",
                            "patches": [
                                {
                                    "segment_id": "",
                                    "current_excerpt": "",
                                    "proposed_replacement": "",
                                }
                            ],
                            "reason": "",
                            "confidence": 0.0,
                            "needs_confirmation": False,
                            "evidence_source_ids": [],
                        }
                    ]
                },
            }
            try:
                raw = _complete_json(
                    prompt,
                    payload,
                    profile_id=request.profile_id,
                    trace_collector=trace_collector,
                    section=section,
                    core_segment_ids=tuple(
                        item.segment_id for item in core
                    ),
                    round_index=request.round_index,
                    completion_gateway=completion_gateway,
                )
            except llm_runtime.LlmRuntimeError as exc:
                if exc.code == "llm_cancelled":
                    raise
                warning = AsrSectionReviewWarning(
                    code="section_failed",
                    section_id=section.section_id,
                    message=(
                        f"{section.section_id} 本轮没有完成："
                        f"{str(exc)[:200]}"
                    ),
                )
                calls = trace_collector.records()
                return (
                    deterministic_issues,
                    [warning],
                    AsrSectionReviewSectionRun(
                        section_id=section.section_id,
                        status="failed",
                        duration_ms=_elapsed_ms(job_started_at),
                        issue_count=len(deterministic_issues),
                        llm_call_ids=[
                            item.call_id for item in calls
                        ],
                        error_code=exc.code,
                        error_message=str(exc)[:200],
                    ),
                    calls,
                )
            found, found_warnings = _parse_issues(
                raw,
                section=section,
                core=core,
                adjacent_editable=[*core, *after[:1]],
                evidence_ids={
                    item.evidence_id for item in request.evidence
                },
            )
            found.extend(deterministic_issues)
            calls = trace_collector.records()
            return (
                found,
                found_warnings,
                AsrSectionReviewSectionRun(
                    section_id=section.section_id,
                    status="completed",
                    duration_ms=_elapsed_ms(job_started_at),
                    issue_count=len(found),
                    llm_call_ids=[
                        item.call_id for item in calls
                    ],
                ),
                calls,
            )

        issues: list[AsrSectionReviewIssue] = []
        warnings: list[AsrSectionReviewWarning] = []
        section_runs: list[AsrSectionReviewSectionRun] = []
        llm_calls: list[AsrLlmCallRecord] = []
        with ThreadPoolExecutor(
            max_workers=min(4, max(1, len(jobs)))
        ) as executor:
            for (
                found,
                found_warnings,
                section_run,
                section_calls,
            ) in executor.map(
                run_job,
                jobs,
            ):
                issues.extend(found)
                warnings.extend(found_warnings)
                section_runs.append(section_run)
                llm_calls.extend(section_calls)
        issues = _dedupe_issues(
            issues,
            segments=request.segments,
        )
        checked_section_count = len(
            {
                section.section_id
                for section in request.sections
                if not any(
                    warning.code == "section_failed"
                    and warning.section_id == section.section_id
                    for warning in warnings
                )
            }
        )
        status: Literal["completed", "partial", "failed"]
        if checked_section_count == 0:
            status = "failed"
        elif any(item.code == "section_failed" for item in warnings):
            status = "partial"
        else:
            status = "completed"
        calls = llm_calls
        failed_section_ids = {
            warning.section_id
            for warning in warnings
            if warning.code == "section_failed"
        }
        adjacent_boundary_count = max(0, len(request.segments) - 1)
        checked_adjacent_boundary_count = sum(
            max(
                0,
                min(section.end_ordinal, len(request.segments) - 1)
                - section.start_ordinal
                + 1,
            )
            for section in request.sections
            if section.section_id not in failed_section_ids
        )
        return AsrSectionReviewResult(
            input=request.model_copy(deep=True),
            status=status,
            profile_id=request.profile_id,
            model_id=calls[0].model_id if calls else None,
            section_runs=section_runs,
            issues=issues,
            warnings=warnings,
            llm_calls=calls,
            duration_ms=_elapsed_ms(started_at),
            quality_summary=AsrSectionReviewQualitySummary(
                status=(
                    "failed"
                    if status == "failed"
                    else "warning"
                    if status == "partial" or warnings
                    else "passed"
                ),
                section_count=len(request.sections),
                checked_section_count=checked_section_count,
                issue_count=len(issues),
                sections_cover_all_segments=_sections_cover_all_segments(
                    request.sections,
                    request.segments,
                ),
                source_text_unchanged=(
                    [item.model_dump(mode="json") for item in source_segments]
                    == [
                        item.model_dump(mode="json")
                        for item in request.segments
                    ]
                ),
                adjacent_boundary_count=adjacent_boundary_count,
                checked_adjacent_boundary_count=(
                    checked_adjacent_boundary_count
                ),
                cross_segment_issue_count=sum(
                    item.scope == "adjacent_segments"
                    for item in issues
                ),
                all_adjacent_boundaries_checked=(
                    checked_adjacent_boundary_count
                    == adjacent_boundary_count
                ),
                acoustic_candidate_count=len(
                    request.acoustic_candidates
                ),
            ),
        )

    @staticmethod
    def _jobs(
        request: AsrSectionReviewInput,
    ) -> list[
        tuple[
            AsrSectionReviewSection,
            list[VideoLocalizationTranscriptSegment],
            list[VideoLocalizationTranscriptSegment],
            list[VideoLocalizationTranscriptSegment],
        ]
    ]:
        jobs = []
        for section in request.sections:
            start = section.start_ordinal - 1
            end = section.end_ordinal
            jobs.append(
                (
                    section,
                    request.segments[
                        max(0, start - REVIEW_OVERLAP_SEGMENTS) : start
                    ],
                    request.segments[start:end],
                    request.segments[
                        end : min(
                            len(request.segments),
                            end + REVIEW_OVERLAP_SEGMENTS,
                        )
                    ],
                )
            )
        return jobs


def build_section_review_input(
    normalization: entity_normalization.AsrEntityNormalizationResult,
    understanding: AsrDocumentUnderstandingResult,
    *,
    normalization_operation_id: str,
    understanding_operation_id: str,
    profile_id: str | None = None,
    glossary: list[VideoLocalizationGlossaryEntry] | None = None,
) -> AsrSectionReviewInput:
    """Combine two immutable upstream artifacts into one atomic input."""

    if (
        normalization.input.source_track_id
        != understanding.input.source_track_id
        or normalization.input.source_audio_sha256
        != understanding.input.source_audio_sha256
        or normalization.input.language != understanding.input.language
    ):
        raise ValueError(
            "section review upstream artifacts do not share one source"
        )
    normalized_segments = normalization.updated_segments
    understood_segments = understanding.input.segments
    if len(normalized_segments) != len(understood_segments):
        raise ValueError(
            "section review upstream transcript lengths do not match"
        )
    for normalized, understood in zip(
        normalized_segments,
        understood_segments,
        strict=True,
    ):
        if (
            normalized.segment_id != understood.segment_id
            or normalized.start_ms != understood.start_ms
            or normalized.end_ms != understood.end_ms
        ):
            raise ValueError(
                "section review upstream transcript boundaries do not match"
            )
    selected_profile = (
        str(profile_id or "").strip()
        or str(normalization.profile_id or "").strip()
        or str(normalization.input.profile_id or "").strip()
        or str(understanding.profile_id or "").strip()
    )
    if not selected_profile:
        raise ValueError("section review requires an LLM profile")
    return AsrSectionReviewInput(
        upstream_contract_version=normalization.contract_version,
        upstream_operation_id=normalization_operation_id,
        understanding_operation_id=understanding_operation_id,
        source_track_id=normalization.input.source_track_id,
        source_audio_sha256=normalization.input.source_audio_sha256,
        language=normalization.input.language,
        profile_id=selected_profile,
        round_index=1,
        document_summary=understanding.brief.summary,
        content_logic=list(understanding.brief.content_logic),
        speaker_style=understanding.brief.speaker_style,
        sections=[
            AsrSectionReviewSection(
                section_id=item.section_id,
                start_ordinal=item.start_ordinal,
                end_ordinal=item.end_ordinal,
                start_segment_id=item.start_segment_id,
                end_segment_id=item.end_segment_id,
                role=item.role,
                focus=list(item.focus),
            )
            for item in understanding.brief.review_sections
        ],
        segments=[
            item.model_copy(deep=True) for item in normalized_segments
        ],
        evidence=[
            item.model_copy(deep=True)
            for item in normalization.input.evidence
        ],
        resolved_entities=[
            item.model_copy(deep=True) for item in normalization.resolutions
        ],
        glossary=list(glossary or normalization.input.glossary),
        locked_changes=[
            entity_normalization.AsrLockedTranscriptChange(
                segment_id=item.segment_id,
                before=item.before,
                after=item.after,
                reason=item.reason,
                confidence=item.confidence,
                evidence_source_ids=list(item.evidence_source_ids),
            )
            for item in normalization.changes
        ],
    )


def _complete_json(
    prompt: str,
    payload: dict,
    *,
    profile_id: str,
    trace_collector: AsrLlmTraceCollector,
    section: AsrSectionReviewSection,
    core_segment_ids: tuple[str, ...],
    round_index: int,
    completion_gateway: CompletionGateway | None = None,
) -> dict:
    def call(attempt: int, *, disable_reasoning: bool) -> dict:
        request = AsrSectionReviewCompletionRequest(
            contract_version="asr-section-review-call-input-v1",
            behavior_version=PROMPT_VERSION,
            call_id=(
                f"section-review-r{round_index}-"
                f"{section.section_id}"
            ),
            section_id=section.section_id,
            section_start_ordinal=section.start_ordinal,
            core_segment_ids=core_segment_ids,
            attempt=attempt,
            round_index=round_index,
            system_prompt=prompt,
            user_payload=payload,
            profile_id=profile_id,
            max_tokens=6_000 if attempt == 1 else 12_000,
            timeout=180,
            disable_reasoning=disable_reasoning,
            trace_sink=trace_collector.sink(
                call_id=(
                    f"section-review-r{round_index}-"
                    f"{section.section_id}-a{attempt:02d}"
                ),
                purpose="section_review",
                round_index=round_index,
                candidate_ids=[
                    section.start_segment_id,
                    section.end_segment_id,
                ],
            ),
        )
        if completion_gateway is not None:
            return completion_gateway(request)
        result = llm_runtime.complete_json(
            system_prompt=request.system_prompt,
            user_payload=request.user_payload,
            profile_id=request.profile_id,
            max_tokens=request.max_tokens,
            timeout=request.timeout,
            disable_reasoning=request.disable_reasoning,
            trace_sink=request.trace_sink,
        )
        if not isinstance(result, dict):
            raise llm_runtime.LlmRuntimeError(
                "语言模型没有返回对象",
                code="llm_json_not_object",
                status_code=502,
            )
        return result

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


def _segment_payload(
    segments: list[VideoLocalizationTranscriptSegment],
) -> list[dict]:
    return [
        {
            "segment_id": item.segment_id,
            "text": item.corrected_text or item.raw_text,
            "speaker_cluster_id": item.speaker_cluster_id,
        }
        for item in segments
    ]


def _sections_cover_all_segments(
    sections: list[AsrSectionReviewSection],
    segments: list[VideoLocalizationTranscriptSegment],
) -> bool:
    expected_start = 1
    for section in sections:
        if (
            section.start_ordinal != expected_start
            or section.end_ordinal > len(segments)
        ):
            return False
        expected_start = section.end_ordinal + 1
    return expected_start == len(segments) + 1


def _parse_issues(
    raw: dict,
    *,
    section: AsrSectionReviewSection,
    core: list[VideoLocalizationTranscriptSegment],
    adjacent_editable: list[VideoLocalizationTranscriptSegment],
    evidence_ids: set[str],
) -> tuple[list[AsrSectionReviewIssue], list[AsrSectionReviewWarning]]:
    core_by_id = {item.segment_id: item for item in core}
    editable_by_id = {
        item.segment_id: item for item in adjacent_editable
    }
    editable_order = [
        item.segment_id for item in adjacent_editable
    ]
    issues: list[AsrSectionReviewIssue] = []
    warnings: list[AsrSectionReviewWarning] = []
    raw_issues = raw.get("issues")
    if not isinstance(raw_issues, list):
        return issues, warnings
    for index, item in enumerate(raw_issues, start=1):
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("segment_id") or "").strip()
        excerpt = str(item.get("current_excerpt") or "").strip()
        reason = str(item.get("reason") or "").strip()
        raw_patches = item.get("patches")
        parsed_patches: list[AsrTranscriptEditPatch] = []
        if isinstance(raw_patches, list):
            for raw_patch in raw_patches:
                if not isinstance(raw_patch, dict):
                    continue
                patch_segment_id = str(
                    raw_patch.get("segment_id") or ""
                ).strip()
                patch_excerpt = str(
                    raw_patch.get("current_excerpt") or ""
                )
                patch_replacement = str(
                    raw_patch.get("proposed_replacement") or ""
                )
                patch_segment = editable_by_id.get(patch_segment_id)
                patch_text = (
                    patch_segment.corrected_text
                    or patch_segment.raw_text
                    if patch_segment is not None
                    else ""
                )
                if (
                    patch_segment is None
                    or not patch_excerpt
                    or patch_text.count(patch_excerpt) != 1
                ):
                    parsed_patches = []
                    break
                parsed_patches.append(
                    AsrTranscriptEditPatch(
                        segment_id=patch_segment_id,
                        current_excerpt=patch_excerpt,
                        proposed_replacement=patch_replacement,
                    )
                )
        if parsed_patches:
            patch_ids = [patch.segment_id for patch in parsed_patches]
            patch_ordinals = [
                editable_order.index(patch_id)
                for patch_id in patch_ids
            ]
            if (
                len(patch_ids) != 2
                or len(set(patch_ids)) != 2
                or patch_ids[0] not in core_by_id
                or patch_ordinals[1] != patch_ordinals[0] + 1
            ):
                parsed_patches = []
            else:
                segment_id = patch_ids[0]
                excerpt = (
                    excerpt
                    or " | ".join(
                        patch.current_excerpt
                        for patch in parsed_patches
                    )
                )
        segment = core_by_id.get(segment_id)
        source_text = (
            (segment.corrected_text or segment.raw_text)
            if segment is not None
            else ""
        )
        if (
            segment is None
            or not excerpt
            or not reason
            or (
                not parsed_patches
                and excerpt.casefold() not in source_text.casefold()
            )
        ):
            warnings.append(
                AsrSectionReviewWarning(
                    code="invalid_issue",
                    section_id=section.section_id,
                    message=(
                        f"{section.section_id} 返回了一条无法定位到原文的建议，"
                        "已忽略。"
                    ),
                )
            )
            continue
        cited = [
            str(value)
            for value in item.get("evidence_source_ids", [])
            if str(value) in evidence_ids
        ]
        issues.append(
            AsrSectionReviewIssue(
                issue_id=(
                    f"r{section.start_ordinal:04d}-"
                    f"{section.section_id}-{segment_id}-{index:02d}"
                ),
                section_id=section.section_id,
                segment_id=segment_id,
                current_excerpt=excerpt,
                proposed_replacement=str(
                    item.get("replacement") or ""
                ).strip()
                or (
                    " | ".join(
                        patch.proposed_replacement
                        for patch in parsed_patches
                    )
                    if parsed_patches
                    else ""
                ),
                reason=reason,
                confidence=_confidence(item.get("confidence")),
                needs_confirmation=bool(
                    item.get("needs_confirmation")
                ),
                evidence_source_ids=cited,
                scope=(
                    "adjacent_segments"
                    if parsed_patches
                    else "single_segment"
                ),
                target_segment_ids=(
                    [patch.segment_id for patch in parsed_patches]
                    if parsed_patches
                    else [segment_id]
                ),
                origin="llm_section_review",
                patches=parsed_patches,
            )
        )
    return issues, warnings


def _section_issue_from_boundary(
    issue: transcript_boundary_issues.AsrTranscriptBoundaryIssue,
    *,
    section: AsrSectionReviewSection,
) -> AsrSectionReviewIssue:
    return AsrSectionReviewIssue(
        issue_id=issue.issue_id,
        section_id=section.section_id,
        segment_id=issue.primary_segment_id,
        current_excerpt=issue.current_excerpt,
        proposed_replacement=issue.proposed_replacement,
        reason=issue.reason,
        confidence=issue.confidence,
        needs_confirmation=False,
        scope="adjacent_segments",
        target_segment_ids=list(issue.related_segment_ids),
        origin="deterministic_boundary_rule",
        patches=[item.model_copy(deep=True) for item in issue.patches],
    )


def _section_issue_from_text_candidate(
    candidate: transcript_text_rules.AsrTranscriptTextCandidate,
    *,
    section: AsrSectionReviewSection,
) -> AsrSectionReviewIssue:
    return AsrSectionReviewIssue(
        issue_id=candidate.candidate_id,
        section_id=section.section_id,
        segment_id=candidate.segment_id,
        current_excerpt=candidate.current_excerpt,
        proposed_replacement=candidate.proposed_replacement,
        reason=candidate.reason,
        confidence=candidate.confidence,
        needs_confirmation=False,
        scope="single_segment",
        target_segment_ids=[candidate.segment_id],
        origin="deterministic_text_rule",
    )


def _dedupe_issues(
    issues: list[AsrSectionReviewIssue],
    *,
    segments: list[VideoLocalizationTranscriptSegment],
) -> list[AsrSectionReviewIssue]:
    output: list[AsrSectionReviewIssue] = []
    seen: set[tuple[object, ...]] = set()
    segment_texts = {
        item.segment_id: item.corrected_text or item.raw_text
        for item in segments
    }
    for item in issues:
        patched_texts = []
        for patch in item.patches:
            source_text = segment_texts.get(patch.segment_id, "")
            if source_text.count(patch.current_excerpt) != 1:
                patched_texts = []
                break
            patched_texts.append(
                (
                    patch.segment_id,
                    source_text.replace(
                        patch.current_excerpt,
                        patch.proposed_replacement,
                        1,
                    ).strip().casefold(),
                )
            )
        key: tuple[object, ...] = (
            (
                "patches",
                tuple(patched_texts),
            )
            if patched_texts
            else (
                "single",
                item.segment_id,
                item.current_excerpt.casefold(),
                item.proposed_replacement.casefold(),
            )
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _elapsed_ms(started_at: float) -> int:
    return max(0, int(round((time.perf_counter() - started_at) * 1000)))


DEFAULT_SECTION_REVIEW_SERVICE = SectionReviewService()
