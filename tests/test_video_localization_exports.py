from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import unquote

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    draft_store,
    exporting,
    media_assets,
    quality_gate,
)
from app.domains.video_localization.export_contracts import (  # noqa: E402
    VideoLocalizationMediaExportCommandRequest,
    VideoLocalizationMediaExportRequest,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationDubSubtitleCue,
    VideoLocalizationOperation,
    VideoLocalizationSubtitleCue,
)
from app.api import video_localization as video_localization_api  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import (  # noqa: E402
    audio_tools,
    database,
    project_store,
    settings_store,
)
from app.services.video_localization_exports import (  # noqa: E402
    video_localization_exports,
)


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


def test_publication_guard_blocks_independent_project_writer(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "发布锁验收", "description": "before"},
    ).json()
    project_id = project["project_id"]
    writer_started = threading.Event()
    writer_finished = threading.Event()

    def update_from_independent_connection() -> None:
        current = project_store.get_project(project_id)
        assert current is not None
        current.description = "after"
        writer_started.set()
        project_store.save_project(current)
        writer_finished.set()

    with project_store.publication_guard(project_id):
        thread = threading.Thread(
            target=update_from_independent_connection,
            daemon=True,
        )
        thread.start()
        assert writer_started.wait(1)
        assert not writer_finished.wait(0.2)

    assert writer_finished.wait(2)
    thread.join(timeout=2)
    saved = project_store.get_project(project_id)
    assert saved is not None
    assert saved.description == "after"


def _localized_video_project(
    client: TestClient,
    tmp_path: Path,
) -> tuple[str, Path]:
    project = client.post(
        "/api/projects",
        json={"name": "显式导出命令", "description": ""},
    ).json()
    project_id = project["project_id"]
    package_root = media_assets.project_video_localization_dir(project_id)
    source_video = package_root / "source" / "source.mp4"
    source_video.parent.mkdir(parents=True, exist_ok=True)
    source_video.write_bytes(b"fake-video")
    tts_path = package_root / "tts" / "cue.wav"
    audio_tools.write_audio(
        tts_path,
        np.zeros(24_000, dtype=np.float32),
        24_000,
    )
    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "source.mp4",
                "duration_ms": 1000,
                "video_path": str(source_video),
            },
            "cues": [
                {
                    "cue_id": "cue_1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "audio_route": "clone_from_source",
                    "tts_audio_path": str(tts_path),
                }
            ],
            "timeline_clips": [
                {
                    "clip_id": "clip_cue_1",
                    "cue_id": "cue_1",
                    "track_id": "dub",
                    "dub_lane": 0,
                    "start_ms": 0,
                    "end_ms": 1000,
                    "source_start_ms": 0,
                    "source_end_ms": 1000,
                    "audio_path": str(tts_path),
                    "status": "ready",
                }
            ],
        },
    )
    assert response.status_code == 200
    draft = draft_store.get(project_id)
    assert draft is not None
    draft_store.save(
        project_id,
        draft.model_copy(
            update={
                "cues": [
                    draft.cues[0].model_copy(
                        update={"tts_audio_path": str(tts_path)}
                    )
                ]
            }
        ),
        intent="runtime",
    )
    return project_id, source_video


def test_json_export_get_does_not_write_draft(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)

    def unexpected_save(*_args, **_kwargs):
        raise AssertionError("read-only JSON export must not save the draft")

    monkeypatch.setattr(draft_store, "save", unexpected_save)

    response = client.get(
        f"/api/projects/{project_id}/video-localization/export"
    )

    assert response.status_code == 200
    assert response.json()["project_id"] == project_id


def test_media_export_contract_keeps_only_relevant_options():
    request = VideoLocalizationMediaExportRequest(
        kind="audio",
        audio_tracks=["background", "background"],
        dub_lanes=[2, 1],
        subtitle_tracks=["localized"],
        audio_format="mp3",
    )

    assert request.audio_tracks == ["background"]
    assert request.dub_lanes == []
    assert request.subtitle_tracks == []
    assert request.audio_format == "mp3"
    assert request.video_quality == "source"
    assert request.audio_bitrate_kbps == "source"
    assert request.localized_subtitle_variant == "localized"


def test_media_export_contract_accepts_current_dub_subtitle_variant():
    request = VideoLocalizationMediaExportRequest(
        kind="video",
        subtitle_tracks=["localized"],
        localized_subtitle_variant="dub",
    )

    assert request.localized_subtitle_variant == "dub"


def test_media_export_contract_accepts_standalone_subtitle_export():
    request = VideoLocalizationMediaExportRequest(
        kind="subtitle",
        audio_tracks=["background"],
        dub_lanes=[0],
        subtitle_tracks=["asr", "localized"],
    )

    assert request.audio_tracks == []
    assert request.dub_lanes == []
    assert request.subtitle_tracks == ["asr", "localized"]
    command = VideoLocalizationMediaExportCommandRequest(
        destination_id="destination_" + ("a" * 24),
        output_filename="用户确认字幕.srt",
        render=request,
    )
    assert command.output_filename == "用户确认字幕.srt"


