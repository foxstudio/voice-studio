from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    dubbing_production,
    media_assets,
    project_snapshot_projection,
    service,
)
from app.main import app  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    VideoLocalizationDraft,
    VideoLocalizationOperation,
    VideoLocalizationTtsTask,
)
from app.schemas.video_localization_dubbing_production import (  # noqa: E402
    DubbingCandidateCqcInput,
    DubbingCandidateCqcReport,
    DubbingTranscriptComparison,
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


def test_workspace_revision_is_small_and_changes_with_content(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "实时工作区"},
    ).json()
    project_id = project["project_id"]

    first = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-revision"
    )
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            localized_subtitles=[{
                "subtitle_id": "localized-live",
                "start_ms": 1_000,
                "end_ms": 2_000,
                "text": "实时更新",
            }]
        ),
    )
    second = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-revision"
    )

    assert saved is not None
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["revision"] != second.json()["revision"]
    assert len(second.content) < 100


def test_workspace_payload_is_bound_to_the_revision_read_with_its_draft(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "版本绑定工作区"},
    ).json()["project_id"]

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )
    revision = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-revision"
    )

    assert workspace.status_code == 200
    assert revision.status_code == 200
    assert workspace.json()["revision"] == revision.json()["revision"]


def test_interactive_localized_subtitle_edit_returns_bounded_revisioned_delta(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "局部字幕保存"},
    ).json()["project_id"]
    service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            cues=[{
                "cue_id": "cue-localized-edit",
                "start_ms": 1_000,
                "end_ms": 4_000,
                "en_subtitle_text": "Edit",
            }],
            localized_subtitles=[{
                "subtitle_id": "localized-edit",
                "linked_cue_id": "cue-localized-edit",
                "source_cue_ids": ["cue-localized-edit"],
                "start_ms": 1_000,
                "end_ms": 2_000,
                "text": "编辑",
            }],
            timeline_clips=[{
                "clip_id": "clip-ready",
                "track_id": "dub",
                "subtitle_id": "localized-edit",
                "start_ms": 1_000,
                "end_ms": 2_000,
                "audio_path": str(tmp_path / "ready.wav"),
                "status": "ready",
            }, {
                "clip_id": "clip-pending",
                "track_id": "dub",
                "subtitle_id": "localized-edit",
                "start_ms": 1_000,
                "end_ms": 2_000,
                "audio_path": None,
                "status": "running",
                "optimistic_tts_workflow_id": "workflow-pending",
            }],
        ),
    )
    background_flushes: list[str] = []
    monkeypatch.setattr(
        project_snapshot_projection,
        "flush",
        lambda value: background_flushes.append(value) or True,
    )

    response = client.patch(
        f"/api/projects/{project_id}/video-localization/"
        "localized-subtitles/localized-edit/edit",
        json={
            "start_ms": 1_000,
            "end_ms": 2_800,
            "text": "最终上屏字幕",
            "tts_text": "最终配音台词",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == project_store.get_project_repository_revision(project_id)
    assert body["affected_clip_ids"] == ["clip-pending", "clip-ready"]
    assert [item["clip_id"] for item in body["timeline_clips"]] == ["clip-ready"]
    assert body["localized_subtitles"][0]["end_ms"] == 2_800
    assert body["localized_subtitles"][0]["text"] == "最终上屏字幕"
    assert body["localized_subtitles"][0]["tts_text"] == "最终配音台词"
    assert body["cues"][0]["cue_id"] == "cue-localized-edit"
    assert len(response.content) < 20_000
    assert background_flushes == [project_id]
    stored = service.get_video_localization(project_id)
    assert stored is not None
    assert stored.localized_subtitles[0].end_ms == 2_800
    assert stored.localized_subtitles[0].text == "最终上屏字幕"
    assert stored.localized_subtitles[0].tts_text == "最终配音台词"


def test_workspace_revision_changes_when_runtime_places_a_timeline_clip(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "后台结果实时上屏"},
    ).json()
    project_id = project["project_id"]
    before = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-revision"
    ).json()["revision"]

    saved = service.update_video_localization_atomic(
        project_id,
        lambda current: current.model_copy(
            update={
                "timeline_clips": [
                    *current.timeline_clips,
                    {
                        "clip_id": "clip-background-result",
                        "track_id": "dub",
                        "start_ms": 2_000,
                        "end_ms": 3_000,
                        "audio_path": str(
                            tmp_path / "outputs" / "background-result.wav"
                        ),
                    },
                ]
            }
        ),
        intent="runtime",
    )
    after = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-revision"
    ).json()["revision"]
    projection = client.get(
        f"/api/projects/{project_id}/video-localization/timeline-projection"
    ).json()

    assert saved is not None
    assert after != before
    assert projection["revision"] == after
    assert projection["timeline_clips"][-1]["clip_id"] == "clip-background-result"


