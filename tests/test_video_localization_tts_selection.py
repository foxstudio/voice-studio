from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import draft_store, media_assets, tts_orchestration  # noqa: E402
from app.domains.video_localization import service as video_localization_service  # noqa: E402
from app.main import app  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    GenerateRequest,
    LicenseStatus,
    VoiceAsset,
    VoiceFile,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationDraft,
    VideoLocalizationReferenceClip,
)
from app.services import audio_tools, database, settings_store, voice_store  # noqa: E402


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


def _project_with_independent_ranges(tmp_path: Path, monkeypatch) -> tuple[TestClient, str, Path]:
    client = _client(tmp_path)
    project = client.post("/api/projects", json={"name": "双轨独立选择", "description": ""}).json()
    vocals_path = (
        media_assets.project_video_localization_dir(project["project_id"])
        / "stems"
        / "vocals.wav"
    )
    reference_path = tmp_path / "reference.wav"
    audio_tools.write_audio(vocals_path, np.full(15_000, 0.1, dtype=np.float32), 1_000)
    audio_tools.write_audio(reference_path, np.full(3_000, 0.2, dtype=np.float32), 1_000)
    client.put(
        f"/api/projects/{project['project_id']}/video-localization",
        json={
            "project_type": "video_localization",
            "schema_version": "v1",
            "stems": {"vocals_clean_path": str(vocals_path)},
            "transcription": {
                "revision_id": "revision-explicit",
                "source_audio_sha256": "sha-explicit",
                "words": [
                    {
                        "word_id": "word-1",
                        "segment_id": "seg-1",
                        "text": "Seedance",
                        "start_ms": 1_000,
                        "end_ms": 2_000,
                    },
                    {
                        "word_id": "word-2",
                        "segment_id": "seg-1",
                        "text": "workflow",
                        "start_ms": 2_000,
                        "end_ms": 3_900,
                    },
                ],
            },
            "cues": [
                {
                    "cue_id": "cue_0001",
                    "speaker_id": "speaker_01",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "en_subtitle_text": "Seedance",
                    "source_word_ids": ["word-1"],
                },
                {
                    "cue_id": "cue_0002",
                    "speaker_id": "speaker_01",
                    "start_ms": 2_000,
                    "end_ms": 4_000,
                    "en_subtitle_text": "workflow",
                    "source_word_ids": ["word-2"],
                },
                {
                    "cue_id": "cue_0003",
                    "speaker_id": "speaker_02",
                    "start_ms": 4_000,
                    "end_ms": 5_000,
                    "en_subtitle_text": "other",
                },
            ],
            "localized_subtitles": [
                {
                    "subtitle_id": "localized_0001",
                    "start_ms": 10_000,
                    "end_ms": 11_000,
                    "text": "目标字幕一",
                    "tts_text": "目标台词一",
                    "source_cue_ids": ["cue_0003"],
                },
                {
                    "subtitle_id": "localized_0002",
                    "start_ms": 11_000,
                    "end_ms": 12_000,
                    "text": "目标字幕二",
                    "tts_text": "目标台词二",
                    "source_cue_ids": ["cue_0003"],
                },
                {
                    "subtitle_id": "localized_0003",
                    "start_ms": 13_000,
                    "end_ms": 14_000,
                    "text": "目标字幕三",
                    "tts_text": "目标台词三",
                    "source_cue_ids": ["cue_0003"],
                },
                {
                    "subtitle_id": "localized_0004",
                    "start_ms": 15_000,
                    "end_ms": 16_000,
                    "text": "目标字幕四",
                    "tts_text": "目标台词四",
                    "source_cue_ids": ["cue_0003"],
                },
            ],
        },
    )
    managed = type("ManagedAudio", (), {"file_id": "source-file", "path": str(vocals_path), "duration_ms": 15_000})()
    clip_voice = type("ClipVoice", (), {"duration_ms": 3_000})()
    monkeypatch.setattr(tts_orchestration.voice_store, "ensure_managed_audio_file", lambda *args, **kwargs: managed)
    monkeypatch.setattr(
        tts_orchestration.voice_store,
        "create_audio_clip",
        lambda *args, **kwargs: {"path": str(reference_path), "voice_file": clip_voice},
    )
    return client, project["project_id"], vocals_path


