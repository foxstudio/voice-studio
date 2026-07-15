from __future__ import annotations

import io
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    BatchSegmentResult,
    BatchTask,
    GenerationTask,
    HistoryItem,
    TaskStatus,
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationResearchQuery,
    VideoLocalizationResearchSource,
    VideoLocalizationResearchState,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptEditOperation,
    VideoLocalizationTranscriptionState,
)
from app.domains.video_localization import media_assets  # noqa: E402
from app.domains.video_localization import operation_queue as video_localization_operation_queue  # noqa: E402
from app.domains.video_localization import operation_state as video_localization_operation_state  # noqa: E402
from app.domains.video_localization import reference_clips as video_localization_reference_clips  # noqa: E402
from app.domains.video_localization import exporting as video_localization_exporting  # noqa: E402
from app.domains.video_localization import service as video_localization_service  # noqa: E402
from app.domains.video_localization import source_pipeline as video_localization_source_pipeline  # noqa: E402
from app.services import audio_tools, batch_queue, database, project_store, settings_store, task_queue  # noqa: E402
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


def _project_root(project_id: str) -> Path:
    return media_assets.project_video_localization_dir(project_id)


def _portable_path(root: Path, value: str) -> Path:
    assert value.startswith("project://")
    relative = value.removeprefix("project://")
    return root if relative in {"", "."} else root / relative


def _completed_asr_result(draft, engine_id: str = "qwen3-asr-mlx"):
    transcript = VideoLocalizationTranscriptionState(
        language="en",
        source_track_id="original",
        engine_id=engine_id,
        raw_text="Concurrent ASR result.",
        corrected_text="Concurrent ASR result.",
        segments=[
            VideoLocalizationTranscriptSegment(
                segment_id="asr_0001",
                start_ms=0,
                end_ms=1200,
                raw_text="Concurrent ASR result.",
                corrected_text="Concurrent ASR result.",
            )
        ],
        words=[
            VideoLocalizationAlignedWord(
                word_id="word_0001",
                segment_id="asr_0001",
                text="Concurrent",
                start_ms=0,
                end_ms=500,
            ),
            VideoLocalizationAlignedWord(
                word_id="word_0002",
                segment_id="asr_0001",
                text="ASR",
                start_ms=500,
                end_ms=850,
            ),
            VideoLocalizationAlignedWord(
                word_id="word_0003",
                segment_id="asr_0001",
                text="result.",
                start_ms=850,
                end_ms=1200,
            ),
        ],
    )
    source_media = draft.source_media.model_copy(
        update={
            "metadata": {
                **draft.source_media.metadata,
                "english_asr_status": "completed",
                "english_asr_engine_id": engine_id,
                "english_asr_source_track_id": "original",
                "english_asr_segment_count": 1,
            }
        }
    )
    return draft.model_copy(update={"source_media": source_media, "transcription": transcript})


def test_video_localization_asr_operation_summary_distinguishes_raw_segments_from_cues():
    draft = _completed_asr_result(VideoLocalizationDraft())
    reviewed_segment = draft.transcription.segments[0].model_copy(
        update={
            "corrected_text": "Concurrent Seedance result.",
            "review_operations": [
                VideoLocalizationTranscriptEditOperation(
                    start_word_id="word_0002",
                    end_word_id="word_0002",
                    source_text="ASR",
                    replacement_text="Seedance",
                    reason="官方产品名称核验",
                    confidence=0.96,
                    status="accepted",
                    evidence_source_ids=["source_01"],
                )
            ],
        }
    )
    research = VideoLocalizationResearchState(
        status="completed",
        provider="web-search",
        queries=[
            VideoLocalizationResearchQuery(
                query_id="query_01",
                query="Seedance official product name",
                category="proper_noun",
                reason="确认产品专名拼写",
                target_terms=["Seedance"],
            )
        ],
        sources=[
            VideoLocalizationResearchSource(
                source_id="source_01",
                query_id="query_01",
                title="Seedance 官方产品页",
                url="https://example.com/seedance",
                snippet="Seedance product documentation",
                provider="web-search",
            )
        ],
    )
    transcription = draft.transcription.model_copy(
        update={
            "segments": [reviewed_segment],
            "review_status": "completed",
            "research": research,
            "pipeline_timing": {
                "total_duration_ms": 4321,
                "stages": {
                    "boundary_review": {
                        "duration_ms": 1200,
                        "candidate_count": 3,
                        "batch_count": 2,
                        "round_count": 2,
                        "profile_id": "subtitle-review",
                        "model_id": "deepseek-chat",
                        "rounds": [
                            {
                                "round": 1,
                                "candidate_count": 2,
                                "batch_count": 1,
                                "duration_ms": 700,
                                "batches": [
                                    {
                                        "round": 1,
                                        "batch": 1,
                                        "candidate_count": 2,
                                        "duration_ms": 700,
                                        "status": "success",
                                        "attempt_count": 1,
                                    }
                                ],
                            },
                            {"round": 2, "candidate_count": 1, "batch_count": 1, "duration_ms": 500},
                        ],
                    }
                },
            }
        }
    )
    source_media = draft.source_media.model_copy(
        update={
            "metadata": {
                **draft.source_media.metadata,
                "english_asr_raw_segment_count": 1,
                "english_asr_segment_count": 3,
            }
        }
    )
    draft = draft.model_copy(
        update={
            "source_media": source_media,
            "transcription": transcription,
            "cues": [
                VideoLocalizationCue(
                    cue_id=f"cue_{index:04d}",
                    start_ms=(index - 1) * 500,
                    end_ms=index * 500,
                    en_subtitle_text=f"Subtitle {index}",
                    quality_flags=["generated_by_asr"],
                )
                for index in range(1, 4)
            ],
        }
    )

    summary = video_localization_operation_state.english_asr_summary(draft)

    assert summary["segment_count"] == 1
    assert summary["cue_count"] == 3
    assert summary["duration_ms"] == 4321
    assert summary["llm_profile_id"] == "subtitle-review"
    assert summary["llm_model_id"] == "deepseek-chat"
    assert summary["stage_timings"]["boundary_review"]["candidate_count"] == 3
    assert [item["candidate_count"] for item in summary["boundary_review_rounds"]] == [2, 1]
    assert summary["stage_timings"]["boundary_review"]["rounds"][0]["batches"][0]["status"] == "success"
    assert set(summary["task_step_results"]) == {
        "asr",
        "web_research",
        "text_review",
        "alignment",
        "audio_boundaries",
        "boundary_review",
        "subtitle_track",
    }
    assert summary["task_step_results"]["asr"]["status"] == "success"
    assert summary["task_step_results"]["asr"]["sections"][0]["items"][0]["text"] == "Concurrent ASR result."
    research_result = summary["task_step_results"]["web_research"]
    assert {item["label"]: item["value"] for item in research_result["metrics"]}["支持修改"] == "1"
    assert research_result["sections"][1]["title"] == "对文本校对的作用"
    assert research_result["sections"][1]["items"][0]["title"] == "ASR → Seedance"
    assert "Seedance 官方产品页" in summary["task_step_results"]["text_review"]["sections"][0]["items"][0]["text"]
    assert summary["task_step_results"]["subtitle_track"]["status"] == "success"
    assert {item["label"]: item["value"] for item in summary["task_step_results"]["subtitle_track"]["metrics"]}[
        "字幕数量"
    ] == "3"


def test_video_localization_asr_rerun_reuses_replaceable_cue_ids():
    latest = _completed_asr_result(VideoLocalizationDraft()).model_copy(
        update={
            "cues": [
                VideoLocalizationCue(
                    cue_id="cue_0001",
                    start_ms=0,
                    end_ms=1200,
                    en_subtitle_text="Old ASR cue.",
                    quality_flags=["generated_by_asr"],
                ),
                VideoLocalizationCue(
                    cue_id="cue_0002",
                    start_ms=2000,
                    end_ms=2500,
                    en_subtitle_text="Manual cue.",
                    quality_flags=["protected_manual_edit"],
                ),
            ]
        }
    )
    result = _completed_asr_result(latest.model_copy(update={"cues": []}))

    merged = video_localization_source_pipeline.merge_english_asr_result(latest, result)

    assert [cue.cue_id for cue in merged.cues] == ["cue_0001", "cue_0002"]
    assert merged.cues[0].en_subtitle_text == "Concurrent ASR result"
    assert merged.cues[1].en_subtitle_text == "Manual cue."


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
        "ui_state": {
            "selected_cue_id": "cue_0001",
            "sidebar_collapsed": True,
            "subtitle_preview": {"enabled": True, "source": "localized", "stylePreset": "boxed"},
            "track_states": {"original": {"muted": True, "solo": False, "volume": 0.35}},
            "timeline_zoom": 2,
        },
        "voice_recipes": [{"recipe_id": "recipe_001", "reference_clip_id": "ref_001", "name": "室内温和"}],
        "generated_candidates": [{"candidate_id": "candidate_001", "recipe_id": "recipe_001", "status": "success"}],
        "timeline_clips": [{"clip_id": "clip_001", "cue_id": "cue_0001", "track_id": "dub", "start_ms": 1200}],
        "quality_gate": {"pending_issues": 1},
    }

    saved = client.put(f"/api/projects/{project['project_id']}/video-localization", json=draft)
    assert saved.status_code == 200
    assert saved.json()["updated_at"]
    assert saved.json()["cues"][0]["tts_recommended_text"].startswith("一九九二年")

    fetched = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert fetched.status_code == 200
    assert fetched.json()["reference_clips"][0]["source_stem"] == "vocals_clean"
    assert fetched.json()["ui_state"]["selected_cue_id"] == "cue_0001"
    assert fetched.json()["ui_state"]["track_states"]["original"]["volume"] == 0.35
    assert fetched.json()["voice_recipes"][0]["name"] == "室内温和"
    assert fetched.json()["generated_candidates"][0]["candidate_id"] == "candidate_001"
    assert fetched.json()["timeline_clips"][0]["track_id"] == "dub"

    stored_project = client.get(f"/api/projects/{project['project_id']}").json()
    assert stored_project["parameters"]["video_localization"]["cues"][0]["cue_id"] == "cue_0001"
    assert stored_project["parameters"]["video_localization"]["ui_state"]["sidebar_collapsed"] is True