def test_workspace_omits_histories_served_by_dedicated_readers(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "轻量工作区"},
    ).json()
    project_id = project["project_id"]
    operation = VideoLocalizationOperation(
        operation_id="workspace-heavy-operation",
        project_id=project_id,
        kind="localization_draft",
        status="success",
        result_summary={
            "task_step_results": {
                "generate": {
                    "status": "success",
                    "summary": "完整步骤结果",
                    "sections": [
                        {
                            "title": "固定详情",
                            "items": [
                                {
                                    "title": f"结果 {index}",
                                    "text": "只应由任务详情接口返回。" * 50,
                                }
                                for index in range(40)
                            ],
                        }
                    ],
                }
            }
        },
    )
    tts_task = VideoLocalizationTtsTask(
        workflow_id="workspace-heavy-tts-task",
        project_id=project_id,
        segment_id="localized_0001",
        subtitle_summary="独立配音任务",
        text="只应由配音任务接口返回。" * 1_000,
        source_cue_ids=["cue_0001"],
        start_ms=0,
        end_ms=1_000,
        status="success",
    )
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            operations=[operation],
            tts_tasks=[tts_task],
        ),
    )
    assert saved is not None

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )
    full_draft = client.get(
        f"/api/projects/{project_id}/video-localization"
    )
    operation_detail = client.get(
        f"/api/projects/{project_id}/video-localization/"
        f"operations/{operation.operation_id}"
    )
    tts_tasks = client.get(
        f"/api/projects/{project_id}/video-localization/tts/tasks"
    )

    assert workspace.status_code == 200
    assert workspace.json()["draft"]["operations"] == []
    assert workspace.json()["draft"]["tts_tasks"] == []
    assert full_draft.status_code == 200
    assert full_draft.json()["operations"][0]["operation_id"] == operation.operation_id
    assert full_draft.json()["tts_tasks"][0]["workflow_id"] == tts_task.workflow_id
    assert operation_detail.status_code == 200
    assert (
        operation_detail.json()["result_summary"]["task_step_results"]
        ["generate"]["sections"][0]["items"][0]["title"]
        == "结果 0"
    )
    assert tts_tasks.status_code == 200
    assert tts_tasks.json()[0]["workflow_id"] == tts_task.workflow_id
    assert len(workspace.content) < len(full_draft.content) // 10

    openapi = client.get("/openapi.json").json()
    workspace_get = openapi["paths"][
        "/api/projects/{project_id}/video-localization/workspace"
    ]["get"]
    assert "operations、tts_tasks 固定为空" in workspace_get["description"]
    workspace_schema = openapi["components"]["schemas"][
        "PublicVideoLocalizationWorkspace"
    ]
    assert (
        "dedicated readers"
        in workspace_schema["properties"]["draft"]["description"]
    )


