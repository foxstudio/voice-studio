"""Durable local execution for managed stem separation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.domains.video_localization import (
    managed_local_step,
    managed_local_workflow_specs,
    media_assets,
    operation_state,
    source_pipeline,
    stem_separation_operation_projection,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    now_iso,
)
from app.errors import AppException
from app.schemas.video_localization_stem_separation_step import (
    STEM_SEPARATION_WORKFLOW_VERSION,
    StemSeparationStepOutputV1,
    stem_separation_step_input_fingerprint,
    stem_separation_step_output_from_summary,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
)


_INPUT_CHANGED = "VIDEO_LOCALIZATION_SOURCE_AUDIO_CHANGED"
_WRITE_ERROR = (
    "VIDEO_LOCALIZATION_STEM_SEPARATION_RESULT_WRITE_FAILED"
)


@dataclass(frozen=True)
class StemSeparationExecutionHandle:
    local_step: (
        managed_local_step.ManagedLocalStepHandle[
            StemSeparationStepOutputV1
        ]
    )
    source_audio_sha256: str

    @property
    def execution_fence(self) -> ExecutionFence:
        return self.local_step.execution_fence

    @property
    def step_attempt_id(self) -> str:
        return self.local_step.step_attempt_id


def execute_managed_stem_separation_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ],
) -> VideoLocalizationDraft:
    """Commit verified stems and operation success in one Project save."""

    from app.domains.video_localization import service

    source_draft = service.get_video_localization(project_id)
    if source_draft is None:
        raise AppException(
            404,
            "PROJECT_NOT_FOUND",
            "Project not found",
        )
    handle = prepare_stem_separation_step(
        execution_fence,
        source_draft,
    )
    reusable = _reusable_separation(project_id, handle)
    commit_verified = False

    def guarded_commit(
        action: Callable[[], VideoLocalizationDraft | None],
    ) -> tuple[bool, VideoLocalizationDraft | None]:
        committed, result = commit_guard(action)
        if not committed:
            raise ExecutionFenceLost(
                "stem separation commit fence was lost"
            )
        return committed, result

    def commit_result(
        separated_draft: VideoLocalizationDraft,
    ) -> VideoLocalizationDraft:
        nonlocal commit_verified
        output = complete_stem_separation_step(
            handle,
            separated_draft,
        )
        commit_verified = True
        return operation_state.with_operation_updates(
            separated_draft,
            operation.operation_id,
            {
                "status": "success",
                "progress": 1.0,
                "completed_at": now_iso(),
                "result_summary": (
                    stem_separation_operation_projection
                    .success_summary(output)
                ),
            },
            kind="stems",
        )

    try:
        updated = service.separate_source_audio(
            project_id,
            commit_transform=commit_result,
            commit_guard=guarded_commit,
            output_prefix=operation.operation_id,
            reuse_separation=reusable,
            preserve_generated_on_failure=(
                lambda: commit_verified
            ),
        )
    except (ExecutionFenceLost, ExecutionOperationCancelled):
        raise
    except AppException as exc:
        if commit_verified:
            _cleanup_owned_outputs(
                project_id,
                operation.operation_id,
            )
        fail_stem_separation_step(handle, exc.code)
        raise
    except Exception:
        if commit_verified:
            _cleanup_owned_outputs(
                project_id,
                operation.operation_id,
            )
        fail_stem_separation_step(
            handle,
            "VIDEO_LOCALIZATION_OPERATION_FAILED",
        )
        raise
    if updated is None:
        raise AppException(
            404,
            "PROJECT_NOT_FOUND",
            "Project not found",
        )
    return updated


def is_managed_stem_separation_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "stems"
        and workflow_version
        == STEM_SEPARATION_WORKFLOW_VERSION
    )


def prepare_stem_separation_step(
    execution_fence: ExecutionFence,
    draft: VideoLocalizationDraft,
) -> StemSeparationExecutionHandle:
    source_audio_sha256 = (
        source_pipeline.source_audio_content_fingerprint(draft)
    )
    local_step = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .STEM_SEPARATION_STEP_SPEC
        ),
        input_fingerprint=(
            stem_separation_step_input_fingerprint(
                source_audio_sha256
            )
        ),
    )
    return StemSeparationExecutionHandle(
        local_step=local_step,
        source_audio_sha256=source_audio_sha256,
    )


def complete_stem_separation_step(
    handle: StemSeparationExecutionHandle,
    draft: VideoLocalizationDraft,
) -> StemSeparationStepOutputV1:
    current_source_sha256 = (
        source_pipeline.source_audio_content_fingerprint(draft)
    )
    if current_source_sha256 != handle.source_audio_sha256:
        fail_stem_separation_step(handle, _INPUT_CHANGED)
        raise AppException(
            409,
            _INPUT_CHANGED,
            "分离人声期间源音轨发生了变化，请重新执行。",
        )
    stems = draft.stems
    output = stem_separation_step_output_from_summary(
        operation_state.stems_summary(draft),
        source_audio_sha256=current_source_sha256,
        vocals_clean_sha256=str(
            stems.vocals_clean_sha256 or ""
        ),
        background_sha256=str(
            stems.background_sha256 or ""
        ),
        quality_flags=stems.quality_flags,
    )
    return managed_local_step.complete_step(
        handle.local_step,
        output,
    )


def fail_stem_separation_step(
    handle: StemSeparationExecutionHandle | None,
    error_code: str,
) -> None:
    managed_local_step.fail_step(
        handle.local_step if handle is not None else None,
        str(error_code or _WRITE_ERROR),
    )


def _reusable_separation(
    project_id: str,
    handle: StemSeparationExecutionHandle,
) -> source_pipeline.ReusableStemSeparation | None:
    if handle.local_step.prepared_status != "success":
        return None
    output = managed_local_step.read_success_output(
        handle.local_step
    )
    vocals_path, background_path = (
        media_assets.stem_separation_output_paths(
            project_id,
            handle.execution_fence.operation_id,
        )
    )
    if not _matches(vocals_path, output.vocals_clean_sha256):
        return None
    if not _matches(
        background_path,
        output.background_sha256,
    ):
        return None
    return source_pipeline.ReusableStemSeparation(
        source_audio_sha256=output.source_audio_sha256,
        vocals_clean_path=vocals_path,
        vocals_clean_sha256=output.vocals_clean_sha256,
        background_path=background_path,
        background_sha256=output.background_sha256,
        separation_engine_id=output.separation_engine_id,
        quality_flags=output.quality_flags,
    )


def _matches(path: Path, expected_sha256: str) -> bool:
    return bool(
        path.is_file()
        and media_assets.file_sha256(path) == expected_sha256
    )


def _cleanup_owned_outputs(
    project_id: str,
    operation_id: str,
) -> None:
    for path in media_assets.stem_separation_output_paths(
        project_id,
        operation_id,
    ):
        path.unlink(missing_ok=True)


__all__ = [
    "StemSeparationExecutionHandle",
    "complete_stem_separation_step",
    "execute_managed_stem_separation_operation",
    "fail_stem_separation_step",
    "is_managed_stem_separation_operation",
    "prepare_stem_separation_step",
]
