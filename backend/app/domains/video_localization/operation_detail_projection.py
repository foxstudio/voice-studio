from __future__ import annotations

from pydantic import ValidationError

from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.video_localization_operation_detail import (
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
    AsrDocumentUnderstandingDetailParametersV1,
    AsrEntityNormalizationDetailParametersV1,
    AsrSectionReviewDetailParametersV1,
    AsrReviewDecisionsDetailParametersV1,
    AsrTranscriptQualityGateDetailParametersV1,
    AsrWholeRecheckDetailParametersV1,
    AsrInitialAnalysisDetailParametersV1,
    AsrRawDetailParametersV1,
    AsrResearchEvidenceDetailParametersV1,
    AsrVisualEvidenceDetailParametersV1,
    OperationDetailCoreV1,
    SpeakerDiarizationDetailParametersV1,
    SemanticTtsGroupingDetailParametersV1,
)
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_WORKFLOW_VERSION,
)


class OperationDetailProjectionError(ValueError):
    """A supported workflow is missing immutable detail input."""


def detail_core_from_draft_operation(
    draft: VideoLocalizationDraft,
    operation_id: str,
    *,
    workflow_version: str | None = None,
) -> OperationDetailCoreV1 | None:
    """Project one explicitly targeted operation, never bulk-backfill history."""

    normalized_operation_id = str(operation_id or "").strip()
    if not normalized_operation_id:
        raise OperationDetailProjectionError(
            "detail shadow write requires an operation ID"
        )
    operation = next(
        (
            candidate
            for candidate in draft.operations
            if candidate.operation_id == normalized_operation_id
        ),
        None,
    )
    if operation is None:
        raise OperationDetailProjectionError(
            "detail shadow write operation is missing from the draft"
        )
    resolved_workflow_version = (
        str(workflow_version or "").strip()
        or _workflow_version_from_operation(operation)
    )
    return detail_core_from_operation(
        operation,
        workflow_version=resolved_workflow_version,
    )


