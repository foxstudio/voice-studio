from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from app.domains.video_localization import operation_state
from app.domains.video_localization import workflow_submission
from app.domains.video_localization.localization_finalization_projection import (
    project_saved_finalization_warning,
)
from app.domains.video_localization import operation_summary_projection
from app.domains.video_localization import operation_runtime
from app.domains.video_localization import operation_scheduler
from app.domains.video_localization import operation_step_shadow
from app.domains.video_localization import semantic_tts_grouping_execution
from app.domains.video_localization import source_audio_execution
from app.domains.video_localization import (
    source_audio_operation_projection,
)
from app.domains.video_localization import stem_separation_execution
from app.domains.video_localization import (
    stem_separation_operation_projection,
)
from app.domains.video_localization import (
    reference_candidates_execution,
    reference_candidates_operation_projection,
)
from app.domains.video_localization import (
    asr_document_understanding_execution,
    asr_document_understanding_operation_projection,
    asr_document_understanding_result_reader,
    asr_visual_evidence_execution,
    asr_visual_evidence_operation_projection,
    asr_visual_evidence_result_reader,
    asr_research_evidence_execution,
    asr_research_evidence_operation_projection,
    asr_research_evidence_result_reader,
    asr_research_evidence_search_gateway,
    asr_entity_normalization_execution,
    asr_entity_normalization_managed_contracts,
    asr_entity_normalization_operation_projection,
    asr_entity_normalization_result_reader,
    asr_section_review_execution,
    asr_section_review_operation_projection,
    asr_section_review_result_reader,
    asr_review_decisions_execution,
    asr_review_decisions_operation_projection,
    asr_review_decisions_result_reader,
    asr_whole_recheck_execution,
    asr_whole_recheck_operation_projection,
    asr_whole_recheck_result_reader,
    asr_transcript_quality_gate_execution,
    asr_transcript_quality_gate_operation_projection,
    asr_transcript_quality_gate_result_reader,
    asr_initial_analysis_detail_reader,
    asr_initial_analysis_execution,
    asr_initial_analysis_operation_projection,
    asr_raw_detail_reader,
    asr_raw_execution,
    asr_raw_operation_projection,
    speaker_diarization_detail_reader,
    speaker_diarization_execution,
    speaker_diarization_operation_projection,
)
from app.domains.video_localization.draft_store import DraftWriteIntent
from app.domains.video_localization import development_checkpoints
from app.domains.video_localization import dub_subtitle_workflow
from app.domains.video_localization import dub_subtitles
from app.domains.video_localization import document_understanding_contracts
from app.domains.video_localization import media_assets
from app.domains.video_localization import media_health
from app.domains.video_localization import managed_local_detail
from app.domains.video_localization import managed_artifact_files
from app.domains.video_localization import asr_flow
from app.domains.video_localization import asr_development_workflow_nodes
from app.domains.video_localization import asr_development_replay
from app.domains.video_localization import asr_development_continuation
from app.domains.video_localization import asr_pipeline
from app.domains.video_localization import asr_targeted_relisten
from app.domains.video_localization import entity_normalization
from app.domains.video_localization import llm_observability
from app.domains.video_localization import localization_requirements
from app.domains.video_localization import localization_spoken_script
from app.domains.video_localization import localization_workflow_execution
from app.domains.video_localization import localization_workflow_nodes
from app.domains.video_localization import research_evidence
from app.domains.video_localization import review_decisions
from app.domains.video_localization import section_review
from app.domains.video_localization import transcript_quality_gate
from app.domains.video_localization import visual_evidence
from app.domains.video_localization import whole_recheck
from app.domains.video_localization import workflow_contracts
from app.domains.video_localization.export_contracts import (
    VideoLocalizationMediaExportRequest,
    validate_media_export_output_filename,
)
from app.domains.video_localization import speaker_diarization
from app.domains.video_localization import service
from app.domains.video_localization import source_pipeline
from app.domains.video_localization import transcription
from app.domains.video_localization.operation_detail_errors import (
    OperationDetailRepairRequired,
)
from app.domains.video_localization.timeline_timecode import (
    format_timeline_position,
    format_timeline_range,
)
from app.errors import AppException
from app.schemas.video_localization_asr_raw_step import (
    AsrRawResultV2,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationOperationSummary,
    now_iso,
)
from app.services import (
    asr_selection_policy,
    database,
    llm_runtime,
    project_store,
    settings_store,
    video_localization_operation_attempt_store,
    video_localization_operation_artifact_store,
    video_localization_operation_execution,
    video_localization_operation_ledger_store,
    video_localization_operation_step_store,
    video_localization_operation_store,
    video_localization_llm_provider_execution,
    video_localization_export_destinations,
    video_localization_exports,
)
from app.services.video_localization_execution_fence import (
    ExecutionFence,
    ExecutionFenceLost,
    ExecutionOperationCancelled,
    execution_fence_scope,
)

OperationKind = operation_state.OperationKind
OperationStatus = operation_state.OperationStatus
OperationRuntimeKey = operation_runtime.OperationRuntimeKey

_scheduler: (
    operation_scheduler.ProjectFairOperationScheduler | None
) = None
_worker_threads: list[threading.Thread] = []
_worker_serial = 0
_lock = threading.Lock()
_runtime = operation_runtime.OperationRuntime()
_runner_id = uuid.uuid4().hex
_logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = operation_state.ACTIVE_STATUSES
_TERMINAL_STATUSES = operation_state.TERMINAL_STATUSES
_KIND_LABELS = operation_state.KIND_LABELS
DEVELOPMENT_SNAPSHOT_ROOT = (
    Path(tempfile.gettempdir()) / "voice-studio-development-snapshots"
)
DEVELOPMENT_LOCALIZATION_WORKFLOW_CHECKPOINT_ROOT = (
    DEVELOPMENT_SNAPSHOT_ROOT / "localization-workflow"
)
DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT = (
    DEVELOPMENT_SNAPSHOT_ROOT / "asr-workflow"
)
DEVELOPMENT_DUB_SUBTITLE_WORKFLOW_CHECKPOINT_ROOT = (
    DEVELOPMENT_SNAPSHOT_ROOT / "dub-subtitle-workflow"
)
_MANAGED_LOCAL_WALL_DURATION_STAGE_IDS = {
    "understand_document",
    "visual_evidence",
    "research",
    "normalize_entities",
    "section_review_r1",
    "review_decisions_r1",
    "whole_recheck_r1",
    "transcript_quality_gate",
}

def _is_partial_asr_operation(operation: VideoLocalizationOperation) -> bool:
    return bool(
        operation.kind == "english_asr"
        and str(operation.parameters.get("execution_mode") or "full") == "stop_after"
        and str(operation.parameters.get("stop_after_step") or "")
        in {
            "asr",
            "initial_analysis",
            "understand_document",
            "visual_evidence",
            "research",
            "normalize_entities",
            "section_review_r1",
            "review_decisions_r1",
            "whole_recheck_r1",
            "transcript_quality_gate",
        }
    )


def _is_asr_development_target_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return bool(
        operation.kind == "english_asr"
        and str(
            operation.parameters.get("execution_mode") or ""
        )
        == "development_target"
    )


def _is_raw_asr_operation(operation: VideoLocalizationOperation) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "") == "asr"
    )


def _is_initial_analysis_operation(operation: VideoLocalizationOperation) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "") == "initial_analysis"
    )


def _is_document_understanding_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "")
        == "understand_document"
    )


def _is_research_evidence_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "")
        == "research"
    )


def _is_visual_evidence_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "")
        == "visual_evidence"
    )


def _is_entity_normalization_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "")
        == "normalize_entities"
    )


def _is_section_review_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "")
        == "section_review_r1"
    )


def _is_review_decisions_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "")
        == "review_decisions_r1"
    )


def _is_whole_recheck_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "")
        == "whole_recheck_r1"
    )


def _is_transcript_quality_gate_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_partial_asr_operation(operation) and (
        str(operation.parameters.get("stop_after_step") or "")
        == "transcript_quality_gate"
    )


def _is_managed_initial_analysis_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_initial_analysis_execution
        .is_managed_initial_analysis_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_document_understanding_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_document_understanding_execution
        .is_managed_document_understanding_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_visual_evidence_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_visual_evidence_execution
        .is_managed_visual_evidence_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_research_evidence_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_research_evidence_execution
        .is_managed_research_evidence_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_entity_normalization_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_entity_normalization_execution
        .is_managed_entity_normalization_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_section_review_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_section_review_execution
        .is_managed_section_review_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_review_decisions_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_review_decisions_execution
        .is_managed_review_decisions_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_whole_recheck_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_whole_recheck_execution
        .is_managed_whole_recheck_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_transcript_quality_gate_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_transcript_quality_gate_execution
        .is_managed_transcript_quality_gate_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_partial_asr_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return any(
        predicate(operation)
        for predicate in (
            _is_managed_asr_raw_operation,
            _is_managed_initial_analysis_operation,
            _is_managed_document_understanding_operation,
            _is_managed_visual_evidence_operation,
            _is_managed_research_evidence_operation,
            _is_managed_entity_normalization_operation,
            _is_managed_section_review_operation,
            _is_managed_review_decisions_operation,
            _is_managed_whole_recheck_operation,
            _is_managed_transcript_quality_gate_operation,
        )
    )


def _is_development_operation(operation: VideoLocalizationOperation) -> bool:
    return bool(
        _is_partial_asr_operation(operation)
        or _is_asr_development_target_operation(operation)
        or operation.kind == "speaker_diarization"
        or (
            operation.kind == "localization_draft"
            and str(
                operation.parameters.get("execution_mode") or "full"
            )
            == "development_target"
        )
        or (
            operation.kind == "dub_subtitle_generation"
            and str(
                operation.parameters.get("execution_mode") or "full"
            )
            == "development_target"
        )
    )


def _status_kind(operation: VideoLocalizationOperation) -> OperationKind | None:
    return None if _is_development_operation(operation) else operation.kind


_TASK_STEP_LABELS: dict[str, dict[str, str]] = {
    "source_audio": {
        "extract_source_audio": "提取并保存原始音轨",
    },
    "stems": {
        "separate_stems": "生成并保存双轨分离结果",
    },
    "reference_clips": {
        "generate_reference_candidates": "裁切并保存参考音候选",
    },
    "english_asr": {
        "asr": "生成原始听写",
        "diarization": "区分说话人",
        "initial_analysis_join": "汇合听写与说话人",
        "normalize_entities": "统一名称与术语",
        "section_review_r1": "第 1 轮分段复查",
        "review_decisions_r1": "汇总第 1 轮修改",
        "whole_recheck_r1": "本地收尾检查",
        "visual_evidence": "画面取证",
        "web_research": "理解全文并核对名称",
        "text_review": "准备校对文本",
        "transcript_quality_gate": "进入校时前检查",
        "alignment": "对齐逐词时间",
        "audio_boundaries": "分析声音停顿",
        "boundary_review": "本地确定字幕断句",
        "subtitle_track": "生成并检查字幕轨",
    },
    "speaker_diarization": {
        "diarization": "区分说话人",
    },
    "localization_draft": {
        "lock_localization_source": "固定本次英文源数据",
        "lock_localization_context_intent": "固定本次本土化要求",
        "analyze_localization_document": "建立全文本土化创作提纲",
        "collect_localization_research_evidence_v3": "查询必要资料",
        "collect_localization_visual_evidence_v3": "查看必要画面",
        "adjudicate_localization_evidence_v3": "确认资料与画面结论",
        "lock_localization_creation_context": "锁定本土化创作策略",
        "generate_localization_spoken_script": "生成全文本土化初稿",
        "review_localization_fidelity": "复核原意与事实",
        "review_localization_naturalness": "盲测中文自然度",
        "finalize_localization_spoken_script": "本土化台词终审",
        "align_localization_semantics": "本地映射语义时间",
        "adjudicate_localization_alignment": "复核时间歧义",
        "build_localization_dual_tracks": "生成台词轨与上屏字幕",
        "adjudicate_localization_display_boundaries": "复核上屏字幕时间",
        "validate_localization_tracks": "检查本土化结果",
        "commit_localization_tracks": "保存正式本土化双轨",
    },
    "semantic_tts_grouping": {
        "prepare": "整理字幕和说话人",
        "group": "判断语义和场景",
        "validate": "检查分组完整性",
        "write": "保存语义分组",
    },
    "media_export": {
        "prepare": "检查导出内容与保存目录",
        "render": "生成所选成品",
        "validate": "检查并保存成品文件",
    },
}


