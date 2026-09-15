"""Durable lifecycle adapter for one external Provider step.

The adapter persists submission before invoking the network boundary, writes
the Provider response to a managed artifact before terminal success, and
fails closed when the result cannot be proven. It contains no Provider- or
workflow-specific request logic.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.services import video_localization_operation_artifact_store
from app.services import video_localization_operation_step_store
from app.services.video_localization_execution_fence import ExecutionFence


ProviderStepExecutionOutcome = Literal["executed", "reused"]
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_SUPPORTED_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "text/plain",
        "application/octet-stream",
    }
)


@dataclass(frozen=True)
class ProviderStepPlan:
    step_id: str
    workflow_version: str
    input_fingerprint: str
    cost_class: video_localization_operation_step_store.StepCostClass
    provider_name: str
    provider_idempotency_key: str
    artifact_kind: str
    artifact_key: str
    payload_schema_version: str
    media_type: str


@dataclass(frozen=True)
class ProviderResponse:
    content: bytes
    provider_request_id: str | None = None


@dataclass(frozen=True)
class ProviderStepExecution:
    outcome: ProviderStepExecutionOutcome
    step: (
        video_localization_operation_step_store.OperationStepAttempt
    )
    artifact: (
        video_localization_operation_artifact_store
        .ManagedOperationArtifact
    )
    content: bytes


class ProviderRequestRejected(RuntimeError):
    """The Provider explicitly rejected the request without accepting it."""

    def __init__(self, error_code: str):
        self.error_code = _validated_error_code(error_code)
        super().__init__(self.error_code)


class ProviderResultUncertain(RuntimeError):
    """The Provider may have accepted work but no result can be proven."""

    def __init__(
        self,
        error_code: str,
        *,
        provider_request_id: str | None = None,
    ):
        self.error_code = _validated_error_code(error_code)
        self.provider_request_id = _optional_text(
            provider_request_id,
            "provider request ID",
        )
        super().__init__(self.error_code)


class ProviderReplayBlocked(RuntimeError):
    """A submitted paid call must be resolved before another attempt."""

    def __init__(
        self,
        step: (
            video_localization_operation_step_store
            .OperationStepAttempt
        ),
    ):
        self.step = step
        super().__init__(
            "the previous paid Provider result is unresolved"
        )


class ProviderStepExecutionFailed(RuntimeError):
    """A durable Provider step reached a known failed terminal state."""

    def __init__(
        self,
        step: (
            video_localization_operation_step_store
            .OperationStepAttempt
        ),
    ):
        self.step = step
        super().__init__(
            step.error_code or "PROVIDER_STEP_FAILED"
        )


class ProviderStepResultUnknown(RuntimeError):
    """The result cannot be proven and automatic replay is prohibited."""

    def __init__(
        self,
        step: (
            video_localization_operation_step_store
            .OperationStepAttempt
        ),
    ):
        self.step = step
        super().__init__(
            step.error_code or "PROVIDER_RESULT_UNKNOWN"
        )


class ProviderStepIntegrityError(RuntimeError):
    """A terminal step and its managed result artifact disagree."""


def run_provider_step(
    execution_fence: ExecutionFence,
    *,
    file_backend: ManagedArtifactFileBackend,
    plan: ProviderStepPlan,
    submit: Callable[[str], ProviderResponse],
    clock: Callable[[], datetime] | None = None,
) -> ProviderStepExecution:
    """Execute or reuse one durable external Provider call."""

    _validate_plan(plan)
    actual_clock = clock or _utc_now
    video_localization_operation_step_store.recover_interrupted_provider_steps(
        execution_fence,
        observed_at=actual_clock(),
    )
    try:
        decision = (
            video_localization_operation_step_store.prepare_step(
                execution_fence,
                step_id=plan.step_id,
                workflow_version=plan.workflow_version,
                input_fingerprint=plan.input_fingerprint,
                cost_class=plan.cost_class,
                provider_name=plan.provider_name,
                provider_idempotency_key=(
                    plan.provider_idempotency_key
                ),
                observed_at=actual_clock(),
            )
        )
    except (
        video_localization_operation_step_store
        .UnresolvedProviderResult
    ) as exc:
        raise ProviderReplayBlocked(exc.step) from None

    step = decision.step
    if step.status == "success":
        return _reuse_success(
            step,
            file_backend=file_backend,
            plan=plan,
        )
    if step.status in {"failed", "cancelled"}:
        raise ProviderStepExecutionFailed(step)
    if step.status in {"submitted", "result_unknown"}:
        raise ProviderReplayBlocked(step)

    step = video_localization_operation_step_store.mark_step_submitted(
        step.step_attempt_id,
        execution_fence=execution_fence,
        observed_at=actual_clock(),
    )
    try:
        response = submit(plan.provider_idempotency_key)
    except ProviderRequestRejected as exc:
        failed = _finish_failed(
            step.step_attempt_id,
            execution_fence=execution_fence,
            error_code=exc.error_code,
            observed_at=actual_clock(),
        )
        raise ProviderStepExecutionFailed(failed) from None
    except ProviderResultUncertain as exc:
        step = _record_optional_request_id(
            step,
            execution_fence=execution_fence,
            provider_request_id=exc.provider_request_id,
            observed_at=actual_clock(),
        )
        unknown = _mark_unknown(
            step,
            execution_fence=execution_fence,
            error_code=exc.error_code,
            observed_at=actual_clock(),
        )
        raise ProviderStepResultUnknown(unknown) from None
    except Exception:
        unknown = _mark_unknown(
            step,
            execution_fence=execution_fence,
            error_code="PROVIDER_RESULT_UNKNOWN",
            observed_at=actual_clock(),
        )
        raise ProviderStepResultUnknown(unknown) from None

    try:
        response = _validate_response(response)
    except (TypeError, ValueError):
        failed = _finish_failed(
            step.step_attempt_id,
            execution_fence=execution_fence,
            error_code="PROVIDER_RESPONSE_INVALID",
            observed_at=actual_clock(),
        )
        raise ProviderStepExecutionFailed(failed) from None

    try:
        step = _record_optional_request_id(
            step,
            execution_fence=execution_fence,
            provider_request_id=response.provider_request_id,
            observed_at=actual_clock(),
        )
    except Exception:
        unknown = _mark_unknown(
            step,
            execution_fence=execution_fence,
            error_code="PROVIDER_RESULT_PERSISTENCE_FAILED",
            observed_at=actual_clock(),
        )
        raise ProviderStepResultUnknown(unknown) from None

    try:
        staged = (
            video_localization_operation_artifact_store.stage_artifact(
                execution_fence,
                file_backend=file_backend,
                step_attempt_id=step.step_attempt_id,
                artifact_kind=plan.artifact_kind,
                artifact_key=plan.artifact_key,
                payload_schema_version=plan.payload_schema_version,
                media_type=plan.media_type,
                content=response.content,
                observed_at=actual_clock(),
            )
        )
    except ValueError:
        failed = _finish_failed(
            step.step_attempt_id,
            execution_fence=execution_fence,
            error_code="PROVIDER_RESPONSE_INVALID",
            observed_at=actual_clock(),
        )
        raise ProviderStepExecutionFailed(failed) from None
    except Exception:
        unknown = _mark_unknown(
            step,
            execution_fence=execution_fence,
            error_code="PROVIDER_RESULT_PERSISTENCE_FAILED",
            observed_at=actual_clock(),
        )
        raise ProviderStepResultUnknown(unknown) from None

    try:
        artifact = (
            video_localization_operation_artifact_store.commit_artifact(
                staged.artifact.artifact_id,
                file_backend=file_backend,
                execution_fence=execution_fence,
                observed_at=actual_clock(),
            )
        )
        completed = (
            video_localization_operation_step_store.finish_step(
                step.step_attempt_id,
                execution_fence=execution_fence,
                status="success",
                observed_at=actual_clock(),
                output_fingerprint=artifact.content_fingerprint,
            )
        )
    except Exception:
        unknown = _mark_unknown(
            step,
            execution_fence=execution_fence,
            error_code="PROVIDER_RESULT_PERSISTENCE_FAILED",
            observed_at=actual_clock(),
        )
        raise ProviderStepResultUnknown(unknown) from None

    return ProviderStepExecution(
        outcome="executed",
        step=completed,
        artifact=artifact,
        content=response.content,
    )


def _reuse_success(
    step: (
        video_localization_operation_step_store.OperationStepAttempt
    ),
    *,
    file_backend: ManagedArtifactFileBackend,
    plan: ProviderStepPlan,
) -> ProviderStepExecution:
    artifact = (
        video_localization_operation_artifact_store.get_step_artifact(
            step.project_id,
            step.operation_id,
            step.step_attempt_id,
            artifact_kind=plan.artifact_kind,
            artifact_key=plan.artifact_key,
        )
    )
    if artifact is None or artifact.status != "committed":
        raise ProviderStepIntegrityError(
            "successful Provider step has no committed artifact"
        )
    read = video_localization_operation_artifact_store.read_artifact(
        artifact.artifact_id,
        file_backend=file_backend,
    )
    if step.output_fingerprint != artifact.content_fingerprint:
        raise ProviderStepIntegrityError(
            "successful Provider step fingerprint differs from artifact"
        )
    return ProviderStepExecution(
        outcome="reused",
        step=step,
        artifact=artifact,
        content=read.content,
    )


def _record_optional_request_id(
    step: (
        video_localization_operation_step_store.OperationStepAttempt
    ),
    *,
    execution_fence: ExecutionFence,
    provider_request_id: str | None,
    observed_at: datetime,
) -> video_localization_operation_step_store.OperationStepAttempt:
    if provider_request_id is None:
        return step
    return (
        video_localization_operation_step_store.record_provider_request_id(
            step.step_attempt_id,
            execution_fence=execution_fence,
            provider_request_id=provider_request_id,
            observed_at=observed_at,
        )
    )


def _finish_failed(
    step_attempt_id: str,
    *,
    execution_fence: ExecutionFence,
    error_code: str,
    observed_at: datetime,
) -> video_localization_operation_step_store.OperationStepAttempt:
    return video_localization_operation_step_store.finish_step(
        step_attempt_id,
        execution_fence=execution_fence,
        status="failed",
        observed_at=observed_at,
        error_code=_validated_error_code(error_code),
    )


def _mark_unknown(
    step: (
        video_localization_operation_step_store.OperationStepAttempt
    ),
    *,
    execution_fence: ExecutionFence,
    error_code: str,
    observed_at: datetime,
) -> video_localization_operation_step_store.OperationStepAttempt:
    try:
        return (
            video_localization_operation_step_store
            .mark_step_result_unknown(
                step.step_attempt_id,
                execution_fence=execution_fence,
                observed_at=observed_at,
                error_code=_validated_error_code(error_code),
            )
        )
    except Exception:
        current = (
            video_localization_operation_step_store.get_step_attempt(
                step.step_attempt_id
            )
        )
        if current is not None and current.status == "result_unknown":
            return current
        raise


def _validate_plan(plan: ProviderStepPlan) -> None:
    if plan.cost_class not in {"external_free", "external_paid"}:
        raise ValueError(
            "Provider lifecycle requires an external cost class"
        )
    for value, label in (
        (plan.step_id, "step ID"),
        (plan.workflow_version, "workflow version"),
        (plan.input_fingerprint, "input fingerprint"),
        (plan.provider_name, "provider name"),
        (plan.provider_idempotency_key, "provider idempotency key"),
        (plan.artifact_kind, "artifact kind"),
        (plan.artifact_key, "artifact key"),
        (plan.payload_schema_version, "payload schema version"),
    ):
        _required_text(value, label)
    if plan.media_type not in _SUPPORTED_MEDIA_TYPES:
        raise ValueError("managed artifact media type is unsupported")


def _validate_response(response: object) -> ProviderResponse:
    if not isinstance(response, ProviderResponse):
        raise TypeError("Provider response uses an unsupported contract")
    if not isinstance(response.content, bytes) or not response.content:
        raise ValueError("Provider response content must be non-empty bytes")
    request_id = _optional_text(
        response.provider_request_id,
        "provider request ID",
    )
    return ProviderResponse(
        content=response.content,
        provider_request_id=request_id,
    )


def _validated_error_code(value: str) -> str:
    normalized = _required_text(value, "error code")
    if _ERROR_CODE.fullmatch(normalized) is None:
        raise ValueError("error code must use bounded uppercase tokens")
    return normalized


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _optional_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ProviderReplayBlocked",
    "ProviderRequestRejected",
    "ProviderResponse",
    "ProviderResultUncertain",
    "ProviderStepExecution",
    "ProviderStepExecutionFailed",
    "ProviderStepExecutionOutcome",
    "ProviderStepIntegrityError",
    "ProviderStepPlan",
    "ProviderStepResultUnknown",
    "run_provider_step",
]
