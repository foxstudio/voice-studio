from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.dubbing_mix import (  # noqa: E402
    DEFAULT_DUBBING_TRACK_STATES,
    default_dubbing_track_states,
    ensure_initial_dubbing_mix,
    has_placed_dubbing_clip,
)
from app.domains.video_localization.exporting import (  # noqa: E402
    _resolved_audio_track_states,
)
from app.domains.video_localization.schemas import VideoLocalizationDraft  # noqa: E402


def _draft(clips, *, ui_state=None):
    return VideoLocalizationDraft(
        timeline_clips=list(clips),
        ui_state=dict(ui_state or {}),
    )


def _dub_clip(*, clip_id="clip_dub_1", status="ready", provisional=False):
    clip = {
        "clip_id": clip_id,
        "track_id": "dub",
        "start_ms": 0,
        "end_ms": 1000,
        "status": status if not provisional else "running",
    }
    if not provisional:
        clip["audio_path"] = "/tmp/example.wav"
    return clip


def test_no_dubbing_clip_keeps_empty_mix():
    draft = _draft([])
    assert has_placed_dubbing_clip(draft) is False
    assert ensure_initial_dubbing_mix(draft) == draft
    assert draft.ui_state == {}


def test_provisional_dubbing_clip_does_not_set_mix():
    draft = _draft([_dub_clip(provisional=True)])
    assert has_placed_dubbing_clip(draft) is False
    assert ensure_initial_dubbing_mix(draft).ui_state == {}


def test_first_durable_dubbing_clip_writes_shared_defaults():
    draft = _draft([_dub_clip()], ui_state={"playhead_ms": 1234})
    placed = ensure_initial_dubbing_mix(draft)
    assert placed.ui_state["track_states"] == DEFAULT_DUBBING_TRACK_STATES
    assert placed.ui_state["playhead_ms"] == 1234
    assert placed.ui_state["track_states"]["original"]["muted"] is True
    assert placed.ui_state["track_states"]["vocals"]["muted"] is True
    assert placed.ui_state["track_states"]["background"]["muted"] is False
    assert placed.ui_state["track_states"]["dub"]["muted"] is False


def test_user_arranged_mix_is_never_overwritten():
    arranged = {
        "original": {"muted": False, "solo": False, "volume": 1.0},
        "vocals": {"muted": False, "solo": True, "volume": 1.0},
    }
    draft = _draft([_dub_clip()], ui_state={"track_states": arranged})
    placed = ensure_initial_dubbing_mix(draft)
    assert placed is draft
    assert placed.ui_state["track_states"] == arranged


def test_mix_defaults_are_isolated_per_project():
    first = default_dubbing_track_states()
    first["original"]["muted"] = False
    assert DEFAULT_DUBBING_TRACK_STATES["original"]["muted"] is True


def test_export_mix_falls_back_to_the_same_defaults():
    states = _resolved_audio_track_states(_draft([_dub_clip()]))
    assert states == DEFAULT_DUBBING_TRACK_STATES


def test_export_mix_keeps_user_arrangement():
    arranged = {
        "original": {"muted": False, "solo": True, "volume": 0.5},
        "dub": {"muted": True, "solo": False, "volume": 2.0},
    }
    states = _resolved_audio_track_states(
        _draft([_dub_clip()], ui_state={"track_states": arranged})
    )
    assert states["original"] == {"muted": False, "solo": True, "volume": 0.5}
    assert states["dub"] == {"muted": True, "solo": False, "volume": 2.0}
    assert states["vocals"]["muted"] is True


def _client(tmp_path: Path):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models.schemas import AppSettings
    from app.services import database, settings_store

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


def _durable_dub_clip(clip_id: str = "clip_dub_first") -> dict:
    return {
        "clip_id": clip_id,
        "track_id": "dub",
        "status": "ready",
        "start_ms": 1000,
        "end_ms": 2600,
        "audio_path": "/tmp/example.wav",
    }


def test_content_write_sets_the_initial_mix(tmp_path: Path):
    from app.domains.video_localization import draft_store, service

    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "初始混音写入", "description": ""},
    ).json()["project_id"]
    assert client.put(
        f"/api/projects/{project_id}/video-localization",
        json={"project_type": "video_localization", "schema_version": "v1"},
    ).status_code == 200

    service.update_video_localization_atomic(
        project_id,
        lambda current: current.model_copy(
            update={"timeline_clips": [_durable_dub_clip()]}
        ),
        intent="content",
    )

    stored = draft_store.get(project_id)
    assert stored is not None
    assert stored.ui_state["track_states"] == DEFAULT_DUBBING_TRACK_STATES


def test_content_write_keeps_a_user_arranged_mix(tmp_path: Path):
    from app.domains.video_localization import draft_store, service

    client = _client(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "用户混音保持", "description": ""},
    ).json()["project_id"]
    arranged = {
        "original": {"muted": False, "solo": True, "volume": 1.0},
    }
    assert client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "ui_state": {"track_states": arranged},
        },
    ).status_code == 200

    service.update_video_localization_atomic(
        project_id,
        lambda current: current.model_copy(
            update={"timeline_clips": [_durable_dub_clip()]}
        ),
        intent="content",
    )

    stored = draft_store.get(project_id)
    assert stored is not None
    assert stored.ui_state["track_states"] == arranged
