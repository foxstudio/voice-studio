"""A completed placement command must not undo later timeline edits."""

import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import draft_store, service
from app.errors import AppException
from app.schemas.voice_studio import (
    AppSettings,
    HistoryItem,
    ProjectCreate,
    VideoLocalizationDraft,
    VideoLocalizationSubtitleCue,
    VideoLocalizationTtsTask,
    VideoLocalizationTtsTaskStage,
)
from app.services import database, history_store, project_store, settings_store


@pytest.fixture
def workspace(tmp_path):
    previous_db = database.DB_PATH
    database.set_db_path(tmp_path / "config" / "voice_studio.db")
    settings_store.update(AppSettings(
        data_dir=str(tmp_path), voice_dir=str(tmp_path / "voices"),
        output_dir=str(tmp_path / "outputs"), export_dir=str(tmp_path / "exports"),
        project_dir=str(tmp_path / "projects"), cache_dir=str(tmp_path / "cache"),
        log_dir=str(tmp_path / "logs"),
    ))
    project = project_store.create_project(ProjectCreate(name="Placement replay fixture"))
    audio = tmp_path / "outputs" / "source.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    sf.write(audio, np.full(2000, 0.1, dtype=np.float32), 1000)
    history = HistoryItem(
        result_id="result", task_id="generation", generation_id="generation",
        engine_id="omnivoice", project_id=project.project_id,
        segment_id="subtitle", localized_subtitle_id="subtitle",
        input_text="完整测试台词", duration_ms=2000, output_path=str(audio),
        parameter_snapshot={"source": "video_localization"},
    )
    history_store.add(history)
    workflow = VideoLocalizationTtsTask(
        workflow_id="workflow", project_id=project.project_id,
        segment_id="subtitle", subtitle_summary="测试", text="完整测试台词",
        start_ms=1000, end_ms=3000, generation_task_id="generation",
        stages=[
            VideoLocalizationTtsTaskStage(kind="generation", status="success"),
            VideoLocalizationTtsTaskStage(kind="placement", parameters={
                "target_snapshot": {"subtitle_ids": ["subtitle"], "start_ms": 1000, "end_ms": 3000, "text": "完整测试台词"},
                "source_snapshot": {"cue_ids": []},
            }),
        ],
    )
    draft_store.save(project.project_id, VideoLocalizationDraft(
        localized_subtitles=[VideoLocalizationSubtitleCue(
            subtitle_id="subtitle", start_ms=1000, end_ms=3000, text="完整测试台词",
        )], tts_tasks=[workflow],
    ), intent="content")
    try:
        yield project.project_id, history
    finally:
        database.set_db_path(previous_db)


def _place_generated(project_id, history, *, explicit_clip_id=None):
    return service.sync_single_tts_result(
        project_id, "subtitle", result_id=history.result_id,
        output_path=history.output_path, duration_ms=history.duration_ms,
        task_id=history.task_id, generation_id=history.generation_id,
        workflow_id="workflow", timeline_clip_id=explicit_clip_id,
    )


def _place_history(project_id, history, *, new_clip_id="history_clip_original"):
    return service.apply_tts_history_to_timeline(
        project_id, history.result_id, segment_id="subtitle",
        new_clip_id=new_clip_id, request_id=new_clip_id,
        start_ms=1000, dub_lane=0, force_new=True,
    )


def _save_edit(project_id, draft, shape):
    original = draft.timeline_clips[0]
    first = {
        **original, "media_source_clip_id": original["clip_id"],
        "source_end_ms": 1000, "end_ms": 2000,
    }
    second = {
        **original, "clip_id": original["clip_id"] + "_part_2",
        "media_source_clip_id": original["clip_id"],
        "source_start_ms": 1000, "start_ms": 2000,
    }
    clips = {
        "trim": [{**original, "source_start_ms": 300, "source_end_ms": 1500, "start_ms": 1100, "end_ms": 2300}],
        "split": [first, second],
        "split_first_removed": [second],
        "deleted": [],
    }[shape]
    # Persist the editor's current projection without changing the completed
    # placement's workflow/receipt. No generation or external model is invoked.
    return draft_store.save(project_id, draft.model_copy(update={"timeline_clips": clips}), intent="content")


@pytest.mark.parametrize("shape", ["trim", "split", "split_first_removed", "deleted"])
def test_completed_generation_replay_preserves_later_edits(workspace, shape):
    project_id, history = workspace
    placed = _place_generated(project_id, history)
    assert placed.tts_tasks[0].stages[1].status == "success"
    original_id = placed.timeline_clips[0]["clip_id"]
    edited = _save_edit(project_id, placed, shape)

    repeated = _place_generated(project_id, history, explicit_clip_id=original_id)

    assert repeated.timeline_clips == edited.timeline_clips
    assert draft_store.get(project_id).timeline_clips == edited.timeline_clips


