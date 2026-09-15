from types import SimpleNamespace

from app.domains.video_localization.audio_access import timeline_clip_audio_path
from app.domains.video_localization import service as video_localization_service
from app.domains.video_localization.media_assets import (
    cache_project_timeline_audio_paths,
    cached_project_timeline_audio_paths,
    invalidate_project_timeline_audio_paths,
)


def test_timeline_clip_audio_path_resolves_stable_media_source_after_split(tmp_path):
    audio_path = tmp_path / "dub.wav"
    audio_path.write_bytes(b"RIFF")
    draft = SimpleNamespace(
        source_media=SimpleNamespace(audio_path=None),
        stems=SimpleNamespace(original_audio_path=None, vocals_clean_path=None, background_path=None),
        timeline_clips=[
            {
                "clip_id": "clip_localized_0001_part_2",
                "media_source_clip_id": "clip_localized_0001",
                "audio_path": str(audio_path),
            }
        ],
    )

    assert timeline_clip_audio_path(draft, "clip_localized_0001") == audio_path


def test_timeline_audio_index_covers_split_media_sources_and_independent_tracks(tmp_path):
    source_path = tmp_path / "source.wav"
    dub_path = tmp_path / "dub.wav"
    source_path.write_bytes(b"RIFF")
    dub_path.write_bytes(b"RIFF")
    project_id = "timeline-audio-index-test"
    draft = SimpleNamespace(
        source_media=SimpleNamespace(audio_path=str(source_path)),
        stems=SimpleNamespace(original_audio_path=None, vocals_clean_path=None, background_path=None),
        timeline_clips=[
            {
                "clip_id": "clip_part_2",
                "media_source_clip_id": "clip_master",
                "audio_path": str(dub_path),
            }
        ],
    )

    try:
        paths = cache_project_timeline_audio_paths(
            project_id,
            draft,
            resolved_media={
                "source_audio": source_path,
                "vocals": None,
                "background": None,
            },
        )
        assert paths["media_original"] == source_path
        assert paths["clip_part_2"] == dub_path
        assert paths["clip_master"] == dub_path
        assert cached_project_timeline_audio_paths(project_id) == paths
    finally:
        invalidate_project_timeline_audio_paths(project_id)


def test_client_split_inherits_backend_owned_audio_from_media_source(tmp_path):
    audio_path = tmp_path / "dub.wav"
    audio_path.write_bytes(b"RIFF")
    current = [
        {
            "clip_id": "clip_master",
            "track_id": "dub",
            "result_id": "result-1",
            "generation_id": "generation-1",
            "audio_path": str(audio_path),
            "status": "ready",
            "start_ms": 0,
            "end_ms": 2_000,
        }
    ]
    incoming = [
        {
            **current[0],
            "media_source_clip_id": "clip_master",
            "end_ms": 1_000,
            "source_end_ms": 1_000,
        },
        {
            **current[0],
            "clip_id": "clip_master_part_2",
            "media_source_clip_id": "clip_master",
            "start_ms": 1_000,
            "source_start_ms": 1_000,
        },
    ]

    merged = video_localization_service._merge_client_timeline_clips(
        current,
        incoming,
        explicitly_added_clip_ids={"clip_master_part_2"},
    )

    child = next(item for item in merged if item["clip_id"] == "clip_master_part_2")
    assert child["audio_path"] == str(audio_path)
    assert child["result_id"] == "result-1"
    assert child["generation_id"] == "generation-1"


def test_stale_full_save_cannot_remove_durable_timeline_clip_by_omission(tmp_path):
    audio_path = tmp_path / "history.wav"
    audio_path.write_bytes(b"RIFF")
    current = [{
        "clip_id": "durable-history-clip",
        "track_id": "dub",
        "result_id": "result-1",
        "generation_id": "generation-1",
        "audio_path": str(audio_path),
        "status": "ready",
        "start_ms": 1_000,
        "end_ms": 2_000,
    }]

    merged = video_localization_service._merge_client_timeline_clips(current, [])

    assert [item["clip_id"] for item in merged] == ["durable-history-clip"]


def test_explicit_timeline_deletion_removes_durable_clip(tmp_path):
    audio_path = tmp_path / "history.wav"
    audio_path.write_bytes(b"RIFF")
    current = [{
        "clip_id": "durable-history-clip",
        "track_id": "dub",
        "result_id": "result-1",
        "generation_id": "generation-1",
        "audio_path": str(audio_path),
        "status": "ready",
        "start_ms": 1_000,
        "end_ms": 2_000,
    }]

    merged = video_localization_service._merge_client_timeline_clips(
        current,
        [],
        explicitly_deleted_clips={"durable-history-clip": "generation-1"},
    )

    assert merged == []


def test_stale_timeline_deletion_cannot_remove_replacement_with_same_clip_id(tmp_path):
    audio_path = tmp_path / "replacement.wav"
    audio_path.write_bytes(b"RIFF")
    replacement = [{
        "clip_id": "stable-clip-id",
        "track_id": "dub",
        "task_id": "task-new",
        "result_id": "result-new",
        "audio_path": str(audio_path),
        "status": "ready",
        "start_ms": 1_000,
        "end_ms": 2_000,
    }]

    merged = video_localization_service._merge_client_timeline_clips(
        replacement,
        [],
        explicitly_deleted_clips={"stable-clip-id": "task-old"},
    )

    assert merged == replacement


def test_timeline_clip_audio_file_recovers_persisted_split_from_project_history(
    monkeypatch,
    tmp_path,
):
    project_id = "project-1"
    audio_path = tmp_path / "history.wav"
    audio_path.write_bytes(b"RIFF")
    draft = SimpleNamespace(
        timeline_clips=[
            {
                "clip_id": "clip_master_part_2",
                "media_source_clip_id": "clip_master",
                "result_id": "result-1",
                "audio_path": None,
            }
        ]
    )
    history = SimpleNamespace(
        project_id=project_id,
        parameter_snapshot={"source": "video_localization"},
    )
    monkeypatch.setattr(
        video_localization_service.media_assets,
        "cached_project_timeline_audio_paths",
        lambda _project_id: {},
    )
    monkeypatch.setattr(
        video_localization_service.draft_store,
        "get",
        lambda _project_id: draft,
    )
    monkeypatch.setattr(
        video_localization_service.history_store,
        "get",
        lambda result_id: history if result_id == "result-1" else None,
    )
    monkeypatch.setattr(
        video_localization_service.history_store,
        "audio_path",
        lambda result_id: audio_path if result_id == "result-1" else None,
    )

    assert (
        video_localization_service.timeline_clip_audio_file(
            project_id,
            "clip_master",
        )
        == audio_path
    )
