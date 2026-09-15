"""Managed execution for the entity-normalization development node."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_entity_normalization_managed_contracts as managed_contracts,
    asr_entity_normalization_provider_gateway as provider_gateway,
    asr_flow,
    asr_research_evidence_result_reader,
    entity_normalization,
    entity_variant_safety,
    managed_artifact_files,
    managed_local_step,
    managed_local_workflow_specs,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.errors import AppException
from app.schemas.video_localization_asr_entity_normalization_step import (
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
    AsrEntityNormalizationCallInputV1,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrEntityNormalizationDetailParametersV1,
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


CompleteJson = Callable[..., dict | list]


def is_managed_entity_normalization_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "english_asr"
        and workflow_version
        == ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
    )


def entity_normalization_behavior_fingerprint() -> str:
    """Hash prompts, evidence rules, replacement safety and contracts."""

    source_objects = (
        entity_normalization.EntityNormalizationService.run,
        entity_normalization._qualified_visual_evidence,
        entity_normalization.requires_model_call,
        asr_flow.normalize_researched_entities,
        asr_flow._resolve_researched_entities,
        asr_flow._map_resolved_entity_variants,
        asr_flow._apply_resolved_entity_variants,
        asr_flow._apply_explicit_glossary,
        asr_flow._evidence_supports_replacement,
        asr_flow._visual_evidence_supports_resolution,
        asr_flow._canonical_replacement_preserving_version,
        entity_variant_safety.is_safe_proper_name_replacement,
    )
    payload = {
        "workflow_version": (
            ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
        ),
        "prompt_version": entity_normalization.PROMPT_VERSION,
        "implementation": {
            f"{value.__module__}.{value.__qualname__}": hashlib.sha256(
                inspect.getsource(value).encode("utf-8")
            ).hexdigest()
            for value in source_objects
        },
        "contracts": {
            "request": (
                entity_normalization.AsrEntityNormalizationInput
                .model_json_schema()
            ),
            "call_input": (
                AsrEntityNormalizationCallInputV1.model_json_schema()
            ),
            "prepared_input": (
                managed_contracts
                .AsrEntityNormalizationPreparedInputV1
                .model_json_schema()
            ),
            "step_output": (
                managed_contracts
                .AsrEntityNormalizationStepOutputV1
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


def execute_managed_entity_normalization_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    is_cancelled,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    complete_json: CompleteJson | None = None,
    clock: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> entity_normalization.AsrEntityNormalizationResult:
    """Rebuild immutable research/glossary input and execute it."""

    if not is_managed_entity_normalization_operation(
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
            "任务不是当前名称统一工作流，已拒绝执行。",
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
                != ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
                or not isinstance(
                    detail_record.core.parameters,
                    AsrEntityNormalizationDetailParametersV1,
                )
            ):
                raise OperationDetailRepairRequired(
                    "detail_core_mismatch"
                )
            parameters = detail_record.core.parameters
            research = (
                asr_research_evidence_result_reader
                .read_success_from_connection(
                    connection,
                    project_id,
                    parameters.input_research_evidence_operation_id,
                    file_backend=file_backend,
                )
            )
            if research is None:
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
            "名称与术语统一任务的新权威输入缺失或损坏，请先运行任务详情审计。",
            {"issue_codes": list(issue_codes)},
        ) from exc
    if (
        research.final_artifact_fingerprint
        != parameters.research_artifact_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_UPSTREAM_CHANGED",
            "上游资料查询结果与提交时锁定的结果不一致。",
        )
    if (
        parameters.glossary_fingerprint
        != managed_contracts.glossary_fingerprint(
            parameters.glossary
        )
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_GLOSSARY_CHANGED",
            "名称与术语统一任务锁定的词表已损坏。",
        )
    if (
        parameters.behavior_fingerprint
        != entity_normalization_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_BEHAVIOR_CHANGED",
            "名称与术语统一实现已升级，请从资料查询结果重新提交任务。",
        )
    request = (
        entity_normalization.AsrEntityNormalizationInput
        .from_research_evidence(
            research.result,
            upstream_operation_id=(
                parameters.input_research_evidence_operation_id
            ),
            glossary=list(parameters.glossary),
        )
        .model_copy(update={"profile_id": parameters.profile_id})
    )
    resolved_profile = None
    if request.profile_id:
        try:
            resolved_profile = llm_runtime.resolve_profile(
                request.profile_id
            )
        except llm_runtime.LlmRuntimeError as exc:
            raise AppException(
                exc.status_code,
                exc.code,
                str(exc),
            ) from exc
    prepared = (
        managed_contracts.AsrEntityNormalizationPreparedInputV1(
            research_artifact_fingerprint=(
                parameters.research_artifact_fingerprint
            ),
            glossary_fingerprint=parameters.glossary_fingerprint,
            profile_configuration_fingerprint=(
                parameters.profile_configuration_fingerprint
            ),
            behavior_fingerprint=parameters.behavior_fingerprint,
            request=request,
        )
    )
    return execute_prepared_entity_normalization(
        prepared,
        execution_fence=execution_fence,
        resolved_profile=resolved_profile,
        is_cancelled=is_cancelled,
        file_backend=file_backend,
        complete_json=(complete_json or llm_runtime.complete_json),
        clock=clock,
    )


def execute_prepared_entity_normalization(
    prepared_input: (
        managed_contracts.AsrEntityNormalizationPreparedInputV1
    ),
    *,
    execution_fence: ExecutionFence,
    resolved_profile: llm_runtime.ResolvedProfile | None,
    is_cancelled,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    service: entity_normalization.EntityNormalizationService = (
        entity_normalization.DEFAULT_ENTITY_NORMALIZATION_SERVICE
    ),
    complete_json: CompleteJson = llm_runtime.complete_json,
    clock: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> entity_normalization.AsrEntityNormalizationResult:
    """Lock input, reuse committed calls and finalize one manifest."""

    if (
        prepared_input.behavior_fingerprint
        != entity_normalization_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_BEHAVIOR_CHANGED",
            "名称与术语统一实现已升级，请重新提交任务。",
        )
    needs_model = entity_normalization.requires_model_call(
        prepared_input.request
    )
    if needs_model != bool(resolved_profile):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_PROFILE_CHANGED",
            "名称与术语统一任务的模型身份与已锁定输入不一致。",
        )
    if resolved_profile is not None:
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
                "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_PROFILE_CHANGED",
                "名称与术语统一使用的模型配置与已锁定输入不一致。",
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
            .ASR_ENTITY_NORMALIZATION_PREPARE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_INPUT_CHANGED",
                "名称与术语统一的上游输入与已保存快照不一致。",
            )
    else:
        managed_local_step.complete_step(
            prepare_handle,
            prepared_input,
        )

    committed_calls: list[
        provider_gateway.CommittedEntityNormalizationCall
    ] = []

    def record_call(
        committed: (
            provider_gateway.CommittedEntityNormalizationCall
        ),
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
                        "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_CALL_CONFLICT",
                        "名称与术语统一出现重复且内容不一致的调用记录。",
                    )
                return
        committed_calls.append(committed)

    completion = (
        provider_gateway.ManagedEntityNormalizationGateway(
            execution_fence=execution_fence,
            resolved_profile=resolved_profile,
            expected_profile_configuration_fingerprint=(
                prepared_input.profile_configuration_fingerprint
                or ""
            ),
            behavior_fingerprint=(
                prepared_input.behavior_fingerprint
            ),
            candidate_ids=tuple(
                item.candidate_id
                for item in prepared_input.request.candidates
            ),
            file_backend=file_backend,
            complete_json=complete_json,
            on_committed_call=record_call,
            clock=clock,
        )
        if resolved_profile is not None
        else None
    )
    result = service.run(
        prepared_input.request,
        is_cancelled=is_cancelled,
        completion_gateway=completion,
    )
    calls = sorted(
        committed_calls,
        key=lambda value: (
            value.reference.call_id,
            value.reference.attempt,
        ),
    )
    deterministic_result = result.model_copy(
        update={
            "model_id": (
                calls[0].artifact.llm_call.model_id
                if calls
                else None
            ),
            "llm_calls": [
                item.artifact.llm_call for item in calls
            ],
            "duration_ms": sum(
                item.artifact.llm_call.duration_ms
                for item in calls
            ),
        }
    )
    final_output = (
        managed_contracts.AsrEntityNormalizationStepOutputV1(
            prepared_input_fingerprint=prepared_fingerprint,
            status=deterministic_result.status,
            profile_id=deterministic_result.profile_id,
            model_id=deterministic_result.model_id,
            prompt_version=deterministic_result.prompt_version,
            resolutions=tuple(deterministic_result.resolutions),
            updated_segments=tuple(
                deterministic_result.updated_segments
            ),
            changes=tuple(deterministic_result.changes),
            warnings=tuple(deterministic_result.warnings),
            calls=tuple(item.reference for item in calls),
            duration_ms=deterministic_result.duration_ms,
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
            "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_FINAL_INVALID",
            "名称与术语统一结果未通过输入、证据或替换安全校验。",
        ) from exc
    finalize_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .ASR_ENTITY_NORMALIZATION_FINALIZE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_FINALIZE_REPLAY_MISMATCH",
                "名称与术语统一汇合结果与已保存结果不一致，请先运行任务详情审计。",
            )
    else:
        managed_local_step.complete_step(
            finalize_handle,
            final_output,
        )
    return deterministic_result


__all__ = [
    "entity_normalization_behavior_fingerprint",
    "execute_managed_entity_normalization_operation",
    "execute_prepared_entity_normalization",
    "is_managed_entity_normalization_operation",
]
