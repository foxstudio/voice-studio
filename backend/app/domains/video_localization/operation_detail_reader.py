from __future__ import annotations

import re
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any, Literal

from app.domains.video_localization import (
    asr_document_understanding_detail_reader,
    asr_entity_normalization_detail_reader,
    asr_section_review_detail_reader,
    asr_review_decisions_detail_reader,
    asr_transcript_quality_gate_detail_reader,
    asr_whole_recheck_detail_reader,
    asr_research_evidence_detail_reader,
    asr_visual_evidence_detail_reader,
    asr_initial_analysis_detail_reader,
    asr_raw_detail_reader,
    llm_observability,
    managed_artifact_files,
    operation_elapsed,
    operation_state,
    source_audio_detail_reader,
    speaker_diarization_detail_reader,
    stem_separation_detail_reader,
    reference_candidates_detail_reader,
    workflow_contracts,
)
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
    ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION,
    SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION,
)
from app.schemas.video_localization_semantic_tts_grouping_step import (
    SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION,
    SemanticTtsGroupingRoundArtifactV1,
    parse_semantic_tts_grouping_round_artifact,
)
from app.schemas.video_localization_source_audio_step import (
    SOURCE_AUDIO_WORKFLOW_VERSION,
)
from app.schemas.video_localization_stem_separation_step import (
    STEM_SEPARATION_WORKFLOW_VERSION,
)
from app.schemas.video_localization_reference_candidates_step import (
    REFERENCE_CANDIDATES_WORKFLOW_VERSION,
)
from app.schemas.video_localization_speaker_diarization_step import (
    SPEAKER_DIARIZATION_WORKFLOW_VERSION,
)
from app.services import database
from app.services import (
    video_localization_operation_artifact_store as artifact_store,
)
from app.services import (
    video_localization_operation_detail_core_store as detail_store,
)
from app.services import (
    video_localization_operation_ledger_store as ledger_store,
)
from app.services import (
    video_localization_operation_step_store as step_store,
)


OperationDetailAuthority = Literal["managed", "legacy", "missing"]
_ROUND_STEP = re.compile(r"^semantic_grouping_round_([12])$")
_ACTIVE_STATUSES = frozenset({"queued", "running"})
_TERMINAL_STATUSES = frozenset(
    {"success", "failed", "cancelled"}
)


@dataclass(frozen=True)
class OperationDetailRead:
    authority: OperationDetailAuthority
    operation: VideoLocalizationOperation | None = None


@dataclass(frozen=True)
class _RoundResult:
    step: step_store.OperationStepAttempt
    artifact: SemanticTtsGroupingRoundArtifactV1


