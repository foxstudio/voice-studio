"""Managed execution for the visual-evidence development node."""

from __future__ import annotations

import hashlib
import inspect
import json
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.domains.video_localization import (
    asr_visual_evidence_extraction_gateway as extraction_gateway,
    asr_visual_evidence_managed_contracts as managed_contracts,
    asr_visual_evidence_provider_gateway as provider_gateway,
    asr_document_understanding_result_reader,
    managed_artifact_files,
    managed_local_step,
    managed_local_workflow_specs,
    media_assets,
    media_health,
    service,
    visual_evidence,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.errors import AppException
from app.schemas.video_localization_asr_visual_evidence_step import (
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    AsrVisualEvidenceCallInputV1,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrVisualEvidenceDetailParametersV1,
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


CompleteMultimodalJson = Callable[..., dict | list]


def is_managed_visual_evidence_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "english_asr"
        and workflow_version
        == ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    )


def visual_evidence_behavior_fingerprint() -> str:
    """Hash fixed extraction, prompt, merge and result boundaries."""

    source_objects = (
        visual_evidence.VisualEvidenceService.run,
        visual_evidence._focus_and_prioritize_subtitle_questions,
        visual_evidence._frame_timestamps,
        visual_evidence._default_extraction_gateway,
        visual_evidence._extract_frame,
        visual_evidence._analyze_question,
        visual_evidence._second_round_reason,
        visual_evidence._localize_visual_answer_times,
        visual_evidence._merge_visual_answers,
        visual_evidence._build_result,
    )
    payload = {
        "workflow_version": (
            ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
        ),
        "implementation": {
            (
                f"{value.__module__}.{value.__qualname__}"
            ): hashlib.sha256(
                inspect.getsource(value).encode("utf-8")
            ).hexdigest()
            for value in source_objects
        },
        "contracts": {
            "request": (
                visual_evidence.AsrVisualEvidenceInput
                .model_json_schema()
            ),
            "call_input": (
                AsrVisualEvidenceCallInputV1.model_json_schema()
            ),
            "prepared_input": (
                managed_contracts.AsrVisualEvidencePreparedInputV2
                .model_json_schema()
            ),
            "extraction_output": (
                managed_contracts
                .AsrVisualEvidenceExtractionOutputV1
                .model_json_schema()
            ),
            "step_output": (
                managed_contracts.AsrVisualEvidenceStepOutputV2
                .model_json_schema()
            ),
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


def execute_managed_visual_evidence_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    is_cancelled,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    complete_multimodal_json: CompleteMultimodalJson | None = None,
    clock: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> visual_evidence.AsrVisualEvidenceResult:
    """Rebuild locked managed upstream/video input, then execute."""

    if not is_managed_visual_evidence_operation(
        operation.kind,
        str(
            operation.result_summary.get(
                "workflow_schema_version"
            )
            or ""
        ).strip(),
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前画面取证工作流，已拒绝执行。",
        )
    try:
        with database.read_conn() as connection:
            detail_record = (
                detail_store.get_detail_core_from_connection(
                    connection,
                    project_id,
                    operation.operation_id,
                )
            )
            if (
                detail_record is None
                or detail_record.core.kind != "english_asr"
                or detail_record.core.workflow_version
                != (
                    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
                )
                or not isinstance(
                    detail_record.core.parameters,
                    AsrVisualEvidenceDetailParametersV1,
                )
            ):
                raise OperationDetailRepairRequired(
                    "detail_core_mismatch"
                )
            parameters = detail_record.core.parameters
            upstream = (
                asr_document_understanding_result_reader
                .read_success_authority_from_connection(
                    connection,
                    project_id,
                    parameters
                    .input_document_understanding_operation_id,
                    file_backend=file_backend,
                )
            )
            if upstream is None:
                raise OperationDetailRepairRequired(
                    "upstream_operation_missing"
                )
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
            (
                "VIDEO_LOCALIZATION_OPERATION_"
                "DETAIL_REPAIR_REQUIRED"
            ),
            "画面取证任务的新权威输入缺失或损坏，请先运行任务详情审计。",
            {"issue_codes": list(issue_codes)},
        ) from exc
    if (
        parameters.upstream_artifact_fingerprint
        != upstream.final_artifact_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_UPSTREAM_CHANGED",
            "上游全文理解结果与提交时锁定的结果不一致，请重新提交任务。",
        )
    if (
        parameters.behavior_fingerprint
        != visual_evidence_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_BEHAVIOR_CHANGED",
            "画面证据实现已升级，请从全文理解结果重新提交任务。",
        )
    request = (
        visual_evidence.AsrVisualEvidenceInput
        .from_document_understanding(
            upstream.result,
            upstream_operation_id=(
                parameters
                .input_document_understanding_operation_id
            ),
            video_sha256=parameters.video_sha256,
            video_duration_ms=parameters.video_duration_ms,
            video_frame_rate=parameters.video_frame_rate,
            profile_id=parameters.profile_id,
            max_frames_per_question=(
                parameters.max_frames_per_question
            ),
            max_total_frames=parameters.max_total_frames,
        )
    )
    if bool(request.questions) != bool(parameters.profile_id):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_PROFILE_CHANGED",
            "画面问题与提交时锁定的模型身份不一致，请重新提交任务。",
        )
    draft = service.get_video_localization(project_id)
    if draft is None:
        raise AppException(
            404,
            "PROJECT_NOT_FOUND",
            "Project not found",
        )
    video_path = (
        media_health.inspect_project_media(draft)
        .paths.source_video
    )
    if (
        video_path is None
        or str(
            draft.source_media.content_sha256 or ""
        ).strip()
        != parameters.video_sha256
        or int(draft.source_media.duration_ms or 0)
        != parameters.video_duration_ms
        or float(draft.source_media.frame_rate or 30.0)
        != parameters.video_frame_rate
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_SOURCE_CHANGED",
            "源视频身份或时长信息在任务提交后发生了变化，请重新提交任务。",
        )
    try:
        current_sha256 = media_assets.file_sha256(video_path)
    except OSError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_SOURCE_UNAVAILABLE",
            "当前项目没有可用于画面取证的源视频。",
        ) from exc
    if current_sha256 != parameters.video_sha256:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_SOURCE_CHANGED",
            "源视频内容在任务提交后发生了变化，请重新提交任务。",
        )
    resolved_profile = None
    if parameters.profile_id is not None:
        try:
            resolved_profile = llm_runtime.resolve_profile(
                parameters.profile_id
            )
        except llm_runtime.LlmRuntimeError as exc:
            raise AppException(
                exc.status_code,
                exc.code,
                str(exc),
            ) from exc
    prepared = (
        managed_contracts.AsrVisualEvidencePreparedInputV2(
            upstream_artifact_fingerprint=(
                parameters.upstream_artifact_fingerprint
            ),
            profile_configuration_fingerprint=(
                parameters
                .profile_configuration_fingerprint
            ),
            behavior_fingerprint=(
                parameters.behavior_fingerprint
            ),
            request=request,
        )
    )
    with tempfile.TemporaryDirectory(
        prefix="voice-studio-visual-evidence-"
    ) as runtime_dir:
        return execute_prepared_visual_evidence(
            prepared,
            execution_fence=execution_fence,
            source_video_path=video_path,
            frame_dir=runtime_dir,
            resolved_profile=resolved_profile,
            is_cancelled=is_cancelled,
            file_backend=file_backend,
            complete_multimodal_json=(
                complete_multimodal_json
                or llm_runtime.complete_multimodal_json
            ),
            clock=clock,
        )


