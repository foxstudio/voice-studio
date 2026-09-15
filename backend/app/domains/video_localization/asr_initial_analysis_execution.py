"""Durable three-step execution for the initial-analysis breakpoint."""

from __future__ import annotations

import threading

from app.domains.video_localization import (
    asr_pipeline,
    asr_raw_execution,
    managed_local_step,
    managed_local_workflow_specs,
    source_pipeline,
    speaker_diarization,
    transcription,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.errors import AppException
from app.schemas.video_localization_asr_initial_analysis_step import (
    ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
    AsrInitialAnalysisDiarizationOutcomeV1,
    AsrInitialAnalysisJoinOutputV1,
    initial_analysis_join_input_fingerprint,
)
from app.schemas.video_localization_speaker_diarization_step import (
    SpeakerDiarizationResultV1,
    speaker_diarization_step_input,
    speaker_diarization_step_input_fingerprint,
    speaker_diarization_step_output_from_payload,
)
from app.services import (
    asr_service,
    video_localization_operation_step_store as step_store,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
)


_DIARIZATION_FAILED = (
    "VIDEO_LOCALIZATION_INITIAL_ANALYSIS_"
    "DIARIZATION_FAILED"
)
_EXECUTION_FAILED = (
    "VIDEO_LOCALIZATION_INITIAL_ANALYSIS_EXECUTION_FAILED"
)


class _DiarizationDegraded(RuntimeError):
    pass


class _FatalInitialAnalysisFailure(RuntimeError):
    pass


def is_managed_initial_analysis_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "english_asr"
        and workflow_version
        == ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
    )


