from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.video_localization import (
    asr_flow,
    asr_timing_contracts,
    asr_targeted_relisten,
    entity_normalization,
    llm_observability,
    research_evidence as research_evidence_domain,
    review_decisions as review_decisions_domain,
    section_review as section_review_domain,
    speaker_diarization,
    transcript_quality_gate as transcript_quality_gate_domain,
    transcription,
    visual_evidence as visual_evidence_domain,
    whole_recheck as whole_recheck_domain,
)
from app.domains.video_localization import (
    document_understanding_contracts,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationBoundaryReview,
    VideoLocalizationGlossaryEntry,
    VideoLocalizationResearchState,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)
from app.services import llm_runtime


CancelCallback = Callable[[], bool]
ProgressCallback = Callable[[float, str], None]
PreviewCallback = Callable[[str, list[dict]], None]
ReportCallback = Callable[[str, dict], None]
AtomicSnapshotCallback = Callable[[str, BaseModel], None]


def transcript_segments_to_preview_cues(
    segments: list[VideoLocalizationTranscriptSegment | research_evidence_domain.AsrResearchEvidenceSegment],
) -> list[dict]:
    """Project one typed transcript snapshot into the shared preview channel."""

    cues = []
    for segment in segments:
        text = (
            segment.corrected_text or segment.raw_text
            if isinstance(segment, VideoLocalizationTranscriptSegment)
            else segment.text
        )
        if not text.strip():
            continue
        cues.append(
            {
                "cue_id": segment.segment_id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": text,
            }
        )
    return cues


def _section_review_sections_from_flow(
    segments: list[VideoLocalizationTranscriptSegment],
    sections: list[dict],
    *,
    require_full_coverage: bool = True,
) -> list[section_review_domain.AsrSectionReviewSection]:
    """Validate current flow ranges before projecting them into typed input."""

    output: list[section_review_domain.AsrSectionReviewSection] = []
    next_available_start = 1
    segment_count = len(segments)
    for index, item in enumerate(sections, start=1):
        try:
            start_ordinal = int(item.get("start_segment") or 1)
            end_ordinal = int(item.get("end_segment") or segment_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("section review range must use integer ordinals") from exc
        if (
            (require_full_coverage and start_ordinal != next_available_start)
            or start_ordinal < next_available_start
            or start_ordinal < 1
            or end_ordinal < start_ordinal
            or end_ordinal > segment_count
        ):
            raise ValueError(
                "section review ranges must be ordered, non-overlapping, and within the transcript"
            )
        output.append(
            section_review_domain.AsrSectionReviewSection(
                section_id=str(item.get("id") or f"S{index}"),
                start_ordinal=start_ordinal,
                end_ordinal=end_ordinal,
                start_segment_id=segments[start_ordinal - 1].segment_id,
                end_segment_id=segments[end_ordinal - 1].segment_id,
                role=str(item.get("role") or "连续复查区块"),
                focus=[str(value) for value in item.get("focus", []) if str(value).strip()],
            )
        )
        next_available_start = end_ordinal + 1
    if not output or (
        require_full_coverage
        and next_available_start != segment_count + 1
    ):
        raise ValueError("section review ranges must continuously cover the transcript")
    return output


class AsrPipelineInput(BaseModel):
    """Resolved, serializable input for one complete ASR workflow run."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-pipeline-v2"] = "asr-pipeline-v2"
    operation_id: str = Field(default="formal-workflow", min_length=1)
    audio_path: str = Field(min_length=1)
    alignment_audio_path: str = Field(min_length=1)
    engine_id: str = Field(min_length=1)
    source_track_id: str = Field(min_length=1)
    alignment_source_track_id: str = Field(min_length=1)
    language: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, ge=0)
    source_filename: str | None = None
    source_video_path: str | None = None
    source_video_sha256: str | None = None
    source_video_duration_ms: int | None = Field(default=None, ge=1)
    source_video_frame_rate: float | None = Field(default=None, gt=0)
    vision_profile_id: str | None = None
    visual_evidence_dir: str | None = None
    llm_profile_id: str | None = None
    glossary: list[VideoLocalizationGlossaryEntry] = Field(default_factory=list)
    scene_context: str = ""
    research_cache_dir: str | None = None
    segmentation_profile_id: str = Field(default="generic_zh", min_length=1)
    existing_boundary_reviews: list[VideoLocalizationBoundaryReview] = Field(default_factory=list)
    source_audio_sha256: str = Field(min_length=1)
    alignment_audio_sha256: str = Field(min_length=1)
    diarization_engine_id: str | None = "auto"
    min_speakers: int | None = Field(
        default=None,
        ge=1,
        le=speaker_diarization.MAX_SPEAKER_COUNT_GUIDANCE,
    )
    max_speakers: int | None = Field(
        default=None,
        ge=1,
        le=speaker_diarization.MAX_SPEAKER_COUNT_GUIDANCE,
    )

    @model_validator(mode="after")
    def validate_speaker_range(self) -> AsrPipelineInput:
        if self.min_speakers is not None and self.max_speakers is not None and self.min_speakers > self.max_speakers:
            raise ValueError("min_speakers must not be greater than max_speakers")
        return self


class AsrInitialAnalysisResult(BaseModel):
    """Independent raw-ASR and diarization artifacts before their join."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-initial-analysis-v1"] = "asr-initial-analysis-v1"
    raw_asr: transcription.TranscribeRawOutput
    diarization: speaker_diarization.DiarizeSpeakersOutput | None = None
    diarization_error: str | None = None


class AsrJoinedTranscript(BaseModel):
    """Raw transcript after optional speaker labels pass the source identity gate."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-joined-transcript-v1"] = "asr-joined-transcript-v1"
    raw_asr: transcription.TranscribeRawOutput
    diarization: speaker_diarization.DiarizeSpeakersOutput | None = None
    segments: list[VideoLocalizationTranscriptSegment] = Field(default_factory=list)
    speaker_grouping_applied: bool = False
    warnings: list[str] = Field(default_factory=list)


class AsrInitialAnalysisSnapshot(BaseModel):
    """Fixed development artifact at the raw-ASR and diarization join boundary."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-initial-analysis-snapshot-v1"] = "asr-initial-analysis-snapshot-v1"
    analysis: AsrInitialAnalysisResult
    joined_transcript: AsrJoinedTranscript