def test_workspace_omits_server_owned_details_and_loads_them_by_section(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "按需工作区详情"},
    ).json()
    project_id = project["project_id"]
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            transcription={
                "revision_id": "transcription-revision",
                "language": "en",
                "raw_text": "heavy " * 1_000,
                "segments": [],
                "words": [
                    {
                        "word_id": f"word-{index}",
                        "segment_id": "segment-1",
                        "text": "heavy",
                        "start_ms": index * 10,
                        "end_ms": index * 10 + 8,
                    }
                    for index in range(1_000)
                ],
            },
            reference_clips=[
                {
                    "reference_clip_id": "reference-1",
                    "speaker_id": "speaker-1",
                    "asr_text": "reference " * 1_000,
                }
            ],
            generated_candidates=[
                {
                    "candidate_id": "candidate-1",
                    "recipe_id": "recipe-1",
                    "status": "success",
                    "notes": "candidate " * 1_000,
                }
            ],
        ),
    )
    assert saved is not None

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )
    transcription = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-details/transcription"
    )
    candidates = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-details/generated_candidates"
    )

    assert workspace.status_code == 200
    body = workspace.json()
    assert body["draft"]["transcription"] is None
    assert body["draft"]["reference_clips"] == []
    assert body["draft"]["generated_candidates"] == []
    assert body["draft"]["dubbing_production"]["active_plan"] is None
    assert set(body["omitted_sections"]) == {
        "transcription",
        "reference_clips",
        "generated_candidates",
        "dubbing_production",
    }
    assert transcription.status_code == 200
    assert transcription.json()["transcription"]["revision_id"] == (
        "transcription-revision"
    )
    assert len(transcription.json()["transcription"]["words"]) == 1_000
    assert candidates.status_code == 200
    assert candidates.json()["generated_candidates"][0]["candidate_id"] == (
        "candidate-1"
    )
    assert len(workspace.content) < len(transcription.content) // 5


def test_workspace_projects_current_timing_confirmation_without_transcription(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "确认时间码投影"},
    ).json()["project_id"]
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            transcription={
                "revision_id": "transcription-1",
                "alignment_status": "partial",
                "alignment_source_track_id": "vocals",
                "alignment_audio_sha256": "a" * 64,
                "audio_boundary_status": "completed",
                "words": [{
                    "word_id": "word-1",
                    "segment_id": "segment-1",
                    "text": "Now",
                    "start_ms": 100,
                    "end_ms": 200,
                    "timing_confidence": "high",
                    "timing_source": "asr_vad_verified",
                }],
                "asr_vad_source_timing_corrections": [{
                    "correction_id": "correction-1",
                    "transcription_revision_id": "transcription-1",
                    "source_track_id": "vocals",
                    "audio_sha256": "a" * 64,
                    "analysis_protocol": "local-vad-v1",
                    "analysis_start_ms": 100,
                    "analysis_end_ms": 300,
                    "word_ids": ["word-1"],
                    "word_timings": [{
                        "word_id": "word-1",
                        "text": "Now",
                        "start_ms": 100,
                        "end_ms": 200,
                    }],
                }],
            },
            cues=[{
                "cue_id": "cue-1",
                "start_ms": 100,
                "end_ms": 200,
                "source_word_ids": ["word-1"],
                "transcription_revision_id": "transcription-1",
                "manual_timing_review_status": "confirmed",
                "manual_timing_revision": 0,
                "manual_timing_confirmed_revision": 0,
                "manual_timing_confirmed_at": "2026-09-07T00:00:00+00:00",
                "manual_timing_confirmed_start_ms": 100,
                "manual_timing_confirmed_end_ms": 200,
                "manual_timing_confirmation_method": "asr_vad_verified",
                "manual_timing_confirmation_evidence": {
                    "transcription_revision_id": "transcription-1",
                    "alignment_source_track_id": "vocals",
                    "alignment_audio_sha256": "a" * 64,
                    "source_word_ids": ["word-1"],
                    "source_timing_correction_id": "correction-1",
                },
            }],
        ),
    )
    assert saved is not None

    current = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )
    assert current.status_code == 200
    assert current.json()["draft"]["transcription"] is None
    assert current.json()["cue_timing_confirmations"] == [{
        "cue_id": "cue-1",
        "confirmation_current": True,
    }]

    stored = service.get_video_localization(project_id)
    assert stored is not None and stored.transcription is not None
    stale_word = stored.transcription.words[0].model_copy(update={"end_ms": 210})
    service.save_video_localization(
        project_id,
        stored.model_copy(update={
            "transcription": stored.transcription.model_copy(update={"words": [stale_word]})
        }),
    )
    stale = client.get(f"/api/projects/{project_id}/video-localization/workspace")
    assert stale.status_code == 200
    assert stale.json()["draft"]["transcription"] is None
    assert stale.json()["cue_timing_confirmations"] == [{
        "cue_id": "cue-1",
        "confirmation_current": False,
    }]