def _create_dubbing_plan(client: TestClient, project_id: str) -> dict:
    snapshot = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/snapshot"
    ).json()
    response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json={
            "schema_version": "dubbing-generation-plan-input-v1",
            "source_revision": snapshot["source_revision"],
            "semantic_units": [
                {
                    **unit,
                    "speech_policy": "translate",
                    "scene_id": "scene-1",
                }
                for unit in snapshot["semantic_units"]
            ],
            "boundaries": snapshot["boundaries"],
            "policy": {
                "preferred_group_units": 1,
                "hard_max_group_units": 2,
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_local_voice(tmp_path: Path, *, voice_id: str = "local-male") -> tuple[VoiceAsset, Path]:
    voice_path = tmp_path / f"{voice_id}.wav"
    audio_tools.write_audio(
        voice_path,
        np.full(4_000, 0.15, dtype=np.float32),
        1_000,
    )
    metadata = audio_tools.probe_audio(voice_path)
    voice_file = VoiceFile(
        file_id=f"{voice_id}-file",
        original_name=voice_path.name,
        path=str(voice_path),
        mime_type="audio/wav",
        duration_ms=metadata["duration_ms"],
        sample_rate=metadata["sample_rate"],
        size_bytes=metadata["size_bytes"],
    )
    database.upsert("voice_files", voice_file.file_id, voice_file.model_dump(mode="json"))
    voice = voice_store.save_voice(
        VoiceAsset(
            voice_id=voice_id,
            name="本地普通男声",
            tags=["男声", "普通对白"],
            reference_text="这是准确的本地音色参考台词。",
            reference_audio_ids=[voice_file.file_id],
            license_status=LicenseStatus.authorized,
            quality_status="verified",
        )
    )
    return voice, voice_path


def test_parameter_pack_separates_target_placement_from_explicit_source_reference(tmp_path: Path, monkeypatch):
    client, project_id, vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json={
            "target_subtitle_ids": ["localized_0001", "localized_0002"],
            "source_cue_ids": ["cue_0001", "cue_0002"],
        },
    )

    assert response.status_code == 200
    pack = response.json()
    assert pack["version"] == "video-localization-tts-parameter-pack-v2"
    assert pack["target"]["subtitle_ids"] == ["localized_0001", "localized_0002"]
    assert pack["target"]["text"] == "目标台词一\n目标台词二"
    assert (pack["target"]["start_ms"], pack["target"]["end_ms"]) == (10_000, 12_000)
    assert pack["source"]["cue_ids"] == ["cue_0001", "cue_0002"]
    assert pack["source"]["word_ids"] == ["word-1", "word-2"]
    assert (pack["source"]["start_ms"], pack["source"]["end_ms"]) == (1_000, 4_000)
    assert pack["source"]["speaker_id"] == "speaker_01"
    assert pack["source"]["transcription_revision_id"] == "revision-explicit"
    assert pack["source"]["source_audio_sha256"] == "sha-explicit"
    assert pack["source"]["ref_text"] == "Seedance workflow"
    request = pack["request"]
    assert request["text"] == "目标台词一\n目标台词二"
    assert request["video_localization_target_subtitle_ids"] == ["localized_0001", "localized_0002"]
    assert request["video_localization_source_cue_ids"] == ["cue_0001", "cue_0002"]
    assert (request["video_localization_start_ms"], request["video_localization_end_ms"]) == (10_000, 12_000)
    assert request["custom_reference_source_audio_path"] == str(vocals_path)
    assert (request["custom_reference_trim_start_ms"], request["custom_reference_trim_end_ms"]) == (1_000, 4_000)
    assert request["ref_text"] == "Seedance workflow"


def test_parameter_pack_treats_unlabelled_cues_as_the_only_known_neighbouring_speaker(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)
    draft = client.get(f"/api/projects/{project_id}/video-localization").json()
    draft["cues"][0]["speaker_id"] = None
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    )
    assert saved.status_code == 200, saved.text

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json={
            "target_subtitle_ids": ["localized_0001", "localized_0002"],
            "source_cue_ids": ["cue_0001", "cue_0002"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["source"]["speaker_id"] == "speaker_01"


def test_handoff_reserve_persists_without_waiting_for_reference_audio(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)

    def unexpected_materialization(*_args, **_kwargs):
        pytest.fail("reservation must not materialize reference audio")

    monkeypatch.setattr(
        tts_orchestration.voice_store,
        "create_audio_clip",
        unexpected_materialization,
    )
    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-reserve/localized_0001",
        json={
            "submission_id": "abc123def456",
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001", "cue_0002"],
        },
    )

    assert response.status_code == 200, response.text
    task = response.json()
    assert task["workflow_id"] == "abc123def456"
    assert task["status"] == "prepared"
    generation_stage = next(stage for stage in task["stages"] if stage["kind"] == "generation")
    assert generation_stage["parameters"]["initializing"] is True
    persisted = client.get(
        f"/api/projects/{project_id}/video-localization/tts/tasks/abc123def456"
    )
    assert persisted.status_code == 200


@pytest.mark.parametrize("source_ids, expected_range, expected_text", [
    (["cue_0001"], (1_000, 2_000), "Seedance"),
    (["cue_0001", "cue_0002"], (1_000, 4_000), "Seedance workflow"),
])
def test_handoff_keeps_selected_asr_sampling_despite_planned_reference(
    tmp_path, monkeypatch, source_ids, expected_range, expected_text,
):
    from types import SimpleNamespace
    client, project_id, _ = _project_with_independent_ranges(tmp_path, monkeypatch)
    monkeypatch.setattr(video_localization_service, "_planned_dubbing_group_for_target",
        lambda *_a, **_k: SimpleNamespace(
            source_reference_start_ms=1_000, source_reference_end_ms=6_310,
            speaker_id="speaker_01", target_start_ms=10_000, target_end_ms=11_000,
            spoken_text="目标台词一", group_id="group-test"))
    def crop(_file_id, start_ms, end_ms, **_kwargs):
        audio_tools.write_audio(tmp_path / "reference.wav",
            np.full(end_ms - start_ms, 0.2, dtype=np.float32), 1_000)
        return {"path": str(tmp_path / "reference.wav"),
                "voice_file": SimpleNamespace(duration_ms=end_ms - start_ms)}
    monkeypatch.setattr(tts_orchestration.voice_store, "create_audio_clip", crop)
    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001",
        json={"target_subtitle_ids": ["localized_0001"], "source_cue_ids": source_ids})
    assert response.status_code == 200, response.text
    request = response.json()
    assert (request["custom_reference_trim_start_ms"], request["custom_reference_trim_end_ms"]) == expected_range
    assert request["ref_text"] == expected_text
    assert request["video_localization_start_ms"] == 10_000
    assert request["video_localization_end_ms"] == 11_000


