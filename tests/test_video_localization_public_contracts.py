from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_pipeline,
    operation_queue,
    speaker_diarization,
    transcription,
)
from app.domains.video_localization import service as video_localization_service  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    VideoLocalizationOperation,
)
from app.services import database, settings_store  # noqa: E402
from app.services import video_localization_operations  # noqa: E402


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


def test_public_project_draft_and_operation_responses_hide_internal_artifact_locators(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "公开契约隔离"}).json()
    project_id = project["project_id"]
    internal_path = "/Users/example/private/development-asr-result.json"
    operation = VideoLocalizationOperation(
        operation_id="operation_with_internal_locator",
        project_id=project_id,
        kind="english_asr",
        status="running",
        result_summary={
            "artifact_available": True,
            "artifact_path": internal_path,
            "debug": {"snapshot_path": internal_path},
            "error_detail": {"artifact_path": internal_path},
        },
        parameters={"development_snapshot_path": internal_path},
    )
    draft = video_localization_service.get_video_localization(project_id)
    assert draft is not None
    saved = video_localization_service.save_video_localization(
        project_id,
        draft.model_copy(
            update={
                "source_media": draft.source_media.model_copy(
                    update={
                        "filename": "source.webm",
                        "video_path": internal_path,
                        "audio_path": internal_path,
                    }
                ),
                "stems": draft.stems.model_copy(
                    update={
                        "original_audio_path": internal_path,
                        "vocals_clean_path": internal_path,
                        "background_path": internal_path,
                    }
                ),
                "timeline_clips": [
                    {
                        "clip_id": "media_original",
                        "track_id": "original",
                        "audio_path": internal_path,
                    }
                ],
                "operations": [operation],
            }
        ),
    )
    assert saved is not None
    promotion = video_localization_operations.promote_operation_summaries(limit=10)
    assert promotion.verified_project_count == 0
    assert promotion.unchanged_project_count == 1
    video_localization_operations._operation_feed_reader.clear()

    responses = [
        client.get("/api/projects"),
        client.get(f"/api/projects/{project_id}"),
        client.get(f"/api/projects/{project_id}/video-localization"),
        client.patch(
            f"/api/projects/{project_id}/video-localization/ui-state",
            json={"playhead_ms": 250},
        ),
        client.get(f"/api/projects/{project_id}/video-localization/operations"),
        client.get(f"/api/projects/{project_id}/video-localization/operations/feed-v2"),
        client.get(f"/api/projects/{project_id}/video-localization/operations/{operation.operation_id}"),
        client.get(f"/api/projects/{project_id}/video-localization/export"),
    ]

    for response in responses:
        assert response.status_code == 200
        assert "artifact_path" not in response.text
        assert "snapshot_path" not in response.text
        assert "development_snapshot_path" not in response.text
        assert internal_path not in response.text

    public_draft = responses[2].json()
    assert public_draft["source_media"]["video_path"] is None
    assert public_draft["source_media"]["audio_path"] is None
    assert public_draft["stems"]["vocals_clean_path"] is None
    assert (
        public_draft["timeline_clips"][0]["media_source_clip_id"]
        == "media_original"
    )
    assert "audio_path" not in public_draft["timeline_clips"][0]

    ui_patch_result = responses[3].json()
    assert ui_patch_result["schema_version"] == (
        "video-localization-ui-state-patch-v1"
    )
    assert ui_patch_result["ui_state_patch"] == {"playhead_ms": 250}
    assert "operations" not in ui_patch_result
    assert "tts_tasks" not in ui_patch_result

    ui_patch_draft = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    ).json()["draft"]
    saved_draft = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=ui_patch_draft,
    )
    assert saved_draft.status_code == 200
    assert saved_draft.json()["operations"] == []
    assert saved_draft.json()["tts_tasks"] == []

    detail = responses[6].json()
    assert detail["result_summary"]["artifact_available"] is True
    persisted = operation_queue.get_operation(project_id, operation.operation_id)
    assert persisted is not None
    assert persisted.result_summary["artifact_path"] == internal_path
    internal_draft = video_localization_service.get_video_localization(
        project_id
    )
    assert internal_draft is not None
    assert internal_draft.source_media.video_path == internal_path
    assert internal_draft.stems.vocals_clean_path == internal_path