def test_workspace_save_preserves_omitted_server_owned_details(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "保存不删除按需详情"},
    ).json()
    project_id = project["project_id"]
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            transcription={
                "revision_id": "server-transcription",
                "language": "en",
                "words": [],
                "segments": [],
            },
            reference_clips=[{"reference_clip_id": "server-reference"}],
            generated_candidates=[
                {
                    "candidate_id": "server-candidate",
                    "recipe_id": "recipe-1",
                    "status": "success",
                }
            ],
            scene_context="before",
        ),
    )
    assert saved is not None
    workspace_draft = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    ).json()["draft"]
    workspace_draft["scene_context"] = "after"

    response = client.put(
        f"/api/projects/{project_id}/video-localization/workspace",
        json=workspace_draft,
    )

    assert response.status_code == 200
    internal = service.get_video_localization(project_id)
    assert internal is not None
    assert internal.scene_context == "after"
    assert internal.transcription is not None
    assert internal.transcription.revision_id == "server-transcription"
    assert internal.reference_clips[0].reference_clip_id == "server-reference"
    assert internal.generated_candidates[0]["candidate_id"] == "server-candidate"


def test_workspace_reader_does_not_load_complete_project_models(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "大工作区"}).json()
    project_id = project["project_id"]
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            timeline_clips=[
                {
                    "clip_id": f"clip-{index}",
                    "track_id": "dub",
                    "start_ms": index * 100,
                    "end_ms": index * 100 + 80,
                }
                for index in range(2_000)
            ],
        ),
    )
    assert saved is not None

    def fail_full_project_load(*_args, **_kwargs):
        raise AssertionError("workspace reader loaded a complete Project model")

    monkeypatch.setattr(project_store, "get_project", fail_full_project_load)

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )

    assert workspace.status_code == 200
    assert len(workspace.json()["draft"]["timeline_clips"]) == 2_000


def test_workspace_and_timeline_readers_do_not_parse_the_project_archive(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "独立工作台读取视图"},
    ).json()["project_id"]
    heavy_marker = "server-only-evidence-" + "x" * 500_000
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            operations=[
                VideoLocalizationOperation(
                    operation_id="heavy-archive-operation",
                    project_id=project_id,
                    kind="localization_draft",
                    status="success",
                    result_summary={"evidence": heavy_marker},
                )
            ],
            localized_subtitles=[{
                "subtitle_id": "localized-projected",
                "start_ms": 1_000,
                "end_ms": 2_000,
                "text": "独立读取",
            }],
            timeline_clips=[{
                "clip_id": "clip-projected",
                "track_id": "dub",
                "start_ms": 1_000,
                "end_ms": 2_000,
                "timeline_edit_gate": {"evidence": heavy_marker},
            }],
        ),
    )
    assert saved is not None

    with database.conn() as connection:
        row = connection.execute(
            """
            SELECT
                length(projects.data) AS archive_size,
                length(projection.workspace_json) AS workspace_size,
                length(projection.timeline_json) AS timeline_size
            FROM projects
            JOIN video_localization_workspace_projections AS projection
              ON projection.project_id = projects.project_id
            WHERE projects.project_id = ?
            """,
            (project_id,),
        ).fetchone()
        assert row is not None
        assert int(row["workspace_size"]) < int(row["archive_size"]) // 4
        assert int(row["timeline_size"]) < int(row["archive_size"]) // 4
        # Deliberately break only the legacy archive in this isolated test DB.
        # The browser readers must remain available from the committed view.
        connection.execute(
            "UPDATE projects SET data = 'invalid archive' WHERE project_id = ?",
            (project_id,),
        )

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )
    timeline = client.get(
        f"/api/projects/{project_id}/video-localization/timeline-projection"
    )

    assert workspace.status_code == 200
    assert workspace.json()["draft"]["localized_subtitles"][0]["text"] == (
        "独立读取"
    )
    assert timeline.status_code == 200
    assert timeline.json()["timeline_clips"][0]["clip_id"] == "clip-projected"
    assert "timeline_edit_gate" not in timeline.json()["timeline_clips"][0]


