from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings, BatchSegmentResult, BatchTask, GenerationTask, HistoryItem, TaskStatus, VideoLocalizationOperation  # noqa: E402
from app.domains.video_localization import media_assets  # noqa: E402
from app.domains.video_localization import operation_queue as video_localization_operation_queue  # noqa: E402
from app.domains.video_localization import reference_clips as video_localization_reference_clips  # noqa: E402
from app.domains.video_localization import service as video_localization_service  # noqa: E402
from app.domains.video_localization import source_pipeline as video_localization_source_pipeline  # noqa: E402
from app.services import audio_tools, batch_queue, database, settings_store, task_queue  # noqa: E402
from app.api import video_localization as video_localization_api  # noqa: E402


def _client(tmp_path: Path) -> TestClient:
    database.set_db_path(tmp_path / "voice_studio.db")
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )
    return TestClient(app)


def test_video_localization_draft_round_trips_in_project_parameters(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化测试", "description": ""}).json()

    draft = {
        "project_type": "video_localization",
        "schema_version": "v1",
        "source_media": {"filename": "source.mp4", "duration_ms": 3400},
        "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
        "reference_clips": [
            {
                "reference_clip_id": "ref_001",
                "speaker_id": "speaker_01",
                "source_stem": "vocals_clean",
                "asr_text": "In 1992, this changed everything.",
                "cleanliness": "clean",
            }
        ],
        "cues": [
            {
                "cue_id": "cue_0001",
                "speaker_id": "speaker_01",
                "start_ms": 1200,
                "end_ms": 3400,
                "en_subtitle_text": "In 1992, this changed everything.",
                "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                "reference_clip_id": "ref_001",
                "review_status": "needs_review",
            }
        ],
        "quality_gate": {"pending_issues": 1},
    }

    saved = client.put(f"/api/projects/{project['project_id']}/video-localization", json=draft)
    assert saved.status_code == 200
    assert saved.json()["updated_at"]
    assert saved.json()["cues"][0]["tts_recommended_text"].startswith("一九九二年")

    fetched = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert fetched.status_code == 200
    assert fetched.json()["reference_clips"][0]["source_stem"] == "vocals_clean"

    stored_project = client.get(f"/api/projects/{project['project_id']}").json()
    assert stored_project["parameters"]["video_localization"]["cues"][0]["cue_id"] == "cue_0001"


def test_video_localization_empty_draft_has_contract_defaults(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空草稿", "description": ""}).json()

    response = client.get(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 200
    body = response.json()
    assert body["project_type"] == "video_localization"
    assert body["schema_version"] == "v1"
    assert body["source_media"]["filename"] is None
    assert body["source_media"]["metadata"] == {}
    assert body["stems"]["separation_status"] == "pending"
    assert body["quality_gate"]["status"] == "unknown"
    assert body["quality_gate"]["pending_issues"] == 0


def test_video_localization_rejects_inverted_time_ranges(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "坏时间码", "description": ""}).json()

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_bad",
                    "start_ms": 5000,
                    "end_ms": 3000,
                    "tts_recommended_text": "这句时间码有问题。",
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_video_localization_save_recalculates_quality_gate_blockers(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "质量门阻断", "description": ""}).json()

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "quality_gate": {"status": "pass", "pending_issues": 0},
            "cues": [
                {
                    "cue_id": "cue_blocked",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "zh_localized_subtitle_text": "中文字幕存在。",
                    "tts_recommended_text": "中文字幕存在。",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["quality_gate"]["status"] == "blocked"
    blocker_codes = {issue["code"] for issue in body["quality_gate"]["blockers"]}
    assert {"CUE_SPEAKER_MISSING", "EN_SUBTITLE_MISSING"}.issubset(blocker_codes)


def test_video_localization_complete_clone_cue_can_pass_quality_gate(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "质量门通过", "description": ""}).json()

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4"},
            "stems": {"separation_status": "completed", "vocals_clean_path": "stems/vocals.wav"},
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A", "route": "clone_from_source"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": "refs/ref_001.wav",
                    "cleanliness": "clean",
                    "asr_text": "This is a clean reference.",
                    "asr_status": "verified",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_ready",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "audio_route": "clone_from_source",
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                    "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                    "reference_clip_id": "ref_001",
                    "review_status": "ready",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready_for_tts"
    assert body["quality_gate"]["status"] == "pass"
    assert body["quality_gate"]["pending_issues"] == 0


def test_video_localization_patch_cue_updates_single_row_and_quality_gate(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "局部保存 cue", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 2200,
                    "en_subtitle_text": "Original English.",
                },
                {
                    "cue_id": "cue_0002",
                    "speaker_id": "speaker_02",
                    "start_ms": 2300,
                    "end_ms": 3200,
                    "en_subtitle_text": "Keep this line.",
                },
            ],
        },
    )

    response = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/cues/cue_0001",
        json={
            "zh_localized_subtitle_text": "显示字幕。",
            "tts_recommended_text": "显示字幕。",
            "review_status": "ready",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cues"][0]["zh_localized_subtitle_text"] == "显示字幕。"
    assert body["cues"][0]["review_status"] == "ready"
    assert body["cues"][1]["en_subtitle_text"] == "Keep this line."
    blocker_codes = {issue["code"] for issue in body["quality_gate"]["blockers"]}
    assert "ZH_SUBTITLE_MISSING" not in {issue["code"] for issue in body["quality_gate"]["blockers"] if issue.get("cue_id") == "cue_0001"}
    assert "ZH_SUBTITLE_MISSING" in blocker_codes


def test_video_localization_patch_cue_rejects_invalid_time_range(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "局部坏时间", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "start_ms": 1000, "end_ms": 2000}],
        },
    )

    response = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/cues/cue_0001",
        json={"end_ms": 500},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_CUE_INVALID"


