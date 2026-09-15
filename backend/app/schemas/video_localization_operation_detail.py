from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from app.schemas.voice_studio import (
    VideoLocalizationGlossaryEntry,
)


OPERATION_DETAIL_CORE_SCHEMA_VERSION = "operation-detail-core-v1"
SEMANTIC_TTS_GROUPING_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "semantic-tts-grouping-detail-parameters-v1"
)
SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION = (
    "semantic-tts-grouping-workflow-v2"
)
SPEAKER_DIARIZATION_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "speaker-diarization-detail-parameters-v1"
)
SPEAKER_DIARIZATION_WORKFLOW_VERSION = (
    "speaker-diarization-workflow-v1"
)
ASR_RAW_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-raw-detail-parameters-v1"
)
ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-raw-development-workflow-v1"
)
ASR_INITIAL_ANALYSIS_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-initial-analysis-detail-parameters-v1"
)
ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-initial-analysis-development-workflow-v1"
)
ASR_DOCUMENT_UNDERSTANDING_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-document-understanding-detail-parameters-v1"
)
ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-document-understanding-development-workflow-v1"
)
ASR_VISUAL_EVIDENCE_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-visual-evidence-detail-parameters-v1"
)
ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-visual-evidence-development-workflow-v1"
)
ASR_RESEARCH_EVIDENCE_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-research-evidence-detail-parameters-v1"
)
ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-research-evidence-development-workflow-v1"
)
ASR_ENTITY_NORMALIZATION_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-entity-normalization-detail-parameters-v1"
)
ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-entity-normalization-development-workflow-v1"
)
ASR_SECTION_REVIEW_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-section-review-detail-parameters-v1"
)
ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-section-review-development-workflow-v1"
)
ASR_REVIEW_DECISIONS_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-review-decisions-detail-parameters-v1"
)
ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-review-decisions-development-workflow-v1"
)
ASR_WHOLE_RECHECK_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-whole-recheck-detail-parameters-v1"
)
ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-whole-recheck-development-workflow-v1"
)
ASR_TRANSCRIPT_QUALITY_GATE_DETAIL_PARAMETERS_SCHEMA_VERSION = (
    "asr-transcript-quality-gate-detail-parameters-v1"
)
ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION = (
    "asr-transcript-quality-gate-development-workflow-v1"
)
MAX_SPEAKER_COUNT_GUIDANCE = 50
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SemanticTtsGroupingDetailParametersV1(BaseModel):
    """Non-derivable public input for one semantic grouping operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "semantic-tts-grouping-detail-parameters-v1"
    ] = SEMANTIC_TTS_GROUPING_DETAIL_PARAMETERS_SCHEMA_VERSION
    profile_id: str = Field(min_length=1, max_length=64)
    profile_configuration_fingerprint: str
    target_chars: int = Field(ge=20, le=1_000)
    max_chars: int = Field(ge=20, le=2_000)

    @field_validator("profile_id")
    @classmethod
    def strip_profile_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("profile ID must not be empty")
        return normalized

    @field_validator("profile_configuration_fingerprint")
    @classmethod
    def validate_profile_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "profile configuration fingerprint must be lowercase SHA-256"
            )
        return normalized

    @model_validator(mode="after")
    def validate_limits(
        self,
    ) -> "SemanticTtsGroupingDetailParametersV1":
        if self.max_chars < self.target_chars:
            raise ValueError(
                "max_chars must be greater than or equal to target_chars"
            )
        return self


class SpeakerDiarizationDetailParametersV1(BaseModel):
    """Immutable public inputs for one standalone diarization run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "speaker-diarization-detail-parameters-v1"
    ] = SPEAKER_DIARIZATION_DETAIL_PARAMETERS_SCHEMA_VERSION
    engine_id: str = Field(min_length=1, max_length=128)
    source_track_id: Literal[
        "auto",
        "original",
        "vocals",
        "dub",
    ]
    min_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )
    max_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )

    @field_validator("engine_id")
    @classmethod
    def strip_engine_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("engine ID must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_speaker_range(
        self,
    ) -> "SpeakerDiarizationDetailParametersV1":
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError(
                "min_speakers must not exceed max_speakers"
            )
        return self


