"""Shared authoritative read path for one-step local-free workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any, Generic, TypeVar

from app.domains.video_localization import (
    managed_local_step,
    operation_elapsed,
    operation_state,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


OutputT = TypeVar("OutputT")
_ACTIVE_STATUSES = frozenset({"queued", "running"})
_TERMINAL_STATUSES = frozenset(
    {"success", "failed", "cancelled"}
)


@dataclass(frozen=True)
class ManagedLocalResult(Generic[OutputT]):
    step: step_store.OperationStepAttempt
    output: OutputT


@dataclass(frozen=True)
class ManagedLocalDetailState(Generic[OutputT]):
    project_id: str
    operation_id: str
    status: str
    cancel_requested: bool
    created_at: str
    started_at: str | None
    completed_at: str | None
    parameters: dict[str, Any]
    steps: tuple[step_store.OperationStepAttempt, ...]
    result: ManagedLocalResult[OutputT] | None
    error_code: str | None

    @property
    def progress(self) -> float:
        if self.status in _TERMINAL_STATUSES:
            return 1.0
        return 0.0 if self.status == "queued" else 0.5

    @property
    def public_step_status(self) -> str:
        if self.result is not None:
            return "success"
        latest = latest_step(self.steps, lambda _step: True)
        if latest is not None:
            return normalized_step_status(latest.status)
        if self.status == "queued":
            return "todo"
        if self.status == "cancelled":
            return "cancelled"
        if self.status == "failed":
            return "failed"
        return "running"

    @property
    def task_duration_ms(self) -> int | None:
        terminal_step = (
            self.result.step
            if self.result is not None
            else latest_step(
                self.steps,
                lambda step: (
                    step.completed_at is not None
                ),
            )
        )
        if (
            terminal_step is not None
            and terminal_step.completed_at is not None
        ):
            step_duration = duration_ms(
                terminal_step.prepared_at,
                terminal_step.completed_at,
            )
            if step_duration is not None:
                return step_duration
        return duration_ms(
            self.started_at,
            self.completed_at,
        )


def read_zero_input_detail_state(
    connection: Connection,
    ledger,
    *,
    spec: managed_local_step.ManagedLocalStepSpec[OutputT],
    file_backend: ManagedArtifactFileBackend,
) -> ManagedLocalDetailState[OutputT]:
    """Validate a zero-parameter workflow without reading Project JSON."""

    return read_detail_state(
        connection,
        ledger,
        spec=spec,
        parameters={},
        file_backend=file_backend,
    )


def read_detail_state(
    connection: Connection,
    ledger,
    *,
    spec: managed_local_step.ManagedLocalStepSpec[OutputT],
    parameters: dict[str, Any],
    file_backend: ManagedArtifactFileBackend,
) -> ManagedLocalDetailState[OutputT]:
    """Validate typed parameters, ledger, step and artifact authority."""

    project_id = str(ledger["project_id"])
    operation_id = str(ledger["operation_id"])
    if str(ledger["kind"]) != spec.kind:
        raise OperationDetailRepairRequired(
            "ledger_identity_invalid"
        )
    status = str(ledger["status"])
    if status not in _ACTIVE_STATUSES | _TERMINAL_STATUSES:
        raise OperationDetailRepairRequired(
            "ledger_status_invalid"
        )
    public_parameters = dict(parameters)
    public_parameters["scope"] = operation_state.operation_scope(
        spec.kind,
        public_parameters,
    )
    if (
        ledger_store.parameters_fingerprint(public_parameters)
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
    if any(
        step.workflow_version != spec.workflow_version
        or step.step_id != spec.step_id
        for step in steps
    ):
        raise OperationDetailRepairRequired(
            "step_workflow_mismatch"
        )
    result = read_step_result(
        connection,
        project_id,
        operation_id,
        steps,
        spec=spec,
        file_backend=file_backend,
    )
    if status == "success" and result is None:
        raise OperationDetailRepairRequired(
            "successful_workflow_step_missing"
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
    started_at = (
        str(attempts[0]["started_at"]) if attempts else None
    )
    completed_at = (
        str(ledger["completed_at"])
        if ledger["completed_at"] is not None
        else None
    )
    return ManagedLocalDetailState(
        project_id=project_id,
        operation_id=operation_id,
        status=status,
        cancel_requested=bool(ledger["cancel_requested"]),
        created_at=str(ledger["created_at"]),
        started_at=started_at,
        completed_at=completed_at,
        parameters=public_parameters,
        steps=steps,
        result=result,
        error_code=_error_code(status, steps, attempts),
    )


def read_step_result(
    connection: Connection,
    project_id: str,
    operation_id: str,
    steps: tuple[step_store.OperationStepAttempt, ...],
    *,
    spec: managed_local_step.ManagedLocalStepSpec[OutputT],
    file_backend: ManagedArtifactFileBackend,
) -> ManagedLocalResult[OutputT] | None:
    step = latest_step(
        steps,
        lambda item: (
            item.step_id == spec.step_id
            and item.status == "success"
        ),
    )
    if step is None:
        return None
    artifact = artifact_store.get_step_artifact_from_connection(
        connection,
        project_id,
        operation_id,
        step.step_attempt_id,
        artifact_kind=managed_local_step.ARTIFACT_KIND,
        artifact_key=managed_local_step.ARTIFACT_KEY,
    )
    if artifact is None:
        raise OperationDetailRepairRequired(
            "successful_step_artifact_missing"
        )
    if step.output_fingerprint != artifact.content_fingerprint:
        raise OperationDetailRepairRequired(
            "artifact_fingerprint_mismatch"
        )
    try:
        verified = artifact_store.read_artifact_from_connection(
            connection,
            artifact.artifact_id,
            file_backend=file_backend,
        )
        parsed = spec.parse_output(verified.content)
    except (
        artifact_store.ArtifactIdentityConflict,
        artifact_store.ArtifactIntegrityError,
        artifact_store.ArtifactPathError,
        artifact_store.ArtifactSchemaError,
        TypeError,
        ValueError,
    ) as exc:
        raise OperationDetailRepairRequired(
            "artifact_invalid"
        ) from exc
    if (
        verified.artifact.payload_schema_version
        != spec.output_schema_version
        or verified.artifact.media_type != "application/json"
    ):
        raise OperationDetailRepairRequired(
            "artifact_contract_invalid"
        )
    return ManagedLocalResult(step=step, output=parsed)


def _error_code(
    status: str,
    steps: tuple[step_store.OperationStepAttempt, ...],
    attempts,
) -> str | None:
    if status != "failed":
        return None
    latest_failed = latest_step(
        steps,
        lambda step: step.status
        in {"failed", "result_unknown"},
    )
    if latest_failed is not None and latest_failed.error_code:
        return latest_failed.error_code
    for attempt in reversed(attempts):
        value = str(attempt["error_code"] or "").strip()
        if value:
            return value
    return "VIDEO_LOCALIZATION_OPERATION_FAILED"


def latest_step(
    steps: tuple[step_store.OperationStepAttempt, ...],
    predicate: Callable[[step_store.OperationStepAttempt], bool],
) -> step_store.OperationStepAttempt | None:
    candidates = [step for step in steps if predicate(step)]
    return (
        max(
            candidates,
            key=lambda step: (
                step.prepared_at,
                step.step_attempt_number,
                step.step_attempt_id,
            ),
        )
        if candidates
        else None
    )


def normalized_step_status(status: str) -> str:
    if status == "success":
        return "success"
    if status in {"failed", "result_unknown"}:
        return "failed"
    if status == "cancelled":
        return "cancelled"
    return "running"


def duration_ms(
    started_at: str | None,
    completed_at: str | None,
) -> int | None:
    return operation_elapsed.duration_ms(started_at, completed_at)


def workflow_duration_ms(
    steps: tuple[step_store.OperationStepAttempt, ...] | list,
    *,
    started_at: str | None,
    completed_at: str | None,
) -> int | None:
    """Prefer the canonical step clock over legacy ledger timestamps."""

    step_span = operation_elapsed.span_duration_ms(
        (step.prepared_at for step in steps),
        (step.completed_at for step in steps),
    )
    if step_span is not None:
        return step_span
    return duration_ms(started_at, completed_at)


def apply_workflow_duration(
    summary: dict[str, Any],
    steps: tuple[step_store.OperationStepAttempt, ...] | list,
    *,
    started_at: str | None,
    completed_at: str | None,
) -> int | None:
    """Project one wall-clock duration into the operation and its single dev stage."""

    wall_ms = workflow_duration_ms(
        steps,
        started_at=started_at,
        completed_at=completed_at,
    )
    if wall_ms is None:
        return None
    summary["task_duration_ms"] = wall_ms
    stage_id = summary.get("stage_id")
    timings = summary.get("task_stage_timings")
    if isinstance(stage_id, str) and isinstance(timings, dict):
        stage_timing = timings.get(stage_id)
        if isinstance(stage_timing, dict):
            stage_timing["duration_ms"] = wall_ms
    return wall_ms


__all__ = [
    "ManagedLocalDetailState",
    "ManagedLocalResult",
    "apply_workflow_duration",
    "duration_ms",
    "latest_step",
    "normalized_step_status",
    "read_step_result",
    "read_detail_state",
    "read_zero_input_detail_state",
    "workflow_duration_ms",
]