def test_video_localization_can_create_speaker_and_assign_cue(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "说话人分配", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "audio_route": "manual_review",
                    "en_subtitle_text": "This changed everything.",
                    "quality_flags": ["generated_by_asr", "needs_speaker_assignment", "needs_zh_localization"],
                }
            ],
        },
    )

    created = client.post(
        f"/api/projects/{project['project_id']}/video-localization/speakers",
        json={"display_name": "A", "route": "clone_from_source"},
    )

    assert created.status_code == 200
    speaker = created.json()["speakers"][0]
    assert speaker["speaker_id"] == "speaker_01"
    assert speaker["display_name"] == "A"
    assert speaker["route"] == "clone_from_source"

    updated = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/cues/cue_0001",
        json={
            "speaker_id": "speaker_01",
            "audio_route": "clone_from_source",
            "zh_localized_subtitle_text": "这改变了一切。",
            "tts_recommended_text": "这，改变了一切。",
        },
    )

    assert updated.status_code == 200
    body = updated.json()
    cue = body["cues"][0]
    assert cue["speaker_id"] == "speaker_01"
    assert cue["audio_route"] == "clone_from_source"
    assert "needs_speaker_assignment" not in cue["quality_flags"]
    assert "needs_zh_localization" not in cue["quality_flags"]
    assert body["speakers"][0]["time_ranges"] == [{"start_ms": 1000, "end_ms": 3200, "source": "cue"}]


def test_video_localization_patch_speaker_keeps_reconciled_tracks(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "更新说话人", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": str(tmp_path / "ref_001.wav"),
                    "cleanliness": "clean",
                    "asr_status": "verified",
                    "asr_text": "Reference line.",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1500,
                    "end_ms": 4200,
                    "en_subtitle_text": "Reference line.",
                }
            ],
        },
    )

    updated = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/speakers/speaker_01",
        json={"display_name": "旁白 A", "route": "preset_tts", "review_status": "ready"},
    )

    assert updated.status_code == 200
    speaker = updated.json()["speakers"][0]
    assert speaker["display_name"] == "旁白 A"
    assert speaker["route"] == "preset_tts"
    assert speaker["review_status"] == "ready"
    assert speaker["reference_clip_ids"] == ["ref_001"]
    assert speaker["time_ranges"] == [{"start_ms": 1500, "end_ms": 4200, "source": "cue"}]


def test_video_localization_import_source_media_updates_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导入视频", "description": ""}).json()
    monkeypatch.setattr(
        media_assets,
        "probe_video",
        lambda path: {
            "duration_ms": 3400,
            "width": 1920,
            "height": 1080,
            "frame_rate": 24.0,
        },
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo clip.mp4", b"fake-video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_media"]["filename"] == "demo clip.mp4"
    assert body["source_media"]["size_bytes"] == len(b"fake-video-bytes")
    assert body["source_media"]["duration_ms"] == 3400
    assert body["source_media"]["width"] == 1920
    assert body["source_media"]["height"] == 1080
    assert body["source_media"]["frame_rate"] == 24.0
    assert body["source_media"]["metadata"]["content_type"] == "video/mp4"
    assert body["source_media"]["metadata"]["probe_status"] == "completed"
    video_path = Path(body["source_media"]["video_path"])
    assert video_path.exists()
    assert video_path.name == "demo_clip.mp4"
    assert project["project_id"] in str(video_path)


def test_video_localization_source_video_can_be_played_after_import(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "播放视频", "description": ""}).json()
    client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")},
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/source-media/video")

    assert response.status_code == 200
    assert response.content == b"fake-video-bytes"


def test_video_localization_source_audio_endpoint_falls_back_to_original_stem(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "源音回退", "description": ""}).json()
    original_audio = tmp_path / "projects" / project["project_id"] / "video_localization" / "audio" / "fallback.wav"
    original_audio.parent.mkdir(parents=True, exist_ok=True)
    original_audio.write_bytes(b"fallback-audio")
    missing_audio = tmp_path / "projects" / project["project_id"] / "video_localization" / "audio" / "missing.wav"

    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(missing_audio)},
            "stems": {"original_audio_path": str(original_audio)},
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/source-media/audio")

    assert response.status_code == 200
    assert response.content == b"fallback-audio"


