"""Same-snapshot reader for managed section-review results."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from app.domains.video_localization import (
    asr_document_understanding_result_reader,
    asr_entity_normalization_result_reader,
    asr_research_evidence_result_reader,
    asr_section_review_managed_contracts as managed_contracts,
    managed_local_detail,
    managed_local_workflow_specs,
    section_review,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.schemas.video_localization_asr_section_review_step import (
    ASR_SECTION_REVIEW_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
    parse_section_review_call_artifact,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrSectionReviewDetailParametersV1,
)
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


@dataclass(frozen=True)
class ManagedSectionReviewAuthority:
    result: section_review.AsrSectionReviewResult
    final_artifact_fingerprint: str


def read_result_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedSectionReviewAuthority | None:
    ledger = connection.execute(
        """
        SELECT project_id, operation_id, kind, status, workflow_version
        FROM video_localization_operations
        WHERE project_id = ? AND operation_id = ?
        """,
        (project_id, operation_id),
    ).fetchone()
    if ledger is None:
        return None
    if (
        str(ledger["kind"]) != "english_asr"
        or str(ledger["workflow_version"])
        != ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
    ):
        raise OperationDetailRepairRequired(
            "ledger_identity_invalid"
        )
    status = str(ledger["status"])
    if status not in {
        "queued",
        "running",
        "success",
        "failed",
        "cancelled",
    }:
        raise OperationDetailRepairRequired(
            "ledger_status_invalid"
        )
    try:
        detail = detail_store.get_detail_core_from_connection(
            connection,
            project_id,
            operation_id,
        )
        steps = tuple(
            step_store.list_step_attempts_from_connection(
                connection,
                project_id,
                operation_id,
            )
        )
    except (
        detail_store.OperationDetailCoreIdentityConflict,
        detail_store.OperationDetailCoreIntegrityError,
        detail_store.OperationDetailCoreSchemaError,
        step_store.StepSchemaError,
        TypeError,
        ValueError,
    ) as exc:
        raise OperationDetailRepairRequired(
            "step_or_detail_state_invalid"
        ) from exc
    if (
        detail is None
        or detail.core.kind != "english_asr"
        or detail.core.workflow_version
        != ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
        or not isinstance(
            detail.core.parameters,
            AsrSectionReviewDetailParametersV1,
        )
    ):
        raise OperationDetailRepairRequired(
            "detail_core_mismatch"
        )
    parameters = detail.core.parameters
    if any(
        step.workflow_version
        != ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
        or not _allowed_step_id(step.step_id)
        for step in steps
    ):
        raise OperationDetailRepairRequired(
            "step_workflow_mismatch"
        )
    prepared = managed_local_detail.read_step_result(
        connection,
        project_id,
        operation_id,
        steps,
        spec=(
            managed_local_workflow_specs
            .ASR_SECTION_REVIEW_PREPARE_STEP_SPEC
        ),
        file_backend=file_backend,
    )
    finalized = managed_local_detail.read_step_result(
        connection,
        project_id,
        operation_id,
        steps,
        spec=(
            managed_local_workflow_specs
            .ASR_SECTION_REVIEW_FINALIZE_STEP_SPEC
        ),
        file_backend=file_backend,
    )
    if status == "success" and (
        prepared is None or finalized is None
    ):
        raise OperationDetailRepairRequired(
            "successful_workflow_step_missing"
        )
    if prepared is None or finalized is None:
        return None
    prepared_fingerprint = (
        managed_contracts.prepared_input_fingerprint(
            prepared.output
        )
    )
    if (
        finalized.output.prepared_input_fingerprint
        != prepared_fingerprint
        or prepared.output.entity_artifact_fingerprint
        != parameters.entity_artifact_fingerprint
        or prepared.output.document_artifact_fingerprint
        != parameters.document_artifact_fingerprint
        or prepared.output.profile_configuration_fingerprint
        != parameters.profile_configuration_fingerprint
        or prepared.output.behavior_fingerprint
        != parameters.behavior_fingerprint
        or prepared.output.request.upstream_operation_id
        != parameters.input_entity_normalization_operation_id
        or prepared.output.request.understanding_operation_id
        != parameters.input_document_understanding_operation_id
        or prepared.output.request.profile_id
        != parameters.profile_id
    ):
        raise OperationDetailRepairRequired(
            "prepared_input_identity_mismatch"
        )
    entity = (
        asr_entity_normalization_result_reader
        .read_success_from_connection(
            connection,
            project_id,
            parameters.input_entity_normalization_operation_id,
            file_backend=file_backend,
        )
    )
    document = (
        asr_document_understanding_result_reader
        .read_success_authority_from_connection(
            connection,
            project_id,
            parameters.input_document_understanding_operation_id,
            file_backend=file_backend,
        )
    )
    if (
        entity is None
        or document is None
        or entity.final_artifact_fingerprint
        != parameters.entity_artifact_fingerprint
        or document.final_artifact_fingerprint
        != parameters.document_artifact_fingerprint
    ):
        raise OperationDetailRepairRequired(
            "upstream_artifact_mismatch"
        )
    research = (
        asr_research_evidence_result_reader
        .read_success_from_connection(
            connection,
            project_id,
            entity.result.input.upstream_operation_id,
            file_backend=file_backend,
        )
    )
    if (
        research is None
        or research.result.input.upstream_operation_id
        != parameters.input_document_understanding_operation_id
    ):
        raise OperationDetailRepairRequired(
            "upstream_chain_mismatch"
        )
    try:
        rebuilt = section_review.build_section_review_input(
            entity.result,
            document.result,
            normalization_operation_id=(
                parameters.input_entity_normalization_operation_id
            ),
            understanding_operation_id=(
                parameters.input_document_understanding_operation_id
            ),
            profile_id=parameters.profile_id,
        )
    except ValueError as exc:
        raise OperationDetailRepairRequired(
            "prepared_input_rebuild_failed"
        ) from exc
    if rebuilt != prepared.output.request:
        raise OperationDetailRepairRequired(
            "prepared_input_rebuild_mismatch"
        )
    call_artifacts = []
    referenced = _referenced_steps(
        steps,
        finalized.output.attempts,
    )
    for reference, step in referenced:
        if reference.status == "failed":
            continue
        assert reference.artifact_fingerprint is not None
        content = _read_primary(
            connection,
            project_id,
            operation_id,
            step.step_attempt_id,
            expected_fingerprint=(
                reference.artifact_fingerprint
            ),
            file_backend=file_backend,
        )
        try:
            artifact = parse_section_review_call_artifact(
                content
            )
        except (TypeError, ValueError) as exc:
            raise OperationDetailRepairRequired(
                "call_artifact_invalid"
            ) from exc
        if (
            artifact.call_input_fingerprint
            != reference.input_fingerprint
            or artifact.call_id != reference.call_id
            or artifact.section_id != reference.section_id
            or artifact.section_start_ordinal
            != reference.section_start_ordinal
            or artifact.core_segment_ids
            != reference.core_segment_ids
            or artifact.attempt != reference.attempt
        ):
            raise OperationDetailRepairRequired(
                "call_artifact_identity_mismatch"
            )
        call_artifacts.append(artifact)
    try:
        managed_contracts.validate_final_against_input(
            prepared.output,
            finalized.output,
        )
    except ValueError as exc:
        raise OperationDetailRepairRequired(
            "final_result_invalid"
        ) from exc
    if not finalized.step.output_fingerprint:
        raise OperationDetailRepairRequired(
            "artifact_fingerprint_mismatch"
        )
    result = section_review.AsrSectionReviewResult(
        input=prepared.output.request,
        status=finalized.output.status,
        profile_id=finalized.output.profile_id,
        model_id=finalized.output.model_id,
        prompt_version=finalized.output.prompt_version,
        section_runs=list(finalized.output.section_runs),
        issues=list(finalized.output.issues),
        warnings=list(finalized.output.warnings),
        llm_calls=[
            item.llm_call for item in call_artifacts
        ],
        duration_ms=finalized.output.duration_ms,
        quality_summary=finalized.output.quality_summary,
    )
    return ManagedSectionReviewAuthority(
        result=result,
        final_artifact_fingerprint=(
            finalized.step.output_fingerprint
        ),
    )


def _referenced_steps(steps, references):
    matched = []
    used: set[str] = set()
    for reference in references:
        step = next(
            (
                candidate
                for candidate in steps
                if candidate.step_id == reference.step_id
                and candidate.input_fingerprint
                == reference.input_fingerprint
                and candidate.status == reference.status
                and (
                    reference.status == "failed"
                    or candidate.output_fingerprint
                    == reference.artifact_fingerprint
                )
                and (
                    reference.status == "success"
                    or candidate.error_code
                    == reference.error_code
                )
            ),
            None,
        )
        if step is None or step.step_attempt_id in used:
            raise OperationDetailRepairRequired(
                "step_manifest_mismatch"
            )
        used.add(step.step_attempt_id)
        matched.append((reference, step))
    known_provider_steps = {
        step.step_attempt_id
        for step in steps
        if step.step_id.startswith("section_call_")
        and step.status in {"success", "failed"}
    }
    unresolved_provider_steps = [
        step
        for step in steps
        if step.step_id.startswith("section_call_")
        and step.status not in {"success", "failed"}
    ]
    if known_provider_steps != used or unresolved_provider_steps:
        raise OperationDetailRepairRequired(
            "step_manifest_incomplete"
        )
    return matched


def _read_primary(
    connection,
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_fingerprint: str,
    file_backend: ManagedArtifactFileBackend,
) -> bytes:
    artifact = artifact_store.get_step_artifact_from_connection(
        connection,
        project_id,
        operation_id,
        step_attempt_id,
        artifact_kind="step-result",
        artifact_key="primary",
    )
    if (
        artifact is None
        or artifact.status != "committed"
        or artifact.content_fingerprint
        != expected_fingerprint
        or artifact.payload_schema_version
        != ASR_SECTION_REVIEW_CALL_ARTIFACT_SCHEMA_VERSION
        or artifact.media_type != "application/json"
    ):
        raise OperationDetailRepairRequired(
            "artifact_contract_mismatch"
        )
    try:
        verified = artifact_store.read_artifact_from_connection(
            connection,
            artifact.artifact_id,
            file_backend=file_backend,
        )
    except (
        artifact_store.ArtifactIdentityConflict,
        artifact_store.ArtifactIntegrityError,
        artifact_store.ArtifactPathError,
        artifact_store.ArtifactSchemaError,
    ) as exc:
        raise OperationDetailRepairRequired(
            "artifact_invalid"
        ) from exc
    return verified.content


def _allowed_step_id(step_id: str) -> bool:
    return step_id in {
        "prepare_section_review_input",
        "finalize_section_review",
    } or step_id.startswith("section_call_")


__all__ = [
    "ManagedSectionReviewAuthority",
    "read_result_from_connection",
]
