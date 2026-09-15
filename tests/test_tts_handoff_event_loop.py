import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import GenerationTask, TaskStatus
from app.services import task_queue, video_localization_dubbing_executor


@pytest.mark.asyncio
@pytest.mark.parametrize("placement_succeeded", [True, False])
async def test_local_content_preflight_does_not_block_event_loop(monkeypatch, tmp_path, placement_succeeded):
    """Slow synchronous handoff work must leave API/heartbeat work runnable."""
    task = GenerationTask(
        task_id="manual-placement", generation_id="manual-placement", engine_id="omnivoice",
        project_id="project-1", segment_id="subtitle-1", bind_to_video_localization=True,
        status=TaskStatus.queued, input_text="测试台词",
        parameters={"text": "测试台词", "engine_id": "omnivoice", "source": "video_localization"},
    )
    output = tmp_path / "generated.wav"
    output.touch()
    history = SimpleNamespace(result_id="generated-result")
    heartbeat = threading.Event()
    event_loop_thread = threading.get_ident()
    observed = []

    async def update(current, **values):
        for key, value in values.items():
            setattr(current, key, value)

    def placement(current, saved):
        assert current is task and saved is history
        observed.append((threading.get_ident(), heartbeat.wait(timeout=0.5)))
        return placement_succeeded

    monkeypatch.setattr(task_queue, "_task_is_protected_by_state", lambda _: False)
    monkeypatch.setattr(task_queue, "_update_status", update)
    monkeypatch.setattr(task_queue.engine_registry, "ensure_loaded", lambda _: None)
    monkeypatch.setattr(task_queue, "_execute_engine", AsyncMock(return_value=({"output_path": str(output), "duration_ms": 1000}, {})))
    monkeypatch.setattr(task_queue, "_postprocess_audio", lambda *args: output)
    monkeypatch.setattr(task_queue, "_save_history", lambda *args: history)
    monkeypatch.setattr(task_queue, "_update_project_segment", lambda *args: None)
    monkeypatch.setattr(task_queue.video_localization_tts_handoff, "place_generated_result_with_retry", placement)
    monkeypatch.setattr(video_localization_dubbing_executor, "handle_completed_task", AsyncMock(return_value="not_managed"))
    timer = asyncio.get_running_loop().call_later(0.01, heartbeat.set)
    try:
        await task_queue._process(task)
    finally:
        timer.cancel()
    assert observed == [(observed[0][0], True)]
    assert observed[0][0] != event_loop_thread
    assert task.status == TaskStatus.success
    assert task.result_id == history.result_id
    assert bool(task.error_message) is (not placement_succeeded)