def test_video_localization_rejects_stale_full_draft_but_ui_patch_preserves_results(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "并发自动保存", "description": ""}).json()
    first = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000, "tts_recommended_text": "测试。"}],
            "generated_candidates": [{"candidate_id": "candidate_001", "recipe_id": "recipe_001", "status": "success"}],
            "ui_state": {"timeline_zoom": 1},
        },
    )
    stale = first.json()

    patched = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/ui-state",
        json={"timeline_zoom": 4, "sidebar_collapsed": True},
    )
    assert patched.status_code == 200
    assert patched.json()["ui_state"]["timeline_zoom"] == 4
    assert patched.json()["generated_candidates"][0]["candidate_id"] == "candidate_001"

    stale["ui_state"]["timeline_zoom"] = 2
    conflict = client.put(f"/api/projects/{project['project_id']}/video-localization", json=stale)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "VIDEO_LOCALIZATION_DRAFT_CONFLICT"


def test_video_localization_save_writes_project_manifest_and_autosave(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "可恢复项目", "description": ""}).json()

    response = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 3400},
            "ui_state": {"selected_cue_id": "cue_0001", "timeline_zoom": 1.6},
            "cues": [{"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1200, "en_subtitle_text": "Hello"}],
        },
    )

    assert response.status_code == 200
    root = _project_root(project["project_id"])
    manifest_path = root / "project.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["kind"] == "video_localization_project"
    assert manifest["project_id"] == project["project_id"]
    assert manifest["project_name"] == "可恢复项目"
    assert manifest["storage"]["primary_state"] == "voice_studio_db.projects.parameters.video_localization"
    assert manifest["storage"]["root"] == "project://."
    assert manifest["storage"]["portability"] == {"status": "portable", "external_paths": []}
    assert manifest["draft"]["ui_state"]["timeline_zoom"] == 1.6
    assert manifest["draft"]["cues"][0]["cue_id"] == "cue_0001"
    assert set(manifest["storage"]["directories"]) >= {"source", "audio", "stems", "references", "tts", "exports", "autosave"}
    for path in manifest["storage"]["directories"].values():
        assert _portable_path(root, path).exists()
    autosaves = sorted((root / "autosave").glob("*-project.json"))
    assert len(autosaves) == 1
    assert json.loads(autosaves[0].read_text(encoding="utf-8"))["draft"]["source_media"]["filename"] == "source.mp4"


def test_video_localization_recovers_from_project_snapshot_when_database_draft_is_missing(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "快照恢复", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "recover.mp4", "duration_ms": 3200},
            "cues": [{"cue_id": "cue_recovered", "start_ms": 0, "end_ms": 1200, "en_subtitle_text": "Recovered."}],
        },
    )
    assert saved.status_code == 200

    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    stored.parameters = {key: value for key, value in stored.parameters.items() if key != "video_localization"}
    project_store.save_project(stored)

    recovered = client.get(f"/api/projects/{project['project_id']}/video-localization")
    assert recovered.status_code == 200
    assert recovered.json()["source_media"]["filename"] == "recover.mp4"
    assert recovered.json()["cues"][0]["cue_id"] == "cue_recovered"


def test_video_localization_sync_recovers_local_package_and_is_idempotent(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本地恢复项目", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "source_media": {"filename": "recover.mp4", "duration_ms": 1200},
            "operations": [
                {
                    "operation_id": "operation_pending",
                    "project_id": project["project_id"],
                    "kind": "english_asr",
                    "status": "running",
                }
            ],
        },
    )
    assert saved.status_code == 200
    root = _project_root(project["project_id"])
    project_store.delete_project(project["project_id"])

    first = client.post("/api/projects/video-localization/sync-projects")
    second = client.post("/api/projects/video-localization/sync-projects")

    assert first.status_code == 200
    assert [item["project_id"] for item in first.json()] == [project["project_id"]]
    assert [item["project_id"] for item in second.json()] == [project["project_id"]]
    recovered = project_store.get_project(project["project_id"])
    assert recovered is not None
    assert recovered.parameters[media_assets.PROJECT_DIR_NAME_KEY] == root.name
    operation = recovered.parameters["video_localization"]["operations"][0]
    assert operation["status"] == "failed"
    assert operation["error_code"] == "PROJECT_INDEX_RECOVERED"


def test_delete_project_api_removes_local_package_so_sync_cannot_restore_it(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "待删除本土化项目", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "delete-me.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200
    root = _project_root(project["project_id"])
    assert root.exists()

    deleted = client.delete(f"/api/projects/{project['project_id']}")
    synced = client.post("/api/projects/video-localization/sync-projects")

    assert deleted.status_code == 200
    assert not root.exists()
    assert project_store.get_project(project["project_id"]) is None
    assert project["project_id"] not in {item["project_id"] for item in synced.json()}


def test_video_localization_sync_hides_missing_directory_without_deleting_database_project(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "目录被删除", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "deleted.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200
    shutil.rmtree(_project_root(project["project_id"]))

    synced = client.post("/api/projects/video-localization/sync-projects")

    assert synced.status_code == 200
    assert synced.json() == []
    assert project_store.get_project(project["project_id"]) is not None
    opened = client.post(f"/api/projects/{project['project_id']}/video-localization/open-directory")
    assert opened.status_code == 410
    assert opened.json()["error"]["code"] == "VIDEO_LOCALIZATION_PROJECT_DIRECTORY_MISSING"
    assert not _project_root(project["project_id"]).exists()


def test_video_localization_sync_does_not_overwrite_existing_database_draft(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "磁盘名称", "description": ""}).json()
    saved = client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"source_media": {"filename": "disk.mp4", "duration_ms": 1200}},
    )
    assert saved.status_code == 200
    stored = project_store.get_project(project["project_id"])
    assert stored is not None
    stored.name = "数据库名称"
    stored.parameters["video_localization"]["source_media"]["filename"] = "database.mp4"
    project_store.save_project(stored)

    synced = client.post("/api/projects/video-localization/sync-projects")

    assert synced.status_code == 200
    assert synced.json()[0]["name"] == "数据库名称"
    current = project_store.get_project(project["project_id"])
    assert current is not None
    assert current.parameters["video_localization"]["source_media"]["filename"] == "database.mp4"


