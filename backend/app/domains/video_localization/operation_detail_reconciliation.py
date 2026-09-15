from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Callable, Literal

from pydantic import ValidationError

from app.domains.video_localization import (
    asr_document_understanding_result_reader,
    asr_entity_normalization_result_reader,
    asr_section_review_result_reader,
    asr_research_evidence_result_reader,
    asr_visual_evidence_result_reader,
    managed_artifact_files,
    operation_detail_projection,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
)
from app.schemas.video_localization_semantic_tts_grouping_step import (
    SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION,
    parse_semantic_tts_grouping_round_artifact,
)
from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_STEP_ID,
    SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION,
    SOURCE_AUDIO_WORKFLOW_VERSION,
    parse_source_audio_step_output,
)
from app.schemas.video_localization_stem_separation_step import (
    STEM_SEPARATION_STEP_ID,
    STEM_SEPARATION_STEP_OUTPUT_SCHEMA_VERSION,
    STEM_SEPARATION_WORKFLOW_VERSION,
    parse_stem_separation_step_output,
)
from app.schemas.video_localization_reference_candidates_step import (
    REFERENCE_CANDIDATES_STEP_ID,
    REFERENCE_CANDIDATES_STEP_OUTPUT_SCHEMA_VERSION,
    REFERENCE_CANDIDATES_WORKFLOW_VERSION,
    parse_reference_candidates_step_output,
)
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_STEP_ID,
    SPEAKER_DIARIZATION_STEP_OUTPUT_SCHEMA_VERSION,
    SPEAKER_DIARIZATION_WORKFLOW_VERSION,
    parse_speaker_diarization_step_output,
)
from app.schemas.video_localization_asr_raw_step import (
    ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RAW_STEP_ID,
    ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION,
    parse_asr_raw_step_output,
)
from app.schemas.video_localization_asr_initial_analysis_step import (
    ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION,
    ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_ID,
    ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION,
    ASR_INITIAL_ANALYSIS_JOIN_STEP_ID,
    parse_asr_initial_analysis_diarization_output,
    parse_asr_initial_analysis_join_output,
)
from app.schemas.video_localization_asr_document_understanding_step import (
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_visual_evidence_step import (
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_research_evidence_step import (
    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_entity_normalization_step import (
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_section_review_step import (
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_review_decisions_step import (
    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_whole_recheck_step import (
    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_asr_transcript_quality_gate_step import (
    ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.domains.video_localization import (
    asr_review_decisions_result_reader,
    asr_transcript_quality_gate_result_reader,
    asr_whole_recheck_result_reader,
)
from app.services import database
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)
from app.services import (
    video_localization_operation_store as operation_store,
)


DetailReconciliationStatus = Literal[
    "matched",
    "missing",
    "incomplete",
    "mismatch",
    "invalid",
]
DetailReconciliationIssueCode = Literal[
    "project_missing",
    "mirror_operation_missing",
    "mirror_operation_invalid",
    "ledger_operation_missing",
    "unsupported_workflow",
    "detail_core_missing",
    "detail_core_invalid",
    "mirror_identity_mismatch",
    "mirror_lifecycle_mismatch",
    "mirror_parameters_mismatch",
    "detail_core_mismatch",
    "step_state_invalid",
    "step_workflow_mismatch",
    "successful_workflow_step_missing",
    "successful_step_artifact_missing",
    "artifact_state_invalid",
    "artifact_fingerprint_mismatch",
    "artifact_contract_invalid",
]

_ROUND_STEP = re.compile(r"^semantic_grouping_round_([12])$")
_INVALID_ISSUES = frozenset(
    {
        "mirror_operation_invalid",
        "detail_core_invalid",
        "step_state_invalid",
        "artifact_state_invalid",
        "artifact_contract_invalid",
    }
)
_MISMATCH_ISSUES = frozenset(
    {
        "mirror_identity_mismatch",
        "mirror_lifecycle_mismatch",
        "mirror_parameters_mismatch",
        "detail_core_mismatch",
        "step_workflow_mismatch",
        "artifact_fingerprint_mismatch",
    }
)
_MISSING_ISSUES = frozenset(
    {
        "detail_core_missing",
        "successful_step_artifact_missing",
    }
)


@dataclass(frozen=True)
class DetailReconciliation:
    """Bounded, path-free result for one operation authority comparison."""

    project_id: str
    operation_id: str
    status: DetailReconciliationStatus
    workflow_version: str | None
    ledger_status: str | None
    checked_artifact_count: int
    issues: tuple[DetailReconciliationIssueCode, ...]

    @property
    def matched(self) -> bool:
        return self.status == "matched"


@dataclass(frozen=True)
class DetailReconciliationReport:
    """Bounded inventory for workflows using managed detail authority."""

    total_candidate_count: int
    checked_candidate_count: int
    truncated: bool
    status_counts: dict[str, int]
    results: tuple[DetailReconciliation, ...]

    @property
    def healthy(self) -> bool:
        return (
            not self.truncated
            and all(result.matched for result in self.results)
        )


@dataclass(frozen=True)
class _LocalStepAuditSpec:
    step_id: str
    workflow_version: str
    output_schema_version: str
    parse_output: Callable[[bytes], object]


_SOURCE_AUDIO_AUDIT_SPEC = _LocalStepAuditSpec(
    step_id=SOURCE_AUDIO_STEP_ID,
    workflow_version=SOURCE_AUDIO_WORKFLOW_VERSION,
    output_schema_version=(
        SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION
    ),
    parse_output=parse_source_audio_step_output,
)
_STEM_SEPARATION_AUDIT_SPEC = _LocalStepAuditSpec(
    step_id=STEM_SEPARATION_STEP_ID,
    workflow_version=STEM_SEPARATION_WORKFLOW_VERSION,
    output_schema_version=(
        STEM_SEPARATION_STEP_OUTPUT_SCHEMA_VERSION
    ),
    parse_output=parse_stem_separation_step_output,
)
_REFERENCE_CANDIDATES_AUDIT_SPEC = _LocalStepAuditSpec(
    step_id=REFERENCE_CANDIDATES_STEP_ID,
    workflow_version=REFERENCE_CANDIDATES_WORKFLOW_VERSION,
    output_schema_version=(
        REFERENCE_CANDIDATES_STEP_OUTPUT_SCHEMA_VERSION
    ),
    parse_output=parse_reference_candidates_step_output,
)
_SPEAKER_DIARIZATION_AUDIT_SPEC = _LocalStepAuditSpec(
    step_id=SPEAKER_DIARIZATION_STEP_ID,
    workflow_version=SPEAKER_DIARIZATION_WORKFLOW_VERSION,
    output_schema_version=(
        SPEAKER_DIARIZATION_STEP_OUTPUT_SCHEMA_VERSION
    ),
    parse_output=parse_speaker_diarization_step_output,
)
_ASR_RAW_AUDIT_SPEC = _LocalStepAuditSpec(
    step_id=ASR_RAW_STEP_ID,
    workflow_version=(
        ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION
    ),
    output_schema_version=(
        ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION
    ),
    parse_output=parse_asr_raw_step_output,
)
_ASR_INITIAL_ANALYSIS_AUDIT_SPECS = (
    _LocalStepAuditSpec(
        step_id=ASR_RAW_STEP_ID,
        workflow_version=(
            ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
        ),
        output_schema_version=(
            ASR_RAW_STEP_OUTPUT_SCHEMA_VERSION
        ),
        parse_output=parse_asr_raw_step_output,
    ),
    _LocalStepAuditSpec(
        step_id=ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_ID,
        workflow_version=(
            ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
        ),
        output_schema_version=(
            ASR_INITIAL_ANALYSIS_DIARIZATION_OUTPUT_SCHEMA_VERSION
        ),
        parse_output=(
            parse_asr_initial_analysis_diarization_output
        ),
    ),
    _LocalStepAuditSpec(
        step_id=ASR_INITIAL_ANALYSIS_JOIN_STEP_ID,
        workflow_version=(
            ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
        ),
        output_schema_version=(
            ASR_INITIAL_ANALYSIS_JOIN_OUTPUT_SCHEMA_VERSION
        ),
        parse_output=parse_asr_initial_analysis_join_output,
    ),
)


def reconcile_operation_details(
    *,
    limit: int = 1_000,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
) -> DetailReconciliationReport:
    """Audit all managed workflow candidates in one bounded read snapshot."""

    if limit < 1 or limit > 10_000:
        raise ValueError(
            "detail reconciliation limit must be between 1 and 10000"
        )
    with database.read_conn() as connection:
        rows = connection.execute(
            """
            WITH candidates(project_id, operation_id) AS (
                SELECT project_id, operation_id
                FROM video_localization_operations
                WHERE workflow_version IN (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                UNION
                SELECT project_id, operation_id
                FROM video_localization_operation_detail_cores
                UNION
                SELECT
                    projects.project_id,
                    json_extract(
                        operation.value,
                        '$.operation_id'
                    )
                FROM projects
                JOIN json_each(
                    CASE
                        WHEN json_valid(projects.data)
                        THEN projects.data
                        ELSE '{}'
                    END,
                    '$.parameters.video_localization.operations'
                ) AS operation
                WHERE json_extract(
                    operation.value,
                    '$.result_summary.workflow_schema_version'
                ) IN (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            )
            SELECT
                project_id,
                operation_id,
                COUNT(*) OVER () AS total_candidate_count
            FROM candidates
            WHERE project_id IS NOT NULL
              AND operation_id IS NOT NULL
            ORDER BY project_id, operation_id
            LIMIT ?
            """,
            (
                SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
                SOURCE_AUDIO_WORKFLOW_VERSION,
                STEM_SEPARATION_WORKFLOW_VERSION,
                REFERENCE_CANDIDATES_WORKFLOW_VERSION,
                SPEAKER_DIARIZATION_WORKFLOW_VERSION,
                ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
                SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
                SOURCE_AUDIO_WORKFLOW_VERSION,
                STEM_SEPARATION_WORKFLOW_VERSION,
                REFERENCE_CANDIDATES_WORKFLOW_VERSION,
                SPEAKER_DIARIZATION_WORKFLOW_VERSION,
                ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
                ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
                limit + 1,
            ),
        ).fetchall()
        total_candidate_count = (
            int(rows[0]["total_candidate_count"]) if rows else 0
        )
        checked_rows = rows[:limit]
        results = tuple(
            reconcile_operation_detail_from_connection(
                connection,
                str(row["project_id"]),
                str(row["operation_id"]),
                file_backend=file_backend,
            )
            for row in checked_rows
        )
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.status] = (
            status_counts.get(result.status, 0) + 1
        )
    return DetailReconciliationReport(
        total_candidate_count=total_candidate_count,
        checked_candidate_count=len(results),
        truncated=total_candidate_count > len(results),
        status_counts=status_counts,
        results=results,
    )


def reconcile_operation_detail(
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
) -> DetailReconciliation:
    """Audit ledger, core, artifacts and mirror in one query-only snapshot."""

    normalized_project_id = _required_identity(
        project_id,
        "project ID",
    )
    normalized_operation_id = _required_identity(
        operation_id,
        "operation ID",
    )
    with database.read_conn() as connection:
        return reconcile_operation_detail_from_connection(
            connection,
            normalized_project_id,
            normalized_operation_id,
            file_backend=file_backend,
        )


def reconcile_operation_detail_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> DetailReconciliation:
    """Compare one operation without opening another database connection."""

    normalized_project_id = _required_identity(
        project_id,
        "project ID",
    )
    normalized_operation_id = _required_identity(
        operation_id,
        "operation ID",
    )
    issues: list[DetailReconciliationIssueCode] = []

    def add(issue: DetailReconciliationIssueCode) -> None:
        if issue not in issues:
            issues.append(issue)

    ledger = connection.execute(
        """
        SELECT
            project_id,
            operation_id,
            kind,
            status,
            cancel_requested,
            parameters_fingerprint,
            workflow_version,
            created_at,
            completed_at
        FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
        """,
        (normalized_project_id, normalized_operation_id),
    ).fetchone()
    workflow_version = (
        str(ledger["workflow_version"])
        if ledger is not None
        else None
    )
    ledger_status = (
        str(ledger["status"]) if ledger is not None else None
    )
    if ledger is None:
        add("ledger_operation_missing")

    try:
        project_exists, mirror_payload = (
            operation_store
            .read_project_mirror_operation_from_connection(
                connection,
                normalized_project_id,
                normalized_operation_id,
            )
        )
    except (sqlite3.DatabaseError, TypeError, ValueError):
        project_exists = True
        mirror_payload = None
        add("mirror_operation_invalid")
    if not project_exists:
        add("project_missing")
    elif mirror_payload is None and (
        "mirror_operation_invalid" not in issues
    ):
        add("mirror_operation_missing")

    detail_record = None
    try:
        detail_record = detail_store.get_detail_core_from_connection(
            connection,
            normalized_project_id,
            normalized_operation_id,
        )
    except (
        detail_store.OperationDetailCoreIdentityConflict,
        detail_store.OperationDetailCoreIntegrityError,
        detail_store.OperationDetailCoreSchemaError,
        TypeError,
        ValueError,
    ):
        add("detail_core_invalid")

    supported_semantic = (
        ledger is not None
        and str(ledger["kind"]) == "semantic_tts_grouping"
        and workflow_version
        == SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION
    )
    supported_source_audio = (
        ledger is not None
        and str(ledger["kind"]) == "source_audio"
        and workflow_version == SOURCE_AUDIO_WORKFLOW_VERSION
    )
    supported_stem_separation = (
        ledger is not None
        and str(ledger["kind"]) == "stems"
        and workflow_version
        == STEM_SEPARATION_WORKFLOW_VERSION
    )
    supported_reference_candidates = (
        ledger is not None
        and str(ledger["kind"]) == "reference_clips"
        and workflow_version
        == REFERENCE_CANDIDATES_WORKFLOW_VERSION
    )
    supported_speaker_diarization = (
        ledger is not None
        and str(ledger["kind"]) == "speaker_diarization"
        and workflow_version
        == SPEAKER_DIARIZATION_WORKFLOW_VERSION
    )
    supported_asr_raw = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION
    )
    supported_initial_analysis = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
    )
    supported_document_understanding = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == (
            ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
        )
    )
    supported_visual_evidence = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    )
    supported_research_evidence = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    )
    supported_entity_normalization = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
    )
    supported_section_review = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
    )
    supported_review_decisions = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
    )
    supported_whole_recheck = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
    )
    supported_transcript_quality_gate = (
        ledger is not None
        and str(ledger["kind"]) == "english_asr"
        and workflow_version
        == ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
    )
    supported_zero_core_local = (
        supported_source_audio
        or supported_stem_separation
        or supported_reference_candidates
    )
    supported_local = (
        supported_zero_core_local
        or supported_speaker_diarization
        or supported_asr_raw
        or supported_initial_analysis
        or supported_document_understanding
        or supported_visual_evidence
        or supported_research_evidence
        or supported_entity_normalization
        or supported_section_review
        or supported_review_decisions
        or supported_whole_recheck
        or supported_transcript_quality_gate
    )
    supported = supported_semantic or supported_local
    if ledger is not None and not supported:
        add("unsupported_workflow")
    elif (
        supported_semantic
        or supported_speaker_diarization
        or supported_asr_raw
        or supported_initial_analysis
        or supported_document_understanding
        or supported_visual_evidence
        or supported_research_evidence
        or supported_entity_normalization
        or supported_section_review
        or supported_review_decisions
        or supported_whole_recheck
        or supported_transcript_quality_gate
    ) and detail_record is None and (
        "detail_core_invalid" not in issues
    ):
        add("detail_core_missing")
    elif (
        supported_zero_core_local
        and detail_record is not None
    ):
        add("detail_core_mismatch")

    mirror_operation = None
    if mirror_payload is not None:
        try:
            mirror_operation = (
                VideoLocalizationOperation.model_validate(
                    mirror_payload
                )
            )
        except (TypeError, ValidationError, ValueError):
            add("mirror_operation_invalid")

    if ledger is not None and mirror_operation is not None:
        if (
            mirror_operation.project_id
            != normalized_project_id
            or mirror_operation.operation_id
            != normalized_operation_id
            or mirror_operation.kind != str(ledger["kind"])
            or mirror_operation.created_at
            != str(ledger["created_at"])
        ):
            add("mirror_identity_mismatch")
        if (
            mirror_operation.status != ledger_status
            or mirror_operation.cancel_requested
            != bool(ledger["cancel_requested"])
            or mirror_operation.completed_at
            != (
                str(ledger["completed_at"])
                if ledger["completed_at"] is not None
                else None
            )
            or _mirror_workflow_version(mirror_operation)
            != workflow_version
        ):
            add("mirror_lifecycle_mismatch")
        if (
            ledger_store.parameters_fingerprint(
                mirror_operation.parameters
            )
            != str(ledger["parameters_fingerprint"])
        ):
            add("mirror_parameters_mismatch")
        if (
            supported_semantic
            or supported_speaker_diarization
            or supported_asr_raw
            or supported_initial_analysis
            or supported_document_understanding
            or supported_visual_evidence
            or supported_research_evidence
            or supported_entity_normalization
            or supported_section_review
            or supported_review_decisions
            or supported_whole_recheck
            or supported_transcript_quality_gate
        ) and detail_record is not None:
            try:
                expected_core = (
                    operation_detail_projection
                    .detail_core_from_operation(
                        mirror_operation,
                        workflow_version=workflow_version or "",
                    )
                )
            except (
                operation_detail_projection
                .OperationDetailProjectionError
            ):
                add("mirror_operation_invalid")
            else:
                if expected_core != detail_record.core:
                    add("detail_core_mismatch")

    checked_artifact_count = 0
    if supported:
        try:
            steps = step_store.list_step_attempts_from_connection(
                connection,
                normalized_project_id,
                normalized_operation_id,
            )
        except (
            step_store.StepSchemaError,
            TypeError,
            ValueError,
        ):
            steps = []
            add("step_state_invalid")
        if supported_semantic:
            checked_artifact_count = (
                _reconcile_semantic_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    workflow_version or "",
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_document_understanding:
            checked_artifact_count = (
                _reconcile_document_understanding_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_visual_evidence:
            checked_artifact_count = (
                _reconcile_visual_evidence_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_research_evidence:
            checked_artifact_count = (
                _reconcile_research_evidence_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_entity_normalization:
            checked_artifact_count = (
                _reconcile_entity_normalization_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_section_review:
            checked_artifact_count = (
                _reconcile_section_review_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_review_decisions:
            checked_artifact_count = (
                _reconcile_review_decisions_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_whole_recheck:
            checked_artifact_count = (
                _reconcile_whole_recheck_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_transcript_quality_gate:
            checked_artifact_count = (
                _reconcile_transcript_quality_gate_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_source_audio:
            checked_artifact_count = (
                _reconcile_local_step(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    workflow_version or "",
                    ledger_status,
                    steps,
                    spec=_SOURCE_AUDIO_AUDIT_SPEC,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_stem_separation:
            checked_artifact_count = (
                _reconcile_local_step(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    workflow_version or "",
                    ledger_status,
                    steps,
                    spec=_STEM_SEPARATION_AUDIT_SPEC,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_reference_candidates:
            checked_artifact_count = (
                _reconcile_local_step(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    workflow_version or "",
                    ledger_status,
                    steps,
                    spec=_REFERENCE_CANDIDATES_AUDIT_SPEC,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_speaker_diarization:
            checked_artifact_count = (
                _reconcile_local_step(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    workflow_version or "",
                    ledger_status,
                    steps,
                    spec=_SPEAKER_DIARIZATION_AUDIT_SPEC,
                    file_backend=file_backend,
                    add=add,
                )
            )
        elif supported_initial_analysis:
            checked_artifact_count = (
                _reconcile_initial_analysis_steps(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    workflow_version or "",
                    ledger_status,
                    steps,
                    file_backend=file_backend,
                    add=add,
                )
            )
        else:
            checked_artifact_count = (
                _reconcile_local_step(
                    connection,
                    normalized_project_id,
                    normalized_operation_id,
                    workflow_version or "",
                    ledger_status,
                    steps,
                    spec=_ASR_RAW_AUDIT_SPEC,
                    file_backend=file_backend,
                    add=add,
                )
            )

    return DetailReconciliation(
        project_id=normalized_project_id,
        operation_id=normalized_operation_id,
        status=_status_for_issues(issues),
        workflow_version=workflow_version,
        ledger_status=ledger_status,
        checked_artifact_count=checked_artifact_count,
        issues=tuple(issues),
    )


def _reconcile_semantic_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    workflow_version: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    checked_artifact_count = 0
    successful_round_count = 0
    for step in steps:
        round_match = _ROUND_STEP.fullmatch(step.step_id)
        if round_match is None:
            continue
        if step.workflow_version != workflow_version:
            add("step_workflow_mismatch")
        if step.status != "success":
            continue
        successful_round_count += 1
        verified = _read_verified_step_artifact(
            connection,
            project_id,
            operation_id,
            step,
            file_backend=file_backend,
            add=add,
        )
        if verified is None:
            continue
        checked_artifact_count += 1
        if (
            verified.artifact.payload_schema_version
            != SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION
            or verified.artifact.media_type != "application/json"
        ):
            add("artifact_contract_invalid")
            continue
        try:
            round_artifact = (
                parse_semantic_tts_grouping_round_artifact(
                    verified.content
                )
            )
        except (TypeError, ValueError):
            add("artifact_contract_invalid")
            continue
        if round_artifact.round_index != int(
            round_match.group(1)
        ):
            add("artifact_contract_invalid")
    if ledger_status == "success" and successful_round_count == 0:
        add("successful_workflow_step_missing")
    return checked_artifact_count


def _reconcile_local_step(
    connection: Connection,
    project_id: str,
    operation_id: str,
    workflow_version: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    spec: _LocalStepAuditSpec,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    return _reconcile_local_steps(
        connection,
        project_id,
        operation_id,
        workflow_version,
        ledger_status,
        steps,
        specs=(spec,),
        file_backend=file_backend,
        add=add,
    )


def _reconcile_initial_analysis_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    workflow_version: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    checked = _reconcile_local_steps(
        connection,
        project_id,
        operation_id,
        workflow_version,
        ledger_status,
        steps,
        specs=_ASR_INITIAL_ANALYSIS_AUDIT_SPECS,
        file_backend=file_backend,
        add=add,
    )
    successful = {
        step_id: max(
            (
                step
                for step in steps
                if step.step_id == step_id
                and step.status == "success"
            ),
            key=lambda step: (
                step.prepared_at,
                step.step_attempt_number,
                step.step_attempt_id,
            ),
            default=None,
        )
        for step_id in {
            ASR_RAW_STEP_ID,
            ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_ID,
            ASR_INITIAL_ANALYSIS_JOIN_STEP_ID,
        }
    }
    if any(step is None for step in successful.values()):
        return checked
    parsed: dict[str, object] = {}
    for spec in _ASR_INITIAL_ANALYSIS_AUDIT_SPECS:
        step = successful[spec.step_id]
        assert step is not None
        verified = _read_verified_step_artifact(
            connection,
            project_id,
            operation_id,
            step,
            file_backend=file_backend,
            add=add,
        )
        if verified is None:
            return checked
        try:
            parsed[spec.step_id] = spec.parse_output(
                verified.content
            )
        except (TypeError, ValueError):
            return checked
    raw = parsed[ASR_RAW_STEP_ID]
    diarization = parsed[
        ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_ID
    ]
    join = parsed[ASR_INITIAL_ANALYSIS_JOIN_STEP_ID]
    raw_step = successful[ASR_RAW_STEP_ID]
    diarization_step = successful[
        ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_ID
    ]
    assert raw_step is not None
    assert diarization_step is not None
    if (
        join.raw_artifact_fingerprint
        != raw_step.output_fingerprint
        or join.diarization_artifact_fingerprint
        != diarization_step.output_fingerprint
    ):
        add("artifact_fingerprint_mismatch")
    if (
        raw.input.audio_sha256
        != diarization.input.audio_sha256
        or raw.input.source_track_id
        != diarization.input.source_track_id
        or join.audio_sha256 != raw.input.audio_sha256
        or join.source_track_id
        != raw.input.source_track_id
    ):
        add("artifact_contract_invalid")
    return checked


def _reconcile_document_understanding_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    try:
        result = (
            asr_document_understanding_result_reader
            .read_result_from_connection(
                connection,
                project_id,
                operation_id,
                file_backend=file_backend,
            )
        )
    except OperationDetailRepairRequired as exc:
        for issue_code in exc.issue_codes:
            if "workflow" in issue_code:
                add("step_workflow_mismatch")
            elif "fingerprint" in issue_code:
                add("artifact_fingerprint_mismatch")
            elif "missing" in issue_code:
                add("successful_workflow_step_missing")
            elif "artifact" in issue_code:
                add("artifact_contract_invalid")
            else:
                add("artifact_state_invalid")
        return 0
    if ledger_status == "success" and result is None:
        add("successful_workflow_step_missing")
    return sum(
        1 for step in steps if step.status == "success"
    )


def _reconcile_visual_evidence_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    try:
        authority = (
            asr_visual_evidence_result_reader
            .read_success_from_connection(
                connection,
                project_id,
                operation_id,
                file_backend=file_backend,
            )
        )
    except OperationDetailRepairRequired as exc:
        for issue_code in exc.issue_codes:
            if "workflow" in issue_code:
                add("step_workflow_mismatch")
            elif "fingerprint" in issue_code:
                add("artifact_fingerprint_mismatch")
            elif "missing" in issue_code:
                add("successful_workflow_step_missing")
            elif "artifact" in issue_code:
                add("artifact_contract_invalid")
            else:
                add("artifact_state_invalid")
        return 0
    if ledger_status == "success" and authority is None:
        add("successful_workflow_step_missing")
    row = connection.execute(
        """
        SELECT COUNT(*) AS artifact_count
        FROM video_localization_operation_artifacts
        WHERE project_id = ?
          AND operation_id = ?
          AND status = 'committed'
        """,
        (project_id, operation_id),
    ).fetchone()
    return int(row["artifact_count"] or 0)


def _reconcile_research_evidence_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    try:
        result = (
            asr_research_evidence_result_reader
            .read_success_from_connection(
                connection,
                project_id,
                operation_id,
                file_backend=file_backend,
            )
        )
    except OperationDetailRepairRequired as exc:
        for issue_code in exc.issue_codes:
            if "workflow" in issue_code:
                add("step_workflow_mismatch")
            elif "fingerprint" in issue_code:
                add("artifact_fingerprint_mismatch")
            elif "missing" in issue_code:
                add("successful_workflow_step_missing")
            elif "artifact" in issue_code:
                add("artifact_contract_invalid")
            else:
                add("artifact_state_invalid")
        return 0
    if ledger_status == "success" and result is None:
        add("successful_workflow_step_missing")
    row = connection.execute(
        """
        SELECT COUNT(*) AS artifact_count
        FROM video_localization_operation_artifacts
        WHERE project_id = ?
          AND operation_id = ?
          AND status = 'committed'
        """,
        (project_id, operation_id),
    ).fetchone()
    return int(row["artifact_count"] or 0)


def _reconcile_entity_normalization_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    try:
        result = (
            asr_entity_normalization_result_reader
            .read_success_from_connection(
                connection,
                project_id,
                operation_id,
                file_backend=file_backend,
            )
        )
    except OperationDetailRepairRequired as exc:
        for issue_code in exc.issue_codes:
            if "workflow" in issue_code:
                add("step_workflow_mismatch")
            elif "fingerprint" in issue_code:
                add("artifact_fingerprint_mismatch")
            elif "missing" in issue_code:
                add("successful_workflow_step_missing")
            elif "artifact" in issue_code:
                add("artifact_contract_invalid")
            else:
                add("artifact_state_invalid")
        return 0
    if ledger_status == "success" and result is None:
        add("successful_workflow_step_missing")
    row = connection.execute(
        """
        SELECT COUNT(*) AS artifact_count
        FROM video_localization_operation_artifacts
        WHERE project_id = ?
          AND operation_id = ?
          AND status = 'committed'
        """,
        (project_id, operation_id),
    ).fetchone()
    return int(row["artifact_count"] or 0)


def _reconcile_section_review_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    try:
        result = (
            asr_section_review_result_reader
            .read_result_from_connection(
                connection,
                project_id,
                operation_id,
                file_backend=file_backend,
            )
        )
    except OperationDetailRepairRequired as exc:
        for issue_code in exc.issue_codes:
            if "workflow" in issue_code:
                add("step_workflow_mismatch")
            elif "fingerprint" in issue_code:
                add("artifact_fingerprint_mismatch")
            elif "missing" in issue_code:
                add("successful_workflow_step_missing")
            elif "artifact" in issue_code:
                add("artifact_contract_invalid")
            else:
                add("artifact_state_invalid")
        return 0
    if ledger_status == "success" and result is None:
        add("successful_workflow_step_missing")
    row = connection.execute(
        """
        SELECT COUNT(*) AS artifact_count
        FROM video_localization_operation_artifacts
        WHERE project_id = ?
          AND operation_id = ?
          AND status = 'committed'
        """,
        (project_id, operation_id),
    ).fetchone()
    return int(row["artifact_count"] or 0)


def _reconcile_review_decisions_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    try:
        result = (
            asr_review_decisions_result_reader
            .read_result_from_connection(
                connection,
                project_id,
                operation_id,
                file_backend=file_backend,
            )
        )
    except OperationDetailRepairRequired as exc:
        for issue_code in exc.issue_codes:
            if "workflow" in issue_code:
                add("step_workflow_mismatch")
            elif "fingerprint" in issue_code:
                add("artifact_fingerprint_mismatch")
            elif "missing" in issue_code:
                add("successful_workflow_step_missing")
            elif "artifact" in issue_code:
                add("artifact_contract_invalid")
            else:
                add("artifact_state_invalid")
        return 0
    if ledger_status == "success" and result is None:
        add("successful_workflow_step_missing")
    row = connection.execute(
        """
        SELECT COUNT(*) AS artifact_count
        FROM video_localization_operation_artifacts
        WHERE project_id = ?
          AND operation_id = ?
          AND status = 'committed'
        """,
        (project_id, operation_id),
    ).fetchone()
    return int(row["artifact_count"] or 0)


def _reconcile_whole_recheck_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    try:
        result = (
            asr_whole_recheck_result_reader
            .read_result_from_connection(
                connection,
                project_id,
                operation_id,
                file_backend=file_backend,
            )
        )
    except OperationDetailRepairRequired as exc:
        for issue_code in exc.issue_codes:
            if "workflow" in issue_code:
                add("step_workflow_mismatch")
            elif "fingerprint" in issue_code:
                add("artifact_fingerprint_mismatch")
            elif "missing" in issue_code:
                add("successful_workflow_step_missing")
            elif "artifact" in issue_code:
                add("artifact_contract_invalid")
            else:
                add("artifact_state_invalid")
        return 0
    if ledger_status == "success" and result is None:
        add("successful_workflow_step_missing")
    row = connection.execute(
        """
        SELECT COUNT(*) AS artifact_count
        FROM video_localization_operation_artifacts
        WHERE project_id = ?
          AND operation_id = ?
          AND status = 'committed'
        """,
        (project_id, operation_id),
    ).fetchone()
    return int(row["artifact_count"] or 0)


def _reconcile_transcript_quality_gate_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    try:
        result = (
            asr_transcript_quality_gate_result_reader
            .read_result_from_connection(
                connection,
                project_id,
                operation_id,
                file_backend=file_backend,
            )
        )
    except OperationDetailRepairRequired as exc:
        for issue_code in exc.issue_codes:
            if "workflow" in issue_code:
                add("step_workflow_mismatch")
            elif "fingerprint" in issue_code:
                add("artifact_fingerprint_mismatch")
            elif "missing" in issue_code:
                add("successful_workflow_step_missing")
            elif "artifact" in issue_code:
                add("artifact_contract_invalid")
            else:
                add("artifact_state_invalid")
        return 0
    if ledger_status == "success" and result is None:
        add("successful_workflow_step_missing")
    row = connection.execute(
        """
        SELECT COUNT(*) AS artifact_count
        FROM video_localization_operation_artifacts
        WHERE project_id = ?
          AND operation_id = ?
          AND status = 'committed'
        """,
        (project_id, operation_id),
    ).fetchone()
    return int(row["artifact_count"] or 0)


def _reconcile_local_steps(
    connection: Connection,
    project_id: str,
    operation_id: str,
    workflow_version: str,
    ledger_status: str | None,
    steps: list[step_store.OperationStepAttempt],
    *,
    specs: tuple[_LocalStepAuditSpec, ...],
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
) -> int:
    checked_artifact_count = 0
    spec_by_step_id = {
        item.step_id: item for item in specs
    }
    successful_step_ids: set[str] = set()
    for step in steps:
        step_spec = spec_by_step_id.get(step.step_id)
        if (
            step_spec is None
            or step.workflow_version != workflow_version
        ):
            add("step_workflow_mismatch")
            continue
        if step.status != "success":
            continue
        successful_step_ids.add(step.step_id)
        verified = _read_verified_step_artifact(
            connection,
            project_id,
            operation_id,
            step,
            file_backend=file_backend,
            add=add,
        )
        if verified is None:
            continue
        checked_artifact_count += 1
        if (
            verified.artifact.payload_schema_version
            != step_spec.output_schema_version
            or verified.artifact.media_type != "application/json"
        ):
            add("artifact_contract_invalid")
            continue
        try:
            step_spec.parse_output(verified.content)
        except (TypeError, ValueError):
            add("artifact_contract_invalid")
    if (
        ledger_status == "success"
        and successful_step_ids
        != set(spec_by_step_id)
    ):
        add("successful_workflow_step_missing")
    return checked_artifact_count


def _read_verified_step_artifact(
    connection: Connection,
    project_id: str,
    operation_id: str,
    step: step_store.OperationStepAttempt,
    *,
    file_backend: ManagedArtifactFileBackend,
    add: Callable[[DetailReconciliationIssueCode], None],
):
    try:
        artifact = artifact_store.get_step_artifact_from_connection(
            connection,
            project_id,
            operation_id,
            step.step_attempt_id,
            artifact_kind="step-result",
            artifact_key="primary",
        )
    except (
        artifact_store.ArtifactSchemaError,
        TypeError,
        ValueError,
    ):
        add("artifact_state_invalid")
        return None
    if artifact is None:
        add("successful_step_artifact_missing")
        return None
    if step.output_fingerprint != artifact.content_fingerprint:
        add("artifact_fingerprint_mismatch")
    try:
        return artifact_store.read_artifact_from_connection(
            connection,
            artifact.artifact_id,
            file_backend=file_backend,
        )
    except (
        artifact_store.ArtifactIdentityConflict,
        artifact_store.ArtifactIntegrityError,
        artifact_store.ArtifactPathError,
        artifact_store.ArtifactSchemaError,
        TypeError,
        ValueError,
    ):
        add("artifact_state_invalid")
        return None


def _mirror_workflow_version(
    operation: VideoLocalizationOperation,
) -> str:
    summary = operation.result_summary
    return (
        str(
            summary.get("workflow_schema_version")
            if isinstance(summary, dict)
            else ""
        ).strip()
        or "operation-v1"
    )


def _status_for_issues(
    issues: list[DetailReconciliationIssueCode],
) -> DetailReconciliationStatus:
    issue_set = frozenset(issues)
    if issue_set & _INVALID_ISSUES:
        return "invalid"
    if issue_set & _MISMATCH_ISSUES:
        return "mismatch"
    if issue_set & _MISSING_ISSUES:
        return "missing"
    if issues:
        return "incomplete"
    return "matched"


def _required_identity(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > 128:
        raise ValueError(f"{label} is too long")
    return normalized


__all__ = [
    "DetailReconciliation",
    "DetailReconciliationIssueCode",
    "DetailReconciliationReport",
    "DetailReconciliationStatus",
    "reconcile_operation_detail",
    "reconcile_operation_detail_from_connection",
    "reconcile_operation_details",
]
