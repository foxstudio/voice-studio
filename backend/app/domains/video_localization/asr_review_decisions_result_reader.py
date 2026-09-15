"""Same-snapshot reader for managed ASR review-decisions results."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from app.domains.video_localization import (
    asr_review_decisions_managed_contracts as managed_contracts,
    asr_section_review_result_reader,
    managed_local_detail,
    managed_local_workflow_specs,
    review_decisions,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.schemas.video_localization_asr_review_decisions_step import (
    ASR_REVIEW_DECISIONS_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
    parse_review_decisions_call_artifact,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrReviewDecisionsDetailParametersV1,
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
class ManagedReviewDecisionsAuthority:
    result: review_decisions.AsrReviewDecisionsResult
    final_artifact_fingerprint: str


def read_result_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedReviewDecisionsAuthority | None:
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
        or str(ledger["workflow_version"]) != ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
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
        raise OperationDetailRepairRequired("step_or_detail_state_invalid") from exc
    if (
        detail is None
        or detail.core.kind != "english_asr"
        or detail.core.workflow_version != ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
        or not isinstance(
            detail.core.parameters,
            AsrReviewDecisionsDetailParametersV1,
        )
    ):
        raise OperationDetailRepairRequired("detail_core_mismatch")
    parameters = detail.core.parameters
    if any(
        step.workflow_version != ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION or not _allowed_step_id(step.step_id)
        for step in steps
    ):
        raise OperationDetailRepairRequired("step_workflow_mismatch")
    prepared = managed_local_detail.read_step_result(
        connection,
        project_id,
        operation_id,
        steps,
        spec=(managed_local_workflow_specs.ASR_REVIEW_DECISIONS_PREPARE_STEP_SPEC),
        file_backend=file_backend,
    )
    finalized = managed_local_detail.read_step_result(
        connection,
        project_id,
        operation_id,
        steps,
        spec=(managed_local_workflow_specs.ASR_REVIEW_DECISIONS_FINALIZE_STEP_SPEC),
        file_backend=file_backend,
    )
    if status == "success" and (prepared is None or finalized is None):
        raise OperationDetailRepairRequired("successful_workflow_step_missing")
    if prepared is None or finalized is None:
        return None
    prepared_fingerprint = managed_contracts.prepared_input_fingerprint(prepared.output)
    if (
        finalized.output.prepared_input_fingerprint != prepared_fingerprint
        or prepared.output.section_review_artifact_fingerprint != parameters.section_review_artifact_fingerprint
        or prepared.output.profile_configuration_fingerprint != parameters.profile_configuration_fingerprint
        or prepared.output.behavior_fingerprint != parameters.behavior_fingerprint
        or prepared.output.request.upstream_operation_id != parameters.input_section_review_operation_id
        or prepared.output.request.profile_id != parameters.profile_id
    ):
        raise OperationDetailRepairRequired("prepared_input_identity_mismatch")
    section = asr_section_review_result_reader.read_result_from_connection(
        connection,
        project_id,
        parameters.input_section_review_operation_id,
        file_backend=file_backend,
    )
    if section is None or section.final_artifact_fingerprint != parameters.section_review_artifact_fingerprint:
        raise OperationDetailRepairRequired("upstream_artifact_mismatch")
    try:
        rebuilt = review_decisions.build_review_decisions_input(
            section.result,
            upstream_operation_id=(parameters.input_section_review_operation_id),
            profile_id=parameters.profile_id,
        )
    except ValueError as exc:
        raise OperationDetailRepairRequired("prepared_input_rebuild_failed") from exc
    persisted_request = prepared.output.request
    if rebuilt.model_copy(update={"acoustic_candidates": []}) != persisted_request.model_copy(
        update={"acoustic_candidates": []}
    ) or not _runtime_acoustic_candidates_match_upstream(persisted_request):
        raise OperationDetailRepairRequired("prepared_input_rebuild_mismatch")
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
            expected_fingerprint=(reference.artifact_fingerprint),
            file_backend=file_backend,
        )
        try:
            artifact = parse_review_decisions_call_artifact(content)
        except (TypeError, ValueError) as exc:
            raise OperationDetailRepairRequired("call_artifact_invalid") from exc
        if (
            artifact.call_input_fingerprint != reference.input_fingerprint
            or artifact.call_group != reference.call_group
            or artifact.attempt != reference.attempt
            or artifact.round_index != reference.round_index
            or artifact.issue_ids != reference.issue_ids
            or artifact.primary_artifact_fingerprint != reference.primary_artifact_fingerprint
        ):
            raise OperationDetailRepairRequired("call_artifact_identity_mismatch")
        artifacts.append(artifact)
    try:
        managed_contracts.validate_final_against_input(
            prepared.output,
            finalized.output,
        )
    except ValueError as exc:
        raise OperationDetailRepairRequired("final_result_invalid") from exc
    successful_references = [item for item in finalized.output.attempts if item.status == "success"]
    if {item.llm_call.call_id for item in artifacts} != {
        (f"review-decisions-r{item.round_index}-{item.call_group}-a{item.attempt:02d}")
        for item in successful_references
    }:
        raise OperationDetailRepairRequired("call_manifest_mismatch")
    group_order = {"primary": 0, "coverage": 1}
    ordered_artifacts = sorted(
        artifacts,
        key=lambda item: (
            group_order[item.call_group],
            item.attempt,
        ),
    )
    expected_calls = [item.llm_call for item in ordered_artifacts]
    if (
        finalized.output.result.llm_calls != expected_calls
        or finalized.output.result.duration_ms != sum(item.duration_ms for item in expected_calls)
        or finalized.output.result.model_id != (expected_calls[0].model_id if expected_calls else None)
    ):
        raise OperationDetailRepairRequired("call_observability_mismatch")
    if not finalized.step.output_fingerprint:
        raise OperationDetailRepairRequired("artifact_fingerprint_mismatch")
    return ManagedReviewDecisionsAuthority(
        result=finalized.output.result,
        final_artifact_fingerprint=(finalized.step.output_fingerprint),
    )


def _runtime_acoustic_candidates_match_upstream(
    request: review_decisions.AsrReviewDecisionsInput,
) -> bool:
    """Bind persisted relisten evidence without repeating ASR on reads."""

    if len(request.acoustic_candidates) > 12:
        return False
    issues = {item.issue_id: item for item in request.issues}
    segments = {item.segment_id: item for item in request.segments}
    for candidate in request.acoustic_candidates:
        if not candidate.issue_ids:
            return False
        target_segments = []
        for issue_id in candidate.issue_ids:
            issue = issues.get(issue_id)
            if issue is None or issue.section_id != candidate.section_id:
                return False
            target_ids = issue.target_segment_ids or [issue.segment_id]
            for segment_id in target_ids:
                segment = segments.get(segment_id)
                if segment is None:
                    return False
                target_segments.append(segment)
        if not target_segments:
            return False
        target_start_ms = min(item.start_ms for item in target_segments)
        target_end_ms = max(item.end_ms for item in target_segments)
        if candidate.end_ms <= target_start_ms or candidate.start_ms >= target_end_ms:
            return False
    return True


def _referenced_steps(steps, references):
    matched = []
    used: set[str] = set()
    for reference in references:
        step = next(
            (
                candidate
                for candidate in steps
                if candidate.step_id == reference.step_id
                and candidate.input_fingerprint == reference.input_fingerprint
                and candidate.status == reference.status
                and (reference.status == "failed" or candidate.output_fingerprint == reference.artifact_fingerprint)
                and (reference.status == "success" or candidate.error_code == reference.error_code)
            ),
            None,
        )
        if step is None or step.step_attempt_id in used:
            raise OperationDetailRepairRequired("step_manifest_mismatch")
        used.add(step.step_attempt_id)
        matched.append((reference, step))
    known_provider_steps = {
        step.step_attempt_id
        for step in steps
        if step.step_id.startswith("decision_call_") and step.status in {"success", "failed"}
    }
    unresolved_provider_steps = [
        step for step in steps if step.step_id.startswith("decision_call_") and step.status not in {"success", "failed"}
    ]
    if known_provider_steps != used or unresolved_provider_steps:
        raise OperationDetailRepairRequired("step_manifest_incomplete")
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
        or artifact.payload_schema_version != ASR_REVIEW_DECISIONS_CALL_ARTIFACT_SCHEMA_VERSION
        or artifact.media_type != "application/json"
    ):
        raise OperationDetailRepairRequired("artifact_contract_mismatch")
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
        raise OperationDetailRepairRequired("artifact_invalid") from exc
    return verified.content


def _allowed_step_id(step_id: str) -> bool:
    return step_id in {
        "prepare_review_decisions_input",
        "finalize_review_decisions",
    } or step_id.startswith("decision_call_")


__all__ = [
    "ManagedReviewDecisionsAuthority",
    "read_result_from_connection",
]