def read_operation_detail(
    project_id: str,
    operation_id: str,
    *,
    file_backend: ManagedArtifactFileBackend = managed_artifact_files,
) -> OperationDetailRead:
    """Route one detail read without touching Project JSON for managed v2."""

    normalized_project_id = _required_identity(
        project_id,
        "project ID",
    )
    normalized_operation_id = _required_identity(
        operation_id,
        "operation ID",
    )
    with database.read_conn() as connection:
        ledger = _read_ledger(
            connection,
            normalized_project_id,
            normalized_operation_id,
        )
        if ledger is None:
            orphaned_core = connection.execute(
                """
                SELECT 1
                FROM video_localization_operation_detail_cores
                WHERE project_id = ?
                  AND operation_id = ?
                """,
                (
                    normalized_project_id,
                    normalized_operation_id,
                ),
            ).fetchone()
            if orphaned_core is not None:
                raise OperationDetailRepairRequired(
                    "ledger_operation_missing"
                )
            return OperationDetailRead(authority="missing")
        workflow_version = str(ledger["workflow_version"])
        if (
            workflow_version
            == ASR_RAW_DEVELOPMENT_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_raw_detail_reader
                    .assemble_asr_raw_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == ASR_INITIAL_ANALYSIS_DEVELOPMENT_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_initial_analysis_detail_reader
                    .assemble_initial_analysis_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == (
                ASR_DOCUMENT_UNDERSTANDING_DEVELOPMENT_WORKFLOW_VERSION
            )
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_document_understanding_detail_reader
                    .assemble_document_understanding_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == ASR_VISUAL_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_visual_evidence_detail_reader
                    .assemble_visual_evidence_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == ASR_RESEARCH_EVIDENCE_DEVELOPMENT_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_research_evidence_detail_reader
                    .assemble_research_evidence_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == ASR_ENTITY_NORMALIZATION_DEVELOPMENT_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_entity_normalization_detail_reader
                    .assemble_entity_normalization_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == ASR_SECTION_REVIEW_DEVELOPMENT_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_section_review_detail_reader
                    .assemble_section_review_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == ASR_REVIEW_DECISIONS_DEVELOPMENT_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_review_decisions_detail_reader
                    .assemble_review_decisions_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == ASR_WHOLE_RECHECK_DEVELOPMENT_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_whole_recheck_detail_reader
                    .assemble_whole_recheck_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == ASR_TRANSCRIPT_QUALITY_GATE_DEVELOPMENT_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    asr_transcript_quality_gate_detail_reader
                    .assemble_transcript_quality_gate_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if workflow_version == SOURCE_AUDIO_WORKFLOW_VERSION:
            return OperationDetailRead(
                authority="managed",
                operation=(
                    source_audio_detail_reader
                    .assemble_source_audio_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == STEM_SEPARATION_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    stem_separation_detail_reader
                    .assemble_stem_separation_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == REFERENCE_CANDIDATES_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    reference_candidates_detail_reader
                    .assemble_reference_candidates_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == SPEAKER_DIARIZATION_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=(
                    speaker_diarization_detail_reader
                    .assemble_speaker_diarization_detail(
                        connection,
                        ledger,
                        file_backend=file_backend,
                    )
                ),
            )
        if (
            workflow_version
            == SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION
        ):
            return OperationDetailRead(
                authority="managed",
                operation=_assemble_semantic_detail(
                    connection,
                    ledger,
                    file_backend=file_backend,
                ),
            )
        return OperationDetailRead(authority="legacy")


def _assemble_semantic_detail(
    connection: Connection,
    ledger,
    *,
    file_backend: ManagedArtifactFileBackend,
) -> VideoLocalizationOperation:
    project_id = str(ledger["project_id"])
    operation_id = str(ledger["operation_id"])
    if str(ledger["kind"]) != "semantic_tts_grouping":
        raise OperationDetailRepairRequired("ledger_identity_invalid")
    status = str(ledger["status"])
    if status not in _ACTIVE_STATUSES | _TERMINAL_STATUSES:
        raise OperationDetailRepairRequired("ledger_status_invalid")
    try:
        detail_record = detail_store.get_detail_core_from_connection(
            connection,
            project_id,
            operation_id,
        )
    except (
        detail_store.OperationDetailCoreIdentityConflict,
        detail_store.OperationDetailCoreIntegrityError,
        detail_store.OperationDetailCoreSchemaError,
        TypeError,
        ValueError,
    ) as exc:
        raise OperationDetailRepairRequired(
            "detail_core_invalid"
        ) from exc
    if detail_record is None:
        raise OperationDetailRepairRequired("detail_core_missing")
    core = detail_record.core
    parameters = {
        "profile_id": core.parameters.profile_id,
        "profile_configuration_fingerprint": (
            core.parameters.profile_configuration_fingerprint
        ),
        "workflow_id": "semantic-tts-grouping",
        "target_chars": core.parameters.target_chars,
        "max_chars": core.parameters.max_chars,
    }
    parameters["scope"] = operation_state.operation_scope(
        "semantic_tts_grouping",
        parameters,
    )
    if (
        ledger_store.parameters_fingerprint(parameters)
        != str(ledger["parameters_fingerprint"])
    ):
        raise OperationDetailRepairRequired(
            "detail_parameters_mismatch"
        )
    try:
        steps = step_store.list_step_attempts_from_connection(
            connection,
            project_id,
            operation_id,
        )
    except (
        step_store.StepSchemaError,
        TypeError,
        ValueError,
    ) as exc:
        raise OperationDetailRepairRequired(
            "step_state_invalid"
        ) from exc
    if any(
        step.workflow_version
        != SEMANTIC_TTS_GROUPING_WORKFLOW_VERSION
        for step in steps
    ):
        raise OperationDetailRepairRequired(
            "step_workflow_mismatch"
        )
    round_results = _read_round_results(
        connection,
        project_id,
        operation_id,
        steps,
        file_backend=file_backend,
    )
    if status == "success" and not round_results:
        raise OperationDetailRepairRequired(
            "successful_workflow_step_missing"
        )
    attempts = connection.execute(
        """
        SELECT
            attempt_number,
            status,
            started_at,
            completed_at,
            error_code
        FROM video_localization_operation_attempts
        WHERE project_id = ?
          AND operation_id = ?
        ORDER BY attempt_number, attempt_id
        """,
        (project_id, operation_id),
    ).fetchall()
    started_at = (
        str(attempts[0]["started_at"]) if attempts else None
    )
    completed_at = (
        str(ledger["completed_at"])
        if ledger["completed_at"] is not None
        else None
    )
    error_code = _operation_error_code(
        status,
        steps,
        attempts,
        round_results,
    )
    error_message = _operation_error_message(
        status,
        error_code,
    )
    result_summary = _semantic_result_summary(
        status=status,
        steps=steps,
        round_results=round_results,
        started_at=started_at,
        completed_at=completed_at,
        error_code=error_code,
        profile_id=core.parameters.profile_id,
    )
    return VideoLocalizationOperation(
        operation_id=operation_id,
        project_id=project_id,
        kind="semantic_tts_grouping",
        status=status,
        label=(
            workflow_contracts
            .SEMANTIC_TTS_GROUPING_WORKFLOW_DEFINITION
            .label
        ),
        progress=_operation_progress(
            status,
            steps,
        ),
        error_code=error_code,
        error_message=error_message,
        cancel_requested=bool(ledger["cancel_requested"]),
        result_summary=result_summary,
        parameters=parameters,
        created_at=str(ledger["created_at"]),
        started_at=started_at,
        completed_at=completed_at,
    )


def _read_round_results(
    connection: Connection,
    project_id: str,
    operation_id: str,
    steps: list[step_store.OperationStepAttempt],
    *,
    file_backend: ManagedArtifactFileBackend,
) -> tuple[_RoundResult, ...]:
    results: list[_RoundResult] = []
    for step in steps:
        round_match = _ROUND_STEP.fullmatch(step.step_id)
        if round_match is None or step.status != "success":
            continue
        artifact = artifact_store.get_step_artifact_from_connection(
            connection,
            project_id,
            operation_id,
            step.step_attempt_id,
            artifact_kind="step-result",
            artifact_key="primary",
        )
        if artifact is None:
            raise OperationDetailRepairRequired(
                "successful_step_artifact_missing"
            )
        if (
            step.output_fingerprint
            != artifact.content_fingerprint
        ):
            raise OperationDetailRepairRequired(
                "artifact_fingerprint_mismatch"
            )
        try:
            verified = artifact_store.read_artifact_from_connection(
                connection,
                artifact.artifact_id,
                file_backend=file_backend,
            )
            parsed = parse_semantic_tts_grouping_round_artifact(
                verified.content
            )
        except (
            artifact_store.ArtifactIdentityConflict,
            artifact_store.ArtifactIntegrityError,
            artifact_store.ArtifactPathError,
            artifact_store.ArtifactSchemaError,
            TypeError,
            ValueError,
        ) as exc:
            raise OperationDetailRepairRequired(
                "artifact_invalid"
            ) from exc
        if (
            verified.artifact.payload_schema_version
            != SEMANTIC_TTS_GROUPING_ROUND_SCHEMA_VERSION
            or verified.artifact.media_type != "application/json"
            or parsed.round_index != int(round_match.group(1))
        ):
            raise OperationDetailRepairRequired(
                "artifact_contract_invalid"
            )
        results.append(_RoundResult(step=step, artifact=parsed))
    return tuple(
        sorted(
            results,
            key=lambda value: (
                value.artifact.round_index,
                value.step.step_attempt_number,
                value.step.step_attempt_id,
            ),
        )
    )


def _semantic_result_summary(
    *,
    status: str,
    steps: list[step_store.OperationStepAttempt],
    round_results: tuple[_RoundResult, ...],
    started_at: str | None,
    completed_at: str | None,
    error_code: str | None,
    profile_id: str,
) -> dict[str, Any]:
    workflow = (
        workflow_contracts
        .semantic_tts_grouping_workflow_summary()
    )
    latest_round = round_results[-1] if round_results else None
    step_results = _semantic_task_steps(
        status=status,
        steps=steps,
        round_results=round_results,
        error_code=error_code,
    )
    stage_id = _operation_stage_id(
        status,
        step_results,
    )
    stage = _operation_stage_label(
        status,
        stage_id,
    )
    summary: dict[str, Any] = {
        "stage": stage,
        "stage_id": stage_id,
        "workflow_schema_version": workflow["schema_version"],
        "workflow_id": workflow["workflow_id"],
        "task_stage_groups": workflow["stages"],
        "task_step_results": step_results,
        "llm_profile_id": profile_id,
    }
    if latest_round is not None:
        summary["llm_model_id"] = (
            latest_round.artifact.llm_call.model_id
        )
    if status == "success" and latest_round is not None:
        group_count = len(latest_round.artifact.groups)
        summary["semantic_group_count"] = group_count
        summary["task_final_result"] = {
            "status": "success",
            "summary": f"已生成并保存 {group_count} 个连续配音语义组。",
            "metrics": [
                {
                    "label": "配音语义组",
                    "value": str(group_count),
                }
            ],
            "sections": [],
            "notes": [],
        }
    duration_ms = _duration_ms(started_at, completed_at)
    if duration_ms is not None:
        summary["task_duration_ms"] = duration_ms
    if status == "failed":
        summary["error_detail"] = {
            "status": "failed",
            "summary": _operation_error_message(
                status,
                error_code,
            )
            or "语义分组任务未完成。",
            "metrics": (
                [
                    {
                        "label": "错误代码",
                        "value": error_code,
                    }
                ]
                if error_code
                else []
            ),
            "sections": [
                {
                    "title": "处理建议",
                    "items": [
                        {
                            "title": "重新检查后重试",
                            "detail": (
                                "确认字幕和模型配置仍然可用；"
                                "若详情提示需要修复，请先运行详情审计。"
                            ),
                        }
                    ],
                }
            ],
            "notes": [],
        }
    return summary


def _semantic_task_steps(
    *,
    status: str,
    steps: list[step_store.OperationStepAttempt],
    round_results: tuple[_RoundResult, ...],
    error_code: str | None,
) -> dict[str, dict[str, Any]]:
    definitions = {
        task.id: task
        for stage in (
            workflow_contracts
            .SEMANTIC_TTS_GROUPING_WORKFLOW_DEFINITION
            .stages
        )
        for task in stage.atomic_tasks
    }
    explicit = {
        step_id: _latest_step(
            steps,
            lambda step, expected=step_id: step.step_id == expected,
        )
        for step_id in ("prepare", "group", "validate", "write")
    }
    provider_steps = [
        step for step in steps
        if _ROUND_STEP.fullmatch(step.step_id)
    ]
    results: dict[str, dict[str, Any]] = {}
    for step_id in ("prepare", "group", "validate", "write"):
        definition = definitions[step_id]
        step_status = _public_step_status(
            step_id,
            status=status,
            explicit=explicit,
            provider_steps=provider_steps,
            round_results=round_results,
        )
        result: dict[str, Any] = {
            "label": definition.label,
            "order": definition.order,
            "status": step_status,
            "purpose": definition.description,
            "summary": _step_summary(
                step_id,
                step_status,
                status=status,
                group_count=(
                    len(round_results[-1].artifact.groups)
                    if round_results
                    else None
                ),
            ),
        }
        source_step = (
            explicit[step_id]
            or (
                _latest_step(
                    provider_steps,
                    lambda _step: True,
                )
                if step_id == "group"
                else None
            )
        )
        if source_step is not None:
            duration = _duration_ms(
                source_step.prepared_at,
                source_step.completed_at
                or source_step.result_unknown_at,
            )
            if duration is not None:
                result["duration_ms"] = duration
            if source_step.error_code:
                result["error_code"] = source_step.error_code
        if step_id == "group" and round_results:
            calls = [
                item.artifact.llm_call
                for item in round_results
            ]
            call_items, call_metrics = (
                llm_observability.project_llm_calls(
                    calls,
                    calls_complete=True,
                )
            )
            result["debug"] = {
                "description": (
                    "用于核对本次语义分组实际使用的模型、"
                    "调用方式和资源消耗。"
                ),
                "metrics": call_metrics,
                "sections": (
                    [
                        {
                            "title": "模型调用明细",
                            "items": call_items,
                        }
                    ]
                    if call_items
                    else []
                ),
                "notes": [],
            }
        if (
            step_status == "failed"
            and "error_code" not in result
            and error_code
        ):
            result["error_code"] = error_code
        results[step_id] = result
    return results


def _public_step_status(
    step_id: str,
    *,
    status: str,
    explicit: dict[
        str,
        step_store.OperationStepAttempt | None,
    ],
    provider_steps: list[step_store.OperationStepAttempt],
    round_results: tuple[_RoundResult, ...],
) -> str:
    current = explicit.get(step_id)
    if current is not None:
        return _normalized_step_status(current.status)
    if step_id == "prepare":
        if provider_steps or round_results or status == "success":
            return "success"
        if status == "failed":
            return "failed"
    elif step_id == "group":
        latest = _latest_step(
            provider_steps,
            lambda _step: True,
        )
        if latest is not None:
            return _normalized_step_status(latest.status)
        if status == "failed":
            return "failed"
    elif step_id == "validate":
        if status == "success":
            return "success"
        if (
            status == "failed"
            and provider_steps
            and all(
                step.status == "success"
                for step in provider_steps
            )
        ):
            return "failed"
    elif step_id == "write":
        if status == "success":
            return "success"
        if status == "cancelled":
            return "cancelled"
    if status == "cancelled":
        return "cancelled"
    return "running" if status == "running" else "todo"


def _normalized_step_status(status: str) -> str:
    if status == "success":
        return "success"
    if status in {"failed", "result_unknown"}:
        return "failed"
    if status == "cancelled":
        return "cancelled"
    return "running"


def _latest_step(
    steps: list[step_store.OperationStepAttempt],
    predicate,
) -> step_store.OperationStepAttempt | None:
    candidates = [step for step in steps if predicate(step)]
    return (
        max(
            candidates,
            key=lambda step: (
                step.prepared_at,
                step.step_attempt_number,
                step.step_attempt_id,
            ),
        )
        if candidates
        else None
    )


def _operation_error_code(
    status: str,
    steps: list[step_store.OperationStepAttempt],
    attempts,
    round_results: tuple[_RoundResult, ...],
) -> str | None:
    if status != "failed":
        return None
    latest_failed_step = _latest_step(
        steps,
        lambda step: step.status
        in {"failed", "result_unknown"},
    )
    if (
        latest_failed_step is not None
        and latest_failed_step.error_code
    ):
        return latest_failed_step.error_code
    for attempt in reversed(attempts):
        value = str(attempt["error_code"] or "").strip()
        if value:
            return value
    if len(round_results) >= 2:
        return "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_INVALID"
    return "VIDEO_LOCALIZATION_OPERATION_FAILED"


def _operation_error_message(
    status: str,
    error_code: str | None,
) -> str | None:
    if status == "cancelled":
        return "任务已取消。"
    if status != "failed":
        return None
    messages = {
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_INVALID": (
            "语义分组结果无法通过完整性校验，请重试。"
        ),
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROFILE_CHANGED": (
            "语义分组使用的模型配置已变化，请重新提交任务。"
        ),
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_SOURCE_CHANGED": (
            "分组期间字幕已被修改，请重新执行语义成组。"
        ),
        "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_RESULT_UNKNOWN": (
            "模型调用结果尚无法确认，需要人工核对后再继续。"
        ),
    }
    return messages.get(
        error_code or "",
        "语义分组任务未完成，请检查字幕和模型配置后重试。",
    )