def test_short_group_reuses_only_plausible_longer_same_speaker_reference():
    draft = VideoLocalizationDraft(
        reference_clips=[
            VideoLocalizationReferenceClip(
                reference_clip_id="mostly-silence",
                speaker_id="speaker_01",
                start_ms=1_000,
                end_ms=7_000,
                duration_ms=6_000,
                source_stem="vocals_clean",
                asr_text="here",
            ),
            VideoLocalizationReferenceClip(
                reference_clip_id="usable",
                speaker_id="speaker_01",
                start_ms=10_000,
                end_ms=14_000,
                duration_ms=4_000,
                source_stem="vocals_clean",
                asr_text="this is a clean longer reference line",
            ),
            VideoLocalizationReferenceClip(
                reference_clip_id="wrong-speaker",
                speaker_id="speaker_02",
                start_ms=20_000,
                end_ms=24_500,
                duration_ms=4_500,
                source_stem="vocals_clean",
                asr_text="another perfectly valid reference sentence",
            ),
        ]
    )

    assert tts_orchestration.best_same_speaker_reference_range(
        draft,
        speaker_id="speaker_01",
        current_start_ms=30_000,
        current_end_ms=31_000,
    ) == (10_000, 14_000)


def test_short_group_keeps_current_reference_when_no_safe_longer_prompt_exists():
    draft = VideoLocalizationDraft(
        reference_clips=[
            VideoLocalizationReferenceClip(
                reference_clip_id="too-sparse",
                speaker_id="speaker_01",
                start_ms=1_000,
                end_ms=6_000,
                duration_ms=5_000,
                source_stem="vocals_clean",
                asr_text="hello",
            )
        ]
    )

    assert tts_orchestration.best_same_speaker_reference_range(
        draft,
        speaker_id="speaker_01",
        current_start_ms=30_000,
        current_end_ms=31_000,
    ) == (30_000, 31_000)


def test_short_group_prefers_nearby_plausible_reference_over_distant_duration_match():
    draft = VideoLocalizationDraft(
        reference_clips=[
            VideoLocalizationReferenceClip(
                reference_clip_id="distant-ideal-duration",
                speaker_id="speaker_01",
                start_ms=900_000,
                end_ms=904_500,
                duration_ms=4_500,
                source_stem="vocals_clean",
                cleanliness="needs_review",
                asr_text="this distant sentence has an ideal prompt duration",
            ),
            VideoLocalizationReferenceClip(
                reference_clip_id="nearby-valid",
                speaker_id="speaker_01",
                start_ms=33_000,
                end_ms=36_200,
                duration_ms=3_200,
                source_stem="vocals_clean",
                cleanliness="needs_review",
                asr_text="this nearby sentence is also clear enough",
            ),
        ]
    )

    assert tts_orchestration.best_same_speaker_reference_range(
        draft,
        speaker_id="speaker_01",
        current_start_ms=30_000,
        current_end_ms=31_000,
    ) == (33_000, 36_200)


