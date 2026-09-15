from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import database, settings_store, task_queue  # noqa: E402


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


def _project_with_draft(client: TestClient, draft: dict) -> str:
    project = client.post("/api/projects", json={"name": "字幕关系事务", "description": ""}).json()
    project_id = project["project_id"]
    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={"project_type": "video_localization", "schema_version": "v1", **draft},
    )
    assert response.status_code == 200
    return project_id


def _cue(cue_id: str, start_ms: int, end_ms: int, word_id: str, text: str) -> dict:
    return {
        "cue_id": cue_id,
        "speaker_id": "speaker_01",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "en_subtitle_text": text,
        "source_text_raw": text,
        "source_word_ids": [word_id],
        "source_duration_ms": end_ms - start_ms,
        "timing_confidence": "high",
    }


def _subtitle(
    subtitle_id: str,
    start_ms: int,
    end_ms: int,
    source_ids: list[str],
    word_ids: list[str],
) -> dict:
    return {
        "subtitle_id": subtitle_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "text": f"台词 {subtitle_id}",
        "tts_text": f"口播 {subtitle_id}",
        "linked_cue_id": source_ids[0] if source_ids else None,
        "source_cue_ids": source_ids,
        "source_word_ids": word_ids,
        "quality_flags": [],
    }


def test_delete_source_cue_api_commits_orphan_and_partial_remap(tmp_path: Path):
    client = _client(tmp_path)
    project_id = _project_with_draft(
        client,
        {
            "cues": [
                _cue("cue_0001", 0, 1_000, "word_01", "One"),
                _cue("cue_0002", 1_000, 2_000, "word_02", "Two"),
            ],
            "localized_subtitles": [
                _subtitle("localized_0001", 0, 1_000, ["cue_0001"], ["word_01"]),
                _subtitle(
                    "localized_0002",
                    1_000,
                    2_000,
                    ["cue_0001", "cue_0002"],
                    ["word_01", "word_02"],
                ),
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_keep",
                    "track_id": "dub",
                    "subtitle_id": "localized_0001",
                    "cue_id": "cue_0001",
                    "source_cue_ids": ["cue_0001"],
                }
            ],
        },
    )

    response = client.delete(f"/api/projects/{project_id}/video-localization/cues/cue_0001")

    assert response.status_code == 200
    payload = response.json()
    assert [item["cue_id"] for item in payload["cues"]] == ["cue_0002"]
    orphaned, retained = payload["localized_subtitles"]
    assert orphaned["source_cue_ids"] == []
    assert {"source_mapping_orphaned", "source_mapping_needs_review"}.issubset(orphaned["quality_flags"])
    assert retained["source_cue_ids"] == ["cue_0002"]
    assert retained["source_word_ids"] == ["word_02"]
    assert payload["timeline_clips"][0]["clip_id"] == "clip_keep"
    assert payload["timeline_clips"][0]["source_cue_ids"] == []
    persisted = client.get(f"/api/projects/{project_id}/video-localization").json()
    assert persisted["localized_subtitles"] == payload["localized_subtitles"]


