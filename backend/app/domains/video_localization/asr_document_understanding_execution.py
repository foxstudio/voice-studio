"""Managed execution for the document-understanding development node."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_document_understanding_managed_contracts as managed_contracts,
    asr_document_understanding_provider_gateway as provider_gateway,
    asr_flow,
    asr_initial_analysis_detail_reader,
    asr_pipeline,
    document_understanding_contracts,
    managed_artifact_files,
    managed_local_step,
    managed_local_workflow_specs,
)
from app.errors import AppException
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrDocumentUnderstandingDetailParametersV1,
)
from app.services import database, llm_runtime
from app.services import (
    video_localization_llm_provider_execution as provider_execution,
)
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
)
from app.schemas.video_localization_asr_document_understanding_step import (
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
    AsrDocumentUnderstandingCallInputV1,
)


CompleteJson = Callable[..., dict | list]


def is_managed_document_understanding_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return kind == "english_asr" and workflow_version == ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION


def document_understanding_behavior_fingerprint() -> str:
    """Hash every fixed prompt/request/normalization/contract boundary."""

    source_objects = (
        asr_flow.understand_document,
        asr_flow._understand_document,
        asr_flow._understand_large_windows,
        asr_flow._complete_understanding_json,
        asr_flow._complete_json_stable,
        asr_flow._normalize_brief,
        asr_flow._brief_is_usable,
        asr_flow._normalize_sections,
        asr_flow._with_deterministic_name_visual_checks,
        asr_pipeline.AsrPipeline.run_document_understanding,
    )
    payload = {
        "behavior_version": asr_flow.PROMPT_VERSION,
        "implementation": {
            (f"{value.__module__}.{value.__qualname__}"): hashlib.sha256(
                inspect.getsource(value).encode("utf-8")
            ).hexdigest()
            for value in source_objects
        },
        "contracts": {
            "call_input": (AsrDocumentUnderstandingCallInputV1.model_json_schema()),
            "prepared_input": (managed_contracts.AsrDocumentUnderstandingPreparedInputV2.model_json_schema()),
            "step_output": (managed_contracts.AsrDocumentUnderstandingStepOutputV2.model_json_schema()),
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def execute_managed_document_understanding_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    is_cancelled,
    file_backend: ManagedArtifactFileBackend = (managed_artifact_files),
    complete_json: CompleteJson | None = None,
    clock: Callable[[], datetime] = (lambda: datetime.now(timezone.utc)),
) -> document_understanding_contracts.AsrDocumentUnderstandingResult:
    """Rebuild the locked input from managed authorities, then execute."""

    if not is_managed_document_understanding_operation(
        operation.kind,
        str(operation.result_summary.get("workflow_schema_version") or "").strip(),
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前全文理解工作流，已拒绝执行。",
        )
    try:
        with database.read_conn() as connection:
            detail_record = detail_store.get_detail_core_from_connection(
                    connection,
                    project_id,
                    operation.operation_id,
                )
            if (
                detail_record is None
                or detail_record.core.kind != "english_asr"
                or detail_record.core.workflow_version != (ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION)
                or not isinstance(
                    detail_record.core.parameters,
                    AsrDocumentUnderstandingDetailParametersV1,
                )
            ):
                raise OperationDetailRepairRequired("detail_core_mismatch")
            parameters = detail_record.core.parameters
            upstream = asr_initial_analysis_detail_reader.read_initial_analysis_success_from_connection(
                    connection,
                    project_id,
                parameters.input_initial_analysis_operation_id,
                    file_backend=file_backend,
                )
            if upstream is None:
                raise OperationDetailRepairRequired("upstream_operation_missing")
    except (
        detail_store.OperationDetailCoreIdentityConflict,
        detail_store.OperationDetailCoreIntegrityError,
        detail_store.OperationDetailCoreSchemaError,
        OperationDetailRepairRequired,
        TypeError,
        ValueError,
    ) as exc:
        issue_codes = (
            exc.issue_codes
            if isinstance(
                exc,
                OperationDetailRepairRequired,
            )
            else ("detail_core_invalid",)
        )
        raise AppException(
            409,
            ("VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED"),
            "全文理解任务的新权威输入缺失或损坏，请先运行任务详情审计。",
            {"issue_codes": list(issue_codes)},
        ) from exc
    current_behavior_fingerprint = document_understanding_behavior_fingerprint()
    if parameters.behavior_fingerprint != current_behavior_fingerprint:
        raise AppException(
            409,
            ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_BEHAVIOR_CHANGED"),
            "全文理解实现已升级，请从上游初始分析结果重新提交任务。",
        )
    try:
        resolved_profile = llm_runtime.resolve_profile(parameters.profile_id)
    except llm_runtime.LlmRuntimeError as exc:
        raise AppException(
            exc.status_code,
            exc.code,
            str(exc),
        ) from exc
    request = document_understanding_contracts.AsrDocumentUnderstandingInput.from_joined_transcript(
            upstream.snapshot.joined_transcript,
        upstream_operation_id=(parameters.input_initial_analysis_operation_id),
            profile_id=parameters.profile_id,
            scene_context=parameters.scene_context,
        )
    if not request.segments:
        raise AppException(
            409,
            ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_INPUT_EMPTY"),
            "上游初始分析没有可供全文理解的讲话片段。",
        )
    prepared_input = managed_contracts.AsrDocumentUnderstandingPreparedInputV2(
        raw_artifact_fingerprint=(upstream.raw_artifact_fingerprint),
        diarization_artifact_fingerprint=(upstream.diarization_artifact_fingerprint),
        join_artifact_fingerprint=(upstream.join_artifact_fingerprint),
        profile_configuration_fingerprint=(parameters.profile_configuration_fingerprint),
        behavior_fingerprint=(parameters.behavior_fingerprint),
            request=request,
        )
    return execute_prepared_document_understanding(
        prepared_input,
        execution_fence=execution_fence,
        resolved_profile=resolved_profile,
        file_backend=file_backend,
        is_cancelled=is_cancelled,
        complete_json=(complete_json or llm_runtime.complete_json),
        clock=clock,
    )


def execute_prepared_document_understanding(
    prepared_input: (managed_contracts.AsrDocumentUnderstandingPreparedInputV2),
    *,
    execution_fence: ExecutionFence,
    resolved_profile: llm_runtime.ResolvedProfile,
    file_backend: ManagedArtifactFileBackend,
    is_cancelled,
    pipeline: asr_pipeline.AsrPipeline = (asr_pipeline.DEFAULT_ASR_PIPELINE),
    complete_json: CompleteJson = llm_runtime.complete_json,
    clock: Callable[[], datetime] = (lambda: datetime.now(timezone.utc)),
) -> document_understanding_contracts.AsrDocumentUnderstandingResult:
    """Lock input, reuse/submit calls, then commit one deterministic result."""

    profile = provider_execution.classify_provider_profile(resolved_profile)
    if (
        prepared_input.request.profile_id != resolved_profile.profile_id
        or prepared_input.profile_configuration_fingerprint != profile.configuration_fingerprint
    ):
        raise AppException(
            409,
            ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_PROFILE_CHANGED"),
            "全文理解使用的模型配置与已锁定输入不一致，请重新提交任务。",
        )
    prepared_fingerprint = managed_contracts.prepared_input_fingerprint(prepared_input)
    prepare_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(managed_local_workflow_specs.ASR_DOCUMENT_UNDERSTANDING_PREPARE_STEP_SPEC),
        input_fingerprint=prepared_fingerprint,
    )
    if prepare_handle.prepared_status == "success":
        persisted_input = managed_local_step.read_success_output(prepare_handle)
        if persisted_input != prepared_input:
            raise AppException(
                409,
                ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_INPUT_CHANGED"),
                "全文理解的上游输入与已保存快照不一致，请重新提交任务。",
            )
    else:
        managed_local_step.complete_step(
            prepare_handle,
            prepared_input,
        )

    committed_calls: list[provider_gateway.CommittedDocumentUnderstandingCall] = []

    def record_call(
        committed: (provider_gateway.CommittedDocumentUnderstandingCall),
    ) -> None:
        identity = (
            committed.reference.call_id,
            committed.reference.attempt,
        )
        for previous in committed_calls:
            if (
                previous.reference.call_id,
                previous.reference.attempt,
            ) == identity:
                if previous != committed:
                    raise AppException(
                        409,
                        ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_CALL_CONFLICT"),
                        "全文理解调用记录出现重复且内容不一致，请先运行任务详情审计。",
                    )
                return
        committed_calls.append(committed)

    gateway = provider_gateway.ManagedDocumentUnderstandingGateway(
            execution_fence=execution_fence,
            resolved_profile=resolved_profile,
        expected_profile_configuration_fingerprint=(prepared_input.profile_configuration_fingerprint),
        behavior_fingerprint=(prepared_input.behavior_fingerprint),
            file_backend=file_backend,
            complete_json=complete_json,
            on_committed_call=record_call,
            clock=clock,
        )
    try:
        result = pipeline.run_document_understanding(
            prepared_input.request,
            context=asr_pipeline.AsrRunContext(is_cancelled=is_cancelled),
            completion_gateway=gateway,
            resolved_profile=resolved_profile,
        )
    except (
        ExecutionFenceLost,
        ExecutionOperationCancelled,
        AppException,
    ):
        raise
    except llm_runtime.LlmRuntimeError as exc:
        raise AppException(
            exc.status_code,
            ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_PROVIDER_FAILED"),
            "语言模型未能完成全文理解，请检查模型配置后重试。",
            {"provider_error_code": (provider_execution.known_provider_error_code(exc.code))},
        ) from exc
    if result.quality_summary.status != "passed":
        raise AppException(
            502,
            ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_RESULT_INVALID"),
            "全文理解结果没有连续覆盖全部讲话内容，本次任务不会标记成功。",
        )
    final_output = managed_contracts.AsrDocumentUnderstandingStepOutputV2(
            prepared_input_fingerprint=prepared_fingerprint,
            brief=result.brief,
            profile_id=result.profile_id,
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            execution_strategy=result.execution_strategy,
            window_count=result.window_count,
            llm_call_count=result.llm_call_count,
            retry_count=result.retry_count,
        duration_ms=sum(call.artifact.llm_call.duration_ms for call in committed_calls),
            quality_summary=result.quality_summary,
            warnings=tuple(result.warnings),
        calls=tuple(call.reference for call in committed_calls),
    )
    finalize_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(managed_local_workflow_specs.ASR_DOCUMENT_UNDERSTANDING_FINALIZE_STEP_SPEC),
        input_fingerprint=_finalize_input_fingerprint(final_output),
    )
    if finalize_handle.prepared_status == "success":
        persisted_output = managed_local_step.read_success_output(finalize_handle)
        if persisted_output != final_output:
            raise AppException(
                409,
                ("VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_FINALIZE_REPLAY_MISMATCH"),
                "全文理解汇合结果与已保存结果不一致，请先运行任务详情审计。",
            )
    else:
        managed_local_step.complete_step(
            finalize_handle,
            final_output,
        )
    return result.model_copy(
        update={"stage_timing": (result.stage_timing.model_copy(update={"duration_ms": final_output.duration_ms}))}
    )


def _finalize_input_fingerprint(
    output: managed_contracts.AsrDocumentUnderstandingStepOutputV2,
) -> str:
    encoded = json.dumps(
        {
            "prepared_input_fingerprint": (output.prepared_input_fingerprint),
            "calls": [call.model_dump(mode="json") for call in output.calls],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "document_understanding_behavior_fingerprint",
    "execute_managed_document_understanding_operation",
    "execute_prepared_document_understanding",
    "is_managed_document_understanding_operation",
]