def test_public_draft_and_client_save_share_one_frame_aligned_audio_range(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "帧级音频范围"},
    ).json()
    project_id = project["project_id"]
    draft = video_localization_service.get_video_localization(project_id)
    assert draft is not None
    saved = video_localization_service.save_video_localization(
        project_id,
        draft.model_copy(
            update={
                "source_media": draft.source_media.model_copy(
                    update={
                        "duration_ms": 5_416_668,
                        "frame_rate": 24,
                    }
                ),
                "timeline_clips": [
                    {
                        "clip_id": "clip-current",
                        "track_id": "dub",
                        "start_ms": 203_708,
                        "end_ms": 216_250,
                        "source_start_ms": 375,
                        "source_end_ms": 12_875,
                        "audio_path": str(tmp_path / "clip.wav"),
                    }
                ],
            }
        ),
    )
    assert saved is not None

    public = client.get(
        f"/api/projects/{project_id}/video-localization"
    )
    assert public.status_code == 200
    public_clip = public.json()["timeline_clips"][0]
    assert (
        public_clip["start_ms"],
        public_clip["end_ms"],
        public_clip["source_start_ms"],
        public_clip["source_end_ms"],
    ) == (203_708, 216_208, 375, 12_875)

    persisted = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=public.json(),
    )
    assert persisted.status_code == 200
    internal = video_localization_service.get_video_localization(
        project_id
    )
    assert internal is not None
    assert internal.timeline_clips[0]["end_ms"] == 216_208
    assert (
        internal.timeline_clips[0]["end_ms"]
        - internal.timeline_clips[0]["start_ms"]
    ) == (
        internal.timeline_clips[0]["source_end_ms"]
        - internal.timeline_clips[0]["source_start_ms"]
    )


def test_corrupt_authoritative_summary_returns_explicit_api_repair_error(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "损坏摘要错误"},
    ).json()
    project_id = project["project_id"]
    draft = video_localization_service.get_video_localization(project_id)
    assert draft is not None
    operation = VideoLocalizationOperation(
        operation_id="corrupt-summary",
        project_id=project_id,
        kind="source_audio",
        status="success",
        completed_at="2026-07-30T03:00:00+00:00",
    )
    saved = video_localization_service.save_video_localization(
        project_id,
        draft.model_copy(update={"operations": [operation]}),
    )
    assert saved is not None
    promotion = video_localization_operations.promote_operation_summaries(limit=10)
    assert promotion.verified_project_count == 0
    assert promotion.unchanged_project_count == 1
    with database.conn() as connection:
        connection.execute(
            """
            UPDATE video_localization_operation_summaries
            SET core_json = '{"broken":true}'
            WHERE project_id = ?
            """,
            (project_id,),
        )
    video_localization_operations._operation_feed_reader.clear()

    response = client.get(f"/api/projects/{project_id}/video-localization/operations/feed-v2")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ("VIDEO_LOCALIZATION_OPERATION_SUMMARY_REPAIR_REQUIRED")


def test_development_result_responses_hide_source_audio_path(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "开发结果公开契约"}).json()
    project_id = project["project_id"]
    internal_path = "/Users/example/private/source-audio.wav"
    raw = transcription.build_transcribe_raw_output(
        input=transcription.TranscribeRawInput(
            audio_path=internal_path,
            audio_sha256="a" * 64,
            engine_id="test-asr",
            source_track_id="original",
            requested_language="en",
            duration_ms=1000,
        ),
        raw_text="Hello.",
        language="en",
    )
    diarization = speaker_diarization.DiarizeSpeakersOutput(
        input=speaker_diarization.DiarizeSpeakersInput(
            audio_path=internal_path,
            audio_sha256="a" * 64,
            source_track_id="original",
            engine_id="test-diarization",
            duration_ms=1000,
        ),
        status="completed",
        engine_id="test-diarization",
        count_guidance=speaker_diarization.SpeakerCountGuidanceResult(
            detected_speaker_count=0,
            engine_applied=False,
            usage="automatic",
            evaluation="automatic",
        ),
        quality_summary=speaker_diarization.SpeakerDiarizationQualitySummary(
            status="warning",
            segment_count=0,
            cluster_count=0,
            overlap_segment_count=0,
            covered_duration_ms=0,
            audio_duration_ms=1000,
            coverage_ratio=0,
            warning_codes=["NO_SPEECH"],
        ),
    )
    snapshot = asr_pipeline.AsrInitialAnalysisSnapshot(
        analysis=asr_pipeline.AsrInitialAnalysisResult(
            raw_asr=raw,
            diarization=diarization,
        ),
        joined_transcript=asr_pipeline.AsrJoinedTranscript(
            raw_asr=raw,
            diarization=diarization,
        ),
    )
    monkeypatch.setattr(
        operation_queue,
        "get_development_asr_result",
        lambda _project_id, _operation_id: raw,
    )
    monkeypatch.setattr(
        operation_queue,
        "get_development_diarization_result",
        lambda _project_id, _operation_id: diarization,
    )
    monkeypatch.setattr(
        operation_queue,
        "get_development_initial_analysis_result",
        lambda _project_id, _operation_id: snapshot,
    )

    responses = [
        client.get(f"/api/projects/{project_id}/video-localization/operations/op/development-asr-result"),
        client.get(f"/api/projects/{project_id}/video-localization/operations/op/development-diarization-result"),
        client.get(f"/api/projects/{project_id}/video-localization/operations/op/development-initial-analysis-result"),
    ]

    for response in responses:
        assert response.status_code == 200
        assert "audio_path" not in response.text
        assert internal_path not in response.text