def detail_core_from_operation(
    operation: VideoLocalizationOperation,
    *,
    workflow_version: str,
) -> OperationDetailCoreV1 | None:
    """Project only detail fields not owned by another durable source."""

    normalized_workflow_version = str(workflow_version or "").strip()
    parameters = operation.parameters
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=(
                    AsrTranscriptQualityGateDetailParametersV1(
                        input_whole_recheck_operation_id=parameters[
                            "input_whole_recheck_operation_id"
                        ],
                        whole_recheck_artifact_fingerprint=parameters[
                            "whole_recheck_artifact_fingerprint"
                        ],
                        behavior_fingerprint=parameters[
                            "behavior_fingerprint"
                        ],
                    )
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "transcript-quality-gate operation detail input is incomplete"
            ) from exc
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=AsrWholeRecheckDetailParametersV1(
                    input_review_decisions_operation_id=parameters[
                        "input_review_decisions_operation_id"
                    ],
                    review_decisions_artifact_fingerprint=parameters[
                        "review_decisions_artifact_fingerprint"
                    ],
                    input_document_understanding_operation_id=parameters[
                        "input_document_understanding_operation_id"
                    ],
                    document_understanding_artifact_fingerprint=parameters[
                        "document_understanding_artifact_fingerprint"
                    ],
                    profile_id=parameters["profile_id"],
                    profile_configuration_fingerprint=parameters[
                        "profile_configuration_fingerprint"
                    ],
                    behavior_fingerprint=parameters[
                        "behavior_fingerprint"
                    ],
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "whole-recheck operation detail input is incomplete"
            ) from exc
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=AsrReviewDecisionsDetailParametersV1(
                    input_section_review_operation_id=parameters[
                        "input_section_review_operation_id"
                    ],
                    section_review_artifact_fingerprint=parameters[
                        "section_review_artifact_fingerprint"
                    ],
                    profile_id=parameters["profile_id"],
                    profile_configuration_fingerprint=parameters.get(
                        "profile_configuration_fingerprint"
                    ),
                    behavior_fingerprint=parameters[
                        "behavior_fingerprint"
                    ],
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "review-decisions operation detail input is incomplete"
            ) from exc
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=AsrSectionReviewDetailParametersV1(
                    input_entity_normalization_operation_id=(
                        parameters[
                            "input_entity_normalization_operation_id"
                        ]
                    ),
                    entity_artifact_fingerprint=parameters[
                        "entity_artifact_fingerprint"
                    ],
                    input_document_understanding_operation_id=(
                        parameters[
                            "input_document_understanding_operation_id"
                        ]
                    ),
                    document_artifact_fingerprint=parameters[
                        "document_artifact_fingerprint"
                    ],
                    profile_id=parameters["profile_id"],
                    profile_configuration_fingerprint=(
                        parameters[
                            "profile_configuration_fingerprint"
                        ]
                    ),
                    behavior_fingerprint=parameters[
                        "behavior_fingerprint"
                    ],
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "section-review operation is missing durable detail input"
            ) from exc
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=(
                    AsrEntityNormalizationDetailParametersV1(
                        input_research_evidence_operation_id=(
                            parameters[
                                "input_research_evidence_operation_id"
                            ]
                        ),
                        research_artifact_fingerprint=(
                            parameters[
                                "research_artifact_fingerprint"
                            ]
                        ),
                        glossary=tuple(
                            parameters.get("glossary") or ()
                        ),
                        glossary_fingerprint=parameters[
                            "glossary_fingerprint"
                        ],
                        profile_id=parameters.get("profile_id"),
                        profile_configuration_fingerprint=(
                            parameters.get(
                                "profile_configuration_fingerprint"
                            )
                        ),
                        behavior_fingerprint=parameters[
                            "behavior_fingerprint"
                        ],
                    )
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "entity-normalization operation is missing durable detail input"
            ) from exc
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=AsrResearchEvidenceDetailParametersV1(
                    input_document_understanding_operation_id=(
                        parameters[
                            "input_document_understanding_operation_id"
                        ]
                    ),
                    document_artifact_fingerprint=(
                        parameters["document_artifact_fingerprint"]
                    ),
                    input_visual_evidence_operation_id=(
                        parameters.get(
                            "input_visual_evidence_operation_id"
                        )
                    ),
                    visual_artifact_fingerprint=(
                        parameters.get(
                            "visual_artifact_fingerprint"
                        )
                    ),
                    max_research_rounds=parameters[
                        "max_research_rounds"
                    ],
                    max_research_queries=parameters[
                        "max_research_queries"
                    ],
                    profile_id=parameters.get("profile_id"),
                    profile_configuration_fingerprint=(
                        parameters.get(
                            "profile_configuration_fingerprint"
                        )
                    ),
                    search_configuration_fingerprint=(
                        parameters.get(
                            "search_configuration_fingerprint"
                        )
                    ),
                    behavior_fingerprint=parameters[
                        "behavior_fingerprint"
                    ],
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "research evidence operation is missing durable detail input"
            ) from exc
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=AsrVisualEvidenceDetailParametersV1(
                    input_document_understanding_operation_id=(
                        parameters[
                            "input_document_understanding_operation_id"
                        ]
                    ),
                    upstream_artifact_fingerprint=(
                        parameters[
                            "upstream_artifact_fingerprint"
                        ]
                    ),
                    video_sha256=parameters["video_sha256"],
                    video_duration_ms=parameters[
                        "video_duration_ms"
                    ],
                    video_frame_rate=parameters[
                        "video_frame_rate"
                    ],
                    max_frames_per_question=parameters[
                        "max_frames_per_question"
                    ],
                    max_total_frames=parameters[
                        "max_total_frames"
                    ],
                    profile_id=parameters.get("profile_id"),
                    profile_configuration_fingerprint=(
                        parameters.get(
                            "profile_configuration_fingerprint"
                        )
                    ),
                    behavior_fingerprint=parameters[
                        "behavior_fingerprint"
                    ],
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "visual evidence operation is missing durable detail input"
            ) from exc
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=(
                    AsrDocumentUnderstandingDetailParametersV1(
                        input_initial_analysis_operation_id=(
                            parameters[
                                "input_initial_analysis_operation_id"
                            ]
                        ),
                        profile_id=parameters["profile_id"],
                        profile_configuration_fingerprint=(
                            parameters[
                                "profile_configuration_fingerprint"
                            ]
                        ),
                        behavior_fingerprint=parameters[
                            "behavior_fingerprint"
                        ],
                        scene_context=parameters.get(
                            "scene_context"
                        )
                        or "",
                    )
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "document understanding operation is missing durable detail input"
            ) from exc
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=AsrRawDetailParametersV1(
                    engine_id=parameters["engine_id"],
                    source_track_id=parameters[
                        "source_track_id"
                    ],
                    source_language=parameters[
                        "source_language"
                    ],
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "ASR raw operation is missing durable detail input"
            ) from exc
    if (
        operation.kind == "english_asr"
        and normalized_workflow_version
        == ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="english_asr",
                workflow_version=(
                    ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
                ),
                parameters=(
                    AsrInitialAnalysisDetailParametersV1(
                        engine_id=parameters["engine_id"],
                        diarization_engine_id=parameters[
                            "diarization_engine_id"
                        ],
                        source_track_id=parameters[
                            "source_track_id"
                        ],
                        source_language=parameters[
                            "source_language"
                        ],
                        min_speakers=parameters.get(
                            "min_speakers"
                        ),
                        max_speakers=parameters.get(
                            "max_speakers"
                        ),
                    )
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "initial-analysis operation is missing durable detail input"
            ) from exc
    if (
        operation.kind == "speaker_diarization"
        and normalized_workflow_version
        == SPEAKER_DIARIZATION_WORKFLOW_VERSION
    ):
        try:
            return OperationDetailCoreV1(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                kind="speaker_diarization",
                workflow_version=(
                    SPEAKER_DIARIZATION_WORKFLOW_VERSION
                ),
                parameters=(
                    SpeakerDiarizationDetailParametersV1(
                        engine_id=parameters["engine_id"],
                        source_track_id=parameters[
                            "source_track_id"
                        ],
                        min_speakers=parameters.get(
                            "min_speakers"
                        ),
                        max_speakers=parameters.get(
                            "max_speakers"
                        ),
                    )
                ),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise OperationDetailProjectionError(
                "speaker diarization operation is missing durable detail input"
            ) from exc
    if (
        operation.kind != "semantic_tts_grouping"
        or normalized_workflow_version
        != SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION
    ):
        return None
    try:
        return OperationDetailCoreV1(
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            kind="semantic_tts_grouping",
            workflow_version=SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
            parameters=SemanticTtsGroupingDetailParametersV1(
                profile_id=parameters["profile_id"],
                profile_configuration_fingerprint=parameters[
                    "profile_configuration_fingerprint"
                ],
                target_chars=parameters["target_chars"],
                max_chars=parameters["max_chars"],
            ),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise OperationDetailProjectionError(
            "semantic grouping operation is missing durable detail input"
        ) from exc


def _workflow_version_from_operation(
    operation: VideoLocalizationOperation,
) -> str:
    result_summary = operation.result_summary
    return (
        str(
            result_summary.get("workflow_schema_version")
            if isinstance(result_summary, dict)
            else ""
        ).strip()
        or "operation-v1"
    )


__all__ = [
    "OperationDetailProjectionError",
    "detail_core_from_draft_operation",
    "detail_core_from_operation",
]