def test_timeline_projection_is_bounded_and_tracks_latest_clip(tmp_path: Path):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "实时轻量时间线"},
    ).json()
    project_id = project["project_id"]
    heavy_words = [
        {
            "word_id": f"word-{index}",
            "segment_id": "segment-heavy",
            "text": "heavy",
            "start_ms": index * 10,
            "end_ms": index * 10 + 8,
        }
        for index in range(2_000)
    ]
    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            transcription={
                "engine_id": "fixture",
                "source_track_id": "vocals",
                "language": "en",
                "raw_text": "heavy " * 2_000,
                "segments": [],
                "words": heavy_words,
            },
            timeline_clips=[{
                "clip_id": "clip-live",
                "track_id": "dub",
                "start_ms": 2_000,
                "end_ms": 3_000,
                "audio_path": str(tmp_path / "outputs" / "clip-live.wav"),
                "cqc_report": {"raw_evidence": "x" * 50_000},
                "timeline_edit_gate": {"word_evidence": "y" * 50_000},
            }],
        ),
    )
    assert saved is not None

    projection = client.get(
        f"/api/projects/{project_id}/video-localization/timeline-projection"
    )
    live_revision = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-revision"
    )
    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )

    assert projection.status_code == 200
    assert projection.json()["revision"] == live_revision.json()["revision"]
    assert projection.json()["timeline_clips"][0]["clip_id"] == "clip-live"
    assert "cqc_report" not in projection.json()["timeline_clips"][0]
    assert "timeline_edit_gate" not in projection.json()["timeline_clips"][0]
    assert "cqc_report" not in workspace.json()["draft"]["timeline_clips"][0]
    assert "timeline_edit_gate" not in workspace.json()["draft"]["timeline_clips"][0]
    assert len(projection.content) < len(workspace.content) // 5

    workspace_draft = workspace.json()["draft"]
    persisted = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=workspace_draft,
    )
    assert persisted.status_code == 200
    internal = service.get_video_localization(project_id)
    assert internal is not None
    assert internal.timeline_clips[0]["cqc_report"]["raw_evidence"] == "x" * 50_000
    assert internal.timeline_clips[0]["timeline_edit_gate"]["word_evidence"] == "y" * 50_000


def test_workspace_inspects_media_once(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "单次媒体检查"},
    ).json()
    project_id = project["project_id"]
    calls = 0
    original = service.media_health.inspect_project_media

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service.media_health, "inspect_project_media", counted)
    response = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )

    assert response.status_code == 200
    assert calls == 1