def test_video_localization_import_rejects_unsupported_media(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导入失败", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("notes.txt", b"not-video", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_UNSUPPORTED_MEDIA"


def test_video_localization_extract_source_audio_updates_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "抽取音轨", "description": ""}).json()
    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")},
    ).json()

    def fake_extract(video_path: Path, audio_path: Path) -> dict:
        assert video_path == Path(imported["source_media"]["video_path"])
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-wav")
        return {"duration_ms": 1234, "sample_rate": 48000, "channels": 2, "size_bytes": 8}

    monkeypatch.setattr(media_assets, "extract_audio_file", fake_extract)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/source-audio")

    assert response.status_code == 200
    body = response.json()
    assert body["source_media"]["audio_path"].endswith("-source.wav")
    assert Path(body["source_media"]["audio_path"]).exists()
    assert body["source_media"]["duration_ms"] == 1234
    assert body["source_media"]["metadata"]["audio_sample_rate"] == 48000
    assert body["source_media"]["metadata"]["audio_channels"] == 2
    assert body["stems"]["original_audio_path"] == body["source_media"]["audio_path"]

    audio = client.get(f"/api/projects/{project['project_id']}/video-localization/source-media/audio")
    assert audio.status_code == 200
    assert audio.content == b"fake-wav"


def test_video_localization_extract_source_audio_requires_video(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺视频", "description": ""}).json()

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/source-audio")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_MISSING"


def test_video_localization_async_source_audio_operation_updates_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "异步抽音频", "description": ""}).json()
    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")},
    ).json()

    def fake_extract(video_path: Path, audio_path: Path) -> dict:
        assert video_path == Path(imported["source_media"]["video_path"])
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-wav")
        return {"duration_ms": 2345, "sample_rate": 44100, "channels": 1, "size_bytes": 8}

    monkeypatch.setattr(media_assets, "extract_audio_file", fake_extract)

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "source_audio"},
    )

    assert response.status_code == 200
    operation = response.json()
    assert operation["kind"] == "source_audio"
    assert operation["status"] in {"queued", "running", "success"}

    completed = None
    for _ in range(30):
        latest = client.get(f"/api/projects/{project['project_id']}/video-localization/operations/{operation['operation_id']}").json()
        if latest["status"] == "success":
            completed = latest
            break
        time.sleep(0.05)

    assert completed is not None
    assert completed["result_summary"]["duration_ms"] == 2345

    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["source_media"]["audio_path"].endswith("-source.wav")
    assert Path(draft["source_media"]["audio_path"]).exists()
    assert draft["source_media"]["metadata"]["audio_extract_status"] == "completed"
    assert draft["operations"][0]["status"] == "success"


def test_video_localization_async_operation_validates_prerequisites(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "异步缺源音", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "stems"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING"


def test_video_localization_cancel_queued_operation_marks_cancelled(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "取消后台任务", "description": ""}).json()
    operation = VideoLocalizationOperation(project_id=project["project_id"], kind="english_asr", status="queued", label="英文 ASR 转字幕")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "operations": [operation.model_dump()],
        },
    )

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/operations/{operation.operation_id}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancel_requested"] is True
    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["operations"][0]["status"] == "cancelled"
    assert draft["source_media"]["metadata"]["english_asr_status"] == "cancelled"


def test_video_localization_retry_failed_operation_creates_new_operation(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重试后台任务", "description": ""}).json()
    monkeypatch.setattr(video_localization_operation_queue, "_enqueue", lambda operation_id: None)
    video_path = tmp_path / "projects" / project["project_id"] / "video_localization" / "source" / "demo.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"fake-video")
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="source_audio",
        status="failed",
        label="抽取源音轨",
        error_code="VIDEO_LOCALIZATION_OPERATION_FAILED",
        error_message="previous failure",
    )
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "demo.mp4",
                "video_path": str(video_path),
                "metadata": {
                    "audio_extract_status": "failed",
                    "audio_extract_error_code": "VIDEO_LOCALIZATION_OPERATION_FAILED",
                    "audio_extract_error": "previous failure",
                },
            },
            "operations": [operation.model_dump()],
        },
    )

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/operations/{operation.operation_id}/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] != operation.operation_id
    assert body["kind"] == "source_audio"
    assert body["status"] == "queued"
    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert len(draft["operations"]) == 2
    assert draft["source_media"]["metadata"]["audio_extract_status"] == "queued"
    assert "audio_extract_error_code" not in draft["source_media"]["metadata"]
    assert "audio_extract_error" not in draft["source_media"]["metadata"]


def test_video_localization_running_cancel_keeps_cancelled_after_late_exception(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "运行中取消", "description": ""}).json()
    video_path = tmp_path / "projects" / project["project_id"] / "video_localization" / "source" / "demo.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"fake-video")
    operation = VideoLocalizationOperation(project_id=project["project_id"], kind="source_audio", status="queued", label="抽取源音轨")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "video_path": str(video_path)},
            "operations": [operation.model_dump()],
        },
    )

    def fake_extract(project_id: str):
        video_localization_operation_queue.cancel(project_id, operation.operation_id)
        raise RuntimeError("late extractor failure")

    monkeypatch.setattr(video_localization_service, "extract_source_audio", fake_extract)

    video_localization_operation_queue._process(operation.operation_id)

    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["operations"][0]["status"] == "cancelled"
    assert draft["operations"][0]["cancel_requested"] is True
    assert draft["source_media"]["metadata"]["audio_extract_status"] == "cancelled"
    assert "audio_extract_error_code" not in draft["source_media"]["metadata"]