def _asr_workflow_summary_fields(
    parameters: dict | None = None,
) -> dict:
    definition = workflow_contracts.asr_workflow_summary(
        include_diarization=_asr_summary_includes_diarization(
            parameters
        )
    )
    return {
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _asr_summary_includes_diarization(
    parameters: dict | None,
) -> bool:
    values = parameters or {}
    execution_mode = str(
        values.get("execution_mode") or "full"
    )
    if execution_mode == "full":
        return (
            bool(values.get("diarization_engine_id"))
            if "diarization_engine_id" in values
            else True
        )
    return bool(values.get("diarization_engine_id")) or str(
        values.get("development_target_step_id") or ""
    ) in {"diarization", "initial_analysis_join"}


def _localization_v3_workflow_summary_fields() -> dict:
    definition = workflow_contracts.localization_workflow_summary()
    return {
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _dub_subtitle_workflow_summary_fields() -> dict:
    definition = workflow_contracts.dub_subtitle_workflow_summary()
    return {
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _semantic_tts_grouping_workflow_summary_fields() -> dict:
    definition = (
        workflow_contracts.semantic_tts_grouping_workflow_summary()
    )
    return {
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
    }


def _source_audio_workflow_summary_fields() -> dict:
    return source_audio_operation_projection.workflow_summary_fields()


def _stem_separation_workflow_summary_fields() -> dict:
    return (
        stem_separation_operation_projection
        .workflow_summary_fields()
    )


def _reference_candidates_workflow_summary_fields() -> dict:
    return (
        reference_candidates_operation_projection
        .workflow_summary_fields()
    )


def _is_managed_source_audio_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return source_audio_execution.is_managed_source_audio_operation(
        operation.kind,
        str(
            operation.result_summary.get(
                "workflow_schema_version"
            )
            or ""
        ).strip(),
    )


def _is_managed_stem_separation_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        stem_separation_execution
        .is_managed_stem_separation_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_reference_candidates_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        reference_candidates_execution
        .is_managed_reference_candidates_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_speaker_diarization_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        speaker_diarization_execution
        .is_managed_speaker_diarization_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _is_managed_asr_raw_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return (
        asr_raw_execution.is_managed_asr_raw_operation(
            operation.kind,
            str(
                operation.result_summary.get(
                    "workflow_schema_version"
                )
                or ""
            ).strip(),
        )
    )


def _asr_workflow_stage_projection(
    stage_id: str,
    task_ids: set[str],
) -> dict:
    definition = workflow_contracts.asr_workflow_summary(
        include_diarization=bool(
            {"diarization", "initial_analysis_join"}
            & task_ids
        )
    )
    stage = next(
        item for item in definition["stages"] if item["id"] == stage_id
    )
    projected_stage = {
        **stage,
        "atomic_tasks": [
            item
            for item in stage["atomic_tasks"]
            if item["id"] in task_ids
        ],
    }
    if stage_id == "transcript_review" and task_ids == {"understand_document"}:
        projected_stage["description"] = (
            "本次开发单步只执行“理解全文并规划复查”；"
            "名称核对、文字修改和后续复查均未运行。"
        )
    if stage_id == "transcript_review" and task_ids == {"research"}:
        projected_stage["description"] = (
            "本次开发单步只查询全文理解提出的疑点；"
            "名称归一、文字修改和后续复查均未运行。"
        )
    if (
        stage_id == "transcript_review"
        and task_ids == {"visual_evidence"}
    ):
        projected_stage["description"] = (
            "本次开发单步只读取画面中的直接可见信息；"
            "不会识别人脸、决定规范名称或修改听写文字。"
        )
    if (
        stage_id == "transcript_review"
        and task_ids == {"section_review_r1"}
    ):
        projected_stage["description"] = (
            "本次开发单步只检查各区块中的可能听写问题；"
            "不会采纳修改，也不会改变当前上屏字幕。"
        )
    if (
        stage_id == "transcript_review"
        and task_ids == {"review_decisions_r1"}
    ):
        projected_stage["description"] = (
            "本次开发单步只判断上一任务提出的疑点；"
            "只采纳通过本地安全规则的修改，并实时更新当前阶段字幕。"
        )
    if (
        stage_id == "transcript_review"
        and task_ids == {"whole_recheck_r1"}
    ):
        projected_stage["description"] = (
            "本次开发单步只按本地规则收尾检查已确认的文字；"
            "保留具体复听提示，不调用模型，也不修改字幕。"
        )
    if (
        stage_id == "transcript_review"
        and task_ids == {"transcript_quality_gate"}
    ):
        projected_stage["description"] = (
            "本次开发单步只判断文字是否已经稳定到可以开始校时；"
            "不调用模型、不修改字幕，也不会执行逐词时间对齐。"
        )
    return {
        "workflow_schema_version": definition["schema_version"],
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": [projected_stage],
    }


def _asr_step_placeholders(
    parameters: dict | None = None,
) -> dict[str, dict]:
    placeholders = {
        step_id: {"label": label, "order": order, "status": "todo"}
        for step_id, label, order in asr_flow.ASR_STEP_PLAN
        if step_id in asr_flow.ASR_PLANNED_STEP_IDS
    }
    if _asr_summary_includes_diarization(parameters):
        placeholders = {
            **placeholders,
            "diarization": {
                "label": "区分匿名说话人",
                "order": 20,
                "status": "todo",
            },
            "initial_analysis_join": {
                "label": "汇合听写与说话人",
                "order": 25,
                "status": "todo",
            },
        }
    return dict(
        sorted(
            placeholders.items(),
            key=lambda item: int(item[1].get("order") or 0),
        )
    )


def _localization_step_placeholders() -> dict[str, dict]:
    return {
        task.id: {
            "label": task.label,
            "order": task.order,
            "status": "todo",
            "purpose": task.description,
        }
        for stage in workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION.stages
        for task in stage.atomic_tasks
    }


def _dub_subtitle_step_placeholders() -> dict[str, dict]:
    return {
        task.id: {
            "label": task.label,
            "order": task.order,
            "status": "todo",
            "purpose": task.description,
        }
        for stage in (
            workflow_contracts.DUB_SUBTITLE_WORKFLOW_DEFINITION.stages
        )
        for task in stage.atomic_tasks
    }


def _semantic_tts_grouping_step_placeholders() -> dict[str, dict]:
    return {
        task.id: {
            "label": task.label,
            "order": task.order,
            "status": "todo",
            "purpose": task.description,
        }
        for stage in (
            workflow_contracts
            .SEMANTIC_TTS_GROUPING_WORKFLOW_DEFINITION.stages
        )
        for task in stage.atomic_tasks
    }


def _mark_unused_localization_steps_skipped(summary: dict) -> dict:
    steps = summary.get("task_step_results")
    if not isinstance(steps, dict):
        return summary
    updated = dict(steps)
    tasks = [
        task
        for stage in workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION.stages
        for task in stage.atomic_tasks
    ]
    for task in tasks:
        step_id = task.id
        current = updated.get(step_id)
        if not isinstance(current, dict) or current.get("status") != "todo":
            continue
        updated[step_id] = {
            **current,
            "label": task.label,
            "order": task.order,
            "status": "skipped",
            "purpose": task.description,
            "summary": "任务在完成前已停止，因此没有执行这一步。",
            "metrics": [],
            "sections": [],
        }
    return {**summary, "task_step_results": updated}


def _mark_unused_asr_steps_skipped(summary: dict) -> dict:
    steps = summary.get("task_step_results")
    if not isinstance(steps, dict):
        return summary
    updated = {
        **_asr_step_placeholders(),
        **steps,
    }
    labels = {
        step_id: str(step.get("label") or step_id)
        for step_id, step in updated.items()
        if isinstance(step, dict)
    }
    deferred = updated.get("asr_review_deferred")
    deferred_error = (
        deferred.get("error_detail")
        if isinstance(deferred, dict)
        and isinstance(deferred.get("error_detail"), dict)
        else None
    )
    deferred_summary = (
        str(deferred.get("summary") or "").strip()
        if isinstance(deferred, dict)
        else ""
    )
    for step_id in list(updated):
        current = updated.get(step_id)
        if not isinstance(current, dict):
            continue
        if current.get("status") == "running":
            updated[step_id] = {
                **current,
                "label": labels[step_id],
                "status": "warning",
                "summary": (
                    deferred_summary
                    or "本步骤没有完成，任务已按降级策略继续生成字幕。"
                ),
                **(
                    {"error_detail": dict(deferred_error)}
                    if deferred_error is not None
                    else {}
                ),
            }
            continue
        if current.get("status") != "todo":
            continue
        updated[step_id] = {
            **current,
            "label": labels[step_id],
            "status": "skipped",
            "purpose": "这一步属于正常流程，但本次没有实际执行。",
            "summary": "本次条件不足或上一步已经给出可用结果，因此跳过。",
            "metrics": [],
            "sections": [],
        }
    return {**summary, "task_step_results": updated}


def _close_unfinished_task_steps_after_failure(
    summary: dict,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    """Make every child step terminal when its parent operation fails."""
    steps = summary.get("task_step_results")
    if not isinstance(steps, dict):
        return summary
    raw_error_detail = summary.get("error_detail")
    error_detail = (
        dict(raw_error_detail)
        if isinstance(raw_error_detail, dict)
        else {}
    )
    if error_code and not error_detail.get("code"):
        error_detail["code"] = error_code
    if error_message and not error_detail.get("message"):
        error_detail["message"] = error_message
    message = str(error_detail.get("message") or "当前步骤未完成。")
    if not error_detail.get("message"):
        error_detail["message"] = message
    updated = dict(steps)
    changed = False
    for step_id, result in steps.items():
        if not isinstance(result, dict):
            continue
        status = result.get("status")
        if status == "running":
            updated[step_id] = {
                **result,
                "status": "failed",
                "summary": message,
                "error_detail": error_detail,
            }
        elif status == "todo":
            updated[step_id] = {
                **result,
                "status": "cancelled",
                "summary": "前置步骤失败，本步骤未执行。",
            }
        else:
            continue
        changed = True
    if not changed:
        return summary
    updated_summary = {**summary, "task_step_results": updated}
    timings = summary.get("task_stage_timings")
    if isinstance(timings, dict):
        updated_summary["task_stage_timings"] = {
            step_id: (
                {
                    key: value
                    for key, value in timing.items()
                    if key != "running"
                }
                if isinstance(timing, dict)
                else timing
            )
            for step_id, timing in timings.items()
        }
    return updated_summary


def _mark_unfinished_task_steps_cancelled(summary: dict) -> dict:
    """Keep completed evidence while closing work that cannot continue."""

    steps = summary.get("task_step_results")
    updated_summary = dict(summary)
    if isinstance(steps, dict):
        updated_steps = dict(steps)
        for step_id, result in steps.items():
            if not isinstance(result, dict):
                continue
            step_status = result.get("status")
            if step_status not in {"running", "todo"}:
                continue
            updated_steps[step_id] = {
                **result,
                "status": "cancelled",
                "summary": (
                    "任务已取消，此步骤未完成。"
                    if step_status == "running"
                    else "任务已取消，此步骤未执行。"
                ),
            }
        updated_summary["task_step_results"] = updated_steps

    timings = summary.get("task_stage_timings")
    if isinstance(timings, dict):
        updated_summary["task_stage_timings"] = {
            step_id: (
                {
                    key: value
                    for key, value in timing.items()
                    if key != "running"
                }
                if isinstance(timing, dict)
                else timing
            )
            for step_id, timing in timings.items()
        }
    return updated_summary


def _normalize_terminal_operation_updates(updates: dict) -> dict:
    """Keep the persisted parent and child lifecycles consistent."""

    normalized = dict(updates)
    status = normalized.get("status")
    if status not in _TERMINAL_STATUSES:
        return normalized
    if not normalized.get("completed_at"):
        normalized["completed_at"] = now_iso()
    summary = normalized.get("result_summary")
    if not isinstance(summary, dict):
        return normalized
    if status == "success":
        steps = summary.get("task_step_results")
        failed_labels = (
            [
                str(result.get("label") or step_id)
                for step_id, result in steps.items()
                if isinstance(result, dict)
                and result.get("status") == "failed"
            ]
            if isinstance(steps, dict)
            else []
        )
        if failed_labels:
            normalized["status"] = "failed"
            if not normalized.get("error_code"):
                normalized["error_code"] = (
                    "VIDEO_LOCALIZATION_CHILD_STEP_FAILED"
                )
            if not normalized.get("error_message"):
                normalized["error_message"] = (
                    "子任务失败："
                    + "、".join(failed_labels[:3])
                    + "。"
                )
            status = "failed"
    if status == "failed":
        normalized["result_summary"] = (
            _close_unfinished_task_steps_after_failure(
                summary,
                error_code=str(normalized.get("error_code") or "") or None,
                error_message=(
                    str(normalized.get("error_message") or "") or None
                ),
            )
        )
    elif status == "cancelled":
        normalized["result_summary"] = (
            _mark_unfinished_task_steps_cancelled(summary)
        )
    return normalized


def _initial_operation_summary(
    kind: OperationKind,
    parameters: dict | None = None,
) -> dict:
    summary = {"stage": "准备处理"}
    if kind == "source_audio":
        summary.update(
            source_audio_operation_projection.initial_summary()
        )
    elif kind == "stems":
        summary.update(
            stem_separation_operation_projection.initial_summary()
        )
    elif kind == "reference_clips":
        summary.update(
            reference_candidates_operation_projection
            .initial_summary()
        )
    elif kind == "english_asr":
        if (
            str(
                (parameters or {}).get("execution_mode") or ""
            )
            == "development_target"
        ):
            target_step_id = str(
                (parameters or {}).get(
                    "development_target_step_id"
                )
                or ""
            )
            target_definition = (
                workflow_contracts.asr_workflow_summary(
                    include_diarization=target_step_id
                    in {"diarization", "initial_analysis_join"}
                )
            )
            target_stage_id = next(
                (
                    stage["id"]
                    for stage in target_definition["stages"]
                    if any(
                        task["id"] == target_step_id
                        for task in stage["atomic_tasks"]
                    )
                ),
                "initial_analysis",
            )
            summary.update(
                {
                    "stage": "准备重跑指定 ASR 子流程",
                    "stage_id": target_step_id,
                    "execution_mode": "development_target",
                    "development_target_step_id": target_step_id,
                    "formal_project_data_changed": False,
                    **_asr_workflow_stage_projection(
                        target_stage_id,
                        {target_step_id},
                    ),
                }
            )
            return summary
        summary.update(
            {
                "stage_id": "asr",
                "task_step_results": _asr_step_placeholders(parameters),
                **_asr_workflow_summary_fields(parameters),
            }
        )
    elif kind == "dub_subtitle_generation":
        summary.update(
            {
                "stage": "正在准备整条配音音轨",
                "stage_id": "prepare_track",
                "task_step_results": _dub_subtitle_step_placeholders(),
                **_dub_subtitle_workflow_summary_fields(),
            }
        )
    elif kind == "localization_draft":
        summary.update(
            {
                "stage": "准备新版本土化流程",
                "stage_id": "lock_localization_source",
                "task_step_results": _localization_step_placeholders(),
                **_localization_v3_workflow_summary_fields(),
            }
        )
    elif kind == "speaker_diarization":
        summary.update(
            speaker_diarization_operation_projection
            .initial_summary()
        )
    elif kind == "semantic_tts_grouping":
        summary.update(
            {
                "stage": "准备语义分组",
                "stage_id": "prepare",
                "task_step_results": (
                    _semantic_tts_grouping_step_placeholders()
                ),
                **_semantic_tts_grouping_workflow_summary_fields(),
            }
        )
    elif kind == "media_export":
        summary.update(
            {
                "stage": "正在准备导出",
                "stage_id": "prepare",
                "task_step_results": {
                    "prepare": {
                        "label": _TASK_STEP_LABELS[
                            "media_export"
                        ]["prepare"],
                        "order": 10,
                        "status": "todo",
                    },
                    "render": {
                        "label": _TASK_STEP_LABELS[
                            "media_export"
                        ]["render"],
                        "order": 20,
                        "status": "todo",
                    },
                    "validate": {
                        "label": _TASK_STEP_LABELS[
                            "media_export"
                        ]["validate"],
                        "order": 30,
                        "status": "todo",
                    },
                },
            }
        )
    return summary


def _fallback_error_detail(code: str, message: str, stage: str | None = None) -> dict:
    normalized = str(code or "").upper()
    source_media_prerequisite_codes = {
        "VIDEO_LOCALIZATION_RENDER_SOURCE_VIDEO_MISSING",
        "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING",
        "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND",
        "VIDEO_LOCALIZATION_SOURCE_MISSING",
        "VIDEO_LOCALIZATION_SOURCE_NOT_FOUND",
        "VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND",
    }
    if normalized in source_media_prerequisite_codes:
        advice = (
            ["先抽取原音轨，再重新提交当前任务。"]
            if "SOURCE_AUDIO" in normalized
            else ["先导入源视频，再重新提交当前任务。"]
        )
    elif "FINAL_QUALITY" in normalized or "QUALITY_GATE" in normalized:
        advice = ["查看终审列出的字幕、原文编号和修改建议；关键问题未解决时，现有字幕轨不会被覆盖。"]
    elif "STEM_SEPARATION_MODEL" in normalized:
        advice = ["前往引擎管理，一键安装并校验 BS-RoFormer 分离模型后重试。"]
    elif "STEM_SEPARATION_RUNTIME" in normalized:
        advice = ["确认后端使用项目虚拟环境启动，并重新安装视频本土化运行组件后重试。"]
    elif "ASR" in normalized and any(
        marker in normalized for marker in ("RUNTIME", "MISSING", "UNAVAILABLE")
    ):
        advice = ["确认后端使用项目虚拟环境启动，并检查默认 ASR 引擎运行环境后重试。"]
    elif any(
        marker in normalized for marker in ("CUE", "COVERAGE", "MAPPING", "TIMING")
    ):
        advice = ["先检查 ASR 字幕的原文、顺序和时间码，再重新提交本土化任务。"]
    elif any(
        marker in normalized
        for marker in ("REVIEW_UNRESOLVED", "REPAIR_EMPTY", "REPAIR_INVALID")
    ):
        advice = ["查看终审列出的原句、原因和修改建议；问题未解决时不会覆盖现有字幕轨，可修正原文后重试。"]
    elif any(marker in normalized for marker in ("LLM", "REVIEW", "LOCALIZATION")):
        advice = ["确认语言模型服务可用并查看失败步骤；终审未通过时不会覆盖现有字幕轨。"]
    else:
        advice = ["查看失败步骤和错误信息，修正输入或服务状态后再重试。"]
    rule_id, rule = _error_rule(code)
    return {
        "code": code,
        "message": message,
        "stage": stage or "任务处理",
        "severity": "fatal",
        "impact": "当前步骤无法产出可安全进入下一阶段的结构化结果，因此任务已停止。",
        "rule_id": rule_id,
        "rule": rule,
        "advice": advice,
    }


def _error_rule(code: str) -> tuple[str, str]:
    normalized = str(code or "").upper()
    if normalized in {
        "VIDEO_LOCALIZATION_RENDER_SOURCE_VIDEO_MISSING",
        "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING",
        "VIDEO_LOCALIZATION_SOURCE_AUDIO_NOT_FOUND",
        "VIDEO_LOCALIZATION_SOURCE_MISSING",
        "VIDEO_LOCALIZATION_SOURCE_NOT_FOUND",
        "VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND",
    }:
        return (
            "source_media_prerequisite",
            "任务必须在所需的源视频或源音轨可用后才能执行；缺少媒体时不得继续运行或伪造结果。",
        )
    if any(marker in normalized for marker in ("NUMBER", "CONTENT_ANCHOR")):
        return (
            "full_document_content_integrity",
            "完整结果必须在全文范围保留关键数字、数量、规格和专名；允许重组语序，不要求逐条字幕一一对应。",
        )
    if any(
        marker in normalized for marker in ("CUE", "COVERAGE", "MAPPING", "TIMING")
    ):
        return (
            "timeline_mapping_integrity",
            "锁定中文必须原样保留；字幕来源范围必须连续、无遗漏、无重复，最终时间必须为正且相邻字幕不能重叠。",
        )
    if any(marker in normalized for marker in ("REVIEW", "REPAIR", "QUALITY")):
        return (
            "localization_quality_convergence",
            "本土化结果应通过分组复核和全文复核；轻微建议可以警告通过，重大事实或语义问题必须修复后再写入。",
        )
    if "LLM" in normalized:
        return ("llm_execution", "语言模型请求必须完整返回可校验结果；长度问题应分窗处理，其他运行错误应明确报告。")
    if "ASR" in normalized:
        return ("asr_execution", "ASR 任务必须产出可用、连续且可追溯的原文字幕，失败时不得伪造后续结果。")
    return ("operation_execution", "任务必须完成当前步骤的输入、输出和结构校验后才能进入下一步。")


def _asr_stage_id(stage: str) -> str:
    normalized = str(stage or "")
    dynamic_match = re.fullmatch(r"flow:([A-Za-z0-9_-]+)(?:\|.*)?", normalized)
    if dynamic_match:
        return dynamic_match.group(1)
    for stage_id, markers in (
        ("diarization", ("区分说话人",)),
        ("web_research", ("联网核验",)),
        ("text_review", ("校对识别", "文本校对")),
        ("transcript_quality_gate", ("整篇 ASR 终审", "整篇 ASR 保守返修", "ASR 返修", "ASR 终审通过")),
        ("alignment", ("逐词时间码", "强制对齐")),
        ("audio_boundaries", ("声学边界",)),
        ("boundary_review", ("字幕断句", "复核断句", "按时间码与停顿生成断句")),
        ("subtitle_track", ("生成字幕轨",)),
    ):
        if any(marker in normalized for marker in markers):
            return stage_id
    return "asr"


def _localization_stage_id(stage: str) -> str:
    normalized = str(stage or "")
    dynamic_match = re.fullmatch(r"flow:([A-Za-z0-9_-]+)(?:\|.*)?", normalized)
    if dynamic_match:
        return dynamic_match.group(1)
    return "lock_localization_source"


class _StageTimer:
    """Continuously attributes operation wall time to exactly one visible step."""

    def __init__(self, stage_resolver: Callable[[str], str], *, clock=time.perf_counter):
        self._clock = clock
        self._stage_resolver = stage_resolver
        self._current_stage_id = stage_resolver("")
        self._current_started_at = clock()
        self._completed: dict[str, int] = {}

    def update(self, stage: str) -> dict[str, dict[str, int | bool]]:
        now = self._clock()
        next_stage_id = self._stage_resolver(stage)
        if next_stage_id != self._current_stage_id:
            self._completed[self._current_stage_id] = self._elapsed_ms(now)
            self._current_stage_id = next_stage_id
            self._current_started_at = now
        return self.snapshot(now=now)

    def snapshot(self, *, now: float | None = None) -> dict[str, dict[str, int | bool]]:
        observed_at = self._clock() if now is None else now
        timings: dict[str, dict[str, int | bool]] = {
            stage_id: {"duration_ms": duration_ms} for stage_id, duration_ms in self._completed.items()
        }
        timings[self._current_stage_id] = {
            "duration_ms": self._elapsed_ms(observed_at),
            "running": True,
        }
        return timings

    def finish(self) -> dict[str, dict[str, int]]:
        now = self._clock()
        self._completed[self._current_stage_id] = self._elapsed_ms(now)
        return {stage_id: {"duration_ms": duration_ms} for stage_id, duration_ms in self._completed.items()}

    def _elapsed_ms(self, now: float) -> int:
        return max(0, round((now - self._current_started_at) * 1000))


class _AsrStageTimer(_StageTimer):
    """ASR-specific wall-time attribution."""

    def __init__(self, *, clock=time.perf_counter):
        super().__init__(_asr_stage_id, clock=clock)


def _finish_stage_timings(stage_timer: _StageTimer | None, summary: dict) -> dict:
    if stage_timer is None:
        return summary
    wall_timings = stage_timer.finish()
    recorded_timings = (
        summary.get("stage_timings")
        if isinstance(summary.get("stage_timings"), dict)
        else summary.get("task_stage_timings")
    )
    timings = (
        {
            step_id: dict(value)
            for step_id, value in recorded_timings.items()
            if isinstance(step_id, str)
            and isinstance(value, dict)
            and value.get("duration_ms") is not None
        }
        if isinstance(recorded_timings, dict)
        else wall_timings
    )
    if not timings:
        timings = wall_timings
    task_duration_ms = summary.get("duration_ms")
    if not isinstance(task_duration_ms, (int, float)):
        task_duration_ms = sum(
            int(item.get("duration_ms") or 0)
            for item in wall_timings.values()
        )
    return {
        **summary,
        "task_stage_timings": timings,
        "task_duration_ms": max(0, int(task_duration_ms)),
    }


def _cancelled_task_summary(
    summary: dict,
    *,
    stage: str,
    stage_timer: _StageTimer | None = None,
) -> dict:
    finished = _finish_stage_timings(stage_timer, dict(summary))
    return _mark_unfinished_task_steps_cancelled(
        {
            **finished,
            "stage": stage,
            "preview_cues": [],
        }
    )


def _operation_commit_gate(
    project_id: str,
    operation_id: str,
) -> operation_runtime.OperationCommitGate:
    return _runtime.commit_gate((project_id, operation_id))


def start_worker() -> None:
    global _scheduler, _worker_threads, _worker_serial
    video_localization_operation_store.backfill_legacy_projects()
    recover = False
    with _lock:
        scheduler = _scheduler
        if scheduler is None or scheduler.closed:
            worker_count = operation_scheduler.parse_worker_count(
                os.environ.get(
                    "VOICE_STUDIO_VIDEO_LOCALIZATION_WORKERS"
                )
            )
            scheduler = (
                operation_scheduler.ProjectFairOperationScheduler(
                    worker_count=worker_count,
                )
            )
            _scheduler = scheduler
            _worker_threads = []
            recover = True
        _worker_threads = [
            thread
            for thread in _worker_threads
            if thread.is_alive()
        ]
        missing_workers = (
            scheduler.worker_count - len(_worker_threads)
        )
        for _index in range(missing_workers):
            _worker_serial += 1
            thread = threading.Thread(
                target=_worker,
                args=(scheduler,),
                daemon=True,
                name=(
                    "video-localization-operation-worker-"
                    f"{_worker_serial}"
                ),
            )
            _worker_threads.append(thread)
            thread.start()
    if recover:
        _recover_active_operations()


async def shutdown() -> None:
    global _scheduler, _worker_threads
    with _lock:
        scheduler = _scheduler
        threads = list(_worker_threads)
        _scheduler = None
        _worker_threads = []
    if scheduler is not None:
        scheduler.close()
    deadline = time.monotonic() + 2
    for thread in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if thread.is_alive():
            await asyncio.to_thread(thread.join, remaining)
    _runtime.reset()


def list_operations(project_id: str) -> list[VideoLocalizationOperation] | None:
    stored = (
        video_localization_operation_store
        .read_project_operations(project_id)
    )
    if stored.project_exists:
        try:
            return sorted(
                (
                    VideoLocalizationOperation.model_validate(operation)
                    for operation in stored.operations
                ),
                key=lambda item: (
                    item.created_at,
                    item.operation_id,
                ),
                reverse=True,
            )
        except (TypeError, ValueError):
            pass
    draft = service.get_video_localization(project_id)
    return _sorted_operations(draft) if draft is not None else None


def _sorted_operations(draft: VideoLocalizationDraft) -> list[VideoLocalizationOperation]:
    return sorted(
        draft.operations,
        key=lambda item: (
            item.created_at,
            item.operation_id,
        ),
        reverse=True,
    )






































def project_repository_operation_summary(
    operation: VideoLocalizationOperationSummary,
) -> VideoLocalizationOperationSummary:
    """Apply bounded dynamic context without reading the Project mirror."""

    return _project_feed_summary(
        operation,
        draft=None,
        managed_artifacts=True,
    )


def _project_feed_summary(
    operation: VideoLocalizationOperation,
    *,
    draft: VideoLocalizationDraft | None,
    managed_artifacts: bool,
) -> VideoLocalizationOperationSummary:
    stage_override, artifact_available = (
        _development_summary_context(
            operation,
            managed_artifacts=managed_artifacts,
        )
    )
    return operation_summary_projection.project_operation_summary(
        operation,
        result_summary_seed=(
            operation_summary_projection.durable_media_summary(
                draft,
                operation,
            )
            if draft is not None
            else None
        ),
        stage_override=stage_override,
        artifact_available=artifact_available,
    )


def _development_summary_context(
    operation: VideoLocalizationOperation,
    *,
    managed_artifacts: bool,
) -> tuple[str | None, bool | None]:
    del managed_artifacts
    if operation.status != "success":
        return None, None
    if _is_managed_speaker_diarization_operation(operation):
        return "说话人区分已完成（开发单步）", None
    if _is_managed_asr_raw_operation(operation):
        return "原始听写已完成（开发断点）", None
    if _is_managed_initial_analysis_operation(operation):
        return (
            "原始听写与说话人区分已完成（并行开发断点）",
            None,
        )
    managed_stages = (
        (
            _is_managed_document_understanding_operation,
            "理解全文并规划复查已完成（开发单步）",
        ),
        (
            _is_managed_visual_evidence_operation,
            "画面取证已完成（开发单步）",
        ),
        (
            _is_managed_research_evidence_operation,
            "资料查询已完成（开发单步）",
        ),
        (
            _is_managed_entity_normalization_operation,
            "统一名称与术语已完成（开发单步）",
        ),
        (
            _is_managed_section_review_operation,
            "第 1 轮分段复查已完成（开发单步）",
        ),
        (
            _is_managed_review_decisions_operation,
            "汇总第 1 轮修改已完成（开发单步）",
        ),
        (
            _is_managed_whole_recheck_operation,
            "本地收尾检查已完成（开发单步）",
        ),
        (
            _is_managed_transcript_quality_gate_operation,
            "进入校时前检查已完成（开发单步）",
        ),
    )
    for predicate, stage in managed_stages:
        if predicate(operation):
            return stage, None
    return None, None


def _compact_live_step_result(result: dict) -> dict:
    return operation_summary_projection.compact_live_step_result(
        result
    )


def _merge_task_step_results(previous: dict, current: dict) -> dict:
    merged = dict(current)
    previous_steps = previous.get("task_step_results")
    current_steps = current.get("task_step_results")
    if isinstance(previous_steps, dict) or isinstance(current_steps, dict):
        previous_steps = previous_steps if isinstance(previous_steps, dict) else {}
        current_steps = current_steps if isinstance(current_steps, dict) else {}
        step_ids = [*previous_steps, *(step_id for step_id in current_steps if step_id not in previous_steps)]
        merged_steps = {}
        for step_id in step_ids:
            previous_step = previous_steps.get(step_id)
            current_step = current_steps.get(step_id)
            if isinstance(previous_step, dict) and isinstance(current_step, dict):
                merged_steps[step_id] = {**previous_step, **current_step}
            else:
                merged_steps[step_id] = current_step if current_step is not None else previous_step
        merged["task_step_results"] = merged_steps
    return merged


def _merge_authoritative_task_step_results(
    observed: object,
    authoritative: object,
) -> dict:
    """Apply final business results without discarding same-run observability."""

    observed_steps = observed if isinstance(observed, dict) else {}
    authoritative_steps = authoritative if isinstance(authoritative, dict) else {}
    merged_steps: dict[str, dict] = {}
    for step_id, final_step in authoritative_steps.items():
        if not isinstance(final_step, dict):
            continue
        observed_step = observed_steps.get(step_id)
        merged_step = (
            {**observed_step, **final_step}
            if isinstance(observed_step, dict)
            else dict(final_step)
        )
        observed_debug = (
            observed_step.get("debug")
            if isinstance(observed_step, dict)
            else None
        )
        if isinstance(observed_debug, dict):
            merged_step["debug"] = observed_debug
        merged_steps[str(step_id)] = merged_step
    return merged_steps


def _build_task_final_result(operation: VideoLocalizationOperation, summary: dict) -> dict:
    task_label = str(operation.label or _KIND_LABELS.get(operation.kind) or "后台任务")
    raw_steps = summary.get("task_step_results")
    steps = {
        step_id: result
        for step_id, result in (
            raw_steps.items() if isinstance(raw_steps, dict) else ()
        )
        if not isinstance(result, dict)
        or str(result.get("status") or "").strip().lower() != "skipped"
    }
    labels = _TASK_STEP_LABELS.get(operation.kind, {})
    has_dynamic_metadata = any(
        isinstance(result, dict) and (result.get("label") or result.get("order") is not None)
        for result in steps.values()
    )
    if has_dynamic_metadata:
        insertion_order = {step_id: index for index, step_id in enumerate(steps)}

        def dynamic_order(step_id: str) -> tuple[int, int]:
            result = steps.get(step_id)
            order = result.get("order") if isinstance(result, dict) else None
            if isinstance(order, (int, float)) and not isinstance(order, bool):
                return int(order), insertion_order[step_id]
            return 1_000_000 + insertion_order[step_id], insertion_order[step_id]

        ordered_step_ids = sorted(steps, key=dynamic_order)
    else:
        ordered_step_ids = [step_id for step_id in labels if step_id in steps]
        ordered_step_ids.extend(step_id for step_id in steps if step_id not in labels)
    step_records = [
        (
            str(step_id),
            {
                "title": str(
                    result.get("label")
                    or labels.get(str(step_id), str(step_id))
                ),
                "text": str(
                    result.get("summary")
                    or result.get("purpose")
                    or "该步骤已完成。"
                ),
                "meta": _task_step_status_label(result.get("status")),
                "tone": (
                    "warning"
                    if result.get("status") in {"warning", "failed"}
                    else "positive"
                ),
            },
        )
        for step_id in ordered_step_ids
        if isinstance((result := steps[step_id]), dict)
    ]
    step_items = [item for _step_id, item in step_records]
    warning_count = sum(
        str(result.get("status") or "") in {"warning", "passed_with_warnings", "partial"}
        for result in steps.values()
        if isinstance(result, dict)
    )
    failed_count = sum(
        str(result.get("status") or "") == "failed"
        for result in steps.values()
        if isinstance(result, dict)
    )
    metrics = [
        {"label": "已执行步骤", "value": str(len(step_items))},
        {"label": "有建议的步骤", "value": str(warning_count)},
        {"label": "失败的步骤", "value": str(failed_count)},
    ]
    outcome = "任务处理完成"
    if operation.kind == "english_asr":
        cue_count = summary.get("cue_count")
        segment_count = summary.get("segment_count")
        if isinstance(cue_count, int) and not isinstance(cue_count, bool):
            metrics.append({"label": "ASR 字幕", "value": str(cue_count)})
            outcome = f"生成 {cue_count} 条 ASR 字幕"
        if isinstance(segment_count, int) and not isinstance(segment_count, bool):
            metrics.append({"label": "识别片段", "value": str(segment_count)})
            if not isinstance(cue_count, int) or isinstance(cue_count, bool):
                outcome = f"识别 {segment_count} 个语音片段"
    elif operation.kind == "localization_draft":
        localized_count = summary.get("localized_subtitle_count")
        if isinstance(localized_count, int) and not isinstance(localized_count, bool):
            metrics.append({"label": "本土化字幕", "value": str(localized_count)})
            outcome = f"生成 {localized_count} 条本土化字幕"
    elif operation.kind == "dub_subtitle_generation":
        dub_subtitle_count = summary.get("dub_subtitle_count")
        review_count = summary.get("review_count")
        asr_call_count = summary.get("asr_call_count")
        correction_count = summary.get("correction_count")
        if isinstance(
            dub_subtitle_count,
            int,
        ) and not isinstance(dub_subtitle_count, bool):
            metrics.append(
                {
                    "label": "合成配音字幕",
                    "value": str(dub_subtitle_count),
                }
            )
            outcome = (
                f"生成 {dub_subtitle_count} 条合成配音字幕"
            )
        if isinstance(
            review_count,
            int,
        ) and not isinstance(review_count, bool):
            metrics.append(
                {
                    "label": "建议复听",
                    "value": str(review_count),
                }
            )
        if isinstance(
            asr_call_count,
            int,
        ) and not isinstance(asr_call_count, bool):
            metrics.append(
                {
                    "label": "ASR 调用",
                    "value": str(asr_call_count),
                }
            )
        if isinstance(
            correction_count,
            int,
        ) and not isinstance(correction_count, bool):
            metrics.append(
                {
                    "label": "已纠正文字差异",
                    "value": str(correction_count),
                }
            )
    elif operation.kind == "semantic_tts_grouping":
        group_count = summary.get("semantic_group_count")
        if isinstance(group_count, int) and not isinstance(group_count, bool):
            metrics.append({"label": "配音语义组", "value": str(group_count)})
            outcome = f"生成 {group_count} 个配音语义组"
    elif operation.kind == "media_export":
        filename = str(summary.get("filename") or "").strip()
        size_bytes = summary.get("size_bytes")
        if filename:
            outcome = f"已保存 {filename}"
            metrics.append({"label": "成品文件", "value": filename})
        if isinstance(size_bytes, int) and not isinstance(
            size_bytes,
            bool,
        ):
            metrics.append(
                {
                    "label": "文件大小",
                    "value": _file_size_label(size_bytes),
                }
            )
    duration_ms = summary.get("task_duration_ms")
    if isinstance(duration_ms, int) and not isinstance(duration_ms, bool):
        metrics.append({"label": "任务耗时", "value": _task_duration_label(duration_ms)})

    sections = [{"title": "任务步骤", "items": step_items}] if step_items else []
    workflow_definition = {
        "source_audio": (
            workflow_contracts.SOURCE_AUDIO_WORKFLOW_DEFINITION
        ),
        "stems": (
            workflow_contracts
            .STEM_SEPARATION_WORKFLOW_DEFINITION
        ),
        "reference_clips": (
            workflow_contracts
            .REFERENCE_CANDIDATES_WORKFLOW_DEFINITION
        ),
        "english_asr": workflow_contracts.ASR_WORKFLOW_DEFINITION,
        "dub_subtitle_generation": (
            workflow_contracts.DUB_SUBTITLE_WORKFLOW_DEFINITION
        ),
        "localization_draft": (
            workflow_contracts.LOCALIZATION_WORKFLOW_V3_DEFINITION
        ),
    }.get(operation.kind)
    if workflow_definition is not None and step_records:
        item_by_step_id = dict(step_records)
        grouped_sections = []
        grouped_step_ids: set[str] = set()
        for stage in workflow_definition.stages:
            stage_items = [
                item_by_step_id[task.id]
                for task in stage.atomic_tasks
                if task.id in item_by_step_id
            ]
            if not stage_items:
                continue
            grouped_sections.append(
                {
                    "title": stage.label,
                    "items": stage_items,
                    "open_by_default": True,
                }
            )
            grouped_step_ids.update(
                task.id
                for task in stage.atomic_tasks
                if task.id in item_by_step_id
            )
        remaining_items = [
            item
            for step_id, item in step_records
            if step_id not in grouped_step_ids
        ]
        if remaining_items:
            grouped_sections.append(
                {
                    "title": "其他已执行步骤",
                    "items": remaining_items,
                    "open_by_default": True,
                }
            )
        sections = grouped_sections

    final_asr_step = (
        steps.get("subtitle_track")
        if operation.kind == "english_asr"
        else None
    )
    final_asr_gate = (
        final_asr_step.get("final_quality_gate")
        if isinstance(final_asr_step, dict)
        and isinstance(final_asr_step.get("final_quality_gate"), dict)
        else None
    )
    if final_asr_gate is not None:
        review_targets = [
            dict(item)
            for item in final_asr_step.get("review_targets", [])
            if isinstance(item, dict)
            and str(item.get("detail") or "").strip()
        ][:12]
        final_blocker_count = int(final_asr_gate.get("blocker_count") or 0)
        final_warning_count = int(final_asr_gate.get("warning_count") or 0)
        metrics.extend(
            [
                {"label": "最终硬问题", "value": str(final_blocker_count)},
                {"label": "最终提醒", "value": str(final_warning_count)},
            ]
        )
    else:
        review_targets = _task_final_review_targets(
            steps,
            ordered_step_ids,
            labels,
        )
        final_blocker_count = 0
        final_warning_count = 0
    execution_mode = str(operation.parameters.get("execution_mode") or "full")
    debug_metrics = [
        {"label": "任务编号", "value": operation.operation_id},
        {
            "label": "执行方式",
            "value": "正式完整流程" if execution_mode == "full" else "开发单步",
        },
    ]
    if summary.get("workflow_id"):
        debug_metrics.append(
            {"label": "流程标识", "value": str(summary["workflow_id"])}
        )
    if summary.get("workflow_schema_version"):
        debug_metrics.append(
            {
                "label": "流程契约",
                "value": str(summary["workflow_schema_version"]),
            }
        )
    if operation.kind == "english_asr" and final_asr_gate is not None:
        completion_detail = (
            f"最终检查有 {final_blocker_count} 个阻断问题、"
            f"{final_warning_count} 条可选建议"
        )
    elif failed_count:
        completion_detail = (
            f"{failed_count} 个步骤失败，{warning_count} 个步骤有建议"
        )
    elif warning_count:
        completion_detail = f"{warning_count} 个步骤有非阻断建议"
    else:
        completion_detail = "全部步骤正常完成"
    return {
        "detail_mode": "workflow_summary",
        "status": (
            "warning"
            if (
                final_blocker_count or final_warning_count
                if final_asr_gate is not None
                else warning_count or failed_count
            )
            else "success"
        ),
        "purpose": (
            "汇总本次实际执行的步骤、字幕产物和需要复核的地方；"
            "没有执行的可选步骤不会显示。"
            if operation.kind == "english_asr"
            else task_label
        ),
        "summary": (
            f"{task_label}已完成，{outcome}；共执行 {len(step_items)} 个步骤，"
            f"{completion_detail}。"
        ),
        "metrics": metrics,
        "sections": sections,
        "review_targets": review_targets,
        "notes": (
            [
                "这些建议不会中断流程；需要复听或抽查的内容已集中列在上方，具体过程仍保留在对应步骤中。"
            ]
            if review_targets
            else []
        ),
        "coverage": {
            "mode": "complete",
            "shown_count": len(step_items),
            "total_count": len(step_items),
            "unit": "个任务步骤",
        },
        "debug": {
            "description": "用于核对本次完整流程的执行范围和结果来源。",
            "metrics": debug_metrics,
            "sections": [],
            "notes": [
                "步骤清单来自本次任务实际保存的结果。",
                "模型调用次数、Token、费用和停止原因请在对应子步骤的调试信息中查看；任务未保存的用量不会补写或估算。",
            ],
        },
    }


def _finalize_formal_operation(
    draft: VideoLocalizationDraft,
    operation_id: str,
    formal_summary: dict,
    completed_at: str,
) -> VideoLocalizationDraft:
    """Purely project one formal workflow result into terminal Draft state."""

    current_operation = operation_state.operation_from_draft(
        draft,
        operation_id,
    )
    if current_operation is None:
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_OPERATION_NOT_FOUND",
            "没有找到本次本土化字幕任务。",
        )
    if operation_state.operation_was_cancelled(current_operation):
        raise ExecutionOperationCancelled(
            "localization operation was cancelled before formal commit"
        )
    terminal_summary = _merge_task_step_results(
        dict(current_operation.result_summary),
        formal_summary,
    )
    if current_operation.kind == "english_asr":
        formal_steps = formal_summary.get("task_step_results")
        if isinstance(formal_steps, dict):
            terminal_summary["task_step_results"] = (
                _merge_authoritative_task_step_results(
                    current_operation.result_summary.get(
                        "task_step_results"
                    ),
                    formal_steps,
                )
            )
    recorded_timings = terminal_summary.get("stage_timings")
    if isinstance(recorded_timings, dict):
        terminal_summary["task_stage_timings"] = {
            step_id: {
                key: value
                for key, value in timing.items()
                if key != "running"
            }
            for step_id, timing in recorded_timings.items()
            if isinstance(step_id, str) and isinstance(timing, dict)
        }
    duration_ms = terminal_summary.get("duration_ms")
    if isinstance(duration_ms, (int, float)) and not isinstance(
        duration_ms,
        bool,
    ):
        terminal_summary["task_duration_ms"] = max(
            0,
            int(duration_ms),
        )
    if current_operation.kind == "localization_draft":
        terminal_summary = _mark_unused_localization_steps_skipped(
            terminal_summary
        )
    elif current_operation.kind == "english_asr":
        terminal_summary = _mark_unused_asr_steps_skipped(
            terminal_summary
        )
    terminal_summary["task_final_result"] = _build_task_final_result(
        current_operation,
        terminal_summary,
    )
    terminal_updates = _normalize_terminal_operation_updates(
        {
            "status": "success",
            "progress": 1.0,
            "completed_at": completed_at,
            "error_code": None,
            "error_message": None,
            "result_summary": terminal_summary,
        }
    )
    return operation_state.with_operation_updates(
        draft,
        operation_id,
        terminal_updates,
        kind=current_operation.kind,
    )


def _formal_operation_already_committed(
    operation: VideoLocalizationOperation,
    latest: VideoLocalizationOperation | None,
) -> bool:
    return bool(
        operation.kind in {"english_asr", "localization_draft"}
        and not _is_development_operation(operation)
        and latest is not None
        and latest.status == "success"
    )


def _log_formal_post_commit_failure(
    operation: VideoLocalizationOperation,
    exc: BaseException,
) -> None:
    _logger.warning(
        "formal operation observer failed after terminal commit "
        "(project_id=%s operation_id=%s kind=%s error_type=%s)",
        operation.project_id,
        operation.operation_id,
        operation.kind,
        type(exc).__name__,
    )


def _task_final_review_targets(
    steps: dict,
    ordered_step_ids: list[str],
    labels: dict,
    *,
    limit: int = 12,
) -> list[dict]:
    warning_steps = [
        (step_id, result)
        for step_id in ordered_step_ids
        if isinstance((result := steps.get(step_id)), dict)
        and str(result.get("status") or "") in {
            "warning",
            "passed_with_warnings",
            "partial",
            "failed",
        }
    ]
    candidates: list[dict] = []

    # Prefer the exact, actionable locations saved by an atomic task.
    for _step_id, result in warning_steps:
        explicit = result.get("review_targets")
        if isinstance(explicit, list):
            candidates.extend(
                dict(item)
                for item in explicit
                if isinstance(item, dict)
                and str(item.get("detail") or "").strip()
            )

    # Older tasks may only have warning items in their saved result sections.
    for step_id, result in warning_steps:
        explicit = result.get("review_targets")
        if isinstance(explicit, list) and explicit:
            continue
        for section in result.get("sections", []):
            if not isinstance(section, dict):
                continue
            for item in section.get("items", []):
                if (
                    not isinstance(item, dict)
                    or str(item.get("tone") or "") != "warning"
                ):
                    continue
                detail = str(item.get("text") or "").strip()
                if not detail:
                    continue
                candidates.append(
                    {
                        "title": str(
                            item.get("title")
                            or result.get("label")
                            or labels.get(str(step_id), str(step_id))
                        ),
                        "location": str(item.get("meta") or "").strip(),
                        "detail": detail,
                    }
                )

    # A step-level summary is still better than hiding a warning entirely.
    for step_id, result in warning_steps:
        explicit = result.get("review_targets")
        warning_items = [
            item
            for section in result.get("sections", [])
            if isinstance(section, dict)
            for item in section.get("items", [])
            if isinstance(item, dict)
            and str(item.get("tone") or "") == "warning"
            and str(item.get("text") or "").strip()
        ]
        if (isinstance(explicit, list) and explicit) or warning_items:
            continue
        candidates.append(
            {
                "title": str(
                    result.get("label")
                    or labels.get(str(step_id), str(step_id))
                ),
                "detail": str(
                    result.get("summary")
                    or "这一步已完成，但仍有内容建议复核。"
                ),
            }
        )

    deduplicated = []
    seen_details: set[str] = set()
    for item in candidates:
        detail = " ".join(str(item.get("detail") or "").split())
        location = " ".join(str(item.get("location") or "").split())
        if not detail:
            continue
        if detail in seen_details:
            continue
        seen_details.add(detail)
        deduplicated.append(
            {
                "title": str(item.get("title") or "建议复核"),
                **({"location": location} if location else {}),
                "detail": detail,
            }
        )
        if len(deduplicated) >= limit:
            break
    return deduplicated


def _task_step_status_label(value: object) -> str:
    return {
        "success": "已完成",
        "completed": "已完成",
        "warning": "需复核",
        "passed_with_warnings": "需复核",
        "partial": "部分完成",
        "failed": "失败",
        "skipped": "已跳过",
        "running": "处理中",
    }.get(str(value or "").strip().lower(), "已完成")


def _task_duration_label(duration_ms: int) -> str:
    total_seconds = max(0, round(duration_ms / 1000))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes and seconds:
        return f"{minutes} 分 {seconds} 秒"
    if minutes:
        return f"{minutes} 分"
    return f"{seconds} 秒"


def _file_size_label(size_bytes: int) -> str:
    size = max(0, size_bytes)
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} GB"
    if size >= 1024**2:
        return f"{size / 1024**2:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def get_operation(project_id: str, operation_id: str) -> VideoLocalizationOperation | None:
    project_exists, stored = (
        video_localization_operation_store.read_operation(
            project_id,
            operation_id,
        )
    )
    if stored is not None:
        try:
            return _project_operation_detail(
                project_id,
                VideoLocalizationOperation.model_validate(stored)
            )
        except (TypeError, ValueError):
            project_exists = False
    if project_exists:
        return None
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    operation = next(
        (
            operation
            for operation in draft.operations
            if operation.operation_id == operation_id
        ),
        None,
    )
    return (
        _project_operation_detail(project_id, operation)
        if operation is not None
        else None
    )


def _project_operation_detail(
    project_id: str,
    operation: VideoLocalizationOperation,
) -> VideoLocalizationOperation:
    summary = dict(operation.result_summary)
    changed = False
    if operation.status == "failed":
        terminal_summary = _close_unfinished_task_steps_after_failure(
            summary,
            error_code=operation.error_code,
            error_message=operation.error_message,
        )
        if terminal_summary != summary:
            summary = terminal_summary
            changed = True
    elif operation.status == "cancelled":
        terminal_summary = _mark_unfinished_task_steps_cancelled(summary)
        if terminal_summary != summary:
            summary = terminal_summary
            changed = True
    if operation.kind == "localization_draft":
        projected_summary = project_saved_finalization_warning(summary)
        if projected_summary != summary:
            summary = projected_summary
            changed = True
        raw_steps = summary.get("task_step_results")
        visual_step = (
            raw_steps.get("collect_localization_visual_evidence_v3")
            if isinstance(raw_steps, dict)
            else None
        )
        if isinstance(visual_step, dict):
            projected_visual_step = (
                _with_localization_visual_evidence_links(
                    project_id,
                    operation.operation_id,
                    visual_step,
                )
            )
            if projected_visual_step != visual_step:
                summary["task_step_results"] = {
                    **raw_steps,
                    "collect_localization_visual_evidence_v3": (
                        projected_visual_step
                    ),
                }
                changed = True
    if (
        not _is_section_review_operation(operation)
        or operation.status != "success"
        or int(summary.get("failed_section_count") or 0) != 0
    ):
        return (
            operation.model_copy(update={"result_summary": summary})
            if changed
            else operation
        )
    raw_steps = summary.get("task_step_results")
    if isinstance(raw_steps, dict):
        review_step = raw_steps.get("section_review_r1")
        if (
            isinstance(review_step, dict)
            and review_step.get("status") == "warning"
        ):
            summary["task_step_results"] = {
                **raw_steps,
                "section_review_r1": {
                    **review_step,
                    "status": "success",
                },
            }
            changed = True
    return (
        operation.model_copy(update={"result_summary": summary})
        if changed
        else operation
    )


def get_development_asr_result(
    project_id: str,
    operation_id: str,
) -> transcription.TranscribeRawOutput | AsrRawResultV2 | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if not _is_raw_asr_operation(operation) or operation.status != "success":
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_ASR_RESULT_UNAVAILABLE",
            "当前任务没有可读取的原始 ASR 开发结果。",
        )

    if not _is_managed_asr_raw_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前原始听写工作流，已拒绝读取。",
        )
    try:
        result = asr_raw_detail_reader.read_asr_raw_result(
            project_id,
            operation_id,
            file_backend=managed_artifact_files,
        )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    if result is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_ASR_RESULT_UNAVAILABLE",
            "当前任务缺少可读取的原始 ASR 开发结果。",
        )
    return result








































































































