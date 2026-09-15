"""Managed execution for the research-evidence development node."""

from __future__ import annotations

import hashlib
import inspect
import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from app.domains.video_localization import (
    asr_document_understanding_result_reader,
    asr_research_evidence_managed_contracts as managed_contracts,
    asr_research_evidence_provider_gateway as provider_gateway,
    asr_research_evidence_search_gateway as search_gateway,
    asr_visual_evidence_result_reader,
    managed_artifact_files,
    managed_local_step,
    managed_local_workflow_specs,
    research_evidence,
    web_research,
)
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationOperation,
)
from app.errors import AppException
from app.schemas.video_localization_asr_research_evidence_step import (
    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    AsrResearchCallInputV1,
    AsrResearchSearchInputV1,
)
from app.schemas.video_localization_operation_artifact_storage import (
    ManagedArtifactFileBackend,
)
from app.schemas.video_localization_operation_detail import (
    AsrResearchEvidenceDetailParametersV1,
)
from app.services import database, llm_runtime, settings_store
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


CompleteJson = Callable[..., dict | list]


def is_managed_research_evidence_operation(
    kind: str,
    workflow_version: str,
) -> bool:
    return (
        kind == "english_asr"
        and workflow_version
        == ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
    )


def research_evidence_behavior_fingerprint() -> str:
    """Hash search, prompt, merge, safety and contract boundaries."""

    source_objects = (
        research_evidence.ResearchEvidenceService.run,
        research_evidence.ResearchEvidenceService._search_round,
        research_evidence._assess_evidence,
        research_evidence._assess_evidence_once,
        research_evidence._bind_visual_hints,
        research_evidence._query_with_visual_hints,
        research_evidence._build_result,
        web_research.research_transcript,
        web_research._rewrite_failed_query,
        web_research._provider_result_supports_query,
        web_research._run_search_with_retry,
    )
    payload = {
        "workflow_version": (
            ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
        ),
        "prompt_version": research_evidence.PROMPT_VERSION,
        "implementation": {
            f"{value.__module__}.{value.__qualname__}": hashlib.sha256(
                inspect.getsource(value).encode("utf-8")
            ).hexdigest()
            for value in source_objects
        },
        "contracts": {
            "request_v1": (
                research_evidence.AsrResearchEvidenceInput
                .model_json_schema()
            ),
            "request_v2": (
                research_evidence.AsrResearchEvidenceInputV2
                .model_json_schema()
            ),
            "search_input": (
                AsrResearchSearchInputV1.model_json_schema()
            ),
            "call_input": (
                AsrResearchCallInputV1.model_json_schema()
            ),
            "prepared_input": (
                managed_contracts
                .AsrResearchEvidencePreparedInputV1
                .model_json_schema()
            ),
            "step_output": (
                managed_contracts
                .AsrResearchEvidenceStepOutputV1
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


def execute_managed_research_evidence_operation(
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
) -> research_evidence.AsrResearchEvidenceResult:
    """Rebuild locked upstreams and execute one managed research task."""

    if not is_managed_research_evidence_operation(
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
            "任务不是当前资料查询工作流，已拒绝执行。",
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
                != ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
                or not isinstance(
                    detail_record.core.parameters,
                    AsrResearchEvidenceDetailParametersV1,
                )
            ):
                raise OperationDetailRepairRequired(
                    "detail_core_mismatch"
                )
            parameters = detail_record.core.parameters
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
            if document is None:
                raise OperationDetailRepairRequired(
                    "upstream_operation_missing"
                )
            visual = None
            if parameters.input_visual_evidence_operation_id:
                visual = (
                    asr_visual_evidence_result_reader
                    .read_success_from_connection(
                        connection,
                        project_id,
                        parameters.input_visual_evidence_operation_id,
                        file_backend=file_backend,
                    )
                )
                if visual is None:
                    raise OperationDetailRepairRequired(
                        "visual_upstream_operation_missing"
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
            "资料查询任务的新权威输入缺失或损坏，请先运行任务详情审计。",
            {"issue_codes": list(issue_codes)},
        ) from exc
    if (
        document.final_artifact_fingerprint
        != parameters.document_artifact_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_RESEARCH_UPSTREAM_CHANGED",
            "上游全文理解结果与提交时锁定的结果不一致。",
        )
    if visual is not None:
        if (
            visual.final_artifact_fingerprint
            != parameters.visual_artifact_fingerprint
            or visual.result.input.upstream_operation_id
            != parameters.input_document_understanding_operation_id
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_RESEARCH_VISUAL_UPSTREAM_CHANGED",
                "画面证据与锁定的全文理解结果不一致。",
            )
    if (
        parameters.behavior_fingerprint
        != research_evidence_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_RESEARCH_BEHAVIOR_CHANGED",
            "资料查询实现已升级，请从全文理解结果重新提交任务。",
        )
    if visual is None:
        request: research_evidence.ResearchEvidenceInput = (
            research_evidence.AsrResearchEvidenceInput
            .from_document_understanding(
                document.result,
                upstream_operation_id=(
                    parameters
                    .input_document_understanding_operation_id
                ),
                max_rounds=parameters.max_research_rounds,
                max_total_queries=parameters.max_research_queries,
            )
        )
    else:
        request = (
            research_evidence.AsrResearchEvidenceInputV2
            .from_document_and_visual_evidence(
                document.result,
                visual.result,
                upstream_operation_id=(
                    parameters
                    .input_document_understanding_operation_id
                ),
                visual_evidence_operation_id=(
                    parameters.input_visual_evidence_operation_id
                    or ""
                ),
                max_rounds=parameters.max_research_rounds,
                max_total_queries=parameters.max_research_queries,
            )
        )
    request = request.model_copy(
        update={"profile_id": parameters.profile_id}
    )
    settings = settings_store.web_search_settings()
    current_search_fingerprint = (
        search_gateway.search_configuration_fingerprint(settings)
    )
    if (
        parameters.search_configuration_fingerprint
        and parameters.search_configuration_fingerprint
        != current_search_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_RESEARCH_SEARCH_CONFIG_CHANGED",
            "资料查询的搜索配置已经变化，请重新提交任务。",
        )
    resolved_profile = None
    if request.candidates:
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
        managed_contracts.AsrResearchEvidencePreparedInputV1(
            document_artifact_fingerprint=(
                parameters.document_artifact_fingerprint
            ),
            visual_artifact_fingerprint=(
                parameters.visual_artifact_fingerprint
            ),
            profile_configuration_fingerprint=(
                parameters.profile_configuration_fingerprint
            ),
            search_configuration_fingerprint=(
                parameters.search_configuration_fingerprint
            ),
            behavior_fingerprint=parameters.behavior_fingerprint,
            request=request,
        )
    )
    return execute_prepared_research_evidence(
        prepared,
        execution_fence=execution_fence,
        resolved_profile=resolved_profile,
        search_settings=settings,
        search_api_key=settings_store.web_search_api_key(),
        is_cancelled=is_cancelled,
        file_backend=file_backend,
        complete_json=(complete_json or llm_runtime.complete_json),
        clock=clock,
    )


def execute_prepared_research_evidence(
    prepared_input: (
        managed_contracts.AsrResearchEvidencePreparedInputV1
    ),
    *,
    execution_fence: ExecutionFence,
    resolved_profile: llm_runtime.ResolvedProfile | None,
    search_settings,
    search_api_key: str | None,
    is_cancelled,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
    service: research_evidence.ResearchEvidenceService = (
        research_evidence.DEFAULT_RESEARCH_EVIDENCE_SERVICE
    ),
    complete_json: CompleteJson = llm_runtime.complete_json,
    clock: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> research_evidence.AsrResearchEvidenceResult:
    """Lock input, reuse external calls, and commit one final manifest."""

    if (
        prepared_input.behavior_fingerprint
        != research_evidence_behavior_fingerprint()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_RESEARCH_BEHAVIOR_CHANGED",
            "资料查询实现已升级，请重新提交任务。",
        )
    has_candidates = bool(prepared_input.request.candidates)
    if has_candidates != bool(resolved_profile):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_RESEARCH_PROFILE_CHANGED",
            "资料查询任务的模型身份与已锁定输入不一致。",
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
                "VIDEO_LOCALIZATION_RESEARCH_PROFILE_CHANGED",
                "资料查询使用的模型配置与已锁定输入不一致。",
            )
    if has_candidates and (
        prepared_input.search_configuration_fingerprint
        != search_gateway.search_configuration_fingerprint(
            search_settings
        )
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_RESEARCH_SEARCH_CONFIG_CHANGED",
            "资料查询的搜索配置与已锁定输入不一致。",
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
            .ASR_RESEARCH_EVIDENCE_PREPARE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_RESEARCH_INPUT_CHANGED",
                "资料查询的上游输入与已保存快照不一致。",
            )
    else:
        managed_local_step.complete_step(
            prepare_handle,
            prepared_input,
        )

    committed_searches: list[
        search_gateway.CommittedResearchSearch
    ] = []
    committed_calls: list[
        provider_gateway.CommittedResearchCall
    ] = []
    record_lock = threading.Lock()

    def record_search(
        committed: search_gateway.CommittedResearchSearch,
    ) -> None:
        with record_lock:
            _record_unique(
                committed_searches,
                committed,
                identity=lambda value: value.reference.request_id,
                error_code=(
                    "VIDEO_LOCALIZATION_RESEARCH_SEARCH_CONFLICT"
                ),
            )

    def record_call(
        committed: provider_gateway.CommittedResearchCall,
    ) -> None:
        with record_lock:
            _record_unique(
                committed_calls,
                committed,
                identity=lambda value: value.reference.call_id,
                error_code=(
                    "VIDEO_LOCALIZATION_RESEARCH_CALL_CONFLICT"
                ),
            )

    completion = (
        provider_gateway.ManagedResearchEvidenceGateway(
            execution_fence=execution_fence,
            resolved_profile=resolved_profile,
            expected_profile_configuration_fingerprint=(
                prepared_input.profile_configuration_fingerprint
            ),
            behavior_fingerprint=(
                prepared_input.behavior_fingerprint
            ),
            file_backend=file_backend,
            complete_json=complete_json,
            on_committed_call=record_call,
            clock=clock,
        )
        if resolved_profile is not None
        else None
    )

    def search_factory(round_index: int, candidate_id: str):
        return search_gateway.ManagedResearchSearchGateway(
            execution_fence=execution_fence,
            round_index=round_index,
            candidate_id=candidate_id,
            settings=search_settings,
            expected_search_configuration_fingerprint=(
                prepared_input.search_configuration_fingerprint
                or ""
            ),
            behavior_fingerprint=(
                prepared_input.behavior_fingerprint
            ),
            file_backend=file_backend,
            on_committed_search=record_search,
            clock=clock,
        )

    try:
        result = service.run(
            prepared_input.request,
            cache_dir=None,
            is_cancelled=is_cancelled,
            completion_gateway=completion,
            resolved_profile=resolved_profile,
            resolved_search_settings=search_settings,
            resolved_search_api_key=search_api_key,
            search_gateway_factory=(
                search_factory if has_candidates else None
            ),
        )
    except (
        ExecutionFenceLost,
        ExecutionOperationCancelled,
        AppException,
    ):
        raise
    searches = sorted(
        committed_searches,
        key=lambda value: (
            value.reference.round_index,
            value.reference.candidate_id,
            value.reference.search_kind,
            value.reference.request_id,
        ),
    )
    calls = sorted(
        committed_calls,
        key=lambda value: value.reference.call_id,
    )
    search_duration_by_candidate_round: dict[
        tuple[int, str], int
    ] = {}
    for item in searches:
        key = (
            item.reference.round_index,
            item.reference.candidate_id,
        )
        search_duration_by_candidate_round[key] = (
            search_duration_by_candidate_round.get(key, 0)
            + item.artifact.duration_ms
        )
    deterministic_query_runs = [
        item.model_copy(
            update={
                "duration_ms": search_duration_by_candidate_round.get(
                    (item.round_index, item.candidate_id),
                    0,
                )
            }
        )
        for item in result.query_runs
    ]
    duration_ms = sum(
        item.artifact.duration_ms for item in searches
    ) + sum(
        item.artifact.llm_call.duration_ms for item in calls
    )
    deterministic_result = result.model_copy(
        update={
            "query_runs": deterministic_query_runs,
            "llm_calls": [item.artifact.llm_call for item in calls],
            "stage_timing": (
                result.stage_timing.model_copy(
                    update={"duration_ms": duration_ms}
                )
            ),
        }
    )
    final_output = (
        managed_contracts.AsrResearchEvidenceStepOutputV1(
            prepared_input_fingerprint=prepared_fingerprint,
            status=deterministic_result.status,
            stop_reason=deterministic_result.stop_reason,
            profile_id=deterministic_result.profile_id,
            model_id=deterministic_result.model_id,
            prompt_version=deterministic_result.prompt_version,
            query_runs=tuple(deterministic_result.query_runs),
            evidence=tuple(deterministic_result.evidence),
            rounds=tuple(deterministic_result.rounds),
            supported_candidate_ids=tuple(
                deterministic_result.supported_candidate_ids
            ),
            unresolved_candidate_ids=tuple(
                deterministic_result.unresolved_candidate_ids
            ),
            warnings=tuple(deterministic_result.warnings),
            stage_timing=deterministic_result.stage_timing,
            quality_summary=deterministic_result.quality_summary,
            searches=tuple(item.reference for item in searches),
            calls=tuple(item.reference for item in calls),
        )
    )
    finalize_handle = managed_local_step.prepare_step(
        execution_fence,
        spec=(
            managed_local_workflow_specs
            .ASR_RESEARCH_EVIDENCE_FINALIZE_STEP_SPEC
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
                "VIDEO_LOCALIZATION_RESEARCH_FINALIZE_REPLAY_MISMATCH",
                "资料查询汇合结果与已保存结果不一致，请先运行任务详情审计。",
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
                    "资料查询出现重复且内容不一致的步骤记录。",
                )
            return
    values.append(committed)


__all__ = [
    "execute_managed_research_evidence_operation",
    "execute_prepared_research_evidence",
    "is_managed_research_evidence_operation",
    "research_evidence_behavior_fingerprint",
]