def test_video_localization_separate_source_audio_requires_source_audio(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺源音分离", "description": ""}).json()

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/stems")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING"


def test_video_localization_separate_source_audio_updates_stems(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "分离人声", "description": ""}).json()
    audio_path = tmp_path / "projects" / project["project_id"] / "video_localization" / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-wav")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(audio_path), "duration_ms": 4200},
        },
    )

    def fake_separate(source_audio: Path, stems_dir: Path) -> dict:
        assert source_audio == audio_path
        vocals = stems_dir / "source-vocals-clean.wav"
        background = stems_dir / "source-background.wav"
        vocals.parent.mkdir(parents=True, exist_ok=True)
        vocals.write_bytes(b"vocals")
        background.write_bytes(b"background")
        return {
            "vocals_clean_path": vocals,
            "background_path": background,
            "engine_id": "demucs:mock",
            "quality_flags": ["needs_reference_review"],
        }

    monkeypatch.setattr(media_assets, "separate_audio_file", fake_separate)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/stems")

    assert response.status_code == 200
    body = response.json()
    assert body["stems"]["separation_status"] == "completed"
    assert body["stems"]["separation_engine_id"] == "demucs:mock"
    assert body["stems"]["vocals_clean_path"].endswith("source-vocals-clean.wav")
    assert body["stems"]["background_path"].endswith("source-background.wav")
    assert body["stems"]["original_audio_path"] == str(audio_path)
    assert body["stems"]["quality_flags"] == ["needs_reference_review"]

    vocals = client.get(f"/api/projects/{project['project_id']}/video-localization/stems/vocals/audio")
    background = client.get(f"/api/projects/{project['project_id']}/video-localization/stems/background/audio")
    assert vocals.status_code == 200
    assert vocals.content == b"vocals"
    assert background.status_code == 200
    assert background.content == b"background"


def test_video_localization_separate_audio_file_writes_demucs_outputs(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "audio" / "source.wav"
    stems_dir = tmp_path / "stems"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"placeholder")

    vocals = np.full((8, 2), 0.25, dtype=np.float32)
    background = np.full((8, 2), -0.25, dtype=np.float32)

    monkeypatch.setattr(media_assets, "_load_demucs_runtime", lambda: {"fake": True})
    monkeypatch.setattr(
        media_assets,
        "_separate_with_demucs",
        lambda source_audio, runtime: {
            "vocals": vocals,
            "background": background,
            "sample_rate": 44100,
            "model_name": "htdemucs",
        },
    )
    monkeypatch.setattr(audio_tools, "quality_metrics", lambda path, min_duration_ms=1000: {"warnings": ["needs_reference_review"]})

    result = media_assets.separate_audio_file(audio_path, stems_dir)

    assert result["engine_id"] == "demucs:htdemucs"
    assert result["quality_flags"] == ["needs_reference_review"]
    assert Path(result["vocals_clean_path"]).exists()
    assert Path(result["background_path"]).exists()
    vocals_audio, vocals_sr = audio_tools.read_audio(result["vocals_clean_path"])
    background_audio, background_sr = audio_tools.read_audio(result["background_path"])
    assert vocals_sr == 44100
    assert background_sr == 44100
    assert vocals_audio.size > 0
    assert background_audio.size > 0


def test_video_localization_english_asr_requires_source_audio(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺源音", "description": ""}).json()

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/asr/en")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_AUDIO_MISSING"


def test_video_localization_english_asr_creates_cue_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "英文转录", "description": ""}).json()
    audio_path = tmp_path / "projects" / project["project_id"] / "video_localization" / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-wav")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(audio_path), "duration_ms": 4200},
        },
    )

    def fake_transcribe(*, engine_id: str, audio_path: str, language: str):
        assert engine_id == "faster-whisper-turbo"
        assert Path(audio_path).name == "source.wav"
        assert language == "en"
        return {
            "text": "We shipped the first localization pass.",
            "segments": [
                {"start_ms": 0, "end_ms": 1850, "text": "We shipped", "language": "en"},
                {"start_ms": 1850, "end_ms": 4200, "text": "the first localization pass.", "language": "en"},
            ],
        }

    monkeypatch.setattr(video_localization_source_pipeline.asr_service, "transcribe", fake_transcribe)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/asr/en")

    assert response.status_code == 200
    body = response.json()
    assert body["source_media"]["metadata"]["english_asr_status"] == "completed"
    assert body["source_media"]["metadata"]["english_asr_engine_id"] == "faster-whisper-turbo"
    assert body["source_media"]["metadata"]["english_asr_segment_count"] == 2
    assert body["status"] == "blocked"
    assert [cue["en_subtitle_text"] for cue in body["cues"]] == ["We shipped", "the first localization pass."]
    assert body["cues"][0]["start_ms"] == 0
    assert body["cues"][1]["end_ms"] == 4200
    assert "generated_by_asr" in body["cues"][0]["quality_flags"]
    blocker_codes = {issue["code"] for issue in body["quality_gate"]["blockers"]}
    assert {"CUE_SPEAKER_MISSING", "ZH_SUBTITLE_MISSING", "TTS_TEXT_MISSING"}.issubset(blocker_codes)