def test_video_localization_sync_rejects_unsafe_or_duplicate_packages(tmp_path: Path):
    client = _client(tmp_path)
    projects_root = tmp_path / "projects"
    unsafe_root = projects_root / "unsafe-package"
    unsafe_root.mkdir(parents=True)
    unsafe_manifest = {
        "kind": "video_localization_project",
        "project_id": "unsafe123456",
        "project_name": "不安全项目",
        "draft": {"source_media": {"filename": "unsafe.mp4", "video_path": "project://../outside.mp4"}},
    }
    (unsafe_root / "project.json").write_text(json.dumps(unsafe_manifest, ensure_ascii=False), encoding="utf-8")
    for directory_name in ("duplicate-a", "duplicate-b"):
        root = projects_root / directory_name
        root.mkdir()
        manifest = {
            "kind": "video_localization_project",
            "project_id": "duplicate123",
            "project_name": "重复项目",
            "draft": {"source_media": {"filename": "duplicate.mp4"}},
        }
        (root / "project.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    synced = client.post("/api/projects/video-localization/sync-projects")

    assert synced.status_code == 200
    assert synced.json() == []
    assert project_store.get_project("unsafe123456") is None
    assert project_store.get_project("duplicate123") is None


def test_video_localization_migrates_legacy_nested_project_and_rebases_paths(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "旧目录迁移", "description": ""}).json()
    legacy_root = tmp_path / "projects" / project["project_id"] / "video_localization"
    legacy_video = legacy_root / "source" / "legacy.mp4"
    legacy_video.parent.mkdir(parents=True, exist_ok=True)
    legacy_video.write_bytes(b"legacy-video")
    legacy_manifest = {
        "schema_version": 1,
        "kind": "video_localization_project",
        "project_id": project["project_id"],
        "project_name": "旧目录迁移",
        "draft": {
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "legacy.mp4", "video_path": str(legacy_video), "duration_ms": 1200},
        },
    }
    (legacy_root / "project.json").write_text(json.dumps(legacy_manifest, ensure_ascii=False), encoding="utf-8")
    legacy_autosave = legacy_root / "autosave" / "20260101-000000-000000-project.json"
    legacy_autosave.parent.mkdir(parents=True, exist_ok=True)
    legacy_autosave.write_text(json.dumps(legacy_manifest, ensure_ascii=False), encoding="utf-8")

    response = client.get(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 200
    migrated_root = _project_root(project["project_id"])
    migrated_video = migrated_root / "source" / "legacy.mp4"
    assert Path(response.json()["source_media"]["video_path"]) == migrated_video
    assert migrated_video.read_bytes() == b"legacy-video"
    assert not (tmp_path / "projects" / project["project_id"]).exists()
    assert migrated_root.name.endswith(f"--{project['project_id']}")

    saved = client.put(f"/api/projects/{project['project_id']}/video-localization", json=response.json())
    assert saved.status_code == 200
    portable_manifest = json.loads((migrated_root / "project.json").read_text(encoding="utf-8"))
    assert portable_manifest["draft"]["source_media"]["video_path"] == "project://source/legacy.mp4"
    migrated_autosave = migrated_root / "autosave" / legacy_autosave.name
    assert json.loads(migrated_autosave.read_text(encoding="utf-8"))["draft"]["source_media"]["video_path"] == "project://source/legacy.mp4"


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
    assert body["localized_subtitles"] == []
    assert body["ui_state"] == {}
    assert body["voice_recipes"] == []
    assert body["generated_candidates"] == []
    assert body["timeline_clips"] == []


def test_video_localization_import_localized_srt_updates_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "导入字幕", "description": ""}).json()
    draft = {
        "project_type": "video_localization",
        "schema_version": "v1",
        "cues": [
            {
                "cue_id": "cue_0001",
                "start_ms": 0,
                "end_ms": 1000,
                "en_subtitle_text": "Hello",
                "tts_recommended_text": "",
            },
            {
                "cue_id": "cue_0002",
                "start_ms": 1200,
                "end_ms": 2200,
                "en_subtitle_text": "World",
                "tts_recommended_text": "保留已有台词",
            },
        ],
    }
    client.put(f"/api/projects/{project['project_id']}/video-localization", json=draft)

    srt_text = """1
00:00:00,100 --> 00:00:01,100
你好

2
00:00:01,250 --> 00:00:02,150
世界
"""
    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={"srt_text": srt_text, "update_timing": True, "overwrite_tts": True},
    )

    assert response.status_code == 200
    body = response.json()
    cues = body["cues"]
    localized_subtitles = body["localized_subtitles"]
    assert cues[0]["start_ms"] == 0
    assert cues[0]["end_ms"] == 1000
    assert cues[0]["zh_localized_subtitle_text"] == "你好"
    assert cues[0]["tts_recommended_text"] == ""
    assert "zh_srt_import" in cues[0]["quality_flags"]
    assert cues[1]["zh_localized_subtitle_text"] == "世界"
    assert cues[1]["tts_recommended_text"] == "保留已有台词"
    assert [(cue["subtitle_id"], cue["linked_cue_id"]) for cue in localized_subtitles] == [
        ("subtitle_0001", "cue_0001"),
        ("subtitle_0002", "cue_0002"),
    ]
    assert [(cue["start_ms"], cue["end_ms"], cue["text"]) for cue in localized_subtitles] == [
        (100, 1100, "你好"),
        (1250, 2150, "世界"),
    ]
    quality_codes = {issue["code"] for issue in [*body["quality_gate"]["blockers"], *body["quality_gate"]["warnings"]]}
    assert "CUE_SPEAKER_MISSING" not in quality_codes
    assert "TTS_TEXT_MISSING" not in quality_codes

    fetched = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert fetched["cues"][0]["zh_localized_subtitle_text"] == "你好"
    assert fetched["localized_subtitles"][0]["subtitle_id"] == "subtitle_0001"


def test_video_localization_import_localized_srt_creates_empty_subtitle_track(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空轨导入字幕", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={
            "srt_text": """1
00:00:01,250 --> 00:00:02,800
第一句本土化字幕

2
00:00:03,100 --> 00:00:05,000
第二句本土化字幕
""",
            "update_timing": True,
            "overwrite_tts": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cues"] == []
    subtitles = body["localized_subtitles"]
    assert [subtitle["subtitle_id"] for subtitle in subtitles] == ["subtitle_0001", "subtitle_0002"]
    assert [(subtitle["start_ms"], subtitle["end_ms"]) for subtitle in subtitles] == [(1250, 2800), (3100, 5000)]
    assert [subtitle["text"] for subtitle in subtitles] == ["第一句本土化字幕", "第二句本土化字幕"]
    assert [subtitle["quality_flags"] for subtitle in subtitles] == [["zh_srt_import"], ["zh_srt_import"]]


def test_video_localization_import_tts_srt_creates_empty_subtitle_track(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空轨导入 TTS", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/tts/import",
        json={
            "srt_text": """1
00:00:00,500 --> 00:00:02,000
第一句配音台词

2
00:00:02,250 --> 00:00:03,700
第二句配音台词
""",
        },
    )

    assert response.status_code == 200
    cues = response.json()["cues"]
    assert [cue["cue_id"] for cue in cues] == ["cue_0001", "cue_0002"]
    assert [(cue["start_ms"], cue["end_ms"], cue["source_duration_ms"]) for cue in cues] == [
        (500, 2000, 1500),
        (2250, 3700, 1450),
    ]
    assert [cue["tts_recommended_text"] for cue in cues] == ["第一句配音台词", "第二句配音台词"]
    assert all(cue["zh_localized_subtitle_text"] is None for cue in cues)
    assert cues[0]["quality_flags"] == ["tts_srt_import"]


def test_video_localization_import_srt_rejects_invalid_payload(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "坏字幕", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={"project_type": "video_localization", "schema_version": "v1", "cues": [{"cue_id": "cue_0001"}]},
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={"srt_text": "not an srt"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_IMPORT_EMPTY"


def test_video_localization_import_localized_srt_rejects_track_overlap(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕重叠", "description": ""}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={
            "srt_text": """1
00:00:00,000 --> 00:00:01,500
第一句

2
00:00:01,200 --> 00:00:02,000
第二句
""",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_TRACK_OVERLAP"
    assert "时间重叠" in response.json()["error"]["message"]


def test_video_localization_reset_clears_draft_and_project_assets(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重置草稿", "description": ""}).json()
    project_dir = _project_root(project["project_id"])
    source_dir = project_dir / "source"
    stems_dir = project_dir / "stems"
    refs_dir = project_dir / "references"
    source_dir.mkdir(parents=True, exist_ok=True)
    stems_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)
    video_path = source_dir / "demo.mp4"
    audio_path = project_dir / "audio" / "demo-source.wav"
    vocals_path = stems_dir / "demo-vocals.wav"
    reference_path = refs_dir / "ref_001.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")
    audio_path.write_bytes(b"audio")
    vocals_path.write_bytes(b"vocals")
    reference_path.write_bytes(b"reference")

    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "video_path": str(video_path), "audio_path": str(audio_path)},
            "stems": {"original_audio_path": str(audio_path), "vocals_clean_path": str(vocals_path), "separation_status": "completed"},
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "reference_clips": [{"reference_clip_id": "ref_001", "speaker_id": "speaker_01", "audio_path": str(reference_path), "cleanliness": "clean", "asr_status": "verified"}],
            "cues": [{"cue_id": "cue_0001", "speaker_id": "speaker_01", "start_ms": 0, "end_ms": 1000, "en_subtitle_text": "Hello."}],
            "operations": [VideoLocalizationOperation(project_id=project["project_id"], kind="english_asr", status="queued", label="英文 ASR 转字幕").model_dump()],
        },
    )

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft"
    assert body["source_media"]["filename"] is None
    assert body["source_media"]["video_path"] is None
    assert body["stems"]["separation_status"] == "pending"
    assert body["speakers"] == []
    assert body["reference_clips"] == []
    assert body["cues"] == []
    assert body["operations"] == []
    assert not project_dir.exists()

    stored_project = client.get(f"/api/projects/{project['project_id']}").json()
    assert "video_localization" not in stored_project["parameters"]


def test_video_localization_reset_blocks_running_operations(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "重置阻断", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4"},
            "operations": [VideoLocalizationOperation(project_id=project["project_id"], kind="stems", status="running", label="分离人声与背景声").model_dump()],
        },
    )

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_RESET_BLOCKED"
    draft = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert draft["source_media"]["filename"] == "demo.mp4"
    assert draft["operations"][0]["status"] == "running"


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
    assert "EN_SUBTITLE_MISSING" in blocker_codes
    assert "CUE_SPEAKER_MISSING" not in blocker_codes


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


def test_video_localization_patch_localized_subtitle_updates_timing_without_overlap(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "局部保存字幕轨", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {"subtitle_id": "subtitle_0001", "start_ms": 0, "end_ms": 1000, "text": "第一句"},
                {"subtitle_id": "subtitle_0002", "start_ms": 1200, "end_ms": 2200, "text": "第二句"},
            ],
        },
    )

    response = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/localized-subtitles/subtitle_0002",
        json={"start_ms": 1000, "end_ms": 2100},
    )

    assert response.status_code == 200
    body = response.json()
    assert [(item["subtitle_id"], item["start_ms"], item["end_ms"]) for item in body["localized_subtitles"]] == [
        ("subtitle_0001", 0, 1000),
        ("subtitle_0002", 1000, 2100),
    ]


