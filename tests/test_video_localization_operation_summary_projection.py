from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    operation_summary_projection,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationOperation,
)


def _operation(
    *,
    status: str = "running",
) -> VideoLocalizationOperation:
    return VideoLocalizationOperation.model_validate(
        {
            "operation_id": "operation-1",
            "project_id": "project-1",
            "kind": "english_asr",
            "status": status,
            "label": "听写字幕",
            "progress": 0.5 if status == "running" else 1.0,
            "cancel_requested": False,
            "created_at": "2026-07-30T10:00:00+00:00",
            "started_at": "2026-07-30T10:00:01+00:00",
            "completed_at": (
                None
                if status == "running"
                else "2026-07-30T10:01:00+00:00"
            ),
            "parameters": {
                "engine_id": "fixed",
                "execution_mode": "formal",
                "private_path": "/private/project/input.wav",
                "large_private_payload": "not-for-feed",
            },
            "result_summary": {
                "stage": "正在听写",
                "cue_count": 12,
                "error_detail": {
                    "message": "固定错误",
                    "debug_path": "/private/project/debug.json",
                },
                "preview_cues": [{"cue_id": "cue-1", "text": "Hello"}],
                "task_step_results": {
                    "asr": {
                        "label": "生成原始听写",
                        "status": "running",
                        "summary": "处理中",
                        "artifact_path": "/private/project/result.json",
                        "sections": [
                            {
                                "title": f"section-{section}",
                                "items": [
                                    {
                                        "title": f"item-{item}",
                                        "snapshot_path": (
                                            "/private/project/snapshot.json"
                                        ),
                                    }
                                    for item in range(10)
                                ],
                            }
                            for section in range(5)
                        ],
                    }
                },
                "task_final_result": {
                    "status": "running",
                    "summary": "处理中",
                    "sections": [],
                },
                "full_private_result": {"secret": True},
            },
        }
    )


def test_projector_keeps_active_summary_bounded_and_path_free():
    summary = (
        operation_summary_projection.project_operation_summary(
            _operation(),
        )
    )

    assert summary.parameters == {
        "engine_id": "fixed",
        "execution_mode": "formal",
    }
    assert summary.result_summary["preview_cues"] == [
        {"cue_id": "cue-1", "text": "Hello"}
    ]
    step = summary.result_summary["task_step_results"]["asr"]
    assert len(step["sections"]) == 3
    assert all(
        len(section["items"]) == 8
        for section in step["sections"]
    )
    assert step["coverage"] == {
        "mode": "focused",
        "shown_count": 24,
        "total_count": 50,
        "unit": "项",
        "reason": (
            "任务轮询摘要为控制体积仅返回部分明细，"
            "完整结果仍保存在任务记录中。"
        ),
        "truncated": True,
    }
    encoded = summary.model_dump_json()
    assert "/private/project" not in encoded
    assert "artifact_path" not in encoded
    assert "snapshot_path" not in encoded
    assert "full_private_result" not in encoded


def test_projector_removes_terminal_detail_but_keeps_list_facts():
    summary = (
        operation_summary_projection.project_operation_summary(
            _operation(status="success"),
            result_summary_seed={
                "duration_ms": 60_000,
                "media_status": "available",
            },
            stage_override="任务已完成",
            artifact_available=True,
        )
    )

    assert summary.result_summary == {
        "duration_ms": 60_000,
        "media_status": "available",
        "stage": "任务已完成",
        "cue_count": 12,
        "artifact_available": True,
    }
    assert "task_step_results" not in summary.result_summary
    assert "task_final_result" not in summary.result_summary
    assert "error_detail" not in summary.result_summary


def test_summary_core_round_trip_uses_ledger_owned_fields():
    summary = (
        operation_summary_projection.project_operation_summary(
            _operation(status="success"),
        )
    )
    core = operation_summary_projection.operation_summary_core(
        summary
    )

    assert core.summary_schema_version == (
        "operation-summary-core-v1"
    )
    assert "kind" not in core.model_fields_set
    assert not hasattr(core, "status")
    assert not hasattr(core, "created_at")

    rebuilt = (
        operation_summary_projection.assemble_operation_summary(
            core,
            kind=summary.kind,
            status=summary.status,
            cancel_requested=summary.cancel_requested,
            created_at=summary.created_at,
            completed_at=summary.completed_at,
        )
    )

    assert rebuilt == summary


def test_summary_core_fingerprint_is_canonical_and_sensitive():
    summary = (
        operation_summary_projection.project_operation_summary(
            _operation(),
        )
    )
    core = operation_summary_projection.operation_summary_core(
        summary
    )
    reordered = core.model_copy(
        update={
            "parameters": {
                "execution_mode": "formal",
                "engine_id": "fixed",
            }
        }
    )
    changed = core.model_copy(update={"progress": 0.6})

    assert (
        operation_summary_projection
        .operation_summary_core_fingerprint(core)
        == operation_summary_projection
        .operation_summary_core_fingerprint(reordered)
    )
    assert (
        operation_summary_projection
        .operation_summary_core_fingerprint(core)
        != operation_summary_projection
        .operation_summary_core_fingerprint(changed)
    )


def test_summary_core_rejects_unknown_storage_fields():
    summary = (
        operation_summary_projection.project_operation_summary(
            _operation(),
        )
    )
    payload = (
        operation_summary_projection.operation_summary_core(
            summary
        ).model_dump(mode="json")
    )
    payload["unexpected"] = "not-versioned"

    with pytest.raises(ValidationError):
        operation_summary_projection.OperationSummaryCoreV1.model_validate(
            payload
        )