def test_video_localization_reference_candidates_require_clean_vocals(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺干净人声", "description": ""}).json()

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/reference-clips")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING"


def test_video_localization_reference_candidates_from_clean_vocals(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "参考音候选", "description": ""}).json()
    vocals_path = tmp_path / "projects" / project["project_id"] / "video_localization" / "stems" / "vocals.wav"
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.write_bytes(b"vocals")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"separation_status": "completed", "vocals_clean_path": str(vocals_path)},
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "This is a reference line.",
                    "zh_localized_subtitle_text": "这是一句参考台词。",
                    "tts_recommended_text": "这是一句，参考台词。",
                }
            ],
        },
    )

    def fake_cut(source_path: Path, destination: Path, start_ms: int, end_ms: int):
        assert source_path == vocals_path
        assert start_ms == 1000
        assert end_ms == 3200
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"clip")
        return destination

    monkeypatch.setattr(media_assets, "cut_audio_clip", fake_cut)
    monkeypatch.setattr(video_localization_reference_clips.audio_tools, "probe_audio", lambda path: {"duration_ms": 2200, "sample_rate": 24000, "channels": 1})

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/reference-clips")

    assert response.status_code == 200
    body = response.json()
    assert body["reference_clips"][0]["reference_clip_id"] == "ref_speaker_01_cue_0001"
    assert body["reference_clips"][0]["source_stem"] == "vocals_clean"
    assert body["reference_clips"][0]["cleanliness"] == "needs_review"
    assert body["reference_clips"][0]["asr_status"] == "candidate"
    assert body["reference_clips"][0]["asr_text"] == "This is a reference line."
    assert body["cues"][0]["reference_clip_id"] == "ref_speaker_01_cue_0001"
    assert body["speakers"][0]["reference_clip_ids"] == ["ref_speaker_01_cue_0001"]
    assert body["speakers"][0]["time_ranges"][0]["source"] == "reference_candidate"


def test_video_localization_async_reference_clip_operation_updates_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "异步参考音候选", "description": ""}).json()
    vocals_path = tmp_path / "projects" / project["project_id"] / "video_localization" / "stems" / "vocals.wav"
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.write_bytes(b"vocals")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"separation_status": "completed", "vocals_clean_path": str(vocals_path)},
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "This is a reference line.",
                    "zh_localized_subtitle_text": "这是一句参考台词。",
                    "tts_recommended_text": "这是一句，参考台词。",
                }
            ],
        },
    )

    def fake_cut(source_path: Path, destination: Path, start_ms: int, end_ms: int):
        assert source_path == vocals_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"{start_ms}-{end_ms}".encode("utf-8"))
        return destination

    monkeypatch.setattr(media_assets, "cut_audio_clip", fake_cut)
    monkeypatch.setattr(video_localization_reference_clips.audio_tools, "probe_audio", lambda path: {"duration_ms": 2200, "sample_rate": 24000, "channels": 1})

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/operations",
        json={"kind": "reference_clips"},
    )

    assert response.status_code == 200
    operation = response.json()
    completed = None
    for _ in range(30):
        latest = client.get(f"/api/projects/{project['project_id']}/video-localization/operations/{operation['operation_id']}").json()
        if latest["status"] == "success":
            completed = latest
            break
        time.sleep(0.05)

    assert completed is not None
    assert completed["result_summary"]["reference_clip_count"] == 1
    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["reference_clips"][0]["reference_clip_id"] == "ref_speaker_01_cue_0001"
    assert draft["cues"][0]["reference_clip_id"] == "ref_speaker_01_cue_0001"


def test_video_localization_chinese_draft_requires_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺 cue", "description": ""}).json()

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/localize/zh")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_CUES_MISSING"


def test_video_localization_chinese_draft_fills_missing_tracks_and_keeps_placeholder_blocked(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "中文草稿", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "In 1992, this changed everything.",
                }
            ],
        },
    )

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/localize/zh")

    assert response.status_code == 200
    body = response.json()
    cue = body["cues"][0]
    assert cue["zh_localized_subtitle_text"] == "【待本土化】In 1992, this changed everything."
    assert cue["tts_recommended_text"] == "【待本土化】In 一千九百九十二, this changed everything."
    assert "localization_draft" in cue["quality_flags"]
    blocker_codes = {issue["code"] for issue in body["quality_gate"]["blockers"]}
    assert {"ZH_SUBTITLE_PLACEHOLDER", "TTS_TEXT_PLACEHOLDER"}.issubset(blocker_codes)


def test_video_localization_chinese_draft_normalizes_existing_subtitle_for_tts(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "数字读法", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "In 1992, 130 people joined.",
                    "zh_localized_subtitle_text": "1992 年，有 130 人加入。",
                }
            ],
        },
    )

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/localize/zh")

    assert response.status_code == 200
    cue = response.json()["cues"][0]
    assert cue["zh_localized_subtitle_text"] == "1992 年，有 130 人加入。"
    assert cue["tts_recommended_text"] == "一九九二年，有一百三十人加入。"
    assert "tts_text_normalized" in cue["quality_flags"]


