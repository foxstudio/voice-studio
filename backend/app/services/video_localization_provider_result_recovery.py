"""Recover a queried Provider result into artifact and adjudication state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.services import database
from app.services import video_localization_operation_artifact_store
from app.services import (
    video_localization_operation_step_adjudication_store,
)
from app.services import video_localization_operation_step_store


ProviderRecoveryOutcome = Literal["created", "reused"]


class ProviderRecoveryConflict(RuntimeError):
    """The recovery command conflicts with durable step state."""


class ProviderRecoveryIntegrityError(RuntimeError):
    """Recovered content, artifact metadata, and adjudication disagree."""


@dataclass(frozen=True)
class ProviderRecoveryResult:
    outcome: ProviderRecoveryOutcome
    step: (
        video_localization_operation_step_store.OperationStepAttempt
    )
    artifact: (
        video_localization_operation_artifact_store
        .ManagedOperationArtifact
    )
    adjudication: (
        video_localization_operation_step_adjudication_store
        .StepResultAdjudication
    )
    content: bytes


def recover_provider_success(
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_status_revision: int,
    reason_code: str,
    artifact_kind: str,
    artifact_key: str,
    payload_schema_version: str,
    media_type: str,
    content: bytes,
    file_backend: ManagedArtifactFileBackend,
    observed_at: datetime,
) -> ProviderRecoveryResult:
    """Persist queried content, then atomically commit and adjudicate it."""

    fingerprint = file_backend.content_fingerprint(content)
    (
        video_localization_operation_step_adjudication_store
        .validate_adjudication_command(
            project_id,
            operation_id,
            step_attempt_id,
            expected_status_revision=expected_status_revision,
            decision="success",
            source="provider_query",
            reason_code=reason_code,
            observed_at=observed_at,
            output_fingerprint=fingerprint,
        )
    )
    reused = _reuse_completed(
        project_id,
        operation_id,
        step_attempt_id,
        expected_status_revision=expected_status_revision,
        reason_code=reason_code,
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        payload_schema_version=payload_schema_version,
        media_type=media_type,
        content=content,
        expected_fingerprint=fingerprint,
        file_backend=file_backend,
        observed_at=observed_at,
    )
    if reused is not None:
        return reused
    _require_unknown_provider_query_step(
        project_id,
        operation_id,
        step_attempt_id,
        expected_status_revision=expected_status_revision,
    )
    try:
        staged = (
            video_localization_operation_artifact_store
            .stage_result_unknown_artifact(
                project_id,
                operation_id,
                step_attempt_id,
                expected_status_revision=expected_status_revision,
                file_backend=file_backend,
                artifact_kind=artifact_kind,
                artifact_key=artifact_key,
                payload_schema_version=payload_schema_version,
                media_type=media_type,
                content=content,
                observed_at=observed_at,
            )
        )
        with database.conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            artifact = (
                video_localization_operation_artifact_store
                .commit_result_unknown_artifact_from_connection(
                    connection,
                    staged.artifact.artifact_id,
                    project_id=project_id,
                    operation_id=operation_id,
                    step_attempt_id=step_attempt_id,
                    expected_status_revision=(
                        expected_status_revision
                    ),
                    file_backend=file_backend,
                    observed_at=observed_at,
                )
            )
            if artifact.content_fingerprint != fingerprint:
                raise ProviderRecoveryIntegrityError(
                    "recovery artifact fingerprint differs from content"
                )
            decision = (
                video_localization_operation_step_adjudication_store
                .adjudicate_result_unknown_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    step_attempt_id,
                    expected_status_revision=(
                        expected_status_revision
                    ),
                    decision="success",
                    source="provider_query",
                    reason_code=reason_code,
                    observed_at=observed_at,
                    output_fingerprint=fingerprint,
                )
            )
    except (
        video_localization_operation_artifact_store
        .ArtifactIdentityConflict,
        video_localization_operation_step_adjudication_store
        .AdjudicationConflict,
    ) as exc:
        reused = _reuse_completed(
            project_id,
            operation_id,
            step_attempt_id,
            expected_status_revision=expected_status_revision,
            reason_code=reason_code,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
            payload_schema_version=payload_schema_version,
            media_type=media_type,
            content=content,
            expected_fingerprint=fingerprint,
            file_backend=file_backend,
            observed_at=observed_at,
        )
        if reused is not None:
            return reused
        raise ProviderRecoveryConflict(str(exc)) from None

    read = video_localization_operation_artifact_store.read_artifact(
        artifact.artifact_id,
        file_backend=file_backend,
    )
    if (
        read.artifact.content_fingerprint != fingerprint
        or read.content != content
        or decision.step.output_fingerprint != fingerprint
        or decision.adjudication.output_fingerprint != fingerprint
    ):
        raise ProviderRecoveryIntegrityError(
            "recovered result differs after durable commit"
        )
    return ProviderRecoveryResult(
        outcome="created",
        step=decision.step,
        artifact=read.artifact,
        adjudication=decision.adjudication,
        content=read.content,
    )


def _require_unknown_provider_query_step(
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_status_revision: int,
) -> None:
    step = video_localization_operation_step_store.get_step_attempt(
        step_attempt_id
    )
    if step is None:
        raise ProviderRecoveryConflict(
            "result_unknown step attempt does not exist"
        )
    if (
        step.project_id != project_id
        or step.operation_id != operation_id
    ):
        raise ProviderRecoveryConflict(
            "result_unknown step identity does not match"
        )
    if (
        step.status != "result_unknown"
        or step.status_revision != expected_status_revision
    ):
        raise ProviderRecoveryConflict(
            "result_unknown status revision is stale"
        )
    if step.cost_class == "local_free":
        raise ProviderRecoveryConflict(
            "Provider recovery requires an external step"
        )
    if step.provider_request_id is None:
        raise ProviderRecoveryConflict(
            "Provider query recovery requires a request ID"
        )


def _reuse_completed(
    project_id: str,
    operation_id: str,
    step_attempt_id: str,
    *,
    expected_status_revision: int,
    reason_code: str,
    artifact_kind: str,
    artifact_key: str,
    payload_schema_version: str,
    media_type: str,
    content: bytes,
    expected_fingerprint: str,
    file_backend: ManagedArtifactFileBackend,
    observed_at: datetime,
) -> ProviderRecoveryResult | None:
    existing = (
        video_localization_operation_step_adjudication_store
        .get_adjudication(step_attempt_id)
    )
    if existing is None:
        return None
    try:
        decision = (
            video_localization_operation_step_adjudication_store
            .adjudicate_result_unknown(
                project_id,
                operation_id,
                step_attempt_id,
                expected_status_revision=expected_status_revision,
                decision="success",
                source="provider_query",
                reason_code=reason_code,
                observed_at=observed_at,
                output_fingerprint=expected_fingerprint,
            )
        )
    except (
        video_localization_operation_step_adjudication_store
        .AdjudicationConflict
    ) as exc:
        raise ProviderRecoveryConflict(
            "step has a different durable adjudication"
        ) from exc
    artifact = (
        video_localization_operation_artifact_store.get_step_artifact(
            project_id,
            operation_id,
            step_attempt_id,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
        )
    )
    if (
        artifact is None
        or artifact.status != "committed"
        or artifact.payload_schema_version != payload_schema_version
        or artifact.media_type != media_type
        or artifact.content_fingerprint != expected_fingerprint
        or decision.step.output_fingerprint != expected_fingerprint
    ):
        raise ProviderRecoveryIntegrityError(
            "completed recovery lacks matching committed artifact"
        )
    read = video_localization_operation_artifact_store.read_artifact(
        artifact.artifact_id,
        file_backend=file_backend,
    )
    if read.content != content:
        raise ProviderRecoveryConflict(
            "completed recovery contains different content"
        )
    return ProviderRecoveryResult(
        outcome="reused",
        step=decision.step,
        artifact=read.artifact,
        adjudication=decision.adjudication,
        content=read.content,
    )


__all__ = [
    "ProviderRecoveryConflict",
    "ProviderRecoveryIntegrityError",
    "ProviderRecoveryOutcome",
    "ProviderRecoveryResult",
    "recover_provider_success",
]