def test_completed_generation_replay_resolves_split_without_ambiguous_identity(workspace):
    project_id, history = workspace
    placed = _place_generated(project_id, history)
    edited = _save_edit(project_id, placed, "split")

    repeated = _place_generated(project_id, history)

    assert repeated.timeline_clips == edited.timeline_clips


@pytest.mark.parametrize("shape", ["trim", "split", "split_first_removed", "deleted"])
def test_same_history_insertion_replay_preserves_later_edits(workspace, shape):
    project_id, history = workspace
    placed = _place_history(project_id, history)
    edited = _save_edit(project_id, placed, shape)

    repeated = _place_history(project_id, history)

    assert repeated.timeline_clips == edited.timeline_clips
    assert draft_store.get(project_id).timeline_clips == edited.timeline_clips


def test_new_history_insertion_still_allows_another_copy_of_same_audio(workspace):
    project_id, history = workspace
    placed = _place_history(project_id, history)
    edited = _save_edit(project_id, placed, "split_first_removed")

    inserted = _place_history(project_id, history, new_clip_id="history_clip_new_command")

    assert len(inserted.timeline_clips) == len(edited.timeline_clips) + 1
    assert inserted.timeline_clips[0] == edited.timeline_clips[0]
    assert inserted.timeline_clips[1]["clip_id"] == "history_clip_new_command"


def test_history_replace_replay_preserves_edit_but_new_command_replaces(workspace):
    project_id, history = workspace
    placed = _place_history(project_id, history)
    clip_id = placed.timeline_clips[0]["clip_id"]

    def replace(request_id):
        return service.apply_tts_history_to_timeline(
            project_id, history.result_id, segment_id="subtitle", clip_id=clip_id,
            request_id=request_id, start_ms=1000, dub_lane=0,
        )

    replaced = replace("replace_command_1")
    edited = _save_edit(project_id, replaced, "trim")
    assert replace("replace_command_1").timeline_clips == edited.timeline_clips

    explicitly_replaced_again = replace("replace_command_2")
    assert explicitly_replaced_again.timeline_clips[0]["source_start_ms"] == 0
    assert explicitly_replaced_again.timeline_clips[0]["source_end_ms"] == 2000
    assert len(explicitly_replaced_again.timeline_clips) == 1


def test_history_request_id_cannot_be_reused_with_changed_parameters(workspace):
    project_id, history = workspace
    placed = _place_history(project_id, history)

    with pytest.raises(AppException) as captured:
        service.apply_tts_history_to_timeline(
            project_id, history.result_id, segment_id="subtitle",
            new_clip_id="history_clip_original", request_id="history_clip_original",
            start_ms=5000, dub_lane=0, force_new=True,
        )

    assert captured.value.code == "VIDEO_LOCALIZATION_PLACEMENT_REQUEST_CONFLICT"
    assert draft_store.get(project_id).timeline_clips == placed.timeline_clips


def test_explicit_new_history_clip_never_replaces_an_existing_subtitle_clip(workspace):
    project_id, history = workspace
    placed = _place_history(project_id, history)

    appended = service.apply_tts_history_to_timeline(
        project_id, history.result_id, segment_id="subtitle",
        new_clip_id="explicit_new_clip", request_id="explicit_new_command",
        start_ms=5000, dub_lane=0,
    )

    assert len(appended.timeline_clips) == 2
    assert appended.timeline_clips[0] == placed.timeline_clips[0]
    assert appended.timeline_clips[1]["clip_id"] == "explicit_new_clip"


def test_new_history_command_cannot_reuse_an_existing_clip_id(workspace):
    project_id, history = workspace
    placed = _place_history(project_id, history)

    with pytest.raises(AppException) as captured:
        service.apply_tts_history_to_timeline(
            project_id, history.result_id, segment_id="subtitle",
            new_clip_id="history_clip_original", request_id="different_command",
            start_ms=1000, dub_lane=0, force_new=True,
        )

    assert captured.value.code == "VIDEO_LOCALIZATION_TIMELINE_CLIP_ID_CONFLICT"
    assert draft_store.get(project_id).timeline_clips == placed.timeline_clips