def test_video_localization_subtitle_export_bilingual_srt(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕导出", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/bilingual")

    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith(f'{project["project_id"]}-video-localization-bilingual.srt"')
    assert response.text == (
        "1\n"
        "00:00:01,000 --> 00:00:03,200\n"
        "In 1992, this changed everything.\n"
        "1992 年，这件事改变了一切。\n"
    )


def test_video_localization_subtitle_export_rejects_empty_timed_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "无时间字幕", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "en_subtitle_text": "No timing yet."}],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/bilingual")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLES_EMPTY"


def test_video_localization_subtitle_export_rejects_unsupported_kind(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕类型", "description": ""}).json()

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/ass")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_KIND_UNSUPPORTED"


def test_video_localization_tts_batch_submits_ready_clean_reference_cues(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "批量 TTS", "description": ""}).json()
    reference_path = tmp_path / "refs" / "speaker.wav"
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(b"voice")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": str(reference_path),
                    "cleanliness": "clean",
                    "asr_text": "In 1992, this changed everything.",
                    "asr_status": "verified",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "audio_route": "clone_from_source",
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                    "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                    "reference_clip_id": "ref_001",
                    "review_status": "ready",
                }
            ],
        },
    )
    captured: dict = {}

    async def fake_submit(payload):
        captured["payload"] = payload
        return BatchTask(
            batch_task_id="batch-video-1",
            project_name=payload["project_name"],
            engine_id=payload["engine_id"],
            status=TaskStatus.queued,
            segments=[BatchSegmentResult(segment_id="cue_0001", text=payload["segments"][0]["text"], status=TaskStatus.queued)],
        )

    monkeypatch.setattr(video_localization_api.batch_queue, "submit", fake_submit)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/tts/batch")

    assert response.status_code == 200
    assert response.json()["batch_task_id"] == "batch-video-1"
    payload = captured["payload"]
    assert payload["project_name"] == "批量 TTS 中文本土化配音"
    assert payload["engine_id"] == "indextts-v2"
    assert payload["output_format"] == "mp3"
    assert payload["parameters"]["source"] == "video_localization"
    segment = payload["segments"][0]
    assert segment["segment_id"] == "cue_0001"
    assert segment["text"] == "一九九二年，这件事，改变了一切。"
    assert segment["reference_audio_path"] == str(reference_path)
    assert segment["ref_text"] == "In 1992, this changed everything."
    assert segment["reference_audio_license_status"] == "本土化"
    assert segment["parameters"]["zh_localized_subtitle_text"] == "1992 年，这件事改变了一切。"
    cue = client.get(f"/api/projects/{project['project_id']}/video-localization").json()["cues"][0]
    assert cue["tts_batch_task_id"] == "batch-video-1"
    assert cue["tts_batch_status"] == "queued"
    assert cue["tts_batch_error"] is None


def test_video_localization_tts_batch_rejects_unverified_reference(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "参考音未复听", "description": ""}).json()
    reference_path = tmp_path / "refs" / "speaker.wav"
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(b"voice")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": str(reference_path),
                    "cleanliness": "needs_review",
                    "asr_text": "Reference line.",
                    "asr_status": "candidate",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "audio_route": "clone_from_source",
                    "en_subtitle_text": "Reference line.",
                    "zh_localized_subtitle_text": "参考台词。",
                    "tts_recommended_text": "参考台词。",
                    "reference_clip_id": "ref_001",
                    "review_status": "ready",
                }
            ],
        },
    )

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/tts/batch")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_TTS_REFERENCE_NOT_READY"


def test_video_localization_reference_clip_review_enables_tts_batch(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "参考音复核", "description": ""}).json()
    reference_path = tmp_path / "refs" / "speaker.wav"
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(b"voice")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": str(reference_path),
                    "cleanliness": "needs_review",
                    "asr_text": "Reference line.",
                    "asr_status": "candidate",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "audio_route": "clone_from_source",
                    "en_subtitle_text": "Reference line.",
                    "zh_localized_subtitle_text": "参考字幕。",
                    "tts_recommended_text": "参考台词。",
                    "reference_clip_id": "ref_001",
                    "review_status": "ready",
                }
            ],
        },
    )

    reviewed = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/reference-clips/ref_001",
        json={"cleanliness": "clean", "asr_status": "verified", "asr_text": "Reference line."},
    )
    assert reviewed.status_code == 200
    reference = reviewed.json()["reference_clips"][0]
    assert reference["cleanliness"] == "clean"
    assert reference["asr_status"] == "verified"
    assert "human_verified_reference" in reference["quality_flags"]

    audio = client.get(f"/api/projects/{project['project_id']}/video-localization/reference-clips/ref_001/audio")
    assert audio.status_code == 200
    assert audio.content == b"voice"

    async def fake_submit(payload):
        return BatchTask(
            batch_task_id="batch-reference-review",
            project_name=payload["project_name"],
            engine_id=payload["engine_id"],
            status=TaskStatus.queued,
            segments=[BatchSegmentResult(segment_id="cue_0001", text=payload["segments"][0]["text"], status=TaskStatus.queued)],
        )

    monkeypatch.setattr(video_localization_api.batch_queue, "submit", fake_submit)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/tts/batch")
    assert response.status_code == 200
    assert response.json()["batch_task_id"] == "batch-reference-review"