def execute_prepared_visual_evidence(
    prepared_input: managed_contracts.AsrVisualEvidencePreparedInputV2,
    *,
    execution_fence: ExecutionFence,
    source_video_path: str | Path,
    frame_dir: str | Path,
    resolved_profile: llm_runtime.ResolvedProfile | None,
    is_cancelled,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    service: visual_evidence.VisualEvidenceService = (
        visual_evidence.DEFAULT_VISUAL_EVIDENCE_SERVICE
    ),
    extractor: extraction_gateway.Extractor = (
        visual_evidence._default_extraction_gateway
    ),
    complete_multimodal_json: CompleteMultimodalJson = (
        llm_runtime.complete_multimodal_json
    ),
    clock: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> visual_evidence.AsrVisualEvidenceResult:
    """Lock input, reuse extraction/calls and commit deterministic output."""

    if (
        prepared_input.behavior_fingerprint
        != visual_evidence_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_BEHAVIOR_CHANGED",
            "画面证据实现已升级，请从全文理解结果重新提交任务。",
        )
    questions = bool(prepared_input.request.questions)
    if questions != bool(resolved_profile):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_PROFILE_CHANGED",
            "画面证据任务的模型身份与已锁定输入不一致，请重新提交任务。",
        )
    if resolved_profile is not None:
        profile = provider_execution.classify_provider_profile(
            resolved_profile
        )
        if (
            prepared_input.request.profile_id
            != resolved_profile.profile_id
            or prepared_input.profile_configuration_fingerprint
            != profile.configuration_fingerprint
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_PROFILE_CHANGED",
                "画面证据使用的模型配置与已锁定输入不一致，请重新提交任务。",
            )
    prepared_fingerprint = (
        managed_contracts.prepared_input_fingerprint(
            prepared_input
        )
    )
    prepare_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .ASR_VISUAL_EVIDENCE_PREPARE_STEP_SPEC
        ),
        input_fingerprint=prepared_fingerprint,
    )
    if prepare_handle.prepared_status == "success":
        persisted = managed_local_step.read_success_output(
            prepare_handle
        )
        if persisted != prepared_input:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_INPUT_CHANGED",
                "画面证据的上游输入与已保存快照不一致，请重新提交任务。",
            )
    else:
        managed_local_step.complete_step(
            prepare_handle,
            prepared_input,
        )

    extractions: list[
        extraction_gateway.CommittedVisualExtraction
    ] = []
    calls: list[provider_gateway.CommittedVisualEvidenceCall] = []

    def record_extraction(
        committed: extraction_gateway.CommittedVisualExtraction,
    ) -> None:
        _record_unique(
            extractions,
            committed,
            identity=lambda value: (
                value.reference.question_id,
                value.reference.round_index,
            ),
            error_code=(
                "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_"
                "EXTRACTION_CONFLICT"
            ),
        )

    def record_call(
        committed: provider_gateway.CommittedVisualEvidenceCall,
    ) -> None:
        _record_unique(
            calls,
            committed,
            identity=lambda value: (
                value.reference.question_id,
                value.reference.round_index,
            ),
            error_code=(
                "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_CALL_CONFLICT"
            ),
        )

    managed_extraction = (
        extraction_gateway.ManagedVisualExtractionGateway(
            execution_fence=execution_fence,
            video_sha256=prepared_input.request.video_sha256,
            behavior_fingerprint=(
                prepared_input.behavior_fingerprint
            ),
            file_backend=file_backend,
            extractor=extractor,
            on_committed_extraction=record_extraction,
            clock=clock,
        )
    )
    managed_completion = (
        provider_gateway.ManagedVisualEvidenceGateway(
            execution_fence=execution_fence,
            resolved_profile=resolved_profile,
            expected_profile_configuration_fingerprint=(
                prepared_input
                .profile_configuration_fingerprint
            ),
            behavior_fingerprint=(
                prepared_input.behavior_fingerprint
            ),
            frame_reference=(
                managed_extraction.frame_reference
            ),
            file_backend=file_backend,
            complete_multimodal_json=(
                complete_multimodal_json
            ),
            on_committed_call=record_call,
            clock=clock,
        )
        if resolved_profile is not None
        else None
    )
    try:
        result = service.run(
            prepared_input.request,
            source_video_path=source_video_path,
            frame_dir=frame_dir,
            is_cancelled=is_cancelled,
            extraction_gateway=managed_extraction,
            completion_gateway=managed_completion,
            image_loader=managed_extraction.image_content,
            resolved_profile=resolved_profile,
        )
    except (
        ExecutionFenceLost,
        ExecutionOperationCancelled,
        AppException,
    ):
        raise
    duration_ms = sum(
        item.artifact.llm_call.duration_ms for item in calls
    )
    frame_references = tuple(
        managed_extraction.frame_reference(frame)
        for frame in result.frames
    )
    deterministic_result = result.model_copy(
        update={
            "stage_timing": (
                result.stage_timing.model_copy(
                    update={"duration_ms": duration_ms}
                )
            )
        }
    )
    final_output = managed_contracts.AsrVisualEvidenceStepOutputV2(
        prepared_input_fingerprint=prepared_fingerprint,
        status=deterministic_result.status,
        stop_reason=deterministic_result.stop_reason,
        profile_id=deterministic_result.profile_id,
        model_id=deterministic_result.model_id,
        frames=frame_references,
        observations=tuple(
            deterministic_result.observations
        ),
        warnings=tuple(deterministic_result.warnings),
        duration_ms=duration_ms,
        quality_summary=(
            deterministic_result.quality_summary
        ),
        extractions=tuple(
            item.reference for item in extractions
        ),
        calls=tuple(item.reference for item in calls),
    )
    finalize_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .ASR_VISUAL_EVIDENCE_FINALIZE_STEP_SPEC
        ),
        input_fingerprint=hashlib.sha256(
            managed_contracts.step_output_bytes(
                final_output
            )
        ).hexdigest(),
    )
    if finalize_handle.prepared_status == "success":
        persisted = managed_local_step.read_success_output(
            finalize_handle
        )
        if persisted != final_output:
            raise AppException(
                409,
                (
                    "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_"
                    "FINALIZE_REPLAY_MISMATCH"
                ),
                "画面证据汇合结果与已保存结果不一致，请先运行任务详情审计。",
            )
    else:
        managed_local_step.complete_step(
            finalize_handle,
            final_output,
        )
    return deterministic_result


def _record_unique(
    values: list,
    committed,
    *,
    identity,
    error_code: str,
) -> None:
    key = identity(committed)
    for previous in values:
        if identity(previous) == key:
            if previous != committed:
                raise AppException(
                    409,
                    error_code,
                    "画面证据任务出现重复且内容不一致的步骤记录，请先运行任务详情审计。",
                )
            return
    values.append(committed)


__all__ = [
    "execute_managed_visual_evidence_operation",
    "execute_prepared_visual_evidence",
    "is_managed_visual_evidence_operation",
    "visual_evidence_behavior_fingerprint",
]
