from __future__ import annotations

from typing import Any, Mapping

from app.domains.video_localization import operation_state, public_payload
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationOperationSummary,
)
from app.schemas.video_localization_operation_summary import (
    OperationSummaryCoreV1,
    SUMMARY_CORE_SCHEMA_VERSION,
    operation_summary_core_fingerprint,
    operation_summary_core_json,
)


SUMMARY_SECTION_LIMIT = 3
SUMMARY_SECTION_LIMIT_MAX = 4
SUMMARY_SECTION_ITEM_LIMIT = 8

MEDIA_SUMMARY_KEYS = frozenset(
    {
        "duration_ms",
        "sample_rate",
        "channels",
        "audio_extract_status",
        "separation_engine_id",
        "separation_status",
        "track_count",
        "available_track_count",
        "media_status",
        "selected_source",
    }
)

_TERMINAL_DETAIL_SUMMARY_KEYS = frozenset(
    {
        "branch_duration_ms",
        "error_detail",
        "preview_phase",
        "quality_summary",
        "sample",
        "sample_schema_version",
        "task_final_result",
        "task_stage_groups",
        "task_stage_timings",
        "task_step_results",
    }
)

_OPERATION_SUMMARY_PARAMETER_KEYS = frozenset(
    {
        "development_source_operation_id",
        "development_target_step_id",
        "engine_id",
        "execution_mode",
        "scope",
        "source_track_id",
        "stop_after_step",
    }
)

_OPERATION_SUMMARY_RESULT_KEYS = frozenset(
    {
        "stage",
        "stage_id",
        "task_stage_timings",
        "task_duration_ms",
        "preview_phase",
        *MEDIA_SUMMARY_KEYS,
        "cue_count",
        "segment_count",
        "localized_subtitle_count",
        "semantic_group_count",
        "filename",
        "size_bytes",
        "export_kind",
        "mixed_track_count",
        "engine_id",
        "language",
        "llm_profile_id",
        "llm_model_id",
        "source_track_id",
        "error_detail",
        "execution_scope",
        "sample_schema_version",
        "sample",
        "frame_count",
        "evidence_count",
        "checked_section_count",
        "failed_section_count",
        "issue_count",
        "decision_count",
        "applied_change_count",
        "unresolved_issue_count",
        "decision",
        "can_start_alignment",
        "review_target_count",
        "blocker_count",
        "warning_count",
        "speaker_count",
        "diarization_status",
        "count_guidance",
        "quality_summary",
        "parallel",
        "raw_asr_segment_count",
        "diarization_segment_count",
        "source_cue_count",
        "source_word_count",
        "source_pause_count",
        "semantic_unit_count",
        "semantic_warning_count",
        "semantic_model_call_count",
        "localization_risk_count",
        "localization_high_risk_count",
        "localization_research_needed_count",
        "localization_visual_needed_count",
        "delivery_budget_unit_count",
        "delivery_budget_multi_event_count",
        "delivery_budget_short_window_count",
        "localization_strategy_unit_count",
        "localization_strategy_risk_count",
        "localization_strategy_evidence_count",
        "localization_research_query_count",
        "localization_research_source_count",
        "localization_research_failed_count",
        "result_count",
        "result_unit",
        "branch_duration_ms",
        "workflow_schema_version",
        "workflow_id",
        "task_stage_groups",
    }
)