def test_video_localization_reference_clip_review_requires_asr_text(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "参考音缺文本", "description": ""}).json()
    reference_path = tmp_path / "refs" / "speaker.wav"
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(b"voice")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "source_stem": "vocals_clean",
                    "audio_path": str(reference_path),
                    "cleanliness": "needs_review",
                    "asr_status": "candidate",
                }
            ],
        },
    )

    response = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/reference-clips/ref_001",
        json={"cleanliness": "clean", "asr_status": "verified", "asr_text": " "},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_REFERENCE_ASR_TEXT_MISSING"


def test_video_localization_syncs_tts_batch_results_to_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "同步 TTS", "description": ""}).json()
    output_path = tmp_path / "tts" / "cue_0001.mp3"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"audio")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "Reference line.",
                    "zh_localized_subtitle_text": "参考台词。",
                    "tts_recommended_text": "参考台词。",
                    "review_status": "ready",
                }
            ],
        },
    )
    batch = BatchTask(
        batch_task_id="batch-video-tts-1",
        project_name="同步 TTS",
        engine_id="indextts-v2",
        status=TaskStatus.success,
        segments=[
            BatchSegmentResult(
                segment_id="cue_0001",
                text="参考台词。",
                output_path=str(output_path),
                duration_ms=2600,
                status=TaskStatus.success,
            )
        ],
        parameters={"parameters": {"source": "video_localization", "project_id": project["project_id"]}},
    )
    batch_queue._save(batch)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/tts/batch/{batch.batch_task_id}/sync")

    assert response.status_code == 200
    cue = response.json()["cues"][0]
    assert cue["tts_result_id"] == "batch-video-tts-1:cue_0001"
    assert cue["tts_audio_path"] == str(output_path)
    assert cue["generated_duration_ms"] == 2600
    assert "tts_generated" in cue["quality_flags"]

    audio = client.get(f"/api/projects/{project['project_id']}/video-localization/cues/cue_0001/tts-audio")
    assert audio.status_code == 200
    assert audio.content == b"audio"


def test_video_localization_source_cue_audio_slices_clean_vocals(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "原声切片", "description": ""}).json()
    vocals_path = tmp_path / "stems" / "vocals.wav"
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.write_bytes(b"vocals")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path), "separation_status": "completed"},
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "Source line.",
                }
            ],
        },
    )
    captured = {}

    def fake_slice_audio(source, destination, start_ms, end_ms):
        captured["source"] = source
        captured["destination"] = destination
        captured["start_ms"] = start_ms
        captured["end_ms"] = end_ms
        destination.write_bytes(b"source-cue")

    monkeypatch.setattr(media_assets, "cut_audio_clip", fake_slice_audio)

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/cues/cue_0001/source-audio")

    assert response.status_code == 200
    assert response.content == b"source-cue"
    assert captured["source"] == vocals_path
    assert captured["start_ms"] == 1000
    assert captured["end_ms"] == 3200
    assert "cue_0001-1000-3200" in captured["destination"].name
    assert f"-{vocals_path.stat().st_size}-" in captured["destination"].name


def test_video_localization_tts_batch_sync_records_failed_segments(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "失败回填", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "Failed line.",
                    "zh_localized_subtitle_text": "失败台词。",
                    "tts_recommended_text": "失败台词。",
                }
            ],
        },
    )
    batch = BatchTask(
        batch_task_id="batch-video-failed",
        project_name="失败回填",
        engine_id="indextts-v2",
        status=TaskStatus.failed,
        segments=[
            BatchSegmentResult(
                segment_id="cue_0001",
                text="失败台词。",
                status=TaskStatus.failed,
                error_message="REFERENCE_AUDIO_NOT_FOUND",
            )
        ],
        parameters={"parameters": {"source": "video_localization", "project_id": project["project_id"]}},
    )
    batch_queue._save(batch)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/tts/batch/{batch.batch_task_id}/sync")

    assert response.status_code == 200
    cue = response.json()["cues"][0]
    assert cue["tts_batch_task_id"] == "batch-video-failed"
    assert cue["tts_batch_status"] == "failed"
    assert cue["tts_batch_error"] == "REFERENCE_AUDIO_NOT_FOUND"
    assert "tts_failed" in cue["quality_flags"]
    assert cue["tts_audio_path"] is None


