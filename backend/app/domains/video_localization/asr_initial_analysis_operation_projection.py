"""Public projections for the managed initial-analysis breakpoint."""

from __future__ import annotations

from typing import Any

from app.domains.video_localization import (
    asr_pipeline,
    asr_raw_operation_projection,
    workflow_contracts,
)


def initial_summary() -> dict[str, Any]:
    definition = (
        workflow_contracts
        .asr_initial_analysis_development_workflow_summary()
    )
    tasks = {
        task["id"]: task
        for task in definition["stages"][0]["atomic_tasks"]
    }
    return {
        "stage": "准备初始语音分析",
        "stage_id": "initial_analysis",
        "execution_scope": "partial",
        "parallel": True,
        "workflow_schema_version": (
            definition["schema_version"]
        ),
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
        "task_step_results": {
            step_id: {
                "label": task["label"],
                "order": task["order"],
                "status": "todo",
                "purpose": task["description"],
                "summary": (
                    "等待两条分支完成。"
                    if step_id == "initial_analysis_join"
                    else "等待锁定实际音轨并开始处理。"
                ),
                "metrics": [],
                "sections": [],
                "notes": [],
            }
            for step_id, task in tasks.items()
        },
    }


def running_summary() -> dict[str, Any]:
    summary = initial_summary()
    summary["stage"] = (
        "正在并行生成原始听写并区分说话人"
    )
    for step_id in ("asr", "diarization"):
        summary["task_step_results"][step_id].update(
            {
                "status": "running",
                "summary": "正在处理同一份已锁定音轨。",
            }
        )
    return summary