def test_standalone_subtitle_export_writes_srt_without_source_media(
    tmp_path: Path,
):
    destination = tmp_path / "字幕成品.srt"
    draft = VideoLocalizationDraft(
        cues=[
            {
                "cue_id": "cue_1",
                "start_ms": 1000,
                "end_ms": 2500,
                "en_subtitle_text": "Hello world",
            }
        ]
    )
    request = VideoLocalizationMediaExportRequest(
        kind="subtitle",
        subtitle_tracks=["asr"],
    )

    manifest = exporting.media_export_file(
        "project_1",
        "字幕项目",
        draft,
        request,
        fingerprint="a" * 64,
        destination_path=destination,
    )

    assert destination.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,500\nHello world\n"
    )
    assert manifest["request"]["kind"] == "subtitle"


@pytest.mark.parametrize(
    ("output_filename", "kind", "audio_format"),
    [
        ("../escape.mp4", "video", "wav"),
        ("nested/result.mp4", "video", "wav"),
        ("nested\\result.mp4", "video", "wav"),
        ("bad\u0007name.mp4", "video", "wav"),
        ("wrong.wav", "video", "wav"),
        ("wrong.mp3", "audio", "wav"),
        ("wrong.wav", "audio", "mp3"),
        ("upper.MP4", "video", "wav"),
    ],
)
def test_media_export_command_rejects_unsafe_or_mismatched_filename(
    output_filename: str,
    kind: str,
    audio_format: str,
):
    with pytest.raises(ValueError):
        VideoLocalizationMediaExportCommandRequest(
            destination_id="destination_" + ("a" * 24),
            output_filename=output_filename,
            render={
                "kind": kind,
                "audio_tracks": (
                    ["background"] if kind == "audio" else []
                ),
                "audio_format": audio_format,
            },
        )


def test_media_export_filename_preview_uses_shared_naming_policy(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)

    response = client.post(
        (
            f"/api/projects/{project_id}/video-localization/"
            "export/filename-preview"
        ),
        json={
            "kind": "audio",
            "audio_tracks": ["background", "dub"],
            "audio_format": "mp3",
            "audio_bitrate_kbps": 192,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["output_filename"].startswith(
        "显式导出命令__音频__背景+配音__MP3-192K__"
    )
    assert "__版本-" in payload["output_filename"]
    assert payload["output_filename"].endswith(".mp3")
    assert "/" not in payload["output_filename"]
    assert "\\" not in payload["output_filename"]


def test_subtitle_export_filename_preview_uses_srt_extension(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)

    response = client.post(
        (
            f"/api/projects/{project_id}/video-localization/"
            "export/filename-preview"
        ),
        json={
            "kind": "subtitle",
            "subtitle_tracks": ["localized"],
            "localized_subtitle_variant": "dub",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["output_filename"].startswith(
        "显式导出命令__字幕__配音字幕__"
    )
    assert response.json()["output_filename"].endswith(".srt")


def test_media_export_fingerprint_includes_synthesized_dub_subtitles():
    draft = VideoLocalizationDraft(
        dub_subtitles=[
            VideoLocalizationDubSubtitleCue(
                subtitle_id="dub_1",
                start_ms=100,
                end_ms=900,
                text="第一版字幕",
                source_audio_sha256="a" * 64,
            )
        ]
    )
    request = VideoLocalizationMediaExportRequest(
        kind="video",
        subtitle_tracks=["localized"],
        localized_subtitle_variant="dub",
    )

    before = exporting.media_export_input_fingerprint(draft, request)
    changed = draft.model_copy(
        update={
            "dub_subtitles": [
                draft.dub_subtitles[0].model_copy(
                    update={"text": "已经修改的字幕"}
                )
            ]
        }
    )

    assert exporting.media_export_input_fingerprint(
        changed,
        request,
    ) != before


def test_media_export_fingerprint_ignores_advisory_production_state():
    draft = VideoLocalizationDraft()
    request = VideoLocalizationMediaExportRequest(
        kind="audio",
        audio_tracks=["dub"],
        audio_format="wav",
    )
    planned = draft.model_copy(
        update={
            "dubbing_production": draft.dubbing_production.model_copy(
                update={"enforcement_mode": "planned"}
            )
        }
    )

    assert exporting.media_export_input_fingerprint(
        planned,
        request,
    ) == exporting.media_export_input_fingerprint(draft, request)


def test_media_export_fingerprint_ignores_audition_solo_but_tracks_output_mix():
    request = VideoLocalizationMediaExportRequest(
        kind="audio",
        audio_tracks=["background", "dub"],
    )
    draft = VideoLocalizationDraft(
        ui_state={
            "track_states": {
                "background": {
                    "muted": False,
                    "solo": False,
                    "volume": 0.8,
                },
            },
            "dub_lane_states": {
                "0": {
                    "muted": False,
                    "solo": False,
                    "volume": 0.6,
                },
            },
            "disabled_media_tracks": ["original"],
        },
    )
    before = exporting.media_export_input_fingerprint(draft, request)
    soloed = draft.model_copy(
        update={
            "ui_state": {
                **draft.ui_state,
                "track_states": {
                    "background": {
                        **draft.ui_state["track_states"]["background"],
                        "solo": True,
                    },
                },
                "dub_lane_states": {
                    "0": {
                        **draft.ui_state["dub_lane_states"]["0"],
                        "solo": True,
                    },
                },
            },
        }
    )
    muted = draft.model_copy(
        update={
            "ui_state": {
                **draft.ui_state,
                "track_states": {
                    "background": {
                        **draft.ui_state["track_states"]["background"],
                        "muted": True,
                    },
                },
            },
        }
    )
    quieter = draft.model_copy(
        update={
            "ui_state": {
                **draft.ui_state,
                "dub_lane_states": {
                    "0": {
                        **draft.ui_state["dub_lane_states"]["0"],
                        "volume": 0.5,
                    },
                },
            },
        }
    )

    assert exporting.media_export_input_fingerprint(soloed, request) == before
    assert exporting.media_export_input_fingerprint(muted, request) != before
    assert exporting.media_export_input_fingerprint(quieter, request) != before


def test_media_export_fingerprint_ignores_cqc_metadata_but_tracks_timing():
    request = VideoLocalizationMediaExportRequest(
        kind="audio",
        audio_tracks=["dub"],
        audio_format="wav",
    )
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "track_id": "dub",
                "start_ms": 0,
                "end_ms": 1_000,
                "cqc_status": "not_reviewed",
            }
        ]
    )
    reviewed = draft.model_copy(
        update={
            "timeline_clips": [
                {
                    **draft.timeline_clips[0],
                    "cqc_status": "passed",
                    "cqc_report_version": "dubbing-candidate-cqc-report-v1",
                    "cqc_report": {"overall_status": "passed"},
                }
            ]
        }
    )
    retimed = reviewed.model_copy(
        update={
            "timeline_clips": [
                {**reviewed.timeline_clips[0], "end_ms": 900}
            ]
        }
    )

    before = exporting.media_export_input_fingerprint(draft, request)
    assert (
        exporting.media_export_input_fingerprint(reviewed, request)
        == before
    )
    assert (
        exporting.media_export_input_fingerprint(retimed, request)
        != before
    )


def test_dub_audio_export_does_not_require_production_audit(
    monkeypatch: pytest.MonkeyPatch,
):
    draft = VideoLocalizationDraft()
    request = VideoLocalizationMediaExportRequest(
        kind="audio",
        audio_tracks=["dub"],
        audio_format="wav",
    )

    def unexpected_audit(*_args, **_kwargs):
        raise AssertionError(
            "配音生产审核只能提示，不能阻止导出配音"
        )

    monkeypatch.setattr(
        quality_gate,
        "dubbing_production_export_blockers",
        unexpected_audit,
    )

    video_localization_exports._raise_delivery_blockers(
        "project_1",
        draft,
        request,
    )


def test_dub_subtitle_export_checks_track_not_production_audit(
    monkeypatch: pytest.MonkeyPatch,
):
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 1_000},
        dub_subtitles=[
            VideoLocalizationDubSubtitleCue(
                subtitle_id="dub_1",
                start_ms=0,
                end_ms=900,
                text="可导出的配音字幕",
                source_audio_sha256="a" * 64,
            )
        ],
    )

    def unexpected_audit(*_args, **_kwargs):
        raise AssertionError(
            "配音生产审核只能提示，不能阻止导出配音字幕"
        )

    monkeypatch.setattr(
        quality_gate,
        "dubbing_production_export_blockers",
        unexpected_audit,
    )

    assert quality_gate.dub_subtitle_export_blockers(draft) == []


