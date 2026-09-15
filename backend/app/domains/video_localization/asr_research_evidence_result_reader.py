"""Same-snapshot reader for managed research-evidence results."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from app.domains.video_localization import (
    asr_research_evidence_managed_contracts as managed_contracts,
    managed_local_detail,
    managed_local_workflow_specs,
    research_evidence,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.schemas.video_localization_asr_research_evidence_step import (
    ASR_RESEARCH_CALL_ARTIFACT_SCHEMA_VERSION,
    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RESEARCH_SEARCH_ARTIFACT_SCHEMA_VERSION,
    parse_research_call_artifact,
    parse_research_search_artifact,
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
class ManagedResearchEvidenceSuccess:
    result: research_evidence.AsrResearchEvidenceResult
    final_artifact_fingerprint: str


def read_success_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedResearchEvidenceSuccess | None:
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
        != ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
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
        != ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
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
            .ASR_RESEARCH_EVIDENCE_PREPARE_STEP_SPEC
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
            .ASR_RESEARCH_EVIDENCE_FINALIZE_STEP_SPEC
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
    ):
        raise OperationDetailRepairRequired(
            "prepared_input_fingerprint_mismatch"
        )
    search_artifacts = []
    search_steps = _referenced_steps(
        steps,
        finalized.output.searches,
        prefix="research_search_",
    )
    for reference, step in search_steps:
        content = _read_primary(
            connection,
            project_id,
            operation_id,
            step.step_attempt_id,
            expected_fingerprint=(
                reference.artifact_fingerprint
            ),
            expected_schema=(
                ASR_RESEARCH_SEARCH_ARTIFACT_SCHEMA_VERSION
            ),
            file_backend=file_backend,
        )
        try:
            artifact = parse_research_search_artifact(content)
        except (TypeError, ValueError) as exc:
            raise OperationDetailRepairRequired(
                "search_artifact_invalid"
            ) from exc
        if (
            artifact.search_input_fingerprint
            != reference.input_fingerprint
            or artifact.request_id != reference.request_id
            or artifact.round_index != reference.round_index
            or artifact.candidate_id != reference.candidate_id
            or artifact.search_kind != reference.search_kind
            or artifact.attempt != reference.attempt
        ):
            raise OperationDetailRepairRequired(
                "search_artifact_identity_mismatch"
            )
        search_artifacts.append(artifact)
    call_artifacts = []
    call_steps = _referenced_steps(
        steps,
        finalized.output.calls,
        prefix="research_call_",
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
            expected_schema=(
                ASR_RESEARCH_CALL_ARTIFACT_SCHEMA_VERSION
            ),
            file_backend=file_backend,
        )
        try:
            artifact = parse_research_call_artifact(content)
        except (TypeError, ValueError) as exc:
            raise OperationDetailRepairRequired(
                "call_artifact_invalid"
            ) from exc
        if (
            artifact.call_input_fingerprint
            != reference.input_fingerprint
            or artifact.call_id != reference.call_id
            or artifact.purpose != reference.purpose
            or artifact.round_index != reference.round_index
            or artifact.candidate_ids != reference.candidate_ids
        ):
            raise OperationDetailRepairRequired(
                "call_artifact_identity_mismatch"
            )
        call_artifacts.append(artifact)
    candidate_ids = {
        item.candidate_id
        for item in prepared.output.request.candidates
    }
    if (
        set(finalized.output.supported_candidate_ids)
        | set(finalized.output.unresolved_candidate_ids)
        != candidate_ids
        or len(finalized.output.query_runs)
        > prepared.output.request.policy.max_total_queries
        or len(finalized.output.rounds)
        > prepared.output.request.policy.max_rounds
    ):
        raise OperationDetailRepairRequired(
            "final_candidate_manifest_invalid"
        )
    available_evidence = {
        (
            artifact.candidate_id,
            artifact.provider,
            item.url,
            item.retrieved_at,
        )
        for artifact in search_artifacts
        for item in artifact.results
    }
    if any(
        (
            item.candidate_id,
            item.provider,
            item.url,
            item.retrieved_at,
        )
        not in available_evidence
        for item in finalized.output.evidence
    ):
        raise OperationDetailRepairRequired(
            "evidence_search_manifest_mismatch"
        )
    if not finalized.step.output_fingerprint:
        raise OperationDetailRepairRequired(
            "artifact_fingerprint_mismatch"
        )
    result = research_evidence.AsrResearchEvidenceResult(
        contract_version=(
            "asr-research-evidence-v2"
            if prepared.output.request.contract_version
            == "asr-research-evidence-input-v2"
            else "asr-research-evidence-v1"
        ),
        input=prepared.output.request,
        status=finalized.output.status,
        stop_reason=finalized.output.stop_reason,
        profile_id=finalized.output.profile_id,
        model_id=finalized.output.model_id,
        prompt_version=finalized.output.prompt_version,
        query_runs=list(finalized.output.query_runs),
        evidence=list(finalized.output.evidence),
        rounds=list(finalized.output.rounds),
        supported_candidate_ids=list(
            finalized.output.supported_candidate_ids
        ),
        unresolved_candidate_ids=list(
            finalized.output.unresolved_candidate_ids
        ),
        llm_calls=[
            item.llm_call for item in call_artifacts
        ],
        warnings=list(finalized.output.warnings),
        stage_timing=finalized.output.stage_timing,
        quality_summary=finalized.output.quality_summary,
    )
    return ManagedResearchEvidenceSuccess(
        result=result,
        final_artifact_fingerprint=(
            finalized.step.output_fingerprint
        ),
    )


def _referenced_steps(steps, references, *, prefix: str):
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
    connection,
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_fingerprint: str,
    expected_schema: str,
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
        or artifact.payload_schema_version != expected_schema
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
        "prepare_research_input",
        "finalize_research_evidence",
    } or step_id.startswith(
        ("research_search_", "research_call_")
    )


__all__ = [
    "ManagedResearchEvidenceSuccess",
    "read_success_from_connection",
]