def test_video_localization_patch_localized_subtitle_rejects_overlap_and_too_short_duration(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕轨坏时间", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {"subtitle_id": "subtitle_0001", "start_ms": 0, "end_ms": 1000, "text": "第一句"},
                {"subtitle_id": "subtitle_0002", "start_ms": 1200, "end_ms": 2200, "text": "第二句"},
            ],
        },
    )

    overlap = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/localized-subtitles/subtitle_0002",
        json={"start_ms": 900},
    )
    too_short = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/localized-subtitles/subtitle_0002",
        json={"start_ms": 1200, "end_ms": 1220},
    )

    assert overlap.status_code == 400
    assert overlap.json()["error"]["code"] == "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_OVERLAP"
    assert "不能重叠" in overlap.json()["error"]["message"]
    assert too_short.status_code == 400
    assert too_short.json()["error"]["code"] == "VIDEO_LOCALIZATION_LOCALIZED_SUBTITLE_TOO_SHORT"


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
    assert body["source_media"]["content_sha256"] == media_assets.file_sha256(video_path)
    assert video_path.exists()
    assert video_path.name == "demo_clip.mp4"
    assert project["project_id"] in str(video_path)
    assert "导入视频" in str(video_path)


def test_video_localization_reimport_invalidates_source_derived_state(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "替换源视频", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "old.mp4", "metadata": {"english_asr_status": "completed"}},
            "stems": {"vocals_clean_path": "/tmp/old-vocals.wav", "separation_status": "completed"},
            "speakers": [{"speaker_id": "old-speaker"}],
            "cues": [{"cue_id": "old-cue", "start_ms": 0, "end_ms": 1000, "en_subtitle_text": "Old"}],
            "transcription": {"raw_text": "Old", "corrected_text": "Old"},
            "localized_subtitles": [{"subtitle_id": "old-zh", "start_ms": 0, "end_ms": 1000, "text": "旧字幕"}],
            "generated_candidates": [{"candidate_id": "old", "status": "ready"}],
            "timeline_clips": [{"clip_id": "old", "track_id": "dub"}],
            "ui_state": {"timeline_zoom": 4},
        },
    )
    monkeypatch.setattr(media_assets, "probe_video", lambda path: {"duration_ms": 2000})

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("new.mp4", b"new-video", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_media"]["filename"] == "new.mp4"
    assert body["source_media"]["metadata"] == {
        "content_type": "video/mp4",
        "upload_status": "stored",
        "probe_status": "completed",
    }
    assert body["stems"]["vocals_clean_path"] is None
    assert body["speakers"] == []
    assert body["cues"] == []
    assert body["transcription"] is None
    assert body["localized_subtitles"] == []
    assert body["generated_candidates"] == []
    assert body["timeline_clips"] == []
    assert body["ui_state"] == {"timeline_zoom": 4}


def test_video_localization_project_rename_moves_storage_and_paths(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "旧项目名", "description": ""}).json()
    monkeypatch.setattr(media_assets, "probe_video", lambda path: {"duration_ms": 3400})

    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/source-media",
        files={"file": ("demo.mp4", b"fake-video-bytes", "video/mp4")},
    ).json()
    old_video_path = Path(imported["source_media"]["video_path"])
    old_root = old_video_path.parents[1]
    assert old_root.name.endswith(f"--{project['project_id']}")
    assert "旧项目名" in str(old_root)

    updated = client.patch(f"/api/projects/{project['project_id']}", json={"name": "新项目名"}).json()
    assert updated["name"] == "新项目名"
    assert "新项目名" in updated["parameters"]["video_localization_dir_name"]

    fetched = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    new_video_path = Path(fetched["source_media"]["video_path"])
    assert new_video_path.exists()
    assert new_video_path.read_bytes() == b"fake-video-bytes"
    assert "新项目名" in str(new_video_path)
    assert str(new_video_path) != str(old_video_path)
    assert not old_root.exists()


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
    original_audio = _project_root(project["project_id"]) / "audio" / "fallback.wav"
    original_audio.parent.mkdir(parents=True, exist_ok=True)
    original_audio.write_bytes(b"fallback-audio")
    missing_audio = _project_root(project["project_id"]) / "audio" / "missing.wav"

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


def test_video_localization_media_original_timeline_waveform_uses_source_audio_at_high_density(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "原音波形", "description": ""}).json()
    audio_path = _project_root(project["project_id"]) / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 100
    duration_seconds = 665
    phase = np.linspace(0, np.pi * 40, sample_rate * duration_seconds, dtype=np.float32)
    stereo = np.column_stack((np.sin(phase), np.cos(phase)))
    sf.write(audio_path, stereo, sample_rate)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "audio_path": str(audio_path), "duration_ms": 665_000},
        },
    )

    audio = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_original/audio"
    )
    automatic = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_original/waveform"
    )
    explicit = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_original/waveform",
        params={"bins": 1501},
    )

    assert audio.status_code == 200
    assert automatic.status_code == 200
    assert automatic.json()["duration"] == 665.0
    assert automatic.json()["bins"] == 66_500
    assert len(automatic.json()["peaks"]) == 66_500
    assert explicit.status_code == 200
    assert explicit.json()["bins"] == 1501
    cache_files = list((tmp_path / "cache" / "waveforms").glob("*.json"))
    assert len(cache_files) == 2


def test_video_localization_timeline_waveform_defaults_to_minimum_bins_for_short_audio(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "短音频波形", "description": ""}).json()
    audio_path = tmp_path / "outputs" / "short.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(audio_path, np.ones(100, dtype=np.float32) * 0.25, 1000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "timeline_clips": [{"clip_id": "clip_short", "track_id": "dub", "audio_path": str(audio_path)}],
        },
    )

    response = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/clip_short/waveform"
    )
    too_many_bins = client.get(
        f"/api/projects/{project['project_id']}/video-localization/timeline-clips/clip_short/waveform",
        params={"bins": 180_001},
    )

    assert response.status_code == 200
    assert response.json()["duration"] == 0.1
    assert response.json()["bins"] == 32
    assert len(response.json()["peaks"]) == 32
    assert too_many_bins.status_code == 400


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
    assert body["source_media"]["audio_sha256"] == media_assets.file_sha256(body["source_media"]["audio_path"])
    assert body["stems"]["original_audio_path"] == body["source_media"]["audio_path"]
    assert body["stems"]["original_audio_sha256"] == body["source_media"]["audio_sha256"]

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
    if video_localization_operation_queue._queue is not None:
        video_localization_operation_queue._queue.join()


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
    video_path = _project_root(project["project_id"]) / "source" / "demo.mp4"
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
    video_path = _project_root(project["project_id"]) / "source" / "demo.mp4"
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
    audio_path = _project_root(project["project_id"]) / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-wav")
    stale_vocals = _project_root(project["project_id"]) / "stems" / "source-vocals-clean-old.wav"
    stale_background = _project_root(project["project_id"]) / "stems" / "source-background-old.wav"
    stale_vocals.parent.mkdir(parents=True, exist_ok=True)
    stale_vocals.write_bytes(b"old-vocals")
    stale_background.write_bytes(b"old-background")
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
        # Simulate frontend autosave while the long-running separator is busy.
        concurrent = video_localization_service.update_video_localization_ui_state(
            project["project_id"], {"timeline_zoom": 6, "sidebar_collapsed": True}
        )
        assert concurrent is not None
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
    assert body["stems"]["vocals_clean_sha256"] == media_assets.file_sha256(body["stems"]["vocals_clean_path"])
    assert body["stems"]["background_sha256"] == media_assets.file_sha256(body["stems"]["background_path"])
    assert body["ui_state"]["timeline_zoom"] == 6
    assert body["ui_state"]["sidebar_collapsed"] is True
    assert not stale_vocals.exists()
    assert not stale_background.exists()

    vocals = client.get(f"/api/projects/{project['project_id']}/video-localization/stems/vocals/audio")
    background = client.get(f"/api/projects/{project['project_id']}/video-localization/stems/background/audio")
    assert vocals.status_code == 200
    assert vocals.content == b"vocals"
    assert background.status_code == 200
    assert background.content == b"background"

    # Canonical media clips exist client-side before their autosave reaches the server.
    # Their audio routes must still resolve directly from the completed stem fields.
    vocals_clip = client.get(f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_vocals/audio")
    background_clip = client.get(f"/api/projects/{project['project_id']}/video-localization/timeline-clips/media_background/audio")
    assert vocals_clip.status_code == 200
    assert vocals_clip.content == b"vocals"
    assert background_clip.status_code == 200
    assert background_clip.content == b"background"


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
    audio_path = _project_root(project["project_id"]) / "audio" / "source.wav"
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
        assert engine_id == "qwen3-asr-mlx"
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
    assert body["source_media"]["metadata"]["english_asr_engine_id"] == "qwen3-asr-mlx"
    assert body["source_media"]["metadata"]["english_asr_source_track_id"] == "original"
    assert body["source_media"]["metadata"]["english_asr_segment_count"] == 1
    assert body["status"] == "blocked"
    assert [cue["en_subtitle_text"] for cue in body["cues"]] == ["We shipped the first localization pass"]
    assert body["cues"][0]["start_ms"] == 0
    assert body["cues"][0]["end_ms"] == 4200
    assert body["transcription"]["pipeline_timing"]["stages"]["subtitle_track"]["cue_count"] == 1
    assert "generated_by_asr" in body["cues"][0]["quality_flags"]
    blocker_codes = {issue["code"] for issue in body["quality_gate"]["blockers"]}
    assert "CUE_SPEAKER_MISSING" not in blocker_codes
    assert "ZH_SUBTITLE_MISSING" not in blocker_codes
    assert "TTS_TEXT_MISSING" not in blocker_codes


def test_video_localization_asr_merges_into_latest_autosaved_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "ASR 自动保存竞态", "description": ""}).json()
    initial = video_localization_service.save_video_localization(
        project["project_id"],
        video_localization_service.get_video_localization(project["project_id"]),
    )
    assert initial is not None

    def fake_asr(draft, engine_id, source_track_id, **_kwargs):
        latest = video_localization_service.get_video_localization(project["project_id"])
        assert latest is not None
        manual_cue = VideoLocalizationCue(
            cue_id="cue_0001",
            start_ms=1500,
            end_ms=2500,
            en_subtitle_text="Manual edit made while ASR was running.",
            review_status="ready",
        )
        autosaved = video_localization_service.save_video_localization(
            project["project_id"],
            latest.model_copy(
                update={
                    "ui_state": {"timeline_zoom": 7, "sidebar_collapsed": True},
                    "cues": [manual_cue],
                }
            ),
        )
        assert autosaved is not None
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    updated = video_localization_service.transcribe_english_source_audio(project["project_id"])

    assert updated is not None
    assert updated.ui_state == {"timeline_zoom": 7, "sidebar_collapsed": True}
    assert [cue.cue_id for cue in updated.cues] == ["cue_0002", "cue_0001"]
    assert updated.cues[0].en_subtitle_text == "Concurrent ASR result"
    assert updated.cues[1].en_subtitle_text == "Manual edit made while ASR was running."
    assert updated.source_media.metadata["english_asr_engine_id"] == "qwen3-asr-mlx"