def test_selection_handoff_uses_project_target_language_instead_of_global_default(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)
    _create_dubbing_plan(client, project_id)
    settings_store.update(settings_store.get().model_copy(update={"default_language": "en"}))
    selection = {
        "target_subtitle_ids": ["localized_0001"],
        "source_cue_ids": ["cue_0001"],
    }

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json=selection,
    )

    assert response.status_code == 200
    assert response.json()["request"]["language"] == "zh"

    preview = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001",
        json=selection,
    )
    assert preview.status_code == 200
    finalized = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(preview.json()).model_copy(update={"language": "en"})
    )
    assert finalized.language == "zh"


def test_handoff_requires_one_explicit_localized_subtitle_selection(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)

    missing_body = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001"
    )
    empty_selection = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001",
        json={"target_subtitle_ids": [], "source_cue_ids": ["cue_0001"]},
    )

    assert missing_body.status_code == 400
    assert empty_selection.status_code == 400


def test_planned_handoff_can_use_validated_local_voice_without_changing_group_scope(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(
        tmp_path,
        monkeypatch,
    )
    plan = _create_dubbing_plan(client, project_id)
    voice, voice_path = _create_local_voice(tmp_path)
    group = plan["groups"][0]

    preview = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/{group['subtitle_ids'][0]}",
        json={
            "target_subtitle_ids": group["subtitle_ids"],
            "source_cue_ids": ["cue_0001"],
            "voice_library_voice_id": voice.voice_id,
        },
    )

    assert preview.status_code == 200, preview.text
    request = GenerateRequest.model_validate(preview.json())
    assert request.voice_id == voice.voice_id
    assert request.voice_source.value == "voice_library"
    assert request.reference_audio_path == str(voice_path)
    assert request.custom_reference_source_audio_path == str(voice_path)
    assert request.custom_reference_trim_start_ms == 0
    assert request.custom_reference_trim_end_ms == 4_000
    assert request.ref_text == voice.reference_text
    assert request.video_localization_target_subtitle_ids == group["subtitle_ids"]
    assert request.video_localization_source_cue_ids == ["cue_0001"]
    assert request.video_localization_dubbing_group_id == group["group_id"]

    finalized = video_localization_service.finalize_single_tts_submission(request)
    assert finalized.voice_id == voice.voice_id
    assert finalized.voice_source.value == "voice_library"
    assert finalized.reference_audio_path == str(voice_path)
    assert finalized.video_localization_target_subtitle_ids == group["subtitle_ids"]
    assert finalized.video_localization_dubbing_group_id is None


def test_handoff_rejects_unknown_local_voice(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(
        tmp_path,
        monkeypatch,
    )

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001",
        json={
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001"],
            "voice_library_voice_id": "missing-local-voice",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_TTS_VOICE_NOT_FOUND"


def test_manual_exact_selection_does_not_require_a_production_plan(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(
        tmp_path,
        monkeypatch,
    )
    preview = client.post(
        (
            f"/api/projects/{project_id}/video-localization/tts/"
            "handoff-preview/localized_0001"
        ),
        json={
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001"],
        },
    )
    assert preview.status_code == 200

    finalized = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(preview.json())
    )

    assert finalized.video_localization_target_subtitle_ids == [
        "localized_0001"
    ]
    assert finalized.video_localization_dubbing_plan_revision is None
    assert finalized.video_localization_dubbing_group_id is None


def test_second_active_submission_for_same_segment_is_rejected_before_generation(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(
        tmp_path,
        monkeypatch,
    )
    selection = {
        "target_subtitle_ids": ["localized_0001"],
        "source_cue_ids": ["cue_0001"],
    }
    first_preview = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001",
        json=selection,
    )
    second_preview = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001",
        json=selection,
    )
    first = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(first_preview.json())
    )

    with pytest.raises(AppException) as busy_error:
        video_localization_service.finalize_single_tts_submission(
            GenerateRequest.model_validate(second_preview.json())
        )

    assert busy_error.value.code == "VIDEO_LOCALIZATION_TTS_SEGMENT_BUSY"
    assert busy_error.value.detail_dict["workflow_id"] == first.video_localization_workflow_id


