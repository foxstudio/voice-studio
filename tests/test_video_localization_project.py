from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings, BatchSegmentResult, BatchTask, GenerationTask, HistoryItem, TaskStatus  # noqa: E402
from app.domains.video_localization import service as video_localization_service  # noqa: E402
from app.services import batch_queue, database, settings_store, task_queue  # noqa: E402
from app.api import projects as projects_api  # noqa: E402


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


def test_video_localization_import_source_media_updates_draft(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导入视频", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo clip.mp4", b"fake-video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_media"]["filename"] == "demo clip.mp4"
    assert body["source_media"]["size_bytes"] == len(b"fake-video-bytes")
    assert body["source_media"]["metadata"]["content_type"] == "video/mp4"
    video_path = Path(body["source_media"]["video_path"])
    assert video_path.exists()
    assert video_path.name == "demo_clip.mp4"
    assert project["project_id"] in str(video_path)


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

    monkeypatch.setattr(video_localization_service, "_extract_audio_file", fake_extract)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/source-audio")

    assert response.status_code == 200
    body = response.json()
    assert body["source_media"]["audio_path"].endswith("-source.wav")
    assert Path(body["source_media"]["audio_path"]).exists()
    assert body["source_media"]["duration_ms"] == 1234
    assert body["source_media"]["metadata"]["audio_sample_rate"] == 48000
    assert body["source_media"]["metadata"]["audio_channels"] == 2
    assert body["stems"]["original_audio_path"] == body["source_media"]["audio_path"]


def test_video_localization_extract_source_audio_requires_video(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺视频", "description": ""}).json()

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/source-audio")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_MISSING"


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

    monkeypatch.setattr(video_localization_service, "_separate_audio_file", fake_separate)

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/stems")

    assert response.status_code == 200
    body = response.json()
    assert body["stems"]["separation_status"] == "completed"
    assert body["stems"]["separation_engine_id"] == "demucs:mock"
    assert body["stems"]["vocals_clean_path"].endswith("source-vocals-clean.wav")
    assert body["stems"]["background_path"].endswith("source-background.wav")
    assert body["stems"]["original_audio_path"] == str(audio_path)
    assert body["stems"]["quality_flags"] == ["needs_reference_review"]


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

    monkeypatch.setattr(video_localization_service.asr_service, "transcribe", fake_transcribe)

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

    monkeypatch.setattr(video_localization_service, "_cut_audio_clip", fake_cut)
    monkeypatch.setattr(video_localization_service.audio_tools, "probe_audio", lambda path: {"duration_ms": 2200, "sample_rate": 24000, "channels": 1})

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

    monkeypatch.setattr(projects_api.batch_queue, "submit", fake_submit)

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

    monkeypatch.setattr(video_localization_service, "_cut_audio_clip", fake_slice_audio)

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