class AsrTranscriptReviewInput(BaseModel):
    """Serializable input for whole-document transcript review."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-transcript-review-v2"] = "asr-transcript-review-v2"
    upstream_contract_version: Literal[
        "asr-raw-v2",
        "asr-joined-transcript-v1",
    ] = "asr-raw-v2"
    upstream_operation_id: str = Field(default="formal-workflow", min_length=1)
    source_track_id: str = Field(default="unknown", min_length=1)
    source_audio_sha256: str = Field(default="unknown", min_length=1)
    audio_path: str = Field(min_length=1)
    engine_id: str = Field(min_length=1)
    context_terms: list[str] = Field(default_factory=list, max_length=8)
    segments: list[VideoLocalizationTranscriptSegment] = Field(default_factory=list)
    language: str = Field(min_length=1)
    profile_id: str | None = None
    glossary: list[VideoLocalizationGlossaryEntry] = Field(default_factory=list)
    scene_context: str = ""
    research_cache_dir: str | None = None
    source_video_path: str | None = None
    source_video_sha256: str | None = None
    source_video_duration_ms: int | None = Field(default=None, ge=1)
    source_video_frame_rate: float | None = Field(default=None, gt=0)
    vision_profile_id: str | None = None
    visual_evidence_dir: str | None = None


class AsrTranscriptReviewResult(BaseModel):
    """Complete review artifact before timing alignment or subtitle segmentation."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["asr-transcript-review-v2"] = "asr-transcript-review-v2"
    input: AsrTranscriptReviewInput
    segments: list[VideoLocalizationTranscriptSegment] = Field(default_factory=list)
    research: VideoLocalizationResearchState
    document_understanding: document_understanding_contracts.AsrDocumentUnderstandingResult | None = None
    research_evidence: research_evidence_domain.AsrResearchEvidenceResult | None = None
    visual_evidence: visual_evidence_domain.AsrVisualEvidenceResult | None = None
    entity_normalizations: list[entity_normalization.AsrEntityNormalizationResult] = Field(default_factory=list)
    section_reviews: list[section_review_domain.AsrSectionReviewResult] = Field(default_factory=list)
    review_decisions: list[review_decisions_domain.AsrReviewDecisionsResult] = Field(default_factory=list)
    whole_rechecks: list[whole_recheck_domain.AsrWholeRecheckResult] = Field(default_factory=list)
    quality_gate: transcript_quality_gate_domain.AsrTranscriptQualityGateResult | None = None
    profile_id: str | None = None
    model_id: str | None = None
    report: dict[str, Any] = Field(default_factory=dict)
    stage_timings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    review_meta: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class AsrRunContext:
    """Non-serializable callbacks belonging to one operation execution."""

    is_cancelled: CancelCallback | None = None
    on_progress: ProgressCallback | None = None
    on_preview: PreviewCallback | None = None
    on_report: ReportCallback | None = None
    on_atomic_snapshot: AtomicSnapshotCallback | None = None


def _publish_atomic_snapshot(
    context: AsrRunContext,
    step_id: str,
    phase: str,
    payload: BaseModel,
) -> None:
    if context.on_atomic_snapshot is not None:
        context.on_atomic_snapshot(f"{step_id}_{phase}", payload)


