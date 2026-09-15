"""Same-snapshot reader for managed visual-evidence results."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from app.domains.video_localization import (
    asr_visual_evidence_managed_contracts as managed_contracts,
    managed_local_detail,
    managed_local_workflow_specs,
    visual_evidence,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.schemas.video_localization_asr_visual_evidence_step import (
    ASR_VISUAL_EVIDENCE_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    parse_visual_call_artifact,
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
class ManagedVisualEvidenceSuccess:
    result: visual_evidence.AsrVisualEvidenceResult
    final_artifact_fingerprint: str
    frames: tuple[
        managed_contracts.AsrVisualEvidenceFrameReferenceV1,
        ...,
    ]


def read_success_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedVisualEvidenceSuccess | None:
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
        or str(ledger["workflow_version"])
        != ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
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
        raise OperationDetailRepairRequired(
            "step_state_invalid"
        ) from exc
    if any(
        step.workflow_version
        != ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
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
            .ASR_VISUAL_EVIDENCE_PREPARE_STEP_SPEC
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
            .ASR_VISUAL_EVIDENCE_FINALIZE_STEP_SPEC
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
    if (
        finalized.output.prepared_input_fingerprint
        != managed_contracts.prepared_input_fingerprint(
            prepared.output
        )
    ):
        raise OperationDetailRepairRequired(
            "prepared_input_fingerprint_mismatch"
        )
    extraction_frames: list[
        managed_contracts.AsrVisualEvidenceFrameReferenceV1
    ] = []
    extraction_steps = _referenced_steps(
        steps,
        finalized.output.extractions,
        prefix="extract_visual_",
    )
    for reference, step in extraction_steps:
        primary = _read_primary(
            connection,
            project_id,
            operation_id,
            step.step_attempt_id,
            expected_fingerprint=(
                reference.artifact_fingerprint
            ),
            expected_schema=(
                managed_contracts
                .ASR_VISUAL_EVIDENCE_EXTRACTION_OUTPUT_SCHEMA_VERSION
            ),
            file_backend=file_backend,
        )
        try:
            output = managed_contracts.parse_extraction_output(
                primary.content
            )
        except (TypeError, ValueError) as exc:
            raise OperationDetailRepairRequired(
                "extraction_artifact_invalid"
            ) from exc
        if (
            output.question_id != reference.question_id
            or output.round_index != reference.round_index
        ):
            raise OperationDetailRepairRequired(
                "extraction_artifact_identity_mismatch"
            )
        for frame in output.frames:
            try:
                verified = artifact_store.read_artifact_from_connection(
                    connection,
                    frame.artifact_id,
                    file_backend=file_backend,
                )
            except (
                artifact_store.ArtifactIdentityConflict,
                artifact_store.ArtifactIntegrityError,
                artifact_store.ArtifactPathError,
                artifact_store.ArtifactSchemaError,
            ) as exc:
                raise OperationDetailRepairRequired(
                    "frame_artifact_invalid"
                ) from exc
            artifact = verified.artifact
            if (
                artifact.project_id != project_id
                or artifact.operation_id != operation_id
                or artifact.step_attempt_id
                != step.step_attempt_id
                or artifact.artifact_kind != "visual-frame"
                or artifact.artifact_key != frame.frame_id
                or artifact.payload_schema_version
                != "asr-visual-frame-v1"
                or artifact.media_type != "image/jpeg"
                or artifact.content_fingerprint
                != frame.artifact_fingerprint
                or artifact.size_bytes != frame.size_bytes
            ):
                raise OperationDetailRepairRequired(
                    "frame_artifact_contract_mismatch"
                )
            extraction_frames.append(frame)
    if tuple(extraction_frames) != finalized.output.frames:
        raise OperationDetailRepairRequired(
            "frame_manifest_mismatch"
        )
    call_artifacts = []
    call_steps = _referenced_steps(
        steps,
        finalized.output.calls,
        prefix="visual_call_",
    )
    for reference, step in call_steps:
        primary = _read_primary(
            connection,
            project_id,
            operation_id,
            step.step_attempt_id,
            expected_fingerprint=(
                reference.artifact_fingerprint
            ),
            expected_schema=(
                ASR_VISUAL_EVIDENCE_CALL_ARTIFACT_SCHEMA_VERSION
            ),
            file_backend=file_backend,
        )
        try:
            parsed = parse_visual_call_artifact(
                primary.content
            )
        except (TypeError, ValueError) as exc:
            raise OperationDetailRepairRequired(
                "call_artifact_invalid"
            ) from exc
        if (
            parsed.call_input_fingerprint
            != reference.input_fingerprint
            or parsed.call_id != reference.call_id
            or parsed.question_id != reference.question_id
            or parsed.round_index != reference.round_index
        ):
            raise OperationDetailRepairRequired(
                "call_artifact_identity_mismatch"
            )
        call_artifacts.append(parsed)
    frames = [
        visual_evidence.AsrVisualEvidenceFrame(
            frame_id=item.frame_id,
            question_id=item.question_id,
            frame_index=item.frame_index,
            round_index=item.round_index,
            timestamp_ms=item.timestamp_ms,
            file_name=f"{item.frame_id}.jpg",
            sha256=item.sha256,
            size_bytes=item.size_bytes,
        )
        for item in finalized.output.frames
    ]
    if not finalized.step.output_fingerprint:
        raise OperationDetailRepairRequired(
            "artifact_fingerprint_mismatch"
        )
    return ManagedVisualEvidenceSuccess(
        result=visual_evidence.AsrVisualEvidenceResult(
            input=prepared.output.request,
            status=finalized.output.status,
            stop_reason=finalized.output.stop_reason,
            profile_id=finalized.output.profile_id,
            model_id=finalized.output.model_id,
            frames=frames,
            observations=list(
                finalized.output.observations
            ),
            llm_calls=[
                item.llm_call for item in call_artifacts
            ],
            warnings=list(finalized.output.warnings),
            stage_timing={
                "duration_ms": finalized.output.duration_ms
            },
            quality_summary=(
                finalized.output.quality_summary
            ),
        ),
        final_artifact_fingerprint=(
            finalized.step.output_fingerprint
        ),
        frames=finalized.output.frames,
    )


def _referenced_steps(
    steps: tuple[step_store.OperationStepAttempt, ...],
    references,
    *,
    prefix: str,
):
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
        if step.step_id.startswith(prefix)
        and step.status == "success"
    }
    if successful != used:
        raise OperationDetailRepairRequired(
            "step_manifest_incomplete"
        )
    return matched


def _read_primary(
    connection: Connection,
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_fingerprint: str,
    expected_schema: str,
    file_backend: ManagedArtifactFileBackend,
):
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
        or artifact.content_fingerprint
        != expected_fingerprint
        or artifact.payload_schema_version
        != expected_schema
        or artifact.media_type != "application/json"
    ):
        raise OperationDetailRepairRequired(
            "artifact_contract_mismatch"
        )
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
    ) as exc:
        raise OperationDetailRepairRequired(
            "artifact_invalid"
        ) from exc


def _allowed_step_id(step_id: str) -> bool:
    return (
        step_id
        in {
            "prepare_visual_input",
            "finalize_visual_evidence",
        }
        or step_id.startswith("extract_visual_")
        or step_id.startswith("visual_call_")
    )


__all__ = [
    "ManagedVisualEvidenceSuccess",
    "read_success_from_connection",
]
