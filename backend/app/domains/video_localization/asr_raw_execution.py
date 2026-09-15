"""Durable execution for the raw-ASR development breakpoint."""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.video_localization import (
    asr_pipeline,
    managed_local_step,
    managed_local_workflow_specs,
    source_pipeline,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.errors import AppException
from app.schemas.video_localization_asr_raw_step import (
    ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
    AsrRawStepInputV1,
    AsrRawStepOutputV1,
    asr_raw_step_input,
    asr_raw_step_input_fingerprint,
    asr_raw_step_output_from_payload,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
)


_INPUT_CHANGED = "VIDEO_LOCALIZATION_ASR_RAW_INPUT_CHANGED"
_WRITE_ERROR = (
    "VIDEO_LOCALIZATION_ASR_RAW_RESULT_WRITE_FAILED"
)


@dataclass(frozen=True)
class AsrRawExecutionHandle:
    local_step: (
        managed_local_step.ManagedLocalStepHandle[
            AsrRawStepOutputV1
        ]
    )
    step_input: AsrRawStepInputV1


def is_managed_asr_raw_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "english_asr"
        and workflow_version
        == ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION
    )


def execute_managed_asr_raw_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    is_cancelled,
) -> AsrRawStepOutputV1:
    """Run raw ASR once, then replay its verified artifact."""

    from app.domains.video_localization import service

    draft = service.get_video_localization(project_id)
    if draft is None:
        raise AppException(
            404,
            "PROJECT_NOT_FOUND",
            "Project not found",
        )
    parameters = operation.parameters
    with source_pipeline.prepared_raw_asr_request(
        draft,
        engine_id=str(parameters["engine_id"]),
        source_track_id=str(
            parameters["source_track_id"]
        ),
        source_language=str(
            parameters["source_language"]
        ),
    ) as request:
        handle = prepare_asr_raw_step(
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
                .run_raw_asr(
                    request,
                    context=asr_pipeline.AsrRunContext(
                        is_cancelled=is_cancelled
                    ),
                )
            )
            return complete_asr_raw_step(
                handle,
                result.model_dump(mode="json"),
            )
        except (
            ExecutionFenceLost,
            ExecutionOperationCancelled,
        ):
            raise
        except AppException as exc:
            fail_asr_raw_step(handle, exc.code)
            raise
        except Exception as exc:
            fail_asr_raw_step(
                handle,
                (
                    "VIDEO_LOCALIZATION_ASR_RAW_"
                    "EXECUTION_FAILED"
                ),
            )
            raise AppException(
                500,
                (
                    "VIDEO_LOCALIZATION_ASR_RAW_"
                    "EXECUTION_FAILED"
                ),
                "原始听写执行失败，请检查输入音轨和听写引擎后重试。",
            ) from exc


def prepare_asr_raw_step(
    execution_fence: ExecutionFence,
    request,
    *,
    spec=managed_local_workflow_specs.ASR_RAW_STEP_SPEC,
) -> AsrRawExecutionHandle:
    step_input = asr_raw_step_input(
        audio_sha256=request.audio_sha256,
        source_track_id=request.source_track_id,
        engine_id=request.engine_id,
        requested_language=request.requested_language,
        duration_ms=request.duration_ms,
    )
    local_step = managed_local_step.prepare_step(
        execution_fence,
        spec=spec,
        input_fingerprint=(
            asr_raw_step_input_fingerprint(step_input)
        ),
    )
    return AsrRawExecutionHandle(
        local_step=local_step,
        step_input=step_input,
    )


def complete_asr_raw_step(
    handle: AsrRawExecutionHandle,
    payload: dict,
) -> AsrRawStepOutputV1:
    try:
        output = asr_raw_step_output_from_payload(
            handle.step_input,
            payload,
        )
    except (TypeError, ValueError) as exc:
        fail_asr_raw_step(handle, _INPUT_CHANGED)
        raise AppException(
            409,
            _INPUT_CHANGED,
            "原始听写的实际结果与已锁定输入不一致，请重新执行。",
        ) from exc
    return managed_local_step.complete_step(
        handle.local_step,
        output,
    )


def fail_asr_raw_step(
    handle: AsrRawExecutionHandle | None,
    error_code: str,
) -> None:
    managed_local_step.fail_step(
        handle.local_step if handle is not None else None,
        str(error_code or _WRITE_ERROR),
    )


__all__ = [
    "AsrRawExecutionHandle",
    "complete_asr_raw_step",
    "execute_managed_asr_raw_operation",
    "fail_asr_raw_step",
    "is_managed_asr_raw_operation",
    "prepare_asr_raw_step",
]