def get_development_initial_analysis_result(
    project_id: str,
    operation_id: str,
) -> asr_pipeline.AsrInitialAnalysisSnapshot | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if not _is_initial_analysis_operation(operation) or operation.status != "success":
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_INITIAL_ANALYSIS_RESULT_UNAVAILABLE",
            "当前任务没有可读取的 ASR 初始分析开发结果。",
        )

    if not _is_managed_initial_analysis_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前初始语音分析工作流，已拒绝读取。",
        )
    try:
        return (
            asr_initial_analysis_detail_reader
            .read_initial_analysis_result(
                project_id,
                operation_id,
                file_backend=managed_artifact_files,
            )
        )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc


def get_development_document_understanding_result(
    project_id: str,
    operation_id: str,
) -> document_understanding_contracts.AsrDocumentUnderstandingResult | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if (
        not _is_document_understanding_operation(operation)
        or operation.status != "success"
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_DOCUMENT_UNDERSTANDING_UNAVAILABLE",
            "当前任务没有可读取的全文理解开发结果。",
        )
    if not _is_managed_document_understanding_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前全文理解工作流，已拒绝读取。",
        )
    try:
        with database.read_conn() as connection:
            return (
                asr_document_understanding_result_reader
                .read_result_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    file_backend=managed_artifact_files,
                )
            )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc


