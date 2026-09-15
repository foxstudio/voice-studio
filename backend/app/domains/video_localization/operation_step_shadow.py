"""Shadow migration of stable operation reports into step/artifact storage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import ValidationError

from app.domains.video_localization import (
    managed_artifact_files,
    source_audio_execution,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
)
from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_STEP_ID,
    SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION,
    SourceAudioStepOutputV1,
    source_audio_step_input_fingerprint,
    source_audio_step_output_bytes,
    source_audio_step_output_from_summary,
)
from app.services import database
from app.services import video_localization_operation_artifact_store
from app.services import video_localization_operation_ledger_store
from app.services import video_localization_operation_step_store
from app.services import video_localization_operation_store
from app.services.video_localization_execution_fence import (
    ExecutionFence,
)


_SHADOW_WRITE_ERROR = (
    "VIDEO_LOCALIZATION_SHADOW_ARTIFACT_WRITE_FAILED"
)
_SHADOW_INPUT_CHANGED = (
    "VIDEO_LOCALIZATION_SHADOW_STEP_INPUT_CHANGED"
)
_logger = logging.getLogger(__name__)

ShadowReconciliationStatus = Literal[
    "matched",
    "missing",
    "incomplete",
    "mismatch",
    "invalid",
]


@dataclass(frozen=True)
class SourceAudioShadowHandle:
    execution_fence: ExecutionFence
    step_attempt_id: str
    source_video_fingerprint: str


@dataclass(frozen=True)
class SourceAudioShadowReconciliation:
    status: ShadowReconciliationStatus
    step_attempt_id: str | None
    issues: tuple[str, ...]


def try_prepare_source_audio_step(
    execution_fence: ExecutionFence,
    draft: VideoLocalizationDraft,
) -> SourceAudioShadowHandle | None:
    try:
        operation = (
            video_localization_operation_ledger_store.get_operation(
                execution_fence.project_id,
                execution_fence.operation_id,
            )
        )
        if operation is None:
            raise RuntimeError(
                "operation ledger row is missing"
            )
        source_fingerprint = (
            source_audio_execution.source_video_fingerprint(draft)
        )
        decision = (
            video_localization_operation_step_store.prepare_step(
                execution_fence,
                step_id=SOURCE_AUDIO_STEP_ID,
                workflow_version=operation.workflow_version,
                input_fingerprint=(
                    source_audio_step_input_fingerprint(
                        source_fingerprint
                    )
                ),
                cost_class="local_free",
                observed_at=_now(),
            )
        )
        return SourceAudioShadowHandle(
            execution_fence=execution_fence,
            step_attempt_id=decision.step.step_attempt_id,
            source_video_fingerprint=source_fingerprint,
        )
    except Exception as exc:
        _logger.warning(
            "source audio shadow preparation failed (%s)",
            type(exc).__name__,
        )
        return None


def try_complete_source_audio_step(
    handle: SourceAudioShadowHandle | None,
    draft: VideoLocalizationDraft,
    summary: dict,
) -> None:
    if handle is None:
        return
    actual_source_fingerprint = (
        source_audio_execution.source_video_fingerprint(draft)
    )
    if (
        actual_source_fingerprint
        != handle.source_video_fingerprint
    ):
        try_fail_source_audio_step(
            handle,
            _SHADOW_INPUT_CHANGED,
        )
        handle = try_prepare_source_audio_step(
            handle.execution_fence,
            draft,
        )
        if handle is None:
            return
    try:
        output = source_audio_step_output_from_summary(summary)
        staged = (
            video_localization_operation_artifact_store
            .stage_artifact(
                handle.execution_fence,
                file_backend=managed_artifact_files,
                step_attempt_id=handle.step_attempt_id,
                artifact_kind=source_audio_execution.ARTIFACT_KIND,
                artifact_key=source_audio_execution.ARTIFACT_KEY,
                payload_schema_version=(
                    SOURCE_AUDIO_STEP_OUTPUT_SCHEMA_VERSION
                ),
                media_type="application/json",
                content=source_audio_step_output_bytes(output),
                observed_at=_now(),
            )
        )
        committed = (
            video_localization_operation_artifact_store
            .commit_artifact(
                staged.artifact.artifact_id,
                file_backend=managed_artifact_files,
                execution_fence=handle.execution_fence,
                observed_at=_now(),
            )
        )
        video_localization_operation_step_store.finish_step(
            handle.step_attempt_id,
            execution_fence=handle.execution_fence,
            status="success",
            observed_at=_now(),
            output_fingerprint=committed.content_fingerprint,
        )
    except Exception as exc:
        try:
            video_localization_operation_step_store.finish_step(
                handle.step_attempt_id,
                execution_fence=handle.execution_fence,
                status="failed",
                observed_at=_now(),
                error_code=_SHADOW_WRITE_ERROR,
            )
        except Exception:
            pass
        _logger.warning(
            "source audio shadow completion failed (%s)",
            type(exc).__name__,
        )


def try_fail_source_audio_step(
    handle: SourceAudioShadowHandle | None,
    error_code: str,
) -> None:
    if handle is None:
        return
    try:
        video_localization_operation_step_store.finish_step(
            handle.step_attempt_id,
            execution_fence=handle.execution_fence,
            status="failed",
            observed_at=_now(),
            error_code=error_code,
        )
    except Exception as exc:
        _logger.warning(
            "source audio shadow failure recording failed (%s)",
            type(exc).__name__,
        )


def reconcile_source_audio_operation(
    project_id: str,
    operation_id: str,
) -> SourceAudioShadowReconciliation:
    step = None
    try:
        with database.read_conn() as connection:
            mirror = (
                video_localization_operation_store
                .read_project_mirror_operations_from_connection(
                    connection,
                    project_id,
                )
            )
            steps = [
                item
                for item in (
                    video_localization_operation_step_store
                    .list_step_attempts_from_connection(
                        connection,
                        project_id,
                        operation_id,
                    )
                )
                if item.step_id == SOURCE_AUDIO_STEP_ID
            ]
            step = steps[-1] if steps else None
            artifact = (
                video_localization_operation_artifact_store
                .get_step_artifact_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    step.step_attempt_id,
                    artifact_kind=source_audio_execution.ARTIFACT_KIND,
                    artifact_key=source_audio_execution.ARTIFACT_KEY,
                )
                if step is not None
                else None
            )
    except (
        video_localization_operation_step_store.StepSchemaError,
        video_localization_operation_artifact_store.ArtifactSchemaError,
        TypeError,
        ValueError,
    ):
        return _reconciliation(
            "invalid",
            step.step_attempt_id if step is not None else None,
            "metadata_invalid",
        )
    operation = next(
        (
            item
            for item in mirror.operations
            if str(item.get("operation_id") or "")
            == operation_id
        ),
        None,
    )
    if operation is None:
        return _reconciliation("missing", None, "mirror_missing")
    if step is None:
        return _reconciliation("missing", None, "step_missing")
    if step.status == "failed":
        return _reconciliation(
            "incomplete",
            step.step_attempt_id,
            "step_failed",
        )
    if step.status != "success":
        return _reconciliation(
            "incomplete",
            step.step_attempt_id,
            "step_not_terminal",
        )
    if artifact is None:
        return _reconciliation(
            "incomplete",
            step.step_attempt_id,
            "artifact_missing",
        )
    if artifact.status != "committed":
        return _reconciliation(
            "incomplete",
            step.step_attempt_id,
            "artifact_not_committed",
        )
    try:
        if (
            artifact.storage_backend
            != managed_artifact_files.ARTIFACT_STORAGE_BACKEND
        ):
            return _reconciliation(
                "invalid",
                step.step_attempt_id,
                "artifact_backend_invalid",
            )
        content = managed_artifact_files.read_verified_file(
            artifact.project_id,
            artifact.storage_key,
            expected_size=artifact.size_bytes,
            expected_fingerprint=(
                artifact.content_fingerprint
            ),
        )
        artifact_output = (
            SourceAudioStepOutputV1.model_validate_json(
                content
            )
        )
        mirror_output = source_audio_step_output_from_summary(
            dict(operation.get("result_summary") or {})
        )
    except ValidationError:
        return _reconciliation(
            "invalid",
            step.step_attempt_id,
            "output_schema_invalid",
        )
    except (
        managed_artifact_files.ManagedArtifactIntegrityError,
        managed_artifact_files.ManagedArtifactPathError,
    ):
        return _reconciliation(
            "invalid",
            step.step_attempt_id,
            "artifact_invalid",
        )
    if step.output_fingerprint != artifact.content_fingerprint:
        return _reconciliation(
            "mismatch",
            step.step_attempt_id,
            "fingerprint_mismatch",
        )
    if artifact_output != mirror_output:
        return _reconciliation(
            "mismatch",
            step.step_attempt_id,
            "output_mismatch",
        )
    return SourceAudioShadowReconciliation(
        status="matched",
        step_attempt_id=step.step_attempt_id,
        issues=(),
    )


def _reconciliation(
    status: ShadowReconciliationStatus,
    step_attempt_id: str | None,
    issue: str,
) -> SourceAudioShadowReconciliation:
    return SourceAudioShadowReconciliation(
        status=status,
        step_attempt_id=step_attempt_id,
        issues=(issue,),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "SourceAudioShadowHandle",
    "SourceAudioShadowReconciliation",
    "reconcile_source_audio_operation",
    "try_complete_source_audio_step",
    "try_fail_source_audio_step",
    "try_prepare_source_audio_step",
]