def project_operation_summary(
    operation: VideoLocalizationOperation,
    *,
    result_summary_seed: Mapping[str, Any] | None = None,
    stage_override: str | None = None,
    artifact_available: bool | None = None,
) -> VideoLocalizationOperationSummary:
    """Build the single canonical high-frequency operation projection."""

    summary = dict(result_summary_seed or {})
    summary.update(
        {
            key: value
            for key, value in operation.result_summary.items()
            if (
                key in _OPERATION_SUMMARY_RESULT_KEYS
                and (
                    operation.status in operation_state.ACTIVE_STATUSES
                    or key not in _TERMINAL_DETAIL_SUMMARY_KEYS
                )
            )
        }
    )
    if stage_override is not None:
        summary["stage"] = stage_override
    if artifact_available is not None:
        summary["artifact_available"] = artifact_available
    if (
        operation.status in operation_state.ACTIVE_STATUSES
        and "preview_cues" in operation.result_summary
    ):
        summary["preview_cues"] = operation.result_summary["preview_cues"]
    raw_step_results = operation.result_summary.get("task_step_results")
    if (
        operation.status in operation_state.ACTIVE_STATUSES
        and isinstance(raw_step_results, dict)
    ):
        summary["task_step_results"] = {
            str(step_id): compact_live_step_result(result)
            for step_id, result in raw_step_results.items()
            if isinstance(result, dict)
        }
    task_final_result = operation.result_summary.get("task_final_result")
    if (
        operation.status in operation_state.ACTIVE_STATUSES
        and isinstance(task_final_result, dict)
    ):
        summary["task_final_result"] = compact_live_step_result(
            task_final_result
        )
    projected = VideoLocalizationOperationSummary.model_validate(
        {
            **operation.model_dump(
                mode="python",
                exclude={"parameters", "result_summary"},
            ),
            "parameters": {
                key: value
                for key, value in operation.parameters.items()
                if key in _OPERATION_SUMMARY_PARAMETER_KEYS
            },
            "result_summary": summary,
        }
    )
    return VideoLocalizationOperationSummary.model_validate(
        public_payload.public_operation_payload(projected)
    )


def operation_summary_core(
    summary: VideoLocalizationOperationSummary,
) -> OperationSummaryCoreV1:
    payload = public_payload.public_operation_payload(summary)
    result_summary = dict(payload.get("result_summary") or {})
    result_summary.pop("artifact_available", None)
    payload["result_summary"] = result_summary
    return OperationSummaryCoreV1.model_validate(
        {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "kind",
                "status",
                "cancel_requested",
                "created_at",
                "completed_at",
            }
        }
    )


def operation_summary_core_from_operation(
    operation: VideoLocalizationOperation,
) -> OperationSummaryCoreV1:
    """Project durable operation data without read-time enrichments."""

    return operation_summary_core(
        project_operation_summary(operation)
    )


def operation_summary_core_from_draft_operation(
    draft: VideoLocalizationDraft,
    operation: VideoLocalizationOperation,
) -> OperationSummaryCoreV1:
    """Project durable list facts with typed Project-owned media context."""

    return operation_summary_core(
        project_operation_summary(
            operation,
            result_summary_seed=durable_media_summary(
                draft,
                operation,
            ),
        )
    )


def durable_media_summary(
    draft: VideoLocalizationDraft,
    operation: VideoLocalizationOperation,
) -> dict[str, Any]:
    """Return stable media facts that legacy v1 added at read time."""

    if operation.status != "success":
        return {}
    if operation.kind == "source_audio":
        extraction_status = draft.source_media.metadata.get(
            "audio_extract_status"
        )
        if (
            not draft.source_media.audio_path
            or extraction_status not in {None, "completed"}
        ):
            return {}
        media_summary = operation_state.source_audio_summary(draft)
    elif operation.kind == "stems":
        if draft.stems.separation_status != "completed" or not (
            draft.stems.vocals_clean_path
            or draft.stems.background_path
        ):
            return {}
        media_summary = operation_state.stems_summary(draft)
    else:
        return {}
    return {
        key: value
        for key, value in media_summary.items()
        if key in MEDIA_SUMMARY_KEYS
    }


def assemble_operation_summary(
    core: OperationSummaryCoreV1,
    *,
    kind: str,
    status: str,
    cancel_requested: bool,
    created_at: str,
    completed_at: str | None,
) -> VideoLocalizationOperationSummary:
    core_payload = core.model_dump(
        mode="python",
        exclude={"summary_schema_version"},
    )
    return VideoLocalizationOperationSummary.model_validate(
        {
            **core_payload,
            "kind": kind,
            "status": status,
            "cancel_requested": cancel_requested,
            "created_at": created_at,
            "completed_at": completed_at,
        }
    )