def get_development_visual_evidence_result(
    project_id: str,
    operation_id: str,
) -> visual_evidence.AsrVisualEvidenceResult | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if (
        not _is_visual_evidence_operation(operation)
        or operation.status != "success"
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_VISUAL_EVIDENCE_UNAVAILABLE",
            "当前任务没有可读取的画面取证开发结果。",
        )
    if not _is_managed_visual_evidence_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前画面取证工作流，已拒绝读取。",
        )
    try:
        with database.read_conn() as connection:
            authority = (
                asr_visual_evidence_result_reader
                .read_success_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    file_backend=managed_artifact_files,
                )
            )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    return authority.result if authority is not None else None


def get_formal_visual_evidence_frame(
    project_id: str,
    operation_id: str,
    frame_id: str,
) -> Path | None:
    """Resolve one isolated frame from a formal ASR or localization task."""

    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if operation.kind not in {"english_asr", "localization_draft"} or (
        _is_development_operation(operation)
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_UNAVAILABLE",
            "当前任务没有正式画面取证结果。",
        )
    if not re.fullmatch(r"frame_[0-9a-f]{12}", frame_id):
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_VISUAL_FRAME_NOT_FOUND",
            "画面取证截图不存在。",
        )
    try:
        frame_root = media_assets.visual_evidence_frame_dir(
            project_id,
            operation_id,
        ).resolve()
    except ValueError as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_INVALID",
            "画面取证任务标识无效。",
        ) from exc
    if operation.kind == "localization_draft":
        localization_root = (frame_root / "localization-v3").resolve()
        if not localization_root.is_dir():
            raise AppException(
                404,
                "VIDEO_LOCALIZATION_VISUAL_FRAME_NOT_FOUND",
                "本土化画面截图不存在。",
            )
        for frame_path in sorted(localization_root.glob("question_????-??.jpg")):
            resolved = frame_path.resolve()
            try:
                resolved.relative_to(localization_root)
            except ValueError:
                continue
            if not resolved.is_file():
                continue
            digest = media_assets.file_sha256(resolved)
            if f"frame_{digest[:12]}" == frame_id:
                return resolved
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_VISUAL_FRAME_NOT_FOUND",
            "本土化画面截图没有登记在当前任务中。",
        )
    manifest_specs = [
        (
            frame_root,
            "asr-visual-evidence-frame-manifest-v1",
            "upstream_operation_id",
            f"{operation_id}:understand_document",
        )
    ]
    saw_manifest = False
    for (
        manifest_root,
        schema_version,
        lineage_field,
        lineage_value,
    ) in manifest_specs:
        manifest_path = manifest_root / "manifest.json"
        if not manifest_path.is_file():
            continue
        saw_manifest = True
        try:
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_INVALID",
                "画面取证截图清单无法读取。",
            ) from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != schema_version
            or manifest.get(lineage_field) != lineage_value
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_INVALID",
                "画面取证截图清单与当前任务不匹配。",
            )
        frame = next(
            (
                item
                for item in manifest.get("frames", [])
                if isinstance(item, dict)
                and item.get("frame_id") == frame_id
            ),
            None,
        )
        if frame is None:
            continue
        resolved_root = manifest_root.resolve()
        frame_path = (
            resolved_root / str(frame.get("file_name") or "")
        ).resolve()
        try:
            frame_path.relative_to(resolved_root)
        except ValueError as exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_INVALID",
                "画面取证截图路径无效。",
            ) from exc
        if (
            not frame_path.is_file()
            or media_assets.file_sha256(frame_path)
            != str(frame.get("sha256") or "")
        ):
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_INVALID",
                "画面取证截图缺失或文件指纹不一致。",
            )
        return frame_path
    if not saw_manifest:
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_VISUAL_FRAME_NOT_FOUND",
            "画面取证截图清单不存在。",
        )
    raise AppException(
        404,
        "VIDEO_LOCALIZATION_VISUAL_FRAME_NOT_FOUND",
        "画面取证截图没有登记在当前任务清单中。",
    )


def _with_formal_visual_evidence_links(
    project_id: str,
    operation_id: str,
    step_result: dict,
) -> dict:
    """Expand internal frame references into one restricted public route."""

    result = dict(step_result)
    sections = []
    for raw_section in result.get("sections", []):
        if not isinstance(raw_section, dict):
            continue
        section = dict(raw_section)
        items = []
        for raw_item in section.get("items", []):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            frames = [
                value
                for value in item.pop("_frames", [])
                if isinstance(value, dict)
            ]
            item.pop("_frame_ids", None)
            frame_rate = item.pop("_frame_rate", 30.0)
            links = []
            for frame in frames:
                frame_id = str(frame.get("frame_id") or "")
                timestamp_ms = frame.get("timestamp_ms")
                if (
                    not re.fullmatch(r"frame_[0-9a-f]{12}", frame_id)
                    or not isinstance(timestamp_ms, int)
                ):
                    continue
                position = format_timeline_position(
                    timestamp_ms,
                    frame_rate=(
                        float(frame_rate)
                        if isinstance(frame_rate, (int, float))
                        and frame_rate > 0
                        else 30.0
                    ),
                )
                links.append(
                    {
                        "title": f"查看 {position} 截图",
                        "url": (
                            f"/api/projects/{project_id}/"
                            "video-localization/operations/"
                            f"{operation_id}/visual-evidence-frames/"
                            f"{frame_id}"
                        ),
                        "meta": position,
                        "text": (
                            f"第 {int(frame.get('round_index') or 1)}"
                            " 轮截图"
                        ),
                    }
                )
            item["links"] = links
            items.append(item)
        section["items"] = items
        sections.append(section)
    result["sections"] = sections
    return result


def _with_localization_visual_evidence_links(
    project_id: str,
    operation_id: str,
    step_result: dict,
) -> dict:
    """Project saved localization frames into restricted reader links."""

    result = dict(step_result)
    sections = [
        dict(section)
        for section in result.get("sections", [])
        if isinstance(section, dict)
    ]
    has_internal_refs = any(
        isinstance(item, dict) and bool(item.get("_frames"))
        for section in sections
        for item in section.get("items", [])
    )
    if has_internal_refs:
        result["sections"] = sections
        return _with_formal_visual_evidence_links(
            project_id,
            operation_id,
            result,
        )

    try:
        root = (
            media_assets.visual_evidence_frame_dir(
                project_id,
                operation_id,
            )
            / "localization-v3"
        ).resolve()
    except ValueError:
        return result
    if not root.is_dir():
        return result
    grouped_frames: dict[str, list[Path]] = {}
    for frame_path in sorted(root.glob("question_????-??.jpg")):
        resolved = frame_path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        match = re.fullmatch(
            r"(question_\d{4})-\d{2}\.jpg",
            resolved.name,
        )
        if match and resolved.is_file():
            grouped_frames.setdefault(match.group(1), []).append(resolved)
    if not grouped_frames:
        return result

    frame_groups = list(grouped_frames.values())
    next_group = 0
    hydrated_sections = []
    for section in sections:
        hydrated_items = []
        for raw_item in section.get("items", []):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            if item.get("links") or next_group >= len(frame_groups):
                hydrated_items.append(item)
                continue
            positions = re.findall(
                r"\d+(?:\.\d+)?\s*秒",
                str(item.get("meta") or ""),
            )
            frames = frame_groups[next_group]
            next_group += 1
            item["_frames"] = [
                {
                    "frame_id": (
                        "frame_"
                        + media_assets.file_sha256(frame_path)[:12]
                    ),
                    "timestamp_ms": (
                        round(
                            float(positions[index].replace("秒", "").strip())
                            * 1000
                        )
                        if index < len(positions)
                        else 0
                    ),
                    "round_index": 1,
                }
                for index, frame_path in enumerate(frames)
            ]
            item["_frame_rate"] = 30.0
            hydrated_items.append(item)
        hydrated_section = dict(section)
        hydrated_section["items"] = hydrated_items
        hydrated_sections.append(hydrated_section)
    result["sections"] = hydrated_sections
    return _with_formal_visual_evidence_links(
        project_id,
        operation_id,
        result,
    )


def _without_non_actionable_section_review_items(
    step_result: dict,
) -> dict:
    """Hide safely rejected model suggestions from reader-facing callouts."""

    result = dict(step_result)
    sections = []
    for raw_section in result.get("sections", []):
        if not isinstance(raw_section, dict):
            continue
        section = dict(raw_section)
        section["items"] = [
            dict(item)
            for item in section.get("items", [])
            if isinstance(item, dict)
            and section_review.warning_needs_reader_review(
                message=str(item.get("text") or ""),
            )
        ]
        if section["items"]:
            sections.append(section)
    result["sections"] = sections
    return result


def get_development_visual_evidence_frame(
    project_id: str,
    operation_id: str,
    frame_id: str,
) -> Path | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if not _is_managed_visual_evidence_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前画面取证工作流，已拒绝读取。",
        )
    if operation.status != "success":
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_VISUAL_EVIDENCE_UNAVAILABLE",
            "当前任务没有可读取的画面取证开发结果。",
        )
    try:
        with database.read_conn() as connection:
            authority = (
                asr_visual_evidence_result_reader
                .read_success_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    file_backend=managed_artifact_files,
                )
            )
            if authority is None:
                return None
            reference = next(
                (
                    item
                    for item in authority.frames
                    if item.frame_id == frame_id
                ),
                None,
            )
            if reference is None:
                return None
            verified = (
                video_localization_operation_artifact_store
                .read_artifact_from_connection(
                    connection,
                    reference.artifact_id,
                    file_backend=managed_artifact_files,
                )
            )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    except (
        video_localization_operation_artifact_store.ArtifactIdentityConflict,
        video_localization_operation_artifact_store.ArtifactIntegrityError,
        video_localization_operation_artifact_store.ArtifactPathError,
        video_localization_operation_artifact_store.ArtifactSchemaError,
    ) as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_VISUAL_FRAME_INVALID",
            "截图文件缺失或内容与当前任务快照不一致。",
        ) from exc
    artifact = verified.artifact
    if (
        artifact.project_id != project_id
        or artifact.operation_id != operation_id
        or artifact.artifact_kind != "visual-frame"
        or artifact.artifact_key != frame_id
        or artifact.content_fingerprint != reference.artifact_fingerprint
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_VISUAL_FRAME_INVALID",
            "截图文件与当前任务快照不一致。",
        )
    try:
        return managed_artifact_files.verified_file_path(
            project_id,
            artifact.storage_key,
            expected_size=artifact.size_bytes,
            expected_fingerprint=artifact.content_fingerprint,
        )
    except (
        managed_artifact_files.ManagedArtifactPathError,
        managed_artifact_files.ManagedArtifactIntegrityError,
    ) as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_VISUAL_FRAME_INVALID",
            "截图文件缺失或内容与当前任务快照不一致。",
        ) from exc


def get_development_research_evidence_result(
    project_id: str,
    operation_id: str,
) -> research_evidence.AsrResearchEvidenceResult | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if (
        not _is_research_evidence_operation(operation)
        or operation.status != "success"
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_RESEARCH_EVIDENCE_UNAVAILABLE",
            "当前任务没有可读取的资料查询开发结果。",
        )
    if not _is_managed_research_evidence_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前资料查询工作流，已拒绝读取。",
        )
    try:
        with database.read_conn() as connection:
            authority = (
                asr_research_evidence_result_reader
                .read_success_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    file_backend=managed_artifact_files,
                )
            )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    return authority.result if authority is not None else None


def get_development_entity_normalization_result(
    project_id: str,
    operation_id: str,
) -> entity_normalization.AsrEntityNormalizationResult | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if (
        not _is_entity_normalization_operation(operation)
        or operation.status != "success"
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_ENTITY_NORMALIZATION_UNAVAILABLE",
            "当前任务没有可读取的名称与术语统一开发结果。",
        )
    if not _is_managed_entity_normalization_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前名称统一工作流，已拒绝读取。",
        )
    try:
        with database.read_conn() as connection:
            authority = (
                asr_entity_normalization_result_reader
                .read_success_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    file_backend=managed_artifact_files,
                )
            )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    return authority.result if authority is not None else None


def get_development_section_review_result(
    project_id: str,
    operation_id: str,
) -> section_review.AsrSectionReviewResult | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if (
        not _is_section_review_operation(operation)
        or operation.status != "success"
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_SECTION_REVIEW_UNAVAILABLE",
            "当前任务没有可读取的分段复查开发结果。",
        )
    if not _is_managed_section_review_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前分段复查工作流，已拒绝读取。",
        )
    try:
        with database.read_conn() as connection:
            authority = (
                asr_section_review_result_reader
                .read_result_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    file_backend=managed_artifact_files,
                )
            )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    return authority.result if authority is not None else None


def get_development_review_decisions_result(
    project_id: str,
    operation_id: str,
) -> review_decisions.AsrReviewDecisionsResult | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if (
        not _is_review_decisions_operation(operation)
        or operation.status != "success"
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_REVIEW_DECISIONS_UNAVAILABLE",
            "当前任务没有可读取的修改汇总开发结果。",
        )
    if not _is_managed_review_decisions_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前复查结论工作流，已拒绝读取。",
        )
    try:
        with database.read_conn() as connection:
            authority = (
                asr_review_decisions_result_reader
                .read_result_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    file_backend=managed_artifact_files,
                )
            )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    return authority.result if authority is not None else None


def get_development_whole_recheck_result(
    project_id: str,
    operation_id: str,
) -> whole_recheck.AsrWholeRecheckResult | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if (
        not _is_whole_recheck_operation(operation)
        or operation.status != "success"
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_WHOLE_RECHECK_UNAVAILABLE",
            "当前任务没有可读取的全文复核开发结果。",
        )
    if not _is_managed_whole_recheck_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前全文复核工作流，已拒绝读取。",
        )
    try:
        with database.read_conn() as connection:
            authority = (
                asr_whole_recheck_result_reader
                .read_result_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    file_backend=managed_artifact_files,
                )
            )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    return authority.result if authority is not None else None


def get_development_transcript_quality_gate_result(
    project_id: str,
    operation_id: str,
) -> transcript_quality_gate.AsrTranscriptQualityGateResult | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if (
        not _is_transcript_quality_gate_operation(operation)
        or operation.status != "success"
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_TRANSCRIPT_QUALITY_GATE_UNAVAILABLE",
            "当前任务没有可读取的进入校时前检查结果。",
        )
    if not _is_managed_transcript_quality_gate_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前质量门工作流，已拒绝读取。",
        )
    try:
        with database.read_conn() as connection:
            authority = (
                asr_transcript_quality_gate_result_reader
                .read_result_from_connection(
                    connection,
                    project_id,
                    operation_id,
                    file_backend=managed_artifact_files,
                )
            )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    return authority.result if authority is not None else None


def get_development_diarization_result(
    project_id: str,
    operation_id: str,
) -> object | None:
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return None
    if operation.kind != "speaker_diarization" or operation.status != "success":
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_DIARIZATION_RESULT_UNAVAILABLE",
            "当前任务没有可读取的说话人区分开发结果。",
        )
    if not _is_managed_speaker_diarization_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_WORKFLOW_INVALID",
            "任务不是当前说话人区分工作流，已拒绝读取。",
        )
    try:
        result = (
            speaker_diarization_detail_reader
            .read_speaker_diarization_result(
                project_id,
                operation_id,
                file_backend=managed_artifact_files,
            )
        )
    except OperationDetailRepairRequired as exc:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_DETAIL_REPAIR_REQUIRED",
            str(exc),
            {"issue_codes": list(exc.issue_codes)},
        ) from exc
    if result is None:
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_DEVELOPMENT_DIARIZATION_RESULT_UNAVAILABLE",
            "当前任务缺少可读取的说话人区分开发结果。",
        )
    return result


