from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


from app.domains.video_localization import operation_queue  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, settings_store  # noqa: E402
from app.services import (  # noqa: E402
    video_localization_export_destinations as destinations,
)
from app.services import video_localization_exports  # noqa: E402


def _client(tmp_path: Path) -> TestClient:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(export_dir),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    return TestClient(app)


def test_media_export_operation_reports_progress_and_safe_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "异步导出", "description": ""},
    ).json()["project_id"]
    selected = destinations.select_destination("default")
    assert selected.destination_id is not None
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)
    captured: dict = {}

    def render(
        value_project_id,
        request,
        directory,
        output_filename,
        *,
        on_progress,
        is_cancelled,
    ):
        captured.update(
            project_id=value_project_id,
            request=request,
            directory=directory,
            output_filename=output_filename,
        )
        assert not is_cancelled()
        on_progress(0.04, "正在检查导出内容")
        on_progress(0.42, "正在渲染视频 42%")
        on_progress(0.98, "正在核对导出文件")
        return {
            "filename": "异步导出.mp4",
            "size_bytes": 1_048_576,
            "kind": "video",
            "mixed_track_count": 2,
        }

    monkeypatch.setattr(
        video_localization_exports.video_localization_exports,
        "create_media_export_at",
        render,
    )
    response = client.post(
        (
            f"/api/projects/{project_id}/video-localization/"
            "export/render"
        ),
        json={
            "schema_version": "v1",
            "destination_id": selected.destination_id,
            "output_filename": "用户确认成品.mp4",
            "render": {
                "schema_version": "v1",
                "kind": "video",
                "audio_tracks": ["background", "dub"],
                "subtitle_tracks": [],
                "video_size": "720p",
            },
        },
    )
    assert response.status_code == 200, response.text
    submitted = response.json()

    operation_queue._process(
        project_id,
        submitted["operation_id"],
    )

    completed = operation_queue.get_operation(
        project_id,
        submitted["operation_id"],
    )
    assert completed is not None
    assert completed.status == "success", (
        completed.error_code,
        completed.error_message,
        completed.result_summary,
    )
    assert completed.progress == 1
    assert completed.result_summary["filename"] == "异步导出.mp4"
    assert completed.result_summary["size_bytes"] == 1_048_576
    assert completed.result_summary["export_kind"] == "video"
    assert {
        step_id: result["status"]
        for step_id, result in completed.result_summary[
            "task_step_results"
        ].items()
    } == {
        "prepare": "success",
        "render": "success",
        "validate": "success",
    }
    assert "destination_id" not in completed.result_summary
    assert "output_path" not in completed.result_summary
    assert captured["project_id"] == project_id
    assert captured["directory"] == Path(selected.display_path)
    assert captured["output_filename"] == "用户确认成品.mp4"
    assert captured["request"].audio_tracks == ["background", "dub"]

    retried = operation_queue.retry(
        project_id,
        submitted["operation_id"],
    )
    assert retried is not None
    assert retried.parameters["output_filename"] == "用户确认成品.mp4"


def test_subtitle_export_operation_writes_exact_filename_to_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "字幕异步导出", "description": ""},
    ).json()["project_id"]
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_1",
                    "start_ms": 1000,
                    "end_ms": 2500,
                    "en_subtitle_text": "Hello world",
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    selected = destinations.select_destination("default")
    assert selected.destination_id is not None
    assert selected.display_path is not None
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _key: None)

    response = client.post(
        (
            f"/api/projects/{project_id}/video-localization/"
            "export/render"
        ),
        json={
            "schema_version": "v1",
            "destination_id": selected.destination_id,
            "output_filename": "用户确认字幕.srt",
            "render": {
                "schema_version": "v1",
                "kind": "subtitle",
                "subtitle_tracks": ["asr"],
            },
        },
    )
    assert response.status_code == 200, response.text
    operation_id = response.json()["operation_id"]

    operation_queue._process(project_id, operation_id)

    completed = operation_queue.get_operation(project_id, operation_id)
    assert completed is not None
    assert completed.status == "success", completed.error_message
    assert completed.result_summary["export_kind"] == "subtitle"
    assert completed.result_summary["filename"] == "用户确认字幕.srt"
    destination = Path(selected.display_path) / "用户确认字幕.srt"
    assert destination.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,500\nHello world\n"
    )
    assert not list(
        Path(selected.display_path).glob(
            ".用户确认字幕.*.partial.srt"
        )
    )