def execute_managed_initial_analysis_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    is_cancelled,
) -> asr_pipeline.AsrInitialAnalysisSnapshot:
    """Run/replay both branches, then commit a fingerprinted join."""

    from app.domains.video_localization import service

    draft = service.get_video_localization(project_id)
    if draft is None:
        raise AppException(
            404,
            "PROJECT_NOT_FOUND",
            "Project not found",
        )
    parameters = operation.parameters
    with source_pipeline.prepared_initial_analysis_requests(
        draft,
        asr_engine_id=str(parameters["engine_id"]),
        diarization_engine_id=str(
            parameters["diarization_engine_id"]
        ),
        source_track_id=str(parameters["source_track_id"]),
        source_language=str(parameters["source_language"]),
        min_speakers=parameters.get("min_speakers"),
        max_speakers=parameters.get("max_speakers"),
    ) as (raw_request, diarization_request):
        raw_handle = asr_raw_execution.prepare_asr_raw_step(
            execution_fence,
            raw_request,
            spec=(
                managed_local_workflow_specs
                .ASR_INITIAL_ANALYSIS_RAW_STEP_SPEC
            ),
        )
        diarization_input = speaker_diarization_step_input(
            audio_sha256=diarization_request.audio_sha256,
            source_track_id=(
                diarization_request.source_track_id
            ),
            requested_engine_id=(
                diarization_request.engine_id
            ),
            duration_ms=diarization_request.duration_ms,
            min_speakers=diarization_request.min_speakers,
            max_speakers=diarization_request.max_speakers,
        )
        diarization_handle = managed_local_step.prepare_step(
            execution_fence,
            spec=(
                managed_local_workflow_specs
                .ASR_INITIAL_ANALYSIS_DIARIZATION_STEP_SPEC
            ),
            input_fingerprint=(
                speaker_diarization_step_input_fingerprint(
                    diarization_input
                )
            ),
        )
        joint_analysis_enabled = (
            asr_service.supports_joint_analysis(raw_request.engine_id)
            and diarization_request.engine_id in {"auto", raw_request.engine_id}
        )
        joint_analysis_lock = threading.Lock()
        joint_analysis_result: transcription.InitialSpeechAnalysisRun | None = None

        def run_joint_analysis() -> transcription.InitialSpeechAnalysisRun:
            nonlocal joint_analysis_result
            with joint_analysis_lock:
                if joint_analysis_result is None:
                    joint_analysis_result = transcription.transcribe_raw_and_diarize(
                        raw_request,
                        diarization_request,
                        is_cancelled=is_cancelled,
                    )
                return joint_analysis_result

        def run_raw(
            request: transcription.TranscribeRawInput,
        ) -> transcription.TranscribeRawOutput:
            try:
                if raw_handle.local_step.prepared_status == "success":
                    output = managed_local_step.read_success_output(
                        raw_handle.local_step
                    )
                else:
                    result = (
                        run_joint_analysis().raw_asr
                        if joint_analysis_enabled
                        else asr_pipeline.DEFAULT_ASR_PIPELINE.run_raw_asr(
                            request,
                            context=asr_pipeline.AsrRunContext(
                                is_cancelled=is_cancelled
                            ),
                        )
                    )
                    output = (
                        asr_raw_execution
                        .complete_asr_raw_step(
                            raw_handle,
                            result.model_dump(mode="json"),
                        )
                    )
                return raw_domain_result_from_managed(
                    output.result,
                    audio_path=request.audio_path,
                )
            except (
                ExecutionFenceLost,
                ExecutionOperationCancelled,
            ):
                raise
            except AppException as exc:
                asr_raw_execution.fail_asr_raw_step(
                    raw_handle,
                    exc.code,
                )
                raise
            except Exception as exc:
                asr_raw_execution.fail_asr_raw_step(
                    raw_handle,
                    _EXECUTION_FAILED,
                )
                raise AppException(
                    500,
                    _EXECUTION_FAILED,
                    "初始分析的原始听写执行失败，请检查输入音轨后重试。",
                ) from exc

        def run_diarization(
            request: speaker_diarization.DiarizeSpeakersInput,
        ) -> speaker_diarization.DiarizeSpeakersOutput:
            if diarization_handle.prepared_status == "success":
                outcome = managed_local_step.read_success_output(
                    diarization_handle
                )
                return _diarization_domain_result_or_raise(
                    outcome,
                    audio_path=request.audio_path,
                )
            try:
                result = (
                    run_joint_analysis().diarization
                    if joint_analysis_enabled
                    else asr_pipeline.DEFAULT_ASR_PIPELINE.run_speaker_diarization(
                        request,
                        context=asr_pipeline.AsrRunContext(
                            is_cancelled=is_cancelled
                        ),
                    )
                )
                if result is None:
                    raise RuntimeError("联合 ASR 没有返回说话人结果")
            except (
                ExecutionFenceLost,
                ExecutionOperationCancelled,
            ):
                raise
            except Exception as exc:
                if is_cancelled():
                    raise ExecutionOperationCancelled(
                        "initial-analysis diarization was cancelled"
                    ) from exc
                error_code = _safe_diarization_error_code(
                    exc
                )
                outcome = (
                    AsrInitialAnalysisDiarizationOutcomeV1(
                        input=diarization_input,
                        status="degraded",
                        error_code=error_code,
                    )
                )
                try:
                    managed_local_step.complete_step(
                        diarization_handle,
                        outcome,
                    )
                except (
                    ExecutionFenceLost,
                    ExecutionOperationCancelled,
                ):
                    raise
                except Exception as write_exc:
                    raise _FatalInitialAnalysisFailure(
                        "diarization outcome was not durably saved"
                    ) from write_exc
                raise _DiarizationDegraded(error_code) from exc
            if is_cancelled():
                raise ExecutionOperationCancelled(
                    "initial-analysis diarization was cancelled"
                )
            try:
                converted = (
                    speaker_diarization_step_output_from_payload(
                        result.model_dump(mode="json")
                    )
                )
                outcome = (
                    AsrInitialAnalysisDiarizationOutcomeV1(
                        input=diarization_input,
                        status="success",
                        result=converted.result,
                    )
                )
            except (TypeError, ValueError) as exc:
                managed_local_step.fail_step(
                    diarization_handle,
                    (
                        "VIDEO_LOCALIZATION_INITIAL_ANALYSIS_"
                        "DIARIZATION_RESULT_INVALID"
                    ),
                )
                raise _FatalInitialAnalysisFailure(
                    "diarization result did not match locked input"
                ) from exc
            try:
                managed_local_step.complete_step(
                    diarization_handle,
                    outcome,
                )
            except (
                ExecutionFenceLost,
                ExecutionOperationCancelled,
            ):
                raise
            except Exception as exc:
                raise _FatalInitialAnalysisFailure(
                    "diarization result was not durably saved"
                ) from exc
            return result

        analysis = (
            asr_pipeline.DEFAULT_ASR_PIPELINE
            .run_initial_analysis(
                raw_asr=raw_request,
                diarization=diarization_request,
                context=asr_pipeline.AsrRunContext(
                    is_cancelled=is_cancelled
                ),
                raw_runner=run_raw,
                diarization_runner=run_diarization,
                is_fatal_diarization_error=(
                    _is_fatal_diarization_error
                ),
            )
        )
        if is_cancelled():
            raise ExecutionOperationCancelled(
                "initial analysis was cancelled before join"
            )
        snapshot = (
            asr_pipeline.DEFAULT_ASR_PIPELINE
            .snapshot_initial_analysis(analysis)
        )
        raw_fingerprint = _output_fingerprint(
            raw_handle.local_step
        )
        diarization_fingerprint = _output_fingerprint(
            diarization_handle
        )
        join_output = AsrInitialAnalysisJoinOutputV1(
            audio_sha256=raw_request.audio_sha256,
            source_track_id=raw_request.source_track_id,
            raw_artifact_fingerprint=raw_fingerprint,
            diarization_artifact_fingerprint=(
                diarization_fingerprint
            ),
            segments=tuple(
                snapshot.joined_transcript.segments
            ),
            warning_codes=tuple(
                str(value).strip().upper()
                for value in snapshot.joined_transcript.warnings
                if str(value).strip()
            ),
        )
        join_handle = managed_local_step.prepare_step(
            execution_fence,
            spec=(
                managed_local_workflow_specs
                .ASR_INITIAL_ANALYSIS_JOIN_STEP_SPEC
            ),
            input_fingerprint=(
                initial_analysis_join_input_fingerprint(
                    raw_artifact_fingerprint=raw_fingerprint,
                    diarization_artifact_fingerprint=(
                        diarization_fingerprint
                    ),
                )
            ),
        )
        try:
            if join_handle.prepared_status == "success":
                persisted = managed_local_step.read_success_output(
                    join_handle
                )
                if persisted != join_output:
                    raise AppException(
                        409,
                        (
                            "VIDEO_LOCALIZATION_INITIAL_ANALYSIS_"
                            "JOIN_REPLAY_MISMATCH"
                        ),
                        "初始分析汇合结果与已保存制品不一致，请重新提交任务。",
                    )
            else:
                managed_local_step.complete_step(
                    join_handle,
                    join_output,
                )
        except (
            ExecutionFenceLost,
            ExecutionOperationCancelled,
        ):
            raise
        except AppException:
            raise
        except Exception as exc:
            managed_local_step.fail_step(
                join_handle,
                (
                    "VIDEO_LOCALIZATION_INITIAL_ANALYSIS_"
                    "JOIN_FAILED"
                ),
            )
            raise AppException(
                500,
                (
                    "VIDEO_LOCALIZATION_INITIAL_ANALYSIS_"
                    "JOIN_FAILED"
                ),
                "初始分析结果未能完整汇合并保存，本次任务不会标记成功。",
            ) from exc
        return snapshot


