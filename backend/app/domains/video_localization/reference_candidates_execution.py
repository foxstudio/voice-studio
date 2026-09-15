"""Durable local execution for automatic reference candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.domains.video_localization import (
    managed_local_step,
    managed_local_workflow_specs,
    media_assets,
    operation_state,
    reference_candidates_operation_projection,
    reference_clips,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    now_iso,
)
from app.errors import AppException
from app.schemas.video_localization_reference_candidates_step import (
    REFERENCE_CANDIDATES_WORKFLOW_VERSION,
    ReferenceCandidateMediaV1,
    ReferenceCandidatesStepOutputV1,
    reference_candidates_step_input_fingerprint,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
)


_INPUT_CHANGED = "VIDEO_LOCALIZATION_REFERENCE_INPUT_CHANGED"
_WRITE_ERROR = (
    "VIDEO_LOCALIZATION_REFERENCE_CANDIDATES_RESULT_WRITE_FAILED"
)


@dataclass(frozen=True)
class ReferenceCandidatesExecutionHandle:
    local_step: (
        managed_local_step.ManagedLocalStepHandle[
            ReferenceCandidatesStepOutputV1
        ]
    )
    clean_vocals_sha256: str
    candidate_revision_sha256: str

    @property
    def execution_fence(self) -> ExecutionFence:
        return self.local_step.execution_fence

    @property
    def step_attempt_id(self) -> str:
        return self.local_step.step_attempt_id


def execute_managed_reference_candidates_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    commit_guard: Callable[
        [Callable[[], VideoLocalizationDraft | None]],
        tuple[bool, VideoLocalizationDraft | None],
    ],
) -> VideoLocalizationDraft:
    from app.domains.video_localization import service

    source_draft = service.get_video_localization(project_id)
    if source_draft is None:
        raise AppException(
            404,
            "PROJECT_NOT_FOUND",
            "Project not found",
        )
    handle = prepare_reference_candidates_step(
        execution_fence,
        source_draft,
    )
    replay_output = (
        managed_local_step.read_success_output(
            handle.local_step
        )
        if handle.local_step.prepared_status == "success"
        else None
    )
    reusable_media = _reusable_media(
        project_id,
        operation.operation_id,
        replay_output,
    )
    commit_verified = False
    committed_output: ReferenceCandidatesStepOutputV1 | None = (
        None
    )

    def guarded_commit(
        action: Callable[[], VideoLocalizationDraft | None],
    ) -> tuple[bool, VideoLocalizationDraft | None]:
        committed, result = commit_guard(action)
        if not committed:
            raise ExecutionFenceLost(
                "reference candidate commit fence was lost"
            )
        return committed, result

    def commit_result(
        candidate_draft: VideoLocalizationDraft,
        candidate_result: (
            reference_clips
            .AutomaticReferenceCandidateResult
        ),
    ) -> VideoLocalizationDraft:
        nonlocal commit_verified, committed_output
        committed_output = complete_reference_candidates_step(
            handle,
            candidate_draft,
            candidate_result,
        )
        commit_verified = True
        return operation_state.with_operation_updates(
            candidate_draft,
            operation.operation_id,
            {
                "status": "success",
                "progress": 1.0,
                "completed_at": now_iso(),
                "result_summary": (
                    reference_candidates_operation_projection
                    .success_summary(committed_output)
                ),
            },
            kind="reference_clips",
        )

    try:
        updated, _candidate_result = (
            service.run_automatic_reference_candidates(
                project_id,
                commit_transform=commit_result,
                commit_guard=guarded_commit,
                operation_id=operation.operation_id,
                reusable_media=reusable_media,
                preserve_generated_on_failure=(
                    lambda: commit_verified
                ),
            )
        )
    except (ExecutionFenceLost, ExecutionOperationCancelled):
        raise
    except AppException as exc:
        if commit_verified or replay_output is not None:
            _cleanup_owned_outputs(
                project_id,
                operation.operation_id,
                committed_output or replay_output,
            )
        fail_reference_candidates_step(handle, exc.code)
        raise
    except Exception:
        if commit_verified or replay_output is not None:
            _cleanup_owned_outputs(
                project_id,
                operation.operation_id,
                committed_output or replay_output,
            )
        fail_reference_candidates_step(
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


def is_managed_reference_candidates_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "reference_clips"
        and workflow_version
        == REFERENCE_CANDIDATES_WORKFLOW_VERSION
    )


def prepare_reference_candidates_step(
    execution_fence: ExecutionFence,
    draft: VideoLocalizationDraft,
) -> ReferenceCandidatesExecutionHandle:
    clean_vocals_sha256 = (
        reference_clips.clean_vocals_content_fingerprint(
            draft
        )
    )
    candidate_revision_sha256 = (
        reference_clips
        .automatic_reference_candidate_revision(draft)
    )
    input_fingerprint = (
        reference_candidates_step_input_fingerprint(
            clean_vocals_sha256=clean_vocals_sha256,
            candidate_revision_sha256=(
                candidate_revision_sha256
            ),
            candidates=(
                reference_clips
                .automatic_candidate_cue_identities(draft)
            ),
        )
    )
    local_step = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .REFERENCE_CANDIDATES_STEP_SPEC
        ),
        input_fingerprint=input_fingerprint,
    )
    return ReferenceCandidatesExecutionHandle(
        local_step=local_step,
        clean_vocals_sha256=clean_vocals_sha256,
        candidate_revision_sha256=(
            candidate_revision_sha256
        ),
    )


def fail_reference_candidates_step(
    handle: ReferenceCandidatesExecutionHandle | None,
    error_code: str,
) -> None:
    managed_local_step.fail_step(
        handle.local_step if handle is not None else None,
        str(error_code or _WRITE_ERROR),
    )


def complete_reference_candidates_step(
    handle: ReferenceCandidatesExecutionHandle,
    draft: VideoLocalizationDraft,
    result: reference_clips.AutomaticReferenceCandidateResult,
) -> ReferenceCandidatesStepOutputV1:
    if (
        reference_clips.clean_vocals_content_fingerprint(
            draft
        )
        != handle.clean_vocals_sha256
    ):
        fail_reference_candidates_step(
            handle,
            _INPUT_CHANGED,
        )
        raise AppException(
            409,
            _INPUT_CHANGED,
            "生成参考音期间干净人声发生了变化，请重新执行。",
        )
    return managed_local_step.complete_step(
        handle.local_step,
        _step_output(result),
    )


def _step_output(
    result: reference_clips.AutomaticReferenceCandidateResult,
) -> ReferenceCandidatesStepOutputV1:
    media = tuple(
        ReferenceCandidateMediaV1(
            reference_clip_id=item.reference_clip_id,
            cue_id=item.cue_id,
            speaker_id=item.speaker_id,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            duration_ms=item.duration_ms,
            audio_sha256=item.audio_sha256,
        )
        for item in result.media
    )
    return ReferenceCandidatesStepOutputV1(
        candidate_clip_count=result.candidate_clip_count,
        generated_clip_count=len(media),
        linked_cue_count=result.linked_cue_count,
        media_status="available",
        generated_media=media,
    )


def _reusable_media(
    project_id: str,
    operation_id: str,
    output: ReferenceCandidatesStepOutputV1 | None,
) -> tuple[reference_clips.AutomaticReferenceMedia, ...]:
    if output is None:
        return ()
    reusable: list[
        reference_clips.AutomaticReferenceMedia
    ] = []
    for item in output.generated_media:
        path = media_assets.automatic_reference_clip_path(
            project_id,
            operation_id,
            item.reference_clip_id,
        )
        if (
            not path.is_file()
            or media_assets.file_sha256(path)
            != item.audio_sha256
        ):
            continue
        reusable.append(
            reference_clips.AutomaticReferenceMedia(
                reference_clip_id=item.reference_clip_id,
                cue_id=item.cue_id,
                speaker_id=item.speaker_id,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                duration_ms=item.duration_ms,
                audio_path=path,
                audio_sha256=item.audio_sha256,
            )
        )
    return tuple(reusable)


def _cleanup_owned_outputs(
    project_id: str,
    operation_id: str,
    output: ReferenceCandidatesStepOutputV1 | None,
) -> None:
    if output is None:
        return
    for item in output.generated_media:
        media_assets.automatic_reference_clip_path(
            project_id,
            operation_id,
            item.reference_clip_id,
        ).unlink(missing_ok=True)


__all__ = [
    "ReferenceCandidatesExecutionHandle",
    "complete_reference_candidates_step",
    "execute_managed_reference_candidates_operation",
    "fail_reference_candidates_step",
    "is_managed_reference_candidates_operation",
    "prepare_reference_candidates_step",
]