def success_summary(
    snapshot: asr_pipeline.AsrInitialAnalysisSnapshot,
    *,
    seed: str,
    wall_duration_ms: int,
    join_duration_ms: int = 0,
) -> dict[str, Any]:
    raw = snapshot.analysis.raw_asr
    diarization = snapshot.analysis.diarization
    joined = snapshot.joined_transcript
    matched = [
        segment
        for segment in joined.segments
        if segment.speaker_cluster_id
    ]
    warnings = list(joined.warnings)
    raw_quality = raw.quality_summary
    raw_status = (
        "success"
        if raw_quality is None
        or raw_quality.status == "passed"
        else "warning"
    )
    diarization_status = (
        "warning"
        if diarization is None
        or diarization.status == "partial"
        or snapshot.analysis.diarization_error
        else "success"
    )
    join_status = (
        "warning"
        if warnings
        or (
            joined.speaker_grouping_applied
            and len(matched) != len(joined.segments)
        )
        else "success"
    )
    raw_duration_ms = int(
        raw.stage_timing.get("duration_ms") or 0
    )
    diarization_duration_ms = (
        int(
            diarization.stage_timing.get("duration_ms")
            or 0
        )
        if diarization is not None
        else 0
    )
    normalized_wall_ms = max(
        int(wall_duration_ms),
        raw_duration_ms,
        diarization_duration_ms,
    )
    normalized_join_duration_ms = max(0, int(join_duration_ms))
    sample = {
        "raw_asr": (
            asr_raw_operation_projection
            .partial_asr_sample(raw, seed=seed)
        ),
        "diarization": (
            {
                "speaker_count": len(diarization.clusters),
                "segment_count": len(diarization.segments),
                "clusters": [
                    {
                        "cluster_id": cluster.cluster_id,
                        "start_ms": cluster.start_ms,
                        "end_ms": cluster.end_ms,
                        "segment_count": cluster.segment_count,
                        "merge_status": cluster.merge_status,
                    }
                    for cluster in diarization.clusters[:8]
                ],
                "segments": [
                    {
                        "start_ms": segment.start_ms,
                        "end_ms": segment.end_ms,
                        "speaker_cluster_id": (
                            segment.speaker_cluster_id
                        ),
                    }
                    for segment in diarization.segments[:8]
                ],
                "quality_summary": (
                    diarization.quality_summary.model_dump(
                        mode="json"
                    )
                ),
            }
            if diarization is not None
            else None
        ),
    }
    summary = initial_summary()
    summary.update(
        {
            "stage": (
                "原始听写与说话人区分已完成"
                "（并行开发断点）"
            ),
            "task_duration_ms": normalized_wall_ms,
            "branch_duration_ms": {
                "raw_asr": raw_duration_ms,
                "diarization": diarization_duration_ms,
                "wall": normalized_wall_ms,
            },
            "task_stage_timings": {
                "asr": {"duration_ms": raw_duration_ms},
                "diarization": {
                    "duration_ms": diarization_duration_ms
                },
                "initial_analysis_join": {
                    "duration_ms": normalized_join_duration_ms
                },
            },
            "raw_asr_segment_count": len(raw.segments),
            "diarization_segment_count": (
                len(diarization.segments)
                if diarization is not None
                else 0
            ),
            "speaker_count": (
                len(diarization.clusters)
                if diarization is not None
                else 0
            ),
            "speaker_grouping_applied": (
                joined.speaker_grouping_applied
            ),
            "diarization_status": (
                diarization.status
                if diarization is not None
                else "failed"
            ),
            "quality_summary": {
                "raw_asr": (
                    raw_quality.model_dump(mode="json")
                    if raw_quality is not None
                    else None
                ),
                "diarization": (
                    diarization.quality_summary.model_dump(
                        mode="json"
                    )
                    if diarization is not None
                    else None
                ),
                "warnings": warnings,
            },
            "sample_schema_version": (
                "initial-analysis-sample-v1"
            ),
            "sample": sample,
        }
    )
    summary["task_step_results"] = {
        "asr": {
            **summary["task_step_results"]["asr"],
            "status": raw_status,
            "summary": (
                f"已生成 {len(raw.segments)} 个原始语音片段。"
            ),
            "metrics": [
                {
                    "label": "原始片段",
                    "value": str(len(raw.segments)),
                },
                {
                    "label": "文字字符",
                    "value": str(len(raw.raw_text)),
                },
            ],
            "notes": (
                list(raw_quality.warning_codes)
                if raw_quality is not None
                else []
            ),
        },
        "diarization": {
            **summary["task_step_results"]["diarization"],
            "status": diarization_status,
            "summary": (
                f"已区分 {len(diarization.clusters)} 位匿名说话人。"
                if diarization is not None
                else "说话人区分失败，已明确降级并保留原始听写。"
            ),
            "metrics": [
                {
                    "label": "匿名说话人",
                    "value": str(
                        len(diarization.clusters)
                        if diarization is not None
                        else 0
                    ),
                },
                {
                    "label": "讲话片段",
                    "value": str(
                        len(diarization.segments)
                        if diarization is not None
                        else 0
                    ),
                },
            ],
            "notes": (
                [snapshot.analysis.diarization_error]
                if snapshot.analysis.diarization_error
                else []
            ),
        },
        "initial_analysis_join": {
            **summary["task_step_results"][
                "initial_analysis_join"
            ],
            "status": join_status,
            "summary": (
                (
                    f"已汇合 {len(joined.segments)} 个听写片段，"
                    f"其中 {len(matched)} 个已匹配匿名说话人。"
                )
                if joined.speaker_grouping_applied
                else (
                    f"已汇合 {len(joined.segments)} 个听写片段；"
                    "音频没有检测到多个稳定声音角色，无需添加说话人标签。"
                )
            ),
            "metrics": [
                {
                    "label": "汇合片段",
                    "value": str(len(joined.segments)),
                },
                {
                    "label": "已匹配",
                    "value": str(len(matched)),
                },
                {
                    "label": "已启用说话人标签",
                    "value": (
                        "是"
                        if joined.speaker_grouping_applied
                        else "否"
                    ),
                },
            ],
            "notes": warnings,
        },
    }
    return summary


__all__ = [
    "initial_summary",
    "running_summary",
    "success_summary",
]