def test_video_localization_operation_progress_merges_into_latest_draft(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "进度原子合并", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="running",
        label="听写字幕",
    )
    saved = video_localization_service.save_video_localization(
        project["project_id"],
        video_localization_service.get_video_localization(project["project_id"]).model_copy(
            update={"operations": [operation]}
        ),
    )
    assert saved is not None
    updated_ui = video_localization_service.update_video_localization_ui_state(
        project["project_id"],
        {"timeline_zoom": 9, "sidebar_collapsed": True},
    )
    assert updated_ui is not None

    video_localization_operation_queue._mark_operation(
        project["project_id"],
        operation.operation_id,
        kind="english_asr",
        status="running",
        progress=0.58,
        result_summary={"stage": "正在生成逐词时间码"},
    )

    latest = video_localization_service.get_video_localization(project["project_id"])
    assert latest is not None
    assert latest.ui_state == {"timeline_zoom": 9, "sidebar_collapsed": True}
    latest_operation = next(item for item in latest.operations if item.operation_id == operation.operation_id)
    assert latest_operation.progress == 0.58
    assert latest_operation.result_summary == {"stage": "正在生成逐词时间码"}


def test_asr_stage_timer_records_non_overlapping_step_durations():
    now = [10.0]
    timer = video_localization_operation_queue._AsrStageTimer(clock=lambda: now[0])

    now[0] = 12.5
    timer.update("正在判断是否需要联网核验")
    now[0] = 13.0
    timer.update("正在校对识别文本")
    now[0] = 16.25
    timings = timer.finish()

    assert timings == {
        "asr": {"duration_ms": 2500},
        "web_research": {"duration_ms": 500},
        "text_review": {"duration_ms": 3250},
    }
    assert sum(item["duration_ms"] for item in timings.values()) == 6250


def test_video_localization_operation_persists_asr_preview_phases(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "ASR 预览阶段", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="queued",
        label="听写字幕",
    )
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert video_localization_service.save_video_localization(
        project["project_id"], draft.model_copy(update={"operations": [operation]})
    ) is not None
    snapshots = []

    def fake_asr(draft, engine_id, source_track_id, *, preview_callback, **_kwargs):
        assert source_track_id == "auto"
        for phase, text in (
            ("asr_draft", "Raw draft"),
            ("text_review", "Reviewed draft"),
            ("timing_segmentation", "Timed cue"),
        ):
            preview_callback(
                phase,
                [{"cue_id": f"preview_{phase}", "start_ms": 100, "end_ms": 900, "text": text}],
            )
            current = video_localization_operation_queue.get_operation(
                project["project_id"], operation.operation_id
            )
            assert current is not None
            snapshots.append(current.result_summary.copy())
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    video_localization_operation_queue._process(operation.operation_id)

    assert [snapshot["preview_phase"] for snapshot in snapshots] == [
        "asr_draft",
        "text_review",
        "timing_segmentation",
    ]
    assert all(snapshot["stage"] == "准备处理" for snapshot in snapshots)
    assert snapshots[-1]["preview_cues"] == [
        {"cue_id": "preview_timing_segmentation", "start_ms": 100, "end_ms": 900, "text": "Timed cue"}
    ]
    completed = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert completed is not None and completed.status == "success"


def test_video_localization_cancelled_operation_rejects_late_preview_progress(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "取消后的预览", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="running",
        label="听写字幕",
        progress=0.42,
        result_summary={"stage": "正在识别人声内容"},
    )
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert video_localization_service.save_video_localization(
        project["project_id"], draft.model_copy(update={"operations": [operation]})
    ) is not None

    cancelled = video_localization_operation_queue.cancel(project["project_id"], operation.operation_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"

    video_localization_operation_queue._mark_operation(
        project["project_id"],
        operation.operation_id,
        kind="english_asr",
        status="running",
        progress=0.91,
        result_summary={
            "preview_phase": "text_review",
            "preview_cues": [{"cue_id": "late_preview", "start_ms": 0, "end_ms": 100, "text": "late"}],
        },
    )

    latest = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert latest is not None
    assert latest.status == "cancelled"
    assert latest.cancel_requested is True
    assert latest.progress == 0.42
    assert latest.result_summary == {"stage": "正在取消", "preview_cues": []}
    draft = video_localization_service.get_video_localization(project["project_id"])
    assert draft is not None
    assert draft.source_media.metadata["english_asr_status"] == "cancelled"


def test_video_localization_cancelled_asr_does_not_persist_result(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "取消 ASR 落盘", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="queued",
        label="听写字幕",
    )
    saved = video_localization_service.save_video_localization(
        project["project_id"],
        video_localization_service.get_video_localization(project["project_id"]).model_copy(
            update={"operations": [operation]}
        ),
    )
    assert saved is not None

    def fake_asr(draft, engine_id, source_track_id, **_kwargs):
        cancelled = video_localization_operation_queue.cancel(project["project_id"], operation.operation_id)
        assert cancelled is not None and cancelled.cancel_requested is True
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    video_localization_operation_queue._process(operation.operation_id)

    updated = video_localization_service.get_video_localization(project["project_id"])
    assert updated is not None
    completed = next(item for item in updated.operations if item.operation_id == operation.operation_id)
    assert completed.status == "cancelled"
    assert completed.cancel_requested is True
    assert updated.transcription is None
    assert updated.cues == []
    assert "english_asr_engine_id" not in updated.source_media.metadata


def test_video_localization_async_asr_defaults_to_qwen3_mlx(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "默认千问 ASR", "description": ""}).json()
    operation = VideoLocalizationOperation(
        project_id=project["project_id"],
        kind="english_asr",
        status="queued",
        label="听写字幕",
    )
    saved = video_localization_service.save_video_localization(
        project["project_id"],
        video_localization_service.get_video_localization(project["project_id"]).model_copy(
            update={"operations": [operation]}
        ),
    )
    assert saved is not None
    captured = {}

    def fake_asr(draft, engine_id, source_track_id, **_kwargs):
        captured["engine_id"] = engine_id
        return _completed_asr_result(draft, engine_id)

    monkeypatch.setattr(video_localization_source_pipeline, "with_english_asr", fake_asr)

    video_localization_operation_queue._process(operation.operation_id)

    assert captured["engine_id"] == "qwen3-asr-mlx"
    completed = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert completed is not None and completed.status == "success"


def test_video_localization_english_asr_empty_result_returns_chinese_guidance(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空识别结果", "description": ""}).json()
    audio_path = _project_root(project["project_id"]) / "audio" / "source.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-wav")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(audio_path), "duration_ms": 1800},
        },
    )
    monkeypatch.setattr(
        video_localization_source_pipeline.asr_service,
        "transcribe",
        lambda **_: {"text": "", "segments": []},
    )

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/asr/en")

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "VIDEO_LOCALIZATION_ASR_EMPTY",
        "message": "语音识别没有返回有效的字幕文本，请检查音轨内容或更换识别引擎后重试。",
        "detail": {},
    }


