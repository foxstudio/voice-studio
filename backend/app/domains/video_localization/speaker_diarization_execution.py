"""Durable execution for standalone speaker diarization."""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.video_localization import (
    asr_pipeline,
    managed_local_step,
    managed_local_workflow_specs,
    source_pipeline,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.errors import AppException
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_WORKFLOW_VERSION,
    SpeakerDiarizationStepInputV1,
    SpeakerDiarizationStepOutputV1,
    speaker_diarization_step_input,
    speaker_diarization_step_input_fingerprint,
    speaker_diarization_step_output_from_payload,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
)


_INPUT_CHANGED = (
    "VIDEO_LOCALIZATION_SPEAKER_DIARIZATION_INPUT_CHANGED"
)
_WRITE_ERROR = (
    "VIDEO_LOCALIZATION_SPEAKER_DIARIZATION_"
    "RESULT_WRITE_FAILED"
)


@dataclass(frozen=True)
class SpeakerDiarizationExecutionHandle:
    local_step: (
        managed_local_step.ManagedLocalStepHandle[
            SpeakerDiarizationStepOutputV1
        ]
    )
    step_input: SpeakerDiarizationStepInputV1

    @property
    def execution_fence(self) -> ExecutionFence:
        return self.local_step.execution_fence

    @property
    def step_attempt_id(self) -> str:
        return self.local_step.step_attempt_id


def execute_managed_speaker_diarization_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    is_cancelled,
) -> SpeakerDiarizationStepOutputV1:
    """Run once, then replay the verified result after interruptions."""

    from app.domains.video_localization import service

    draft = service.get_video_localization(project_id)
    if draft is None:
        raise AppException(
            404,
            "PROJECT_NOT_FOUND",
            "Project not found",
        )
    parameters = operation.parameters
    with source_pipeline.prepared_speaker_diarization_request(
        draft,
        engine_id=str(
            parameters.get("engine_id") or "auto"
        ),
        source_track_id=str(
            parameters.get("source_track_id") or "auto"
        ),
        min_speakers=parameters.get("min_speakers"),
        max_speakers=parameters.get("max_speakers"),
    ) as request:
        handle = prepare_speaker_diarization_step(
            execution_fence,
            request,
        )
        if handle.local_step.prepared_status == "success":
            return managed_local_step.read_success_output(
                handle.local_step
            )
        try:
            result = (
                asr_pipeline.DEFAULT_ASR_PIPELINE
                .run_speaker_diarization(
                    request,
                    context=asr_pipeline.AsrRunContext(
                        is_cancelled=is_cancelled
                    ),
                )
            )
            return complete_speaker_diarization_step(
                handle,
                result.model_dump(mode="json"),
            )
        except (
            ExecutionFenceLost,
            ExecutionOperationCancelled,
        ):
            raise
        except AppException as exc:
            fail_speaker_diarization_step(handle, exc.code)
            raise
        except Exception:
            fail_speaker_diarization_step(
                handle,
                "VIDEO_LOCALIZATION_OPERATION_FAILED",
            )
            raise


def is_managed_speaker_diarization_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "speaker_diarization"
        and workflow_version
        == SPEAKER_DIARIZATION_WORKFLOW_VERSION
    )


def prepare_speaker_diarization_step(
    execution_fence: ExecutionFence,
    request,
) -> SpeakerDiarizationExecutionHandle:
    step_input = speaker_diarization_step_input(
        audio_sha256=request.audio_sha256,
        source_track_id=request.source_track_id,
        requested_engine_id=request.engine_id,
        duration_ms=request.duration_ms,
        min_speakers=request.min_speakers,
        max_speakers=request.max_speakers,
    )
    local_step = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .SPEAKER_DIARIZATION_STEP_SPEC
        ),
        input_fingerprint=(
            speaker_diarization_step_input_fingerprint(
                step_input
            )
        ),
    )
    return SpeakerDiarizationExecutionHandle(
        local_step=local_step,
        step_input=step_input,
    )


def complete_speaker_diarization_step(
    handle: SpeakerDiarizationExecutionHandle,
    payload: dict,
) -> SpeakerDiarizationStepOutputV1:
    output = speaker_diarization_step_output_from_payload(
        payload
    )
    result_input = output.result.input
    if (
        result_input.audio_sha256
        != handle.step_input.audio_sha256
        or result_input.source_track_id
        != handle.step_input.source_track_id
        or result_input.engine_id
        != handle.step_input.requested_engine_id
        or result_input.duration_ms
        != handle.step_input.duration_ms
        or result_input.min_speakers
        != handle.step_input.min_speakers
        or result_input.max_speakers
        != handle.step_input.max_speakers
    ):
        fail_speaker_diarization_step(
            handle,
            _INPUT_CHANGED,
        )
        raise AppException(
            409,
            _INPUT_CHANGED,
            "说话人区分的实际输入与已锁定输入不一致，请重新执行。",
        )
    return managed_local_step.complete_step(
        handle.local_step,
        output,
    )


def fail_speaker_diarization_step(
    handle: SpeakerDiarizationExecutionHandle | None,
    error_code: str,
) -> None:
    managed_local_step.fail_step(
        handle.local_step if handle is not None else None,
        str(error_code or _WRITE_ERROR),
    )


__all__ = [
    "SpeakerDiarizationExecutionHandle",
    "complete_speaker_diarization_step",
    "execute_managed_speaker_diarization_operation",
    "fail_speaker_diarization_step",
    "is_managed_speaker_diarization_operation",
    "prepare_speaker_diarization_step",
]
