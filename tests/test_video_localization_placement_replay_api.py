"""Placement responses must describe one committed snapshot, not a newer writer."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_video_localization_placement_replay import workspace  # noqa: F401 - shared owned-storage fixture

from app.api import video_localization as api
from app.domains.video_localization import draft_store, service
from app.services import project_store


def test_history_response_uses_its_committed_revision_after_another_writer(request, monkeypatch):
    project_id, history = request.getfixturevalue("workspace")
    original_apply = service.apply_tts_history_to_timeline
    states = {}

    def apply_then_another_writer(*args, **kwargs):
        committed = original_apply(*args, **kwargs)
        states["committed"] = committed
        states["committed_revision"] = project_store.get_project_repository_revision(project_id)
        edited = [
            {**clip, "source_start_ms": 300, "start_ms": clip["start_ms"] + 300}
            for clip in committed.timeline_clips
        ]
        states["newer"] = draft_store.save(
            project_id, committed.model_copy(update={"timeline_clips": edited}), intent="content",
        )
        states["newer_revision"] = project_store.get_project_repository_revision(project_id)
        return committed

    monkeypatch.setattr(service, "apply_tts_history_to_timeline", apply_then_another_writer)
    app = FastAPI()
    app.include_router(api.router, prefix="/projects")
    with TestClient(app) as client:
        response = client.post(
            f"/projects/{project_id}/video-localization/timeline-clips/history/{history.result_id}/apply",
            json={"request_id": "history-request", "segment_id": "subtitle", "new_clip_id": "history-clip",
                  "start_ms": 1000, "dub_lane": 0, "force_new": True},
        )
    assert response.status_code == 200, response.text
    result = response.json()
    assert states["newer_revision"] != states["committed_revision"]
    assert result["revision"] == str(states["committed_revision"])
    assert result["timeline_clips"][0]["source_start_ms"] == 0
    assert draft_store.get(project_id).timeline_clips[0]["source_start_ms"] == 300
    assert result["affected_clip_ids"] == ["history-clip"]