class AsrRawDetailParametersV1(BaseModel):
    """Immutable requested inputs for a raw-ASR development run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-raw-detail-parameters-v1"
    ] = ASR_RAW_DETAIL_PARAMETERS_SCHEMA_VERSION
    engine_id: str = Field(min_length=1, max_length=128)
    source_track_id: Literal[
        "auto",
        "original",
        "vocals",
        "dub",
    ]
    source_language: str = Field(min_length=1, max_length=32)

    @field_validator("engine_id", "source_language")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "ASR raw detail input must not be empty"
            )
        return normalized


class AsrInitialAnalysisDetailParametersV1(BaseModel):
    """Immutable requested inputs for the parallel initial-analysis run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-initial-analysis-detail-parameters-v1"
    ] = ASR_INITIAL_ANALYSIS_DETAIL_PARAMETERS_SCHEMA_VERSION
    engine_id: str = Field(min_length=1, max_length=128)
    diarization_engine_id: str = Field(
        min_length=1,
        max_length=128,
    )
    source_track_id: Literal[
        "auto",
        "original",
        "vocals",
        "dub",
    ]
    source_language: str = Field(min_length=1, max_length=32)
    min_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )
    max_speakers: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SPEAKER_COUNT_GUIDANCE,
    )

    @field_validator(
        "engine_id",
        "diarization_engine_id",
        "source_language",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "initial-analysis detail input must not be empty"
            )
        return normalized

    @model_validator(mode="after")
    def validate_speaker_range(
        self,
    ) -> "AsrInitialAnalysisDetailParametersV1":
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError(
                "min_speakers must not exceed max_speakers"
            )
        return self


class AsrDocumentUnderstandingDetailParametersV1(BaseModel):
    """Immutable upstream, model, behavior and scene-context identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-document-understanding-detail-parameters-v1"
    ] = ASR_DOCUMENT_UNDERSTANDING_DETAIL_PARAMETERS_SCHEMA_VERSION
    input_initial_analysis_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    profile_id: str = Field(min_length=1, max_length=128)
    profile_configuration_fingerprint: str
    behavior_fingerprint: str
    scene_context: str = Field(default="", max_length=4_000)

    @field_validator(
        "input_initial_analysis_operation_id",
        "profile_id",
    )
    @classmethod
    def strip_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "document understanding identity must not be empty"
            )
        return normalized

    @field_validator(
        "profile_configuration_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "document understanding fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrVisualEvidenceDetailParametersV1(BaseModel):
    """Immutable upstream, video, policy, model and behavior identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-visual-evidence-detail-parameters-v1"
    ] = ASR_VISUAL_EVIDENCE_DETAIL_PARAMETERS_SCHEMA_VERSION
    input_document_understanding_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    upstream_artifact_fingerprint: str
    video_sha256: str
    video_duration_ms: int = Field(ge=1)
    video_frame_rate: float = Field(gt=0, le=1_000)
    max_frames_per_question: int = Field(ge=1, le=4)
    max_total_frames: int = Field(ge=1, le=24)
    profile_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    profile_configuration_fingerprint: str | None = None
    behavior_fingerprint: str

    @field_validator(
        "input_document_understanding_operation_id",
    )
    @classmethod
    def strip_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "visual evidence upstream identity must not be empty"
            )
        return normalized

    @field_validator(
        "upstream_artifact_fingerprint",
        "video_sha256",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_required_fingerprint(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "visual evidence fingerprint must be lowercase SHA-256"
            )
        return normalized

    @field_validator(
        "profile_configuration_fingerprint",
    )
    @classmethod
    def validate_optional_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "visual evidence profile fingerprint must be lowercase SHA-256"
            )
        return normalized

    @model_validator(mode="after")
    def validate_profile_identity(
        self,
    ) -> "AsrVisualEvidenceDetailParametersV1":
        if bool(self.profile_id) != bool(
            self.profile_configuration_fingerprint
        ):
            raise ValueError(
                "visual evidence profile identity must be complete"
            )
        return self