def cancel(project_id: str, operation_id: str) -> VideoLocalizationOperation | None:
    expected_project_revision = (
        video_localization_operation_store.project_revision(project_id)
    )
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    operation = get_operation(project_id, operation_id)
    if not operation:
        raise AppException(404, "VIDEO_LOCALIZATION_OPERATION_NOT_FOUND", "Operation not found")
    if operation.status not in _ACTIVE_STATUSES:
        return operation

    runtime_key = (project_id, operation_id)
    gate = _operation_commit_gate(project_id, operation_id)
    completed_at = now_iso() if operation.status == "queued" else None
    cancellation_summary = _cancelled_task_summary(
        operation.result_summary,
        stage=(
            "已取消"
            if operation.status == "queued"
            else "正在取消"
        ),
    )
    updates = {
        "status": "cancelled",
        "cancel_requested": True,
        "completed_at": completed_at,
        "error_message": "已取消" if operation.status == "queued" else "已请求取消；当前处理结束后会丢弃任务结果。",
        "result_summary": cancellation_summary,
    }
    command_operation = operation.model_copy(update=updates)
    command = (
        video_localization_operation_ledger_store
        .command_from_operation(
            "cancel",
            command_operation.model_dump(mode="json"),
            expected_project_revision=expected_project_revision,
        )
    )
    try:
        committed, _result = gate.commit_cancel(
            lambda: _mark_operation(
                project_id,
                operation_id,
                kind=_status_kind(operation),
                write_intent="content",
                operation_command=command,
                **updates,
            )
        )
    except (
        video_localization_operation_ledger_store
        .OperationCommandConflict,
        video_localization_operation_store
        .OperationProjectRevisionConflict,
    ) as exc:
        latest = get_operation(project_id, operation_id)
        if latest is not None and latest.status in _TERMINAL_STATUSES:
            return latest
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_COMMAND_CONFLICT",
            "任务状态刚刚发生变化，请刷新后重试。",
        ) from exc
    if not committed:
        return get_operation(project_id, operation_id) or operation

    _runtime.mark_cancelled(runtime_key)
    updated = get_operation(project_id, operation_id)
    return updated or operation.model_copy(update=updates)


def _cancel_requested(project_id: str, operation_id: str) -> bool:
    runtime_key = (project_id, operation_id)
    gate = _operation_commit_gate(project_id, operation_id)
    if gate.is_cancel_requested():
        return True
    if _runtime.cancellation_requested(runtime_key):
        return True
    return operation_state.operation_was_cancelled(get_operation(project_id, operation_id))


def retry(project_id: str, operation_id: str) -> VideoLocalizationOperation | None:
    draft = service.get_video_localization(project_id)
    if draft is None:
        return None
    operation = get_operation(project_id, operation_id)
    if not operation:
        raise AppException(404, "VIDEO_LOCALIZATION_OPERATION_NOT_FOUND", "Operation not found")
    if operation.status in _ACTIVE_STATUSES:
        raise AppException(409, "VIDEO_LOCALIZATION_OPERATION_ACTIVE", "Operation is still active")
    if _is_partial_asr_operation(operation):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ASR_STOP_AFTER_RETIRED",
            "旧 ASR 开发断点只保留历史查看；请用 development_target 创建新的定点任务。",
        )
    unknown_provider_result = any(
        step.cost_class == "external_paid"
        and step.status in {"submitted", "result_unknown"}
        for step in (
            video_localization_operation_step_store
            .list_step_attempts(
                project_id,
                operation_id,
            )
        )
    )
    if (
        _is_managed_document_understanding_operation(operation)
        and unknown_provider_result
    ):
        raise AppException(
            409,
            (
                "VIDEO_LOCALIZATION_DOCUMENT_UNDERSTANDING_"
                "RESULT_UNKNOWN"
            ),
            (
                "上一次付费模型调用的结果仍然未知；"
                "为避免重复计费，暂时不能创建重试任务。"
            ),
        )
    if (
        _is_managed_visual_evidence_operation(operation)
        and unknown_provider_result
    ):
        raise AppException(
            409,
            (
                "VIDEO_LOCALIZATION_VISUAL_EVIDENCE_"
                "RESULT_UNKNOWN"
            ),
            (
                "上一次付费画面识别调用的结果仍然未知；"
                "为避免重复计费，暂时不能创建重试任务。"
            ),
        )
    if (
        _is_managed_research_evidence_operation(operation)
        and unknown_provider_result
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_RESEARCH_RESULT_UNKNOWN",
            (
                "上一次付费资料查询调用的结果仍然未知；"
                "为避免重复计费，暂时不能创建重试任务。"
            ),
        )
    if (
        _is_managed_entity_normalization_operation(operation)
        and unknown_provider_result
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_ENTITY_NORMALIZATION_RESULT_UNKNOWN",
            (
                "上一次付费名称统一调用的结果仍然未知；"
                "为避免重复计费，暂时不能创建重试任务。"
            ),
        )
    if (
        _is_managed_section_review_operation(operation)
        and unknown_provider_result
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_SECTION_REVIEW_RESULT_UNKNOWN",
            (
                "上一次付费分段复查调用的结果仍然未知；"
                "为避免重复计费，暂时不能创建重试任务。"
            ),
        )
    if (
        _is_managed_review_decisions_operation(operation)
        and unknown_provider_result
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_REVIEW_DECISIONS_RESULT_UNKNOWN",
            (
                "上一次付费复查结论汇总调用的结果仍然未知；"
                "为避免重复计费，暂时不能创建重试任务。"
            ),
        )
    if (
        _is_managed_whole_recheck_operation(operation)
        and unknown_provider_result
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_WHOLE_RECHECK_RESULT_UNKNOWN",
            (
                "上一次付费全文复核调用的结果仍然未知；"
                "为避免重复计费，暂时不能创建重试任务。"
            ),
        )
    parameters = dict(operation.parameters)
    if operation.kind in {
        "source_audio",
        "stems",
        "reference_clips",
    }:
        parameters = {}
    elif operation.kind == "dub_subtitle_generation":
        parameters = {
            key: value
            for key, value in parameters.items()
            if key
            in {
                "engine_id",
                "regeneration_mode",
                "execution_mode",
                "development_target_step_id",
                "development_session_id",
            }
        }
        if parameters.get("execution_mode") == "full":
            parameters = {
                "engine_id": parameters.get("engine_id"),
                "regeneration_mode": parameters.get("regeneration_mode"),
                "execution_mode": "full",
            }
    elif operation.kind == "localization_draft":
        parameters = {
            key: value
            for key, value in parameters.items()
            if key
            in {
                "source_language",
                "target_language",
                "profile_id",
                "localization_requirements_id",
                "execution_mode",
                "development_target_step_id",
                "development_session_id",
                "force_development_target",
                "recover_verified_candidates",
            }
        }
        if parameters.get("execution_mode") == "full":
            parameters.pop("execution_mode", None)
    elif operation.kind == "semantic_tts_grouping":
        parameters = {
            key: value
            for key, value in parameters.items()
            if key
            in {
                "profile_id",
                "target_chars",
                "max_chars",
            }
        }
    elif operation.kind == "media_export":
        parameters = {
            key: value
            for key, value in parameters.items()
            if key in {
                "destination_id",
                "render",
                "output_filename",
            }
        }
    elif operation.kind == "speaker_diarization":
        parameters = {
            key: value
            for key, value in parameters.items()
            if key
            in {
                "engine_id",
                "source_track_id",
                "min_speakers",
                "max_speakers",
            }
        }
    return submit(
        project_id,
        operation.kind,
        parameters,
        command_type="retry",
        source_operation_id=operation_id,
    )


def _normalized_operation_parameters(
    kind: OperationKind,
    parameters: dict | None,
    draft: VideoLocalizationDraft,
) -> dict:
    normalized = {key: value for key, value in (parameters or {}).items() if key != "scope"}
    if kind in {"localization_draft", "english_asr"}:
        # This identity is server-owned, not an editable request parameter.
        normalized.pop("submission_binding", None)
    if kind == "source_audio":
        if normalized:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_SOURCE_AUDIO_PARAMETERS_INVALID",
                "提取原始音轨任务不接受额外参数。",
            )
        normalized = {}
    elif kind == "stems":
        if normalized:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_STEM_SEPARATION_PARAMETERS_INVALID",
                "分离人声与背景声任务不接受额外参数。",
            )
        normalized = {}
    elif kind == "dub_subtitle_generation":
        unknown = set(normalized) - {
            "engine_id",
            "regeneration_mode",
            "execution_mode",
            "development_target_step_id",
            "development_session_id",
        }
        if unknown:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_PARAMETERS_INVALID",
                "合成配音字幕任务包含不支持的参数。",
                {"unsupported_parameters": sorted(unknown)},
            )
        engine_id = str(
            normalized.get("engine_id")
            or source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID
        ).strip()
        if not engine_id:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_ENGINE_REQUIRED",
                "合成配音字幕任务缺少识别引擎。",
            )
        execution_mode = str(
            normalized.get("execution_mode") or "full"
        ).strip()
        regeneration_mode = str(
            normalized.get("regeneration_mode") or "auto"
        ).strip()
        if regeneration_mode not in {"auto", "full"}:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_PARAMETERS_INVALID",
                "字幕重建范围只支持 auto 或 full。",
            )
        if execution_mode not in {"full", "development_target"}:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_PARAMETERS_INVALID",
                "合成配音字幕执行模式只支持 full 或 development_target。",
            )
        development_target_step_id = str(
            normalized.get("development_target_step_id") or ""
        ).strip()
        development_session_id = str(
            normalized.get("development_session_id") or ""
        ).strip()
        if execution_mode == "development_target":
            if not (
                settings_store.get()
                .video_localization_development_step_control_enabled
            ):
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DEVELOPMENT_STEP_CONTROL_DISABLED",
                    "开发子流程控制当前已关闭，请先在后台设置中显式开启。",
                )
            available_steps = {
                task.id
                for stage in (
                    workflow_contracts
                    .DUB_SUBTITLE_WORKFLOW_DEFINITION.stages
                )
                for task in stage.atomic_tasks
            }
            if development_target_step_id not in available_steps:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DEVELOPMENT_TARGET_INVALID",
                    "开发目标节点不存在。",
                    {
                        "available_target_step_ids": sorted(
                            available_steps
                        )
                    },
                )
            if not development_session_id:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DEVELOPMENT_SESSION_REQUIRED",
                    "开发目标模式必须指定稳定的开发会话 ID。",
                )
        elif development_target_step_id or development_session_id:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DUB_SUBTITLE_PARAMETERS_INVALID",
                "正式全流程不能指定开发目标节点或开发会话。",
            )
        normalized = {
            "engine_id": engine_id,
            "regeneration_mode": regeneration_mode,
            "execution_mode": execution_mode,
        }
        if execution_mode == "development_target":
            normalized.update(
                {
                    "development_target_step_id": (
                        development_target_step_id
                    ),
                    "development_session_id": development_session_id,
                }
            )
    elif kind == "reference_clips":
        if normalized:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_REFERENCE_CANDIDATES_PARAMETERS_INVALID",
                "自动参考音候选任务不接受额外参数。",
            )
        normalized = {}
    elif kind == "english_asr":
        execution_mode = str(normalized.get("execution_mode") or "full").strip()
        if execution_mode == "development_target":
            allowed = {
                "execution_mode",
                "development_source_operation_id",
                "development_predecessor_operation_id",
                "development_target_step_id",
            }
            unsupported = sorted(set(normalized) - allowed)
            if unsupported:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_ASR_DEVELOPMENT_PARAMETERS_INVALID",
                    "ASR 开发定点重跑包含不支持的参数。",
                    {"unsupported_parameters": unsupported},
                )
            if not (
                settings_store.get()
                .video_localization_development_step_control_enabled
            ):
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DEVELOPMENT_STEP_CONTROL_DISABLED",
                    "开发子流程控制当前已关闭，请先在后台设置中显式开启。",
                )
            source_operation_id = str(
                normalized.get(
                    "development_source_operation_id"
                )
                or ""
            ).strip()
            target_step_id = str(
                normalized.get("development_target_step_id") or ""
            ).strip()
            predecessor_operation_id = str(
                normalized.get("development_predecessor_operation_id") or ""
            ).strip()
            if not source_operation_id:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_ASR_DEVELOPMENT_SOURCE_REQUIRED",
                    "ASR 开发定点重跑必须指定原流程任务 ID。",
                )
            if (
                target_step_id
                not in (
                    asr_development_replay
                    .ASR_DEVELOPMENT_TARGET_STEP_IDS
                )
            ):
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DEVELOPMENT_TARGET_INVALID",
                    "ASR 开发目标节点不存在。",
                    {
                        "available_target_step_ids": list(
                            asr_development_replay
                            .ASR_DEVELOPMENT_TARGET_STEP_IDS
                        )
                    },
                )
            if predecessor_operation_id:
                predecessor = _operation_from_draft(
                    draft, predecessor_operation_id
                )
                if predecessor is None or predecessor.status != "success":
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_ASR_CONTINUATION_PREDECESSOR_INVALID",
                        "ASR 开发续接只能使用同项目已成功的直接前驱任务。",
                    )
                predecessor_step_id = str(
                    predecessor.parameters.get("development_target_step_id")
                    or ""
                )
                predecessor_lineage_id = str(
                    predecessor.parameters.get("development_source_operation_id")
                    or ""
                )
                if (
                    predecessor_lineage_id != source_operation_id
                    or asr_development_continuation.expected_predecessor(
                        target_step_id
                    )
                    != predecessor_step_id
                ):
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_ASR_CONTINUATION_LINEAGE_INVALID",
                        "ASR 开发续接必须沿同一原流程按节点顺序使用直接前驱。",
                    )
            return {
                "execution_mode": "development_target",
                "development_source_operation_id": (
                    source_operation_id
                ),
                **(
                    {
                        "development_predecessor_operation_id": (
                            predecessor_operation_id
                        )
                    }
                    if predecessor_operation_id
                    else {}
                ),
                "development_target_step_id": target_step_id,
            }
        if execution_mode != "full":
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_ASR_EXECUTION_MODE_INVALID",
                "execution_mode 仅支持 full 或 development_target。",
            )
        retired_parameters = sorted(
            set(normalized)
            & {
                "stop_after_step",
                "input_initial_analysis_operation_id",
                "input_document_understanding_operation_id",
                "input_visual_evidence_operation_id",
                "input_research_evidence_operation_id",
                "input_entity_normalization_operation_id",
                "input_section_review_operation_id",
                "input_review_decisions_operation_id",
                "input_whole_recheck_operation_id",
            }
        )
        if retired_parameters:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_ASR_PARAMETERS_INVALID",
                "完整 ASR 不接受旧开发断点参数；请使用 development_target。",
                {"unsupported_parameters": retired_parameters},
            )
        selected_profile_id = _selected_llm_profile_id(normalized)
        if selected_profile_id:
            normalized["profile_id"] = selected_profile_id
        else:
            normalized.pop("profile_id", None)
        requested_asr_engine_id = str(
            normalized.get("engine_id") or ""
        ).strip()
        automatic_asr_requested = (
            not requested_asr_engine_id
            or requested_asr_engine_id.lower() == "auto"
        )
        asr_selection = asr_selection_policy.select(
            requested_engine_id=(
                "auto" if automatic_asr_requested else requested_asr_engine_id
            ),
            needs_speaker_diarization=_asr_summary_includes_diarization(normalized),
        )
        if automatic_asr_requested:
            if asr_selection.diarization_engine_id is None:
                normalized.pop("diarization_engine_id", None)
            else:
                normalized["diarization_engine_id"] = asr_selection.diarization_engine_id
        normalized.update(
            {
                "engine_id": asr_selection.engine_id,
                "source_track_id": str(normalized.get("source_track_id") or "auto"),
                "source_language": source_pipeline.normalize_source_language(
                    str(normalized.get("source_language") or draft.language_config.source_language)
                ),
                "execution_mode": "full",
                "segmentation_profile_id": str(
                    normalized.get("segmentation_profile_id") or "generic_zh"
                ),
            }
        )
        for key in ("min_speakers", "max_speakers"):
            value = _optional_speaker_count(normalized.get(key), key=key)
            if value is None:
                normalized.pop(key, None)
            else:
                normalized[key] = value
        _validate_speaker_count_range(normalized)
        if (
            normalized.get("min_speakers") is not None
            or normalized.get("max_speakers") is not None
        ) and not normalized.get("diarization_engine_id"):
            normalized["diarization_engine_id"] = "auto"
        elif "diarization_engine_id" not in normalized:
            normalized["diarization_engine_id"] = "auto"
        elif not normalized.get("diarization_engine_id"):
            normalized.pop("diarization_engine_id", None)
    elif kind == "speaker_diarization":
        if not settings_store.get().video_localization_development_step_control_enabled:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_DEVELOPMENT_STEP_CONTROL_DISABLED",
                "开发子流程控制当前已关闭，请先在后台设置中显式开启。",
            )
        unsupported = sorted(
            set(normalized)
            - {
                "engine_id",
                "source_track_id",
                "min_speakers",
                "max_speakers",
            }
        )
        if unsupported:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_SPEAKER_DIARIZATION_PARAMETERS_INVALID",
                "说话人区分任务包含不支持的参数："
                + "、".join(unsupported),
            )
        source_track_id = str(
            normalized.get("source_track_id") or "auto"
        ).strip()
        if source_track_id not in {
            "auto",
            "original",
            "vocals",
        }:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_SPEAKER_DIARIZATION_PARAMETERS_INVALID",
                "说话人区分 source_track_id 仅支持 auto、original 或 vocals。",
            )
        normalized.update(
            {
                "engine_id": (
                    str(
                        normalized.get("engine_id")
                        or "auto"
                    ).strip()
                    or "auto"
                ),
                "source_track_id": source_track_id,
            }
        )
        for key in ("min_speakers", "max_speakers"):
            value = _optional_speaker_count(normalized.get(key), key=key)
            if value is None:
                normalized.pop(key, None)
            else:
                normalized[key] = value
        _validate_speaker_count_range(normalized)
    elif kind == "localization_draft":
        unsupported = sorted(
            set(normalized)
            - {
                "source_language",
                "target_language",
                "profile_id",
                "localization_requirements_id",
                "workflow_id",
                "execution_mode",
                "development_target_step_id",
                "development_session_id",
                "force_development_target",
                "recover_verified_candidates",
                "retry_unknown_batch_step_id",
            }
        )
        if unsupported:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_LOCALIZATION_PARAMETERS_INVALID",
                "本土化任务包含不支持的参数。",
                {"unsupported_parameters": unsupported},
            )
        execution_mode = str(
            normalized.get("execution_mode") or "full"
        ).strip()
        if execution_mode not in {"full", "development_target"}:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_LOCALIZATION_PARAMETERS_INVALID",
                "本土化执行模式只支持 full 或 development_target。",
            )
        development_target_step_id = str(
            normalized.get("development_target_step_id") or ""
        ).strip()
        development_session_id = str(
            normalized.get("development_session_id") or ""
        ).strip()
        force_development_target = bool(
            normalized.get("force_development_target", True)
        )
        recover_verified_candidates = normalized.get("recover_verified_candidates", False)
        retry_unknown_batch_step_id = normalized.get("retry_unknown_batch_step_id")
        if retry_unknown_batch_step_id is not None and (
            not isinstance(retry_unknown_batch_step_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", retry_unknown_batch_step_id)
            or execution_mode != "development_target"
            or not force_development_target
            or not retry_unknown_batch_step_id.startswith(development_target_step_id + ".llm_batch.")
        ):
            raise AppException(
                400, "VIDEO_LOCALIZATION_LOCALIZATION_PARAMETERS_INVALID",
                "未知结果重试必须明确指定当前开发目标的一条失败批次，并强制执行该目标。",
            )
        if not isinstance(recover_verified_candidates, bool):
            raise AppException(
                400, "VIDEO_LOCALIZATION_LOCALIZATION_PARAMETERS_INVALID",
                "候选恢复开关必须为布尔值。",
            )
        if recover_verified_candidates and execution_mode != "development_target":
            raise AppException(
                400, "VIDEO_LOCALIZATION_LOCALIZATION_PARAMETERS_INVALID",
                "正式全流程不能导入恢复候选。",
            )
        if execution_mode == "development_target":
            if not (
                settings_store.get()
                .video_localization_development_step_control_enabled
            ):
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DEVELOPMENT_STEP_CONTROL_DISABLED",
                    "开发子流程控制当前已关闭，请先在后台设置中显式开启。",
                )
            available_steps = {
                task.id
                for stage in (
                    workflow_contracts
                    .LOCALIZATION_WORKFLOW_V3_DEFINITION.stages
                )
                for task in stage.atomic_tasks
            }
            if development_target_step_id not in available_steps:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DEVELOPMENT_TARGET_INVALID",
                    "开发目标节点不存在。",
                    {
                        "available_target_step_ids": sorted(
                            available_steps
                        )
                    },
                )
            if not development_session_id:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_DEVELOPMENT_SESSION_REQUIRED",
                    "开发增量模式必须指定稳定的开发会话 ID。",
                )
        elif development_target_step_id or development_session_id:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_LOCALIZATION_PARAMETERS_INVALID",
                "正式全流程不能指定开发目标节点或开发会话。",
            )
        selected_profile_id = _selected_llm_profile_id(normalized)
        localization_requirements_id = (
            str(
                normalized.get("localization_requirements_id") or ""
            ).strip()
            or None
        )
        try:
            localization_requirements.resolve_localization_requirements(
                localization_requirements_id
            )
        except ValueError as exc:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_REQUIREMENTS_NOT_FOUND",
                str(exc),
            ) from exc
        normalized = {
            "source_language": source_pipeline.normalize_source_language(
                str(
                    normalized.get("source_language")
                    or draft.language_config.source_language
                )
            ),
            "target_language": str(
                normalized.get("target_language")
                or draft.language_config.target_language
            ),
            "profile_id": selected_profile_id,
            "workflow_id": "localization-v3",
            "execution_mode": execution_mode,
        }
        if localization_requirements_id:
            normalized["localization_requirements_id"] = (
                localization_requirements_id
            )
        if execution_mode == "development_target":
            normalized.update(
                {
                    "development_target_step_id": (
                        development_target_step_id
                    ),
                    "development_session_id": development_session_id,
                    "force_development_target": force_development_target,
                    "recover_verified_candidates": recover_verified_candidates,
                }
            )
            if retry_unknown_batch_step_id is not None:
                normalized["retry_unknown_batch_step_id"] = retry_unknown_batch_step_id
    elif kind == "semantic_tts_grouping":
        selected_profile_id = _selected_llm_profile_id(normalized)
        target_chars = max(20, min(int(normalized.get("target_chars") or 120), 1000))
        profile_configuration_fingerprint = str(
            normalized.get(
                "profile_configuration_fingerprint"
            )
            or ""
        ).strip()
        if profile_configuration_fingerprint:
            if not re.fullmatch(
                r"[0-9a-f]{64}",
                profile_configuration_fingerprint,
            ):
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROFILE_FINGERPRINT_INVALID",
                    "语义分组任务中的模型配置指纹无效。",
                )
            if not selected_profile_id:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_SEMANTIC_GROUPING_PROFILE_REQUIRED",
                    "语义分组任务缺少已锁定的模型配置。",
                )
        else:
            try:
                resolved_profile = llm_runtime.resolve_profile(
                    selected_profile_id or None
                )
            except llm_runtime.LlmRuntimeError as exc:
                raise AppException(
                    exc.status_code,
                    exc.code,
                    str(exc),
                ) from exc
            selected_profile_id = resolved_profile.profile_id
            profile_configuration_fingerprint = (
                semantic_tts_grouping_execution
                .provider_configuration_fingerprint(
                    resolved_profile
                )
            )
        normalized = {
            "profile_id": selected_profile_id,
            "profile_configuration_fingerprint": (
                profile_configuration_fingerprint
            ),
            "workflow_id": "semantic-tts-grouping",
            "target_chars": target_chars,
            "max_chars": max(
                target_chars,
                min(
                    int(normalized.get("max_chars") or 180),
                    2000,
                ),
            ),
        }
    elif kind == "media_export":
        unknown = set(normalized) - {
            "destination_id",
            "render",
            "output_filename",
        }
        if unknown:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_MEDIA_EXPORT_PARAMETERS_INVALID",
                "导出任务包含不支持的参数。",
                {"unsupported_parameters": sorted(unknown)},
            )
        destination_id = str(
            normalized.get("destination_id") or ""
        ).strip()
        try:
            video_localization_export_destinations.resolve_destination(
                destination_id
            )
        except (
            video_localization_export_destinations
            .ExportDestinationError
        ) as exc:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_EXPORT_DESTINATION_UNAVAILABLE",
                str(exc),
            ) from exc
        try:
            render_request = (
                VideoLocalizationMediaExportRequest.model_validate(
                    normalized.get("render")
                )
            )
        except Exception as exc:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_MEDIA_EXPORT_REQUEST_INVALID",
                "导出设置无效，请重新检查所选轨道和格式。",
            ) from exc
        try:
            output_filename = validate_media_export_output_filename(
                str(normalized.get("output_filename") or ""),
                render_request,
            )
        except ValueError as exc:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_MEDIA_EXPORT_FILENAME_INVALID",
                str(exc),
            ) from exc
        normalized = {
            "destination_id": destination_id,
            "render": render_request.model_dump(mode="json"),
            "output_filename": output_filename,
        }
    normalized["scope"] = operation_state.operation_scope(kind, normalized)
    return normalized


