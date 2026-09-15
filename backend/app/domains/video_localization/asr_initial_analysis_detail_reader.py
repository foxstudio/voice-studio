"""Authoritative reader for the managed initial-analysis workflow."""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any

from app.domains.video_localization import (
    asr_initial_analysis_execution,
    asr_initial_analysis_operation_projection,
    asr_pipeline,
    managed_local_detail,
    managed_local_workflow_specs,
    operation_state,
    workflow_contracts,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.schemas.video_localization_asr_initial_analysis_step import (
    ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
    AsrInitialAnalysisDiarizationOutcomeV1,
    AsrInitialAnalysisJoinOutputV1,
)
from app.schemas.video_localization_asr_raw_step import (
    AsrRawStepOutputV1,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrInitialAnalysisDetailParametersV1,
)
from app.services import database
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


_ACTIVE_STATUSES = frozenset({"queued", "running"})
_TERMINAL_STATUSES = frozenset(
    {"success", "failed", "cancelled"}
)


@dataclass(frozen=True)
class _InitialAnalysisState:
    project_id: str
    operation_id: str
    status: str
    cancel_requested: bool
    created_at: str
    started_at: str | None
    completed_at: str | None
    parameters: dict[str, Any]
    steps: tuple[step_store.OperationStepAttempt, ...]
    snapshot: asr_pipeline.AsrInitialAnalysisSnapshot | None
    error_code: str | None

    @property
    def progress(self) -> float:
        if self.status in _TERMINAL_STATUSES:
            return 1.0
        if self.status == "queued":
            return 0.0
        completed = len(
            {
                step.step_id
                for step in self.steps
                if step.status == "success"
            }
        )
        return min(0.9, 0.15 + completed * 0.25)

    @property
    def duration_ms(self) -> int | None:
        if self.status in _TERMINAL_STATUSES:
            completed_steps = [
                step
                for step in self.steps
                if step.completed_at is not None
            ]
            if completed_steps:
                step_duration = managed_local_detail.duration_ms(
                    min(step.prepared_at for step in completed_steps),
                    max(
                        str(step.completed_at)
                        for step in completed_steps
                    ),
                )
                if step_duration is not None:
                    return step_duration
        return managed_local_detail.duration_ms(
            self.started_at,
            self.completed_at,
        )

    def step_duration_ms(self, step_id: str) -> int:
        step = managed_local_detail.latest_step(
            self.steps,
            lambda item: (
                item.step_id == step_id
                and item.completed_at is not None
            ),
        )
        if step is None or step.completed_at is None:
            return 0
        return (
            managed_local_detail.duration_ms(
                step.prepared_at,
                step.completed_at,
            )
            or 0
        )


@dataclass(frozen=True)
class ManagedInitialAnalysisSuccess:
    """Verified upstream input and exact artifact lineage."""

    snapshot: asr_pipeline.AsrInitialAnalysisSnapshot
    raw_artifact_fingerprint: str
    diarization_artifact_fingerprint: str
    join_artifact_fingerprint: str


def assemble_initial_analysis_detail(
    connection: Connection,
    ledger,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> VideoLocalizationOperation:
    state = _read_state(
        connection,
        ledger,
        file_backend=file_backend,
    )
    return VideoLocalizationOperation(
        operation_id=state.operation_id,
        project_id=state.project_id,
        kind="english_asr",
        status=state.status,
        label=(
            workflow_contracts
            .ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_DEFINITION
            .label
        ),
        progress=state.progress,
        error_code=state.error_code,
        error_message=_error_message(
            state.status,
            state.error_code,
        ),
        cancel_requested=state.cancel_requested,
        result_summary=_result_summary(state),
        parameters=state.parameters,
        created_at=state.created_at,
        started_at=state.started_at,
        completed_at=state.completed_at,
    )


def read_initial_analysis_result(
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> asr_pipeline.AsrInitialAnalysisSnapshot | None:
    with database.read_conn() as connection:
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
            (project_id, operation_id),
        ).fetchone()
        if ledger is None:
            return None
        return _read_state(
            connection,
            ledger,
            file_backend=file_backend,
        ).snapshot


def read_initial_analysis_success_from_connection(
    connection: Connection,
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> ManagedInitialAnalysisSuccess | None:
    """Read one successful upstream workflow in the caller's snapshot."""

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
        (project_id, operation_id),
    ).fetchone()
    if ledger is None:
        return None
    state = _read_state(
        connection,
        ledger,
        file_backend=file_backend,
    )
    if state.status != "success" or state.snapshot is None:
        raise OperationDetailRepairRequired(
            "upstream_not_successful"
        )
    fingerprints: dict[str, str] = {}
    for step_id in (
        "asr",
        "diarization",
        "initial_analysis_join",
    ):
        step = managed_local_detail.latest_step(
            state.steps,
            lambda item, expected=step_id: (
                item.step_id == expected
                and item.status == "success"
            ),
        )
        if step is None or not step.output_fingerprint:
            raise OperationDetailRepairRequired(
                "upstream_artifact_fingerprint_missing"
            )
        fingerprints[step_id] = step.output_fingerprint
    return ManagedInitialAnalysisSuccess(
        snapshot=state.snapshot,
        raw_artifact_fingerprint=fingerprints["asr"],
        diarization_artifact_fingerprint=(
            fingerprints["diarization"]
        ),
        join_artifact_fingerprint=(
            fingerprints["initial_analysis_join"]
        ),
    )


def _read_state(
    connection: Connection,
    ledger,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> _InitialAnalysisState:
    project_id = str(ledger["project_id"])
    operation_id = str(ledger["operation_id"])
    if (
        str(ledger["kind"]) != "english_asr"
        or str(ledger["workflow_version"])
        != ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
    ):
        raise OperationDetailRepairRequired(
            "ledger_identity_invalid"
        )
    status = str(ledger["status"])
    if status not in _ACTIVE_STATUSES | _TERMINAL_STATUSES:
        raise OperationDetailRepairRequired(
            "ledger_status_invalid"
        )
    try:
        detail_record = detail_store.get_detail_core_from_connection(
            connection,
            project_id,
            operation_id,
        )
    except (
        detail_store.OperationDetailCoreIdentityConflict,
        detail_store.OperationDetailCoreIntegrityError,
        detail_store.OperationDetailCoreSchemaError,
        TypeError,
        ValueError,
    ) as exc:
        raise OperationDetailRepairRequired(
            "detail_core_invalid"
        ) from exc
    if detail_record is None:
        raise OperationDetailRepairRequired(
            "detail_core_missing"
        )
    core = detail_record.core
    if (
        core.kind != "english_asr"
        or core.workflow_version
        != ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
        or not isinstance(
            core.parameters,
            AsrInitialAnalysisDetailParametersV1,
        )
    ):
        raise OperationDetailRepairRequired(
            "detail_core_mismatch"
        )
    parameters = {
        **core.parameters.model_dump(
            mode="json",
            exclude={"parameters_schema_version"},
            exclude_none=True,
        ),
        "execution_mode": "stop_after",
        "stop_after_step": "initial_analysis",
    }
    public_parameters = {
        **parameters,
        "scope": operation_state.operation_scope(
            "english_asr",
            parameters,
        ),
    }
    if (
        ledger_store.parameters_fingerprint(
            public_parameters
        )
        != str(ledger["parameters_fingerprint"])
    ):
        raise OperationDetailRepairRequired(
            "detail_parameters_mismatch"
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
    specs = {
        spec.step_id: spec
        for spec in (
            managed_local_workflow_specs
            .ASR_INITIAL_ANALYSIS_RAW_STEP_SPEC,
            managed_local_workflow_specs
            .ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_SPEC,
            managed_local_workflow_specs
            .ASR_INITIAL_ANALYSIS_JOIN_STEP_SPEC,
        )
    }
    if any(
        step.workflow_version
        != ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
        or step.step_id not in specs
        for step in steps
    ):
        raise OperationDetailRepairRequired(
            "step_workflow_mismatch"
        )
    raw_result = managed_local_detail.read_step_result(
        connection,
        project_id,
        operation_id,
        steps,
        spec=specs["asr"],
        file_backend=file_backend,
    )
    diarization_result = (
        managed_local_detail.read_step_result(
            connection,
            project_id,
            operation_id,
            steps,
            spec=specs["diarization"],
            file_backend=file_backend,
        )
    )
    join_result = managed_local_detail.read_step_result(
        connection,
        project_id,
        operation_id,
        steps,
        spec=specs["initial_analysis_join"],
        file_backend=file_backend,
    )
    if status == "success" and (
        raw_result is None
        or diarization_result is None
        or join_result is None
    ):
        raise OperationDetailRepairRequired(
            "successful_workflow_step_missing"
        )
    snapshot = None
    if (
        raw_result is not None
        and diarization_result is not None
        and join_result is not None
    ):
        snapshot = _reconstruct_snapshot(
            project_id,
            operation_id,
            raw_result.output,
            diarization_result.output,
            join_result.output,
            raw_fingerprint=(
                raw_result.step.output_fingerprint
            ),
            diarization_fingerprint=(
                diarization_result.step.output_fingerprint
            ),
        )
    attempts = connection.execute(
        """
        SELECT
            attempt_number,
            status,
            started_at,
            completed_at,
            error_code
        FROM video_localization_operation_attempts
        WHERE project_id = ?
          AND operation_id = ?
        ORDER BY attempt_number, attempt_id
        """,
        (project_id, operation_id),
    ).fetchall()
    return _InitialAnalysisState(
        project_id=project_id,
        operation_id=operation_id,
        status=status,
        cancel_requested=bool(ledger["cancel_requested"]),
        created_at=str(ledger["created_at"]),
        started_at=(
            str(attempts[0]["started_at"])
            if attempts
            else None
        ),
        completed_at=(
            str(ledger["completed_at"])
            if ledger["completed_at"] is not None
            else None
        ),
        parameters=public_parameters,
        steps=steps,
        snapshot=snapshot,
        error_code=_error_code(
            status,
            steps,
            attempts,
        ),
    )


def _reconstruct_snapshot(
    project_id: str,
    operation_id: str,
    raw_output: AsrRawStepOutputV1,
    diarization_outcome: (
        AsrInitialAnalysisDiarizationOutcomeV1
    ),
    join_output: AsrInitialAnalysisJoinOutputV1,
    *,
    raw_fingerprint: str | None,
    diarization_fingerprint: str | None,
) -> asr_pipeline.AsrInitialAnalysisSnapshot:
    if (
        not raw_fingerprint
        or not diarization_fingerprint
        or join_output.raw_artifact_fingerprint
        != raw_fingerprint
        or join_output.diarization_artifact_fingerprint
        != diarization_fingerprint
        or join_output.audio_sha256
        != raw_output.input.audio_sha256
        or join_output.source_track_id
        != raw_output.input.source_track_id
        or diarization_outcome.input.audio_sha256
        != raw_output.input.audio_sha256
        or diarization_outcome.input.source_track_id
        != raw_output.input.source_track_id
    ):
        raise OperationDetailRepairRequired(
            "artifact_fingerprint_mismatch"
        )
    synthetic_audio_path = (
        f"managed-artifact://{project_id}/{operation_id}"
    )
    raw = (
        asr_initial_analysis_execution
        .raw_domain_result_from_managed(
            raw_output.result,
            audio_path=synthetic_audio_path,
        )
    )
    if diarization_outcome.status == "success":
        if diarization_outcome.result is None:
            raise OperationDetailRepairRequired(
                "artifact_contract_invalid"
            )
        diarization = (
            asr_initial_analysis_execution
            .diarization_domain_result_from_managed(
                diarization_outcome.result,
                audio_path=synthetic_audio_path,
            )
        )
        diarization_error = None
    else:
        diarization = None
        diarization_error = (
            diarization_outcome.error_code
        )
    analysis = asr_pipeline.AsrInitialAnalysisResult(
        raw_asr=raw,
        diarization=diarization,
        diarization_error=diarization_error,
    )
    snapshot = (
        asr_pipeline.DEFAULT_ASR_PIPELINE
        .snapshot_initial_analysis(analysis)
    )
    if (
        tuple(snapshot.joined_transcript.segments)
        != join_output.segments
        or tuple(
            str(value).strip().upper()
            for value in snapshot.joined_transcript.warnings
        )
        != join_output.warning_codes
    ):
        raise OperationDetailRepairRequired(
            "artifact_contract_invalid"
        )
    return snapshot


def _result_summary(
    state: _InitialAnalysisState,
) -> dict[str, Any]:
    if state.status == "success" and state.snapshot is not None:
        return (
            asr_initial_analysis_operation_projection
            .success_summary(
                state.snapshot,
                seed=state.operation_id,
                wall_duration_ms=state.duration_ms or 0,
                join_duration_ms=state.step_duration_ms(
                    "initial_analysis_join"
                ),
            )
        )
    summary = (
        asr_initial_analysis_operation_projection
        .initial_summary()
    )
    summary["stage"] = {
        "queued": "准备初始语音分析",
        "failed": "初始语音分析失败",
        "cancelled": "初始语音分析已取消",
    }.get(
        state.status,
        "正在并行生成原始听写并区分说话人",
    )
    latest_by_step = {
        step_id: managed_local_detail.latest_step(
            state.steps,
            lambda step, expected=step_id: (
                step.step_id == expected
            ),
        )
        for step_id in summary["task_step_results"]
    }
    parallel_branches_succeeded = all(
        latest_by_step[step_id] is not None
        and latest_by_step[step_id].status == "success"
        for step_id in ("asr", "diarization")
    )
    for step_id, step_result in (
        summary["task_step_results"].items()
    ):
        latest = latest_by_step[step_id]
        if (
            latest is not None
            and latest.status
            in {"success", "failed", "result_unknown", "cancelled"}
        ):
            public_status = (
                managed_local_detail.normalized_step_status(
                    latest.status
                )
            )
        elif state.status == "queued":
            public_status = "todo"
        elif state.status == "cancelled":
            public_status = "cancelled"
        elif state.status == "failed":
            public_status = "failed"
        elif (
            step_id == "initial_analysis_join"
            and not parallel_branches_succeeded
        ):
            public_status = "todo"
        else:
            public_status = "running"
        step_result["status"] = public_status
    if state.duration_ms is not None:
        summary["task_duration_ms"] = state.duration_ms
    if state.status == "failed":
        summary["error_detail"] = {
            "status": "failed",
            "summary": _error_message(
                state.status,
                state.error_code,
            ),
            "metrics": (
                [
                    {
                        "label": "错误代码",
                        "value": state.error_code,
                    }
                ]
                if state.error_code
                else []
            ),
            "sections": [],
            "notes": ["请确认输入音轨仍然可用后重试。"],
        }
    return summary


def _error_code(status: str, steps, attempts) -> str | None:
    if status != "failed":
        return None
    failed = managed_local_detail.latest_step(
        steps,
        lambda step: step.status
        in {"failed", "result_unknown"},
    )
    if failed is not None and failed.error_code:
        return failed.error_code
    for attempt in reversed(attempts):
        value = str(attempt["error_code"] or "").strip()
        if value:
            return value
    return "VIDEO_LOCALIZATION_OPERATION_FAILED"


def _error_message(
    status: str,
    error_code: str | None,
) -> str | None:
    if status == "cancelled":
        return "任务已取消。"
    if status != "failed":
        return None
    return (
        "初始语音分析未能完整生成并保存，"
        "请检查输入音轨和本地引擎后重试。"
    )


__all__ = [
    "ManagedInitialAnalysisSuccess",
    "assemble_initial_analysis_detail",
    "read_initial_analysis_success_from_connection",
    "read_initial_analysis_result",
]
