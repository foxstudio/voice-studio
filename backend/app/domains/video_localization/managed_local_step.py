"""Shared fenced execution primitive for deterministic local workflow steps."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, Literal, TypeVar

from app.domains.video_localization import (
    managed_artifact_files,
)
from app.errors import AppException
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
)


OutputT = TypeVar("OutputT")

ARTIFACT_KIND = "step-result"
ARTIFACT_KEY = "primary"


@dataclass(frozen=True)
class ManagedLocalStepSpec(Generic[OutputT]):
    """Stable identity and codec for one local-free atomic step."""

    kind: str
    workflow_version: str
    step_id: str
    output_schema_version: str
    error_namespace: str
    label: str
    serialize_output: Callable[[OutputT], bytes]
    parse_output: Callable[[bytes], OutputT]


@dataclass(frozen=True)
class ManagedLocalStepHandle(Generic[OutputT]):
    execution_fence: ExecutionFence
    spec: ManagedLocalStepSpec[OutputT]
    step_attempt_id: str
    input_fingerprint: str
    prepared_status: Literal["prepared", "success"]


def prepare_step(
    execution_fence: ExecutionFence,
    *,
    spec: ManagedLocalStepSpec[OutputT],
    input_fingerprint: str,
) -> ManagedLocalStepHandle[OutputT]:
    """Prepare or replay one step under the operation's execution fence."""

    operation = ledger_store.get_operation(
        execution_fence.project_id,
        execution_fence.operation_id,
    )
    if operation is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_LEDGER_MISSING",
            f"{spec.label}任务缺少权威任务记录，请先运行任务详情审计。",
        )
    if (
        operation.kind != spec.kind
        or operation.workflow_version != spec.workflow_version
    ):
        raise AppException(
            409,
            _error_code(spec, "WORKFLOW_MISMATCH"),
            f"{spec.label}任务的工作流身份不匹配，请刷新后重新提交。",
        )
    previous_steps = step_store.list_step_attempts(
        execution_fence.project_id,
        execution_fence.operation_id,
    )
    if any(
        step.step_id == spec.step_id
        and step.workflow_version == spec.workflow_version
        and step.input_fingerprint != input_fingerprint
        for step in previous_steps
    ):
        raise AppException(
            409,
            _error_code(spec, "INPUT_CHANGED"),
            f"{spec.label}任务的实际输入已经变化，请重新提交任务。",
        )
    decision = step_store.prepare_step(
        execution_fence,
        step_id=spec.step_id,
        workflow_version=spec.workflow_version,
        input_fingerprint=input_fingerprint,
        cost_class="local_free",
        observed_at=_now(),
    )
    if decision.step.status not in {"prepared", "success"}:
        raise AppException(
            409,
            _error_code(spec, "STEP_CONFLICT"),
            f"{spec.label}步骤已有冲突状态，请重新执行该任务。",
        )
    return ManagedLocalStepHandle(
        execution_fence=execution_fence,
        spec=spec,
        step_attempt_id=decision.step.step_attempt_id,
        input_fingerprint=input_fingerprint,
        prepared_status=decision.step.status,
    )


