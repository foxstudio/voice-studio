"""Deleting a workflow cannot delete media adopted from a different take."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.video_localization import draft_store, service
from app.domains.video_localization.schemas import VideoLocalizationDraft


@pytest.mark.parametrize("media", [
    {"task_id": "new-task", "generation_id": "new-task", "result_id": "new-result"},
    {"result_id": "new-result"},
    # An old runtime marker or task mirror is not ownership of a new result.
    {"task_id": "old-task", "result_id": "new-result", "optimistic_tts_workflow_id": "old-workflow"},
])
def test_delete_old_workflow_preserves_replacement_media(monkeypatch, media):
    _exercise_delete(monkeypatch, media, survives=True)


@pytest.mark.parametrize("media", [
    {"task_id": "old-task", "generation_id": "old-task", "result_id": "old-result"},
    {"result_id": "old-result"},
    {"task_id": "old-task", "clip_id": "split-child"},
    {"optimistic_tts_workflow_id": "old-workflow"},
])
def test_delete_workflow_removes_its_own_media_and_slices(monkeypatch, media):
    _exercise_delete(monkeypatch, media, survives=False)


def _exercise_delete(monkeypatch, media, *, survives):
    clip = {"clip_id": "stable-slot", "track_id": "dub", "start_ms": 0,
            "end_ms": 1000, "source_start_ms": 0, "source_end_ms": 1000,
            "status": "ready", **media}
    neighbor = {**clip, "clip_id": "neighbor", "task_id": "neighbor-task",
                "generation_id": "neighbor-task", "result_id": "neighbor-result"}
    state = VideoLocalizationDraft(timeline_clips=[clip, neighbor], tts_tasks=[{
        "workflow_id": "old-workflow", "project_id": "isolated", "segment_id": "cue",
        "subtitle_summary": "测试", "text": "测试", "start_ms": 0, "end_ms": 1000,
        "status": "success", "generation_task_id": "old-task", "result_id": "old-result",
        "timeline_clip_id": "stable-slot",
    }])
    monkeypatch.setattr(draft_store, "get", lambda project_id: state)
    monkeypatch.setattr(draft_store, "save", lambda project_id, draft, **kwargs: draft)
    result = service.delete_tts_task("isolated", "old-workflow")
    assert result.tts_tasks == []
    assert result.timeline_clips == ([clip, neighbor] if survives else [neighbor])
    assert {"old-workflow", "old-task"} <= set(result.ui_state["discarded_tts_task_ids"])