def test_workspace_omits_duplicate_candidate_cqc_evidence(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "轻量配音证据"},
    ).json()
    project_id = project["project_id"]
    base = VideoLocalizationDraft()
    source_revision = dubbing_production.dubbing_source_revision(base)
    candidate_input = DubbingCandidateCqcInput(
        source_revision=source_revision,
        group_id="group-1",
        candidate_id="candidate-1",
        task_status="success",
        expected_spoken_text="测试台词",
        candidate_transcript="测试台词",
        target_start_ms=0,
        target_end_ms=1_000,
    )
    report = DubbingCandidateCqcReport(
        source_revision=source_revision,
        group_id="group-1",
        candidate_id="candidate-1",
        automatic_status="passed",
        subjective_status="not_reviewed",
        overall_status="needs_review",
        transcript=DubbingTranscriptComparison(
            expected_tokens=4,
            matched_tokens=4,
            coverage_ratio=1,
            extra_ratio=0,
        ),
        recommended_action="listen_and_review",
    )
    base = base.model_copy(
        update={
            "generated_candidates": [
                {
                    "candidate_id": "candidate-1",
                    "recipe_id": "recipe-1",
                    "status": "success",
                    "cqc_status": "needs_review",
                    "cqc_report": report.model_dump(mode="json"),
                }
            ]
        }
    )
    saved = service.save_video_localization(
        project_id,
        base.model_copy(
            update={
                "dubbing_production": (
                    base.dubbing_production.model_copy(
                        update={
                            "candidate_inputs": [candidate_input],
                            "candidate_reports": [report],
                        }
                    )
                )
            }
        ),
    )
    assert saved is not None

    workspace = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )
    production_detail = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-details/dubbing_production"
    )
    candidate_detail = client.get(
        f"/api/projects/{project_id}/video-localization/workspace-details/generated_candidates"
    )
    assert workspace.status_code == 200
    public_draft = workspace.json()["draft"]
    assert public_draft["dubbing_production"]["candidate_inputs"] == []
    assert public_draft["dubbing_production"]["candidate_reports"] == []
    assert public_draft["generated_candidates"] == []
    assert production_detail.status_code == 200
    assert len(
        production_detail.json()["dubbing_production"]["candidate_reports"]
    ) == 1
    assert production_detail.json()["dubbing_production"]["candidate_inputs"] == []
    assert candidate_detail.status_code == 200
    assert "cqc_report" not in candidate_detail.json()["generated_candidates"][0]

    internal = service.get_video_localization(project_id)
    assert internal is not None
    assert len(internal.dubbing_production.candidate_inputs) == 1
    assert "cqc_report" in internal.generated_candidates[0]


def test_public_draft_uses_resource_identity_for_managed_project_media(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "路径隔离工作区"},
    ).json()
    project_id = project["project_id"]
    package_root = media_assets.project_video_localization_dir(project_id)
    source_video = package_root / "source" / "source.webm"
    source_audio = package_root / "audio" / "source.wav"
    vocals = package_root / "stems" / "vocals.wav"
    background = package_root / "stems" / "background.wav"
    for path in (source_video, source_audio, vocals, background):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"managed-media")

    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            source_media={
                "filename": source_video.name,
                "video_path": str(source_video),
                "audio_path": str(source_audio),
                "duration_ms": 1_000,
            },
            stems={
                "original_audio_path": str(source_audio),
                "vocals_clean_path": str(vocals),
                "background_path": str(background),
                "separation_status": "completed",
            },
            timeline_clips=[
                {
                    "clip_id": "media_original",
                    "track_id": "original",
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "audio_path": str(source_audio),
                },
                {
                    "clip_id": "media_vocals",
                    "track_id": "vocals",
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "audio_path": str(vocals),
                },
            ],
        ),
    )
    assert saved is not None

    workspace_response = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )
    full_draft_response = client.get(
        f"/api/projects/{project_id}/video-localization"
    )

    assert workspace_response.status_code == 200
    assert full_draft_response.status_code == 200
    workspace = workspace_response.json()
    for public_draft in (
        workspace["draft"],
        full_draft_response.json(),
    ):
        assert public_draft["source_media"]["video_path"] is None
        assert public_draft["source_media"]["audio_path"] is None
        assert public_draft["stems"]["original_audio_path"] is None
        assert public_draft["stems"]["vocals_clean_path"] is None
        assert public_draft["stems"]["background_path"] is None
        assert all(
            "audio_path" not in clip
            for clip in public_draft["timeline_clips"]
            if clip["track_id"] in {"original", "vocals", "background"}
        )
    assert {
        clip["media_source_clip_id"]
        for clip in workspace["draft"]["timeline_clips"]
    } == {"media_original", "media_vocals", "media_background"}
    assert {
        clip["media_source_clip_id"]
        for clip in full_draft_response.json()["timeline_clips"]
    } == {"media_original", "media_vocals"}

    assert workspace["media_health"]["source_video"]["resource_id"] == (
        "source_video"
    )
    assert workspace["media_health"]["source_audio"]["resource_id"] == (
        "source_audio"
    )
    assert workspace["media_health"]["vocals"]["resource_id"] == "vocals"
    assert workspace["media_health"]["background"]["resource_id"] == (
        "background"
    )

    internal = service.get_video_localization(project_id)
    assert internal is not None
    assert internal.source_media.video_path == str(source_video)
    assert internal.source_media.audio_path == str(source_audio)
    assert internal.stems.vocals_clean_path == str(vocals)
    assert internal.timeline_clips[0]["audio_path"] == str(source_audio)