def complete_step(
    handle: ManagedLocalStepHandle[OutputT],
    output: OutputT,
) -> OutputT:
    """Commit one canonical artifact and terminal step, or verify replay."""

    current = step_store.get_step_attempt(handle.step_attempt_id)
    if current is None:
        raise AppException(
            409,
            _error_code(handle.spec, "STEP_MISSING"),
            f"{handle.spec.label}步骤记录缺失，请先运行任务详情审计。",
        )
    if current.status == "success":
        persisted = read_success_output(handle)
        if persisted != output:
            raise AppException(
                409,
                _error_code(handle.spec, "REPLAY_MISMATCH"),
                f"重新执行得到的{handle.spec.label}信息与已保存结果不一致，请重新提交任务。",
            )
        return persisted
    if current.status != "prepared":
        raise AppException(
            409,
            _error_code(handle.spec, "STEP_CONFLICT"),
            f"{handle.spec.label}步骤已有冲突状态，请重新执行该任务。",
        )
    content = handle.spec.serialize_output(output)
    try:
        staged = artifact_store.stage_artifact(
            handle.execution_fence,
            file_backend=managed_artifact_files,
            step_attempt_id=handle.step_attempt_id,
            artifact_kind=ARTIFACT_KIND,
            artifact_key=ARTIFACT_KEY,
            payload_schema_version=(
                handle.spec.output_schema_version
            ),
            media_type="application/json",
            content=content,
            observed_at=_now(),
        )
        committed = artifact_store.commit_artifact(
            staged.artifact.artifact_id,
            file_backend=managed_artifact_files,
            execution_fence=handle.execution_fence,
            observed_at=_now(),
        )
        step_store.finish_step(
            handle.step_attempt_id,
            execution_fence=handle.execution_fence,
            status="success",
            observed_at=_now(),
            output_fingerprint=committed.content_fingerprint,
        )
    except (ExecutionFenceLost, ExecutionOperationCancelled):
        raise
    except Exception as exc:
        fail_step(
            handle,
            _error_code(handle.spec, "RESULT_WRITE_FAILED"),
        )
        raise AppException(
            500,
            _error_code(handle.spec, "RESULT_WRITE_FAILED"),
            f"{handle.spec.label}已经生成，但结果记录未能完整保存，本次任务不会标记成功。",
        ) from exc
    return output


def fail_step(
    handle: ManagedLocalStepHandle[OutputT] | None,
    error_code: str,
) -> None:
    if handle is None:
        return
    current = step_store.get_step_attempt(handle.step_attempt_id)
    if current is None or current.status in {
        "success",
        "failed",
        "cancelled",
        "result_unknown",
    }:
        return
    step_store.finish_step(
        handle.step_attempt_id,
        execution_fence=handle.execution_fence,
        status="failed",
        observed_at=_now(),
        error_code=str(
            error_code
            or _error_code(
                handle.spec,
                "RESULT_WRITE_FAILED",
            )
        ),
    )


def read_success_output(
    handle: ManagedLocalStepHandle[OutputT],
) -> OutputT:
    artifact = artifact_store.get_step_artifact(
        handle.execution_fence.project_id,
        handle.execution_fence.operation_id,
        handle.step_attempt_id,
        artifact_kind=ARTIFACT_KIND,
        artifact_key=ARTIFACT_KEY,
    )
    if artifact is None:
        raise AppException(
            409,
            _error_code(handle.spec, "ARTIFACT_MISSING"),
            f"已完成的{handle.spec.label}步骤缺少结果文件，请先运行任务详情审计。",
        )
    current = step_store.get_step_attempt(handle.step_attempt_id)
    if (
        current is None
        or current.output_fingerprint
        != artifact.content_fingerprint
    ):
        raise AppException(
            409,
            _error_code(handle.spec, "ARTIFACT_MISMATCH"),
            f"{handle.spec.label}步骤与结果文件的完整性指纹不一致，请先运行任务详情审计。",
        )
    try:
        verified = artifact_store.read_artifact(
            artifact.artifact_id,
            file_backend=managed_artifact_files,
        )
        if (
            verified.artifact.payload_schema_version
            != handle.spec.output_schema_version
            or verified.artifact.media_type
            != "application/json"
        ):
            raise ValueError(
                "managed local artifact contract mismatch"
            )
        return handle.spec.parse_output(verified.content)
    except AppException:
        raise
    except Exception as exc:
        raise AppException(
            409,
            _error_code(handle.spec, "ARTIFACT_INVALID"),
            f"已保存的{handle.spec.label}结果无法通过完整性校验，请先运行任务详情审计。",
        ) from exc


def step_succeeded(
    handle: ManagedLocalStepHandle[OutputT],
) -> bool:
    current = step_store.get_step_attempt(
        handle.step_attempt_id
    )
    return bool(current is not None and current.status == "success")


def _error_code(
    spec: ManagedLocalStepSpec[object],
    suffix: str,
) -> str:
    return (
        f"VIDEO_LOCALIZATION_{spec.error_namespace}_{suffix}"
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ARTIFACT_KEY",
    "ARTIFACT_KIND",
    "ManagedLocalStepHandle",
    "ManagedLocalStepSpec",
    "complete_step",
    "fail_step",
    "prepare_step",
    "read_success_output",
    "step_succeeded",
]
