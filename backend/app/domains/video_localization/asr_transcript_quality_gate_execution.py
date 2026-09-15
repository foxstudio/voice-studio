"""Managed execution for the transcript-quality-gate development node."""

from __future__ import annotations

import hashlib
import inspect
import json

from app.domains.video_localization import (
    asr_pipeline,
    asr_transcript_quality_gate_managed_contracts as managed_contracts,
    asr_whole_recheck_result_reader,
    managed_artifact_files,
    managed_local_step,
    managed_local_workflow_specs,
    transcript_quality_gate,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.errors import AppException
from app.schemas.video_localization_asr_transcript_quality_gate_step import (
    ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrTranscriptQualityGateDetailParametersV1,
)
from app.services import database
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
)


def is_managed_transcript_quality_gate_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "english_asr"
        and workflow_version
        == ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
    )


def transcript_quality_gate_behavior_fingerprint() -> str:
    """Hash the pure rules and contracts that derive the gate result."""

    source_objects = (
        asr_pipeline.AsrPipeline.run_transcript_quality_gate,
        transcript_quality_gate.TranscriptQualityGateService.run,
        transcript_quality_gate.build_input,
        transcript_quality_gate.hard_alignment_blockers,
        transcript_quality_gate.blocks_alignment,
    )
    payload = {
        "workflow_version": (
            ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
        ),
        "implementation": {
            f"{value.__module__}.{value.__qualname__}": hashlib.sha256(
                inspect.getsource(value).encode("utf-8")
            ).hexdigest()
            for value in source_objects
        },
        "contracts": {
            "request": (
                transcript_quality_gate.AsrTranscriptQualityGateInput
                .model_json_schema()
            ),
            "result": (
                transcript_quality_gate.AsrTranscriptQualityGateResult
                .model_json_schema()
            ),
            "prepared_input": (
                managed_contracts
                .AsrTranscriptQualityGatePreparedInputV1
                .model_json_schema()
            ),
            "step_output": (
                managed_contracts
                .AsrTranscriptQualityGateStepOutputV1
                .model_json_schema()
            ),
        },
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def execute_managed_transcript_quality_gate_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
) -> transcript_quality_gate.AsrTranscriptQualityGateResult:
    """Rebuild the exact locked whole-recheck result and run the local gate."""

    workflow_version = str(
        operation.result_summary.get("workflow_schema_version") or ""
    ).strip()
    if not is_managed_transcript_quality_gate_operation(
        operation.kind,
        workflow_version,
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前进入校时前检查工作流，已拒绝执行。",
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
                or detail_record.core.workflow_version
                != ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
                or not isinstance(
                    detail_record.core.parameters,
                    AsrTranscriptQualityGateDetailParametersV1,
                )
            ):
                raise OperationDetailRepairRequired(
                    "detail_core_mismatch"
                )
            parameters = detail_record.core.parameters
            whole_authority = (
                asr_whole_recheck_result_reader
                .read_result_from_connection(
                    connection,
                    project_id,
                    parameters.input_whole_recheck_operation_id,
                    file_backend=file_backend,
                )
            )
            if whole_authority is None:
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
            if isinstance(exc, OperationDetailRepairRequired)
            else ("detail_core_invalid",)
        )
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            "进入校时前检查的新权威输入缺失或损坏，请先运行任务详情审计。",
            {"issue_codes": list(issue_codes)},
        ) from exc
    if (
        whole_authority.final_artifact_fingerprint
        != parameters.whole_recheck_artifact_fingerprint
    ):
        raise AppException(
            409,
            (
                "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_"
                "UPSTREAM_CHANGED"
            ),
            "进入校时前检查的全文复核结果与提交时锁定的结果不一致。",
        )
    if (
        parameters.behavior_fingerprint
        != transcript_quality_gate_behavior_fingerprint()
    ):
        raise AppException(
            409,
            (
                "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_"
                "BEHAVIOR_CHANGED"
            ),
            "进入校时前检查规则已升级，请从全文复核结果重新提交任务。",
        )
    whole_result = whole_authority.result
    try:
        request = transcript_quality_gate.build_input(
            whole_result,
            upstream_operation_id=(
                parameters.input_whole_recheck_operation_id
            ),
            source_track_id=whole_result.input.source_track_id,
            source_audio_sha256=(
                whole_result.input.source_audio_sha256
            ),
            segments=whole_result.input.segments,
        )
    except ValueError as exc:
        raise AppException(
            409,
            (
                "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_"
                "INPUT_MISMATCH"
            ),
            str(exc),
        ) from exc
    prepared = (
        managed_contracts.AsrTranscriptQualityGatePreparedInputV1(
            whole_recheck_artifact_fingerprint=(
                parameters.whole_recheck_artifact_fingerprint
            ),
            behavior_fingerprint=parameters.behavior_fingerprint,
            request=request,
        )
    )
    return execute_prepared_transcript_quality_gate(
        prepared,
        execution_fence=execution_fence,
    )


def execute_prepared_transcript_quality_gate(
    prepared_input: (
        managed_contracts.AsrTranscriptQualityGatePreparedInputV1
    ),
    *,
    execution_fence: ExecutionFence,
    pipeline: asr_pipeline.AsrPipeline = (
        asr_pipeline.DEFAULT_ASR_PIPELINE
    ),
) -> transcript_quality_gate.AsrTranscriptQualityGateResult:
    """Persist deterministic input and result, replaying either local step."""

    if (
        prepared_input.behavior_fingerprint
        != transcript_quality_gate_behavior_fingerprint()
    ):
        raise AppException(
            409,
            (
                "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_"
                "BEHAVIOR_CHANGED"
            ),
            "进入校时前检查规则已升级，请重新提交任务。",
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
            .ASR_TRANSCRIPT_QUALITY_GATE_PREPARE_STEP_SPEC
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
                (
                    "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_"
                    "INPUT_CHANGED"
                ),
                "进入校时前检查的上游输入与已保存快照不一致。",
            )
    else:
        managed_local_step.complete_step(
            prepare_handle,
            prepared_input,
        )

    deterministic_result = pipeline.run_transcript_quality_gate(
        prepared_input.request
    ).model_copy(update={"duration_ms": 0})
    final_output = (
        managed_contracts.AsrTranscriptQualityGateStepOutputV1(
            prepared_input_fingerprint=prepared_fingerprint,
            result=deterministic_result,
        )
    )
    try:
        managed_contracts.validate_final_against_input(
            prepared_input,
            final_output,
        )
    except ValueError as exc:
        raise AppException(
            409,
            (
                "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_"
                "FINAL_INVALID"
            ),
            "进入校时前检查结果未通过输入、结论或只读规则校验。",
        ) from exc
    finalize_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .ASR_TRANSCRIPT_QUALITY_GATE_FINALIZE_STEP_SPEC
        ),
        input_fingerprint=hashlib.sha256(
            managed_contracts.step_output_bytes(final_output)
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
                    "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_"
                    "FINALIZE_REPLAY_MISMATCH"
                ),
                "进入校时前检查结果与已保存结果不一致，请先运行任务详情审计。",
            )
    else:
        managed_local_step.complete_step(
            finalize_handle,
            final_output,
        )
    return deterministic_result


__all__ = [
    "execute_managed_transcript_quality_gate_operation",
    "execute_prepared_transcript_quality_gate",
    "is_managed_transcript_quality_gate_operation",
    "transcript_quality_gate_behavior_fingerprint",
]
