"""Managed execution for the ASR review-decisions development node."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_review_decisions_managed_contracts as managed_contracts,
    asr_review_decisions_provider_gateway as provider_gateway,
    asr_targeted_relisten,
    asr_section_review_result_reader,
    managed_artifact_files,
    managed_local_step,
    managed_local_workflow_specs,
    review_decisions,
    transcript_text_rules,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
    VideoLocalizationTranscriptSegment,
)
from app.errors import AppException
from app.schemas.video_localization_asr_review_decisions_step import (
    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
    AsrReviewDecisionsCallInputV1,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrReviewDecisionsDetailParametersV1,
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
SegmentsChanged = Callable[
    [list[VideoLocalizationTranscriptSegment]],
    None,
]


def is_managed_review_decisions_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "english_asr"
        and workflow_version
        == ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
    )


def review_decisions_behavior_fingerprint() -> str:
    """Hash prompts, decision preparation, edit safety and contracts."""

    source_objects = (
        review_decisions.ReviewDecisionsService.run,
        review_decisions._complete_json,
        review_decisions._missing_issue_ids,
        review_decisions._prepare_decisions,
        review_decisions.apply_decisions,
        review_decisions._preserves_locked_changes,
        transcript_text_rules.normalize_transcript_orthography,
        asr_targeted_relisten.relisten_review_issues,
    )
    payload = {
        "workflow_version": (
            ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
        ),
        "prompt_version": review_decisions.PROMPT_VERSION,
        "implementation": {
            f"{value.__module__}.{value.__qualname__}": hashlib.sha256(
                inspect.getsource(value).encode("utf-8")
            ).hexdigest()
            for value in source_objects
        },
        "contracts": {
            "request": (
                review_decisions.AsrReviewDecisionsInput
                .model_json_schema()
            ),
            "result": (
                review_decisions.AsrReviewDecisionsResult
                .model_json_schema()
            ),
            "call_input": (
                AsrReviewDecisionsCallInputV1.model_json_schema()
            ),
            "prepared_input": (
                managed_contracts
                .AsrReviewDecisionsPreparedInputV1
                .model_json_schema()
            ),
            "step_output": (
                managed_contracts
                .AsrReviewDecisionsStepOutputV1
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


def execute_managed_review_decisions_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_fence: ExecutionFence,
    is_cancelled,
    on_segments_changed: SegmentsChanged | None = None,
    acoustic_candidates: list[
        asr_targeted_relisten.AsrAcousticCandidate
    ] | None = None,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    complete_json: CompleteJson | None = None,
    clock: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> review_decisions.AsrReviewDecisionsResult:
    """Rebuild the locked section-review input and execute it."""

    if not is_managed_review_decisions_operation(
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
            "任务不是当前复查结论工作流，已拒绝执行。",
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
                != ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
                or not isinstance(
                    detail_record.core.parameters,
                    AsrReviewDecisionsDetailParametersV1,
                )
            ):
                raise OperationDetailRepairRequired(
                    "detail_core_mismatch"
                )
            parameters = detail_record.core.parameters
            section = (
                asr_section_review_result_reader
                .read_result_from_connection(
                    connection,
                    project_id,
                    parameters.input_section_review_operation_id,
                    file_backend=file_backend,
                )
            )
            if section is None:
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
            "复查结论汇总任务的新权威输入缺失或损坏，请先运行任务详情审计。",
            {"issue_codes": list(issue_codes)},
        ) from exc
    if (
        section.final_artifact_fingerprint
        != parameters.section_review_artifact_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REVIEW_DECISIONS_UPSTREAM_CHANGED",
            "上游分段复查结果与提交时锁定的结果不一致。",
        )
    if (
        parameters.behavior_fingerprint
        != review_decisions_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REVIEW_DECISIONS_BEHAVIOR_CHANGED",
            "复查结论汇总实现已升级，请从分段复查结果重新提交任务。",
        )
    request = review_decisions.build_review_decisions_input(
        section.result,
        upstream_operation_id=(
            parameters.input_section_review_operation_id
        ),
        profile_id=parameters.profile_id,
    )
    if acoustic_candidates is not None:
        request = request.model_copy(
            update={
                "acoustic_candidates": list(acoustic_candidates)
            }
        )
    resolved_profile = None
    if request.issues:
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
        managed_contracts.AsrReviewDecisionsPreparedInputV1(
            section_review_artifact_fingerprint=(
                parameters.section_review_artifact_fingerprint
            ),
            profile_configuration_fingerprint=(
                parameters.profile_configuration_fingerprint
            ),
            behavior_fingerprint=parameters.behavior_fingerprint,
            request=request,
        )
    )
    return execute_prepared_review_decisions(
        prepared,
        execution_fence=execution_fence,
        resolved_profile=resolved_profile,
        is_cancelled=is_cancelled,
        on_segments_changed=on_segments_changed,
        file_backend=file_backend,
        complete_json=(complete_json or llm_runtime.complete_json),
        clock=clock,
    )


def execute_prepared_review_decisions(
    prepared_input: (
        managed_contracts.AsrReviewDecisionsPreparedInputV1
    ),
    *,
    execution_fence: ExecutionFence,
    resolved_profile: llm_runtime.ResolvedProfile | None,
    is_cancelled,
    on_segments_changed: SegmentsChanged | None = None,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    service: review_decisions.ReviewDecisionsService = (
        review_decisions.DEFAULT_REVIEW_DECISIONS_SERVICE
    ),
    complete_json: CompleteJson = llm_runtime.complete_json,
    clock: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> review_decisions.AsrReviewDecisionsResult:
    """Lock input, reuse committed calls and finalize one manifest."""

    if (
        prepared_input.behavior_fingerprint
        != review_decisions_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REVIEW_DECISIONS_BEHAVIOR_CHANGED",
            "复查结论汇总实现已升级，请重新提交任务。",
        )
    needs_model = bool(prepared_input.request.issues)
    if needs_model != bool(resolved_profile):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REVIEW_DECISIONS_PROFILE_CHANGED",
            "复查结论汇总的模型身份与已锁定输入不一致。",
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
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_PROFILE_CHANGED",
                "复查结论汇总使用的模型配置与已锁定输入不一致。",
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
            .ASR_REVIEW_DECISIONS_PREPARE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_INPUT_CHANGED",
                "复查结论汇总的上游输入与已保存快照不一致。",
            )
    else:
        managed_local_step.complete_step(
            prepare_handle,
            prepared_input,
        )

    known_attempts: list[
        provider_gateway.KnownReviewDecisionsAttempt
    ] = []

    def record_attempt(
        known: provider_gateway.KnownReviewDecisionsAttempt,
    ) -> None:
        identity = (
            known.reference.call_group,
            known.reference.attempt,
        )
        for previous in known_attempts:
            if (
                previous.reference.call_group,
                previous.reference.attempt,
            ) == identity:
                if previous != known:
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_REVIEW_DECISIONS_CALL_CONFLICT",
                        "复查结论汇总出现重复且内容不一致的调用记录。",
                    )
                return
        known_attempts.append(known)

    completion = (
        provider_gateway.ManagedReviewDecisionsGateway(
            execution_fence=execution_fence,
            resolved_profile=resolved_profile,
            expected_profile_configuration_fingerprint=(
                prepared_input.profile_configuration_fingerprint
                or ""
            ),
            behavior_fingerprint=(
                prepared_input.behavior_fingerprint
            ),
            expected_issue_ids=tuple(
                item.issue_id
                for item in prepared_input.request.issues
            ),
            file_backend=file_backend,
            complete_json=complete_json,
            on_known_attempt=record_attempt,
            clock=clock,
        )
        if resolved_profile is not None
        else None
    )
    result = service.run(
        prepared_input.request,
        is_cancelled=is_cancelled,
        on_segments_changed=on_segments_changed,
        completion_gateway=completion,
    )
    group_order = {"primary": 0, "coverage": 1}
    ordered = sorted(
        known_attempts,
        key=lambda value: (
            group_order[value.reference.call_group],
            value.reference.attempt,
        ),
    )
    successful = [
        item for item in ordered if item.artifact is not None
    ]
    deterministic_result = result.model_copy(
        update={
            "model_id": (
                successful[0].artifact.llm_call.model_id
                if successful
                and successful[0].artifact is not None
                else None
            ),
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
    final_output = (
        managed_contracts.AsrReviewDecisionsStepOutputV1(
            prepared_input_fingerprint=prepared_fingerprint,
            result=deterministic_result,
            attempts=tuple(item.reference for item in ordered),
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
            "VIDEO_LOCALIZATION_REVIEW_DECISIONS_FINAL_INVALID",
            "复查结论汇总结果未通过问题范围、片段或修改安全校验。",
        ) from exc
    finalize_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .ASR_REVIEW_DECISIONS_FINALIZE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_REVIEW_DECISIONS_FINALIZE_REPLAY_MISMATCH",
                "复查结论汇总结果与已保存结果不一致，请先运行任务详情审计。",
            )
    else:
        managed_local_step.complete_step(
            finalize_handle,
            final_output,
        )
    return deterministic_result


__all__ = [
    "execute_managed_review_decisions_operation",
    "execute_prepared_review_decisions",
    "is_managed_review_decisions_operation",
    "review_decisions_behavior_fingerprint",
]
