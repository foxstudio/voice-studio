from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.video_localization_tts_handoff import (  # noqa: E402
    TtsTaskRegistrationEventV1,
)
from app.schemas.voice_studio import (  # noqa: E402
    GenerateRequest,
    GenerationTask,
    HistoryItem,
    LongformGenerateRequest,
    LongformTask,
    TaskStatus,
)
from app.services import database  # noqa: E402
from app.services import history_store  # noqa: E402
from app.services import video_localization_tts_handoff  # noqa: E402
from app.services import video_localization_operation_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_tts_handoff_store as handoff_store,
)


@pytest.fixture
def isolated_db(tmp_path):
    original = database.DB_PATH
    database.set_db_path(tmp_path / "voice_studio.db")
    try:
        with database.conn() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    project_id,
                    data,
                    updated_at,
                    repository_revision
                )
                VALUES (?, ?, ?, 0)
                """,
                (
                    "project-outbox",
                    "{}",
                    "2026-08-02T00:00:00",
                ),
            )
        yield
    finally:
        database.set_db_path(original)


def _projection(
    **overrides,
) -> video_localization_tts_handoff.VideoLocalizationTtsProjection:
    callbacks = {
        "finalize_submission": lambda request: request,
        "register_task": (lambda *_args, **_kwargs: SimpleNamespace()),
        "sync_result": (
            lambda *_args, **_kwargs: SimpleNamespace(
                tts_tasks=[],
                timeline_clips=[],
            )
        ),
        "mark_workflow_terminal": (lambda *_args, **_kwargs: SimpleNamespace()),
    }
    callbacks.update(overrides)
    return video_localization_tts_handoff.VideoLocalizationTtsProjection(**callbacks)


def _bound_task(
    *,
    task_id: str = "task-outbox",
) -> GenerationTask:
    return GenerationTask(
        task_id=task_id,
        generation_id=task_id,
        engine_id="indextts-v2",
        project_id="project-outbox",
        segment_id="localized-outbox",
        localized_subtitle_id="localized-outbox",
        cue_id="cue-outbox",
        bind_to_video_localization=True,
        input_text="测试",
        status=TaskStatus.success,
        parameters={
            "source": "video_localization",
            "generation_id": task_id,
            "video_localization_dubbing_plan_revision": 7,
            "video_localization_dubbing_group_id": "group-outbox",
            "video_localization_target_subtitle_ids": [
                "localized-outbox"
            ],
        },
    )


def _bound_history(
    *,
    task_id: str = "task-outbox",
    result_id: str = "result-outbox",
    output_path: str = "/tmp/result-outbox.wav",
) -> HistoryItem:
    return HistoryItem(
        result_id=result_id,
        task_id=task_id,
        generation_id=task_id,
        engine_id="indextts-v2",
        project_id="project-outbox",
        segment_id="localized-outbox",
        localized_subtitle_id="localized-outbox",
        cue_id="cue-outbox",
        bind_to_video_localization=True,
        input_text="测试",
        output_path=output_path,
    )


@pytest.mark.parametrize("transcript", [None, "", "English 测试"])
def test_manual_placement_does_not_require_content_approval(isolated_db, monkeypatch, transcript):
    from app.schemas.tts_content import CONTENT_ASR_ENGINE, TtsContentEvidence
    from app.services import tts_content_verification

    calls = []
    task, history = _bound_task(), _bound_history()
    if transcript is not None:
        history.content_evidence = TtsContentEvidence(
            audio_sha256="a" * 64, engine_id=CONTENT_ASR_ENGINE,
            status="complete" if transcript else "unavailable", transcript=transcript,
        )

    def sync(*args, **kwargs):
        calls.append(kwargs["result_id"])
        return SimpleNamespace(tts_tasks=[], timeline_clips=[{
            "track_id": "dub", "task_id": task.task_id, "audio_path": history.output_path,
        }])

    def no_asr(*args, **kwargs):
        raise AssertionError("placement must not start raw content ASR")

    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    service.configure(_projection(sync_result=sync))
    monkeypatch.setattr(tts_content_verification, "acquire_content_evidence", no_asr)
    service.persist_generated_history(task, history)
    assert service.place_generated_result_with_retry(task, history)
    assert service.place_generated_result_with_retry(task, history)
    assert service.replay_pending().inspected == 0
    assert handoff_store.pending_count() == 0
    assert calls == [history.result_id]
    assert history_store.get(history.result_id) is not None
    assert task.status == TaskStatus.success


@pytest.mark.parametrize("entrypoint", ["immediate", "replay"])
@pytest.mark.parametrize("code", [
    "VIDEO_LOCALIZATION_DUBBING_TASK_STALE",
    "VIDEO_LOCALIZATION_DUBBING_GROUP_STALE",
])
def test_obsolete_placement_retires_once_and_preserves_audio(
    isolated_db, tmp_path, monkeypatch, entrypoint, code,
):
    calls = []

    def obsolete(*args, **kwargs):
        calls.append(1)
        raise video_localization_tts_handoff.AppException(409, code, "旧计划已经失效")

    monkeypatch.setattr(video_localization_tts_handoff.time, "sleep", lambda _: None)
    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    service.configure(_projection(sync_result=obsolete))
    audio = tmp_path / "preserved.wav"
    audio.write_bytes(b"immutable generated audio")
    task, history = _bound_task(), _bound_history(output_path=str(audio))
    service.persist_generated_history(task, history)
    if entrypoint == "immediate":
        assert service.place_generated_result_with_retry(task, history) is False
    else:
        report = service.replay_pending()
        assert report.abandoned == 1
        assert report.applied == 0
    event = handoff_store.get(handoff_store.result_placement_event_id(task.task_id))
    assert event.status == "abandoned"
    assert code in event.last_error
    assert event.attempt_count == 1
    assert service.place_generated_result_with_retry(task, history) is False
    assert service.replay_pending().inspected == 0
    assert calls == [1]
    assert history_store.get(history.result_id) is not None
    assert audio.read_bytes() == b"immutable generated audio"
    assert task.status == TaskStatus.success


@pytest.mark.parametrize("error", [
    OSError("database temporarily unavailable"),
    video_localization_tts_handoff.AppException(409, "PROJECT_REVISION_CONFLICT", "retry with current revision"),
])
def test_retryable_projection_errors_remain_pending(isolated_db, error):
    def temporary(*args, **kwargs):
        raise error

    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    service.configure(_projection(sync_result=temporary))
    task, history = _bound_task(), _bound_history()
    service.persist_generated_history(task, history)
    report = service.replay_pending()
    event = handoff_store.get(handoff_store.result_placement_event_id(task.task_id))
    assert report.abandoned == 0
    assert event.status == "pending"
    assert event.attempt_count == 1


@pytest.mark.parametrize("entrypoint", ["immediate", "replay"])
def test_discarded_result_retires_without_restoring_deleted_clips(
    isolated_db, tmp_path, monkeypatch, entrypoint,
):
    from app.domains.video_localization import service as project_service
    from app.schemas.voice_studio import VideoLocalizationDraft

    task = _bound_task()
    task.parameters["video_localization_workflow_id"] = "deleted-workflow"
    audio = tmp_path / "deleted-take.wav"
    audio.write_bytes(b"preserved editable history")
    history = _bound_history(output_path=str(audio))
    discarded = VideoLocalizationDraft(
        ui_state={"discarded_tts_task_ids": [task.task_id]}, timeline_clips=[],
    )
    attempts = []

    def atomic(_project_id, apply, **_kwargs):
        attempts.append(1)
        return apply(discarded)

    def no_adoption(*_args, **_kwargs):
        raise AssertionError("a user-discarded task must not read or adopt media")

    monkeypatch.setattr(project_service.project_store, "get_project", lambda _: SimpleNamespace())
    monkeypatch.setattr(project_service.draft_store, "get", lambda _: discarded)
    monkeypatch.setattr(project_service, "update_video_localization_atomic", atomic)
    monkeypatch.setattr(project_service.media_assets, "adopt_tts_audio", no_adoption)
    monkeypatch.setattr(video_localization_tts_handoff.time, "sleep", lambda _: None)
    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    service.configure(_projection(sync_result=project_service.sync_single_tts_result))
    service.persist_generated_history(task, history)

    if entrypoint == "immediate":
        assert service.place_generated_result_with_retry(task, history) is False
    else:
        report = service.replay_pending()
        assert report.abandoned == 1
        assert report.applied == 0

    event = handoff_store.get(handoff_store.result_placement_event_id(task.task_id))
    assert event.status == "abandoned"
    assert event.attempt_count == 1
    assert "VIDEO_LOCALIZATION_TTS_RESULT_DISCARDED" in event.last_error
    assert attempts == [1]
    assert discarded.timeline_clips == []
    assert history_store.get(history.result_id) is not None
    assert audio.read_bytes() == b"preserved editable history"
    assert service.replay_pending().inspected == 0
    assert service.place_generated_result_with_retry(task, history) is False
    assert attempts == [1]


def test_history_and_result_event_rollback_together(
    isolated_db,
    monkeypatch,
):
    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    task = _bound_task()
    history = _bound_history()

    def reject_event(*_args, **_kwargs):
        raise OSError("outbox unavailable")

    monkeypatch.setattr(
        handoff_store,
        "enqueue",
        reject_event,
    )

    with pytest.raises(OSError, match="outbox unavailable"):
        service.persist_generated_history(task, history)

    assert history_store.get(history.result_id) is None


def test_failed_result_projection_replays_from_durable_event(
    isolated_db,
    monkeypatch,
):
    failing = video_localization_tts_handoff.TtsHandoffApplicationService()
    failing.configure(
        _projection(
            sync_result=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("project snapshot unavailable"))
        )
    )
    task = _bound_task()
    history = _bound_history()
    failing.persist_generated_history(task, history)
    monkeypatch.setattr(
        video_localization_tts_handoff.time,
        "sleep",
        lambda _seconds: None,
    )

    assert (
        failing.place_generated_result_with_retry(
            task,
            history,
        )
        is False
    )
    event_id = handoff_store.result_placement_event_id(task.task_id)
    pending = handoff_store.get(event_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.attempt_count == 1
    assert "project snapshot unavailable" in (pending.last_error or "")
    database.upsert(
        "tasks",
        task.task_id,
        task.model_copy(
            update={
                "error_message": (
                    "音频已生成并保存在配音记录中，但写回视频时间线失败。"
                ),
                "logs": [
                    "视频本土化 cue 回填失败（3/3）：project snapshot unavailable"
                ],
            }
        ).model_dump(),
    )

    replayed_lineage: dict[str, object] = {}

    def replay_result(*_args, **kwargs):
        replayed_lineage.update(kwargs)
        return SimpleNamespace(
            tts_tasks=[],
            timeline_clips=[
                {
                    "track_id": "dub",
                    "task_id": task.task_id,
                    "audio_path": history.output_path,
                }
            ],
        )

    recovered = video_localization_tts_handoff.TtsHandoffApplicationService()
    recovered.configure(
        _projection(
            sync_result=replay_result
        )
    )

    report = recovered.replay_pending(limit=10)

    assert report.inspected == 1
    assert report.applied == 1
    assert report.abandoned == 0
    assert report.remaining_pending == 0
    assert replayed_lineage["dubbing_plan_revision"] == 7
    assert replayed_lineage["dubbing_group_id"] == "group-outbox"
    assert replayed_lineage["dubbing_target_subtitle_ids"] == [
        "localized-outbox"
    ]
    applied = handoff_store.get(event_id)
    assert applied is not None
    assert applied.status == "applied"
    recovered_task = GenerationTask(
        **database.get_one("tasks", "task_id", task.task_id)
    )
    assert recovered_task.error_message is None
    assert recovered_task.logs == [
        "视频本土化时间线写回已由后台恢复"
    ]


def test_slow_result_projection_keeps_delivery_claim_alive(
    isolated_db,
    monkeypatch,
):
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "_DELIVERY_LEASE_DURATION_MS",
        1_000,
    )
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "_DELIVERY_HEARTBEAT_INTERVAL_SECONDS",
        0.02,
        raising=False,
    )
    heartbeat_seen = threading.Event()
    original_heartbeat_claim = handoff_store.heartbeat_claim

    def observed_heartbeat(*args, **kwargs):
        renewed = original_heartbeat_claim(*args, **kwargs)
        if renewed:
            heartbeat_seen.set()
        return renewed

    monkeypatch.setattr(
        handoff_store,
        "heartbeat_claim",
        observed_heartbeat,
    )
    service = video_localization_tts_handoff.TtsHandoffApplicationService(
        runner_id="runner-slow-placement",
    )
    task = _bound_task(task_id="task-slow-placement")
    history = _bound_history(
        task_id=task.task_id,
        result_id="result-slow-placement",
    )

    def slow_result(*_args, **kwargs):
        assert heartbeat_seen.wait(timeout=2.0)
        time.sleep(1.2)
        claim = kwargs["handoff_claim"]
        with database.conn() as connection:
            handoff_store.require_active_claim(
                connection,
                claim,
                target_project_id=task.project_id or "",
                observed_at_ms=time.time_ns() // 1_000_000,
            )
        return SimpleNamespace(
            tts_tasks=[],
            timeline_clips=[
                {
                    "track_id": "dub",
                    "task_id": task.task_id,
                    "audio_path": history.output_path,
                }
            ],
        )

    service.configure(_projection(sync_result=slow_result))
    service.persist_generated_history(task, history)

    assert service.place_generated_result_with_retry(task, history) is True
    stored = handoff_store.get(
        handoff_store.result_placement_event_id(task.task_id)
    )
    assert stored is not None
    assert stored.status == "applied"
    assert not any("expired" in entry for entry in task.logs)


def test_replay_abandons_poison_event_after_bounded_attempts(
    isolated_db,
    monkeypatch,
):
    monkeypatch.setattr(
        video_localization_tts_handoff,
        "_MAX_REPLAY_ATTEMPTS",
        2,
    )
    task = _bound_task(task_id="task-poison")
    history = _bound_history(
        task_id=task.task_id,
        result_id="result-poison",
    )
    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    service.configure(
        _projection(sync_result=lambda *_args, **_kwargs: None)
    )
    service.persist_generated_history(task, history)

    assert service.replay_pending(limit=10).applied == 0
    assert service.replay_pending(limit=10).applied == 0
    report = service.replay_pending(limit=10)

    stored = handoff_store.get(
        handoff_store.result_placement_event_id(task.task_id)
    )
    assert report.abandoned == 1
    assert stored is not None
    assert stored.status == "abandoned"
    assert "automatic replay limit" in (stored.last_error or "")


def test_abandoned_result_event_never_reprojects_after_reset(
    isolated_db,
):
    calls: list[str] = []
    service = (
        video_localization_tts_handoff
        .TtsHandoffApplicationService()
    )
    service.configure(
        _projection(
            sync_result=(
                lambda *_args, **_kwargs: calls.append(
                    "projected"
                )
            )
        )
    )
    task = _bound_task(task_id="task-reset-race")
    history = _bound_history(
        task_id=task.task_id,
        result_id="result-reset-race",
    )
    service.persist_generated_history(task, history)
    with database.conn() as connection:
        handoff_store.abandon_project(
            connection,
            task.project_id or "",
            abandoned_at="2026-08-02T00:00:01",
            reason="video-localization project was reset",
        )

    assert (
        service.place_generated_result_with_retry(
            task,
            history,
        )
        is False
    )
    assert calls == []
    assert any("退役" in item for item in task.logs)


def test_deleted_project_cannot_recreate_handoff_event(
    isolated_db,
):
    calls: list[str] = []
    service = (
        video_localization_tts_handoff
        .TtsHandoffApplicationService()
    )
    service.configure(
        _projection(
            sync_result=(
                lambda *_args, **_kwargs: calls.append(
                    "projected"
                )
            )
        )
    )
    task = _bound_task(task_id="task-delete-race")
    history = _bound_history(
        task_id=task.task_id,
        result_id="result-delete-race",
    )
    service.persist_generated_history(task, history)
    event_id = handoff_store.result_placement_event_id(
        task.task_id
    )
    with database.conn() as connection:
        handoff_store.delete_project(
            connection,
            task.project_id or "",
        )
        connection.execute(
            "DELETE FROM projects WHERE project_id = ?",
            (task.project_id,),
        )

    assert (
        service.place_generated_result_with_retry(
            task,
            history,
        )
        is False
    )
    assert calls == []
    assert handoff_store.get(event_id) is None
    assert any("项目已删除" in item for item in task.logs)


def test_registration_failure_compensates_source_and_event(
    isolated_db,
):
    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    service.configure(
        _projection(register_task=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("draft unavailable")))
    )
    task = _bound_task(task_id="task-registration-failed")

    with pytest.raises(
        RuntimeError,
        match="draft unavailable",
    ):
        service.persist_and_register_generation_task(
            task,
            workflow_id="workflow-registration",
        )

    assert (
        database.get_one(
            "tasks",
            "task_id",
            task.task_id,
        )
        is None
    )
    event = handoff_store.get(
        handoff_store.registration_event_id(
            source_kind="task",
            source_id=task.task_id,
        )
    )
    assert event is not None
    assert event.status == "abandoned"


def test_registration_event_replays_after_commit_crash_gap(
    isolated_db,
):
    task = _bound_task(task_id="task-registration-replay")
    payload = TtsTaskRegistrationEventV1(
        project_id=task.project_id or "",
        source_kind="task",
        source_id=task.task_id,
        segment_id=task.segment_id or "",
        generation_task_id=task.task_id,
        workflow_id="workflow-replay",
    )
    event_id = handoff_store.registration_event_id(
        source_kind=payload.source_kind,
        source_id=payload.source_id,
    )
    with database.conn() as connection:
        database.upsert_from_connection(
            connection,
            "tasks",
            task.task_id,
            task.model_dump(),
        )
        handoff_store.enqueue(
            connection,
            event_id=event_id,
            payload=payload,
            created_at=task.created_at,
        )
    calls: list[tuple[str, str, str, str | None]] = []
    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    service.configure(
        _projection(
            register_task=(
                lambda project_id, segment_id, task_id, workflow_id, **_kwargs: (
                    calls.append(
                        (
                            project_id,
                            segment_id,
                            task_id,
                            workflow_id,
                        )
                    )
                    or SimpleNamespace()
                )
            )
        )
    )

    report = service.replay_pending(limit=10)

    assert report.applied == 1
    assert calls == [
        (
            "project-outbox",
            "localized-outbox",
            task.task_id,
            "workflow-replay",
        )
    ]
    stored = handoff_store.get(event_id)
    assert stored is not None
    assert stored.status == "applied"


def test_registration_replay_abandons_missing_source_record(
    isolated_db,
):
    payload = TtsTaskRegistrationEventV1(
        project_id="project-outbox",
        source_kind="task",
        source_id="task-was-removed",
        segment_id="localized-outbox",
        generation_task_id="task-was-removed",
        workflow_id="workflow-missing-source",
    )
    event_id = handoff_store.registration_event_id(
        source_kind=payload.source_kind,
        source_id=payload.source_id,
    )
    with database.conn() as connection:
        handoff_store.enqueue(
            connection,
            event_id=event_id,
            payload=payload,
            created_at="2026-08-02T00:00:00",
        )
    calls: list[str] = []
    service = (
        video_localization_tts_handoff
        .TtsHandoffApplicationService()
    )
    service.configure(
        _projection(
            register_task=(
                lambda *_args, **_kwargs: calls.append(
                    "projected"
                )
            )
        )
    )

    report = service.replay_pending(limit=10)

    assert report.applied == 0
    assert report.abandoned == 1
    assert report.remaining_pending == 0
    assert calls == []
    stored = handoff_store.get(event_id)
    assert stored is not None
    assert stored.status == "abandoned"


def test_terminal_task_and_event_persist_and_replay(
    isolated_db,
):
    request = GenerateRequest(
        text="长段配音",
        engine_id="indextts-v2",
        source="video_localization",
        project_id="project-outbox",
        segment_id="localized-outbox",
        localized_subtitle_id="localized-outbox",
        cue_id="cue-outbox",
        bind_to_video_localization=True,
        video_localization_workflow_id="workflow-terminal",
    )
    task = LongformTask(
        longform_task_id="longform-terminal",
        engine_id="indextts-v2",
        input_text="长段配音",
        status=TaskStatus.failed,
        error_message="生成失败",
        parameters=LongformGenerateRequest(generate_request=request).model_dump(),
        completed_at="2026-08-02T00:00:00",
    )
    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    service.configure(
        _projection(
            mark_workflow_terminal=(lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("draft write unavailable")))
        )
    )

    assert service.persist_terminal_longform_task(task) is True
    assert (
        database.get_one(
            "longform_tasks",
            "longform_task_id",
            task.longform_task_id,
        )
        is not None
    )
    assert (
        service.mark_workflow_terminal(
            request,
            status="failed",
            error_message=task.error_message,
            source_id=task.longform_task_id,
        )
        is None
    )
    event_id = handoff_store.workflow_terminal_event_id(
        source_id=task.longform_task_id,
        workflow_id="workflow-terminal",
    )
    pending = handoff_store.get(event_id)
    assert pending is not None
    assert pending.status == "pending"

    recovered_calls: list[tuple[str, str, str]] = []
    recovered = video_localization_tts_handoff.TtsHandoffApplicationService()
    recovered.configure(
        _projection(
            mark_workflow_terminal=(
                lambda project_id, workflow_id, **kwargs: (
                    recovered_calls.append(
                        (
                            project_id,
                            workflow_id,
                            kwargs["status"],
                        )
                    )
                    or SimpleNamespace()
                )
            )
        )
    )

    report = recovered.replay_pending(limit=10)

    assert report.applied == 1
    assert recovered_calls == [
        (
            "project-outbox",
            "workflow-terminal",
            "failed",
        )
    ]
    applied = handoff_store.get(event_id)
    assert applied is not None
    assert applied.status == "applied"


def test_concurrent_replay_uses_one_live_delivery_claim(
    isolated_db,
):
    task = _bound_task(task_id="task-concurrent-replay")
    payload = TtsTaskRegistrationEventV1(
        project_id=task.project_id or "",
        source_kind="task",
        source_id=task.task_id,
        segment_id=task.segment_id or "",
        generation_task_id=task.task_id,
        workflow_id="workflow-concurrent",
    )
    event_id = handoff_store.registration_event_id(
        source_kind=payload.source_kind,
        source_id=payload.source_id,
    )
    with database.conn() as connection:
        database.upsert_from_connection(
            connection,
            "tasks",
            task.task_id,
            task.model_dump(),
        )
        handoff_store.enqueue(
            connection,
            event_id=event_id,
            payload=payload,
            created_at=task.created_at,
        )

    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def project(*_args, **_kwargs):
        calls.append("projected")
        entered.set()
        assert release.wait(timeout=2)
        return SimpleNamespace()

    first = video_localization_tts_handoff.TtsHandoffApplicationService(
        runner_id="runner-first",
    )
    first.configure(_projection(register_task=project))
    second = video_localization_tts_handoff.TtsHandoffApplicationService(
        runner_id="runner-second",
    )
    second.configure(_projection(register_task=project))
    reports: list[
        video_localization_tts_handoff.TtsHandoffReplayReport
    ] = []
    worker = threading.Thread(
        target=lambda: reports.append(
            first.replay_pending(limit=10)
        )
    )
    worker.start()
    assert entered.wait(timeout=1)

    blocked = second.replay_pending(limit=10)
    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert calls == ["projected"]
    assert blocked.inspected == 1
    assert blocked.applied == 0
    assert reports[0].applied == 1
    assert handoff_store.pending_count() == 0


def test_reclaimed_delivery_fences_expired_runner_project_write(
    isolated_db,
):
    payload = TtsTaskRegistrationEventV1(
        project_id="project-outbox",
        source_kind="task",
        source_id="task-fenced",
        segment_id="localized-outbox",
        generation_task_id="task-fenced",
        workflow_id="workflow-fenced",
    )
    event_id = handoff_store.registration_event_id(
        source_kind=payload.source_kind,
        source_id=payload.source_id,
    )
    with database.conn() as connection:
        handoff_store.enqueue(
            connection,
            event_id=event_id,
            payload=payload,
            created_at="2026-08-02T00:00:00",
        )
    first = handoff_store.claim_pending(
        event_id,
        runner_id="runner-stale",
        observed_at_ms=1_000,
        lease_duration_ms=100,
    )
    second = handoff_store.claim_pending(
        event_id,
        runner_id="runner-current",
        observed_at_ms=1_100,
        lease_duration_ms=1_000,
    )
    assert first is not None and first.claim is not None
    assert second is not None and second.claim is not None
    assert second.claim.fencing_token > first.claim.fencing_token
    payload_json = {
        "project_id": "project-outbox",
        "name": "fenced project",
        "parameters": {},
        "updated_at": "2026-08-02T00:00:01",
    }

    with pytest.raises(
        handoff_store.TtsHandoffClaimLost,
        match="expired, or superseded",
    ):
        video_localization_operation_store.save_project_with_projection(
            "project-outbox",
            payload_json,
            updated_at="2026-08-02T00:00:01",
            expected_repository_revision=0,
            tts_handoff_claim=first.claim,
            observed_at_ms=1_101,
        )

    revision = (
        video_localization_operation_store
        .save_project_with_projection(
            "project-outbox",
            payload_json,
            updated_at="2026-08-02T00:00:01",
            expected_repository_revision=0,
            tts_handoff_claim=second.claim,
            observed_at_ms=1_101,
        )
    )
    assert revision == 1


def test_reclaimed_delivery_fences_expired_runner_abandonment(
    isolated_db,
):
    payload = TtsTaskRegistrationEventV1(
        project_id="project-outbox",
        source_kind="task",
        source_id="task-abandon-fenced",
        segment_id="localized-outbox",
        generation_task_id="task-abandon-fenced",
        workflow_id="workflow-abandon-fenced",
    )
    event_id = handoff_store.registration_event_id(
        source_kind=payload.source_kind,
        source_id=payload.source_id,
    )
    with database.conn() as connection:
        handoff_store.enqueue(
            connection,
            event_id=event_id,
            payload=payload,
            created_at="2026-08-02T00:00:00",
        )
    first = handoff_store.claim_pending(
        event_id,
        runner_id="runner-stale",
        observed_at_ms=1_000,
        lease_duration_ms=100,
    )
    second = handoff_store.claim_pending(
        event_id,
        runner_id="runner-current",
        observed_at_ms=1_100,
        lease_duration_ms=1_000,
    )
    assert first is not None and first.claim is not None
    assert second is not None and second.claim is not None

    assert handoff_store.abandon_claim(
        first.claim,
        attempted_at="2026-08-02T00:00:01",
        observed_at_ms=1_101,
        reason="stale runner must not retire event",
    ) is False
    pending = handoff_store.get(event_id)
    assert pending is not None
    assert pending.status == "pending"
    assert handoff_store.mark_applied(
        second.claim,
        attempted_at="2026-08-02T00:00:02",
        observed_at_ms=1_101,
    ) is True


def test_process_exit_leaves_reclaimable_durable_lease(
    isolated_db,
):
    payload = TtsTaskRegistrationEventV1(
        project_id="project-outbox",
        source_kind="task",
        source_id="task-process-exit",
        segment_id="localized-outbox",
        generation_task_id="task-process-exit",
        workflow_id="workflow-process-exit",
    )
    event_id = handoff_store.registration_event_id(
        source_kind=payload.source_kind,
        source_id=payload.source_id,
    )
    with database.conn() as connection:
        handoff_store.enqueue(
            connection,
            event_id=event_id,
            payload=payload,
            created_at="2026-08-02T00:00:00",
        )
    child_script = """