def _operation_progress(
    status: str,
    steps: list[step_store.OperationStepAttempt],
) -> float:
    if status in _TERMINAL_STATUSES:
        return 1.0
    if status == "queued":
        return 0.0
    latest_write = _latest_step(
        steps,
        lambda step: step.step_id == "write",
    )
    if latest_write is not None:
        return 0.95
    latest_validate = _latest_step(
        steps,
        lambda step: step.step_id == "validate",
    )
    if latest_validate is not None:
        return 0.85
    provider_steps = [
        step for step in steps
        if _ROUND_STEP.fullmatch(step.step_id)
    ]
    if provider_steps:
        return 0.7 if any(
            step.status == "success"
            for step in provider_steps
        ) else 0.4
    return 0.2


def _operation_stage_id(
    status: str,
    step_results: dict[str, dict[str, Any]],
) -> str:
    if status == "success":
        return "write"
    if status == "queued":
        return "prepare"
    for step_id in ("write", "validate", "group", "prepare"):
        if step_results[step_id]["status"] in {
            "running",
            "failed",
        }:
            return step_id
    return "write" if status == "cancelled" else "prepare"


def _operation_stage_label(status: str, step_id: str) -> str:
    if status == "success":
        return "语义分组已保存"
    if status == "failed":
        return {
            "prepare": "整理字幕失败",
            "group": "判断语义失败",
            "validate": "检查分组失败",
            "write": "保存分组失败",
        }.get(step_id, "语义分组失败")
    if status == "cancelled":
        return "语义分组已取消"
    return {
        "prepare": "整理字幕和说话人",
        "group": "判断语义和场景",
        "validate": "检查分组完整性",
        "write": "保存语义分组",
    }.get(step_id, "准备语义分组")


