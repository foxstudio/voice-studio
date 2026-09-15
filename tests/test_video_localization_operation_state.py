from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import operation_state  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)


def _draft_with_operation(
    *,
    status: str = "running",
    cancel_requested: bool = False,
    result_summary: dict | None = None,
) -> VideoLocalizationDraft:
    return VideoLocalizationDraft(
        operations=[
            VideoLocalizationOperation(
                operation_id="operation-1",
                project_id="project-1",
                kind="english_asr",
                status=status,
                cancel_requested=cancel_requested,
                result_summary=result_summary or {},
            )
        ]
    )


def test_active_progress_cannot_revive_cancelled_operation():
    draft = _draft_with_operation(
        status="cancelled",
        cancel_requested=True,
        result_summary={"stage": "已取消"},
    )

    updated = operation_state.with_operation_updates(
        draft,
        "operation-1",
        {
            "status": "running",
            "progress": 0.8,
            "result_summary": {"stage": "迟到的进度"},
        },
        kind="english_asr",
    )

    assert updated.operations[0] == draft.operations[0]
    assert "english_asr_status" not in updated.source_media.metadata


def test_active_progress_merges_steps_and_preserves_atomic_timing():
    draft = _draft_with_operation(
        result_summary={
            "stage": "第一步",
            "task_step_results": {
                "asr": {"status": "success"},
            },
            "task_stage_timings": {
                "asr": {"duration_ms": 120, "atomic": True},
                "analysis": {"duration_ms": 20, "running": True},
            },
        }
    )

    updated = operation_state.with_operation_updates(
        draft,
        "operation-1",
        {
            "status": "running",
            "progress": 0.6,
            "result_summary": {
                "stage": "第二步",
                "task_step_results": {
                    "analysis": {"status": "running"},
                },
                "task_stage_timings": {
                    "asr": {"duration_ms": 999, "running": True},
                    "analysis": {"duration_ms": 40, "running": True},
                },
            },
        },
        kind="english_asr",
    )

    operation = updated.operations[0]
    assert operation.progress == 0.6
    assert operation.result_summary["stage"] == "第二步"
    assert operation.result_summary["task_step_results"] == {
        "asr": {"status": "success"},
        "analysis": {"status": "running"},
    }
    assert operation.result_summary["task_stage_timings"] == {
        "asr": {"duration_ms": 120, "atomic": True},
        "analysis": {"duration_ms": 40, "running": True},
    }
    assert updated.source_media.metadata["english_asr_status"] == "running"


def test_active_progress_preserves_step_contract_fields_when_status_changes():
    draft = _draft_with_operation(
        result_summary={
            "task_step_results": {
                "diarization": {
                    "label": "区分匿名说话人",
                    "order": 20,
                    "status": "todo",
                    "purpose": "区分同一音轨中的稳定声音角色。",
                },
            },
        }
    )

    updated = operation_state.with_operation_updates(
        draft,
        "operation-1",
        {
            "status": "running",
            "result_summary": {
                "task_step_results": {
                    "diarization": {
                        "status": "running",
                        "summary": "正在分析声音角色。",
                    },
                },
            },
        },
        kind="english_asr",
    )

    assert updated.operations[0].result_summary["task_step_results"][
        "diarization"
    ] == {
        "label": "区分匿名说话人",
        "order": 20,
        "status": "running",
        "purpose": "区分同一音轨中的稳定声音角色。",
        "summary": "正在分析声音角色。",
    }


def test_terminal_transition_uses_final_summary_and_kind_status():
    draft = _draft_with_operation(
        result_summary={
            "stage": "处理中",
            "temporary": True,
        }
    )

    updated = operation_state.with_operation_updates(
        draft,
        "operation-1",
        {
            "status": "failed",
            "progress": 1.0,
            "error_code": "ASR_FAILED",
            "error_message": "识别失败",
            "result_summary": {"stage": "失败"},
        },
        kind="english_asr",
    )

    operation = updated.operations[0]
    assert operation.status == "failed"
    assert operation.result_summary == {"stage": "失败"}
    assert updated.source_media.metadata == {
        "english_asr_status": "failed",
    }


def test_missing_operation_does_not_change_kind_status():
    draft = VideoLocalizationDraft()

    updated = operation_state.with_operation_updates(
        draft,
        "missing",
        {"status": "running"},
        kind="english_asr",
    )

    assert updated == draft