def test_media_export_fingerprint_hashes_audio_bytes_not_only_file_metadata(
    tmp_path: Path,
):
    audio_path = tmp_path / "candidate.wav"
    audio_path.write_bytes(b"first-audio-bytes")
    original_stat = audio_path.stat()
    draft = VideoLocalizationDraft(
        timeline_clips=[
            {
                "clip_id": "clip_1",
                "track_id": "dub",
                "audio_path": str(audio_path),
                "start_ms": 0,
                "end_ms": 1_000,
            }
        ]
    )
    request = VideoLocalizationMediaExportRequest(
        kind="audio",
        audio_tracks=["dub"],
        audio_format="wav",
    )
    before = exporting.media_export_input_fingerprint(draft, request)

    audio_path.write_bytes(b"second-audio-byte")
    assert audio_path.stat().st_size == original_stat.st_size
    audio_path.touch()
    os.utime(
        audio_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )

    assert exporting.media_export_input_fingerprint(draft, request) != before


def test_timeline_edl_contains_synthesized_dub_subtitle_track():
    draft = VideoLocalizationDraft(
        dub_subtitles=[
            VideoLocalizationDubSubtitleCue(
                subtitle_id="dub_1",
                start_ms=100,
                end_ms=900,
                text="配音字幕",
                source_audio_sha256="b" * 64,
            )
        ]
    )

    edl = exporting.timeline_edl("project_1", "导出项目", draft)

    assert edl["dub_subtitles"] == [
        draft.dub_subtitles[0].model_dump(mode="json")
    ]