class AsrPipeline:
    """The single domain entry point for ASR subtask execution.

    Project loading, source-track preparation, formal cue creation, and draft
    commit remain in the application adapter. This class consumes resolved
    audio contracts and never reads or writes project persistence.
    """

    def run_raw_asr(
        self,
        request: transcription.TranscribeRawInput,
        *,
        context: AsrRunContext | None = None,
    ) -> transcription.TranscribeRawOutput:
        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "asr",
            "input",
            request,
        )
        result = transcription.transcribe_raw(
            request,
            is_cancelled=run_context.is_cancelled,
        )
        _publish_atomic_snapshot(
            run_context,
            "asr",
            "result",
            result,
        )
        return result

    def run_strict_alignment(
        self,
        request: asr_timing_contracts.AsrAlignmentInput,
        *,
        context: AsrRunContext | None = None,
    ) -> asr_timing_contracts.AsrAlignmentResult:
        """Return only real forced-aligner word times for external evidence."""

        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "strict_alignment",
            "input",
            request,
        )
        result = transcription.run_strict_alignment_step(request)
        _publish_atomic_snapshot(
            run_context,
            "strict_alignment",
            "result",
            result,
        )
        return result

    def run_audio_boundaries(
        self,
        request: asr_timing_contracts.AsrAudioBoundariesInput,
        *,
        context: AsrRunContext | None = None,
    ) -> asr_timing_contracts.AsrAudioBoundariesResult:
        """Return pause and subtitle-entry evidence for aligned words."""

        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "audio_boundaries",
            "input",
            request,
        )
        result = transcription.run_audio_boundaries_step(request)
        _publish_atomic_snapshot(
            run_context,
            "audio_boundaries",
            "result",
            result,
        )
        return result

    def run_speaker_diarization(
        self,
        request: speaker_diarization.DiarizeSpeakersInput,
        *,
        context: AsrRunContext | None = None,
    ) -> speaker_diarization.DiarizeSpeakersOutput:
        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "diarization",
            "input",
            request,
        )
        result = speaker_diarization.diarize_speakers(
            request,
            is_cancelled=run_context.is_cancelled,
        )
        _publish_atomic_snapshot(
            run_context,
            "diarization",
            "result",
            result,
        )
        return result

    def run_initial_analysis(
        self,
        *,
        raw_asr: transcription.TranscribeRawInput,
        diarization: speaker_diarization.DiarizeSpeakersInput | None = None,
        context: AsrRunContext | None = None,
        on_raw_asr_complete: Callable[[transcription.TranscribeRawOutput], None] | None = None,
        raw_runner: (
            Callable[
                [transcription.TranscribeRawInput],
                transcription.TranscribeRawOutput,
            ]
            | None
        ) = None,
        diarization_runner: (
            Callable[
                [speaker_diarization.DiarizeSpeakersInput],
                speaker_diarization.DiarizeSpeakersOutput,
            ]
            | None
        ) = None,
        is_fatal_diarization_error: (Callable[[Exception], bool] | None) = None,
    ) -> AsrInitialAnalysisResult:
        run_context = context or AsrRunContext()
        result = transcription.run_initial_speech_analysis(
            asr_request=raw_asr,
            diarization_request=diarization,
            is_cancelled=run_context.is_cancelled,
            on_raw_asr_complete=on_raw_asr_complete,
            raw_runner=(
                raw_runner
                or (
                    lambda request: self.run_raw_asr(
                        request,
                        context=run_context,
                    )
                )
            ),
            diarization_runner=(
                diarization_runner
                or (
                    lambda request: self.run_speaker_diarization(
                        request,
                        context=run_context,
                    )
                )
            ),
            is_fatal_diarization_error=(is_fatal_diarization_error),
        )
        return AsrInitialAnalysisResult(
            raw_asr=result.raw_asr,
            diarization=result.diarization,
            diarization_error=result.diarization_error,
        )

    def join_initial_analysis(
        self,
        result: AsrInitialAnalysisResult,
        *,
        context: AsrRunContext | None = None,
    ) -> AsrJoinedTranscript:
        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "initial_analysis_join",
            "input",
            result,
        )
        segments = result.raw_asr.segments
        warnings: list[str] = []
        diarization_payload: dict = {
            "status": "not_run",
            "segments": [],
        }
        if result.diarization is not None:
            speaker_grouping_applied = (
                speaker_diarization.should_apply_speaker_grouping(
                    result.diarization
                )
            )
            if speaker_grouping_applied:
                segments = speaker_diarization.attach_to_transcript_segments(
                    segments,
                    result.diarization,
                    audio_sha256=result.raw_asr.input.audio_sha256,
                    source_track_id=result.raw_asr.input.source_track_id,
                )
            diarization_payload = {
                "status": result.diarization.status,
                "segments": [
                    {
                        "start_ms": segment.start_ms,
                        "end_ms": segment.end_ms,
                    }
                    for segment in result.diarization.segments
                ],
            }
        else:
            speaker_grouping_applied = False
        if result.diarization_error:
            warnings.append(result.diarization_error)
            diarization_payload["status"] = "failed"
        transcription.ensure_transcript_covers_diarized_speech(
            segments,
            diarization_payload,
            incomplete_chunk_ranges=result.raw_asr.incomplete_chunk_ranges,
        )
        joined = AsrJoinedTranscript(
            raw_asr=result.raw_asr,
            diarization=result.diarization,
            segments=segments,
            speaker_grouping_applied=speaker_grouping_applied,
            warnings=warnings,
        )
        _publish_atomic_snapshot(
            run_context,
            "initial_analysis_join",
            "result",
            joined,
        )
        return joined

    def snapshot_initial_analysis(
        self,
        result: AsrInitialAnalysisResult,
    ) -> AsrInitialAnalysisSnapshot:
        return AsrInitialAnalysisSnapshot(
            analysis=result,
            joined_transcript=self.join_initial_analysis(result),
        )

    def run_document_understanding(
        self,
        request: (document_understanding_contracts.AsrDocumentUnderstandingInput),
        *,
        context: AsrRunContext | None = None,
        completion_gateway: (asr_flow.AsrDocumentUnderstandingCompletionGateway | None) = None,
        resolved_profile: llm_runtime.ResolvedProfile | None = None,
    ) -> document_understanding_contracts.AsrDocumentUnderstandingResult:
        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "understand_document",
            "input",
            request,
        )
        request_snapshot = request.model_copy(deep=True)
        source_segments = [
            VideoLocalizationTranscriptSegment(
                segment_id=segment.segment_id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                raw_text=segment.text,
                speaker_cluster_id=segment.speaker_cluster_id,
                speaker_confidence=segment.speaker_confidence,
                has_speaker_overlap=segment.has_speaker_overlap,
            )
            for segment in request_snapshot.segments
        ]
        run = asr_flow.understand_document(
            source_segments,
            language=request.language,
            scene_context=request.scene_context,
            profile_id=request.profile_id,
            is_cancelled=run_context.is_cancelled,
            completion_gateway=completion_gateway,
            resolved_profile=resolved_profile,
        )
        raw_brief = run.brief
        sections = []
        for item in raw_brief["sections"]:
            start_ordinal = int(item["start_segment"])
            end_ordinal = int(item["end_segment"])
            sections.append(
                document_understanding_contracts.AsrDocumentReviewSection(
                    section_id=str(item["id"]),
                    start_ordinal=start_ordinal,
                    end_ordinal=end_ordinal,
                    start_segment_id=request_snapshot.segments[start_ordinal - 1].segment_id,
                    end_segment_id=request_snapshot.segments[end_ordinal - 1].segment_id,
                    role=str(item["role"]),
                    focus=[str(value) for value in item["focus"]],
                )
            )
        brief = document_understanding_contracts.AsrDocumentUnderstandingBrief(
            content_kind=str(raw_brief.get("content_kind") or "unknown"),
            summary=str(raw_brief["summary"]),
            content_logic=[str(value) for value in raw_brief["logic"]],
            speaker_style=str(raw_brief["speaker_style"]),
            language_notes=[
                str(value)
                for value in raw_brief.get("language_notes") or []
            ],
            entity_candidates=[
                document_understanding_contracts.AsrDocumentEntityCandidate(
                    name=str(item.get("name") or "").strip(),
                    role=str(item.get("role") or "").strip(),
                    needs_research=bool(item.get("needs_research")),
                )
                for item in raw_brief["entities"]
                if str(item.get("name") or "").strip()
            ],
            research_candidates=[
                document_understanding_contracts.AsrDocumentResearchCandidate.model_validate(item)
                for item in raw_brief["search_queries"]
            ],
            visual_questions=[
                document_understanding_contracts.AsrDocumentVisualQuestion(
                    question_id=f"visual_{index:02d}",
                    start_ordinal=int(item["start_segment"]),
                    end_ordinal=int(item["end_segment"]),
                    start_segment_id=request_snapshot.segments[int(item["start_segment"]) - 1].segment_id,
                    end_segment_id=request_snapshot.segments[int(item["end_segment"]) - 1].segment_id,
                    start_ms=request_snapshot.segments[int(item["start_segment"]) - 1].start_ms,
                    end_ms=request_snapshot.segments[int(item["end_segment"]) - 1].end_ms,
                    kind=str(item["kind"]),
                    reason=str(item["reason"]),
                    question=str(item["question"]),
                    frame_strategy=str(item["frame_strategy"]),
                )
                for index, item in enumerate(
                    raw_brief.get("visual_questions") or [],
                    start=1,
                )
            ],
            review_sections=sections,
        )
        output_text_unchanged = request.model_dump(mode="json") == request_snapshot.model_dump(mode="json") and [
            (segment.segment_id, segment.raw_text) for segment in source_segments
        ] == [(segment.segment_id, segment.text) for segment in request_snapshot.segments]
        responses = [
            document_understanding_contracts.AsrDocumentUnderstandingRawResponse(
                stage=str(item["stage"]),
                attempt=int(item["attempt"]),
                response_json=(
                    json.dumps(
                        item["response"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if item.get("response") is not None
                    else None
                ),
                error_code=(str(item["error_code"]) if item.get("error_code") is not None else None),
                error=(str(item["error"]) if item.get("error") is not None else None),
            )
            for item in run.raw_responses
        ]
        sections_cover_all_segments = (
            sections[0].start_ordinal == 1
            and sections[-1].end_ordinal == len(request_snapshot.segments)
            and all(left.end_ordinal + 1 == right.start_ordinal for left, right in zip(sections, sections[1:]))
        )
        quality_status: Literal["passed", "warning", "failed"] = (
            "passed" if sections_cover_all_segments and output_text_unchanged else "failed"
        )
        warnings = []
        if not sections_cover_all_segments:
            warnings.append("复查区块没有连续覆盖全部讲话片段。")
        if not output_text_unchanged:
            warnings.append("全文理解过程中检测到输入文字发生变化。")
        window_count = sum(1 for item in responses if item.stage.startswith("window_") and item.stage != "window_merge")
        retry_count = sum(max(0, item.attempt - 1) for item in responses)
        result = document_understanding_contracts.AsrDocumentUnderstandingResult(
            input=request_snapshot,
            brief=brief,
            profile_id=run.profile_id,
            model_id=run.model_id,
            prompt_version=asr_flow.PROMPT_VERSION,
            execution_strategy=run.execution_strategy,
            window_count=window_count,
            llm_call_count=run.llm_call_count,
            retry_count=max(retry_count, run.retry_count),
            stage_timing=(
                document_understanding_contracts.AsrDocumentUnderstandingStageTiming(duration_ms=run.duration_ms)
            ),
            quality_summary=(
                document_understanding_contracts.AsrDocumentUnderstandingQualitySummary(
                status=quality_status,
                segment_count=len(request_snapshot.segments),
                section_count=len(sections),
                sections_cover_all_segments=sections_cover_all_segments,
                source_text_unchanged=output_text_unchanged,
                )
            ),
            llm_calls=list(run.llm_calls),
            warnings=warnings,
            raw_responses=responses,
        )
        _publish_atomic_snapshot(
            run_context,
            "understand_document",
            "result",
            result,
        )
        return result

    def run_research_evidence(
        self,
        request: research_evidence_domain.ResearchEvidenceInput,
        *,
        context: AsrRunContext | None = None,
        cache_dir: str | None = None,
    ) -> research_evidence_domain.AsrResearchEvidenceResult:
        """Gather source evidence without resolving names or editing text."""

        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "research",
            "input",
            request,
        )
        result = research_evidence_domain.DEFAULT_RESEARCH_EVIDENCE_SERVICE.run(
            request,
            cache_dir=cache_dir,
            is_cancelled=run_context.is_cancelled,
        )
        _publish_atomic_snapshot(
            run_context,
            "research",
            "result",
            result,
        )
        return result

    def run_entity_normalization(
        self,
        request: entity_normalization.AsrEntityNormalizationInput,
        *,
        context: AsrRunContext | None = None,
    ) -> entity_normalization.AsrEntityNormalizationResult:
        """Normalize evidence-backed names and project terminology."""

        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "normalize_entities",
            "input",
            request,
        )
        result = entity_normalization.DEFAULT_ENTITY_NORMALIZATION_SERVICE.run(
                request,
                is_cancelled=run_context.is_cancelled,
            )
        if run_context.on_preview is not None and result.updated_segments:
            run_context.on_preview(
                "text_review",
                transcript_segments_to_preview_cues(result.updated_segments),
            )
        _publish_atomic_snapshot(
            run_context,
            "normalize_entities",
            "result",
            result,
        )
        return result

    def run_section_review(
        self,
        request: section_review_domain.AsrSectionReviewInput,
        *,
        context: AsrRunContext | None = None,
    ) -> section_review_domain.AsrSectionReviewResult:
        """Find section-level transcript issues without applying edits."""

        run_context = context or AsrRunContext()
        step_id = f"section_review_r{request.round_index}"
        _publish_atomic_snapshot(
            run_context,
            step_id,
            "input",
            request,
        )
        result = section_review_domain.DEFAULT_SECTION_REVIEW_SERVICE.run(
            request,
            is_cancelled=run_context.is_cancelled,
        )
        _publish_atomic_snapshot(
            run_context,
            step_id,
            "result",
            result,
        )
        return result

    def run_review_decisions(
        self,
        request: review_decisions_domain.AsrReviewDecisionsInput,
        *,
        context: AsrRunContext | None = None,
    ) -> review_decisions_domain.AsrReviewDecisionsResult:
        """Adjudicate supplied review issues and publish safe snapshots."""

        run_context = context or AsrRunContext()
        step_id = f"review_decisions_r{request.round_index}"
        _publish_atomic_snapshot(
            run_context,
            step_id,
            "input",
            request,
        )

        def publish(
            segments: list[VideoLocalizationTranscriptSegment],
        ) -> None:
            if run_context.on_preview is None:
                return
            run_context.on_preview(
                "text_review",
                transcript_segments_to_preview_cues(segments),
            )

        result = (
            review_decisions_domain.DEFAULT_REVIEW_DECISIONS_SERVICE.run(
                request,
                is_cancelled=run_context.is_cancelled,
                on_segments_changed=publish,
                on_prepared_decisions=(
                    (
                        lambda snapshot: run_context.on_atomic_snapshot(
                            f"{step_id}_prepared",
                            snapshot,
                        )
                    )
                    if run_context.on_atomic_snapshot is not None
                    else None
                ),
            )
        )
        _publish_atomic_snapshot(
            run_context,
            step_id,
            "result",
            result,
        )
        return result

    def run_whole_recheck(
        self,
        request: whole_recheck_domain.AsrWholeRecheckInput,
        *,
        context: AsrRunContext | None = None,
    ) -> whole_recheck_domain.AsrWholeRecheckResult:
        """Re-read one reviewed transcript without modifying its contents."""

        run_context = context or AsrRunContext()
        step_id = f"whole_recheck_r{request.round_index}"
        _publish_atomic_snapshot(
            run_context,
            step_id,
            "input",
            request,
        )
        result = whole_recheck_domain.DEFAULT_WHOLE_RECHECK_SERVICE.run(
            request,
            is_cancelled=run_context.is_cancelled,
        )
        _publish_atomic_snapshot(
            run_context,
            step_id,
            "result",
            result,
        )
        return result

    def run_transcript_quality_gate(
        self,
        request: transcript_quality_gate_domain.AsrTranscriptQualityGateInput,
        *,
        context: AsrRunContext | None = None,
    ) -> transcript_quality_gate_domain.AsrTranscriptQualityGateResult:
        """Decide locally whether reviewed text is ready for alignment."""

        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "transcript_quality_gate",
            "input",
            request,
        )
        result = (
            transcript_quality_gate_domain
            .DEFAULT_TRANSCRIPT_QUALITY_GATE_SERVICE
            .run(request)
        )
        _publish_atomic_snapshot(
            run_context,
            "transcript_quality_gate",
            "result",
            result,
        )
        return result

    def run_visual_evidence(
        self,
        request: visual_evidence_domain.AsrVisualEvidenceInput,
        *,
        source_video_path: str,
        frame_dir: str,
        context: AsrRunContext | None = None,
    ) -> visual_evidence_domain.AsrVisualEvidenceResult:
        """Read bounded frame evidence without resolving names or editing text."""

        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "visual_evidence",
            "input",
            request,
        )
        result = visual_evidence_domain.DEFAULT_VISUAL_EVIDENCE_SERVICE.run(
            request,
            source_video_path=source_video_path,
            frame_dir=frame_dir,
            is_cancelled=run_context.is_cancelled,
        )
        _publish_atomic_snapshot(
            run_context,
            "visual_evidence",
            "result",
            result,
        )
        return result

    def run_transcript_review(
        self,
        request: AsrTranscriptReviewInput,
        *,
        context: AsrRunContext | None = None,
    ) -> AsrTranscriptReviewResult:
        run_context = context or AsrRunContext()
        understanding_results: list[document_understanding_contracts.AsrDocumentUnderstandingResult] = []
        research_results: list[research_evidence_domain.AsrResearchEvidenceResult] = []
        visual_results: list[visual_evidence_domain.AsrVisualEvidenceResult] = []
        normalization_results: list[entity_normalization.AsrEntityNormalizationResult] = []
        section_review_results: list[section_review_domain.AsrSectionReviewResult] = []
        review_decision_results: list[review_decisions_domain.AsrReviewDecisionsResult] = []
        whole_recheck_results: list[whole_recheck_domain.AsrWholeRecheckResult] = []

        def atomic_result_for_step(step_id: str) -> object | None:
            if step_id == "understand_document":
                return understanding_results[-1] if understanding_results else None
            if step_id == "visual_evidence":
                return visual_results[-1] if visual_results else None
            if step_id == "research":
                return research_results[-1] if research_results else None
            if step_id == "normalize_entities":
                return normalization_results[-1] if normalization_results else None
            for prefix, values in (
                ("section_review_r", section_review_results),
                ("review_decisions_r", review_decision_results),
                ("whole_recheck_r", whole_recheck_results),
            ):
                if not step_id.startswith(prefix):
                    continue
                try:
                    round_index = int(step_id.removeprefix(prefix))
                except ValueError:
                    return None
                return values[round_index - 1] if 0 < round_index <= len(values) else None
            return None

        def enrich_step_result(step_id: str, step_result: dict) -> dict:
            atomic_result = atomic_result_for_step(step_id)
            if atomic_result is None:
                return dict(step_result)
            enriched = llm_observability.enrich_task_step_with_llm_calls(
                step_result,
                atomic_result,
            )
            if isinstance(
                atomic_result,
                whole_recheck_domain.AsrWholeRecheckResult,
            ):
                unresolved_items = whole_recheck_domain.reader_unresolved_items(
                        atomic_result,
                    frame_rate=(request.source_video_frame_rate or 30.0),
                )
                if unresolved_items:
                    enriched["sections"] = [
                        {
                            "title": "建议复听",
                            "items": unresolved_items,
                        }
                    ]
                    enriched["review_targets"] = [
                        {
                            "title": item["title"],
                            "location": item["meta"],
                            "detail": item["text"],
                        }
                        for item in unresolved_items
                    ]
            return enriched

        def publish_report(step_id: str, step_result: dict) -> None:
            if run_context.on_report is not None:
                run_context.on_report(
                    step_id,
                    enrich_step_result(step_id, step_result),
                )

        def run_understanding(
            segments: list[VideoLocalizationTranscriptSegment],
            **_kwargs,
        ) -> dict:
            return self._document_brief_for_review(
                request,
                segments=segments,
                context=run_context,
                capture=understanding_results,
            )

        def run_research(
            _segments: list[VideoLocalizationTranscriptSegment],
            **_kwargs,
        ) -> VideoLocalizationResearchState:
            if not understanding_results:
                raise ValueError("document understanding must complete before research")
            if visual_results:
                atomic_input = research_evidence_domain.AsrResearchEvidenceInputV2.from_document_and_visual_evidence(
                        understanding_results[-1],
                        visual_results[-1],
                    upstream_operation_id=(f"{request.upstream_operation_id}:understand_document"),
                    visual_evidence_operation_id=(f"{request.upstream_operation_id}:visual_evidence"),
                )
            else:
                atomic_input = research_evidence_domain.AsrResearchEvidenceInput.from_document_understanding(
                    understanding_results[-1],
                    upstream_operation_id=(f"{request.upstream_operation_id}:understand_document"),
                )
            atomic_result = self.run_research_evidence(
                atomic_input,
                context=run_context,
                cache_dir=request.research_cache_dir,
            )
            research_results.append(atomic_result)
            return research_evidence_domain.to_project_research_state(atomic_result)

        def run_visual_evidence(
            _segments: list[VideoLocalizationTranscriptSegment],
            **_kwargs,
        ) -> visual_evidence_domain.AsrVisualEvidenceResult | None:
            if not understanding_results:
                raise ValueError("document understanding must complete before visual evidence")
            if not (
                request.source_video_path
                and request.source_video_sha256
                and request.source_video_duration_ms
                and request.visual_evidence_dir
            ):
                return None
            visual_input = visual_evidence_domain.AsrVisualEvidenceInput.from_document_understanding(
                    understanding_results[-1],
                upstream_operation_id=(f"{request.upstream_operation_id}:understand_document"),
                    video_sha256=request.source_video_sha256,
                    video_duration_ms=request.source_video_duration_ms,
                video_frame_rate=(request.source_video_frame_rate or 30.0),
                    profile_id=request.vision_profile_id,
                )
            atomic_result = self.run_visual_evidence(
                visual_input,
                source_video_path=request.source_video_path,
                frame_dir=request.visual_evidence_dir,
                context=run_context,
            )
            visual_results.append(atomic_result)
            return atomic_result

        def run_entity_normalization(
            segments: list[VideoLocalizationTranscriptSegment],
            *,
            locked_changes: list[dict],
            **_kwargs,
        ) -> entity_normalization.AsrEntityNormalizationResult:
            if not research_results:
                raise ValueError("research evidence must complete before entity normalization")
            atomic_input = entity_normalization.AsrEntityNormalizationInput.from_research_evidence(
                    research_results[-1],
                upstream_operation_id=(f"{request.upstream_operation_id}:research"),
                    locked_changes=[
                        entity_normalization.AsrLockedTranscriptChange(
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
                        issue_id=(str(item.get("issue_id") or "") or None),
                        source_task_id=(str(item.get("source_task_id") or "") or None),
                            round_index=(
                            int(item.get("round_index") or item.get("round"))
                            if (item.get("round_index") or item.get("round"))
                                else None
                            ),
                        )
                        for item in locked_changes
                    if str(item.get("segment_id") or "") and str(item.get("after") or "")
                    ],
                )
            atomic_input = atomic_input.model_copy(
                update={
                    "segments": [
                        research_evidence_domain.AsrResearchEvidenceSegment(
                            ordinal=index,
                            segment_id=segment.segment_id,
                            start_ms=segment.start_ms,
                            end_ms=segment.end_ms,
                            text=(segment.corrected_text or segment.raw_text),
                            speaker_cluster_id=(segment.speaker_cluster_id),
                        )
                        for index, segment in enumerate(
                            segments,
                            start=1,
                        )
                    ]
                }
            )
            result = self.run_entity_normalization(
                atomic_input,
                context=AsrRunContext(
                    is_cancelled=run_context.is_cancelled,
                    on_atomic_snapshot=run_context.on_atomic_snapshot,
                ),
            )
            normalization_results.append(result)
            return result

        def run_section_review(
            segments: list[VideoLocalizationTranscriptSegment],
            *,
            round_index: int,
            sections: list[dict],
            locked_changes: list[dict],
            profile_id: str,
            **_kwargs,
        ) -> section_review_domain.AsrSectionReviewResult:
            if not understanding_results or not normalization_results:
                raise ValueError("understanding and entity normalization must complete before section review")
            if round_index != 1:
                raise ValueError("only one ASR review round is supported")
            typed_sections = _section_review_sections_from_flow(
                segments,
                sections,
                require_full_coverage=True,
            )
            atomic_input = section_review_domain.build_section_review_input(
                    normalization_results[-1],
                    understanding_results[-1],
                normalization_operation_id=(f"{request.upstream_operation_id}:normalize_entities"),
                understanding_operation_id=(f"{request.upstream_operation_id}:understand_document"),
                    profile_id=profile_id,
                    glossary=request.glossary,
                )
            atomic_input = atomic_input.model_copy(
                update={
                    "upstream_contract_version": normalization_results[-1].contract_version,
                    "upstream_operation_id": f"{request.upstream_operation_id}:normalize_entities",
                    "round_index": round_index,
                    "segments": [item.model_copy(deep=True) for item in segments],
                    "sections": typed_sections,
                    "acoustic_candidates": [],
                    "locked_changes": [
                        entity_normalization.AsrLockedTranscriptChange(
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
                            issue_id=(str(item.get("issue_id") or "") or None),
                            source_task_id=(str(item.get("source_task_id") or "") or None),
                            round_index=(
                                int(item.get("round_index") or item.get("round"))
                                if (item.get("round_index") or item.get("round"))
                                else None
                            ),
                        )
                        for item in locked_changes
                        if str(item.get("segment_id") or "") and str(item.get("after") or "")
                    ],
                }
            )
            result = self.run_section_review(
                atomic_input,
                context=AsrRunContext(
                    is_cancelled=run_context.is_cancelled,
                    on_atomic_snapshot=run_context.on_atomic_snapshot,
                ),
            )
            section_review_results.append(result)
            return result

        def run_review_decisions(
            _segments: list[VideoLocalizationTranscriptSegment],
            *,
            round_index: int,
            profile_id: str,
            **_kwargs,
        ) -> review_decisions_domain.AsrReviewDecisionsResult:
            if not section_review_results:
                raise ValueError("section review must complete before review decisions")
            if round_index != 1:
                raise ValueError("only one ASR review round is supported")
            atomic_input = review_decisions_domain.build_review_decisions_input(
                    section_review_results[-1],
                upstream_operation_id=(f"{request.upstream_operation_id}:section_review_r{round_index}"),
                    profile_id=profile_id,
                )
            if atomic_input.issues:
                atomic_input = atomic_input.model_copy(
                    update={
                        "acoustic_candidates": (
                            asr_targeted_relisten
                            .relisten_review_issues(
                                audio_path=request.audio_path,
                                engine_id=request.engine_id,
                                language=request.language,
                                issues=atomic_input.issues,
                                segments=atomic_input.segments,
                                context_terms=request.context_terms,
                                is_cancelled=(
                                    run_context.is_cancelled
                                ),
                            )
                        )
                    }
                )
            result = self.run_review_decisions(
                atomic_input,
                context=AsrRunContext(
                    is_cancelled=run_context.is_cancelled,
                    on_preview=run_context.on_preview,
                    on_atomic_snapshot=(
                        run_context.on_atomic_snapshot
                    ),
                ),
            )
            review_decision_results.append(result)
            return result

        def run_whole_recheck(
            _segments: list[VideoLocalizationTranscriptSegment],
            *,
            round_index: int,
            profile_id: str,
            **_kwargs,
        ) -> whole_recheck_domain.AsrWholeRecheckResult:
            if not review_decision_results or not understanding_results:
                raise ValueError("review decisions and document understanding must complete before whole recheck")
            if round_index != 1:
                raise ValueError("only one ASR review round is supported")
            atomic_input = whole_recheck_domain.build_whole_recheck_input(
                    review_decision_results[-1],
                    understanding_results[-1],
                upstream_operation_id=(f"{request.upstream_operation_id}:review_decisions_r{round_index}"),
                understanding_operation_id=(f"{request.upstream_operation_id}:understand_document"),
                    profile_id=profile_id,
            )
            result = self.run_whole_recheck(
                atomic_input,
                context=AsrRunContext(
                    is_cancelled=run_context.is_cancelled,
                    on_atomic_snapshot=(
                        run_context.on_atomic_snapshot
                    ),
                ),
            )
            whole_recheck_results.append(result)
            return result

        def publish_segments_changed(
            _step_id: str,
            segments: list[VideoLocalizationTranscriptSegment],
        ) -> None:
            if run_context.on_preview is None:
                return
            run_context.on_preview(
                "text_review",
                transcript_segments_to_preview_cues(segments),
            )

        result = asr_flow.review_transcript(
            request.segments,
            language=request.language,
            profile_id=request.profile_id,
            glossary=request.glossary,
            scene_context=request.scene_context,
            research_cache_dir=request.research_cache_dir,
            is_cancelled=run_context.is_cancelled,
            on_progress=run_context.on_progress,
            on_report=publish_report,
            document_understanding_runner=run_understanding,
            visual_evidence_runner=run_visual_evidence,
            research_runner=run_research,
            entity_normalization_runner=run_entity_normalization,
            section_review_runner=run_section_review,
            review_decisions_runner=run_review_decisions,
            whole_recheck_runner=run_whole_recheck,
            on_segments_changed=publish_segments_changed,
        )
        persisted_steps = result.report.get("task_step_results")
        if isinstance(persisted_steps, dict):
            enriched_steps = {
                str(step_id): enrich_step_result(
                    str(step_id),
                    step_result,
                )
                for step_id, step_result in persisted_steps.items()
                if isinstance(step_result, dict)
            }
            result.report["task_step_results"] = enriched_steps
            result.review_meta["task_step_results"] = enriched_steps
        quality_gate_result = None
        if whole_recheck_results:
            latest_recheck = whole_recheck_results[-1]
            quality_gate_result = self.run_transcript_quality_gate(
                transcript_quality_gate_domain.build_input(
                    latest_recheck,
                    upstream_operation_id=(
                        f"{request.upstream_operation_id}:whole_recheck_r{latest_recheck.input.round_index}"
                    ),
                    source_track_id=request.source_track_id,
                    source_audio_sha256=request.source_audio_sha256,
                    segments=result.segments,
                ),
                context=run_context,
            )
        else:
            error_detail = result.report.get("error_detail")
            if (
                result.report.get("status") == "degraded"
                and isinstance(error_detail, dict)
                and error_detail.get("action")
                == "continue_with_original_asr"
            ):
                quality_gate_result = self.run_transcript_quality_gate(
                    transcript_quality_gate_domain
                    .build_degraded_review_input(
                        upstream_operation_id=(
                            f"{request.upstream_operation_id}:"
                            "asr_review_deferred"
                        ),
                        source_track_id=request.source_track_id,
                        source_audio_sha256=(
                            request.source_audio_sha256
                        ),
                        language=request.language,
                        segments=result.segments,
                        warning=str(
                            error_detail.get("message")
                            or "语言复核没有完成，已保留原始 ASR。"
                        ),
                    ),
                    context=run_context,
                )
        return AsrTranscriptReviewResult(
            input=request,
            segments=result.segments,
            research=result.research,
            document_understanding=(understanding_results[-1] if understanding_results else None),
            research_evidence=(research_results[-1] if research_results else None),
            visual_evidence=(visual_results[-1] if visual_results else None),
            entity_normalizations=normalization_results,
            section_reviews=section_review_results,
            review_decisions=review_decision_results,
            whole_rechecks=whole_recheck_results,
            quality_gate=quality_gate_result,
            profile_id=result.profile_id,
            model_id=result.model_id,
            report=result.report,
            stage_timings=result.stage_timings,
            review_meta=result.review_meta,
        )

    def run_full(
        self,
        request: AsrPipelineInput,
        *,
        context: AsrRunContext | None = None,
    ) -> VideoLocalizationTranscriptionState:
        run_context = context or AsrRunContext()
        _publish_atomic_snapshot(
            run_context,
            "workflow",
            "input",
            request,
        )
        return transcription.transcribe_and_process(
            audio_path=request.audio_path,
            alignment_audio_path=request.alignment_audio_path,
            engine_id=request.engine_id,
            source_track_id=request.source_track_id,
            alignment_source_track_id=request.alignment_source_track_id,
            language=request.language,
            duration_ms=request.duration_ms,
            llm_profile_id=request.llm_profile_id,
            glossary=request.glossary,
            source_filename=request.source_filename,
            source_video_path=request.source_video_path,
            source_video_frame_rate=request.source_video_frame_rate,
            scene_context=request.scene_context,
            research_cache_dir=request.research_cache_dir,
            segmentation_profile_id=request.segmentation_profile_id,
            existing_boundary_reviews=request.existing_boundary_reviews,
            source_audio_sha256=request.source_audio_sha256,
            alignment_audio_sha256=request.alignment_audio_sha256,
            progress_callback=run_context.on_progress,
            is_cancelled=run_context.is_cancelled,
            preview_callback=run_context.on_preview,
            report_callback=run_context.on_report,
            atomic_snapshot_callback=run_context.on_atomic_snapshot,
            diarization_engine_id=request.diarization_engine_id,
            min_speakers=request.min_speakers,
            max_speakers=request.max_speakers,
            initial_analysis_runner=lambda **kwargs: self.run_initial_analysis(
                raw_asr=kwargs["asr_request"],
                diarization=kwargs.get("diarization_request"),
                context=run_context,
                on_raw_asr_complete=kwargs.get("on_raw_asr_complete"),
            ),
            initial_analysis_joiner=(
                lambda result: self.join_initial_analysis(
                    result,
                    context=run_context,
                )
            )
            if request.diarization_engine_id
            else None,
            transcript_review_runner=lambda segments, **kwargs: self.run_transcript_review(
                AsrTranscriptReviewInput(
                    upstream_contract_version=(
                        "asr-joined-transcript-v1"
                        if request.diarization_engine_id
                        else "asr-raw-v2"
                    ),
                    upstream_operation_id=request.operation_id,
                    source_track_id=request.source_track_id,
                    source_audio_sha256=request.source_audio_sha256,
                    audio_path=request.audio_path,
                    engine_id=request.engine_id,
                    context_terms=kwargs.get("context_terms") or [],
                    segments=segments,
                    language=kwargs["language"],
                    profile_id=kwargs.get("profile_id"),
                    glossary=kwargs.get("glossary") or [],
                    scene_context=kwargs.get("scene_context") or "",
                    research_cache_dir=kwargs.get("research_cache_dir"),
                    source_video_path=request.source_video_path,
                    source_video_sha256=request.source_video_sha256,
                    source_video_duration_ms=(request.source_video_duration_ms),
                    source_video_frame_rate=(request.source_video_frame_rate),
                    vision_profile_id=request.vision_profile_id,
                    visual_evidence_dir=request.visual_evidence_dir,
                ),
                context=run_context,
            ),
        )

    def _document_brief_for_review(
        self,
        request: AsrTranscriptReviewInput,
        *,
        segments: list[VideoLocalizationTranscriptSegment],
        context: AsrRunContext,
        capture: (list[document_understanding_contracts.AsrDocumentUnderstandingResult] | None) = None,
    ) -> dict:
        atomic_input = document_understanding_contracts.AsrDocumentUnderstandingInput(
            upstream_contract_version=request.upstream_contract_version,
            upstream_operation_id=request.upstream_operation_id,
            source_track_id=request.source_track_id,
            source_audio_sha256=request.source_audio_sha256,
            language=request.language,
            scene_context=request.scene_context,
            profile_id=request.profile_id,
            segments=[
                document_understanding_contracts.AsrDocumentUnderstandingSegment(
                    ordinal=index,
                    segment_id=segment.segment_id,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=(segment.corrected_text or segment.raw_text).strip(),
                    speaker_cluster_id=segment.speaker_cluster_id,
                    speaker_confidence=segment.speaker_confidence,
                    has_speaker_overlap=segment.has_speaker_overlap,
                )
                for index, segment in enumerate(segments, start=1)
                if (segment.corrected_text or segment.raw_text).strip()
            ],
        )
        result = self.run_document_understanding(atomic_input, context=context)
        if capture is not None:
            capture.append(result)
        return {
            "summary": result.brief.summary,
            "logic": result.brief.content_logic,
            "speaker_style": result.brief.speaker_style,
            "entities": [item.model_dump(mode="json") for item in result.brief.entity_candidates],
            "search_queries": [item.model_dump(mode="json") for item in result.brief.research_candidates],
            "sections": [
                {
                    "id": item.section_id,
                    "start_segment": item.start_ordinal,
                    "end_segment": item.end_ordinal,
                    "role": item.role,
                    "focus": item.focus,
                }
                for item in result.brief.review_sections
            ],
        }


DEFAULT_ASR_PIPELINE = AsrPipeline()


__all__ = [
    "AsrInitialAnalysisResult",
    "AsrInitialAnalysisSnapshot",
    "AsrJoinedTranscript",
    "AsrPipeline",
    "AsrPipelineInput",
    "AsrRunContext",
    "AsrTranscriptReviewInput",
    "AsrTranscriptReviewResult",
    "DEFAULT_ASR_PIPELINE",
]