def test_video_localization_async_asr_uses_requested_vocals_track(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "人声轨转录", "description": ""}).json()
    project_root = _project_root(project["project_id"])
    original_path = project_root / "audio" / "source.wav"
    vocals_path = project_root / "stems" / "vocals.wav"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(b"original")
    vocals_path.write_bytes(b"vocals")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "demo.mp4", "audio_path": str(original_path), "duration_ms": 1800},
            "stems": {"vocals_clean_path": str(vocals_path), "separation_status": "completed"},
        },
    )

    def fake_transcribe(*, engine_id: str, audio_path: str, language: str):
        assert engine_id == "faster-whisper-turbo"
        assert Path(audio_path) == vocals_path
        assert language == "en"
        return {"segments": [{"start_ms": 0, "end_ms": 1800, "text": "Clean voice", "language": "en"}]}

    monkeypatch.setattr(video_localization_source_pipeline.asr_service, "transcribe", fake_transcribe)
    monkeypatch.setattr(video_localization_operation_queue, "_enqueue", lambda operation_id: None)
    progress_stages: list[str] = []
    original_mark_operation = video_localization_operation_queue._mark_operation

    def record_progress(*args, **kwargs):
        stage = (kwargs.get("result_summary") or {}).get("stage")
        if stage:
            progress_stages.append(stage)
        return original_mark_operation(*args, **kwargs)

    monkeypatch.setattr(video_localization_operation_queue, "_mark_operation", record_progress)

    operation = video_localization_operation_queue.submit(
        project["project_id"],
        "english_asr",
        {"engine_id": "faster-whisper-turbo", "source_track_id": "vocals"},
    )
    assert operation is not None
    assert operation.parameters["scope"] == {
        "area": "subtitle",
        "exclusive": True,
        "cancel_mode": "safe_point",
        "tracks": [
            {"id": "vocals", "role": "input"},
            {"id": "subtitles", "role": "output"},
        ],
    }
    video_localization_operation_queue._process(operation.operation_id)

    updated = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    completed = video_localization_operation_queue.get_operation(project["project_id"], operation.operation_id)
    assert completed is not None and completed.status == "success"
    assert updated["source_media"]["metadata"]["english_asr_source_track_id"] == "vocals"
    assert updated["source_media"]["metadata"]["english_asr_alignment_source_track_id"] == "original"
    assert updated["transcription"]["source_track_id"] == "vocals"
    assert updated["transcription"]["alignment_source_track_id"] == "original"
    assert updated["cues"][0]["en_subtitle_text"] == "Clean voice"
    assert progress_stages == [
        "准备处理",
        "正在识别人声内容",
        "正在判断是否需要联网核验",
        "正在校对识别文本",
        "正在生成逐词时间码",
        "正在分析停顿与声学边界",
        "正在复核字幕断句",
        "正在生成字幕轨",
    ]


def test_video_localization_reference_candidates_require_clean_vocals(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺干净人声", "description": ""}).json()

    response = client.post(f"/api/projects/{project['project_id']}/video-localization/reference-clips")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_CLEAN_VOCALS_MISSING"


def test_video_localization_reference_candidates_from_clean_vocals(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "参考音候选", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
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


def test_video_localization_reference_clip_can_save_current_selection_metadata(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "当前选区音色", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
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
                {"cue_id": "cue_0001", "speaker_id": "speaker_01", "start_ms": 1000, "end_ms": 2600, "en_subtitle_text": "First line."},
                {"cue_id": "cue_0002", "speaker_id": "speaker_01", "start_ms": 3000, "end_ms": 4600, "en_subtitle_text": "Second line."},
            ],
        },
    )

    def fake_cut(source_path: Path, destination: Path, start_ms: int, end_ms: int):
        assert source_path == vocals_path
        assert start_ms == 3000
        assert end_ms == 4600
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"selection")
        return destination

    monkeypatch.setattr(media_assets, "cut_audio_clip", fake_cut)
    monkeypatch.setattr(video_localization_reference_clips.audio_tools, "probe_audio", lambda path: {"duration_ms": 1600, "sample_rate": 24000, "channels": 1})

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/reference-clips",
        json={
            "cue_id": "cue_0002",
            "title": "室外开心",
            "person_name": "Alex",
            "emotion": "开心",
            "tags": ["室外", "开心", "室外"],
            "description": "适合开放空间台词",
            "cover_frame_path": "/tmp/frame.jpg",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["reference_clips"]) == 1
    clip = body["reference_clips"][0]
    assert clip["reference_clip_id"] == "ref_speaker_01_cue_0002"
    assert clip["title"] == "室外开心"
    assert clip["person_name"] == "Alex"
    assert clip["emotion"] == "开心"
    assert clip["tags"] == ["室外", "开心"]
    assert clip["description"] == "适合开放空间台词"
    assert clip["cover_frame_path"] == "/tmp/frame.jpg"
    assert body["cues"][0]["reference_clip_id"] is None
    assert body["cues"][1]["reference_clip_id"] == "ref_speaker_01_cue_0002"

    updated = client.patch(
        f"/api/projects/{project['project_id']}/video-localization/reference-clips/ref_speaker_01_cue_0002",
        json={"title": "室外更开心", "tags": ["户外", "近景"], "description": ""},
    )
    assert updated.status_code == 200
    updated_clip = updated.json()["reference_clips"][0]
    assert updated_clip["title"] == "室外更开心"
    assert updated_clip["tags"] == ["户外", "近景"]
    assert updated_clip["description"] is None


def test_video_localization_reference_clip_uses_independent_audio_selection(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "自由选区音色", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
    video_path = tmp_path / "source.mp4"
    vocals_path.parent.mkdir(parents=True, exist_ok=True)
    vocals_path.write_bytes(b"vocals")
    video_path.write_bytes(b"video")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"duration_ms": 10000, "video_path": str(video_path)},
            "stems": {"separation_status": "completed", "vocals_clean_path": str(vocals_path)},
            "speakers": [{"speaker_id": "speaker_01", "display_name": "A"}],
            "cues": [{"cue_id": "cue_0001", "speaker_id": "speaker_01", "start_ms": 1000, "end_ms": 2600, "en_subtitle_text": "Cue text."}],
        },
    )

    def fake_cut(source_path: Path, destination: Path, start_ms: int, end_ms: int):
        assert source_path == vocals_path
        assert start_ms == 4200
        assert end_ms == 6100
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"free-selection")
        return destination

    monkeypatch.setattr(media_assets, "cut_audio_clip", fake_cut)
    monkeypatch.setattr(video_localization_reference_clips.audio_tools, "probe_audio", lambda path: {"duration_ms": 1900, "sample_rate": 24000, "channels": 1})

    def fake_extract_frame(source_path: Path, destination: Path, at_ms: int):
        assert source_path == video_path
        assert at_ms == 5150
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"cover-frame")
        return destination

    monkeypatch.setattr(media_assets, "extract_video_frame", fake_extract_frame)

    response = client.post(
        f"/api/projects/{project['project_id']}/video-localization/reference-clips",
        json={
            "cue_id": "cue_0001",
            "speaker_id": "speaker_01",
            "start_ms": 4200,
            "end_ms": 6100,
            "asr_text": "Independent selection text.",
            "title": "自由选区",
        },
    )

    assert response.status_code == 200
    body = response.json()
    clip = body["reference_clips"][0]
    assert clip["reference_clip_id"] == "ref_speaker_01_4200_6100"
    assert clip["start_ms"] == 4200
    assert clip["end_ms"] == 6100
    assert clip["asr_text"] == "Independent selection text."
    assert clip["cover_frame_path"].endswith("ref_speaker_01_4200_6100.jpg")
    assert "generated_from_selection" in clip["quality_flags"]
    assert body["cues"][0]["start_ms"] == 1000
    assert body["cues"][0]["end_ms"] == 2600
    assert any(item["source"] == "manual_selection" and item["start_ms"] == 4200 and item["end_ms"] == 6100 for item in body["speakers"][0]["time_ranges"])
    cover = client.get(f"/api/projects/{project['project_id']}/video-localization/reference-clips/ref_speaker_01_4200_6100/cover")
    assert cover.status_code == 200
    assert cover.content == b"cover-frame"


def test_video_localization_delete_reference_clip_unbinds_cues_and_speakers(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "删除项目音色", "description": ""}).json()
    reference_path = _project_root(project["project_id"]) / "references" / "ref_001.wav"
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(b"reference")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "speakers": [{"speaker_id": "speaker_01", "reference_clip_ids": ["ref_001"]}],
            "reference_clips": [{"reference_clip_id": "ref_001", "speaker_id": "speaker_01", "audio_path": str(reference_path), "cleanliness": "needs_review"}],
            "cues": [{"cue_id": "cue_0001", "speaker_id": "speaker_01", "start_ms": 0, "end_ms": 1200, "reference_clip_id": "ref_001"}],
        },
    )

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization/reference-clips/ref_001")

    assert response.status_code == 200
    body = response.json()
    assert body["reference_clips"] == []
    assert body["cues"][0]["reference_clip_id"] is None
    assert body["speakers"][0]["reference_clip_ids"] == []
    assert reference_path.exists()


def test_video_localization_async_reference_clip_operation_updates_draft(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "异步参考音候选", "description": ""}).json()
    vocals_path = _project_root(project["project_id"]) / "stems" / "vocals.wav"
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
    assert cue["tts_recommended_text"] is None
    assert "localization_draft" in cue["quality_flags"]
    blocker_codes = {issue["code"] for issue in body["quality_gate"]["blockers"]}
    assert "ZH_SUBTITLE_PLACEHOLDER" in blocker_codes
    assert "TTS_TEXT_PLACEHOLDER" not in blocker_codes


def test_video_localization_chinese_draft_does_not_prepare_tts_text(tmp_path: Path):
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

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_LOCALIZATION_UNCHANGED"
    cue = client.get(f"/api/projects/{project['project_id']}/video-localization").json()["cues"][0]
    assert cue["zh_localized_subtitle_text"] == "1992 年，有 130 人加入。"
    assert cue["tts_recommended_text"] is None
    assert "tts_text_normalized" not in cue["quality_flags"]


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