def test_stale_prepared_submission_does_not_lock_segment_forever(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(
        tmp_path,
        monkeypatch,
    )
    selection = {
        "target_subtitle_ids": ["localized_0001"],
        "source_cue_ids": ["cue_0001"],
    }
    first_preview = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001",
        json=selection,
    )
    second_preview = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001",
        json=selection,
    )
    first = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(first_preview.json())
    )
    draft = draft_store.get(project_id)
    assert draft is not None
    stale_tasks = [
        task.model_copy(update={"updated_at": "2000-01-01T00:00:00"})
        if task.workflow_id == first.video_localization_workflow_id
        else task
        for task in draft.tts_tasks
    ]
    assert draft_store.save(
        project_id,
        draft.model_copy(update={"tts_tasks": stale_tasks}),
        intent="runtime",
    ) is not None

    second = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(second_preview.json())
    )

    assert second.video_localization_workflow_id
    assert second.video_localization_workflow_id != first.video_localization_workflow_id


def test_planned_handoff_freezes_plan_and_group_lineage_at_submission(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(
        tmp_path,
        monkeypatch,
    )
    draft = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    draft["localized_subtitles"][0]["spoken_segment_id"] = (
        "spoken_segment_0001"
    )
    draft["localized_spoken_segments"] = [
        {
            "segment_id": "spoken_segment_0001",
            "paragraph_id": "paragraph_0001",
            "text": "计划 2.0 台词，输出 4K。",
            "start_ms": 10_000,
            "end_ms": 11_000,
            "source_cue_ids": ["cue_0003"],
            "source_word_ids": ["word-1"],
        }
    ]
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    )
    assert saved.status_code == 200, saved.text
    snapshot = client.get(
        f"/api/projects/{project_id}/video-localization/dubbing/snapshot"
    ).json()
    plan_response = client.post(
        f"/api/projects/{project_id}/video-localization/dubbing/plan",
        json={
            "schema_version": "dubbing-generation-plan-input-v1",
            "source_revision": snapshot["source_revision"],
            "semantic_units": [
                {
                    **unit,
                    "speech_policy": "translate",
                    "scene_id": "scene-1",
                }
                for unit in snapshot["semantic_units"]
            ],
            "boundaries": snapshot["boundaries"],
            "policy": {
                "preferred_group_units": 1,
                "hard_max_group_units": 2,
            },
        },
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    expected_group = next(
        group
        for group in plan["groups"]
        if "localized_0001" in group["subtitle_ids"]
    )
    source_cue_ids = list(
        dict.fromkeys(
            cue_id
            for unit in plan["semantic_units"]
            if unit["unit_id"] in expected_group["unit_ids"]
            for cue_id in unit["source_cue_ids"]
        )
    )
    requested_reference_ranges: list[tuple[int, int]] = []

    def create_reference_clip(_file_id, start_ms, end_ms, **_kwargs):
        requested_reference_ranges.append((start_ms, end_ms))
        clip_voice = type(
            "ClipVoice",
            (),
            {"duration_ms": end_ms - start_ms},
        )()
        return {
            "path": str(tmp_path / "reference.wav"),
            "voice_file": clip_voice,
        }

    monkeypatch.setattr(
        tts_orchestration.voice_store,
        "create_audio_clip",
        create_reference_clip,
    )
    preview = client.post(
        (
            f"/api/projects/{project_id}/video-localization/tts/"
            "handoff-preview/localized_0001"
        ),
        json={
            "target_subtitle_ids": expected_group["subtitle_ids"],
            "source_cue_ids": source_cue_ids,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["text"] == expected_group["spoken_text"]
    assert preview.json()["text"] == "计划 2.0 台词，输出 4K。"
    assert preview.json()["video_localization_start_ms"] == expected_group[
        "target_start_ms"
    ]
    assert preview.json()["video_localization_end_ms"] == expected_group[
        "target_end_ms"
    ]
    assert preview.json()["video_localization_dubbing_plan_revision"] == plan[
        "plan_revision"
    ]
    assert preview.json()["video_localization_dubbing_group_id"] == expected_group[
        "group_id"
    ]
    assert requested_reference_ranges[-1] == (
        expected_group["source_reference_start_ms"],
        expected_group["source_reference_end_ms"],
    )

    finalized = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(preview.json())
    )

    # A manual generation freezes this exact subtitle/source selection but is
    # not owned by the mutable all-remaining production plan.  Neighbouring
    # submissions may rebuild that plan while this audio waits in the queue.
    assert finalized.video_localization_dubbing_plan_revision is None
    assert finalized.video_localization_dubbing_group_id is None
    assert finalized.text == "计划 2.0 台词，输出 4K。"

def test_manual_selection_inside_a_plan_group_stays_exact(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(
        tmp_path,
        monkeypatch,
    )
    draft = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    for subtitle in draft["localized_subtitles"][:2]:
        subtitle["spoken_segment_id"] = "spoken_segment_0001"
    draft["localized_spoken_segments"] = [
        {
            "segment_id": "spoken_segment_0001",
            "paragraph_id": "paragraph_0001",
            "text": "合并后的完整配音台词。",
            "start_ms": 10_000,
            "end_ms": 12_000,
            "source_cue_ids": ["cue_0003"],
            "source_word_ids": ["word-1", "word-2"],
        }
    ]
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    )
    assert saved.status_code == 200, saved.text
    plan = _create_dubbing_plan(client, project_id)
    expected_group = next(
        group
        for group in plan["groups"]
        if "localized_0001" in group["subtitle_ids"]
    )
    assert expected_group["subtitle_ids"] == [
        "localized_0001",
        "localized_0002",
    ]

    preview = client.post(
        (
            f"/api/projects/{project_id}/video-localization/tts/"
            "handoff-preview/localized_0001"
        ),
        json={
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0003"],
        },
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()["video_localization_target_subtitle_ids"] == [
        "localized_0001",
    ]
    assert preview.json()["text"] == "目标台词一"
    assert preview.json()["video_localization_start_ms"] == 10_000
    assert preview.json()["video_localization_end_ms"] == 11_000
    assert preview.json()["video_localization_dubbing_plan_revision"] is None
    assert preview.json()["video_localization_dubbing_group_id"] is None
    finalized = video_localization_service.finalize_single_tts_submission(
        GenerateRequest.model_validate(preview.json())
    )
    assert finalized.text == "目标台词一"
    assert finalized.video_localization_target_subtitle_ids == [
        "localized_0001"
    ]
    assert finalized.video_localization_dubbing_group_id is None


def test_manual_exact_result_is_placed_without_plan_group_lineage(
    tmp_path: Path,
    monkeypatch,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(
        tmp_path,
        monkeypatch,
    )
    draft = client.get(
        f"/api/projects/{project_id}/video-localization"
    ).json()
    for subtitle in draft["localized_subtitles"][:2]:
        subtitle["spoken_segment_id"] = "spoken_segment_0001"
    draft["localized_spoken_segments"] = [
        {
            "segment_id": "spoken_segment_0001",
            "paragraph_id": "paragraph_0001",
            "text": "目标台词一目标台词二",
            "start_ms": 10_000,
            "end_ms": 12_000,
            "source_cue_ids": ["cue_0001", "cue_0002"],
            "source_word_ids": ["word-1", "word-2"],
        }
    ]
    saved = client.put(
        f"/api/projects/{project_id}/video-localization",
        json=draft,
    )
    assert saved.status_code == 200, saved.text
    _create_dubbing_plan(client, project_id)
    output_path = tmp_path / "manual-exact.wav"
    audio_tools.write_audio(
        output_path,
        np.full(900, 0.3, dtype=np.float32),
        1_000,
    )
    prepared = client.post(
        (
            f"/api/projects/{project_id}/video-localization/tts/"
            "handoff/localized_0001"
        ),
        json={
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001"],
        },
    ).json()
    workflow_id = prepared["video_localization_workflow_id"]
    video_localization_service.register_single_tts_task(
        project_id,
        prepared["segment_id"],
        "task-manual-exact",
        workflow_id,
    )

    updated = video_localization_service.sync_single_tts_result(
        project_id,
        prepared["segment_id"],
        result_id="result-manual-exact",
        output_path=str(output_path),
        duration_ms=900,
        task_id="task-manual-exact",
        generation_id="task-manual-exact",
        dubbing_target_subtitle_ids=["localized_0001"],
    )

    assert updated is not None
    clip = next(
        item
        for item in updated.timeline_clips
        if item.get("task_id") == "task-manual-exact"
    )
    assert clip["target_subtitle_ids"] == ["localized_0001"]
    assert clip["tts_target_text"] == "目标台词一"
    workflow = next(
        item for item in updated.tts_tasks if item.workflow_id == workflow_id
    )
    assert workflow.status == "success"
    assert workflow.timeline_clip_id == clip["clip_id"]


def test_parameter_pack_prefers_reviewed_source_cue_text_over_stale_aligned_words(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)
    draft = client.get(f"/api/projects/{project_id}/video-localization").json()
    draft["cues"][0]["en_subtitle_text"] = "Seedance"
    draft["transcription"]["words"][0]["text"] = "Seadance"
    assert client.put(f"/api/projects/{project_id}/video-localization", json=draft).status_code == 200

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json={
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001"],
        },
    )

    assert response.status_code == 200
    assert response.json()["source"]["ref_text"] == "Seedance"


def test_parameter_pack_derives_source_from_target_mapping_when_source_is_empty(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json={"target_subtitle_ids": ["localized_0001"], "source_cue_ids": []},
    )

    assert response.status_code == 200
    assert response.json()["source"]["cue_ids"] == ["cue_0003"]


def test_parameter_pack_allows_noncontiguous_target_subtitles(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json={
            "target_subtitle_ids": ["localized_0003", "localized_0001"],
            "source_cue_ids": ["cue_0001", "cue_0002"],
        },
    )

    assert response.status_code == 200
    pack = response.json()
    assert pack["target"]["subtitle_ids"] == ["localized_0001", "localized_0003"]
    assert pack["target"]["text"] == "目标台词一\n目标台词三"
    assert (pack["target"]["start_ms"], pack["target"]["end_ms"]) == (10_000, 14_000)


def test_parameter_pack_segment_id_distinguishes_different_middle_targets(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)

    first = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json={
            "target_subtitle_ids": ["localized_0001", "localized_0002", "localized_0004"],
            "source_cue_ids": ["cue_0001", "cue_0002"],
        },
    ).json()
    second = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json={
            "target_subtitle_ids": ["localized_0001", "localized_0003", "localized_0004"],
            "source_cue_ids": ["cue_0001", "cue_0002"],
        },
    ).json()

    assert first["target"]["segment_id"] != second["target"]["segment_id"]
    assert first["target"]["segment_id"].startswith("group_localized_0001_localized_0004_3_")
    assert second["target"]["segment_id"].startswith("group_localized_0001_localized_0004_3_")


def test_explicit_handoff_freezes_target_and_source_snapshots(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff/group_localized_0001_localized_0003_2",
        json={
            "target_subtitle_ids": ["localized_0001", "localized_0003"],
            "source_cue_ids": ["cue_0001", "cue_0002"],
        },
    )

    assert response.status_code == 200
    request = response.json()
    assert request["segment_id"].startswith("group_localized_0001_localized_0003_2_")
    assert request["video_localization_target_subtitle_ids"] == ["localized_0001", "localized_0003"]
    assert request["video_localization_source_cue_ids"] == ["cue_0001", "cue_0002"]
    workflow = client.get(
        f"/api/projects/{project_id}/video-localization/tts/tasks/{request['video_localization_workflow_id']}"
    ).json()
    frozen_pack = workflow["stages"][0]["parameters"]["video_localization_parameter_pack"]
    assert frozen_pack["target"]["subtitle_ids"] == ["localized_0001", "localized_0003"]
    assert frozen_pack["source"]["cue_ids"] == ["cue_0001", "cue_0002"]
    assert workflow["stages"][1]["parameters"]["target_snapshot"] == frozen_pack["target"]
    assert workflow["stages"][1]["parameters"]["source_snapshot"] == frozen_pack["source"]

    draft = client.get(f"/api/projects/{project_id}/video-localization").json()
    draft["localized_subtitles"][0]["tts_text"] = "被修改的目标台词"
    client.put(f"/api/projects/{project_id}/video-localization", json=draft)
    with pytest.raises(AppException) as changed:
        video_localization_service.finalize_single_tts_submission(GenerateRequest.model_validate(request))
    assert changed.value.code == "VIDEO_LOCALIZATION_TTS_SUBTITLE_CHANGED"


def test_preview_finalize_preserves_independent_target_and_source_ids(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)
    _create_dubbing_plan(client, project_id)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff-preview/localized_0001",
        json={
            "target_subtitle_ids": ["localized_0001"],
            "source_cue_ids": ["cue_0001", "cue_0002"],
        },
    )

    assert response.status_code == 200
    preview = GenerateRequest.model_validate(response.json())
    assert preview.video_localization_workflow_id is None

    finalized = video_localization_service.finalize_single_tts_submission(
        preview.model_copy(
            update={
                "ref_text": "Seedance workflow, recognized after adjusting the reference range.",
                "custom_reference_trim_start_ms": 1_000,
                "custom_reference_trim_end_ms": 4_000,
                "video_localization_execution_scope": "all_remaining",
                "video_localization_generation_attempt": 1,
                "video_localization_execution_start_group_id": (
                    "dubbing_group_0001"
                ),
                "video_localization_execution_end_group_id": (
                    "dubbing_group_0003"
                ),
                "video_localization_max_in_flight_groups": 2,
                "video_localization_ordinary_speed_baseline": 1.18,
            }
        )
    )

    assert finalized.localized_subtitle_id == "localized_0001"
    assert finalized.cue_id == "cue_0001"
    assert finalized.video_localization_target_subtitle_ids == ["localized_0001"]
    assert finalized.video_localization_source_cue_ids == ["cue_0001", "cue_0002"]
    assert finalized.video_localization_execution_scope == "all_remaining"
    assert finalized.video_localization_execution_start_group_id == "dubbing_group_0001"
    assert finalized.video_localization_execution_end_group_id == "dubbing_group_0003"
    assert finalized.video_localization_max_in_flight_groups == 2
    assert finalized.video_localization_ordinary_speed_baseline == 1.18
    workflow = video_localization_service.get_tts_task(
        project_id,
        finalized.video_localization_workflow_id or "",
    )
    assert workflow is not None
    frozen_pack = workflow.stages[0].parameters["video_localization_parameter_pack"]
    assert frozen_pack["target"]["subtitle_ids"] == ["localized_0001"]
    assert frozen_pack["source"]["cue_ids"] == ["cue_0001", "cue_0002"]
    parameters = workflow.stages[0].parameters
    assert parameters["video_localization_execution_start_group_id"] == "dubbing_group_0001"
    assert parameters["video_localization_execution_end_group_id"] == "dubbing_group_0003"
    assert parameters["video_localization_ordinary_speed_baseline"] == 1.18


