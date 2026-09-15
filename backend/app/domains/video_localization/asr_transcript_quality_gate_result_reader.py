"""Same-snapshot reader for managed transcript-quality-gate results."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from app.domains.video_localization import (
    asr_transcript_quality_gate_managed_contracts as managed_contracts,
    asr_whole_recheck_result_reader,
    managed_local_detail,
    managed_local_workflow_specs,
    transcript_quality_gate,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.schemas.video_localization_asr_transcript_quality_gate_step import (
    ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrTranscriptQualityGateDetailParametersV1,
)
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


@dataclass(frozen=True)
class ManagedTranscriptQualityGateAuthority:
    result: transcript_quality_gate.AsrTranscriptQualityGateResult
    final_artifact_fingerprint: str


def read_result_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedTranscriptQualityGateAuthority | None:
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
        != ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
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
        != ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
        or not isinstance(
            detail.core.parameters,
            AsrTranscriptQualityGateDetailParametersV1,
        )
    ):
        raise OperationDetailRepairRequired(
            "detail_core_mismatch"
        )
    parameters = detail.core.parameters
    allowed_step_ids = {
        "prepare_transcript_quality_gate_input",
        "finalize_transcript_quality_gate",
    }
    if any(
        step.workflow_version
        != ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
        or step.step_id not in allowed_step_ids
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
            .ASR_TRANSCRIPT_QUALITY_GATE_PREPARE_STEP_SPEC
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
            .ASR_TRANSCRIPT_QUALITY_GATE_FINALIZE_STEP_SPEC
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
        or prepared.output.whole_recheck_artifact_fingerprint
        != parameters.whole_recheck_artifact_fingerprint
        or prepared.output.behavior_fingerprint
        != parameters.behavior_fingerprint
        or prepared.output.request.upstream_operation_id
        != parameters.input_whole_recheck_operation_id
    ):
        raise OperationDetailRepairRequired(
            "prepared_input_identity_mismatch"
        )
    whole_authority = (
        asr_whole_recheck_result_reader.read_result_from_connection(
            connection,
            project_id,
            parameters.input_whole_recheck_operation_id,
            file_backend=file_backend,
        )
    )
    if (
        whole_authority is None
        or whole_authority.final_artifact_fingerprint
        != parameters.whole_recheck_artifact_fingerprint
    ):
        raise OperationDetailRepairRequired(
            "upstream_artifact_mismatch"
        )
    whole_result = whole_authority.result
    try:
        rebuilt = transcript_quality_gate.build_input(
            whole_result,
            upstream_operation_id=(
                parameters.input_whole_recheck_operation_id
            ),
            source_track_id=whole_result.input.source_track_id,
            source_audio_sha256=(
                whole_result.input.source_audio_sha256
            ),
            segments=whole_result.input.segments,
        )
    except ValueError as exc:
        raise OperationDetailRepairRequired(
            "prepared_input_rebuild_failed"
        ) from exc
    if rebuilt != prepared.output.request:
        raise OperationDetailRepairRequired(
            "prepared_input_rebuild_mismatch"
        )
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
    return ManagedTranscriptQualityGateAuthority(
        result=finalized.output.result,
        final_artifact_fingerprint=(
            finalized.step.output_fingerprint
        ),
    )


__all__ = [
    "ManagedTranscriptQualityGateAuthority",
    "read_result_from_connection",
]
