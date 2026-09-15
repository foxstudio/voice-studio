"""Typed identities and codecs for managed deterministic local workflows."""

from __future__ import annotations

from app.domains.video_localization.managed_local_step import (
    ManagedLocalStepSpec,
)
from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_STEP_ID,
    SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION,
    SOURCE_AUDIO_WORKFLOW_VERSION,
    SourceAudioStepOutputV1,
    parse_source_audio_step_output,
    source_audio_step_output_bytes,
)
from app.schemas.video_localization_stem_separation_step import (
    STEM_SEPARATION_STEP_ID,
    STEM_SEPARATION_STEP_OUTPUT_SCHEMA_VERSION,
    STEM_SEPARATION_WORKFLOW_VERSION,
    StemSeparationStepOutputV1,
    parse_stem_separation_step_output,
    stem_separation_step_output_bytes,
)
from app.schemas.video_localization_reference_candidates_step import (
    REFERENCE_CANDIDATES_STEP_ID,
    REFERENCE_CANDIDATES_STEP_OUTPUT_SCHEMA_VERSION,
    REFERENCE_CANDIDATES_WORKFLOW_VERSION,
    ReferenceCandidatesStepOutputV1,
    parse_reference_candidates_step_output,
    reference_candidates_step_output_bytes,
)
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_STEP_ID,
    SPEAKER_DIARIZATION_STEP_OUTPUT_SCHEMA_VERSION,
    SPEAKER_DIARIZATION_WORKFLOW_VERSION,
    SpeakerDiarizationStepOutputV1,
    parse_speaker_diarization_step_output,
    speaker_diarization_step_output_bytes,
)
from app.schemas.video_localization_asr_raw_step import (
    ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RAW_STEP_ID,
    ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION,
    AsrRawStepOutputV1,
    asr_raw_step_output_bytes,
    parse_asr_raw_step_output,
)
from app.schemas.video_localization_asr_initial_analysis_step import (
    ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION,
    ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_ID,
    ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION,
    ASR_INITIAL_ANALYSIS_JOIN_STEP_ID,
    AsrInitialAnalysisDiarizationOutcomeV1,
    AsrInitialAnalysisJoinOutputV1,
    asr_initial_analysis_diarization_output_bytes,
    asr_initial_analysis_join_output_bytes,
    parse_asr_initial_analysis_diarization_output,
    parse_asr_initial_analysis_join_output,
)
from app.schemas.video_localization_asr_document_understanding_step import (
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_document_understanding_managed_contracts import (
    ASR_DOCUMENT_UNDERSTANDING_PREPARED_INPUT_SCHEMA_VERSION,
    ASR_DOCUMENT_UNDERSTANDING_STEP_OUTPUT_SCHEMA_VERSION,
    AsrDocumentUnderstandingPreparedInputV2,
    AsrDocumentUnderstandingStepOutputV2,
    parse_prepared_input,
    parse_step_output,
    prepared_input_bytes,
    step_output_bytes,
)
from app.schemas.video_localization_asr_visual_evidence_step import (
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_visual_evidence_managed_contracts import (
    ASR_VISUAL_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION,
    ASR_VISUAL_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION,
    AsrVisualEvidencePreparedInputV2,
    AsrVisualEvidenceStepOutputV2,
    parse_prepared_input as parse_visual_prepared_input,
    parse_step_output as parse_visual_step_output,
    prepared_input_bytes as visual_prepared_input_bytes,
    step_output_bytes as visual_step_output_bytes,
)
from app.schemas.video_localization_asr_research_evidence_step import (
    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_research_evidence_managed_contracts import (
    ASR_RESEARCH_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION,
    ASR_RESEARCH_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION,
    AsrResearchEvidencePreparedInputV1,
    AsrResearchEvidenceStepOutputV1,
    parse_prepared_input as parse_research_prepared_input,
    parse_step_output as parse_research_step_output,
    prepared_input_bytes as research_prepared_input_bytes,
    step_output_bytes as research_step_output_bytes,
)
from app.schemas.video_localization_asr_entity_normalization_step import (
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_entity_normalization_managed_contracts import (
    ASR_ENTITY_NORMALIZATION_PREPARED_INPUT_SCHEMA_VERSION,
    ASR_ENTITY_NORMALIZATION_STEP_OUTPUT_SCHEMA_VERSION,
    AsrEntityNormalizationPreparedInputV1,
    AsrEntityNormalizationStepOutputV1,
    parse_prepared_input as parse_entity_prepared_input,
    parse_step_output as parse_entity_step_output,
    prepared_input_bytes as entity_prepared_input_bytes,
    step_output_bytes as entity_step_output_bytes,
)
from app.schemas.video_localization_asr_section_review_step import (
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_section_review_managed_contracts import (
    ASR_SECTION_REVIEW_PREPARED_INPUT_SCHEMA_VERSION,
    ASR_SECTION_REVIEW_STEP_OUTPUT_SCHEMA_VERSION,
    AsrSectionReviewPreparedInputV1,
    AsrSectionReviewStepOutputV1,
    parse_prepared_input as parse_section_review_prepared_input,
    parse_step_output as parse_section_review_step_output,
    prepared_input_bytes as section_review_prepared_input_bytes,
    step_output_bytes as section_review_step_output_bytes,
)
from app.schemas.video_localization_asr_review_decisions_step import (
    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_review_decisions_managed_contracts import (
    ASR_REVIEW_DECISIONS_PREPARED_INPUT_SCHEMA_VERSION,
    ASR_REVIEW_DECISIONS_STEP_OUTPUT_SCHEMA_VERSION,
    AsrReviewDecisionsPreparedInputV1,
    AsrReviewDecisionsStepOutputV1,
    parse_prepared_input as parse_review_decisions_prepared_input,
    parse_step_output as parse_review_decisions_step_output,
    prepared_input_bytes as review_decisions_prepared_input_bytes,
    step_output_bytes as review_decisions_step_output_bytes,
)
from app.schemas.video_localization_asr_whole_recheck_step import (
    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_whole_recheck_managed_contracts import (
    ASR_WHOLE_RECHECK_PREPARED_INPUT_SCHEMA_VERSION,
    ASR_WHOLE_RECHECK_STEP_OUTPUT_SCHEMA_VERSION,
    AsrWholeRecheckPreparedInputV1,
    AsrWholeRecheckStepOutputV1,
    parse_prepared_input as parse_whole_recheck_prepared_input,
    parse_step_output as parse_whole_recheck_step_output,
    prepared_input_bytes as whole_recheck_prepared_input_bytes,
    step_output_bytes as whole_recheck_step_output_bytes,
)
from app.schemas.video_localization_asr_transcript_quality_gate_step import (
    ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization.asr_transcript_quality_gate_managed_contracts import (
    ASR_TRANSCRIPT_QUALITY_GATE_PREPARED_INPUT_SCHEMA_VERSION,
    ASR_TRANSCRIPT_QUALITY_GATE_STEP_OUTPUT_SCHEMA_VERSION,
    AsrTranscriptQualityGatePreparedInputV1,
    AsrTranscriptQualityGateStepOutputV1,
    parse_prepared_input as parse_transcript_quality_gate_prepared_input,
    parse_step_output as parse_transcript_quality_gate_step_output,
    prepared_input_bytes as transcript_quality_gate_prepared_input_bytes,
    step_output_bytes as transcript_quality_gate_step_output_bytes,
)


SOURCE_AUDIO_STEP_SPEC = ManagedLocalStepSpec[
    SourceAudioStepOutputV1
](
    kind="source_audio",
    workflow_version=SOURCE_AUDIO_WORKFLOW_VERSION,
    step_id=SOURCE_AUDIO_STEP_ID,
    output_schema_version=(
        SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="SOURCE_AUDIO",
    label="原音轨",
    serialize_output=source_audio_step_output_bytes,
    parse_output=parse_source_audio_step_output,
)

STEM_SEPARATION_STEP_SPEC = ManagedLocalStepSpec[
    StemSeparationStepOutputV1
](
    kind="stems",
    workflow_version=STEM_SEPARATION_WORKFLOW_VERSION,
    step_id=STEM_SEPARATION_STEP_ID,
    output_schema_version=(
        STEM_SEPARATION_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="STEM_SEPARATION",
    label="人声与背景声分离",
    serialize_output=stem_separation_step_output_bytes,
    parse_output=parse_stem_separation_step_output,
)

REFERENCE_CANDIDATES_STEP_SPEC = ManagedLocalStepSpec[
    ReferenceCandidatesStepOutputV1
](
    kind="reference_clips",
    workflow_version=REFERENCE_CANDIDATES_WORKFLOW_VERSION,
    step_id=REFERENCE_CANDIDATES_STEP_ID,
    output_schema_version=(
        REFERENCE_CANDIDATES_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="REFERENCE_CANDIDATES",
    label="参考音候选",
    serialize_output=reference_candidates_step_output_bytes,
    parse_output=parse_reference_candidates_step_output,
)

SPEAKER_DIARIZATION_STEP_SPEC = ManagedLocalStepSpec[
    SpeakerDiarizationStepOutputV1
](
    kind="speaker_diarization",
    workflow_version=SPEAKER_DIARIZATION_WORKFLOW_VERSION,
    step_id=SPEAKER_DIARIZATION_STEP_ID,
    output_schema_version=(
        SPEAKER_DIARIZATION_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="SPEAKER_DIARIZATION",
    label="说话人区分",
    serialize_output=speaker_diarization_step_output_bytes,
    parse_output=parse_speaker_diarization_step_output,
)

ASR_RAW_STEP_SPEC = ManagedLocalStepSpec[
    AsrRawStepOutputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id=ASR_RAW_STEP_ID,
    output_schema_version=(
        ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_RAW",
    label="原始听写",
    serialize_output=asr_raw_step_output_bytes,
    parse_output=parse_asr_raw_step_output,
)

ASR_INITIAL_ANALYSIS_RAW_STEP_SPEC = ManagedLocalStepSpec[
    AsrRawStepOutputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id=ASR_RAW_STEP_ID,
    output_schema_version=(
        ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_INITIAL_ANALYSIS_RAW",
    label="初始分析原始听写",
    serialize_output=asr_raw_step_output_bytes,
    parse_output=parse_asr_raw_step_output,
)

ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_SPEC = (
    ManagedLocalStepSpec[
        AsrInitialAnalysisDiarizationOutcomeV1
    ](
        kind="english_asr",
        workflow_version=(
            ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
        ),
        step_id=ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_ID,
        output_schema_version=(
            ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION
        ),
        error_namespace=(
            "ASR_INITIAL_ANALYSIS_DIARIZATION"
        ),
        label="初始分析说话人区分",
        serialize_output=(
            asr_initial_analysis_diarization_output_bytes
        ),
        parse_output=(
            parse_asr_initial_analysis_diarization_output
        ),
    )
)

ASR_INITIAL_ANALYSIS_JOIN_STEP_SPEC = ManagedLocalStepSpec[
    AsrInitialAnalysisJoinOutputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id=ASR_INITIAL_ANALYSIS_JOIN_STEP_ID,
    output_schema_version=(
        ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_INITIAL_ANALYSIS_JOIN",
    label="初始分析汇合",
    serialize_output=asr_initial_analysis_join_output_bytes,
    parse_output=parse_asr_initial_analysis_join_output,
)

ASR_DOCUMENT_UNDERSTANDING_PREPARE_STEP_SPEC = (
    ManagedLocalStepSpec[
        AsrDocumentUnderstandingPreparedInputV2
    ](
        kind="english_asr",
        workflow_version=(
            ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
        ),
        step_id="prepare_document_input",
        output_schema_version=(
            ASR_DOCUMENT_UNDERSTANDING_PREPARED_INPUT_SCHEMA_VERSION
        ),
        error_namespace=(
            "ASR_DOCUMENT_UNDERSTANDING_PREPARE"
        ),
        label="全文理解输入锁定",
        serialize_output=prepared_input_bytes,
        parse_output=parse_prepared_input,
    )
)

ASR_DOCUMENT_UNDERSTANDING_FINALIZE_STEP_SPEC = (
    ManagedLocalStepSpec[
        AsrDocumentUnderstandingStepOutputV2
    ](
        kind="english_asr",
        workflow_version=(
            ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
        ),
        step_id="finalize_document_understanding",
        output_schema_version=(
            ASR_DOCUMENT_UNDERSTANDING_STEP_OUTPUT_SCHEMA_VERSION
        ),
        error_namespace=(
            "ASR_DOCUMENT_UNDERSTANDING_FINALIZE"
        ),
        label="全文理解结果汇合",
        serialize_output=step_output_bytes,
        parse_output=parse_step_output,
    )
)

ASR_VISUAL_EVIDENCE_PREPARE_STEP_SPEC = ManagedLocalStepSpec[
    AsrVisualEvidencePreparedInputV2
](
    kind="english_asr",
    workflow_version=(
        ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="prepare_visual_input",
    output_schema_version=(
        ASR_VISUAL_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_VISUAL_EVIDENCE_PREPARE",
    label="画面证据输入锁定",
    serialize_output=visual_prepared_input_bytes,
    parse_output=parse_visual_prepared_input,
)

ASR_VISUAL_EVIDENCE_FINALIZE_STEP_SPEC = ManagedLocalStepSpec[
    AsrVisualEvidenceStepOutputV2
](
    kind="english_asr",
    workflow_version=(
        ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="finalize_visual_evidence",
    output_schema_version=(
        ASR_VISUAL_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_VISUAL_EVIDENCE_FINALIZE",
    label="画面证据结果汇合",
    serialize_output=visual_step_output_bytes,
    parse_output=parse_visual_step_output,
)

ASR_RESEARCH_EVIDENCE_PREPARE_STEP_SPEC = ManagedLocalStepSpec[
    AsrResearchEvidencePreparedInputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="prepare_research_input",
    output_schema_version=(
        ASR_RESEARCH_EVIDENCE_PREPARED_INPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_RESEARCH_EVIDENCE_PREPARE",
    label="资料查询输入锁定",
    serialize_output=research_prepared_input_bytes,
    parse_output=parse_research_prepared_input,
)

ASR_RESEARCH_EVIDENCE_FINALIZE_STEP_SPEC = ManagedLocalStepSpec[
    AsrResearchEvidenceStepOutputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="finalize_research_evidence",
    output_schema_version=(
        ASR_RESEARCH_EVIDENCE_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_RESEARCH_EVIDENCE_FINALIZE",
    label="资料查询结果汇合",
    serialize_output=research_step_output_bytes,
    parse_output=parse_research_step_output,
)

ASR_ENTITY_NORMALIZATION_PREPARE_STEP_SPEC = ManagedLocalStepSpec[
    AsrEntityNormalizationPreparedInputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="prepare_entity_normalization_input",
    output_schema_version=(
        ASR_ENTITY_NORMALIZATION_PREPARED_INPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_ENTITY_NORMALIZATION_PREPARE",
    label="名称与术语统一输入锁定",
    serialize_output=entity_prepared_input_bytes,
    parse_output=parse_entity_prepared_input,
)

ASR_ENTITY_NORMALIZATION_FINALIZE_STEP_SPEC = ManagedLocalStepSpec[
    AsrEntityNormalizationStepOutputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="finalize_entity_normalization",
    output_schema_version=(
        ASR_ENTITY_NORMALIZATION_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_ENTITY_NORMALIZATION_FINALIZE",
    label="名称与术语统一结果汇合",
    serialize_output=entity_step_output_bytes,
    parse_output=parse_entity_step_output,
)

ASR_SECTION_REVIEW_PREPARE_STEP_SPEC = ManagedLocalStepSpec[
    AsrSectionReviewPreparedInputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="prepare_section_review_input",
    output_schema_version=(
        ASR_SECTION_REVIEW_PREPARED_INPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_SECTION_REVIEW_PREPARE",
    label="分段复查输入锁定",
    serialize_output=section_review_prepared_input_bytes,
    parse_output=parse_section_review_prepared_input,
)

ASR_SECTION_REVIEW_FINALIZE_STEP_SPEC = ManagedLocalStepSpec[
    AsrSectionReviewStepOutputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="finalize_section_review",
    output_schema_version=(
        ASR_SECTION_REVIEW_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_SECTION_REVIEW_FINALIZE",
    label="分段复查结果汇合",
    serialize_output=section_review_step_output_bytes,
    parse_output=parse_section_review_step_output,
)

ASR_REVIEW_DECISIONS_PREPARE_STEP_SPEC = ManagedLocalStepSpec[
    AsrReviewDecisionsPreparedInputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="prepare_review_decisions_input",
    output_schema_version=(
        ASR_REVIEW_DECISIONS_PREPARED_INPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_REVIEW_DECISIONS_PREPARE",
    label="复查结论汇总输入锁定",
    serialize_output=review_decisions_prepared_input_bytes,
    parse_output=parse_review_decisions_prepared_input,
)

ASR_REVIEW_DECISIONS_FINALIZE_STEP_SPEC = ManagedLocalStepSpec[
    AsrReviewDecisionsStepOutputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="finalize_review_decisions",
    output_schema_version=(
        ASR_REVIEW_DECISIONS_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_REVIEW_DECISIONS_FINALIZE",
    label="复查结论汇总结果汇合",
    serialize_output=review_decisions_step_output_bytes,
    parse_output=parse_review_decisions_step_output,
)

ASR_WHOLE_RECHECK_PREPARE_STEP_SPEC = ManagedLocalStepSpec[
    AsrWholeRecheckPreparedInputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="prepare_whole_recheck_input",
    output_schema_version=(
        ASR_WHOLE_RECHECK_PREPARED_INPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_WHOLE_RECHECK_PREPARE",
    label="全文复核输入锁定",
    serialize_output=whole_recheck_prepared_input_bytes,
    parse_output=parse_whole_recheck_prepared_input,
)

ASR_WHOLE_RECHECK_FINALIZE_STEP_SPEC = ManagedLocalStepSpec[
    AsrWholeRecheckStepOutputV1
](
    kind="english_asr",
    workflow_version=(
        ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
    ),
    step_id="finalize_whole_recheck",
    output_schema_version=(
        ASR_WHOLE_RECHECK_STEP_OUTPUT_SCHEMA_VERSION
    ),
    error_namespace="ASR_WHOLE_RECHECK_FINALIZE",
    label="全文复核结果汇合",
    serialize_output=whole_recheck_step_output_bytes,
    parse_output=parse_whole_recheck_step_output,
)

ASR_TRANSCRIPT_QUALITY_GATE_PREPARE_STEP_SPEC = (
    ManagedLocalStepSpec[
        AsrTranscriptQualityGatePreparedInputV1
    ](
        kind="english_asr",
        workflow_version=(
            ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
        ),
        step_id="prepare_transcript_quality_gate_input",
        output_schema_version=(
            ASR_TRANSCRIPT_QUALITY_GATE_PREPARED_INPUT_SCHEMA_VERSION
        ),
        error_namespace="ASR_TRANSCRIPT_QUALITY_GATE_PREPARE",
        label="进入校时前检查输入锁定",
        serialize_output=(
            transcript_quality_gate_prepared_input_bytes
        ),
        parse_output=(
            parse_transcript_quality_gate_prepared_input
        ),
    )
)

ASR_TRANSCRIPT_QUALITY_GATE_FINALIZE_STEP_SPEC = (
    ManagedLocalStepSpec[
        AsrTranscriptQualityGateStepOutputV1
    ](
        kind="english_asr",
        workflow_version=(
            ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
        ),
        step_id="finalize_transcript_quality_gate",
        output_schema_version=(
            ASR_TRANSCRIPT_QUALITY_GATE_STEP_OUTPUT_SCHEMA_VERSION
        ),
        error_namespace="ASR_TRANSCRIPT_QUALITY_GATE_FINALIZE",
        label="进入校时前检查结果汇合",
        serialize_output=(
            transcript_quality_gate_step_output_bytes
        ),
        parse_output=(
            parse_transcript_quality_gate_step_output
        ),
    )
)


__all__ = [
    "ASR_ENTITY_NORMALIZATION_FINALIZE_STEP_SPEC",
    "ASR_ENTITY_NORMALIZATION_PREPARE_STEP_SPEC",
    "ASR_SECTION_REVIEW_FINALIZE_STEP_SPEC",
    "ASR_SECTION_REVIEW_PREPARE_STEP_SPEC",
    "ASR_REVIEW_DECISIONS_FINALIZE_STEP_SPEC",
    "ASR_REVIEW_DECISIONS_PREPARE_STEP_SPEC",
    "ASR_WHOLE_RECHECK_FINALIZE_STEP_SPEC",
    "ASR_WHOLE_RECHECK_PREPARE_STEP_SPEC",
    "ASR_TRANSCRIPT_QUALITY_GATE_FINALIZE_STEP_SPEC",
    "ASR_TRANSCRIPT_QUALITY_GATE_PREPARE_STEP_SPEC",
    "ASR_DOCUMENT_UNDERSTANDING_FINALIZE_STEP_SPEC",
    "ASR_DOCUMENT_UNDERSTANDING_PREPARE_STEP_SPEC",
    "ASR_VISUAL_EVIDENCE_FINALIZE_STEP_SPEC",
    "ASR_VISUAL_EVIDENCE_PREPARE_STEP_SPEC",
    "ASR_RESEARCH_EVIDENCE_FINALIZE_STEP_SPEC",
    "ASR_RESEARCH_EVIDENCE_PREPARE_STEP_SPEC",
    "ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_SPEC",
    "ASR_INITIAL_ANALYSIS_JOIN_STEP_SPEC",
    "ASR_INITIAL_ANALYSIS_RAW_STEP_SPEC",
    "ASR_RAW_STEP_SPEC",
    "REFERENCE_CANDIDATES_STEP_SPEC",
    "SPEAKER_DIARIZATION_STEP_SPEC",
    "SOURCE_AUDIO_STEP_SPEC",
    "STEM_SEPARATION_STEP_SPEC",
]