def compact_live_step_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: result[key]
        for key in (
            "detail_mode",
            "label",
            "order",
            "status",
            "purpose",
            "summary",
            "metrics",
            "notes",
            "coverage",
            "error_detail",
        )
        if key in result
    }
    sections = result.get("sections")
    debug = result.get("debug")
    review_targets = result.get("review_targets")
    if isinstance(review_targets, list):
        compact["review_targets"] = [
            item
            for item in review_targets
            if isinstance(item, dict)
        ][:12]
    if isinstance(sections, list):
        requested_section_limit = result.get("summary_section_limit")
        section_limit = (
            min(
                SUMMARY_SECTION_LIMIT_MAX,
                max(
                    SUMMARY_SECTION_LIMIT,
                    requested_section_limit,
                ),
            )
            if isinstance(requested_section_limit, int)
            and not isinstance(requested_section_limit, bool)
            else SUMMARY_SECTION_LIMIT
        )
        valid_sections = [
            section
            for section in sections
            if isinstance(section, dict)
            and isinstance(section.get("items"), list)
        ]
        compact_sections = [
            {
                **section,
                "items": section["items"][:SUMMARY_SECTION_ITEM_LIMIT],
            }
            for section in valid_sections[:section_limit]
        ]
        compact["sections"] = compact_sections
        original_item_count = sum(
            len(section["items"]) for section in valid_sections
        )
        shown_item_count = sum(
            len(section["items"]) for section in compact_sections
        )
        truncated = (
            len(valid_sections) > section_limit
            or shown_item_count < original_item_count
        )
        if truncated:
            raw_coverage = result.get("coverage")
            coverage = (
                dict(raw_coverage)
                if isinstance(raw_coverage, dict)
                else {}
            )
            total_count = coverage.get("total_count")
            if not isinstance(total_count, int) or isinstance(
                total_count,
                bool,
            ):
                total_count = original_item_count
            compact["coverage"] = {
                **coverage,
                "mode": "focused",
                "shown_count": shown_item_count,
                "total_count": max(
                    total_count,
                    original_item_count,
                ),
                "unit": str(coverage.get("unit") or "项"),
                "reason": (
                    "任务轮询摘要为控制体积仅返回部分明细，"
                    "完整结果仍保存在任务记录中。"
                ),
                "truncated": True,
            }
            notes = list(compact.get("notes") or [])
            notes.append(
                "任务列表只展示部分明细；可在完整结果中"
                "查看其余机器记录。"
            )
            compact["notes"] = notes
    if isinstance(debug, dict):
        compact_debug = {
            key: debug[key]
            for key in ("description", "metrics", "notes")
            if key in debug
        }
        debug_sections = debug.get("sections")
        if isinstance(debug_sections, list):
            compact_debug["sections"] = [
                {
                    **section,
                    "items": section.get("items", [])[
                        :SUMMARY_SECTION_ITEM_LIMIT
                    ],
                }
                for section in debug_sections[:SUMMARY_SECTION_LIMIT]
                if isinstance(section, dict)
                and isinstance(section.get("items"), list)
            ]
        compact["debug"] = compact_debug
    return public_payload.without_internal_locators(compact)


__all__ = [
    "MEDIA_SUMMARY_KEYS",
    "OperationSummaryCoreV1",
    "SUMMARY_CORE_SCHEMA_VERSION",
    "assemble_operation_summary",
    "compact_live_step_result",
    "operation_summary_core",
    "operation_summary_core_from_draft_operation",
    "operation_summary_core_from_operation",
    "operation_summary_core_fingerprint",
    "operation_summary_core_json",
    "project_operation_summary",
    "durable_media_summary",
]
