from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    GenerationTask,
    HistoryItem,
    TaskStatus,
)
from app.services import (  # noqa: E402
    database as db,
    history_store,
    settings_store,
    task_queue,
)


def test_history_delete_removes_audio_and_waveform_cache(tmp_path, monkeypatch):
    original_db = db.DB_PATH
    db.set_db_path(tmp_path / "voice_studio.db")
    settings = AppSettings(
        data_dir=str(tmp_path),
        voice_dir=str(tmp_path / "voices"),
        output_dir=str(tmp_path / "outputs"),
        export_dir=str(tmp_path / "exports"),
        project_dir=str(tmp_path / "projects"),
        cache_dir=str(tmp_path / "cache"),
        log_dir=str(tmp_path / "logs"),
    )
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    try:
        output = tmp_path / "outputs" / "result.wav"
        output.parent.mkdir()
        output.write_bytes(b"audio")
        waveform = tmp_path / "cache" / "waveforms" / "result-1-2-320.json"
        waveform.parent.mkdir(parents=True)
        waveform.write_text("{}", encoding="utf-8")
        history_store.add(
            HistoryItem(
                result_id="result",
                task_id="task",
                engine_id="indextts-v2",
                input_text="test",
                output_path=str(output),
            )
        )

        history_store.delete("result")

        assert not output.exists()
        assert not waveform.exists()
        assert history_store.get("result") is None
    finally:
        db.set_db_path(original_db)


def test_history_list_filters_video_localization_records(tmp_path):
    original_db = db.DB_PATH
    db.set_db_path(tmp_path / "voice_studio.db")
    try:
        history_store.add(
            HistoryItem(
                result_id="localized-a",
                task_id="task-a",
                engine_id="indextts-v2",
                project_id="project-a",
                segment_id="localized-1",
                input_text="第一条",
                parameter_snapshot={"source": "video_localization"},
            )
        )
        history_store.add(
            HistoryItem(
                result_id="localized-b",
                task_id="task-b",
                engine_id="indextts-v2",
                project_id="project-b",
                segment_id="localized-1",
                input_text="第二条",
                parameter_snapshot={"source": "video_localization"},
            )
        )
        history_store.add(
            HistoryItem(
                result_id="regular-a",
                task_id="task-c",
                engine_id="indextts-v2",
                project_id="project-a",
                segment_id="localized-1",
                input_text="普通生成",
            )
        )
        history_store.add(
            HistoryItem(
                result_id="grouped-a",
                task_id="task-grouped-a",
                engine_id="indextts-v2",
                project_id="project-a",
                segment_id="group-localized-1-localized-2",
                localized_subtitle_id="localized-1",
                cue_id="cue-1",
                input_text="组合生成",
                parameter_snapshot={
                    "source": "video_localization",
                    "video_localization_target_subtitle_ids": [
                        "localized-1",
                        "localized-2",
                    ],
                    "video_localization_source_cue_ids": ["cue-1", "cue-2"],
                },
            )
        )

        items = history_store.list_history(
            project_id="project-a",
            segment_id="localized-1",
            source="video_localization",
        )

        assert {item.result_id for item in items} == {"localized-a", "grouped-a"}
        assert history_store.count_history(
            project_id="project-a",
            segment_id="localized-1",
            source="video_localization",
        ) == 2
        assert {
            item.result_id
            for item in history_store.list_history(
                project_id="project-a",
                segment_id="localized-2",
                source="video_localization",
            )
        } == {"grouped-a"}
        assert history_store.count_history(
            project_id="project-a",
            segment_id="cue-2",
            source="video_localization",
        ) == 1
    finally:
        db.set_db_path(original_db)


