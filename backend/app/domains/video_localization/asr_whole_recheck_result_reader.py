"""Same-snapshot reader for managed ASR whole-recheck results."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from app.domains.video_localization import (
    asr_document_understanding_result_reader,
    asr_review_decisions_result_reader,
    asr_section_review_result_reader,
    asr_whole_recheck_managed_contracts as managed_contracts,
    managed_local_detail,
    managed_local_workflow_specs,
    whole_recheck,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.schemas.video_localization_asr_whole_recheck_step import (
    ASR_WHOLE_RECHECK_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
    parse_whole_recheck_call_artifact,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrWholeRecheckDetailParametersV1,
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
class ManagedWholeRecheckAuthority:
    result: whole_recheck.AsrWholeRecheckResult
    final_artifact_fingerprint: str


def read_result_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedWholeRecheckAuthority | None:
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
        != ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
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
        raise OperationDetailRepairRequired("ledger_status_invalid")
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
        != ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
        or not isinstance(
            detail.core.parameters,
            AsrWholeRecheckDetailParametersV1,
        )
    ):
        raise OperationDetailRepairRequired(
            "detail_core_mismatch"
        )
    parameters = detail.core.parameters
    if any(
        step.workflow_version
        != ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
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
            .ASR_WHOLE_RECHECK_PREPARE_STEP_SPEC
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
            .ASR_WHOLE_RECHECK_FINALIZE_STEP_SPEC
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
        or prepared.output.review_decisions_artifact_fingerprint
        != parameters.review_decisions_artifact_fingerprint
        or prepared.output
        .document_understanding_artifact_fingerprint
        != parameters.document_understanding_artifact_fingerprint
        or prepared.output.profile_configuration_fingerprint
        != parameters.profile_configuration_fingerprint
        or prepared.output.behavior_fingerprint
        != parameters.behavior_fingerprint
        or prepared.output.request.upstream_operation_id
        != parameters.input_review_decisions_operation_id
        or prepared.output.request.understanding_operation_id
        != parameters.input_document_understanding_operation_id
        or prepared.output.request.profile_id != parameters.profile_id
    ):
        raise OperationDetailRepairRequired(
            "prepared_input_identity_mismatch"
        )
    decisions = (
        asr_review_decisions_result_reader.read_result_from_connection(
            connection,
            project_id,
            parameters.input_review_decisions_operation_id,
            file_backend=file_backend,
        )
    )
    understanding = (
        asr_document_understanding_result_reader
        .read_success_authority_from_connection(
            connection,
            project_id,
            parameters.input_document_understanding_operation_id,
            file_backend=file_backend,
        )
    )
    if (
        decisions is None
        or understanding is None
        or decisions.final_artifact_fingerprint
        != parameters.review_decisions_artifact_fingerprint
        or understanding.final_artifact_fingerprint
        != parameters.document_understanding_artifact_fingerprint
    ):
        raise OperationDetailRepairRequired(
            "upstream_artifact_mismatch"
        )
    section = (
        asr_section_review_result_reader.read_result_from_connection(
            connection,
            project_id,
            decisions.result.input.upstream_operation_id,
            file_backend=file_backend,
        )
    )
    if (
        section is None
        or section.result.input.understanding_operation_id
        != parameters.input_document_understanding_operation_id
    ):
        raise OperationDetailRepairRequired(
            "upstream_lineage_mismatch"
        )
    try:
        rebuilt = whole_recheck.build_whole_recheck_input(
            decisions.result,
            understanding.result,
            upstream_operation_id=(
                parameters.input_review_decisions_operation_id
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
    artifacts = []
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
            artifact = parse_whole_recheck_call_artifact(
                content
            )
        except (TypeError, ValueError) as exc:
            raise OperationDetailRepairRequired(
                "call_artifact_invalid"
            ) from exc
        if (
            artifact.call_input_fingerprint
            != reference.input_fingerprint
            or artifact.attempt != reference.attempt
            or artifact.round_index != reference.round_index
            or artifact.decision_ids != reference.decision_ids
        ):
            raise OperationDetailRepairRequired(
                "call_artifact_identity_mismatch"
            )
        artifacts.append(artifact)
    try:
        managed_contracts.validate_final_against_input(
            prepared.output,
            finalized.output,
        )
    except ValueError as exc:
        raise OperationDetailRepairRequired(
            "final_result_invalid"
        ) from exc
    ordered_artifacts = sorted(
        artifacts,
        key=lambda item: item.attempt,
    )
    expected_calls = [
        item.llm_call for item in ordered_artifacts
    ]
    if (
        finalized.output.result.llm_calls != expected_calls
        or (
            expected_calls
            and finalized.output.result.duration_ms
            != sum(item.duration_ms for item in expected_calls)
        )
        or finalized.output.result.model_id
        != (
            expected_calls[0].model_id
            if expected_calls
            else None
        )
    ):
        raise OperationDetailRepairRequired(
            "call_observability_mismatch"
        )
    if not finalized.step.output_fingerprint:
        raise OperationDetailRepairRequired(
            "artifact_fingerprint_mismatch"
        )
    return ManagedWholeRecheckAuthority(
        result=finalized.output.result,
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
                    or candidate.error_code == reference.error_code
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
        if step.step_id.startswith("whole_recheck_call_")
        and step.status in {"success", "failed"}
    }
    unresolved_provider_steps = [
        step
        for step in steps
        if step.step_id.startswith("whole_recheck_call_")
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
        or artifact.content_fingerprint != expected_fingerprint
        or artifact.payload_schema_version
        != ASR_WHOLE_RECHECK_CALL_ARTIFACT_SCHEMA_VERSION
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
        "prepare_whole_recheck_input",
        "finalize_whole_recheck",
    } or step_id.startswith("whole_recheck_call_")


__all__ = [
    "ManagedWholeRecheckAuthority",
    "read_result_from_connection",
]
