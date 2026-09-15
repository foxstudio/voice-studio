"""Managed execution for the ASR whole-recheck development node."""

from __future__ import annotations

import hashlib
import inspect
import json

from app.domains.video_localization import (
    asr_document_understanding_result_reader,
    asr_review_decisions_result_reader,
    asr_section_review_result_reader,
    asr_whole_recheck_managed_contracts as managed_contracts,
    managed_artifact_files,
    managed_local_step,
    managed_local_workflow_specs,
    transcript_boundary_issues,
    whole_recheck,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.errors import AppException
from app.schemas.video_localization_asr_whole_recheck_step import (
    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrWholeRecheckDetailParametersV1,
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
)


def is_managed_whole_recheck_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "english_asr"
        and workflow_version
        == ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
    )


def whole_recheck_behavior_fingerprint() -> str:
    """Hash the local read-only closure rules and contracts."""

    source_objects = (
        whole_recheck.WholeRecheckService.run,
        whole_recheck._normalize_unresolved_items,
        transcript_boundary_issues.find_transcript_boundary_issues,
    )
    payload = {
        "workflow_version": (
            ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
        ),
        "prompt_version": whole_recheck.PROMPT_VERSION,
        "implementation": {
            f"{value.__module__}.{value.__qualname__}": hashlib.sha256(
                inspect.getsource(value).encode("utf-8")
            ).hexdigest()
            for value in source_objects
        },
        "contracts": {
            "request": (
                whole_recheck.AsrWholeRecheckInput.model_json_schema()
            ),
            "result": (
                whole_recheck.AsrWholeRecheckResult.model_json_schema()
            ),
            "prepared_input": (
                managed_contracts.AsrWholeRecheckPreparedInputV1
                .model_json_schema()
            ),
            "step_output": (
                managed_contracts.AsrWholeRecheckStepOutputV1
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


def execute_managed_whole_recheck_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    is_cancelled,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
) -> whole_recheck.AsrWholeRecheckResult:
    """Rebuild the two locked upstream results and execute recheck."""

    if not is_managed_whole_recheck_operation(
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
            "任务不是当前全文复核工作流，已拒绝执行。",
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
                != ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
                or not isinstance(
                    detail_record.core.parameters,
                    AsrWholeRecheckDetailParametersV1,
                )
            ):
                raise OperationDetailRepairRequired(
                    "detail_core_mismatch"
                )
            parameters = detail_record.core.parameters
            decisions = (
                asr_review_decisions_result_reader
                .read_result_from_connection(
                    connection,
                    project_id,
                    parameters.input_review_decisions_operation_id,
                    file_backend=file_backend,
                )
            )
            understanding = (
                asr_document_understanding_result_reader
                .read_success_authority_from_connection(
                    connection,
                    project_id,
                    parameters
                    .input_document_understanding_operation_id,
                    file_backend=file_backend,
                )
            )
            if decisions is None or understanding is None:
                raise OperationDetailRepairRequired(
                    "upstream_operation_missing"
                )
            section = (
                asr_section_review_result_reader
                .read_result_from_connection(
                    connection,
                    project_id,
                    decisions.result.input.upstream_operation_id,
                    file_backend=file_backend,
                )
            )
            if (
                section is None
                or section.result.input.understanding_operation_id
                != parameters
                .input_document_understanding_operation_id
            ):
                raise OperationDetailRepairRequired(
                    "upstream_lineage_mismatch"
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
            "全文复核任务的新权威输入缺失或损坏，请先运行任务详情审计。",
            {"issue_codes": list(issue_codes)},
        ) from exc
    if (
        decisions.final_artifact_fingerprint
        != parameters.review_decisions_artifact_fingerprint
        or understanding.final_artifact_fingerprint
        != parameters.document_understanding_artifact_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_WHOLE_RECHECK_UPSTREAM_CHANGED",
            "全文复核的上游结果与提交时锁定的结果不一致。",
        )
    if (
        parameters.behavior_fingerprint
        != whole_recheck_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_WHOLE_RECHECK_BEHAVIOR_CHANGED",
            "全文复核实现已升级，请从修改汇总结果重新提交任务。",
        )
    try:
        request = whole_recheck.build_whole_recheck_input(
            decisions.result,
            understanding.result,
            upstream_operation_id=(
                parameters.input_review_decisions_operation_id
            ),
            understanding_operation_id=(
                parameters.input_document_understanding_operation_id
            ),
            profile_id=parameters.profile_id,
        )
        resolved_profile = llm_runtime.resolve_profile(
            request.profile_id
        )
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_WHOLE_RECHECK_INPUT_MISMATCH",
            str(exc),
        ) from exc
    except llm_runtime.LlmRuntimeError as exc:
        raise AppException(
            exc.status_code,
            exc.code,
            str(exc),
        ) from exc
    prepared = managed_contracts.AsrWholeRecheckPreparedInputV1(
        review_decisions_artifact_fingerprint=(
            parameters.review_decisions_artifact_fingerprint
        ),
        document_understanding_artifact_fingerprint=(
            parameters.document_understanding_artifact_fingerprint
        ),
        profile_configuration_fingerprint=(
            parameters.profile_configuration_fingerprint
        ),
        behavior_fingerprint=parameters.behavior_fingerprint,
        request=request,
    )
    return execute_prepared_whole_recheck(
        prepared,
        execution_fence=execution_fence,
        resolved_profile=resolved_profile,
        is_cancelled=is_cancelled,
        file_backend=file_backend,
    )


def execute_prepared_whole_recheck(
    prepared_input: (
        managed_contracts.AsrWholeRecheckPreparedInputV1
    ),
    *,
    execution_fence: ExecutionFence,
    resolved_profile: llm_runtime.ResolvedProfile,
    is_cancelled,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    service: whole_recheck.WholeRecheckService = (
        whole_recheck.DEFAULT_WHOLE_RECHECK_SERVICE
    ),
) -> whole_recheck.AsrWholeRecheckResult:
    """Lock input, reuse committed attempt and finalize one manifest."""

    if (
        prepared_input.behavior_fingerprint
        != whole_recheck_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_WHOLE_RECHECK_BEHAVIOR_CHANGED",
            "全文复核实现已升级，请重新提交任务。",
        )
    provider = provider_execution.classify_provider_profile(
        resolved_profile
    )
    if (
        prepared_input.request.profile_id
        != resolved_profile.profile_id
        or prepared_input.profile_configuration_fingerprint
        != provider.configuration_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_WHOLE_RECHECK_PROFILE_CHANGED",
            "全文复核使用的模型配置与已锁定输入不一致。",
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
            .ASR_WHOLE_RECHECK_PREPARE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_WHOLE_RECHECK_INPUT_CHANGED",
                "全文复核的上游输入与已保存快照不一致。",
            )
    else:
        managed_local_step.complete_step(
            prepare_handle,
            prepared_input,
        )

    result = service.run(
        prepared_input.request,
        is_cancelled=is_cancelled,
    )
    deterministic_result = result.model_copy(
        update={
            "model_id": None,
            "llm_calls": [],
        }
    )
    final_output = managed_contracts.AsrWholeRecheckStepOutputV1(
        prepared_input_fingerprint=prepared_fingerprint,
        result=deterministic_result,
        attempts=(),
    )
    try:
        managed_contracts.validate_final_against_input(
            prepared_input,
            final_output,
        )
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_WHOLE_RECHECK_FINAL_INVALID",
            "全文复核结果未通过调用、只读字幕或下一轮范围校验。",
        ) from exc
    finalize_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .ASR_WHOLE_RECHECK_FINALIZE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_WHOLE_RECHECK_FINALIZE_REPLAY_MISMATCH",
                "全文复核结果与已保存结果不一致，请先运行任务详情审计。",
            )
    else:
        managed_local_step.complete_step(
            finalize_handle,
            final_output,
        )
    return deterministic_result


__all__ = [
    "execute_managed_whole_recheck_operation",
    "execute_prepared_whole_recheck",
    "is_managed_whole_recheck_operation",
    "whole_recheck_behavior_fingerprint",
]