def _selected_llm_profile_id(parameters: dict) -> str:
    """Resolve one new task against the current settings-page default.

    A caller may explicitly pin a profile for reproducible API/Agent use.
    Otherwise every newly submitted development or formal task reads the
    currently selected default instead of inheriting a stale upstream task.
    """

    requested = str(parameters.get("profile_id") or "").strip()
    if requested:
        return requested
    return str(
        settings_store.llm_profiles().default_profile_id or ""
    ).strip()


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    field_name: str,
) -> int:
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_RESEARCH_POLICY_INVALID",
            f"{field_name} 必须是整数。",
        ) from exc
    if parsed < minimum or parsed > maximum:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_RESEARCH_POLICY_INVALID",
            f"{field_name} 必须在 {minimum} 到 {maximum} 之间。",
        )
    return parsed


def _optional_speaker_count(value: object, *, key: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        parsed = -1
    else:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = -1
    if parsed < 1 or parsed > speaker_diarization.MAX_SPEAKER_COUNT_GUIDANCE:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_SPEAKER_COUNT_INVALID",
            f"{key} 必须是 1 到 {speaker_diarization.MAX_SPEAKER_COUNT_GUIDANCE} 之间的整数。",
        )
    return parsed


def _validate_speaker_count_range(parameters: dict) -> None:
    minimum = parameters.get("min_speakers")
    maximum = parameters.get("max_speakers")
    if minimum is not None and maximum is not None and int(minimum) > int(maximum):
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_SPEAKER_COUNT_RANGE_INVALID",
            "min_speakers 不能大于 max_speakers。",
        )


def submit(
    project_id: str,
    kind: OperationKind,
    parameters: dict | None = None,
    *,
    command_type: (
        video_localization_operation_ledger_store.CommandType
    ) = "submit",
    source_operation_id: str | None = None,
) -> VideoLocalizationOperation | None:
    expected_project_revision = (
        video_localization_operation_store.project_revision(project_id)
    )
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = service.get_video_localization(project_id) or VideoLocalizationDraft()
    operation_parameters = _normalized_operation_parameters(kind, parameters, draft)

    try:
        operation_state.validate_prerequisites(kind, draft, operation_parameters)
    except AppException as exc:
        failure_summary = {
            "stage": "提交任务",
            "error_detail": exc.detail_dict or _fallback_error_detail(exc.code, exc.message, "提交任务"),
        }
        operation = VideoLocalizationOperation(
            project_id=project_id,
            kind=kind,
            label=(
                "本土化流程开发"
                if kind == "localization_draft"
                and operation_parameters.get("execution_mode")
                == "development_target"
                else "ASR 子流程开发"
                if kind == "english_asr"
                and operation_parameters.get("execution_mode")
                == "development_target"
                else _KIND_LABELS[kind]
            ),
            parameters=operation_parameters,
            status="failed",
            progress=1.0,
            completed_at=now_iso(),
            error_code=exc.code,
            error_message=exc.message,
            result_summary=failure_summary,
        )
        failed_draft = operation_state.with_operation(draft, operation)
        if not _is_development_operation(operation):
            failed_draft = operation_state.with_kind_status(
                failed_draft,
                kind,
                "failed",
                error_code=exc.code,
                error_message=exc.message,
            )
        command = (
            video_localization_operation_ledger_store
            .command_from_operation(
                command_type,
                operation.model_dump(mode="json"),
                expected_project_revision=expected_project_revision,
                source_operation_id=source_operation_id,
            )
        )
        try:
            service.save_video_localization(
                project_id,
                failed_draft,
                operation_command=command,
            )
        except (
            video_localization_operation_ledger_store
            .OperationCommandConflict,
            video_localization_operation_store
            .OperationProjectRevisionConflict,
        ) as command_exc:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_OPERATION_COMMAND_CONFLICT",
                "项目任务刚刚发生变化，请刷新后重试。",
            ) from command_exc
        raise

    active = _active_operation_for_kind(
        project_id,
        kind,
        fallback_draft=draft,
    )
    if active:
        active_parameters = _normalized_operation_parameters(kind, active.parameters, draft)
        if active_parameters != operation_parameters:
            raise AppException(
                409,
                "VIDEO_LOCALIZATION_OPERATION_PARAMETERS_CONFLICT",
                "同类任务正在使用另一组参数处理，请等待当前任务结束后再试。",
            )
        _enqueue((project_id, active.operation_id))
        return active

    if kind == "localization_draft" and operation_parameters.get("execution_mode") == "full":
        operation_parameters["submission_binding"] = workflow_submission.capture_submission_binding(
            draft,
            profile_id=operation_parameters.get("profile_id"),
            requirements_id=operation_parameters.get("localization_requirements_id"),
        ).model_dump(mode="json")

    if kind == "english_asr" and operation_parameters.get("execution_mode", "full") == "full":
        operation_parameters["submission_binding"] = workflow_submission.capture_asr_submission_binding(
            draft, profile_id=operation_parameters.get("profile_id"),
        ).model_dump(mode="json")

    operation = VideoLocalizationOperation(
        project_id=project_id,
        kind=kind,
        label=(
            "本土化流程开发"
            if kind == "localization_draft"
            and operation_parameters.get("execution_mode")
            == "development_target"
            else "ASR 子流程开发"
            if kind == "english_asr"
            and operation_parameters.get("execution_mode")
            == "development_target"
            else _KIND_LABELS[kind]
        ),
        parameters=operation_parameters,
        result_summary=_initial_operation_summary(
            kind,
            operation_parameters,
        ),
    )
    draft = operation_state.with_operation(draft, operation)
    if not _is_development_operation(operation):
        draft = operation_state.with_kind_status(draft, kind, "queued")
    command = (
        video_localization_operation_ledger_store
        .command_from_operation(
            command_type,
            operation.model_dump(mode="json"),
            expected_project_revision=expected_project_revision,
            source_operation_id=source_operation_id,
        )
    )
    try:
        service.save_video_localization(
            project_id,
            draft,
            operation_command=command,
        )
    except (
        video_localization_operation_ledger_store
        .OperationCommandConflict,
        video_localization_operation_store
        .OperationProjectRevisionConflict,
    ) as exc:
        latest_draft = service.get_video_localization(project_id)
        latest_active = (
            _active_operation_for_kind(
                project_id,
                kind,
                fallback_draft=latest_draft,
            )
            if latest_draft is not None
            else None
        )
        if latest_active is not None:
            latest_parameters = _normalized_operation_parameters(
                kind,
                latest_active.parameters,
                latest_draft,
            )
            if latest_parameters == {key: value for key, value in operation_parameters.items() if key != "submission_binding"}:
                _enqueue((project_id, latest_active.operation_id))
                return latest_active
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_OPERATION_COMMAND_CONFLICT",
            "项目任务刚刚发生变化，请刷新后重试。",
        ) from exc
    _enqueue((project_id, operation.operation_id))
    return operation


def _worker(
    task_queue: operation_scheduler.ProjectFairOperationScheduler,
) -> None:
    while True:
        operation_key = task_queue.get()
        if operation_key is None:
            return
        project_id, operation_id = operation_key
        _runtime.mark_dequeued(operation_key)
        try:
            _process(project_id, operation_id)
        finally:
            _runtime.complete(operation_key)
            task_queue.complete(operation_key)




















































































def _process(project_id: str, operation_id: str) -> None:
    runtime_key = (project_id, operation_id)
    operation = get_operation(project_id, operation_id)
    if operation is None:
        return
    if operation.status in _TERMINAL_STATUSES:
        _runtime.cancel_recovery(runtime_key)
        return
    try:
        decision = (
            video_localization_operation_execution
            .acquire_execution_claim(
                project_id,
                operation_id,
                runner_id=_runner_id,
            )
        )
    except Exception as exc:
        _logger.warning(
            "operation execution claim failed (%s)",
            type(exc).__name__,
        )
        _schedule_operation_recovery(
            project_id,
            operation_id,
            deadline_ms=(
                time.time_ns() // 1_000_000
                + max(
                    1_000,
                    database.SQLITE_BUSY_TIMEOUT_MS,
                )
            ),
        )
        return
    if not decision.acquired:
        if decision.retry_at_ms is not None:
            _schedule_operation_recovery(
                project_id,
                operation_id,
                deadline_ms=decision.retry_at_ms,
            )
        return
    claim = decision.claim
    assert claim is not None
    claim.start_heartbeat()
    try:
        with execution_fence_scope(claim.execution_fence):
            _process_operation(
                project_id,
                operation,
                execution_claim=claim,
            )
    except ExecutionFenceLost:
        claim.mark_lost()
        _logger.info(
            "operation execution fence lost before commit"
        )
    except ExecutionOperationCancelled:
        _logger.info(
            "operation execution commit discarded after cancellation"
        )
    finally:
        operation = get_operation(project_id, operation_id)
        status, error_code = _attempt_terminal_result(operation)
        try:
            claim.finish(status=status, error_code=error_code)
        except Exception as exc:
            _logger.warning(
                "operation execution claim finish failed (%s)",
                type(exc).__name__,
            )
        if operation is not None and operation.status in _TERMINAL_STATUSES:
            _runtime.cancel_recovery(runtime_key)


def _attempt_terminal_result(
    operation: VideoLocalizationOperation | None,
) -> tuple[
    video_localization_operation_attempt_store.TerminalAttemptStatus,
    str | None,
]:
    if operation is None:
        return (
            "incomplete",
            "VIDEO_LOCALIZATION_OPERATION_MISSING_AFTER_ATTEMPT",
        )
    if operation.status in _TERMINAL_STATUSES:
        return operation.status, operation.error_code
    return (
        "incomplete",
        operation.error_code
        or "VIDEO_LOCALIZATION_OPERATION_NOT_TERMINAL_AFTER_ATTEMPT",
    )


def _schedule_operation_recovery(
    project_id: str,
    operation_id: str,
    *,
    deadline_ms: int,
) -> None:
    _runtime.schedule_recovery(
        (project_id, operation_id),
        deadline_ms=deadline_ms,
        callback=_recover_operation_after_lease,
    )


def _recover_operation_after_lease(
    operation_key: OperationRuntimeKey,
) -> None:
    project_id, operation_id = operation_key
    try:
        _recover_project_operations(project_id)
    except Exception as exc:
        _logger.warning(
            "operation delayed recovery failed (%s)",
            type(exc).__name__,
        )
        _schedule_operation_recovery(
            project_id,
            operation_id,
            deadline_ms=(
                time.time_ns() // 1_000_000
                + max(1_000, database.SQLITE_BUSY_TIMEOUT_MS)
            ),
        )


