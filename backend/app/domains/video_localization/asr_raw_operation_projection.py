"""Path-free public projections for managed raw-ASR development runs."""

from __future__ import annotations

import math
import random
from typing import Any

from app.domains.video_localization import (
    transcription,
    workflow_contracts,
)
from app.schemas.video_localization_asr_raw_step import (
    ASR_RAW_STEP_ID,
    AsrRawResultV2,
    AsrRawStepOutputV1,
)


_SAMPLE_RATIO = 0.10
_SAMPLE_MIN = 3
_SAMPLE_MAX = 20


def initial_summary() -> dict[str, Any]:
    definition = (
        workflow_contracts
        .asr_raw_development_workflow_summary()
    )
    task = definition["stages"][0]["atomic_tasks"][0]
    return {
        "stage": "准备生成原始听写",
        "stage_id": ASR_RAW_STEP_ID,
        "execution_scope": "partial",
        "workflow_schema_version": (
            definition["schema_version"]
        ),
        "workflow_id": definition["workflow_id"],
        "task_stage_groups": definition["stages"],
        "task_step_results": {
            ASR_RAW_STEP_ID: {
                "label": task["label"],
                "order": task["order"],
                "status": "todo",
                "purpose": task["description"],
                "summary": "等待锁定实际音轨并开始听写。",
                "metrics": [],
                "sections": [],
                "notes": [],
            }
        },
    }


def running_summary() -> dict[str, Any]:
    summary = initial_summary()
    summary["stage"] = "正在生成原始听写"
    summary["task_step_results"][ASR_RAW_STEP_ID].update(
        {
            "status": "running",
            "summary": (
                "正在把当前音轨转换为未经校对的文字与粗时间片段。"
            ),
        }
    )
    return summary


def success_summary(
    output: AsrRawStepOutputV1,
    *,
    seed: str,
) -> dict[str, Any]:
    result = output.result
    quality = result.quality_summary
    step_status = (
        "success"
        if quality.status == "passed"
        else "warning"
    )
    summary = initial_summary()
    summary.update(
        {
            "stage": "原始听写已完成（开发断点）",
            "sample_schema_version": "asr-sample-v2",
            "sample": partial_asr_sample(
                result,
                seed=seed,
            ),
            "task_duration_ms": (
                result.stage_timing.duration_ms
            ),
            "segment_count": len(result.segments),
            "language": result.language,
            "quality_summary": quality.model_dump(
                mode="json"
            ),
        }
    )
    summary["task_step_results"][ASR_RAW_STEP_ID].update(
        {
            "status": step_status,
            "summary": (
                f"已生成 {len(result.segments)} 个原始语音片段，"
                f"共 {len(result.raw_text)} 个字符。"
            ),
            "metrics": [
                {
                    "label": "原始片段",
                    "value": str(len(result.segments)),
                },
                {
                    "label": "文字字符",
                    "value": str(len(result.raw_text)),
                },
                {
                    "label": "未完成区间",
                    "value": str(
                        len(
                            result.incomplete_chunk_ranges
                        )
                    ),
                },
            ],
            "notes": list(quality.warning_codes),
        }
    )
    return summary


def partial_asr_sample(
    result: (
        transcription.TranscribeRawOutput
        | AsrRawResultV2
    ),
    *,
    seed: str,
) -> dict[str, Any]:
    total_count = len(result.segments)
    sample_count = min(
        total_count,
        max(
            _SAMPLE_MIN,
            min(
                _SAMPLE_MAX,
                math.ceil(total_count * _SAMPLE_RATIO),
            ),
        ),
    )
    rng = random.Random(seed)
    sample_indices: list[int] = []
    for bucket_index in range(sample_count):
        bucket_start = (
            bucket_index * total_count // sample_count
        )
        bucket_end = (
            (bucket_index + 1)
            * total_count
            // sample_count
        )
        sample_indices.append(
            rng.randrange(
                bucket_start,
                max(bucket_start + 1, bucket_end),
            )
        )
    return {
        "raw_text": result.raw_text[:800],
        "language": result.language,
        "segment_count": total_count,
        "sample_count": sample_count,
        "sample_ratio": (
            sample_count / total_count
            if total_count
            else 0
        ),
        "sampling_mode": (
            "deterministic_stratified_random"
        ),
        "segments": [
            {
                "segment_id": segment.segment_id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": segment.raw_text,
            }
            for segment_index in sample_indices
            for segment in [result.segments[segment_index]]
        ],
        "quality_summary": (
            result.quality_summary.model_dump(mode="json")
            if result.quality_summary is not None
            else None
        ),
    }


__all__ = [
    "initial_summary",
    "partial_asr_sample",
    "running_summary",
    "success_summary",
]