class AsrResearchEvidenceDetailParametersV1(BaseModel):
    """Immutable upstream, policy, search, model and behavior identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-research-evidence-detail-parameters-v1"
    ] = ASR_RESEARCH_EVIDENCE_DETAIL_PARAMETERS_SCHEMA_VERSION
    input_document_understanding_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    document_artifact_fingerprint: str
    input_visual_evidence_operation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    visual_artifact_fingerprint: str | None = None
    max_research_rounds: int = Field(ge=1, le=3)
    max_research_queries: int = Field(ge=1, le=12)
    profile_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    profile_configuration_fingerprint: str | None = None
    search_configuration_fingerprint: str | None = None
    behavior_fingerprint: str

    @field_validator(
        "input_document_understanding_operation_id",
    )
    @classmethod
    def strip_required_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "research evidence upstream identity must not be empty"
            )
        return normalized

    @field_validator(
        "document_artifact_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_required_fingerprint(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "research evidence fingerprint must be lowercase SHA-256"
            )
        return normalized

    @field_validator(
        "visual_artifact_fingerprint",
        "profile_configuration_fingerprint",
        "search_configuration_fingerprint",
    )
    @classmethod
    def validate_optional_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "research evidence fingerprint must be lowercase SHA-256"
            )
        return normalized

    @model_validator(mode="after")
    def validate_optional_identities(
        self,
    ) -> "AsrResearchEvidenceDetailParametersV1":
        if bool(self.input_visual_evidence_operation_id) != bool(
            self.visual_artifact_fingerprint
        ):
            raise ValueError(
                "research visual upstream identity must be complete"
            )
        provider_fields = (
            self.profile_id,
            self.profile_configuration_fingerprint,
            self.search_configuration_fingerprint,
        )
        if any(provider_fields) and not all(provider_fields):
            raise ValueError(
                "research Provider identity must be complete"
            )
        return self


class AsrEntityNormalizationDetailParametersV1(BaseModel):
    """Immutable research, glossary, model and behavior identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-entity-normalization-detail-parameters-v1"
    ] = ASR_ENTITY_NORMALIZATION_DETAIL_PARAMETERS_SCHEMA_VERSION
    input_research_evidence_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    research_artifact_fingerprint: str
    glossary: tuple[VideoLocalizationGlossaryEntry, ...] = Field(
        default=(),
        max_length=500,
    )
    glossary_fingerprint: str
    profile_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    profile_configuration_fingerprint: str | None = None
    behavior_fingerprint: str

    @field_validator("input_research_evidence_operation_id")
    @classmethod
    def strip_upstream_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "entity-normalization upstream ID must not be empty"
            )
        return normalized

    @field_validator(
        "research_artifact_fingerprint",
        "glossary_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_required_fingerprint(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "entity-normalization fingerprint must be lowercase SHA-256"
            )
        return normalized

    @field_validator("profile_configuration_fingerprint")
    @classmethod
    def validate_optional_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "entity-normalization profile fingerprint is invalid"
            )
        return normalized

    @model_validator(mode="after")
    def validate_provider_identity(
        self,
    ) -> "AsrEntityNormalizationDetailParametersV1":
        if bool(self.profile_id) != bool(
            self.profile_configuration_fingerprint
        ):
            raise ValueError(
                "entity-normalization Provider identity is incomplete"
            )
        if len(
            json.dumps(
                [
                    item.model_dump(mode="json")
                    for item in self.glossary
                ],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ) > 262_144:
            raise ValueError(
                "entity-normalization glossary snapshot is too large"
            )
        return self


class AsrSectionReviewDetailParametersV1(BaseModel):
    """Immutable upstream, model and behavior identity for review round one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-section-review-detail-parameters-v1"
    ] = ASR_SECTION_REVIEW_DETAIL_PARAMETERS_SCHEMA_VERSION
    input_entity_normalization_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    entity_artifact_fingerprint: str
    input_document_understanding_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    document_artifact_fingerprint: str
    profile_id: str = Field(min_length=1, max_length=128)
    profile_configuration_fingerprint: str
    behavior_fingerprint: str

    @field_validator(
        "input_entity_normalization_operation_id",
        "input_document_understanding_operation_id",
        "profile_id",
    )
    @classmethod
    def strip_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "section-review identity must not be empty"
            )
        return normalized

    @field_validator(
        "entity_artifact_fingerprint",
        "document_artifact_fingerprint",
        "profile_configuration_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "section-review fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrReviewDecisionsDetailParametersV1(BaseModel):
    """Immutable upstream, model and behavior identity for adjudication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-review-decisions-detail-parameters-v1"
    ] = ASR_REVIEW_DECISIONS_DETAIL_PARAMETERS_SCHEMA_VERSION
    input_section_review_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    section_review_artifact_fingerprint: str
    profile_id: str = Field(min_length=1, max_length=128)
    profile_configuration_fingerprint: str | None = None
    behavior_fingerprint: str

    @field_validator(
        "input_section_review_operation_id",
        "profile_id",
    )
    @classmethod
    def strip_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "review-decisions identity must not be empty"
            )
        return normalized

    @field_validator(
        "section_review_artifact_fingerprint",
        "profile_configuration_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "review-decisions fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrWholeRecheckDetailParametersV1(BaseModel):
    """Immutable upstream, model and behavior identity for whole recheck."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-whole-recheck-detail-parameters-v1"
    ] = ASR_WHOLE_RECHECK_DETAIL_PARAMETERS_SCHEMA_VERSION
    input_review_decisions_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    review_decisions_artifact_fingerprint: str
    input_document_understanding_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    document_understanding_artifact_fingerprint: str
    profile_id: str = Field(min_length=1, max_length=128)
    profile_configuration_fingerprint: str
    behavior_fingerprint: str

    @field_validator(
        "input_review_decisions_operation_id",
        "input_document_understanding_operation_id",
        "profile_id",
    )
    @classmethod
    def strip_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "whole-recheck identity must not be empty"
            )
        return normalized

    @field_validator(
        "review_decisions_artifact_fingerprint",
        "document_understanding_artifact_fingerprint",
        "profile_configuration_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "whole-recheck fingerprint must be lowercase SHA-256"
            )
        return normalized


class AsrTranscriptQualityGateDetailParametersV1(BaseModel):
    """Immutable upstream and behavior identity for the local gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters_schema_version: Literal[
        "asr-transcript-quality-gate-detail-parameters-v1"
    ] = ASR_TRANSCRIPT_QUALITY_GATE_DETAIL_PARAMETERS_SCHEMA_VERSION
    input_whole_recheck_operation_id: str = Field(
        min_length=1,
        max_length=128,
    )
    whole_recheck_artifact_fingerprint: str
    behavior_fingerprint: str

    @field_validator("input_whole_recheck_operation_id")
    @classmethod
    def strip_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "transcript quality gate identity must not be empty"
            )
        return normalized

    @field_validator(
        "whole_recheck_artifact_fingerprint",
        "behavior_fingerprint",
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.strip()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError(
                "transcript quality gate fingerprint must be lowercase SHA-256"
            )
        return normalized


class OperationDetailCoreV1(BaseModel):
    """Small immutable detail-only input, never a second step-result blob."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    detail_schema_version: Literal[
        "operation-detail-core-v1"
    ] = OPERATION_DETAIL_CORE_SCHEMA_VERSION
    project_id: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)
    kind: Literal[
        "semantic_tts_grouping",
        "speaker_diarization",
        "english_asr",
    ]
    workflow_version: Literal[
        "semantic-tts-grouping-workflow-v2",
        "speaker-diarization-workflow-v1",
        "asr-raw-development-workflow-v1",
        "asr-initial-analysis-development-workflow-v1",
        "asr-document-understanding-development-workflow-v1",
        "asr-visual-evidence-development-workflow-v1",
        "asr-research-evidence-development-workflow-v1",
        "asr-entity-normalization-development-workflow-v1",
        "asr-section-review-development-workflow-v1",
        "asr-review-decisions-development-workflow-v1",
        "asr-whole-recheck-development-workflow-v1",
        "asr-transcript-quality-gate-development-workflow-v1",
    ] = SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION
    parameters: (
        SemanticTtsGroupingDetailParametersV1
        | SpeakerDiarizationDetailParametersV1
        | AsrRawDetailParametersV1
        | AsrInitialAnalysisDetailParametersV1
        | AsrDocumentUnderstandingDetailParametersV1
        | AsrVisualEvidenceDetailParametersV1
        | AsrResearchEvidenceDetailParametersV1
        | AsrEntityNormalizationDetailParametersV1
        | AsrSectionReviewDetailParametersV1
        | AsrReviewDecisionsDetailParametersV1
        | AsrWholeRecheckDetailParametersV1
        | AsrTranscriptQualityGateDetailParametersV1
    )

    @field_validator("project_id", "operation_id")
    @classmethod
    def strip_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("operation detail identity must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_workflow_identity(
        self,
    ) -> "OperationDetailCoreV1":
        expected = {
            (
                "semantic_tts_grouping",
                SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
            ): SemanticTtsGroupingDetailParametersV1,
            (
                "speaker_diarization",
                SPEAKER_DIARIZATION_WORKFLOW_VERSION,
            ): SpeakerDiarizationDetailParametersV1,
            (
                "english_asr",
                ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrRawDetailParametersV1,
            (
                "english_asr",
                ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrInitialAnalysisDetailParametersV1,
            (
                "english_asr",
                ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrDocumentUnderstandingDetailParametersV1,
            (
                "english_asr",
                ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrVisualEvidenceDetailParametersV1,
            (
                "english_asr",
                ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrResearchEvidenceDetailParametersV1,
            (
                "english_asr",
                ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrEntityNormalizationDetailParametersV1,
            (
                "english_asr",
                ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrSectionReviewDetailParametersV1,
            (
                "english_asr",
                ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrReviewDecisionsDetailParametersV1,
            (
                "english_asr",
                ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrWholeRecheckDetailParametersV1,
            (
                "english_asr",
                ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
            ): AsrTranscriptQualityGateDetailParametersV1,
        }
        parameter_type = expected.get(
            (self.kind, self.workflow_version)
        )
        if parameter_type is None:
            raise ValueError(
                "operation detail workflow does not match kind"
            )
        if not isinstance(self.parameters, parameter_type):
            raise ValueError(
                "operation detail parameters do not match kind"
            )
        return self


def operation_detail_core_json(
    core: OperationDetailCoreV1,
) -> str:
    return json.dumps(
        core.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def operation_detail_core_fingerprint(
    core: OperationDetailCoreV1,
) -> str:
    return hashlib.sha256(
        operation_detail_core_json(core).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ASR_ENTITY_NORMALIZATION_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_SECTION_REVIEW_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_REVIEW_DECISIONS_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_TRANSCRIPT_QUALITY_GATE_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_DOCUMENT_UNDERSTANDING_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_VISUAL_EVIDENCE_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_RESEARCH_EVIDENCE_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_INITIAL_ANALYSIS_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION",
    "ASR_RAW_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION",
    "OPERATION_DETAIL_CORE_SCHEMA_VERSION",
    "SPEAKER_DIARIZATION_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "SPEAKER_DIARIZATION_WORKFLOW_VERSION",
    "SEMANTIC_TTS_GROUPING_DETAIL_PARAMETERS_SCHEMA_VERSION",
    "SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION",
    "OperationDetailCoreV1",
    "AsrInitialAnalysisDetailParametersV1",
    "AsrDocumentUnderstandingDetailParametersV1",
    "AsrVisualEvidenceDetailParametersV1",
    "AsrResearchEvidenceDetailParametersV1",
    "AsrEntityNormalizationDetailParametersV1",
    "AsrSectionReviewDetailParametersV1",
    "AsrReviewDecisionsDetailParametersV1",
    "AsrWholeRecheckDetailParametersV1",
    "AsrTranscriptQualityGateDetailParametersV1",
    "AsrRawDetailParametersV1",
    "SpeakerDiarizationDetailParametersV1",
    "SemanticTtsGroupingDetailParametersV1",
    "operation_detail_core_fingerprint",
    "operation_detail_core_json",
]
