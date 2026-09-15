"""Same-snapshot reader for managed document-understanding results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from sqlite3 import Connection

from app.domains.video_localization import (
    asr_document_understanding_managed_contracts as managed_contracts,
    document_understanding_contracts,
    managed_local_detail,
    managed_local_workflow_specs,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.schemas.video_localization_asr_document_understanding_step import (
    ASR_DOCUMENT_UNDERSTANDING_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
    parse_asr_document_understanding_call_artifact,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


@dataclass(frozen=True)
class ManagedDocumentUnderstandingSuccess:
    """Validated result plus the final artifact identity for downstream locks."""

    result: document_understanding_contracts.AsrDocumentUnderstandingResult
    final_artifact_fingerprint: str


def read_result_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> document_understanding_contracts.AsrDocumentUnderstandingResult | None:
    """Validate all authorities without opening Project JSON."""

    ledger = connection.execute(
        """
        SELECT
            project_id,
            operation_id,
            kind,
            status,
            workflow_version
        FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
        """,
        (project_id, operation_id),
    ).fetchone()
    if ledger is None:
        return None
    if (
        str(ledger["kind"]) != "english_asr"
        or str(ledger["workflow_version"]) != ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
    ):
        raise OperationDetailRepairRequired("ledger_identity_invalid")
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
        steps = tuple(
            step_store.list_step_attempts_from_connection(
                connection,
                project_id,
                operation_id,
            )
        )
    except (
        step_store.StepSchemaError,
        TypeError,
        ValueError,
    ) as exc:
        raise OperationDetailRepairRequired("step_state_invalid") from exc
    if any(
        step.workflow_version != ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
        or not _allowed_step_id(step.step_id)
        for step in steps
    ):
        raise OperationDetailRepairRequired("step_workflow_mismatch")
    prepared = managed_local_detail.read_step_result(
        connection,
        project_id,
        operation_id,
        steps,
        spec=(managed_local_workflow_specs.ASR_DOCUMENT_UNDERSTANDING_PREPARE_STEP_SPEC),
        file_backend=file_backend,
    )
    finalized = managed_local_detail.read_step_result(
        connection,
        project_id,
        operation_id,
        steps,
        spec=(managed_local_workflow_specs.ASR_DOCUMENT_UNDERSTANDING_FINALIZE_STEP_SPEC),
        file_backend=file_backend,
    )
    if status == "success" and (prepared is None or finalized is None):
        raise OperationDetailRepairRequired("successful_workflow_step_missing")
    if prepared is None or finalized is None:
        return None
    prepared_fingerprint = managed_contracts.prepared_input_fingerprint(prepared.output)
    if finalized.output.prepared_input_fingerprint != prepared_fingerprint:
        raise OperationDetailRepairRequired("prepared_input_fingerprint_mismatch")
    call_artifacts = []
    referenced_steps: set[str] = set()
    for reference in finalized.output.calls:
        step = next(
            (
                candidate
                for candidate in steps
                if candidate.step_id == reference.step_id
                and candidate.input_fingerprint == reference.input_fingerprint
                and candidate.output_fingerprint == reference.artifact_fingerprint
                and candidate.status == "success"
            ),
            None,
        )
        if step is None or step.step_attempt_id in referenced_steps:
            raise OperationDetailRepairRequired("call_manifest_step_mismatch")
        referenced_steps.add(step.step_attempt_id)
        artifact = artifact_store.get_step_artifact_from_connection(
                connection,
                project_id,
                operation_id,
                step.step_attempt_id,
                artifact_kind="step-result",
                artifact_key="primary",
            )
        if (
            artifact is None
            or artifact.content_fingerprint != reference.artifact_fingerprint
            or artifact.payload_schema_version != ASR_DOCUMENT_UNDERSTANDING_CALL_ARTIFACT_SCHEMA_VERSION
            or artifact.media_type != "application/json"
        ):
            raise OperationDetailRepairRequired("call_artifact_contract_mismatch")
        try:
            verified = artifact_store.read_artifact_from_connection(
                    connection,
                    artifact.artifact_id,
                    file_backend=file_backend,
                )
            parsed = parse_asr_document_understanding_call_artifact(verified.content)
        except (
            artifact_store.ArtifactIdentityConflict,
            artifact_store.ArtifactIntegrityError,
            artifact_store.ArtifactPathError,
            artifact_store.ArtifactSchemaError,
            TypeError,
            ValueError,
        ) as exc:
            raise OperationDetailRepairRequired("call_artifact_invalid") from exc
        if (
            parsed.call_input_fingerprint != reference.input_fingerprint
            or parsed.call_id != reference.call_id
            or parsed.attempt != reference.attempt
        ):
            raise OperationDetailRepairRequired("call_artifact_identity_mismatch")
        call_artifacts.append(parsed)
    successful_provider_steps = {
        step.step_attempt_id for step in steps if step.step_id.startswith("document_call_") and step.status == "success"
    }
    if successful_provider_steps != referenced_steps:
        raise OperationDetailRepairRequired("call_manifest_incomplete")
    return document_understanding_contracts.AsrDocumentUnderstandingResult(
        input=prepared.output.request,
        brief=finalized.output.brief,
        profile_id=finalized.output.profile_id,
        model_id=finalized.output.model_id,
        prompt_version=finalized.output.prompt_version,
        execution_strategy=(finalized.output.execution_strategy),
        window_count=finalized.output.window_count,
        llm_call_count=finalized.output.llm_call_count,
        retry_count=finalized.output.retry_count,
        stage_timing={"duration_ms": finalized.output.duration_ms},
        quality_summary=finalized.output.quality_summary,
        llm_calls=[artifact.llm_call for artifact in call_artifacts],
        warnings=list(finalized.output.warnings),
        raw_responses=[
            {
                "stage": _stage(artifact.call_id),
                "attempt": artifact.attempt,
                "response_json": json.dumps(
                    artifact.response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
            for artifact in call_artifacts
        ],
    )


def read_success_authority_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedDocumentUnderstandingSuccess | None:
    """Return the managed result and exact final artifact fingerprint."""

    result = read_result_from_connection(
        connection,
        project_id,
        operation_id,
        file_backend=file_backend,
    )
    if result is None:
        return None
    steps = tuple(
        step_store.list_step_attempts_from_connection(
            connection,
            project_id,
            operation_id,
        )
    )
    final_step = managed_local_detail.latest_step(
        steps,
        lambda item: item.step_id == "finalize_document_understanding" and item.status == "success",
        )
    if final_step is None or final_step.output_fingerprint is None:
        raise OperationDetailRepairRequired("successful_workflow_step_missing")
    return ManagedDocumentUnderstandingSuccess(
        result=result,
        final_artifact_fingerprint=(final_step.output_fingerprint),
    )


def _allowed_step_id(step_id: str) -> bool:
    return step_id in {
        "prepare_document_input",
        "finalize_document_understanding",
    } or step_id.startswith("document_call_")


def _stage(call_id: str) -> str:
    prefix = "understand_document:"
    return call_id[len(prefix) :] if call_id.startswith(prefix) else call_id


__all__ = [
    "ManagedDocumentUnderstandingSuccess",
    "read_result_from_connection",
    "read_success_authority_from_connection",
]
