from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api import video_localization as video_localization_api  # noqa: E402
from app.domains.video_localization import localization  # noqa: E402
from app.domains.video_localization import operation_queue  # noqa: E402
from app.domains.video_localization import service  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationCue,
    VideoLocalizationCueUpdate,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationSubtitleCue,
)
from app.errors import AppException  # noqa: E402


PROJECT_ID = "project_concurrency"


@pytest.fixture(autouse=True)
def clear_operation_runtime_state():
    with operation_queue._lock:
        operation_queue._cancelled_operation_ids.clear()
        operation_queue._operation_commit_gates.clear()
    yield
    with operation_queue._lock:
        operation_queue._cancelled_operation_ids.clear()
        operation_queue._operation_commit_gates.clear()


def _operation(status: str = "running") -> VideoLocalizationOperation:
    return VideoLocalizationOperation(
        operation_id="localization_operation",
        project_id=PROJECT_ID,
        kind="localization_draft",
        status=status,
    )


def _draft(operation: VideoLocalizationOperation | None = None) -> VideoLocalizationDraft:
    return VideoLocalizationDraft(
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                start_ms=0,
                end_ms=1200,
                en_subtitle_text="Original text",
                zh_localized_subtitle_text="当前镜像",
                tts_recommended_text="当前口播",
            )
        ],
        localized_subtitles=[
            VideoLocalizationSubtitleCue(
                subtitle_id="localized_0001",
                start_ms=0,
                end_ms=1200,
                text="当前目标轨",
                source_cue_ids=["cue_0001"],
            )
        ],
        localization_state={"owner": "backend"},
        operations=[operation] if operation is not None else [],
    )


def _install_memory_store(monkeypatch, initial: VideoLocalizationDraft, *, on_save=None):
    state = {"draft": initial}

    monkeypatch.setattr(service.project_store, "get_project", lambda project_id: object() if project_id == PROJECT_ID else None)
    monkeypatch.setattr(service.draft_store, "get", lambda project_id: state["draft"] if project_id == PROJECT_ID else None)

    def save(project_id, draft, **_kwargs):
        assert project_id == PROJECT_ID
        state["draft"] = draft
        if on_save is not None:
            on_save(draft)
        return draft

    monkeypatch.setattr(service.draft_store, "save", save)

    def find_operation(operation_id):
        operation = next(
            (item for item in state["draft"].operations if item.operation_id == operation_id),
            None,
        )
        return (PROJECT_ID, operation) if operation is not None else (None, None)

    monkeypatch.setattr(operation_queue, "_find_operation", find_operation)
    return state


def _generated_run(snapshot: VideoLocalizationDraft) -> localization.LocalizationRun:
    generated = snapshot.model_copy(
        update={
            "cues": [
                snapshot.cues[0].model_copy(
                    update={
                        "zh_localized_subtitle_text": "已提交镜像",
                        "tts_recommended_text": "已提交口播",
                    }
                )
            ],
            "localized_subtitles": [
                VideoLocalizationSubtitleCue(
                    subtitle_id="localized_generated",
                    start_ms=0,
                    end_ms=1200,
                    text="已提交目标轨",
                    source_cue_ids=["cue_0001"],
                )
            ],
            "localization_state": {"owner": "generated"},
        }
    )
    return localization.LocalizationRun(draft=generated, summary={})