def _process_operation(
    project_id: str,
    operation: VideoLocalizationOperation,
    *,
    execution_claim: (
        video_localization_operation_execution.OperationExecutionClaim
    ),
) -> None:
    operation_id = operation.operation_id
    status_kind = _status_kind(operation)
    if operation.status == "cancelled" or operation.cancel_requested:
        _mark_operation(
            project_id,
            operation_id,
            kind=status_kind,
            status="cancelled",
            completed_at=operation.completed_at or now_iso(),
            result_summary=_cancelled_task_summary(
                operation.result_summary,
                stage="已取消",
            ),
        )
        return

    commit_gate = _operation_commit_gate(project_id, operation_id)

    def execution_cancelled() -> bool:
        return (
            execution_claim.lost
            or _cancel_requested(project_id, operation_id)
        )

    def guarded_commit(
        action: Callable[[], object],
    ) -> tuple[bool, object | None]:
        if execution_claim.lost:
            return False, None
        return commit_gate.commit(action)

    initial_summary = _initial_operation_summary(
        operation.kind,
        operation.parameters,
    )
    _mark_operation(
        project_id,
        operation_id,
        kind=status_kind,
        status="running",
        progress=0.05,
        started_at=operation.started_at or now_iso(),
        result_summary=initial_summary,
    )
    stage_timer: _StageTimer | None = None
    try:
        if operation.kind == "source_audio":
            if _is_managed_source_audio_operation(operation):
                source_audio_execution.execute_managed_source_audio_operation(
                    project_id,
                    operation,
                    execution_fence=(
                        execution_claim.execution_fence
                    ),
                    commit_guard=guarded_commit,
                )
                return
            source_draft = service.get_video_localization(
                project_id
            )
            if source_draft is None:
                raise AppException(
                    404,
                    "PROJECT_NOT_FOUND",
                    "Project not found",
                )
            shadow_step = (
                operation_step_shadow
                .try_prepare_source_audio_step(
                    execution_claim.execution_fence,
                    source_draft,
                )
            )
            try:
                updated = service.extract_source_audio(
                    project_id,
                    commit_guard=guarded_commit,
                )
            except (
                ExecutionFenceLost,
                ExecutionOperationCancelled,
            ):
                raise
            except AppException as exc:
                operation_step_shadow.try_fail_source_audio_step(
                    shadow_step,
                    exc.code,
                )
                raise
            except Exception:
                operation_step_shadow.try_fail_source_audio_step(
                    shadow_step,
                    "VIDEO_LOCALIZATION_OPERATION_FAILED",
                )
                raise
            summary = operation_state.source_audio_summary(updated)
            if updated is not None:
                operation_step_shadow.try_complete_source_audio_step(
                    shadow_step,
                    updated,
                    summary,
                )
        elif operation.kind == "stems":
            if _is_managed_stem_separation_operation(
                operation
            ):
                (
                    stem_separation_execution
                    .execute_managed_stem_separation_operation(
                        project_id,
                        operation,
                        execution_fence=(
                            execution_claim.execution_fence
                        ),
                        commit_guard=guarded_commit,
                    )
                )
                return
            updated = service.separate_source_audio(
                project_id,
                commit_guard=guarded_commit,
            )
            summary = operation_state.stems_summary(updated)
        elif operation.kind == "speaker_diarization":
            if _is_managed_speaker_diarization_operation(
                operation
            ):
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=None,
                    status="running",
                    progress=0.15,
                    result_summary=(
                        speaker_diarization_operation_projection
                        .running_summary()
                    ),
                )
                diarization_output = (
                    speaker_diarization_execution
                    .execute_managed_speaker_diarization_operation(
                        project_id,
                        operation,
                        execution_fence=(
                            execution_claim.execution_fence
                        ),
                        is_cancelled=execution_cancelled,
                    )
                )
                updated = service.get_video_localization(
                    project_id
                )
                summary = (
                    speaker_diarization_operation_projection
                    .success_summary(diarization_output)
                )
            else:
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_DIARIZATION_WORKFLOW_INVALID",
                    "任务不是当前说话人区分工作流，已拒绝执行。",
                )
        elif operation.kind == "media_export":
            destination_id = str(
                operation.parameters.get("destination_id") or ""
            )
            try:
                destination_directory = (
                    video_localization_export_destinations
                    .resolve_destination(destination_id)
                )
            except (
                video_localization_export_destinations
                .ExportDestinationError
            ) as exc:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_EXPORT_DESTINATION_UNAVAILABLE",
                    str(exc),
                ) from exc
            render_request = (
                VideoLocalizationMediaExportRequest.model_validate(
                    operation.parameters.get("render")
                )
            )
            output_filename = validate_media_export_output_filename(
                str(
                    operation.parameters.get("output_filename")
                    or ""
                ),
                render_request,
            )
            last_reported_progress = 0.0
            last_reported_stage = ""

            def report_media_export_progress(
                progress: float,
                stage: str,
            ) -> None:
                nonlocal last_reported_progress
                nonlocal last_reported_stage
                bounded = max(0.0, min(1.0, progress))
                stage_key = (
                    "render_video"
                    if stage.startswith("正在渲染视频")
                    else stage
                )
                if (
                    stage_key == last_reported_stage
                    and bounded < last_reported_progress + 0.01
                    and bounded < 1.0
                ):
                    return
                last_reported_progress = max(
                    last_reported_progress,
                    bounded,
                )
                last_reported_stage = stage_key
                if bounded < 0.08:
                    stage_id = "prepare"
                    step_updates = {
                        "prepare": {"status": "running"}
                    }
                elif bounded < 0.97:
                    stage_id = "render"
                    step_updates = {
                        "prepare": {"status": "success"},
                        "render": {"status": "running"},
                    }
                else:
                    stage_id = "validate"
                    step_updates = {
                        "prepare": {"status": "success"},
                        "render": {"status": "success"},
                        "validate": {"status": "running"},
                    }
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=status_kind,
                    status="running",
                    progress=max(0.01, bounded),
                    result_summary={
                        "stage": stage,
                        "stage_id": stage_id,
                        "task_step_results": step_updates,
                    },
                )

            export_result = (
                video_localization_exports
                .video_localization_exports
                .create_media_export_at(
                    project_id,
                    render_request,
                    destination_directory,
                    output_filename,
                    on_progress=report_media_export_progress,
                    is_cancelled=execution_cancelled,
                )
            )
            if export_result is None:
                raise AppException(
                    404,
                    "VIDEO_LOCALIZATION_MEDIA_EXPORT_NOT_FOUND",
                    "导出项目不存在。",
                )
            summary = {
                "stage": "成品已保存",
                "stage_id": "validate",
                "filename": export_result["filename"],
                "size_bytes": export_result["size_bytes"],
                "export_kind": export_result["kind"],
                "mixed_track_count": export_result[
                    "mixed_track_count"
                ],
                "task_step_results": {
                    "prepare": {
                        "label": _TASK_STEP_LABELS[
                            "media_export"
                        ]["prepare"],
                        "order": 10,
                        "status": "success",
                        "summary": "导出内容与保存目录检查通过。",
                    },
                    "render": {
                        "label": _TASK_STEP_LABELS[
                            "media_export"
                        ]["render"],
                        "order": 20,
                        "status": "success",
                        "summary": "所选导出内容已经生成。",
                    },
                    "validate": {
                        "label": _TASK_STEP_LABELS[
                            "media_export"
                        ]["validate"],
                        "order": 30,
                        "status": "success",
                        "summary": (
                            f"已保存 {export_result['filename']}"
                        ),
                    },
                },
            }
            updated = service.get_video_localization(project_id)
        elif operation.kind == "english_asr":
            engine_id = str(operation.parameters.get("engine_id") or source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID)
            source_track_id = str(operation.parameters.get("source_track_id") or "auto")
            source_language = source_pipeline.normalize_source_language(
                str(operation.parameters.get("source_language") or "auto")
            )
            if _is_asr_development_target_operation(operation):
                target_step_id = str(
                    operation.parameters[
                        "development_target_step_id"
                    ]
                )
                source_operation_id = str(
                    operation.parameters[
                        "development_source_operation_id"
                    ]
                )
                predecessor_operation_id = str(
                    operation.parameters.get(
                        "development_predecessor_operation_id"
                    )
                    or ""
                )
                replay_source_operation_id = source_operation_id
                checkpoint_writer = (
                    development_checkpoints
                    .LocalizationDevelopmentCheckpointWriter(
                        DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT,
                        project_id=project_id,
                        workflow_operation_id=operation_id,
                    )
                )
                predecessor_step_id = ""
                if predecessor_operation_id:
                    predecessor_operation = get_operation(
                        project_id, predecessor_operation_id
                    )
                    if (
                        predecessor_operation is None
                        or predecessor_operation.status != "success"
                    ):
                        raise AppException(
                            409,
                            "VIDEO_LOCALIZATION_ASR_CONTINUATION_PREDECESSOR_INVALID",
                            "ASR 开发续接的直接前驱任务尚未成功。",
                        )
                    predecessor_step_id = str(
                        predecessor_operation.parameters.get(
                            "development_target_step_id"
                        )
                        or ""
                    )
                    continuation_input = (
                        asr_development_continuation
                        .build_target_input(
                            DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT,
                            project_id=project_id,
                            lineage_operation_id=source_operation_id,
                            predecessor_operation_id=(
                                predecessor_operation_id
                            ),
                            predecessor_step_id=predecessor_step_id,
                            target_step_id=target_step_id,
                        )
                    )
                    checkpoint_writer(
                        f"{target_step_id}_input",
                        continuation_input,
                    )
                    replay_source_operation_id = operation_id
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=None,
                    status="running",
                    progress=0.25,
                    result_summary={
                        "stage": (
                            "正在从类型化快照重跑："
                            f"{target_step_id}"
                        ),
                        "stage_id": target_step_id,
                        "execution_mode": "development_target",
                        "development_target_step_id": (
                            target_step_id
                        ),
                        "development_source_operation_id": (
                            source_operation_id
                        ),
                        "formal_project_data_changed": False,
                    },
                )
                replay = (
                    asr_development_replay.replay_asr_target(
                        root=(
                            DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT
                        ),
                        project_id=project_id,
                        source_operation_id=(
                            replay_source_operation_id
                        ),
                        replay_operation_id=operation_id,
                        target_step_id=target_step_id,
                        is_cancelled=execution_cancelled,
                    )
                )
                serialized = replay.result.model_dump(mode="json")
                actual_contract = str(
                    serialized.get("contract_version")
                    or serialized.get("schema_version")
                    or ""
                )
                expected_contract = (
                    asr_development_replay
                    .expected_output_contract(target_step_id)
                )
                if (
                    not actual_contract
                    or not (
                        asr_development_replay
                        .output_contract_matches(
                            target_step_id,
                            actual_contract,
                        )
                    )
                ):
                    raise AppException(
                        409,
                        "VIDEO_LOCALIZATION_ASR_DEVELOPMENT_OUTPUT_INVALID",
                        "定点重跑结果不符合当前工作流声明的输出契约。",
                        {
                            "target_step_id": target_step_id,
                            "expected_contract_version": (
                                expected_contract
                            ),
                            "actual_contract_version": (
                                actual_contract
                            ),
                        },
                    )
                checkpoint_writer(
                    f"{target_step_id}_result",
                    replay.result,
                )
                continuation_state = None
                if (
                    target_step_id
                    in asr_development_continuation.CONTINUATION_STEP_ORDER
                ):
                    continuation_state = (
                        asr_development_continuation
                        .build_state_after_result(
                            DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT,
                            project_id=project_id,
                            lineage_operation_id=source_operation_id,
                            predecessor_operation_id=(
                                predecessor_operation_id or None
                            ),
                            target_step_id=target_step_id,
                            result=replay.result,
                        )
                    )
                if continuation_state is not None:
                    checkpoint_writer(
                        "continuation_state",
                        continuation_state,
                    )
                step_definition = (
                    asr_development_replay
                    .development_task_definition(target_step_id)
                )
                updated = service.get_video_localization(project_id)
                summary = {
                    **_initial_operation_summary(
                        "english_asr",
                        operation.parameters,
                    ),
                    "stage": "ASR 开发结果已保存，未写入正式字幕",
                    "stage_id": target_step_id,
                    "execution_mode": "development_target",
                    "development_target_step_id": target_step_id,
                    "development_source_operation_id": (
                        source_operation_id
                    ),
                    "source_snapshot_step_id": (
                        replay.source_snapshot_step_id
                    ),
                    "skipped_paid_preparation": (
                        replay.skipped_paid_preparation
                    ),
                    "formal_project_data_changed": False,
                    "result_contract_version": actual_contract,
                    "task_step_results": {
                        target_step_id: {
                            "label": step_definition.label,
                            "order": step_definition.order,
                            "status": "success",
                            "summary": (
                                "已使用模型调用后的准备快照完成确定性重放。"
                                if replay.skipped_paid_preparation
                                else (
                                    "已使用直接前驱的新结果构建输入并运行。"
                                    if predecessor_operation_id
                                    else "已使用原节点入口快照重跑。"
                                )
                            ),
                            "result_contract_version": (
                                actual_contract
                            ),
                        }
                    },
                }
            elif _is_partial_asr_operation(operation):
                raise AppException(
                    409,
                    "VIDEO_LOCALIZATION_ASR_STOP_AFTER_RETIRED",
                    "旧 ASR 开发断点只保留历史查看；请用 development_target 创建新的定点任务。",
                )
            else:
                segmentation_profile_id = str(operation.parameters.get("segmentation_profile_id") or "generic_zh")
                diarization_engine_id = (
                    str(operation.parameters["diarization_engine_id"])
                    if operation.parameters.get("diarization_engine_id")
                    else None
                )
                stage_timer = _StageTimer(_asr_stage_id)

                def report_asr_progress(progress: float, stage: str) -> None:
                    assert stage_timer is not None
                    display_stage = stage.split("|", 1)[1] if stage.startswith("flow:") and "|" in stage else stage
                    _mark_operation(
                        project_id,
                        operation_id,
                        kind=status_kind,
                        status="running",
                        progress=progress,
                        result_summary={
                            "stage": display_stage,
                            "stage_id": _asr_stage_id(stage),
                            "task_stage_timings": stage_timer.update(stage),
                        },
                    )

                def report_asr_step(step_id: str, step_result: dict) -> None:
                    enriched_result = dict(step_result)
                    if step_id in asr_flow.ASR_STEP_ORDER:
                        enriched_result.setdefault("label", _TASK_STEP_LABELS["english_asr"].get(step_id, step_id))
                        enriched_result.setdefault("order", asr_flow.ASR_STEP_ORDER[step_id])
                    if step_id == "visual_evidence":
                        enriched_result = (
                            _with_formal_visual_evidence_links(
                                project_id,
                                operation_id,
                                enriched_result,
                            )
                        )
                    if step_id.startswith("section_review_r"):
                        enriched_result = (
                            _without_non_actionable_section_review_items(
                                enriched_result,
                            )
                        )
                    result_summary = {
                        "task_step_results": {step_id: enriched_result},
                    }
                    duration_ms = enriched_result.get("duration_ms")
                    if isinstance(duration_ms, (int, float)):
                        result_summary["task_stage_timings"] = {
                            step_id: {
                                "duration_ms": max(0, int(duration_ms)),
                                "atomic": True,
                            }
                        }
                    _mark_operation(
                        project_id,
                        operation_id,
                        kind=status_kind,
                        status="running",
                        result_summary=result_summary,
                    )

                updated = service.transcribe_english_source_audio(
                    project_id,
                    operation_id=operation_id,
                    expected_submission_binding=operation.parameters.get("submission_binding"),
                    finalize_formal_commit=lambda draft, formal_summary, completed_at: _finalize_formal_operation(
                        draft, operation_id, formal_summary, completed_at,
                    ),
                    engine_id=engine_id,
                    source_track_id=source_track_id,
                    source_language=source_language,
                    is_cancelled=execution_cancelled,
                    on_progress=report_asr_progress,
                    on_report=report_asr_step,
                    on_atomic_snapshot=(
                        development_checkpoints
                        .LocalizationDevelopmentCheckpointWriter(
                            DEVELOPMENT_ASR_WORKFLOW_CHECKPOINT_ROOT,
                            project_id=project_id,
                            workflow_operation_id=operation_id,
                        )
                        if (
                            settings_store.get()
                            .video_localization_development_step_control_enabled
                        )
                        else None
                    ),
                    on_preview=lambda phase, cues: _mark_operation(
                        project_id,
                        operation_id,
                        kind=status_kind,
                        status="running",
                        result_summary={"preview_phase": phase, "preview_cues": cues},
                    ),
                    segmentation_profile_id=segmentation_profile_id,
                    llm_profile_id=(
                        str(operation.parameters.get("profile_id") or "").strip()
                        or None
                    ),
                    vision_profile_id=(
                        str(operation.parameters.get("profile_id") or "").strip()
                        or None
                    ),
                    diarization_engine_id=diarization_engine_id,
                    min_speakers=operation.parameters.get("min_speakers"),
                    max_speakers=operation.parameters.get("max_speakers"),
                    commit_guard=guarded_commit,
                )
                summary = operation_state.english_asr_summary(updated)
                summary = _finish_stage_timings(stage_timer, summary)
        elif operation.kind == "dub_subtitle_generation":
            def report_dub_subtitle_progress(
                progress: float,
                stage: str,
            ) -> None:
                workflow_stage = stage.startswith("flow:")
                display_stage = (
                    stage.split("|", 1)[1]
                    if workflow_stage and "|" in stage
                    else stage
                )
                stage_id = (
                    stage.split("|", 1)[0].removeprefix("flow:")
                    if workflow_stage
                    else str(
                        operation.result_summary.get("stage_id")
                        or "prepare_track"
                    )
                )
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=status_kind,
                    status="running",
                    progress=progress,
                    result_summary={
                        "stage": display_stage,
                        "stage_id": stage_id,
                    },
                )

            def report_dub_subtitle_step(
                step_id: str,
                step_result: dict,
            ) -> None:
                projected = dict(step_result)
                timing = projected.pop("_task_timing", None)
                result_summary = {
                    "task_step_results": {step_id: projected},
                }
                if isinstance(timing, dict):
                    result_summary["task_stage_timings"] = {
                        step_id: timing,
                    }
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=status_kind,
                    status="running",
                    result_summary=result_summary,
                )

            development_execution = (
                dub_subtitle_workflow
                .DubSubtitleDevelopmentExecutionConfig(
                    target_step_id=str(
                        operation.parameters[
                            "development_target_step_id"
                        ]
                    ),
                    development_session_id=str(
                        operation.parameters[
                            "development_session_id"
                        ]
                    ),
                    project_id=project_id,
                    snapshot_root=(
                        DEVELOPMENT_DUB_SUBTITLE_WORKFLOW_CHECKPOINT_ROOT
                    ),
                )
                if operation.parameters.get("execution_mode")
                == "development_target"
                else None
            )
            updated, summary = service.generate_dub_subtitles(
                project_id,
                operation_id=operation_id,
                engine_id=str(
                    operation.parameters.get("engine_id")
                    or source_pipeline.DEFAULT_ENGLISH_ASR_ENGINE_ID
                ),
                regeneration_mode=str(
                    operation.parameters.get("regeneration_mode") or "auto"
                ),
                development_execution=development_execution,
                is_cancelled=execution_cancelled,
                on_progress=report_dub_subtitle_progress,
                on_report=report_dub_subtitle_step,
                on_preview=lambda phase, cues: _mark_operation(
                    project_id,
                    operation_id,
                    kind=status_kind,
                    status="running",
                    result_summary={
                        "preview_phase": phase,
                        "preview_cues": cues,
                    },
                ),
                commit_guard=guarded_commit,
            )
        elif operation.kind == "localization_draft":
            stage_timer = _StageTimer(_localization_stage_id)

            formal_retry_source_operation_ids: list[str] = []
            if (
                operation.parameters.get("execution_mode") != "development_target"
            ):
                retry_events = (
                    video_localization_operation_ledger_store.list_outbox(
                        project_id,
                        operation_id,
                    )
                )
                retry_event = next(
                    (
                        event
                        for event in retry_events
                        if event.command_type == "retry"
                        and event.source_operation_id
                    ),
                    None,
                )
                if retry_event is not None:
                    current_entry = (
                        video_localization_operation_ledger_store.get_operation(
                            project_id,
                            operation_id,
                        )
                    )
                    source_entry = (
                        video_localization_operation_ledger_store.get_operation(
                            project_id,
                            str(retry_event.source_operation_id),
                        )
                    )
                    if (
                        current_entry is not None
                        and source_entry is not None
                        and current_entry.kind == source_entry.kind
                        and current_entry.parameters_fingerprint
                        == source_entry.parameters_fingerprint
                    ):
                        candidate_source_id = str(
                            retry_event.source_operation_id
                        )
                        visited_source_ids: set[str] = set()
                        while (
                            candidate_source_id
                            and candidate_source_id
                            not in visited_source_ids
                            and len(visited_source_ids) < 20
                        ):
                            visited_source_ids.add(candidate_source_id)
                            candidate_entry = (
                                video_localization_operation_ledger_store
                                .get_operation(
                                    project_id,
                                    candidate_source_id,
                                )
                            )
                            if (
                                candidate_entry is None
                                or candidate_entry.kind
                                != current_entry.kind
                                or candidate_entry.parameters_fingerprint
                                != current_entry.parameters_fingerprint
                            ):
                                break
                            formal_retry_source_operation_ids.append(
                                candidate_source_id
                            )
                            parent_event = next(
                                (
                                    event
                                    for event in (
                                        video_localization_operation_ledger_store
                                        .list_outbox(
                                            project_id,
                                            candidate_source_id,
                                        )
                                    )
                                    if event.command_type == "retry"
                                    and event.source_operation_id
                                ),
                                None,
                            )
                            candidate_source_id = (
                                str(parent_event.source_operation_id)
                                if parent_event is not None
                                else ""
                            )

            def report_localization_progress(progress: float, stage: str) -> None:
                assert stage_timer is not None
                workflow_stage = stage.startswith("flow:")
                display_stage = (
                    stage.split("|", 1)[1]
                    if workflow_stage and "|" in stage
                    else stage
                )
                result_summary = {
                    "stage": display_stage,
                    "stage_id": _localization_stage_id(stage),
                }
                if not workflow_stage:
                    result_summary["task_stage_timings"] = (
                        stage_timer.update(stage)
                    )
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=operation.kind,
                    status="running",
                    progress=progress,
                    result_summary=result_summary,
                )

            def report_localization_step(step_id: str, step_result: dict) -> None:
                step_result = dict(step_result)
                timing = step_result.pop("_task_timing", None)
                if step_id == "collect_localization_visual_evidence_v3":
                    step_result = _with_localization_visual_evidence_links(
                        project_id,
                        operation_id,
                        step_result,
                    )
                result_summary = {
                    "task_step_results": {step_id: step_result},
                }
                if isinstance(timing, dict):
                    result_summary["task_stage_timings"] = {
                        step_id: timing
                    }
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=operation.kind,
                    status="running",
                    result_summary=result_summary,
                )

            def finalize_localization_formal_commit(
                draft: VideoLocalizationDraft,
                formal_summary: dict,
                completed_at: str,
            ) -> VideoLocalizationDraft:
                return _finalize_formal_operation(
                    draft,
                    operation_id,
                    formal_summary,
                    completed_at,
                )

            development_execution = (
                localization_workflow_execution
                .LocalizationDevelopmentExecutionConfig(
                    development_session_id=str(
                        operation.parameters[
                            "development_session_id"
                        ]
                    ),
                    target_step_id=str(
                        operation.parameters[
                            "development_target_step_id"
                        ]
                    ),
                    snapshot_root=(
                        DEVELOPMENT_LOCALIZATION_WORKFLOW_CHECKPOINT_ROOT
                    ),
                    force_target=bool(
                        operation.parameters.get(
                            "force_development_target",
                            True,
                        )
                    ),
                    recover_verified_candidates=bool(
                        operation.parameters.get("recover_verified_candidates", False)
                    ),
                    retry_unknown_batch_step_id=operation.parameters.get("retry_unknown_batch_step_id"),
                )
                if operation.parameters.get("execution_mode")
                == "development_target"
                else None
            )
            updated, summary = service.run_localization_v3_draft(
                project_id,
                operation_id=operation_id,
                expected_submission_binding=operation.parameters.get("submission_binding"),
                source_language=str(
                    operation.parameters.get("source_language") or "auto"
                ),
                target_language=(
                    str(operation.parameters.get("target_language") or "")
                    or None
                ),
                profile_id=(
                    str(operation.parameters.get("profile_id") or "")
                    or None
                ),
                localization_requirements_id=(
                    str(
                        operation.parameters.get(
                            "localization_requirements_id"
                        )
                        or ""
                    )
                    or None
                ),
                is_cancelled=execution_cancelled,
                on_progress=report_localization_progress,
                on_report=report_localization_step,
                on_preview=lambda phase, cues: _mark_operation(
                    project_id,
                    operation_id,
                    kind=operation.kind,
                    status="running",
                    result_summary={
                        "preview_phase": phase,
                        "preview_cues": cues,
                    },
                ),
                on_atomic_result=(
                    development_checkpoints.LocalizationDevelopmentCheckpointWriter(
                        DEVELOPMENT_LOCALIZATION_WORKFLOW_CHECKPOINT_ROOT,
                        project_id=project_id,
                        workflow_operation_id=(
                            development_execution.development_session_id
                            if development_execution is not None
                            else operation_id
                        ),
                        behavior_fingerprint_resolver=(
                            localization_workflow_nodes
                            .checkpoint_behavior_fingerprint
                        ),
                    )
                    if (
                        settings_store.get()
                        .video_localization_development_step_control_enabled
                    )
                    else None
                ),
                formal_retry_source_operation_ids=(
                    formal_retry_source_operation_ids
                ),
                formal_retry_checkpoint_root=(
                    DEVELOPMENT_LOCALIZATION_WORKFLOW_CHECKPOINT_ROOT
                    if formal_retry_source_operation_ids
                    else None
                ),
                development_execution=development_execution,
                finalize_formal_commit=(
                    finalize_localization_formal_commit
                    if development_execution is None
                    else None
                ),
                commit_guard=guarded_commit,
            )
            summary = _finish_stage_timings(stage_timer, summary)
        elif operation.kind == "reference_clips":
            if _is_managed_reference_candidates_operation(
                operation
            ):
                (
                    reference_candidates_execution
                    .execute_managed_reference_candidates_operation(
                        project_id,
                        operation,
                        execution_fence=(
                            execution_claim.execution_fence
                        ),
                        commit_guard=guarded_commit,
                    )
                )
                return
            updated = service.create_reference_clips_from_cues(project_id)
            summary = operation_state.reference_clips_summary(updated)
        elif operation.kind == "semantic_tts_grouping":
            def report_semantic_progress(progress: float, stage: str) -> None:
                stage_id = (
                    "prepare" if "整理" in stage else
                    "validate" if "检查" in stage else
                    "group"
                )
                _mark_operation(
                    project_id,
                    operation_id,
                    kind=operation.kind,
                    status="running",
                    progress=progress,
                    result_summary={
                        "stage": stage,
                        "stage_id": stage_id,
                        "task_step_results": {
                            stage_id: {
                                "label": _TASK_STEP_LABELS["semantic_tts_grouping"][stage_id],
                                "order": {"prepare": 10, "group": 20, "validate": 30}[stage_id],
                                "status": "running",
                                "summary": stage,
                            }
                        },
                    },
                )

            updated, grouping = service.run_semantic_tts_grouping(
                project_id,
                execution_fence=execution_claim.execution_fence,
                profile_id=str(operation.parameters.get("profile_id") or "") or None,
                expected_profile_configuration_fingerprint=(
                    str(
                        operation.parameters.get(
                            "profile_configuration_fingerprint"
                        )
                        or ""
                    )
                    or None
                ),
                target_chars=int(operation.parameters.get("target_chars") or 120),
                max_chars=int(operation.parameters.get("max_chars") or 180),
                on_progress=report_semantic_progress,
                commit_guard=guarded_commit,
            )
            grouping_llm_calls = [
                llm_observability.VideoLocalizationLlmCallRecord.model_validate(
                    item
                )
                for item in grouping.get("llm_calls") or []
                if isinstance(item, dict)
            ]
            grouping_call_items, grouping_call_metrics = (
                llm_observability.project_llm_calls(
                    grouping_llm_calls,
                    calls_complete=True,
                )
            )
            summary = {
                "stage": "语义分组已保存",
                "stage_id": "write",
                "semantic_group_count": len(grouping.get("groups") or []),
                "llm_profile_id": str(operation.parameters.get("profile_id") or "") or None,
                "llm_model_id": (
                    grouping_llm_calls[0].model_id
                    if grouping_llm_calls
                    else None
                ),
                **_semantic_tts_grouping_workflow_summary_fields(),
                "task_step_results": {
                    "prepare": {
                        "label": _TASK_STEP_LABELS["semantic_tts_grouping"]["prepare"],
                        "order": 10,
                        "status": "success",
                        "summary": "已整理字幕顺序和说话人。",
                    },
                    "group": {
                        "label": _TASK_STEP_LABELS["semantic_tts_grouping"]["group"],
                        "order": 20,
                        "status": "success",
                        "summary": "已按连续语义和场景完成分组。",
                        "debug": {
                            "description": (
                                "用于核对本次语义分组实际使用的模型、"
                                "调用方式和资源消耗。"
                            ),
                            "metrics": grouping_call_metrics,
                            "sections": (
                                [
                                    {
                                        "title": "模型调用明细",
                                        "items": grouping_call_items,
                                    }
                                ]
                                if grouping_call_items
                                else []
                            ),
                            "notes": [],
                        },
                    },
                    "validate": {
                        "label": _TASK_STEP_LABELS["semantic_tts_grouping"]["validate"],
                        "order": 30,
                        "status": "success",
                        "summary": "已确认字幕无遗漏、无重复且没有跨说话人。",
                    },
                    "write": {
                        "label": _TASK_STEP_LABELS["semantic_tts_grouping"]["write"],
                        "order": 40,
                        "status": "success",
                        "summary": f"已保存 {len(grouping.get('groups') or [])} 个连续配音组。",
                    }
                },
            }
        else:
            raise AppException(
                400, "VIDEO_LOCALIZATION_OPERATION_UNSUPPORTED", f"Unsupported operation: {operation.kind}"
            )
        if updated is None:
            raise AppException(404, "PROJECT_NOT_FOUND", "Project not found")
        latest = get_operation(project_id, operation_id)
        if _formal_operation_already_committed(operation, latest):
            return
        if operation_state.operation_was_cancelled(latest):
            cancellation_summary = _cancelled_task_summary(
                dict(
                    latest.result_summary
                    if latest is not None
                    else {}
                ),
                stage="已取消",
                stage_timer=stage_timer,
            )
            _mark_operation(
                project_id,
                operation_id,
                kind=status_kind,
                status="cancelled",
                progress=1.0,
                completed_at=now_iso(),
                error_message="已取消，任务结果未作为成功状态保留。",
                result_summary=cancellation_summary,
            )
            return
        latest_summary = dict(latest.result_summary) if latest is not None else {}
        if _is_development_operation(operation):
            summary = _merge_task_step_results(
                latest_summary,
                summary,
            )
            completed_at = now_iso()
            if summary.get("stage_id") in (
                _MANAGED_LOCAL_WALL_DURATION_STAGE_IDS
            ):
                managed_local_detail.apply_workflow_duration(
                    summary,
                    video_localization_operation_step_store
                    .list_step_attempts(project_id, operation_id),
                    started_at=operation.started_at,
                    completed_at=completed_at,
                )
            _mark_operation(
                project_id,
                operation_id,
                kind=None,
                status="success",
                progress=1.0,
                completed_at=completed_at,
                result_summary=summary,
            )
            return
        final_asr_steps = summary.get("task_step_results") if operation.kind == "english_asr" else None
        summary = _merge_task_step_results(latest_summary, summary)
        if operation.kind == "english_asr":
            if isinstance(final_asr_steps, dict):
                # Rebuild from persisted final artifacts so every recorded review round
                # remains inspectable while the consolidated quality result stays current.
                # Same-run debug data is observability, not business state, so retain it
                # when the final project snapshot does not carry an equally rich copy.
                summary["task_step_results"] = (
                    _merge_authoritative_task_step_results(
                        latest_summary.get("task_step_results"),
                        final_asr_steps,
                    )
                )
            summary = _mark_unused_asr_steps_skipped(summary)
        elif operation.kind == "localization_draft":
            summary = _mark_unused_localization_steps_skipped(summary)
        summary["task_final_result"] = _build_task_final_result(operation, summary)
        _mark_operation(
            project_id,
            operation_id,
            kind=status_kind,
            status="success",
            progress=1.0,
            completed_at=now_iso(),
            result_summary=summary,
        )
    except (ExecutionFenceLost, ExecutionOperationCancelled):
        raise
    except AppException as exc:
        latest = get_operation(project_id, operation_id)
        if _formal_operation_already_committed(operation, latest):
            _log_formal_post_commit_failure(operation, exc)
            return
        if operation_state.operation_was_cancelled(latest):
            cancellation_summary = _cancelled_task_summary(
                dict(
                    latest.result_summary
                    if latest is not None
                    else {}
                ),
                stage="已取消",
                stage_timer=stage_timer,
            )
            _mark_operation(
                project_id,
                operation_id,
                kind=status_kind,
                status="cancelled",
                progress=1.0,
                completed_at=now_iso(),
                error_message="已取消，失败结果未保留。",
                result_summary=cancellation_summary,
            )
            return
        failure_summary = _finish_stage_timings(
            stage_timer,
            dict(latest.result_summary if latest is not None else {}),
        )
        if exc.detail_dict:
            nested_error = exc.detail_dict.get("error_detail")
            failure_summary["error_detail"] = {
                **_fallback_error_detail(exc.code, exc.message, str(failure_summary.get("stage") or "")),
                **(nested_error if isinstance(nested_error, dict) else exc.detail_dict),
            }
            step_result = exc.detail_dict.get("step_result")
            if isinstance(step_result, dict):
                step_id = str(failure_summary.get("stage_id") or "task")
                task_step_results = dict(failure_summary.get("task_step_results") or {})
                task_step_results[step_id] = step_result
                failure_summary["task_step_results"] = task_step_results
        else:
            failure_summary["error_detail"] = _fallback_error_detail(
                exc.code,
                exc.message,
                str(failure_summary.get("stage") or ""),
            )
        failure_summary = _close_unfinished_task_steps_after_failure(
            failure_summary
        )
        if status_kind is not None:
            _mark_kind_failed(project_id, operation.kind, exc.code, exc.message)
        _mark_operation(
            project_id,
            operation_id,
            kind=status_kind,
            status="failed",
            progress=1.0,
            completed_at=now_iso(),
            error_code=exc.code,
            error_message=exc.message,
            result_summary=failure_summary,
        )
    except Exception as exc:
        latest = get_operation(project_id, operation_id)
        if _formal_operation_already_committed(operation, latest):
            _log_formal_post_commit_failure(operation, exc)
            return
        if operation_state.operation_was_cancelled(latest):
            cancellation_summary = _cancelled_task_summary(
                dict(
                    latest.result_summary
                    if latest is not None
                    else {}
                ),
                stage="已取消",
                stage_timer=stage_timer,
            )
            _mark_operation(
                project_id,
                operation_id,
                kind=status_kind,
                status="cancelled",
                progress=1.0,
                completed_at=now_iso(),
                error_message="已取消，失败结果未保留。",
                result_summary=cancellation_summary,
            )
            return
        failure_summary = _finish_stage_timings(
            stage_timer,
            dict(latest.result_summary if latest is not None else {}),
        )
        failure_summary["error_detail"] = _fallback_error_detail(
            "VIDEO_LOCALIZATION_OPERATION_FAILED",
            str(exc),
            str(failure_summary.get("stage") or ""),
        )
        failure_summary = _close_unfinished_task_steps_after_failure(
            failure_summary
        )
        if status_kind is not None:
            _mark_kind_failed(project_id, operation.kind, "VIDEO_LOCALIZATION_OPERATION_FAILED", str(exc))
        _mark_operation(
            project_id,
            operation_id,
            kind=status_kind,
            status="failed",
            progress=1.0,
            completed_at=now_iso(),
            error_code="VIDEO_LOCALIZATION_OPERATION_FAILED",
            error_message=str(exc),
            result_summary=failure_summary,
        )