def _step_summary(
    step_id: str,
    step_status: str,
    *,
    status: str,
    group_count: int | None,
) -> str:
    if step_status == "success":
        return {
            "prepare": "已整理字幕顺序和说话人。",
            "group": "已按连续语义和场景完成分组。",
            "validate": "已确认字幕无遗漏、无重复且没有跨说话人。",
            "write": (
                f"已保存 {group_count} 个连续配音组。"
                if group_count is not None
                else "已保存连续配音组。"
            ),
        }[step_id]
    if step_status == "failed":
        return {
            "prepare": "字幕或模型配置未能锁定。",
            "group": "模型未能返回可确认的语义分组。",
            "validate": "语义分组未通过完整性检查。",
            "write": "语义分组未能保存到项目。",
        }[step_id]
    if step_status == "cancelled":
        return "任务已取消，此步骤未继续执行。"
    if step_status == "running":
        return {
            "prepare": "正在整理字幕顺序和说话人。",
            "group": "正在判断连续语义和场景。",
            "validate": "正在检查分组完整性。",
            "write": "正在保存语义分组。",
        }[step_id]
    return (
        "等待前置步骤完成。"
        if status in {"queued", "running"}
        else "本次任务未执行此步骤。"
    )


def _duration_ms(
    started_at: str | None,
    completed_at: str | None,
) -> int | None:
    return operation_elapsed.duration_ms(started_at, completed_at)


def _read_ledger(
    connection: Connection,
    project_id: str,
    operation_id: str,
):
    return connection.execute(
        """
        SELECT
            project_id,
            operation_id,
            kind,
            status,
            cancel_requested,
            parameters_fingerprint,
            workflow_version,
            created_at,
            completed_at
        FROM video_localization_operations
        WHERE project_id = ?
          AND operation_id = ?
        """,
        (project_id, operation_id),
    ).fetchone()


def _required_identity(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > 128:
        raise ValueError(f"{label} is too long")
    return normalized


__all__ = [
    "OperationDetailAuthority",
    "OperationDetailRead",
    "OperationDetailRepairRequired",
    "read_operation_detail",
]
