from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import media_assets  # noqa: E402
from app.domains.video_localization import service as video_localization_service  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    VideoLocalizationCue,
    VideoLocalizationReferenceClip,
    VideoLocalizationSubtitleCue,
)
from app.services import database, project_store, settings_store  # noqa: E402


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


def _write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def test_managed_project_file_rejects_external_and_symlink_escape(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "资源边界"}).json()
    project_id = project["project_id"]
    package_root = media_assets.ensure_project_video_localization_dir(project_id)
    managed = package_root / "source" / "managed.wav"
    external = tmp_path / "private.wav"
    other_project = client.post(
        "/api/projects",
        json={"name": "另一个项目"},
    ).json()
    cross_project = (
        media_assets.ensure_project_video_localization_dir(
            other_project["project_id"]
        )
        / "source"
        / "other.wav"
    )
    _write(managed, b"managed")
    _write(external, b"private")
    _write(cross_project, b"other")
    escaped_link = package_root / "source" / "escaped.wav"
    escaped_link.symlink_to(external)

    assert media_assets.managed_project_file(project_id, managed) == managed.resolve()
    assert media_assets.managed_project_file(project_id, external) is None
    assert media_assets.managed_project_file(project_id, cross_project) is None
    assert media_assets.managed_project_file(project_id, escaped_link) is None


def test_managed_project_file_reuses_lightweight_directory_locator(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "媒体定位索引"}).json()
    project_id = project["project_id"]
    package_root = media_assets.ensure_project_video_localization_dir(project_id)
    managed = package_root / "source" / "managed.wav"
    _write(managed, b"managed")
    media_assets.invalidate_project_directory_name(project_id)
    calls = 0
    original = project_store.get_video_localization_directory_locator

    def counted_locator(target_project_id: str):
        nonlocal calls
        calls += 1
        return original(target_project_id)

    monkeypatch.setattr(
        project_store,
        "get_video_localization_directory_locator",
        counted_locator,
    )

    assert media_assets.managed_project_file(project_id, managed) == managed.resolve()
    assert media_assets.managed_project_file(project_id, managed) == managed.resolve()
    assert calls == 1