def test_history_update_replaces_stale_scope_index(tmp_path):
    original_db = db.DB_PATH
    db.set_db_path(tmp_path / "voice_studio.db")
    try:
        item = HistoryItem(
            result_id="replace-scope",
            task_id="replace-scope-task",
            engine_id="indextts-v2",
            project_id="project-a",
            segment_id="localized-old",
            input_text="旧范围",
            parameter_snapshot={"source": "video_localization"},
        )
        history_store.add(item)
        history_store.add(
            item.model_copy(
                update={
                    "segment_id": "localized-new",
                    "input_text": "新范围",
                }
            )
        )

        assert history_store.count_history(segment_id="localized-old") == 0
        assert history_store.count_history(segment_id="localized-new") == 1
    finally:
        db.set_db_path(original_db)


def test_legacy_history_rows_are_backfilled_into_read_index(tmp_path):
    original_db = db.DB_PATH
    database_path = tmp_path / "voice_studio.db"
    payload = HistoryItem(
        result_id="legacy-history",
        task_id="legacy-history-task",
        engine_id="indextts-v2",
        project_id="legacy-project",
        localized_subtitle_id="localized-legacy",
        input_text="旧记录",
        parameter_snapshot={"source": "video_localization"},
    ).model_dump(mode="json")
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE history (
            result_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO history (result_id, data, created_at) VALUES (?, ?, ?)",
        (
            "legacy-history",
            json.dumps(payload, ensure_ascii=False),
            payload["created_at"],
        ),
    )
    connection.commit()
    connection.close()
    db.set_db_path(database_path)
    try:
        items = history_store.list_history(
            project_id="legacy-project",
            segment_id="localized-legacy",
            source="video_localization",
        )

        assert [item.result_id for item in items] == ["legacy-history"]
    finally:
        db.set_db_path(original_db)


def test_bulk_history_delete_commits_records_when_artifact_cleanup_fails(
    tmp_path,
    monkeypatch,
):
    original_db = db.DB_PATH
    db.set_db_path(tmp_path / "voice_studio.db")
    settings = AppSettings(
        data_dir=str(tmp_path),
        voice_dir=str(tmp_path / "voices"),
        output_dir=str(tmp_path / "outputs"),
        export_dir=str(tmp_path / "exports"),
        project_dir=str(tmp_path / "projects"),
        cache_dir=str(tmp_path / "cache"),
        log_dir=str(tmp_path / "logs"),
    )
    monkeypatch.setattr(settings_store, "get", lambda: settings)
    output = tmp_path / "outputs" / "locked.wav"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"audio")
    history_store.add(
        HistoryItem(
            result_id="cleanup-failure",
            task_id="cleanup-failure-task",
            engine_id="indextts-v2",
            input_text="test",
            output_path=str(output),
        )
    )
    original_unlink = Path.unlink

    def fail_selected_unlink(path, *args, **kwargs):
        if path == output:
            raise PermissionError("locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)
    try:
        report = history_store.delete_many_with_report(["cleanup-failure"])

        assert report.removed_records == 1
        assert report.removed_files == 0
        assert report.cleanup_failures == 1
        assert history_store.get("cleanup-failure") is None
        assert output.exists()
    finally:
        db.set_db_path(original_db)


def test_history_api_retires_task_artifact_reference_in_same_command(tmp_path):
    original_db = db.DB_PATH
    db.set_db_path(tmp_path / "voice_studio.db")
    try:
        history_store.add(
            HistoryItem(
                result_id="api-delete-result",
                task_id="api-delete-task",
                engine_id="indextts-v2",
                input_text="删除结果",
            )
        )
        task_queue._save(
            GenerationTask(
                task_id="api-delete-task",
                engine_id="indextts-v2",
                input_text="删除结果",
                status=TaskStatus.success,
                result_audio_id="api-delete-result",
                result_id="api-delete-result",
                result_duration_ms=1_000,
            )
        )

        response = TestClient(app).delete("/api/history/api-delete-result")

        assert response.status_code == 200
        assert history_store.get("api-delete-result") is None
        task = task_queue.get_task("api-delete-task")
        assert task is not None
        assert task.status == TaskStatus.success
        assert task.result_id is None
        assert task.result_audio_id is None
        assert task.artifacts_removed_at
    finally:
        db.set_db_path(original_db)