def test_video_localization_subtitle_export_zh_prefers_localized_subtitle_track(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕轨优先导出", "description": ""}).json()
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
                    "en_subtitle_text": "In 1992, this changed everything.",
                    "zh_localized_subtitle_text": "旧的 cue 中文。",
                }
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "subtitle_0001",
                    "start_ms": 1500,
                    "end_ms": 2600,
                    "text": "新的独立字幕轨。",
                    "linked_cue_id": "cue_0001",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 200
    assert response.text == (
        "1\n"
        "00:00:01,500 --> 00:00:02,600\n"
        "新的独立字幕轨。\n"
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

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    assert response.json()["error"]["detail"]["issues"][0]["code"] == "CUE_TIMECODE_MISSING"


def test_video_localization_subtitle_export_blocks_empty_track_before_serialization(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "空字幕轨", "description": ""}).json()

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    assert response.json()["error"]["detail"]["issues"] == [
        {
            "code": "SUBTITLE_TRACK_EMPTY",
            "message": "字幕轨为空，没有可导出的字幕",
            "severity": "blocker",
            "cue_id": None,
            "speaker_id": None,
            "reference_clip_id": None,
        }
    ]


def test_video_localization_subtitle_export_blocks_low_confidence_timing(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "低置信时间", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 0,
                    "end_ms": 1500,
                    "en_subtitle_text": "Needs timing review.",
                    "timing_confidence": "low",
                    "quality_flags": ["timing_review_required"],
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/en")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    codes = {issue["code"] for issue in response.json()["error"]["detail"]["issues"]}
    assert "ASR_CUE_TIMING_LOW_CONFIDENCE" in codes


def test_video_localization_subtitle_export_does_not_skip_invalid_cue(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "禁止静默跳过", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000, "en_subtitle_text": "Valid."},
                {"cue_id": "cue_0002", "start_ms": 1000, "end_ms": 2000, "en_subtitle_text": ""},
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/en")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    issues = response.json()["error"]["detail"]["issues"]
    assert any(issue["code"] == "EN_SUBTITLE_MISSING" and issue["cue_id"] == "cue_0002" for issue in issues)


def test_video_localization_subtitle_export_does_not_skip_invalid_localized_track_entry(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "本土化字幕结构门禁", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "localized_subtitles": [
                {"subtitle_id": "subtitle_bad", "start_ms": 0, "end_ms": 0, "text": "不能被跳过"},
                {"subtitle_id": "subtitle_ok", "start_ms": 1000, "end_ms": 2000, "text": "有效字幕"},
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_BLOCKED"
    codes = {issue["code"] for issue in response.json()["error"]["detail"]["issues"]}
    assert "LOCALIZED_SUBTITLE_DURATION_INVALID" in codes


def test_video_localization_subtitle_export_blocks_hard_chinese_cue_quality(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "中文硬门禁", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "en_subtitle_text": "This is too fast.",
                    "zh_localized_subtitle_text": "一二三四五六七八九十甲乙丙",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/bilingual")

    assert response.status_code == 409
    issues = response.json()["error"]["detail"]["issues"]
    assert any(
        issue["code"] == "LOCALIZED_SUBTITLE_CPS_HARD_LIMIT" and issue["cue_id"] == "cue_0001"
        for issue in issues
    )


def test_video_localization_subtitle_export_allows_soft_chinese_quality_warning(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "中文软提示", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "en_subtitle_text": "Ten characters.",
                    "zh_localized_subtitle_text": "一二三四五六七八九十",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 200
    assert "一二三四五六七八九十" in response.text


def test_video_localization_subtitle_export_rejects_unsupported_kind(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "字幕类型", "description": ""}).json()

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/subtitles/ass")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_KIND_UNSUPPORTED"


def test_video_localization_timeline_edl_export_includes_clips_and_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "时间线 EDL", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 4200},
            "stems": {"original_audio_path": "/tmp/source.wav"},
            "ui_state": {"track_states": {"dub": {"muted": False, "solo": True, "volume": 0.8}}},
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 100,
                    "end_ms": 1200,
                    "en_subtitle_text": "Hello",
                    "zh_localized_subtitle_text": "你好",
                    "tts_recommended_text": "你好",
                    "tts_audio_path": "/tmp/tts.wav",
                    "audio_route": "clone_from_source",
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_001",
                    "cue_id": "cue_0001",
                    "candidate_id": "candidate_001",
                    "track_id": "dub",
                    "start_ms": 100,
                    "end_ms": 1200,
                    "source_start_ms": 0,
                    "source_end_ms": 1100,
                    "audio_path": "/tmp/tts.wav",
                    "status": "ready",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/export/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "video_localization_timeline_edl"
    assert body["project_name"] == "时间线 EDL"
    assert body["duration_ms"] == 4200
    assert body["track_states"]["dub"]["solo"] is True
    assert body["timeline_clips"][0]["clip_id"] == "clip_001"
    assert body["cues"][0]["localized_text"] == "你好"


def test_video_localization_timeline_audio_package_exports_segments_and_manifest(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "时间线音频包", "description": ""}).json()
    tts_path = tmp_path / "tts" / "cue_0001.wav"
    samples = np.sin(np.linspace(0, np.pi * 8, 24000, endpoint=False)).astype(np.float32) * 0.2
    audio_tools.write_audio(tts_path, samples, 24000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 2500},
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 500,
                    "end_ms": 1500,
                    "tts_audio_path": str(tts_path),
                    "generated_duration_ms": 1000,
                    "audio_route": "clone_from_source",
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_001",
                    "cue_id": "cue_0001",
                    "track_id": "dub",
                    "start_ms": 500,
                    "end_ms": 1300,
                    "source_start_ms": 100,
                    "source_end_ms": 900,
                    "audio_path": str(tts_path),
                    "status": "ready",
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/export/timeline/audio-package")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "dub-track.wav" in names
        assert "segments/001_clip_001.wav" in names
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["kind"] == "video_localization_timeline_audio_package"
    assert manifest["segments"][0]["timeline_start_ms"] == 500
    assert manifest["segments"][0]["source_start_ms"] == 100
    assert manifest["missing_segments"] == []
    stored = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert stored["exports"]["timeline_audio_package_path"].endswith(".zip")
    assert Path(stored["exports"]["timeline_audio_package_path"]).exists()


def test_video_localization_audio_package_preserves_original_voice_route(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "保留原声片段", "description": ""}).json()
    vocals_path = tmp_path / "stems" / "vocals.wav"
    samples = np.sin(np.linspace(0, np.pi * 6, 48000, endpoint=False)).astype(np.float32) * 0.18
    audio_tools.write_audio(vocals_path, samples, 48000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 1000},
            "stems": {"separation_status": "completed", "vocals_clean_path": str(vocals_path)},
            "cues": [{"cue_id": "cue_0001", "start_ms": 200, "end_ms": 700, "audio_route": "preserve_original_audio"}],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/export/timeline/audio-package")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = set(archive.namelist())
    assert "segments/001_preserve_cue_0001.wav" in names
    assert manifest["segments"][0]["audio_route"] == "preserve_original_audio"
    assert manifest["segments"][0]["source_start_ms"] == 200
    assert manifest["segments"][0]["source_end_ms"] == 700


def test_video_localization_timeline_audio_package_rejects_missing_sources(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "缺少音频包", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 2500},
            "cues": [{"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000}],
            "timeline_clips": [
                {
                    "clip_id": "clip_missing",
                    "cue_id": "cue_0001",
                    "track_id": "dub",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "audio_path": str(tmp_path / "missing.wav"),
                }
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/export/timeline/audio-package")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_RENDER_AUDIO_MISSING"


def test_video_localization_localized_video_export_returns_file(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "合成视频", "description": ""}).json()
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"fake-video")
    tts_path = tmp_path / "tts" / "cue_0001.wav"
    samples = np.sin(np.linspace(0, np.pi * 8, 24000, endpoint=False)).astype(np.float32) * 0.2
    audio_tools.write_audio(tts_path, samples, 24000)

    def fake_mux(source_path: Path, dub_path: Path, background_path: Path | None, destination: Path):
        assert source_path == source_video
        assert dub_path.exists()
        assert background_path is None
        mixed_audio, _ = audio_tools.read_audio(dub_path)
        assert 0.09 <= float(np.max(np.abs(mixed_audio))) <= 0.11
        destination.write_bytes(b"localized-video")

    monkeypatch.setattr(video_localization_exporting, "_mux_localized_video", fake_mux)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 1200, "video_path": str(source_video)},
            "ui_state": {"track_states": {"dub": {"muted": False, "solo": True, "volume": 0.5}}},
            "cues": [{"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000, "audio_route": "clone_from_source", "tts_audio_path": str(tts_path), "generated_duration_ms": 1000}],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/export/timeline/video")

    assert response.status_code == 200
    assert response.content == b"localized-video"
    stored = client.get(f"/api/projects/{project['project_id']}/video-localization").json()
    assert stored["exports"]["localized_video_path"].endswith("localized-video.mp4")
    assert Path(stored["exports"]["localized_video_path"]).exists()


def test_video_localization_mixdown_respects_editable_background_clip(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "背景片段裁切", "description": ""}).json()
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"fake-video")
    background_path = tmp_path / "background.wav"
    audio_tools.write_audio(background_path, np.full(48000 * 2, 0.2, dtype=np.float32), 48000)
    tts_path = tmp_path / "tts.wav"
    audio_tools.write_audio(tts_path, np.zeros(24000, dtype=np.float32), 24000)

    def fake_mux(source_path: Path, mixdown_path: Path, background_path_arg: Path | None, destination: Path):
        assert source_path == source_video
        assert background_path_arg is None
        mixed_audio, sample_rate = audio_tools.read_audio(mixdown_path)
        assert float(np.max(np.abs(mixed_audio[: int(sample_rate * 0.4)]))) < 0.001
        assert 0.19 <= float(np.max(np.abs(mixed_audio[int(sample_rate * 0.55) : int(sample_rate * 0.9)]))) <= 0.21
        assert float(np.max(np.abs(mixed_audio[int(sample_rate * 1.1) :]))) < 0.001
        destination.write_bytes(b"localized-video")

    monkeypatch.setattr(video_localization_exporting, "_mux_localized_video", fake_mux)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 1500, "video_path": str(source_video)},
            "stems": {"background_path": str(background_path), "separation_status": "completed"},
            "ui_state": {"track_states": {"background": {"muted": False, "solo": True, "volume": 1.0}}},
            "cues": [{"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000, "audio_route": "clone_from_source", "tts_audio_path": str(tts_path)}],
            "timeline_clips": [
                {
                    "clip_id": "media_background",
                    "track_id": "background",
                    "start_ms": 500,
                    "end_ms": 1000,
                    "source_start_ms": 200,
                    "source_end_ms": 700,
                    "audio_path": str(background_path),
                },
                {"clip_id": "dub_0001", "cue_id": "cue_0001", "track_id": "dub", "start_ms": 0, "end_ms": 1000, "audio_path": str(tts_path)},
            ],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/export/timeline/video")

    assert response.status_code == 200, response.json()


def test_video_localization_localized_video_export_rejects_missing_source_video(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "无源视频", "description": ""}).json()
    tts_path = tmp_path / "tts" / "cue_0001.wav"
    samples = np.sin(np.linspace(0, np.pi * 8, 24000, endpoint=False)).astype(np.float32) * 0.2
    audio_tools.write_audio(tts_path, samples, 24000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {"filename": "source.mp4", "duration_ms": 1200, "video_path": str(tmp_path / "missing.mp4")},
            "cues": [{"cue_id": "cue_0001", "start_ms": 0, "end_ms": 1000, "tts_audio_path": str(tts_path), "generated_duration_ms": 1000}],
        },
    )

    response = client.get(f"/api/projects/{project['project_id']}/video-localization/export/timeline/video")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_RENDER_SOURCE_VIDEO_MISSING"


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
            "generated_candidates": [
                {
                    "candidate_id": "candidate_task-video-single",
                    "recipe_id": "recipe_001",
                    "reference_clip_id": "ref_001",
                    "cue_id": "cue_0001",
                    "task_id": "task-video-single",
                    "audio_path": None,
                    "duration_ms": None,
                    "status": "queued",
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_candidate_task-video-single",
                    "cue_id": "cue_0001",
                    "candidate_id": "candidate_task-video-single",
                    "track_id": "dub",
                    "start_ms": 1000,
                    "end_ms": 3200,
                    "source_start_ms": 0,
                    "source_end_ms": None,
                    "audio_path": None,
                    "status": "queued",
                }
            ],
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
    adopted_path = _project_root(project["project_id"]) / "tts" / "cue_0001" / "task-video-single.wav"
    assert cue["tts_result_id"] == "result-video-single"
    assert cue["tts_audio_path"] == str(adopted_path)
    assert adopted_path.read_bytes() == b"single-audio"
    assert output_path.exists()
    assert cue["generated_duration_ms"] == 2300
    candidate = response.json()["generated_candidates"][0]
    assert candidate["result_id"] == "result-video-single"
    assert candidate["audio_path"] == str(adopted_path)
    assert candidate["duration_ms"] == 2300
    assert candidate["status"] == "success"
    clip = response.json()["timeline_clips"][0]
    assert clip["audio_path"] == str(adopted_path)
    assert clip["source_end_ms"] == 2300
    assert clip["status"] == "ready"

    task_queue._sync_video_localization_tts_result(task, hist)
    assert list(adopted_path.parent.glob("task-video-single*.wav")) == [adopted_path]


