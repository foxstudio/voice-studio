"""Partial acknowledgements bind their commit, not a consumed full snapshot."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app
from app.domains.video_localization import draft_store, service
from app.domains.video_localization.schemas import VideoLocalizationDraft
from app.schemas.voice_studio import AppSettings, ProjectCreate
from app.services import database, project_store, settings_store


@pytest.fixture
def isolated_store(tmp_path):
    # conftest isolates all import-time application paths before this module.
    # This test's additional DB/settings override is restored for later tests.
    previous_db = database.DB_PATH
    database.set_db_path(tmp_path / "config" / "voice_studio.db")
    try:
        settings_store.update(AppSettings(
            data_dir=str(tmp_path), voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"), export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"), cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        ))
        yield
    finally:
        database.set_db_path(previous_db)


def test_partial_timeline_ack_keeps_its_commit_revision_without_consuming_new_snapshot(isolated_store, monkeypatch):
    project = project_store.create_project(ProjectCreate(name="isolated timeline revision"))
    old = {"clip_id": "old", "track_id": "dub", "result_id": "old-result",
           "start_ms": 0, "end_ms": 1000, "source_start_ms": 0,
           "source_end_ms": 1000, "status": "ready"}
    new = {**old, "clip_id": "new", "result_id": "new-result",
           "start_ms": 2000, "end_ms": 3000}
    draft_store.save(project.project_id, VideoLocalizationDraft(timeline_clips=[old]), intent="runtime")
    update = service.update_video_localization_timeline_edit
    observations = {}

    def update_then_another_writer(project_id, patch):
        # Real commit R1 finishes and releases the shared lock. Another writer
        # commits R2 before the route resumes to form its response.
        result = update(project_id, patch)
        observations["mutation_revision"] = project_store.get_project_repository_revision(project_id)
        service.update_video_localization_atomic(
            project_id,
            lambda current: current.model_copy(update={
                "timeline_clips": [*current.timeline_clips, new],
            }),
            intent="runtime",
        )
        observations["latest_revision"] = project_store.get_project_repository_revision(project_id)
        return result

    monkeypatch.setattr(service, "update_video_localization_timeline_edit", update_then_another_writer)
    client = TestClient(app)
    response = client.patch(
        f"/api/projects/{project.project_id}/video-localization/timeline-edit",
        json={"clip_patches": [{"clip_id": "old", "expected_generation_identity": "old-result",
                                "expected_editable_fields": {
                                    "start_ms": 0,
                                    "end_ms": 1000,
                                    "source_start_ms": 0,
                                    "source_end_ms": 1000,
                                    "media_source_clip_id": None,
                                    "dub_lane": None,
                                },
                                "start_ms": 100, "end_ms": 1100}]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [clip["clip_id"] for clip in payload["timeline_clips"]] == ["old"]
    assert payload["timeline_clips"][0]["start_ms"] == 100
    assert observations["mutation_revision"] != observations["latest_revision"]
    assert payload["revision"] == observations["mutation_revision"]
    # The receipt belongs to R1 and includes only this command's entities.
    # R2 remains discoverable; neither revision can advance a full-read cursor
    # until that corresponding complete snapshot has actually been consumed.
    complete = client.get(f"/api/projects/{project.project_id}/video-localization")
    assert complete.status_code == 200, complete.text
    assert [clip["clip_id"] for clip in complete.json()["timeline_clips"]] == ["old", "new"]
