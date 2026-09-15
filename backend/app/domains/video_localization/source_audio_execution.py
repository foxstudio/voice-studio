"""Durable local execution for the managed source-audio workflow."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.domains.video_localization import (
    managed_local_step,
    managed_local_workflow_specs,
    media_assets,
    operation_state,
    source_audio_operation_projection,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    now_iso,
)
from app.errors import AppException
from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_WORKFLOW_VERSION,
    SourceAudioStepOutputV1,
    source_audio_step_input_fingerprint,
    source_audio_step_output_from_summary,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
)


ARTIFACT_KIND = managed_local_step.ARTIFACT_KIND
ARTIFACT_KEY = managed_local_step.ARTIFACT_KEY
_WRITE_ERROR = "VIDEO_LOCALIZATION_SOURCE_AUDIO_RESULT_WRITE_FAILED"
_INPUT_CHANGED = "VIDEO_LOCALIZATION_SOURCE_CHANGED"


@dataclass(frozen=True)
class SourceAudioExecutionHandle:
    local_step: (
        managed_local_step.ManagedLocalStepHandle[
            SourceAudioStepOutputV1
        ]
    )
    source_video_fingerprint: str

    @property
    def execution_fence(self) -> ExecutionFence:
        return self.local_step.execution_fence

    @property
    def step_attempt_id(self) -> str:
        return self.local_step.step_attempt_id


def execute_managed_source_audio_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ],
) -> VideoLocalizationDraft:
    """Run the local step and commit media plus success in one Project save."""

    from app.domains.video_localization import service

    source_draft = service.get_video_localization(project_id)
    if source_draft is None:
        raise AppException(
            404,
            "PROJECT_NOT_FOUND",
            "Project not found",
        )
    handle = prepare_source_audio_step(
        execution_fence,
        source_draft,
    )

    def commit_result(
        extracted_draft: VideoLocalizationDraft,
    ) -> VideoLocalizationDraft:
        output = complete_source_audio_step(
            handle,
            extracted_draft,
            operation_state.source_audio_summary(
                extracted_draft
            ),
        )
        return operation_state.with_operation_updates(
            extracted_draft,
            operation.operation_id,
            {
                "status": "success",
                "progress": 1.0,
                "completed_at": now_iso(),
                "result_summary": (
                    source_audio_operation_projection
                    .success_summary(output)
                ),
            },
            kind="source_audio",
        )

    try:
        updated = service.extract_source_audio(
            project_id,
            commit_transform=commit_result,
            commit_guard=commit_guard,
        )
    except (ExecutionFenceLost, ExecutionOperationCancelled):
        raise
    except AppException as exc:
        fail_source_audio_step(handle, exc.code)
        raise
    except Exception:
        fail_source_audio_step(
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


def is_managed_source_audio_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "source_audio"
        and workflow_version == SOURCE_AUDIO_WORKFLOW_VERSION
    )


def prepare_source_audio_step(
    execution_fence: ExecutionFence,
    draft: VideoLocalizationDraft,
) -> SourceAudioExecutionHandle:
    source_fingerprint = source_video_fingerprint(draft)
    local_step = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .SOURCE_AUDIO_STEP_SPEC
        ),
        input_fingerprint=source_audio_step_input_fingerprint(
            source_fingerprint
        ),
    )
    return SourceAudioExecutionHandle(
        local_step=local_step,
        source_video_fingerprint=source_fingerprint,
    )


def complete_source_audio_step(
    handle: SourceAudioExecutionHandle,
    draft: VideoLocalizationDraft,
    summary: dict,
) -> SourceAudioStepOutputV1:
    if (
        source_video_fingerprint(draft)
        != handle.source_video_fingerprint
    ):
        fail_source_audio_step(handle, _INPUT_CHANGED)
        raise AppException(
            409,
            _INPUT_CHANGED,
            "抽取原音轨期间源视频发生了变化，请重新执行。",
        )
    output = source_audio_step_output_from_summary(summary)
    return managed_local_step.complete_step(
        handle.local_step,
        output,
    )


def fail_source_audio_step(
    handle: SourceAudioExecutionHandle | None,
    error_code: str,
) -> None:
    managed_local_step.fail_step(
        handle.local_step if handle is not None else None,
        str(error_code or _WRITE_ERROR),
    )


def source_video_fingerprint(
    draft: VideoLocalizationDraft,
) -> str:
    fingerprint = str(
        draft.source_media.content_sha256 or ""
    ).strip().lower()
    if (
        len(fingerprint) == 64
        and all(
            character in "0123456789abcdef"
            for character in fingerprint
        )
    ):
        return fingerprint
    video_path = Path(str(draft.source_media.video_path or ""))
    if not video_path.is_file():
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND",
            "当前项目没有可用的源视频。",
        )
    return media_assets.file_sha256(video_path)


__all__ = [
    "ARTIFACT_KEY",
    "ARTIFACT_KIND",
    "SourceAudioExecutionHandle",
    "complete_source_audio_step",
    "execute_managed_source_audio_operation",
    "fail_source_audio_step",
    "is_managed_source_audio_operation",
    "prepare_source_audio_step",
    "source_video_fingerprint",
]