def test_client_draft_put_cannot_replace_server_owned_media_locators(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "客户端路径隔离"}).json()
    project_id = project["project_id"]
    package_root = media_assets.ensure_project_video_localization_dir(project_id)
    managed_source = _write(package_root / "source" / "source.mp4", b"managed-video")
    managed_audio = _write(package_root / "audio" / "source.wav", b"managed-audio")
    managed_vocals = _write(package_root / "stems" / "vocals.wav", b"managed-vocals")
    managed_reference = _write(package_root / "references" / "ref.wav", b"managed-reference")
    managed_tts = _write(package_root / "tts" / "cue.wav", b"managed-tts")
    managed_candidate = _write(package_root / "tts" / "candidate.wav", b"managed-candidate")
    external = _write(tmp_path / "outside" / "private.wav", b"private")

    current = video_localization_service.get_video_localization(project_id)
    assert current is not None
    current = current.model_copy(
        update={
            "source_media": current.source_media.model_copy(
                update={
                    "filename": "source.mp4",
                    "video_path": managed_source,
                    "audio_path": managed_audio,
                }
            ),
            "stems": current.stems.model_copy(
                update={
                    "original_audio_path": managed_audio,
                    "vocals_clean_path": managed_vocals,
                }
            ),
            "reference_clips": [
                VideoLocalizationReferenceClip(
                    reference_clip_id="ref-1",
                    audio_path=managed_reference,
                )
            ],
            "cues": [
                VideoLocalizationCue(
                    cue_id="cue-1",
                    start_ms=0,
                    end_ms=1000,
                    tts_audio_path=managed_tts,
                )
            ],
            "localized_subtitles": [
                VideoLocalizationSubtitleCue(
                    subtitle_id="subtitle-1",
                    start_ms=0,
                    end_ms=1000,
                    text="测试字幕",
                    tts_audio_path=managed_tts,
                )
            ],
            "generated_candidates": [
                {
                    "candidate_id": "candidate-1",
                    "cue_id": "cue-1",
                    "audio_path": managed_candidate,
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip-1",
                    "track_id": "dub",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "audio_path": managed_tts,
                    "status": "ready",
                }
            ],
        }
    )
    saved = video_localization_service.save_video_localization(project_id, current)
    assert saved is not None

    payload = saved.model_dump(mode="json")
    payload["source_media"]["video_path"] = external
    payload["source_media"]["audio_path"] = external
    payload["stems"]["original_audio_path"] = external
    payload["stems"]["vocals_clean_path"] = external
    payload["reference_clips"][0]["audio_path"] = external
    payload["reference_clips"].append(
        {"reference_clip_id": "ref-injected", "audio_path": external}
    )
    payload["cues"][0]["tts_audio_path"] = external
    payload["localized_subtitles"][0]["tts_audio_path"] = external
    payload["generated_candidates"] = [
        {"candidate_id": "candidate-injected", "cue_id": "cue-1", "audio_path": external}
    ]
    payload["timeline_clips"][0]["audio_path"] = external
    payload["timeline_clips"].append(
        {
            "clip_id": "clip-injected",
            "track_id": "dub",
            "start_ms": 0,
            "end_ms": 1000,
            "audio_path": external,
            "status": "ready",
        }
    )

    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=payload,
    )

    assert response.status_code == 200
    persisted = video_localization_service.get_video_localization(project_id)
    assert persisted is not None
    assert persisted.source_media.video_path == managed_source
    assert persisted.source_media.audio_path == managed_audio
    assert persisted.stems.original_audio_path == managed_audio
    assert persisted.stems.vocals_clean_path == managed_vocals
    assert persisted.reference_clips[0].audio_path == managed_reference
    assert persisted.reference_clips[1].audio_path is None
    assert persisted.cues[0].tts_audio_path == managed_tts
    assert persisted.localized_subtitles[0].tts_audio_path == managed_tts
    assert persisted.generated_candidates[0]["candidate_id"] == "candidate-injected"
    assert "audio_path" not in persisted.generated_candidates[0]
    assert persisted.timeline_clips[0]["audio_path"] == managed_tts
    assert [clip["clip_id"] for clip in persisted.timeline_clips] == ["clip-1"]
    assert external not in response.text
    assert client.get(
        f"/api/projects/{project_id}/video-localization/source-media/video"
    ).content == b"managed-video"


def test_media_endpoints_reject_persisted_paths_outside_project_package(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "读取边界"}).json()
    project_id = project["project_id"]
    external = _write(tmp_path / "outside" / "private.wav", b"private")
    current = video_localization_service.get_video_localization(project_id)
    assert current is not None
    poisoned = current.model_copy(
        update={
            "source_media": current.source_media.model_copy(
                update={"filename": "private.mp4", "video_path": external}
            ),
            "reference_clips": [
                VideoLocalizationReferenceClip(
                    reference_clip_id="ref-external",
                    audio_path=external,
                )
            ],
            "cues": [
                VideoLocalizationCue(
                    cue_id="cue-external",
                    start_ms=0,
                    end_ms=1000,
                    tts_audio_path=external,
                )
            ],
            "generated_candidates": [
                {
                    "candidate_id": "candidate-external",
                    "cue_id": "cue-external",
                    "audio_path": external,
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip-external",
                    "track_id": "dub",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "audio_path": external,
                    "status": "ready",
                }
            ],
        }
    )
    assert video_localization_service.save_video_localization(
        project_id,
        poisoned,
    ) is not None

    urls = [
        f"/api/projects/{project_id}/video-localization/source-media/video",
        f"/api/projects/{project_id}/video-localization/cues/cue-external/tts-audio",
        f"/api/projects/{project_id}/video-localization/candidates/candidate-external/audio",
        f"/api/projects/{project_id}/video-localization/timeline-clips/clip-external/audio",
        f"/api/projects/{project_id}/video-localization/reference-clips/ref-external/audio",
    ]

    for url in urls:
        response = client.get(url)
        assert response.status_code == 404
        assert response.content != b"private"