@pytest.mark.parametrize("endpoint_suffix", ["", "/workspace"])
def test_deleting_one_clip_does_not_delete_omitted_same_generation_copy(workspace, endpoint_suffix):
    from fastapi.testclient import TestClient
    from app.main import app

    project_id, history = workspace
    original = _place_history(project_id, history).timeline_clips[0]
    latest = _place_history(project_id, history, new_clip_id="new_copy_not_in_client_snapshot")
    # Workspace revision and clip projection are separate client state. An
    # omitted server clip is not a deletion request: only the exact ID below
    # was selected and removed by the user. The task tombstone stops delivery.
    payload = {
        **latest.model_dump(mode="json"), "timeline_clips": [],
        "ui_state": {
            "discarded_tts_task_ids": [history.generation_id],
            "client_timeline_edit_intent": {"deleted_timeline_clips": [{
                "clip_id": original["clip_id"],
                "expected_generation_identity": history.generation_id,
            }]},
        },
    }

    response = TestClient(app).put(
        f"/api/projects/{project_id}/video-localization{endpoint_suffix}", json=payload,
    )

    assert response.status_code == 200, response.text
    assert [clip["clip_id"] for clip in response.json()["timeline_clips"]] == ["new_copy_not_in_client_snapshot"]


@pytest.mark.parametrize("client_receipts", [{}, {"forged_command": "forged_fingerprint"}])
@pytest.mark.parametrize("workspace_only", [False, True])
def test_client_draft_cannot_erase_or_forge_placement_receipts(workspace, client_receipts, workspace_only):
    project_id, history = workspace
    placed = _place_history(project_id, history)
    client_draft = VideoLocalizationDraft.model_validate({
        **placed.model_dump(), "history_placement_receipts": client_receipts,
    })
    save_client = (
        service.replace_video_localization_workspace_from_client
        if workspace_only else service.replace_video_localization_from_client
    )

    saved = save_client(project_id, client_draft)

    assert saved.history_placement_receipts == placed.history_placement_receipts
    assert draft_store.get(project_id).history_placement_receipts == placed.history_placement_receipts


@pytest.mark.parametrize("concurrent_action", ["insert", "delete"])
def test_atomic_update_rebases_after_independent_project_commit(workspace, concurrent_action):
    project_id, history = workspace
    placed = _place_history(project_id, history)
    original = placed.timeline_clips[0]
    calls = 0

    def update_ui_after_another_writer(current):
        nonlocal calls
        calls += 1
        if calls == 1:
            # A separate store connection commits after the facade has read
            # its base. This models another process, which cannot share the
            # facade's in-process RLock, without threads or timing sleeps.
            other_project = project_store.get_project(project_id)
            raw = dict(other_project.parameters[draft_store.VIDEO_LOCALIZATION_KEY])
            raw["timeline_clips"] = (
                [original, {**original, "clip_id": "other_writer_clip", "start_ms": 5000, "end_ms": 7000}]
                if concurrent_action == "insert" else []
            )
            other_project.parameters = {
                **other_project.parameters, draft_store.VIDEO_LOCALIZATION_KEY: raw,
            }
            project_store.save_project(other_project)
        return current.model_copy(update={"ui_state": {**current.ui_state, "inspector_tab": "dubbing"}})

    saved = service.update_video_localization_atomic(
        project_id, update_ui_after_another_writer, intent="workspace",
    )

    expected_ids = [original["clip_id"], "other_writer_clip"] if concurrent_action == "insert" else []
    assert [clip["clip_id"] for clip in saved.timeline_clips] == expected_ids
    assert draft_store.get(project_id).timeline_clips == saved.timeline_clips
    assert saved.ui_state["inspector_tab"] == "dubbing"
    assert calls == 2


def test_group_history_preserves_frozen_binding(workspace):
    project_id, history = workspace
    draft = service.get_video_localization(project_id)
    second = VideoLocalizationSubtitleCue(subtitle_id="second", start_ms=2000, end_ms=3000, text="后半句")
    workflow = draft.tts_tasks[0]
    placement = workflow.stages[1].model_copy(update={"parameters": {
        "target_snapshot": {"subtitle_ids": ["subtitle", "second"], "start_ms": 1000, "end_ms": 3000, "text": history.input_text},
        "source_snapshot": {"cue_ids": ["source1", "source2"]},
    }})
    draft_store.save(project_id, draft.model_copy(update={
        "localized_subtitles": [*draft.localized_subtitles, second],
        "tts_tasks": [workflow.model_copy(update={"stages": [workflow.stages[0], placement]})],
    }), intent="content")
    placed = _place_history(project_id, history)
    clip = next(c for c in placed.timeline_clips if c['clip_id']=='history_clip_original')
    assert clip['target_subtitle_ids'] == ['subtitle', 'second']
    assert clip['source_cue_ids'] == ['source1', 'source2']