def raw_domain_result_from_managed(
    result,
    *,
    audio_path: str,
) -> transcription.TranscribeRawOutput:
    payload = result.model_dump(mode="json")
    payload["input"] = {
        **payload["input"],
        "audio_path": audio_path,
    }
    return transcription.TranscribeRawOutput.model_validate(
        payload
    )


def diarization_domain_result_from_managed(
    result: SpeakerDiarizationResultV1,
    *,
    audio_path: str,
) -> speaker_diarization.DiarizeSpeakersOutput:
    payload = result.model_dump(mode="json")
    payload["input"] = {
        **payload["input"],
        "audio_path": audio_path,
    }
    return speaker_diarization.DiarizeSpeakersOutput.model_validate(
        payload
    )


def _diarization_domain_result_or_raise(
    outcome: AsrInitialAnalysisDiarizationOutcomeV1,
    *,
    audio_path: str,
) -> speaker_diarization.DiarizeSpeakersOutput:
    if outcome.status == "degraded":
        raise _DiarizationDegraded(
            outcome.error_code or _DIARIZATION_FAILED
        )
    if outcome.result is None:
        raise _FatalInitialAnalysisFailure(
            "successful diarization outcome has no result"
        )
    return diarization_domain_result_from_managed(
        outcome.result,
        audio_path=audio_path,
    )


def _safe_diarization_error_code(exc: Exception) -> str:
    if isinstance(exc, AppException):
        code = str(exc.code or "").strip().upper()
        if code and all(
            character.isalnum() or character == "_"
            for character in code
        ):
            return code[:160]
    return _DIARIZATION_FAILED


def _is_fatal_diarization_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            _FatalInitialAnalysisFailure,
            ExecutionFenceLost,
            ExecutionOperationCancelled,
        ),
    )


def _output_fingerprint(
    handle: managed_local_step.ManagedLocalStepHandle,
) -> str:
    step = step_store.get_step_attempt(
        handle.step_attempt_id
    )
    if (
        step is None
        or step.status != "success"
        or not step.output_fingerprint
    ):
        raise AppException(
            409,
            (
                "VIDEO_LOCALIZATION_INITIAL_ANALYSIS_"
                "BRANCH_RESULT_MISSING"
            ),
            "初始分析分支缺少已提交的结果指纹，请重新执行。",
        )
    return step.output_fingerprint


__all__ = [
    "diarization_domain_result_from_managed",
    "execute_managed_initial_analysis_operation",
    "is_managed_initial_analysis_operation",
    "raw_domain_result_from_managed",
]