def test_video_localization_candidate_can_be_previewed_and_applied(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "候选试听与采用", "description": ""}).json()
    first_path = tmp_path / "outputs" / "first.wav"
    second_path = tmp_path / "outputs" / "second.wav"
    first_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_bytes(b"first-audio")
    second_path.write_bytes(b"second-audio")
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [{"cue_id": "cue_0001", "start_ms": 1000, "end_ms": 3000, "tts_recommended_text": "候选台词。", "tts_audio_path": str(first_path)}],
            "generated_candidates": [
                {"candidate_id": "candidate_first", "recipe_id": "recipe_001", "cue_id": "cue_0001", "audio_path": str(first_path), "duration_ms": 1800, "status": "success"},
                {"candidate_id": "candidate_second", "recipe_id": "recipe_001", "cue_id": "cue_0001", "audio_path": str(second_path), "duration_ms": 2100, "status": "success"},
            ],
            "timeline_clips": [{"clip_id": "clip_cue_0001", "cue_id": "cue_0001", "candidate_id": "candidate_first", "track_id": "dub", "start_ms": 1000, "end_ms": 3000, "audio_path": str(first_path), "status": "ready"}],
        },
    )

    preview = client.get(f"/api/projects/{project['project_id']}/video-localization/candidates/candidate_second/audio")
    assert preview.status_code == 200
    assert preview.content == b"second-audio"

    applied = client.post(f"/api/projects/{project['project_id']}/video-localization/candidates/candidate_second/apply")
    assert applied.status_code == 200
    body = applied.json()
    assert body["cues"][0]["tts_audio_path"] == str(second_path)
    assert body["cues"][0]["generated_duration_ms"] == 2100
    assert body["timeline_clips"][0]["candidate_id"] == "candidate_second"
    assert body["timeline_clips"][0]["audio_path"] == str(second_path)
    assert body["generated_candidates"][0]["selected"] is False
    assert body["generated_candidates"][1]["selected"] is True
    clip_audio = client.get(f"/api/projects/{project['project_id']}/video-localization/timeline-clips/clip_cue_0001/audio")
    assert clip_audio.status_code == 200
    assert clip_audio.content == b"second-audio"


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


def test_video_localization_clear_asr_subtitle_track_is_atomic_and_idempotent(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "清空 ASR 字幕", "description": ""}).json()
    base = _completed_asr_result(VideoLocalizationDraft())
    payload = base.model_dump(mode="json")
    payload["source_media"]["metadata"]["keep_me"] = "yes"
    payload["cues"] = [
        {
            "cue_id": "cue_0001",
            "start_ms": 0,
            "end_ms": 1200,
            "en_subtitle_text": "Clear this subtitle.",
            "quality_flags": ["generated_by_asr"],
        }
    ]
    payload["localized_subtitles"] = [
        {
            "subtitle_id": "subtitle_0001",
            "start_ms": 0,
            "end_ms": 1200,
            "text": "保留本土化字幕",
            "linked_cue_id": "cue_0001",
        }
    ]
    video_localization_service.save_video_localization(project["project_id"], VideoLocalizationDraft.model_validate(payload))

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/en")

    assert response.status_code == 200
    body = response.json()
    assert body["cues"] == []
    assert body["transcription"] is None
    assert body["source_media"]["metadata"] == {"keep_me": "yes"}
    assert body["localized_subtitles"][0]["text"] == "保留本土化字幕"
    assert body["localized_subtitles"][0]["linked_cue_id"] is None
    assert body["ui_state"]["selected_cue_id"] == ""
    assert client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/en").status_code == 200


def test_video_localization_clear_localized_subtitle_track_preserves_asr_cues(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "清空本土化字幕", "description": ""}).json()
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "en_subtitle_text": "Keep the ASR cue.",
                }
            ],
        },
    )
    imported = client.post(
        f"/api/projects/{project['project_id']}/video-localization/subtitles/zh/import",
        json={"srt_text": "1\n00:00:00,100 --> 00:00:00,900\n保留镜像文案\n"},
    )
    assert imported.status_code == 200
    assert imported.json()["localized_subtitles"]

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh")

    assert response.status_code == 200
    body = response.json()
    assert body["localized_subtitles"] == []
    assert body["cues"][0]["en_subtitle_text"] == "Keep the ASR cue."
    assert body["cues"][0]["zh_localized_subtitle_text"] == "保留镜像文案"
    assert client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/zh").status_code == 200


def test_video_localization_clear_asr_track_blocks_active_transcription(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "运行中字幕", "description": ""}).json()
    draft = VideoLocalizationDraft(
        operations=[
            VideoLocalizationOperation(
                project_id=project["project_id"],
                kind="english_asr",
                status="running",
            )
        ]
    )
    video_localization_service.save_video_localization(project["project_id"], draft)

    response = client.delete(f"/api/projects/{project['project_id']}/video-localization/subtitles/en")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_SUBTITLE_CLEAR_BLOCKED"