def _start_process(operation_id: str):
    errors = []

    def run():
        try:
            operation_queue._process(operation_id)
        except BaseException as exc:  # pragma: no cover - asserted by the parent thread
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def test_target_track_mutations_cannot_bypass_active_localization_lock(monkeypatch):
    current = _draft(_operation())
    _install_memory_store(monkeypatch, current)

    with pytest.raises(AppException) as exc_info:
        service.update_cue(
            PROJECT_ID,
            "cue_0001",
            VideoLocalizationCueUpdate(
                zh_localized_subtitle_text="绕过镜像",
                tts_recommended_text="绕过口播",
            ),
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_TRACK_BUSY"

    forged_operation = current.operations[0].model_copy(update={"status": "success"})
    incoming = current.model_copy(
        update={
            "cues": [
                current.cues[0].model_copy(
                    update={
                        "en_subtitle_text": "Edited source text",
                        "zh_localized_subtitle_text": "绕过镜像",
                        "tts_recommended_text": "绕过口播",
                    }
                )
            ],
            "localized_subtitles": [
                current.localized_subtitles[0].model_copy(update={"text": "绕过目标轨"})
            ],
            "localization_state": {"owner": "client"},
            "operations": [forged_operation],
        }
    )

    saved = service.replace_video_localization_from_client(PROJECT_ID, incoming)

    assert saved is not None
    assert saved.cues[0].en_subtitle_text == "Edited source text"
    assert saved.cues[0].zh_localized_subtitle_text == "当前镜像"
    assert saved.cues[0].tts_recommended_text == "当前口播"
    assert saved.localized_subtitles[0].text == "当前目标轨"
    assert saved.localization_state == {"owner": "backend"}
    assert saved.operations[0].status == "running"


def test_legacy_localize_endpoint_reuses_active_queue_operation(monkeypatch):
    active = _operation()
    state = _install_memory_store(monkeypatch, _draft(active))
    enqueued: list[str] = []
    monkeypatch.setattr(operation_queue.operation_state, "validate_prerequisites", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operation_queue, "_enqueue", enqueued.append)
    def fail_sync_generation(*_args, **_kwargs):
        raise AssertionError("legacy endpoint ran synchronously")

    monkeypatch.setattr(service, "generate_localization_draft", fail_sync_generation)

    returned = asyncio.run(video_localization_api.generate_video_localization_chinese_draft(PROJECT_ID))

    assert returned.operation_id == active.operation_id
    assert returned.status == "running"
    assert enqueued == [active.operation_id]
    assert len(state["draft"].operations) == 1


def test_cancel_wins_before_final_commit_and_generated_track_is_not_saved(monkeypatch):
    operation = _operation()
    state = _install_memory_store(monkeypatch, _draft(operation))
    generated = threading.Event()
    allow_return = threading.Event()

    def generate(snapshot, **_kwargs):
        generated.set()
        assert allow_return.wait(1)
        return _generated_run(snapshot)

    monkeypatch.setattr(localization, "generate_localization_draft", generate)
    thread, errors = _start_process(operation.operation_id)
    assert generated.wait(1)

    cancelled = operation_queue.cancel(PROJECT_ID, operation.operation_id)
    assert cancelled is not None and cancelled.status == "cancelled"
    allow_return.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert not errors
    saved = state["draft"]
    assert saved.operations[0].status == "cancelled"
    assert saved.cues[0].zh_localized_subtitle_text == "当前镜像"
    assert saved.localized_subtitles[0].text == "当前目标轨"


def test_final_commit_wins_before_cancel_and_operation_stays_success(monkeypatch):
    operation = _operation()
    commit_started = threading.Event()
    release_commit = threading.Event()
    commit_was_blocked = False

    def on_save(draft):
        nonlocal commit_was_blocked
        if draft.localized_subtitles[0].text != "已提交目标轨" or commit_was_blocked:
            return
        commit_was_blocked = True
        commit_started.set()
        assert release_commit.wait(1)

    state = _install_memory_store(monkeypatch, _draft(operation), on_save=on_save)
    monkeypatch.setattr(localization, "generate_localization_draft", lambda snapshot, **_kwargs: _generated_run(snapshot))

    process_thread, process_errors = _start_process(operation.operation_id)
    assert commit_started.wait(1)

    cancel_started = threading.Event()
    cancel_result = []

    def cancel():
        cancel_started.set()
        cancel_result.append(operation_queue.cancel(PROJECT_ID, operation.operation_id))

    cancel_thread = threading.Thread(target=cancel)
    cancel_thread.start()
    assert cancel_started.wait(1)
    assert cancel_thread.is_alive()

    release_commit.set()
    process_thread.join(timeout=1)
    cancel_thread.join(timeout=1)

    assert not process_thread.is_alive()
    assert not cancel_thread.is_alive()
    assert not process_errors
    assert cancel_result and cancel_result[0] is not None
    assert cancel_result[0].status != "cancelled"
    saved = state["draft"]
    assert saved.operations[0].status == "success"
    assert saved.cues[0].zh_localized_subtitle_text == "已提交镜像"
    assert saved.localized_subtitles[0].text == "已提交目标轨"