def test_custom_media_export_endpoint_passes_explicit_track_selection(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    captured = None
    destination_id = "destination_" + ("a" * 24)

    def fake_resolve(value_destination_id):
        assert value_destination_id == destination_id
        return tmp_path

    def fake_submit(value_project_id, kind, parameters):
        nonlocal captured
        assert value_project_id == project_id
        assert kind == "media_export"
        captured = parameters
        return VideoLocalizationOperation(
            project_id=project_id,
            kind="media_export",
            label="导出成品",
        )

    monkeypatch.setattr(
        video_localization_api.video_localization_export_destinations,
        "resolve_destination",
        fake_resolve,
    )
    monkeypatch.setattr(
        video_localization_api.video_localization_operations,
        "submit_operation",
        fake_submit,
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/export/render",
        json={
            "schema_version": "v1",
            "destination_id": destination_id,
            "output_filename": "用户确认的成品.mp4",
            "render": {
                "schema_version": "v1",
                "kind": "video",
                "audio_tracks": ["background", "dub"],
                "dub_lanes": [1],
                "subtitle_tracks": ["localized"],
                "video_size": "1080p",
                "video_quality": "high",
                "audio_format": "wav",
                "audio_bitrate_kbps": 256,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "media_export"
    assert captured is not None
    assert captured["destination_id"] == destination_id
    assert captured["output_filename"] == "用户确认的成品.mp4"
    assert captured["render"]["audio_tracks"] == ["background", "dub"]
    assert captured["render"]["dub_lanes"] == [1]
    assert captured["render"]["subtitle_tracks"] == ["localized"]
    assert captured["render"]["video_size"] == "1080p"


def test_media_export_rejects_stale_selected_localized_subtitles(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    draft = draft_store.get(project_id)
    assert draft is not None
    saved = draft_store.save(
        project_id,
        draft.model_copy(
            update={
                "localized_subtitles": [
                    VideoLocalizationSubtitleCue(
                        subtitle_id="localized_1",
                        start_ms=0,
                        end_ms=1000,
                        text="已经过期的字幕",
                    )
                ],
                "localization_state": {
                    "status": "draft",
                    "source_fingerprint": "stale-source",
                },
            }
        ),
        intent="content",
    )
    assert saved is not None

    def unexpected_render(*_args, **_kwargs):
        raise AssertionError("stale subtitles must fail before rendering")

    monkeypatch.setattr(
        exporting,
        "media_export_file",
        unexpected_render,
    )

    destination = tmp_path / "export"
    destination.mkdir()
    with pytest.raises(AppException) as raised:
        video_localization_exports.create_media_export_at(
            project_id,
            VideoLocalizationMediaExportRequest(
                kind="video",
                audio_tracks=[],
                subtitle_tracks=["localized"],
            ),
            destination,
            "stale.mp4",
        )
    assert raised.value.code == "VIDEO_LOCALIZATION_MEDIA_EXPORT_BLOCKED"
    assert raised.value.detail_dict["issues"][0]["code"] == (
        "LOCALIZATION_SOURCE_CHANGED"
    )


def test_direct_media_export_publishes_atomically_and_cleans_partial_files(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    destination = tmp_path / "chosen"
    destination.mkdir()
    observed_destination: Path | None = None

    def render(*_args, destination_path, on_progress, **_kwargs):
        nonlocal observed_destination
        observed_destination = destination_path
        assert destination_path.name.startswith(".")
        assert ".partial" in destination_path.name
        destination_path.write_bytes(b"complete-export")
        if on_progress:
            on_progress(1.0, "音频已保存")
        return {
            "output_path": str(destination_path),
            "mixed_tracks": [{"track_id": "dub"}],
        }

    monkeypatch.setattr(exporting, "media_export_file", render)
    progress: list[tuple[float, str]] = []
    result = video_localization_exports.create_media_export_at(
        project_id,
        VideoLocalizationMediaExportRequest(
            kind="audio",
            audio_tracks=["dub"],
            audio_format="wav",
        ),
        destination,
        "最终混音.wav",
        on_progress=lambda value, stage: progress.append(
            (value, stage)
        ),
    )

    assert result is not None
    assert result["filename"] == "最终混音.wav"
    final_path = destination / result["filename"]
    assert final_path.read_bytes() == b"complete-export"
    assert observed_destination is not None
    assert not observed_destination.exists()
    assert not list(destination.glob(".*.partial.*"))
    assert progress == [(1.0, "音频已保存")]


def test_direct_media_export_removes_partial_file_after_failure(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    destination = tmp_path / "chosen"
    destination.mkdir()

    def fail(*_args, destination_path, **_kwargs):
        destination_path.write_bytes(b"incomplete")
        raise AppException(
            500,
            "TEST_RENDER_FAILED",
            "测试渲染失败",
        )

    monkeypatch.setattr(exporting, "media_export_file", fail)
    with pytest.raises(AppException) as raised:
        video_localization_exports.create_media_export_at(
            project_id,
            VideoLocalizationMediaExportRequest(
                kind="audio",
                audio_tracks=["dub"],
                audio_format="wav",
            ),
            destination,
            "失败清理.wav",
        )

    assert raised.value.code == "TEST_RENDER_FAILED"
    assert list(destination.iterdir()) == []


def test_direct_media_export_rechecks_input_fingerprint_before_publish(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    destination = tmp_path / "chosen"
    destination.mkdir()

    def render(*_args, destination_path, **_kwargs):
        destination_path.write_bytes(b"must-not-publish")
        current = draft_store.get(project_id)
        assert current is not None
        draft_store.save(
            project_id,
            current.model_copy(
                update={
                    "timeline_clips": [
                        {
                            **current.timeline_clips[0],
                            "end_ms": 900,
                            "source_end_ms": 900,
                        }
                    ]
                }
            ),
            intent="content",
        )
        return {
            "output_path": str(destination_path),
            "mixed_tracks": [{"track_id": "dub"}],
        }

    monkeypatch.setattr(exporting, "media_export_file", render)

    with pytest.raises(AppException) as raised:
        video_localization_exports.create_media_export_at(
            project_id,
            VideoLocalizationMediaExportRequest(
                kind="audio",
                audio_tracks=["dub"],
                audio_format="wav",
            ),
            destination,
            "不能发布.wav",
        )

    assert raised.value.code == "VIDEO_LOCALIZATION_EXPORT_INPUT_CHANGED"
    assert list(destination.iterdir()) == []


def test_direct_media_export_rejects_existing_exact_filename_without_suffix(
    tmp_path: Path,
    monkeypatch,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    destination = tmp_path / "chosen"
    destination.mkdir()
    existing = destination / "已存在.wav"
    existing.write_bytes(b"keep-existing")

    def unexpected_render(*_args, **_kwargs):
        raise AssertionError("filename conflict must fail before rendering")

    monkeypatch.setattr(
        exporting,
        "media_export_file",
        unexpected_render,
    )
    with pytest.raises(AppException) as raised:
        video_localization_exports.create_media_export_at(
            project_id,
            VideoLocalizationMediaExportRequest(
                kind="audio",
                audio_tracks=["dub"],
                audio_format="wav",
            ),
            destination,
            existing.name,
        )

    assert raised.value.status_code == 409
    assert (
        raised.value.code
        == "VIDEO_LOCALIZATION_EXPORT_FILENAME_CONFLICT"
    )
    assert existing.read_bytes() == b"keep-existing"
    assert not (destination / "已存在-2.wav").exists()


def test_explicit_mixdown_uses_only_selected_audio_tracks(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    package_root = media_assets.project_video_localization_dir(project_id)
    source_audio = package_root / "source" / "source.wav"
    background = package_root / "stems" / "background.wav"
    audio_tools.write_audio(
        source_audio,
        np.full(24_000, 0.1, dtype=np.float32),
        24_000,
    )
    audio_tools.write_audio(
        background,
        np.full(24_000, 0.35, dtype=np.float32),
        24_000,
    )
    current = draft_store.get(project_id)
    assert current is not None
    next_draft = current.model_copy(
        update={
            "source_media": current.source_media.model_copy(
                update={"audio_path": str(source_audio)}
            ),
            "stems": current.stems.model_copy(
                update={"background_path": str(background)}
            ),
            "ui_state": {
                **current.ui_state,
                "track_states": {
                    "original": {
                        "muted": False,
                        "solo": True,
                        "volume": 1.0,
                    },
                    "background": {
                        "muted": True,
                        "solo": False,
                        "volume": 1.0,
                    },
                },
            },
        }
    )
    draft_store.save(project_id, next_draft, intent="runtime")
    destination = tmp_path / "selected.wav"

    mixed_tracks = exporting._write_localized_mixdown(
        destination,
        next_draft,
        None,
        1000,
        selected_tracks={"background"},
        selected_dub_lanes=set(),
    )

    assert destination.is_file()
    assert {item["track_id"] for item in mixed_tracks} == {"background"}
    audio, _ = audio_tools.read_audio(destination)
    assert float(np.mean(audio)) == pytest.approx(0.35, abs=0.02)


def test_default_mixdown_ignores_audition_solo_but_preserves_mutes(
    tmp_path: Path,
):
    original = tmp_path / "original.wav"
    background = tmp_path / "background.wav"
    lane_zero = tmp_path / "lane-zero.wav"
    lane_one = tmp_path / "lane-one.wav"
    lane_two = tmp_path / "lane-two.wav"
    for path, value in (
        (original, 0.10),
        (background, 0.40),
        (lane_zero, 0.20),
        (lane_one, 0.30),
        (lane_two, 0.40),
    ):
        audio_tools.write_audio(
            path,
            np.full(24_000, value, dtype=np.float32),
            24_000,
        )
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 1_000, "audio_path": str(original)},
        stems={"background_path": str(background)},
        ui_state={
            "track_states": {
                "original": {"muted": False, "solo": False, "volume": 1.0},
                "background": {"muted": True, "solo": False, "volume": 1.0},
            },
            "dub_lane_states": {
                "0": {"muted": False, "solo": False, "volume": 0.5},
                "1": {"muted": False, "solo": True, "volume": 0.5},
                "2": {"muted": True, "solo": True, "volume": 0.5},
            },
        },
        timeline_clips=[
            {
                "clip_id": "lane-zero",
                "track_id": "dub",
                "dub_lane": 0,
                "start_ms": 0,
                "end_ms": 1_000,
                "audio_path": str(lane_zero),
                "status": "ready",
            },
            {
                "clip_id": "lane-one",
                "track_id": "dub",
                "dub_lane": 1,
                "start_ms": 0,
                "end_ms": 1_000,
                "audio_path": str(lane_one),
                "status": "ready",
            },
            {
                "clip_id": "lane-two",
                "track_id": "dub",
                "dub_lane": 2,
                "start_ms": 0,
                "end_ms": 1_000,
                "audio_path": str(lane_two),
                "status": "ready",
            },
        ],
    )
    destination = tmp_path / "default-mix.wav"

    mixed_tracks = exporting._write_localized_mixdown(
        destination,
        draft,
        None,
        1_000,
    )

    assert [(item["track_id"], item.get("dub_lane")) for item in mixed_tracks] == [
        ("original", None),
        ("dub", 0),
        ("dub", 1),
    ]
    mixed, _ = audio_tools.read_audio(destination)
    assert float(np.mean(mixed)) == pytest.approx(0.35, abs=0.02)


def test_explicit_mixdown_preserves_plus_twelve_db_track_gain(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    package_root = media_assets.project_video_localization_dir(project_id)
    background = package_root / "stems" / "background.wav"
    audio_tools.write_audio(
        background,
        np.full(24_000, 0.1, dtype=np.float32),
        24_000,
    )
    current = draft_store.get(project_id)
    assert current is not None
    next_draft = current.model_copy(
        update={
            "stems": current.stems.model_copy(
                update={"background_path": str(background)}
            ),
            "ui_state": {
                **current.ui_state,
                "track_states": {
                    "background": {
                        "muted": False,
                        "solo": True,
                        "volume": 4.0,
                    }
                },
            },
        }
    )
    draft_store.save(project_id, next_draft, intent="runtime")
    destination = tmp_path / "plus-twelve-db.wav"

    mixed_tracks = exporting._write_localized_mixdown(
        destination,
        next_draft,
        None,
        1000,
        selected_tracks={"background"},
        selected_dub_lanes=set(),
    )

    assert mixed_tracks[0]["volume"] == 4.0
    audio, _ = audio_tools.read_audio(destination)
    assert float(np.mean(audio)) == pytest.approx(0.4, abs=0.02)


def test_explicit_dub_mixdown_resolves_split_clip_media_source(
    tmp_path: Path,
):
    source = tmp_path / "dub.wav"
    audio_tools.write_audio(
        source,
        np.full(48_000, 0.25, dtype=np.float32),
        24_000,
    )
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 2_000},
        timeline_clips=[
            {
                "clip_id": "clip_master",
                "media_source_clip_id": "clip_master",
                "track_id": "dub",
                "dub_lane": 0,
                "start_ms": 0,
                "end_ms": 1_000,
                "source_start_ms": 0,
                "source_end_ms": 1_000,
                "audio_path": str(source),
                "status": "ready",
            },
            {
                "clip_id": "clip_master_part_2",
                "media_source_clip_id": "clip_master",
                "track_id": "dub",
                "dub_lane": 0,
                "start_ms": 1_000,
                "end_ms": 2_000,
                "source_start_ms": 1_000,
                "source_end_ms": 2_000,
                "audio_path": None,
                "status": "ready",
            },
        ],
    )
    destination = tmp_path / "split-mix.wav"

    mixed_tracks = exporting._write_localized_mixdown(
        destination,
        draft,
        None,
        2_000,
        selected_tracks={"dub"},
        selected_dub_lanes={0},
    )

    mixed, sample_rate = audio_tools.read_audio(destination)
    assert [item["clip_id"] for item in mixed_tracks] == [
        "clip_master",
        "clip_master_part_2",
    ]
    assert float(np.mean(mixed[:sample_rate])) == pytest.approx(
        0.25,
        abs=0.02,
    )
    assert float(np.mean(mixed[sample_rate:])) == pytest.approx(
        0.25,
        abs=0.02,
    )


def test_explicit_dub_mixdown_rejects_unresolvable_selected_clip(
    tmp_path: Path,
):
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 1_000},
        timeline_clips=[
            {
                "clip_id": "clip_missing",
                "track_id": "dub",
                "dub_lane": 0,
                "start_ms": 0,
                "end_ms": 1_000,
                "source_start_ms": 0,
                "source_end_ms": 1_000,
                "audio_path": None,
                "status": "ready",
            }
        ],
    )

    with pytest.raises(AppException) as raised:
        exporting._write_localized_mixdown(
            tmp_path / "missing.wav",
            draft,
            None,
            1_000,
            selected_tracks={"dub"},
            selected_dub_lanes={0},
        )

    assert raised.value.code == (
        "VIDEO_LOCALIZATION_EXPORT_DUB_AUDIO_INCOMPLETE"
    )
    assert raised.value.detail_dict["clip_ids"] == ["clip_missing"]


def test_explicit_dub_mixdown_rejects_clip_beyond_video_instead_of_truncating(
    tmp_path: Path,
):
    source = tmp_path / "dub.wav"
    audio_tools.write_audio(
        source,
        np.full(48_000, 0.25, dtype=np.float32),
        48_000,
    )
    draft = VideoLocalizationDraft(
        source_media={"duration_ms": 1_000},
        timeline_clips=[
            {
                "clip_id": "clip_outside",
                "track_id": "dub",
                "dub_lane": 0,
                "start_ms": 500,
                "end_ms": 1_500,
                "source_start_ms": 0,
                "source_end_ms": 1_000,
                "audio_path": str(source),
                "status": "ready",
            }
        ],
    )

    with pytest.raises(AppException) as raised:
        exporting._write_localized_mixdown(
            tmp_path / "outside.wav",
            draft,
            None,
            1_000,
            selected_tracks={"dub"},
            selected_dub_lanes={0},
        )

    assert raised.value.code == (
        "VIDEO_LOCALIZATION_TIMELINE_AUDIO_RANGE_INVALID"
    )
    assert raised.value.detail_dict == {
        "item_ids": ["clip_outside"],
        "timeline_duration_ms": 1_000,
        "start_ms": 500,
        "end_ms": 1_500,
    }


def test_explicit_mixdown_preserves_stereo_background_channels(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    package_root = media_assets.project_video_localization_dir(project_id)
    background = package_root / "stems" / "background.wav"
    stereo_background = np.stack(
        [
            np.full(24_000, 0.1, dtype=np.float32),
            np.full(24_000, 0.4, dtype=np.float32),
        ],
        axis=1,
    )
    audio_tools.write_audio(background, stereo_background, 24_000)
    current = draft_store.get(project_id)
    assert current is not None
    next_draft = current.model_copy(
        update={
            "stems": current.stems.model_copy(
                update={"background_path": str(background)}
            ),
        }
    )
    draft_store.save(project_id, next_draft, intent="runtime")
    destination = tmp_path / "stereo-selected.wav"

    exporting._write_localized_mixdown(
        destination,
        next_draft,
        None,
        1000,
        selected_tracks={"background"},
        selected_dub_lanes=set(),
    )

    exported, sample_rate = sf.read(
        str(destination),
        always_2d=True,
        dtype="float32",
    )
    assert sample_rate == 48_000
    assert exported.shape[1] == 2
    assert float(np.mean(exported[:, 0])) == pytest.approx(0.1, abs=0.02)
    assert float(np.mean(exported[:, 1])) == pytest.approx(0.4, abs=0.02)


def test_subtitle_overlay_keeps_selected_tracks_and_timing(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    current = draft_store.get(project_id)
    assert current is not None
    next_draft = current.model_copy(
        update={
            "cues": [
                current.cues[0].model_copy(
                    update={"en_subtitle_text": "Original line"}
                )
            ],
            "localized_subtitles": [
                VideoLocalizationSubtitleCue(
                    subtitle_id="localized_1",
                    start_ms=0,
                    end_ms=1000,
                    text="本土化字幕",
                    tts_text="本土化字幕",
                )
            ],
        }
    )
    destination = tmp_path / "subtitle-overlay"

    timeline = exporting._write_subtitle_overlay_timeline(
        destination,
        next_draft,
        selected_tracks={"asr", "localized"},
        duration_ms=1000,
        width=640,
        height=360,
    )

    assert timeline.is_file()
    assert list(destination.glob("*.png"))
    assert exporting._active_subtitle_text(
        [(0, 1000, "Original line")],
        500,
    ) == "Original line"
    assert exporting._active_subtitle_text(
        [(0, 1000, "本土化字幕")],
        500,
    ) == "本土化字幕"


def test_subtitle_overlay_uses_requested_dub_subtitle_variant(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    current = draft_store.get(project_id)
    assert current is not None
    next_draft = current.model_copy(
        update={
            "localized_subtitles": [
                VideoLocalizationSubtitleCue(
                    subtitle_id="localized_1",
                    start_ms=0,
                    end_ms=1000,
                    text="本土化字幕",
                )
            ],
            "dub_subtitles": [
                VideoLocalizationDubSubtitleCue(
                    subtitle_id="dub_1",
                    start_ms=0,
                    end_ms=1000,
                    text="当前显示的配音字幕",
                    source_audio_sha256="a" * 64,
                )
            ],
        }
    )

    assert exporting._selected_localized_subtitle_cues(
        next_draft,
        "dub",
    ) == [(0, 1000, "当前显示的配音字幕")]


def test_subtitle_wrapping_never_drops_text():
    image = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text = "这是一条需要换成三行以上但绝对不能在导出时被截断的字幕内容"

    wrapped = exporting._wrap_subtitle_text(
        draw,
        text,
        ImageFont.load_default(),
        max_width=50,
    )

    assert wrapped.replace("\n", "") == text
    assert wrapped.count("\n") >= 2


def test_standalone_subtitle_export_can_select_dub_variant(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project_id, _ = _localized_video_project(client, tmp_path)
    current = draft_store.get(project_id)
    assert current is not None
    draft_store.save(
        project_id,
        current.model_copy(
            update={
                "dub_subtitles": [
                    VideoLocalizationDubSubtitleCue(
                        subtitle_id="dub_1",
                        start_ms=0,
                        end_ms=1000,
                        text="单独导出的配音字幕",
                        source_audio_sha256="b" * 64,
                    )
                ]
            }
        ),
        intent="content",
    )

    response = client.get(
        f"/api/projects/{project_id}/video-localization/subtitles/zh",
        params={"localized_variant": "dub"},
    )

    assert response.status_code == 200, response.text
    assert "单独导出的配音字幕" in response.text
    assert "本土化" not in response.text
    filename = unquote(
        response.headers["content-disposition"].split(
            "filename*=UTF-8''",
            1,
        )[1]
    )
    assert filename.startswith("显式导出命令__字幕__配音__")
    assert "__版本-" in filename
    assert filename.endswith(".srt")


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is required for the media export smoke test",
)
def test_media_export_smoke_renders_one_video_and_one_audio_file(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "短素材导出测试", "description": ""},
    ).json()
    project_id = project["project_id"]
    package_root = media_assets.ensure_project_video_localization_dir(
        project_id
    )
    source_video = package_root / "source" / "short.mp4"
    source_video.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(shutil.which("ffmpeg")),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source_video),
        ],
        check=True,
        capture_output=True,
    )
    background = package_root / "stems" / "background.wav"
    audio_tools.write_audio(
        background,
        np.sin(
            np.linspace(0, 2 * np.pi * 440, 24_000, endpoint=False)
        ).astype(np.float32)
        * 0.08,
        24_000,
    )
    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "short.mp4",
                "duration_ms": 1000,
                "video_path": str(source_video),
            },
            "stems": {"background_path": str(background)},
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_1",
                    "start_ms": 0,
                    "end_ms": 950,
                    "text": "这是导出测试",
                    "tts_text": "这是导出测试",
                }
            ],
        },
    )
    assert response.status_code == 200

    destination = tmp_path / "exports"
    destination.mkdir(exist_ok=True)
    video_result = video_localization_exports.create_media_export_at(
        project_id,
        VideoLocalizationMediaExportRequest(
            kind="video",
            audio_tracks=["background"],
            subtitle_tracks=["localized"],
            video_size="720p",
        ),
        destination,
        "smoke-video.mp4",
    )
    audio_result = video_localization_exports.create_media_export_at(
        project_id,
        VideoLocalizationMediaExportRequest(
            kind="audio",
            audio_tracks=["background"],
            audio_format="mp3",
        ),
        destination,
        "smoke-audio.mp3",
    )

    assert video_result is not None
    rendered_video = destination / video_result["filename"]
    assert rendered_video.stat().st_size > 1_000
    rendered_frame = tmp_path / "downloaded-frame.png"
    subprocess.run(
        [
            str(shutil.which("ffmpeg")),
            "-y",
            "-ss",
            "0.5",
            "-i",
            str(rendered_video),
            "-frames:v",
            "1",
            str(rendered_frame),
        ],
        check=True,
        capture_output=True,
    )
    frame = np.asarray(Image.open(rendered_frame).convert("RGB"))
    assert int(frame.max()) > 100
    assert audio_result is not None
    rendered_audio = destination / audio_result["filename"]
    assert rendered_audio.suffix == ".mp3"
    assert rendered_audio.stat().st_size > 1_000


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is required for the media export duration test",
)
def test_video_export_uses_actual_source_video_duration(
    tmp_path: Path,
):
    client = _client(tmp_path)
    project = client.post(
        "/api/projects",
        json={"name": "源视频时长完整性", "description": ""},
    ).json()
    project_id = project["project_id"]
    package_root = media_assets.ensure_project_video_localization_dir(
        project_id
    )
    source_video = package_root / "source" / "source.mp4"
    source_video.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(shutil.which("ffmpeg")),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source_video),
        ],
        check=True,
        capture_output=True,
    )
    response = client.put(
        f"/api/projects/{project_id}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "source_media": {
                "filename": "source.mp4",
                "duration_ms": 500,
                "video_path": str(source_video),
            },
        },
    )
    assert response.status_code == 200

    destination = tmp_path / "exports"
    destination.mkdir(exist_ok=True)
    video_result = video_localization_exports.create_media_export_at(
        project_id,
        VideoLocalizationMediaExportRequest(
            kind="video",
            audio_tracks=[],
            subtitle_tracks=[],
        ),
        destination,
        "actual-duration.mp4",
    )

    assert video_result is not None
    rendered = destination / video_result["filename"]
    assert media_assets.probe_video(rendered)["duration_ms"] == pytest.approx(
        1_000,
        abs=60,
    )