def _enqueue(operation_key: OperationRuntimeKey) -> None:
    start_worker()
    if not _runtime.enqueue_once(operation_key):
        return
    with _lock:
        scheduler = _scheduler
    if scheduler is None or not scheduler.put(operation_key):
        _runtime.complete(operation_key)


def _recover_active_operations() -> None:
    project_ids = (
        video_localization_operation_ledger_store
        .list_recoverable_project_ids(
            _ACTIVE_STATUSES
        )
    )
    for project_id in project_ids:
        _recover_project_operations(project_id)


def _is_resumable_semantic_tts_grouping_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    expected_workflow_version = (
        workflow_contracts
        .SEMANTIC_TTS_GROUPING_WORKFLOW_DEFINITION
        .schema_version
    )
    fingerprint = str(
        operation.parameters.get(
            "profile_configuration_fingerprint"
        )
        or ""
    ).strip()
    return (
        operation.kind == "semantic_tts_grouping"
        and str(
            operation.result_summary.get(
                "workflow_schema_version"
            )
            or ""
        ).strip()
        == expected_workflow_version
        and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        is not None
    )


def _is_resumable_source_audio_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_source_audio_operation(operation)


def _is_resumable_stem_separation_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_stem_separation_operation(operation)


def _is_resumable_reference_candidates_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_reference_candidates_operation(operation)


def _is_resumable_speaker_diarization_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_speaker_diarization_operation(operation)


def _is_resumable_asr_raw_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_asr_raw_operation(operation)


def _is_resumable_initial_analysis_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_initial_analysis_operation(
        operation
    )


def _is_resumable_document_understanding_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_document_understanding_operation(
        operation
    )


def _is_resumable_visual_evidence_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_visual_evidence_operation(operation)


def _is_resumable_research_evidence_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_research_evidence_operation(operation)


def _is_resumable_entity_normalization_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_entity_normalization_operation(
        operation
    )


def _is_resumable_section_review_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_section_review_operation(operation)


def _is_resumable_review_decisions_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_review_decisions_operation(operation)


def _is_resumable_whole_recheck_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_whole_recheck_operation(operation)


def _is_resumable_transcript_quality_gate_operation(
    operation: VideoLocalizationOperation,
) -> bool:
    return _is_managed_transcript_quality_gate_operation(
        operation
    )


def _recover_project_operations(project_id: str) -> None:
    operations = list_operations(project_id)
    if operations is None:
        return
    for operation in operations:
        if operation.status not in _ACTIVE_STATUSES:
            continue
        if operation.cancel_requested:
            _mark_operation(
                project_id,
                operation.operation_id,
                kind=_status_kind(operation),
                status="cancelled",
                completed_at=operation.completed_at or now_iso(),
            )
            continue
        if operation.status == "queued" and operation.started_at is None:
            continue
        try:
            decision = (
                video_localization_operation_execution
                .acquire_execution_claim(
                    project_id,
                    operation.operation_id,
                    runner_id=_runner_id,
                )
            )
        except Exception as exc:
            _logger.warning(
                "operation recovery claim failed (%s)",
                type(exc).__name__,
            )
            _schedule_operation_recovery(
                project_id,
                operation.operation_id,
                deadline_ms=(
                    time.time_ns() // 1_000_000
                    + max(1_000, database.SQLITE_BUSY_TIMEOUT_MS)
                ),
            )
            continue
        if not decision.acquired:
            if decision.retry_at_ms is not None:
                _schedule_operation_recovery(
                    project_id,
                    operation.operation_id,
                    deadline_ms=decision.retry_at_ms,
                )
            continue
        claim = decision.claim
        assert claim is not None
        claim.start_heartbeat()
        handled = False
        interrupted_legacy_operation = False
        try:
            with execution_fence_scope(claim.execution_fence):
                if (
                    _is_resumable_semantic_tts_grouping_operation(
                        operation
                    )
                    or _is_resumable_source_audio_operation(
                        operation
                    )
                    or _is_resumable_stem_separation_operation(
                        operation
                    )
                    or _is_resumable_reference_candidates_operation(
                        operation
                    )
                    or _is_resumable_speaker_diarization_operation(
                        operation
                    )
                    or _is_resumable_asr_raw_operation(
                        operation
                    )
                    or _is_resumable_initial_analysis_operation(
                        operation
                    )
                    or _is_resumable_document_understanding_operation(
                        operation
                    )
                    or _is_resumable_visual_evidence_operation(
                        operation
                    )
                    or _is_resumable_research_evidence_operation(
                        operation
                    )
                    or _is_resumable_entity_normalization_operation(
                        operation
                    )
                    or _is_resumable_section_review_operation(
                        operation
                    )
                    or _is_resumable_review_decisions_operation(
                        operation
                    )
                    or _is_resumable_whole_recheck_operation(
                        operation
                    )
                    or _is_resumable_transcript_quality_gate_operation(
                        operation
                    )
                ):
                    _process_operation(
                        project_id,
                        operation,
                        execution_claim=claim,
                    )
                else:
                    _mark_operation(
                        project_id,
                        operation.operation_id,
                        kind=_status_kind(operation),
                        status="failed",
                        completed_at=(
                            operation.completed_at or now_iso()
                        ),
                        error_code=(
                            "VIDEO_LOCALIZATION_OPERATION_INTERRUPTED"
                        ),
                        error_message=(
                            "服务在任务运行期间停止，本次任务已中断。"
                            "已完成的正式结果不会被覆盖，可以从任务记录重新执行。"
                        ),
                        result_summary=(
                            _close_unfinished_task_steps_after_failure(
                                {
                                    **operation.result_summary,
                                    "stage": "任务因服务停止而中断",
                                    "interrupted": True,
                                },
                                error_code=(
                                    "VIDEO_LOCALIZATION_OPERATION_INTERRUPTED"
                                ),
                                error_message=(
                                    "服务在任务运行期间停止，本次任务已中断。"
                                    "已完成的正式结果不会被覆盖，可以从任务记录重新执行。"
                                ),
                            )
                        ),
                    )
                    interrupted_legacy_operation = True
            handled = True
        except ExecutionFenceLost:
            claim.mark_lost()
        except ExecutionOperationCancelled:
            pass
        except Exception as exc:
            _logger.warning(
                "operation recovery commit failed (%s)",
                type(exc).__name__,
            )
            _schedule_operation_recovery(
                project_id,
                operation.operation_id,
                deadline_ms=(
                    time.time_ns() // 1_000_000
                    + max(1_000, database.SQLITE_BUSY_TIMEOUT_MS)
                ),
            )
        finally:
            current = get_operation(
                project_id,
                operation.operation_id,
            )
            terminal_status, error_code = _attempt_terminal_result(
                current
            )
            if interrupted_legacy_operation:
                terminal_status = "failed"
                error_code = (
                    "VIDEO_LOCALIZATION_OPERATION_INTERRUPTED"
                )
            try:
                claim.finish(
                    status=terminal_status,
                    error_code=error_code,
                )
            except Exception as exc:
                _logger.warning(
                    "operation recovery claim finish failed (%s)",
                    type(exc).__name__,
                )
        current = get_operation(
            project_id,
            operation.operation_id,
        )
        if (
            handled
            and current is not None
            and current.status in _TERMINAL_STATUSES
        ):
            _runtime.cancel_recovery(
                (project_id, operation.operation_id)
            )
    for operation in operations:
        if operation.status == "queued":
            _enqueue((project_id, operation.operation_id))


def _operation_from_draft(draft: VideoLocalizationDraft, operation_id: str) -> VideoLocalizationOperation | None:
    return operation_state.operation_from_draft(draft, operation_id)


def _active_operation_for_kind(
    project_id: str,
    kind: OperationKind,
    *,
    fallback_draft: VideoLocalizationDraft,
) -> VideoLocalizationOperation | None:
    operations = list_operations(project_id)
    if operations is None:
        operations = list(fallback_draft.operations)
    return next(
        (
            operation
            for operation in operations
            if operation.kind == kind
            and operation.status in _ACTIVE_STATUSES
        ),
        None,
    )


def _mark_operation(
    project_id: str,
    operation_id: str,
    *,
    kind: OperationKind | None = None,
    write_intent: DraftWriteIntent = "runtime",
    execution_fence: ExecutionFence | None = None,
    observed_at_ms: int | None = None,
    operation_command: (
        video_localization_operation_ledger_store.OperationCommand | None
    ) = None,
    **updates,
) -> None:
    normalized_updates = _normalize_terminal_operation_updates(updates)
    service.update_video_localization_atomic(
        project_id,
        lambda draft: operation_state.with_operation_updates(
            draft,
            operation_id,
            normalized_updates,
            kind=kind,
        ),
        intent=write_intent,
        execution_fence=execution_fence,
        observed_at_ms=observed_at_ms,
        operation_command=operation_command,
    )


def _mark_kind_failed(
    project_id: str,
    kind: OperationKind,
    code: str,
    message: str,
    *,
    execution_fence: ExecutionFence | None = None,
    observed_at_ms: int | None = None,
) -> None:
    service.update_video_localization_atomic(
        project_id,
        lambda draft: operation_state.with_kind_status(
            draft,
            kind,
            "failed",
            error_code=code,
            error_message=message,
        ),
        intent="runtime",
        execution_fence=execution_fence,
        observed_at_ms=observed_at_ms,
    )