def test_video_localization_single_tts_generation_syncs_from_task_queue(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "单条回填", "description": ""}).json()
    output_path = tmp_path / "outputs" / "single.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"single-audio")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "en_subtitle_text": "Source line.",
                    "zh_localized_subtitle_text": "源台词。",
                    "tts_recommended_text": "源台词。",
                }
            ],
        },
    )
    task = GenerationTask(
        task_id="task-video-single",
        engine_id="indextts-v2",
        project_id=project["project_id"],
        segment_id="cue_0001",
        input_text="源台词。",
        status=TaskStatus.success,
        parameters={"source": "video_localization"},
    )
    hist = HistoryItem(
        result_id="result-video-single",
        task_id=task.task_id,
        engine_id=task.engine_id,
        project_id=task.project_id,
        segment_id=task.segment_id,
        input_text=task.input_text,
        output_path=str(output_path),
        duration_ms=2300,
    )

    task_queue._sync_video_localization_tts_result(task, hist)

    response = client.get(f"/api/projects/{project['project_id']}/video-localization")
    cue = response.json()["cues"][0]
    assert cue["tts_result_id"] == "result-video-single"
    assert cue["tts_audio_path"] == str(output_path)
    assert cue["generated_duration_ms"] == 2300


def test_video_localization_tts_batch_sync_rejects_wrong_project(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "项目 A", "description": ""}).json()
    batch = BatchTask(
        batch_task_id="batch-other-project",
        project_name="其他项目",
        engine_id="indextts-v2",
        status=TaskStatus.success,
        parameters={"parameters": {"source": "video_localization", "project_id": "other-project"}},
    )
    batch_queue._save(batch)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/tts/batch/{batch.batch_task_id}/sync")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_TTS_BATCH_PROJECT_MISMATCH"


def test_video_localization_export_adds_project_metadata(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导出测试", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "tts_recommended_text": "你好。"}],
        },
    )

    exported = client.get(f"/api/projects/{project['project_id']}/video-localization/export")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"].endswith(f'{project["project_id"]}-video-localization.json"')
    body = exported.json()
    assert body["project_id"] == project["project_id"]
    assert body["project_name"] == "导出测试"
    assert body["exported_at"]
    assert body["export_summary"]["cue_count"] == 1
    assert body["quality_gate"]["status"] == "blocked"
    assert body["cues"][0]["tts_recommended_text"] == "你好。"


def test_video_localization_readiness_exports_ready_for_mix(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "就绪审计", "description": ""}).json()
    tts_audio = tmp_path / "outputs" / "cue_0001.wav"
    tts_audio.parent.mkdir(parents=True, exist_ok=True)
    tts_audio.write_bytes(b"fake-tts-audio")

    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "audio_path": str(tmp_path / "source.wav")},
            "stems": {
                "separation_status": "completed",
                "original_audio_path": str(tmp_path / "source.wav"),
                "vocals_clean_path": str(tmp_path / "vocals.wav"),
                "background_path": str(tmp_path / "background.wav"),
            },
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A", "route": "clone_from_source"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": str(tmp_path / "ref_001.wav"),
                    "cleanliness": "clean",
                    "asr_text": "This is a clean reference.",
                    "asr_status": "verified",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "audio_route": "clone_from_source",
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                    "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                    "reference_clip_id": "ref_001",
                    "tts_audio_path": str(tts_audio),
                    "generated_duration_ms": 2000,
                    "source_duration_ms": 2000,
                    "review_status": "ready",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/readiness")

    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith(f'{project["project_id"]}-video-localization-readiness.json"')
    body = response.json()
    assert body["status"] == "ready_for_mix"
    assert body["summary"]["generated_tts_count"] == 1
    assert body["summary"]["quality_gate_status"] == "pass"
    assert body["cue_status"][0]["has_tts_audio"] is True
    assert all(check["status"] == "pass" for check in body["checks"])


def test_video_localization_readiness_blocks_missing_or_failed_tts(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "失败审计", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "audio_path": str(tmp_path / "source.wav")},
            "stems": {
                "separation_status": "completed",
                "original_audio_path": str(tmp_path / "source.wav"),
                "vocals_clean_path": str(tmp_path / "vocals.wav"),
                "background_path": str(tmp_path / "background.wav"),
            },
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A", "route": "clone_from_source"}],
            "reference_clips": [
                {
                    "reference_clip_id": "ref_001",
                    "speaker_id": "speaker_01",
                    "source_stem": "vocals_clean",
                    "audio_path": str(tmp_path / "ref_001.wav"),
                    "cleanliness": "clean",
                    "asr_text": "This is a clean reference.",
                    "asr_status": "verified",
                }
            ],
            "cues": [
                {
                    "cue_id": "cue_failed",
                    "speaker_id": "speaker_01",
                    "start_ms": 1000,
                    "end_ms": 3000,
                    "audio_route": "clone_from_source",
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "1992 年，这件事改变了一切。",
                    "tts_recommended_text": "一九九二年，这件事，改变了一切。",
                    "reference_clip_id": "ref_001",
                    "tts_batch_status": "failed",
                    "tts_batch_error": "REFERENCE_AUDIO_NOT_FOUND",
                    "review_status": "ready",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    check_by_code = {check["code"]: check for check in body["checks"]}
    assert check_by_code["tts_audio_coverage"]["status"] == "blocked"
    assert check_by_code["tts_audio_coverage"]["details"]["missing_cue_ids"] == ["cue_failed"]
    assert check_by_code["tts_failures"]["status"] == "blocked"
    assert check_by_code["tts_failures"]["details"]["failed_cue_ids"] == ["cue_failed"]
    assert body["cue_status"][0]["tts_batch_error"] == "REFERENCE_AUDIO_NOT_FOUND"