@pytest.mark.parametrize("track_id", ["dub", "original", "vocals", "background"])
@pytest.mark.parametrize("timeline_span,source_span", [(1041, 1000), (1000, 1005)])
def test_export_editorial_clip_uses_playable_intersection_without_changing_edit(
    tmp_path, track_id, timeline_span, source_span,
):
    """Editorial padding/crops must render like playback, without retiming clips."""
    from copy import deepcopy

    source = tmp_path / "editorial.wav"
    audio_tools.write_audio(source, np.full(96_000, 0.25, dtype=np.float32), 48_000)
    source_media = {"duration_ms": 3000, "frame_rate": 24, "audio_path": str(source)}
    draft = VideoLocalizationDraft(
        source_media=source_media,
        stems={"vocals_clean_path": str(source), "background_path": str(source)},
        timeline_clips=[{
            "clip_id": "edited", "track_id": track_id, "dub_lane": 0,
            "start_ms": 250, "end_ms": 250 + timeline_span,
            "source_start_ms": 200, "source_end_ms": 200 + source_span,
            "audio_path": str(source), "status": "ready",
            "timeline_timing_version": "editorial-v1",
        }],
    )
    before = deepcopy(draft.model_dump())
    destination = tmp_path / "mixed.wav"
    exporting._write_localized_mixdown(
        destination, draft, None, 3000,
        selected_tracks={track_id}, selected_dub_lanes={0},
    )
    mixed, rate = audio_tools.read_audio(destination)
    assert rate == 48_000 and len(mixed) == 144_000
    assert np.max(np.abs(mixed[:12000])) < .001
    assert np.mean(mixed[12000:60000]) == pytest.approx(.25, abs=.001)
    assert np.max(np.abs(mixed[60000:])) < .001
    assert draft.model_dump() == before
