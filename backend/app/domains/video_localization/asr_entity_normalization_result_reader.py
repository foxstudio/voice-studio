"""Same-snapshot reader for managed entity-normalization results."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from app.domains.video_localization import (
    asr_entity_normalization_managed_contracts as managed_contracts,
    asr_research_evidence_result_reader,
    entity_normalization,
    managed_local_detail,
    managed_local_workflow_specs,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.schemas.video_localization_asr_entity_normalization_step import (
    ASR_ENTITY_NORMALIZATION_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
    parse_entity_normalization_call_artifact,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrEntityNormalizationDetailParametersV1,
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
class ManagedEntityNormalizationSuccess:
    result: entity_normalization.AsrEntityNormalizationResult
    final_artifact_fingerprint: str


def read_success_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedEntityNormalizationSuccess | None:
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
        != ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
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
        != ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
        or not isinstance(
            detail.core.parameters,
            AsrEntityNormalizationDetailParametersV1,
        )
    ):
        raise OperationDetailRepairRequired(
            "detail_core_mismatch"
        )
    parameters = detail.core.parameters
    if any(
        step.workflow_version
        != ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
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
            .ASR_ENTITY_NORMALIZATION_PREPARE_STEP_SPEC
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
            .ASR_ENTITY_NORMALIZATION_FINALIZE_STEP_SPEC
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
        or prepared.output.research_artifact_fingerprint
        != parameters.research_artifact_fingerprint
        or prepared.output.glossary_fingerprint
        != parameters.glossary_fingerprint
        or prepared.output.behavior_fingerprint
        != parameters.behavior_fingerprint
        or prepared.output.profile_configuration_fingerprint
        != parameters.profile_configuration_fingerprint
        or prepared.output.request.upstream_operation_id
        != parameters.input_research_evidence_operation_id
        or tuple(prepared.output.request.glossary)
        != parameters.glossary
        or prepared.output.request.profile_id
        != parameters.profile_id
    ):
        raise OperationDetailRepairRequired(
            "prepared_input_identity_mismatch"
        )
    upstream = (
        asr_research_evidence_result_reader
        .read_success_from_connection(
            connection,
            project_id,
            parameters.input_research_evidence_operation_id,
            file_backend=file_backend,
        )
    )
    if (
        upstream is None
        or upstream.final_artifact_fingerprint
        != parameters.research_artifact_fingerprint
    ):
        raise OperationDetailRepairRequired(
            "upstream_artifact_mismatch"
        )
    rebuilt = (
        entity_normalization.AsrEntityNormalizationInput
        .from_research_evidence(
            upstream.result,
            upstream_operation_id=(
                parameters.input_research_evidence_operation_id
            ),
            glossary=list(parameters.glossary),
        )
        .model_copy(update={"profile_id": parameters.profile_id})
    )
    if rebuilt != prepared.output.request:
        raise OperationDetailRepairRequired(
            "prepared_input_rebuild_mismatch"
        )
    call_artifacts = []
    call_steps = _referenced_steps(
        steps,
        finalized.output.calls,
    )
    for reference, step in call_steps:
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
            artifact = parse_entity_normalization_call_artifact(
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
            or artifact.purpose != reference.purpose
            or artifact.attempt != reference.attempt
            or artifact.candidate_ids
            != reference.candidate_ids
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
    result = entity_normalization.AsrEntityNormalizationResult(
        input=prepared.output.request,
        status=finalized.output.status,
        profile_id=finalized.output.profile_id,
        model_id=finalized.output.model_id,
        prompt_version=finalized.output.prompt_version,
        resolutions=list(finalized.output.resolutions),
        updated_segments=list(
            finalized.output.updated_segments
        ),
        changes=list(finalized.output.changes),
        warnings=list(finalized.output.warnings),
        llm_calls=[
            item.llm_call for item in call_artifacts
        ],
        duration_ms=finalized.output.duration_ms,
    )
    return ManagedEntityNormalizationSuccess(
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
                and candidate.output_fingerprint
                == reference.artifact_fingerprint
                and candidate.status == "success"
            ),
            None,
        )
        if step is None or step.step_attempt_id in used:
            raise OperationDetailRepairRequired(
                "step_manifest_mismatch"
            )
        used.add(step.step_attempt_id)
        matched.append((reference, step))
    successful = {
        step.step_attempt_id
        for step in steps
        if step.step_id.startswith("entity_call_")
        and step.status == "success"
    }
    if successful != used:
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
        != ASR_ENTITY_NORMALIZATION_CALL_ARTIFACT_SCHEMA_VERSION
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
        "prepare_entity_normalization_input",
        "finalize_entity_normalization",
    } or step_id.startswith("entity_call_")


__all__ = [
    "ManagedEntityNormalizationSuccess",
    "read_success_from_connection",
]
