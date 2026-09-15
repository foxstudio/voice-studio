"""Real persistence regressions for bounded, atomic editor transactions."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import app
from app.domains.video_localization import draft_store, service, timeline_clip_timing
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.schemas.voice_studio import AppSettings, ProjectCreate, VideoLocalizationSubtitleCueUpdate
from app.services import database, project_store, settings_store


@pytest.fixture
def editor(tmp_path):
    previous_db = database.DB_PATH
    database.set_db_path(tmp_path / "config" / "voice_studio.db")
    try:
        settings_store.update(AppSettings(
            data_dir=str(tmp_path), voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"), export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"), cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        ))
        project = project_store.create_project(ProjectCreate(name="bounded editorial fixture"))
        yield TestClient(app), project.project_id
    finally:
        database.set_db_path(previous_db)


def seed(project_id, **values):
    project = project_store.get_project(project_id)
    project.parameters["video_localization"] = VideoLocalizationDraft(**values).model_dump(mode="json")
    project_store.save_project(project, touch_updated_at=False)
    return raw(project_id)


def raw(project_id):
    return project_store.get_project(project_id).parameters["video_localization"]


def patch(client, project_id, **payload):
    return client.patch(f"/api/projects/{project_id}/video-localization/timeline-edit", json=payload)


def clip():
    return {"clip_id": "audio", "track_id": "dub", "task_id": "voice-1",
            "audio_path": "/backend-owned.wav", "start_ms": 0, "end_ms": 1200,
            "source_start_ms": 0, "source_end_ms": 1000, "dub_lane": 0}


def expected_clip(**updates):
    return {"clip_id": "audio", "expected_generation_identity": "voice-1",
            "expected_editable_fields": {"start_ms": 0, "end_ms": 1000,
                "source_start_ms": 0, "source_end_ms": 1000,
                "media_source_clip_id": None, "dub_lane": 0}, **updates}


def test_ui_and_audio_saves_preserve_unrelated_raw_state_and_exact_timing(editor, monkeypatch):
    client, project_id = editor
    initial = seed(project_id,
        source_media={"frame_rate": 30}, timeline_clips=[clip(), {**clip(), "clip_id": "untouched"}],
        cues=[{"cue_id": "asr", "start_ms": 0, "end_ms": 1000,
               "en_subtitle_text": "Source", "quality_flags": ["manual_timing_verified"]}],
        quality_gate={"status": "pass", "checked_at": "earlier evidence"},
    )
    from app.domains.video_localization import dubbing_text_rebase

    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary editor save called global normalization or review")

    with monkeypatch.context() as bounded:
        bounded.setattr(draft_store, "evaluate_quality_gate", forbidden)
        bounded.setattr(timeline_clip_timing, "normalize_draft", forbidden)
        bounded.setattr(dubbing_text_rebase, "rebase_text_edit", forbidden)
        saved_ui = service.update_video_localization_ui_state(project_id, {"timeline_zoom": 2})
        assert saved_ui.ui_state["timeline_zoom"] == 2
        assert raw(project_id)["timeline_clips"] == initial["timeline_clips"]
        assert raw(project_id)["cues"] == initial["cues"]
        response = patch(client, project_id, clip_patches=[expected_clip(start_ms=103, end_ms=1337)])
        assert response.status_code == 200, response.text
    assert raw(project_id)["timeline_clips"][1] == initial["timeline_clips"][1]
    assert raw(project_id)["quality_gate"] == initial["quality_gate"]
    assert raw(project_id)["cues"] == initial["cues"]
    assert response.json()["timeline_clips"][0]["generation_identity"] == "voice-1"
    # Both the repository reader and public workspace must retain these exact
    # editor-owned milliseconds even when a legacy crop is shorter.
    assert draft_store.get(project_id).timeline_clips[0]["end_ms"] == 1337
    complete = client.get(f"/api/projects/{project_id}/video-localization")
    assert complete.status_code == 200, complete.text
    assert complete.json()["timeline_clips"][0]["start_ms"] == 103
    assert complete.json()["timeline_clips"][0]["end_ms"] == 1337


def test_collection_merge_preserves_other_fields_rows_and_strips_injected_metadata(editor):
    client, project_id = editor
    initial = seed(project_id, cues=[{"cue_id": "asr", "start_ms": 0, "end_ms": 1000,
        "en_subtitle_text": "Before", "tts_audio_path": "/trusted.wav"}])
    observed = deepcopy(initial["cues"])
    concurrent = deepcopy(initial)
    concurrent["cues"][0]["notes"] = "another writer"
    concurrent["cues"].append({"cue_id": "other", "start_ms": 2000, "end_ms": 3000, "en_subtitle_text": "Keep"})
    seed(project_id, **concurrent)
    desired = deepcopy(observed)
    desired[0].update({"en_subtitle_text": "After", "tts_audio_path": "/injected.wav"})
    desired.append({"cue_id": "new", "start_ms": 1000, "end_ms": 1800,
                    "en_subtitle_text": "Inserted", "tts_audio_path": "/injected.wav",
                    "tts_result_id": "forged", "quality_flags": ["manual_timing_verified"]})
    response = patch(client, project_id, cue_collection_change={"expected": observed, "desired": desired})
    assert response.status_code == 200, response.text
    stored = {item["cue_id"]: item for item in raw(project_id)["cues"]}
    assert stored["asr"]["en_subtitle_text"] == "After"
    assert stored["asr"]["notes"] == "another writer"
    assert stored["asr"]["tts_audio_path"] == "/trusted.wav"
    assert stored["other"]["en_subtitle_text"] == "Keep"
    assert stored["new"]["tts_audio_path"] is None
    assert stored["new"]["tts_result_id"] is None
    assert "manual_timing_verified" not in stored["new"]["quality_flags"]
    assert all(item["tts_audio_path"] is None for item in response.json()["cues"])


def test_collection_conflict_rolls_back_audio_lane_and_all_collection_edits(editor):
    client, project_id = editor
    initial = seed(project_id, source_media={"frame_rate": 30}, timeline_clips=[clip()],
        cues=[{"cue_id": "asr", "en_subtitle_text": "Original"}])
    desired = [{**initial["cues"][0], "en_subtitle_text": "Local"}]
    seed(project_id, **{**initial, "cues": [{**initial["cues"][0], "en_subtitle_text": "Concurrent"}]})
    before = deepcopy(raw(project_id))
    response = patch(client, project_id,
        clip_patches=[expected_clip(start_ms=200, end_ms=1200)],
        dub_lane_state_patches=[{"lane": 1, "muted": True}],
        cue_collection_change={"expected": initial["cues"], "desired": desired})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_TIMELINE_COLLECTION_CHANGED"
    assert raw(project_id) == before


def test_subtitle_delete_undo_redo_retains_spoken_identity_and_placed_audio(editor):
    client, project_id = editor
    initial = seed(project_id,
        cues=[{"cue_id": "asr", "start_ms": 0, "end_ms": 1000,
               "zh_localized_subtitle_text": "内容", "tts_recommended_text": "内容"}],
        localized_subtitles=[{"subtitle_id": "sub", "start_ms": 0, "end_ms": 1000,
            "text": "内容", "tts_text": "内容", "linked_cue_id": "asr", "source_cue_ids": ["asr"],
            "source_word_ids": ["word"], "spoken_segment_id": "spoken"}],
        localized_spoken_segments=[{"segment_id": "spoken", "paragraph_id": "original-paragraph",
            "text": "内容", "start_ms": 0, "end_ms": 1000,
            "source_cue_ids": ["asr"], "source_word_ids": ["word"]}],
        timeline_clips=[{**clip(), "subtitle_id": "sub", "status": "ready"}],
        localization_state={"status": "success", "quality_gate_fingerprint": "obsolete"},
    )
    response = patch(client, project_id, localized_subtitle_collection_change={
        "expected": initial["localized_subtitles"], "desired": []})
    assert response.status_code == 200, response.text
    assert raw(project_id)["localized_subtitles"] == []
    assert raw(project_id)["localized_spoken_segments"] == initial["localized_spoken_segments"]
    assert raw(project_id)["localization_state"] == {"status": "edited"}
    assert raw(project_id)["cues"][0]["zh_localized_subtitle_text"] is None
    restored = patch(client, project_id, localized_subtitle_collection_change={
        "expected": [], "desired": initial["localized_subtitles"]})
    assert restored.status_code == 200, restored.text
    assert raw(project_id)["localized_spoken_segments"] == initial["localized_spoken_segments"]
    assert raw(project_id)["cues"][0]["zh_localized_subtitle_text"] == "内容"
    for field in ("start_ms", "end_ms", "source_start_ms", "source_end_ms", "audio_path", "task_id"):
        assert raw(project_id)["timeline_clips"][0][field] == initial["timeline_clips"][0][field]
    redone = patch(client, project_id, localized_subtitle_collection_change={
        "expected": restored.json()["localized_subtitles"], "desired": []})
    assert redone.status_code == 200, redone.text
    assert raw(project_id)["localized_subtitles"] == []


def test_cue_timing_edit_invalidates_confirmation_without_trusting_old_audit(editor):
    client, project_id = editor
    initial = seed(project_id, cues=[{"cue_id": "asr", "start_ms": 0, "end_ms": 1000,
        "manual_timing_review_status": "confirmed", "manual_timing_revision": 1,
        "manual_timing_confirmed_revision": 1, "manual_timing_confirmed_at": "auditioned-before",
        "manual_timing_confirmed_start_ms": 0, "manual_timing_confirmed_end_ms": 1000,
        "manual_timing_confirmation_method": "auditioned"}])
    response = patch(client, project_id, cue_collection_change={"expected": initial["cues"],
        "desired": [{**initial["cues"][0], "start_ms": 100}]})
    assert response.status_code == 200, response.text
    saved = raw(project_id)["cues"][0]
    assert saved["start_ms"] == 100
    assert saved["manual_timing_revision"] == 2
    assert saved["manual_timing_review_status"] == "required"


def test_single_subtitle_timing_save_does_not_normalize_other_audio(editor, monkeypatch):
    _, project_id = editor
    initial = seed(project_id, source_media={"frame_rate": 30}, timeline_clips=[clip()],
        localized_subtitles=[{"subtitle_id": "sub", "start_ms": 0, "end_ms": 1000, "text": "内容"}])

    def forbidden(*args, **kwargs):
        raise AssertionError("single timing edit invoked whole-project quality or timing")

    monkeypatch.setattr(draft_store, "evaluate_quality_gate", forbidden)
    monkeypatch.setattr(timeline_clip_timing, "normalize_draft", forbidden)
    saved, _ = service.update_localized_subtitle_interactive(
        project_id, "sub", VideoLocalizationSubtitleCueUpdate(start_ms=100, end_ms=900),
    )
    assert saved.localized_subtitles[0].start_ms == 100
    assert raw(project_id)["timeline_clips"] == initial["timeline_clips"]


def test_combined_concurrent_subtitle_bounds_are_validated_before_commit(editor):
    client, project_id = editor
    initial = seed(project_id, localized_subtitles=[{"subtitle_id": "sub", "start_ms": 0, "end_ms": 1000, "text": "内容"}])
    seed(project_id, **{**initial, "localized_subtitles": [{**initial["localized_subtitles"][0], "end_ms": 500}]})
    before = deepcopy(raw(project_id))
    response = patch(client, project_id, localized_subtitle_collection_change={
        "expected": initial["localized_subtitles"],
        "desired": [{**initial["localized_subtitles"][0], "start_ms": 700}]})
    assert response.status_code == 422, response.text
    assert raw(project_id) == before


def test_new_audio_slice_rejects_invalid_range_and_collection_rejects_duplicate_ids(editor):
    client, project_id = editor
    initial = seed(project_id, timeline_clips=[clip()])
    response = patch(client, project_id, added_clips=[{"clip_id": "new", "media_source_clip_id": "audio",
        "start_ms": 100, "end_ms": 50, "source_start_ms": 0, "source_end_ms": 50}])
    assert response.status_code == 400
    response = patch(client, project_id, cue_collection_change={"expected": [], "desired": [
        {"cue_id": "duplicate"}, {"cue_id": "duplicate"}]})
    assert response.status_code == 400
    assert raw(project_id) == initial


def test_cas_retry_does_not_cancel_generation_from_the_failed_attempt(editor, monkeypatch):
    client, project_id = editor
    initial = seed(project_id,
        localized_subtitles=[{"subtitle_id": "sub", "start_ms": 0, "end_ms": 1000, "text": "Before"}],
        tts_tasks=[{"workflow_id": "workflow", "project_id": project_id, "segment_id": "sub",
                    "subtitle_summary": "Before", "text": "Before", "start_ms": 0, "end_ms": 1000,
                    "status": "running", "generation_task_id": "generation"}],
    )
    save = draft_store.save
    attempts = []
    cancelled = []

    def race_then_save(project_id, draft, **kwargs):
        attempts.append(draft)
        if len(attempts) == 1:
            # A real newer Project revision retargets the running workflow.
            # The stale save loses CAS; its cancellation is never committed.
            concurrent = deepcopy(initial)
            concurrent["tts_tasks"][0]["segment_id"] = "unrelated"
            seed(project_id, **concurrent)
        return save(project_id, draft, **kwargs)

    monkeypatch.setattr(draft_store, "save", race_then_save)
    monkeypatch.setattr(service, "_cancel_tts_generation_task", cancelled.append)
    response = patch(client, project_id, localized_subtitle_collection_change={
        "expected": initial["localized_subtitles"],
        "desired": [{**initial["localized_subtitles"][0], "text": "After"}],
    })
    assert response.status_code == 200, response.text
    assert len(attempts) == 2
    assert attempts[0].tts_tasks[0].status == "cancelled"
    assert raw(project_id)["tts_tasks"][0]["status"] == "running"
    assert cancelled == []