def test_workspace_projects_available_system_media_without_persisting_defaults(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "工作区默认媒体轨"},
    ).json()
    project_id = project["project_id"]
    package_root = media_assets.project_video_localization_dir(project_id)
    source_video = package_root / "source" / "source.webm"
    source_audio = package_root / "audio" / "source.wav"
    vocals = package_root / "stems" / "vocals.wav"
    background = package_root / "stems" / "background.wav"
    for path in (source_video, source_audio, vocals, background):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"managed-media")

    saved = service.save_video_localization(
        project_id,
        VideoLocalizationDraft(
            source_media={
                "filename": source_video.name,
                "video_path": str(source_video),
                "audio_path": str(source_audio),
                "duration_ms": 12_000,
            },
            stems={
                "original_audio_path": str(source_audio),
                "vocals_clean_path": str(vocals),
                "background_path": str(background),
                "separation_status": "completed",
            },
            timeline_clips=[],
        ),
    )
    assert saved is not None

    workspace_response = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    )

    assert workspace_response.status_code == 200
    workspace_clips = workspace_response.json()["draft"]["timeline_clips"]
    assert [
        (
            clip["clip_id"],
            clip["track_id"],
            clip["media_source_clip_id"],
            clip["start_ms"],
            clip["end_ms"],
        )
        for clip in workspace_clips
    ] == [
        ("media_original", "original", "media_original", 0, 12_000),
        ("media_vocals", "vocals", "media_vocals", 0, 12_000),
        ("media_background", "background", "media_background", 0, 12_000),
    ]
    assert all("audio_path" not in clip for clip in workspace_clips)

    internal = service.get_video_localization(project_id)
    assert internal is not None
    assert internal.timeline_clips == []

    saved = service.save_video_localization(
        project_id,
        internal.model_copy(
            update={
                "ui_state": {"disabled_media_tracks": ["vocals"]},
                "timeline_clips": [
                    {
                        "clip_id": "custom_background",
                        "track_id": "background",
                        "start_ms": 2_000,
                        "end_ms": 8_000,
                        "media_source_clip_id": "media_background",
                    }
                ],
            }
        ),
    )
    assert saved is not None

    refreshed = client.get(
        f"/api/projects/{project_id}/video-localization/workspace"
    ).json()["draft"]["timeline_clips"]
    assert [
        (clip["clip_id"], clip["track_id"])
        for clip in refreshed
    ] == [
        ("custom_background", "background"),
        ("media_original", "original"),
    ]
    assert all(clip["track_id"] != "vocals" for clip in refreshed)

    internal = service.get_video_localization(project_id)
    assert internal is not None
    assert [clip["clip_id"] for clip in internal.timeline_clips] == [
        "custom_background"
    ]