def test_delete_localized_subtitle_api_detaches_audio_and_clears_source_mirror(tmp_path: Path):
    client = _client(tmp_path)
    cue = _cue("cue_0001", 0, 1_000, "word_01", "One")
    cue["zh_localized_subtitle_text"] = "台词 localized_0001"
    cue["tts_recommended_text"] = "口播 localized_0001"
    project_id = _project_with_draft(
        client,
        {
            "cues": [cue],
            "localized_subtitles": [
                _subtitle("localized_0001", 0, 1_000, ["cue_0001"], ["word_01"]),
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_keep_audio",
                    "track_id": "dub",
                    "subtitle_id": "localized_0001",
                    "target_subtitle_ids": ["localized_0001"],
                    "cue_id": "cue_0001",
                    "source_cue_ids": ["cue_0001"],
                    "audio_path": "generated.wav",
                }
            ],
        },
    )

    response = client.delete(
        f"/api/projects/{project_id}/video-localization/localized-subtitles/localized_0001"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["localized_subtitles"] == []
    assert payload["cues"][0]["zh_localized_subtitle_text"] is None
    assert payload["timeline_clips"][0]["clip_id"] == "clip_keep_audio"
    assert payload["timeline_clips"][0]["subtitle_id"] is None
    assert payload["timeline_clips"][0]["target_subtitle_ids"] == []


def test_edit_localized_spoken_text_cancels_active_tts_but_keeps_ready_clip(
    tmp_path: Path,
    monkeypatch,
):
    cancelled: list[str] = []
    monkeypatch.setattr(
        task_queue,
        "cancel_task",
        lambda task_id: cancelled.append(task_id),
    )
    subtitle = _subtitle(
        "localized_0001",
        0,
        1_000,
        ["cue_0001"],
        ["word_01"],
    )
    subtitle.update(
        {
            "tts_result_id": "result_01",
            "tts_generation_id": "generation_01",
            "tts_audio_path": "/tmp/generated_01.wav",
        }
    )
    target = {
        "subtitle_ids": ["localized_0001"],
        "start_ms": 0,
        "end_ms": 1_000,
        "text": subtitle["tts_text"],
    }
    client = _client(tmp_path)
    project_id = _project_with_draft(
        client,
        {
            "cues": [
                _cue("cue_0001", 0, 1_000, "word_01", "One"),
            ],
            "localized_subtitles": [subtitle],
            "timeline_clips": [
                {
                    "clip_id": "clip_01",
                    "track_id": "dub",
                    "subtitle_id": "localized_0001",
                    "target_subtitle_ids": ["localized_0001"],
                    "task_id": "task_01",
                    "generation_id": "generation_01",
                    "audio_path": "/tmp/generated_01.wav",
                }
            ],
            "tts_tasks": [
                {
                    "workflow_id": "workflow_01",
                    "project_id": "project_audit_metadata",
                    "segment_id": "localized_0001",
                    "subtitle_summary": "第一句",
                    "text": subtitle["tts_text"],
                    "source_cue_ids": ["cue_0001"],
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "status": "running",
                    "generation_task_id": "task_01",
                    "timeline_clip_id": "clip_01",
                    "stages": [
                        {
                            "kind": "generation",
                            "status": "running",
                            "parameters": {
                                "video_localization_parameter_pack": {
                                    "target": target,
                                }
                            },
                        },
                        {
                            "kind": "placement",
                            "parameters": {"target_snapshot": target},
                        },
                    ],
                }
            ],
        },
    )
    # The task's project field is audit metadata; the service binds by the
    # enclosing project draft and never trusts it for cancellation.

    response = client.patch(
        (
            f"/api/projects/{project_id}/video-localization/"
            "localized-subtitles/localized_0001"
        ),
        json={"tts_text": "修改后的口播"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert cancelled == ["task_01"]
    assert len(payload["timeline_clips"]) == 1
    assert payload["timeline_clips"][0]["clip_id"] == "clip_01"
    assert payload["timeline_clips"][0]["generation_id"] == (
        "generation_01"
    )
    assert payload["timeline_clips"][0][
        "tts_target_binding_status"
    ] == "stale"
    assert payload["timeline_clips"][0]["tts_target_text"] == (
        subtitle["tts_text"]
    )
    assert payload["tts_tasks"][0]["status"] == "cancelled"
    assert {
        "workflow_01",
        "task_01",
    }.issubset(payload["ui_state"]["discarded_tts_task_ids"])
    assert "generation_01" not in payload["ui_state"][
        "discarded_tts_task_ids"
    ]
    assert payload["localized_subtitles"][0]["tts_audio_path"] is None


def test_editing_visible_tts_text_updates_the_spoken_segment_and_plan_snapshot(
    tmp_path: Path,
):
    client = _client(tmp_path)
    first = _subtitle(
        "localized_0001",
        0,
        1_000,
        ["cue_0001"],
        ["word_01"],
    )
    second = _subtitle(
        "localized_0002",
        1_000,
        2_000,
        ["cue_0002"],
        ["word_02"],
    )
    first["spoken_segment_id"] = "spoken_0001"
    second["spoken_segment_id"] = "spoken_0001"
    project_id = _project_with_draft(
        client,
        {
            "cues": [
                _cue("cue_0001", 0, 1_000, "word_01", "First"),
                _cue("cue_0002", 1_000, 2_000, "word_02", "Second"),
            ],
            "localized_subtitles": [first, second],
            "localized_spoken_segments": [
                {
                    "segment_id": "spoken_0001",
                    "paragraph_id": "paragraph_0001",
                    "text": "口播 localized_0001口播 localized_0002",
                    "start_ms": 0,
                    "end_ms": 2_000,
                    "source_cue_ids": ["cue_0001", "cue_0002"],
                    "source_word_ids": ["word_01", "word_02"],
                }
            ],
        },
    )

    response = client.patch(
        (
            f"/api/projects/{project_id}/video-localization/"
            "localized-subtitles/localized_0002"
        ),
        json={"tts_text": "一零八零 P。"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["localized_spoken_segments"][0]["text"] == (
        "口播 localized_0001一零八零 P。"
    )
    snapshot = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/snapshot"
    )
    assert snapshot.status_code == 200, snapshot.text
    unit = snapshot.json()["semantic_units"][0]
    assert unit["spoken_text"] == "口播 localized_0001一零八零 P。"


def test_merge_source_cues_api_updates_stable_links(tmp_path: Path):
    client = _client(tmp_path)
    project_id = _project_with_draft(
        client,
        {
            "cues": [
                _cue("cue_0001", 0, 1_000, "word_01", "One"),
                _cue("cue_0002", 1_000, 2_000, "word_02", "Two"),
            ],
            "localized_subtitles": [
                _subtitle(
                    "localized_0001",
                    0,
                    2_000,
                    ["cue_0001", "cue_0002"],
                    ["word_01", "word_02"],
                )
            ],
        },
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/cues/merge",
        json={"cue_ids": ["cue_0001", "cue_0002"], "survivor_cue_id": "cue_0001"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["cue_id"] for item in payload["cues"]] == ["cue_0001"]
    assert payload["cues"][0]["source_word_ids"] == ["word_01", "word_02"]
    assert payload["cues"][0]["en_subtitle_text"] == "One\nTwo"
    assert payload["localized_subtitles"][0]["source_cue_ids"] == ["cue_0001"]
    assert payload["localized_subtitles"][0]["linked_cue_id"] == "cue_0001"


def test_split_source_cue_api_routes_targets_by_frozen_word_ids(tmp_path: Path):
    client = _client(tmp_path)
    parent = _cue("cue_parent", 0, 2_000, "word_01", "One two")
    parent["source_word_ids"] = ["word_01", "word_02"]
    project_id = _project_with_draft(
        client,
        {
            "cues": [parent],
            "localized_subtitles": [
                _subtitle("localized_0001", 0, 1_000, ["cue_parent"], ["word_01"]),
                _subtitle("localized_0002", 1_000, 2_000, ["cue_parent"], ["word_02"]),
            ],
        },
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/cues/cue_parent/split",
        json={
            "replacements": [
                _cue("cue_parent", 0, 1_000, "word_01", "One"),
                _cue("cue_child", 1_000, 2_000, "word_02", "two"),
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["cue_id"] for item in payload["cues"]] == ["cue_parent", "cue_child"]
    assert [item["source_cue_ids"] for item in payload["localized_subtitles"]] == [
        ["cue_parent"],
        ["cue_child"],
    ]
    assert all("source_mapping_needs_review" not in item["quality_flags"] for item in payload["localized_subtitles"])


def test_split_localized_subtitle_api_uses_explicit_word_partition(tmp_path: Path):
    client = _client(tmp_path)
    project_id = _project_with_draft(
        client,
        {
            "cues": [
                _cue("cue_0001", 0, 1_000, "word_01", "One"),
                _cue("cue_0002", 1_000, 2_000, "word_02", "Two"),
            ],
            "localized_subtitles": [
                _subtitle(
                    "localized_0001",
                    0,
                    2_000,
                    ["cue_0001", "cue_0002"],
                    ["word_01", "word_02"],
                )
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_keep",
                    "track_id": "dub",
                    "subtitle_id": "localized_0001",
                    "cue_id": "cue_0001",
                    "source_cue_ids": ["cue_0001", "cue_0002"],
                }
            ],
        },
    )
    children = [
        _subtitle("localized_0001", 0, 1_000, [], []),
        _subtitle("localized_0002", 1_000, 2_000, [], []),
    ]

    response = client.post(
        f"/api/projects/{project_id}/video-localization/localized-subtitles/localized_0001/split",
        json={
            "children": children,
            "source_word_ids_by_subtitle_id": {
                "localized_0001": ["word_01"],
                "localized_0002": ["word_02"],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    first, second = payload["localized_subtitles"]
    assert first["subtitle_id"] == "localized_0001"
    assert first["source_cue_ids"] == ["cue_0001"]
    assert first["source_word_ids"] == ["word_01"]
    assert second["subtitle_id"] == "localized_0002"
    assert second["source_cue_ids"] == ["cue_0002"]
    assert second["source_word_ids"] == ["word_02"]
    assert payload["timeline_clips"][0]["clip_id"] == "clip_keep"
    assert payload["timeline_clips"][0]["source_cue_ids"] == ["cue_0001"]


def test_subtitle_mutation_api_returns_specific_not_found_and_invalid_codes(tmp_path: Path):
    client = _client(tmp_path)
    project_id = _project_with_draft(
        client,
        {"cues": [_cue("cue_0001", 0, 1_000, "word_01", "One")]},
    )

    missing = client.delete(f"/api/projects/{project_id}/video-localization/cues/missing")
    invalid = client.post(
        f"/api/projects/{project_id}/video-localization/cues/merge",
        json={"cue_ids": ["cue_0001", "missing"]},
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_CUE_NOT_FOUND"
    assert invalid.status_code == 404
    assert invalid.json()["error"]["code"] == "VIDEO_LOCALIZATION_SOURCE_CUE_NOT_FOUND"