import os
from app.services import video_localization_tts_handoff_store as store

decision = store.claim_pending(
    "task-registration:task:task-process-exit",
    runner_id="runner-killed",
    observed_at_ms=1_000,
    lease_duration_ms=100,
)
assert decision is not None and decision.acquired
os._exit(0)
"""
    completed = subprocess.run(
        [sys.executable, "-c", child_script],
        cwd=ROOT,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(BACKEND),
            "VOICE_STUDIO_DB_PATH": str(database.DB_PATH),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr

    blocked = handoff_store.claim_pending(
        event_id,
        runner_id="runner-before-expiry",
        observed_at_ms=1_099,
        lease_duration_ms=100,
    )
    reclaimed = handoff_store.claim_pending(
        event_id,
        runner_id="runner-after-expiry",
        observed_at_ms=1_100,
        lease_duration_ms=100,
    )

    assert blocked is not None
    assert blocked.outcome == "active_lease"
    assert reclaimed is not None
    assert reclaimed.acquired is True
    assert reclaimed.claim is not None
    assert reclaimed.claim.fencing_token == 2


@pytest.mark.parametrize("scope", ["single_group", "all_remaining"])
@pytest.mark.parametrize("matching_result", [True, False])
def test_managed_adoption_uses_result_identity_not_history_path(scope, matching_result):
    task, history = _bound_task(), _bound_history()
    workflow = SimpleNamespace(
        generation_task_id=task.task_id,
        result_id=history.result_id,
        stages=[SimpleNamespace(kind="generation", status="success", parameters={
            "video_localization_execution_scope": scope,
        })],
    )
    adopted = SimpleNamespace(tts_tasks=[workflow], generated_candidates=[{
        "task_id": task.task_id,
        "result_id": history.result_id if matching_result else "other-result",
        "audio_path": "/project/managed/adopted.wav",
    }], timeline_clips=[])
    service = video_localization_tts_handoff.TtsHandoffApplicationService()
    service.configure(_projection(sync_result=lambda *args, **kwargs: adopted))
    assert service.place_generated_result(task, history) is matching_result