def test_explicit_handoff_places_planned_result_without_legacy_review_labels(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)
    plan = _create_dubbing_plan(client, project_id)
    output_path = tmp_path / "generated.wav"
    audio_tools.write_audio(output_path, np.full(1_500, 0.3, dtype=np.float32), 1_000)
    request = client.post(
        f"/api/projects/{project_id}/video-localization/tts/handoff/group_localized_0001_localized_0003_2",
        json={
            "target_subtitle_ids": ["localized_0001", "localized_0003"],
            "source_cue_ids": ["cue_0001", "cue_0002"],
        },
    ).json()
    video_localization_service.register_single_tts_task(
        project_id,
        request["segment_id"],
        "task-explicit-placement",
        request["video_localization_workflow_id"],
    )

    updated = video_localization_service.sync_single_tts_result(
        project_id,
        request["segment_id"],
        result_id="result-explicit-placement",
        output_path=str(output_path),
        duration_ms=1_500,
        task_id="task-explicit-placement",
        generation_id="task-explicit-placement",
        dubbing_plan_revision=plan["plan_revision"],
        dubbing_group_id=plan["groups"][0]["group_id"],
        dubbing_target_subtitle_ids=plan["groups"][0][
            "subtitle_ids"
        ],
    )

    assert updated is not None
    clip = next(
        item
        for item in updated.timeline_clips
        if item.get("task_id") == "task-explicit-placement"
    )
    assert clip["subtitle_id"] == request["segment_id"]
    assert clip["target_subtitle_ids"] == ["localized_0001", "localized_0003"]
    assert clip["source_cue_ids"] == ["cue_0001", "cue_0002"]
    assert clip["target_start_ms"] == 10_000
    assert clip["target_end_ms"] == 14_000
    assert clip["start_ms"] == 10_000
    assert "cqc_status" not in clip
    assert updated.localized_subtitles[0].tts_result_id is None
    assert updated.localized_subtitles[2].tts_result_id is None
    candidate = next(
        item
        for item in updated.generated_candidates
        if item.get("task_id") == "task-explicit-placement"
    )
    assert candidate["candidate_id"] == "candidate_task-explicit-placement"
    assert candidate["result_id"] == "result-explicit-placement"
    assert Path(candidate["audio_path"]).is_file()
    assert candidate["audio_path"] != str(output_path)
    assert "cqc_status" not in candidate
    workflow = next(item for item in updated.tts_tasks if item.workflow_id == request["video_localization_workflow_id"])
    assert workflow.status == "success"
    assert workflow.timeline_clip_id == clip["clip_id"]


@pytest.mark.parametrize(
    ("source_cue_ids", "expected_code"),
    [
        (["cue_0001", "cue_0003"], "VIDEO_LOCALIZATION_TTS_SOURCE_NOT_CONTIGUOUS"),
        (["cue_0002", "cue_0003"], "VIDEO_LOCALIZATION_TTS_SOURCE_CROSS_SPEAKER"),
    ],
)
def test_parameter_pack_rejects_noncontiguous_or_cross_speaker_reference_audio(
    tmp_path: Path,
    monkeypatch,
    source_cue_ids: list[str],
    expected_code: str,
):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json={"target_subtitle_ids": ["localized_0001"], "source_cue_ids": source_cue_ids},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == expected_code


def test_parameter_pack_still_rejects_a_missing_explicit_source_cue(tmp_path: Path, monkeypatch):
    client, project_id, _vocals_path = _project_with_independent_ranges(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/video-localization/tts/parameter-pack",
        json={"target_subtitle_ids": ["localized_0001"], "source_cue_ids": ["cue_missing"]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VIDEO_LOCALIZATION_TTS_SOURCE_CUE_NOT_FOUND"
