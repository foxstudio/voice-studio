"""Managed execution for the section-review development node."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock

from app.domains.video_localization import (
    asr_document_understanding_result_reader,
    asr_entity_normalization_result_reader,
    asr_research_evidence_result_reader,
    asr_section_review_managed_contracts as managed_contracts,
    asr_section_review_provider_gateway as provider_gateway,
    managed_artifact_files,
    managed_local_step,
    managed_local_workflow_specs,
    section_review,
    transcript_boundary_issues,
    transcript_text_rules,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.errors import AppException
from app.schemas.video_localization_asr_section_review_step import (
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
    AsrSectionReviewCallInputV1,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrSectionReviewDetailParametersV1,
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


def is_managed_section_review_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "english_asr"
        and workflow_version
        == ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
    )


def section_review_behavior_fingerprint() -> str:
    """Hash the prompt, deterministic rules, parsing and typed contracts."""

    source_objects = (
        section_review.SectionReviewService.run,
        section_review.SectionReviewService._jobs,
        section_review.build_section_review_input,
        section_review._complete_json,
        section_review._parse_issues,
        section_review._dedupe_issues,
        section_review._section_issue_from_boundary,
        section_review._section_issue_from_text_candidate,
        transcript_boundary_issues.find_transcript_boundary_issues,
        transcript_text_rules.find_transcript_text_candidates,
    )
    payload = {
        "workflow_version": (
            ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
        ),
        "prompt_version": section_review.PROMPT_VERSION,
        "overlap_segments": section_review.REVIEW_OVERLAP_SEGMENTS,
        "implementation": {
            f"{value.__module__}.{value.__qualname__}": hashlib.sha256(
                inspect.getsource(value).encode("utf-8")
            ).hexdigest()
            for value in source_objects
        },
        "contracts": {
            "request": (
                section_review.AsrSectionReviewInput.model_json_schema()
            ),
            "call_input": (
                AsrSectionReviewCallInputV1.model_json_schema()
            ),
            "prepared_input": (
                managed_contracts.AsrSectionReviewPreparedInputV1
                .model_json_schema()
            ),
            "step_output": (
                managed_contracts.AsrSectionReviewStepOutputV1
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


def execute_managed_section_review_operation(
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
) -> section_review.AsrSectionReviewResult:
    """Rebuild both immutable upstreams and execute review round one."""

    if not is_managed_section_review_operation(
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
            "任务不是当前分段复查工作流，已拒绝执行。",
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
                != ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
                or not isinstance(
                    detail_record.core.parameters,
                    AsrSectionReviewDetailParametersV1,
                )
            ):
                raise OperationDetailRepairRequired(
                    "detail_core_mismatch"
                )
            parameters = detail_record.core.parameters
            entity = (
                asr_entity_normalization_result_reader
                .read_success_from_connection(
                    connection,
                    project_id,
                    parameters
                    .input_entity_normalization_operation_id,
                    file_backend=file_backend,
                )
            )
            document = (
                asr_document_understanding_result_reader
                .read_success_authority_from_connection(
                    connection,
                    project_id,
                    parameters
                    .input_document_understanding_operation_id,
                    file_backend=file_backend,
                )
            )
            if entity is None or document is None:
                raise OperationDetailRepairRequired(
                    "upstream_operation_missing"
                )
            research = (
                asr_research_evidence_result_reader
                .read_success_from_connection(
                    connection,
                    project_id,
                    entity.result.input.upstream_operation_id,
                    file_backend=file_backend,
                )
            )
            if research is None:
                raise OperationDetailRepairRequired(
                    "upstream_chain_missing"
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
            "分段复查任务的新权威输入缺失或损坏，请先运行任务详情审计。",
            {"issue_codes": list(issue_codes)},
        ) from exc
    if (
        entity.final_artifact_fingerprint
        != parameters.entity_artifact_fingerprint
        or document.final_artifact_fingerprint
        != parameters.document_artifact_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SECTION_REVIEW_UPSTREAM_CHANGED",
            "分段复查的上游结果与提交时锁定的结果不一致。",
        )
    if (
        research.result.input.upstream_operation_id
        != parameters.input_document_understanding_operation_id
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SECTION_REVIEW_UPSTREAM_CHAIN_CHANGED",
            "名称统一与全文理解结果不属于同一条任务链。",
        )
    if (
        parameters.behavior_fingerprint
        != section_review_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SECTION_REVIEW_BEHAVIOR_CHANGED",
            "分段复查实现已升级，请从名称统一结果重新提交任务。",
        )
    try:
        request = section_review.build_section_review_input(
            entity.result,
            document.result,
            normalization_operation_id=(
                parameters.input_entity_normalization_operation_id
            ),
            understanding_operation_id=(
                parameters.input_document_understanding_operation_id
            ),
            profile_id=parameters.profile_id,
        )
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SECTION_REVIEW_UPSTREAM_MISMATCH",
            "名称统一和全文理解结果不能组成同一次分段复查。",
        ) from exc
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
    prepared = managed_contracts.AsrSectionReviewPreparedInputV1(
        entity_artifact_fingerprint=(
            parameters.entity_artifact_fingerprint
        ),
        document_artifact_fingerprint=(
            parameters.document_artifact_fingerprint
        ),
        profile_configuration_fingerprint=(
            parameters.profile_configuration_fingerprint
        ),
        behavior_fingerprint=parameters.behavior_fingerprint,
        request=request,
    )
    return execute_prepared_section_review(
        prepared,
        execution_fence=execution_fence,
        resolved_profile=resolved_profile,
        is_cancelled=is_cancelled,
        file_backend=file_backend,
        complete_json=(complete_json or llm_runtime.complete_json),
        clock=clock,
    )


def execute_prepared_section_review(
    prepared_input: managed_contracts.AsrSectionReviewPreparedInputV1,
    *,
    execution_fence: ExecutionFence,
    resolved_profile: llm_runtime.ResolvedProfile,
    is_cancelled,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    service: section_review.SectionReviewService = (
        section_review.DEFAULT_SECTION_REVIEW_SERVICE
    ),
    complete_json: CompleteJson = llm_runtime.complete_json,
    clock: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> section_review.AsrSectionReviewResult:
    """Lock input, reuse per-section attempts and finalize one manifest."""

    if (
        prepared_input.behavior_fingerprint
        != section_review_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SECTION_REVIEW_BEHAVIOR_CHANGED",
            "分段复查实现已升级，请重新提交任务。",
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
            "VIDEO_LOCALIZATION_SECTION_REVIEW_PROFILE_CHANGED",
            "分段复查使用的模型配置与已锁定输入不一致。",
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
            .ASR_SECTION_REVIEW_PREPARE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_SECTION_REVIEW_INPUT_CHANGED",
                "分段复查的上游输入与已保存快照不一致。",
            )
    else:
        managed_local_step.complete_step(
            prepare_handle,
            prepared_input,
        )

    known_attempts: dict[
        tuple[str, int],
        provider_gateway.KnownSectionReviewAttempt,
    ] = {}
    attempt_lock = Lock()

    def record_attempt(
        known: provider_gateway.KnownSectionReviewAttempt,
    ) -> None:
        identity = (
            known.reference.section_id,
            known.reference.attempt,
        )
        with attempt_lock:
            previous = known_attempts.get(identity)
            if previous is not None and previous != known:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_SECTION_REVIEW_CALL_CONFLICT",
                    "分段复查出现重复且内容不一致的调用记录。",
                )
            known_attempts[identity] = known

    completion = provider_gateway.ManagedSectionReviewGateway(
        execution_fence=execution_fence,
        resolved_profile=resolved_profile,
        expected_profile_configuration_fingerprint=(
            prepared_input.profile_configuration_fingerprint
        ),
        behavior_fingerprint=prepared_input.behavior_fingerprint,
        file_backend=file_backend,
        complete_json=complete_json,
        on_known_attempt=record_attempt,
        clock=clock,
    )
    result = service.run(
        prepared_input.request,
        is_cancelled=is_cancelled,
        completion_gateway=completion,
    )
    ordered = sorted(
        known_attempts.values(),
        key=lambda value: (
            value.reference.section_start_ordinal,
            value.reference.attempt,
        ),
    )
    successful = [
        item for item in ordered if item.artifact is not None
    ]
    normalized_runs = []
    for run in result.section_runs:
        section_attempts = [
            item
            for item in ordered
            if item.reference.section_id == run.section_id
        ]
        calls = [
            item
            for item in section_attempts
            if item.artifact is not None
        ]
        normalized_runs.append(
            run.model_copy(
                update={
                    "duration_ms": sum(
                        item.artifact.llm_call.duration_ms
                        for item in calls
                        if item.artifact is not None
                    ),
                    "llm_call_ids": [
                        item.artifact.llm_call.call_id
                        for item in calls
                        if item.artifact is not None
                    ],
                }
            )
        )
    deterministic_result = result.model_copy(
        update={
            "model_id": (
                successful[0].artifact.llm_call.model_id
                if successful
                and successful[0].artifact is not None
                else None
            ),
            "section_runs": normalized_runs,
            "llm_calls": [
                item.artifact.llm_call
                for item in successful
                if item.artifact is not None
            ],
            "duration_ms": sum(
                item.artifact.llm_call.duration_ms
                for item in successful
                if item.artifact is not None
            ),
        }
    )
    final_output = managed_contracts.AsrSectionReviewStepOutputV1(
        prepared_input_fingerprint=prepared_fingerprint,
        status=deterministic_result.status,
        profile_id=deterministic_result.profile_id,
        model_id=deterministic_result.model_id,
        prompt_version=deterministic_result.prompt_version,
        section_runs=tuple(deterministic_result.section_runs),
        issues=tuple(deterministic_result.issues),
        warnings=tuple(deterministic_result.warnings),
        attempts=tuple(item.reference for item in ordered),
        duration_ms=deterministic_result.duration_ms,
        quality_summary=deterministic_result.quality_summary,
    )
    try:
        managed_contracts.validate_final_against_input(
            prepared_input,
            final_output,
        )
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SECTION_REVIEW_FINAL_INVALID",
            "分段复查结果未通过区块、片段、证据或覆盖率校验。",
        ) from exc
    finalize_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .ASR_SECTION_REVIEW_FINALIZE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_SECTION_REVIEW_FINALIZE_REPLAY_MISMATCH",
                "分段复查汇合结果与已保存结果不一致，请先运行任务详情审计。",
            )
    else:
        managed_local_step.complete_step(
            finalize_handle,
            final_output,
        )
    return deterministic_result


__all__ = [
    "execute_managed_section_review_operation",
    "execute_prepared_section_review",
    "is_managed_section_review_operation",
    "section_review_behavior_fingerprint",
]
