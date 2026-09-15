from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    cues,
    localization_workflow_execution,
    localization_source,
    operation_queue,
    service,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationAsrVadTimingCorrectionRequest,
    VideoLocalizationAudioBoundaryEvidence,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationGlossaryEntry,
    VideoLocalizationSpeaker,
    VideoLocalizationTranscriptionState,
)
from app.main import app  # noqa: E402
from app.api.video_localization import (  # noqa: E402
    VideoLocalizationLocalizationOperationRequest,
)
from app.models.schemas import (  # noqa: E402
    LlmProviderListResponse,
    LlmProviderProfile,
)
from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    ProjectCreate,
)
from app.services import (  # noqa: E402
    database,
    project_store,
    settings_store,
)


def _configure(tmp_path: Path, *, enabled: bool) -> None:
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
            video_localization_development_step_control_enabled=enabled,
        )
    )


def _word(
    number: int,
    text: str,
    *,
    start_ms: int,
    end_ms: int,
) -> VideoLocalizationAlignedWord:
    return VideoLocalizationAlignedWord(
        word_id=f"word_{number:04d}",
        segment_id=f"segment_{1 if number <= 2 else 2:04d}",
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker_cluster_id="cluster_01",
        speaker_confidence=0.96,
        timing_confidence="high",
        timing_source="forced_aligner",
    )


def _draft(
    *,
    include_speaker: bool = True,
    include_pause: bool = True,
) -> VideoLocalizationDraft:
    words = [
        _word(1, "Hello", start_ms=100, end_ms=420),
        _word(2, "world.", start_ms=450, end_ms=900),
        _word(3, "Good", start_ms=1_100, end_ms=1_420),
        _word(4, "morning.", start_ms=1_450, end_ms=1_900),
    ]
    pauses = (
        [
            VideoLocalizationAudioBoundaryEvidence(
                boundary_id="word_0002:word_0003",
                left_word_id="word_0002",
                right_word_id="word_0003",
                start_ms=900,
                end_ms=1_100,
                gap_ms=200,
                low_energy_ms=180,
                low_energy_ratio=0.9,
                gap_rms_dbfs=-42.0,
                speech_reference_dbfs=-20.0,
                noise_floor_dbfs=-48.0,
                energy_drop_db=22.0,
                confidence="high",
            )
        ]
        if include_pause
        else []
    )
    return VideoLocalizationDraft(
        source_media=VideoLocalizationDraft().source_media.model_copy(
            update={
                "filename": "private-video.mp4",
                "video_path": "/Users/example/秘密/private-video.mp4",
                "audio_path": "/Users/example/秘密/private-audio.wav",
                "duration_ms": 2_000,
            }
        ),
        cues=[
            VideoLocalizationCue(
                cue_id="cue_0001",
                speaker_id="speaker_01" if include_speaker else None,
                speaker_cluster_id="cluster_01",
                start_ms=100,
                end_ms=900,
                en_subtitle_text="Hello world.",
                source_word_ids=["word_0001", "word_0002"],
                transcription_revision_id="revision_001",
            ),
            VideoLocalizationCue(
                cue_id="cue_0002",
                speaker_id="speaker_01" if include_speaker else None,
                speaker_cluster_id="cluster_01",
                start_ms=1_100,
                end_ms=1_900,
                en_subtitle_text="Good morning.",
                source_word_ids=["word_0003", "word_0004"],
                transcription_revision_id="revision_001",
            ),
        ],
        transcription=VideoLocalizationTranscriptionState(
            revision_id="revision_001",
            language="en",
            source_track_id="vocals",
            source_audio_sha256="a" * 64,
            alignment_source_track_id="vocals",
            alignment_audio_sha256="a" * 64,
            corrected_text="Hello world. Good morning.",
            words=words,
            audio_boundary_status="completed" if include_pause else "not_run",
            audio_boundary_features=pauses,
            subtitle_entry_by_word_id={
                "word_0001": 180,
                "word_0003": 1_180,
            },
        ),
        speakers=(
            [
                VideoLocalizationSpeaker(
                    speaker_id="speaker_01",
                    display_name="主持人",
                    acoustic_cluster_ids=["cluster_01"],
                    notes="语气轻松",
                )
            ]
            if include_speaker
            else []
        ),
        glossary=[
            VideoLocalizationGlossaryEntry(
                glossary_id="glossary_01",
                source_text="Good morning",
                zh_text="早上好",
                notes="保持口语",
            )
        ],
        scene_context="两人科技访谈的开场。",
    )


def _lock(
    draft: VideoLocalizationDraft,
) -> localization_source.LocalizationSourceLockResult:
    request = localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(draft)
    return localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(request)


def test_source_lock_carries_optional_subtitle_entries_without_moving_words():
    locked = _lock(_draft())

    assert locked.input.words[0].start_ms == 100
    assert locked.input.words[0].display_entry_ms == 180
    assert locked.input.words[1].start_ms == 450
    assert locked.input.words[1].display_entry_ms is None


def test_source_timing_correction_provenance_reaches_localization_lock_with_zero_width_warning():
    draft = _draft()
    assert draft.transcription is not None
    draft = draft.model_copy(
        update={
            "stems": draft.stems.model_copy(
                update={"separation_status": "completed"}
            ),
            "transcription": draft.transcription.model_copy(
                update={"alignment_status": "completed"}
            ),
        }
    )
    corrected = cues.apply_asr_vad_source_timing_correction(
        draft,
        VideoLocalizationAsrVadTimingCorrectionRequest(
            expected_project_revision="1",
            transcription_revision_id="revision_001",
            source_track_id="vocals",
            audio_sha256="a" * 64,
            analysis_protocol="fixed-vocals-vad-fixture-v1",
            analysis_start_ms=100,
            analysis_end_ms=1_900,
            word_timings=[
                {
                    "word_id": "word_0001",
                    "text": "Hello",
                    "start_ms": 120,
                    "end_ms": 400,
                },
                {
                    "word_id": "word_0002",
                    "text": "world.",
                    "start_ms": 430,
                    "end_ms": 430,
                },
                {
                    "word_id": "word_0003",
                    "text": "Good",
                    "start_ms": 1_120,
                    "end_ms": 1_400,
                },
                {
                    "word_id": "word_0004",
                    "text": "morning.",
                    "start_ms": 1_450,
                    "end_ms": 1_880,
                },
            ],
            cue_corrections=[
                {
                    "cue_id": "cue_0001",
                    "expected_start_ms": 100,
                    "expected_end_ms": 900,
                    "start_ms": 120,
                    "end_ms": 430,
                    "source_word_ids": ["word_0001", "word_0002"],
                    "confirm_timing": False,
                },
                {
                    "cue_id": "cue_0002",
                    "expected_start_ms": 1_100,
                    "expected_end_ms": 1_900,
                    "start_ms": 1_120,
                    "end_ms": 1_880,
                    "source_word_ids": ["word_0003", "word_0004"],
                    "confirm_timing": False,
                },
            ],
        ),
    )

    locked = _lock(corrected)
    round_tripped = localization_source.LocalizationSourceLockResult.model_validate_json(
        locked.model_dump_json()
    )

    timing_by_word = {
        word.word_id: (word.timing_source, word.timing_confidence)
        for word in round_tripped.input.words
    }
    assert timing_by_word == {
        "word_0001": ("asr_vad_verified", "high"),
        "word_0002": ("forced_aligner", "low"),
        "word_0003": ("asr_vad_verified", "high"),
        "word_0004": ("asr_vad_verified", "high"),
    }
    assert round_tripped.input.words[1].start_ms == round_tripped.input.words[1].end_ms == 430
    assert round_tripped.quality_summary.status == "warning"
    zero_width_warning = next(
        warning
        for warning in round_tripped.warnings
        if warning.code == "zero_width_word_timing"
    )
    assert zero_width_warning.source_ids == ["word_0002"]


def test_new_asr_and_localization_tasks_use_current_default_llm_profile(
    monkeypatch,
):
    monkeypatch.setattr(
        operation_queue.settings_store,
        "llm_profiles",
        lambda: SimpleNamespace(default_profile_id="profile_current"),
    )
    draft = _draft()

    asr = operation_queue._normalized_operation_parameters(
        "english_asr",
        {"execution_mode": "full"},
        draft,
    )
    localization = operation_queue._normalized_operation_parameters(
        "localization_draft",
        {},
        draft,
    )
    explicit = operation_queue._normalized_operation_parameters(
        "localization_draft",
        {
            "profile_id": "profile_explicit",
        },
        draft,
    )

    assert asr["profile_id"] == "profile_current"
    assert localization["profile_id"] == "profile_current"
    assert explicit["profile_id"] == "profile_explicit"


def test_lock_source_builds_complete_versioned_path_free_snapshot():
    result = _lock(_draft())

    assert result.contract_version == "localization-source-lock-v1"
    assert result.input.contract_version == "localization-source-lock-input-v1"
    assert result.quality_summary.status == "passed"
    assert result.quality_summary.model_call_count == 0
    assert [cue.cue_id for cue in result.input.cues] == [
        "cue_0001",
        "cue_0002",
    ]
    assert [word.word_id for word in result.input.words] == [
        "word_0001",
        "word_0002",
        "word_0003",
        "word_0004",
    ]
    assert result.input.pauses[0].boundary_id == "word_0002:word_0003"
    assert result.input.cues[1].source_word_ids == [
        "word_0003",
        "word_0004",
    ]

    serialized = result.model_dump_json()
    assert "/Users/example" not in serialized
    assert "private-video.mp4" not in serialized
    assert "private-audio.wav" not in serialized
    assert "video_path" not in serialized
    assert "audio_path" not in serialized


def test_lock_source_rejects_missing_core_cues_words_and_timing():
    with pytest.raises(ValueError, match="至少一条"):
        localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(_draft().model_copy(update={"cues": []}))

    no_words = _draft()
    no_words = no_words.model_copy(update={"transcription": no_words.transcription.model_copy(update={"words": []})})
    with pytest.raises(ValueError, match="逐词时间"):
        localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(no_words)

    invalid_timing = _draft()
    invalid_timing = invalid_timing.model_copy(
        update={
            "cues": [
                invalid_timing.cues[0].model_copy(update={"start_ms": 500, "end_ms": 500}),
                invalid_timing.cues[1],
            ]
        }
    )
    with pytest.raises(ValueError, match="缺少有效时间"):
        localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(invalid_timing)


def test_lock_source_keeps_missing_optional_speaker_and_pause_as_warnings():
    result = _lock(_draft(include_speaker=False, include_pause=False))

    assert result.quality_summary.status == "warning"
    assert [warning.code for warning in result.warnings] == [
        "speaker_data_missing",
        "audio_pauses_missing",
    ]
    assert result.quality_summary.word_references_complete is True
    assert result.quality_summary.pause_references_complete is True


def test_source_lock_omits_long_unsupported_asr_hallucination_run():
    base = _draft()
    words = [
        _word(1, "Intro.", start_ms=100, end_ms=500),
        _word(2, "Sinin", start_ms=10_000, end_ms=14_000).model_copy(
            update={
                "segment_id": "segment_0002",
                "speaker_cluster_id": None,
                "speaker_confidence": None,
                "timing_confidence": "low",
                "timing_source": "asr_segment_interpolation",
            }
        ),
        _word(3, "masalindeyken", start_ms=14_100, end_ms=21_000).model_copy(
            update={
                "segment_id": "segment_0002",
                "speaker_cluster_id": None,
                "speaker_confidence": None,
                "timing_confidence": "low",
                "timing_source": "asr_segment_interpolation",
            }
        ),
        _word(4, "gözümüz", start_ms=21_100, end_ms=28_000).model_copy(
            update={
                "segment_id": "segment_0002",
                "speaker_cluster_id": None,
                "speaker_confidence": None,
                "timing_confidence": "low",
                "timing_source": "asr_segment_interpolation",
            }
        ),
        _word(5, "makutepalamba", start_ms=28_100, end_ms=29_000).model_copy(
            update={
                "segment_id": "segment_0003",
                "speaker_cluster_id": None,
                "speaker_confidence": None,
                "timing_confidence": "medium",
            }
        ),
        _word(6, "Continue.", start_ms=30_000, end_ms=30_500).model_copy(update={"segment_id": "segment_0004"}),
    ]
    cues = [
        VideoLocalizationCue(
            cue_id="cue_0001",
            speaker_id="speaker_01",
            speaker_cluster_id="cluster_01",
            start_ms=100,
            end_ms=500,
            en_subtitle_text="Intro.",
            source_word_ids=["word_0001"],
            transcription_revision_id="revision_001",
        ),
        VideoLocalizationCue(
            cue_id="cue_0002",
            start_ms=10_000,
            end_ms=21_000,
            en_subtitle_text="Sinin masalindeyken",
            source_word_ids=["word_0002", "word_0003"],
            transcription_revision_id="revision_001",
            timing_confidence="low",
            review_status="needs_review",
            quality_flags=[
                "segment_timing_interpolated",
                "timing_review_required",
            ],
        ),
        VideoLocalizationCue(
            cue_id="cue_0003",
            start_ms=21_100,
            end_ms=28_000,
            en_subtitle_text="gözümüz",
            source_word_ids=["word_0004"],
            transcription_revision_id="revision_001",
            timing_confidence="low",
            review_status="needs_review",
            quality_flags=[
                "segment_timing_interpolated",
                "timing_review_required",
            ],
        ),
        VideoLocalizationCue(
            cue_id="cue_0004",
            start_ms=28_100,
            end_ms=29_000,
            en_subtitle_text="makutepalamba",
            source_word_ids=["word_0005"],
            transcription_revision_id="revision_001",
            timing_confidence="medium",
            review_status="needs_review",
            quality_flags=["needs_speaker_assignment"],
        ),
        VideoLocalizationCue(
            cue_id="cue_0005",
            speaker_id="speaker_01",
            speaker_cluster_id="cluster_01",
            start_ms=30_000,
            end_ms=30_500,
            en_subtitle_text="Continue.",
            source_word_ids=["word_0006"],
            transcription_revision_id="revision_001",
        ),
    ]
    draft = base.model_copy(
        update={
            "cues": cues,
            "transcription": base.transcription.model_copy(
                update={
                    "corrected_text": ("Intro. Sinin masalindeyken gözümüz makutepalamba Continue."),
                    "words": words,
                    "audio_boundary_features": [],
                    "subtitle_entry_by_word_id": {},
                }
            ),
        }
    )

    locked = _lock(draft)

    assert [cue.cue_id for cue in locked.input.cues] == [
        "cue_0001",
        "cue_0005",
    ]
    assert [word.word_id for word in locked.input.words] == [
        "word_0001",
        "word_0006",
    ]
    warning = next(item for item in locked.warnings if item.code == "unsupported_non_speech_run_omitted")
    assert warning.source_ids == [
        "cue_0002",
        "cue_0003",
        "cue_0004",
    ]


def test_source_lock_projects_isolated_span_to_contiguous_acoustic_support():
    base = _draft()
    words = list(base.transcription.words)
    words[0] = words[0].model_copy(
        update={
            "start_ms": 100,
            "end_ms": 7_100,
            "speaker_confidence": 0.08,
            "acoustic_support_start_ms": 6_300,
            "acoustic_support_end_ms": 7_100,
            "acoustic_support_source": "speaker_diarization",
        }
    )
    words[1] = words[1].model_copy(update={"start_ms": 7_100, "end_ms": 7_500})
    cues = list(base.cues)
    cues[0] = cues[0].model_copy(update={"start_ms": 100, "end_ms": 7_500})
    cues[1] = cues[1].model_copy(update={"start_ms": 8_000, "end_ms": 8_800})
    words[2] = words[2].model_copy(update={"start_ms": 8_000, "end_ms": 8_320})
    words[3] = words[3].model_copy(update={"start_ms": 8_350, "end_ms": 8_800})
    draft = base.model_copy(
        update={
            "cues": cues,
            "source_media": base.source_media.model_copy(update={"duration_ms": 9_000}),
            "transcription": base.transcription.model_copy(
                update={
                    "words": words,
                    "audio_boundary_features": [],
                    "subtitle_entry_by_word_id": {"word_0001": 180},
                }
            ),
        }
    )

    locked = _lock(draft)

    first_word = locked.input.words[0]
    first_cue = locked.input.cues[0]
    assert words[0].start_ms == 100
    assert words[0].end_ms == 7_100
    assert first_word.start_ms == 6_300
    assert first_word.end_ms == 7_100
    assert first_word.display_entry_ms == 6_300
    assert first_cue.start_ms == 6_300
    assert first_cue.end_ms == 7_500


def test_source_lock_blocks_when_long_word_has_no_acoustic_support():
    base = _draft()
    words = list(base.transcription.words)
    words[0] = words[0].model_copy(
        update={
            "start_ms": 100,
            "end_ms": 7_100,
            "speaker_confidence": 0.08,
            "has_speaker_overlap": True,
        }
    )
    words[1] = words[1].model_copy(update={"start_ms": 7_100, "end_ms": 7_500})
    words[2] = words[2].model_copy(update={"start_ms": 8_000, "end_ms": 8_320})
    words[3] = words[3].model_copy(update={"start_ms": 8_350, "end_ms": 8_800})
    cues = list(base.cues)
    cues[0] = cues[0].model_copy(update={"start_ms": 100, "end_ms": 7_500})
    cues[1] = cues[1].model_copy(update={"start_ms": 8_000, "end_ms": 8_800})
    draft = base.model_copy(
        update={
            "cues": cues,
            "source_media": base.source_media.model_copy(update={"duration_ms": 9_000}),
            "transcription": base.transcription.model_copy(update={"words": words, "audio_boundary_features": []}),
        }
    )

    with pytest.raises(ValueError, match="刷新 ASR|不能用猜测时间"):
        _lock(draft)


def test_lock_source_fingerprints_are_stable_and_component_scoped():
    first = _lock(_draft())
    second = _lock(_draft())
    moved_paths = _draft().model_copy(
        update={
            "source_media": _draft().source_media.model_copy(
                update={
                    "video_path": "/another/private/location/video.mp4",
                    "audio_path": "/another/private/location/audio.wav",
                }
            )
        }
    )
    path_changed = _lock(moved_paths)

    assert first.source_fingerprint == second.source_fingerprint
    assert first.component_fingerprints == second.component_fingerprints
    assert first.source_fingerprint == path_changed.source_fingerprint

    glossary_changed = _draft()
    glossary_changed = glossary_changed.model_copy(
        update={"glossary": [glossary_changed.glossary[0].model_copy(update={"zh_text": "上午好"})]}
    )
    changed = _lock(glossary_changed)
    assert changed.component_fingerprints.glossary != (first.component_fingerprints.glossary)
    assert changed.component_fingerprints.cues == (first.component_fingerprints.cues)
    assert changed.source_fingerprint != first.source_fingerprint


def test_lock_source_ignores_downstream_localization_status_flags():
    before = _draft()
    after = before.model_copy(
        update={
            "cues": [
                cue.model_copy(
                    update={
                        "quality_flags": [flag for flag in cue.quality_flags if flag != "needs_zh_localization"]
                        + [
                            "localization_v3_dual_track",
                            "tts_generated",
                        ]
                    }
                )
                for cue in before.cues
            ]
        }
    )

    before_lock = _lock(before)
    after_lock = _lock(after)

    assert after_lock.component_fingerprints.cues == (before_lock.component_fingerprints.cues)
    assert after_lock.source_fingerprint == (before_lock.source_fingerprint)


def test_lock_source_rejects_broken_word_and_pause_references():
    broken_cue = _draft()
    broken_cue = broken_cue.model_copy(
        update={
            "cues": [
                broken_cue.cues[0].model_copy(
                    update={
                        "source_word_ids": [
                            "word_0001",
                            "word_missing",
                        ]
                    }
                ),
                broken_cue.cues[1],
            ]
        }
    )
    with pytest.raises(ValueError, match="不存在的逐词时间"):
        _lock(broken_cue)

    request = localization_source.DEFAULT_LOCALIZATION_PIPELINE.build_input(_draft())
    broken_pause = request.pauses[0].model_copy(
        update={
            "boundary_id": "word_0001:word_0004",
            "left_word_id": "word_0001",
            "right_word_id": "word_0004",
        }
    )
    with pytest.raises(ValueError, match="没有连接相邻词"):
        localization_source.DEFAULT_LOCALIZATION_PIPELINE.lock_source(
            request.model_copy(update={"pauses": [broken_pause]})
        )


def test_source_lock_step_result_uses_even_samples_and_plain_chinese_debug():
    base = _draft()
    cues = []
    words = []
    for number in range(1, 11):
        word = _word(
            number,
            f"word-{number}",
            start_ms=number * 1_000,
            end_ms=number * 1_000 + 500,
        ).model_copy(
            update={
                "segment_id": f"segment_{number:04d}",
                "speaker_cluster_id": None,
            }
        )
        words.append(word)
        cues.append(
            VideoLocalizationCue(
                cue_id=f"cue_{number:04d}",
                start_ms=word.start_ms,
                end_ms=word.end_ms,
                en_subtitle_text=f"Sentence {number}.",
                source_word_ids=[word.word_id],
                transcription_revision_id="revision_001",
            )
        )
    draft = base.model_copy(
        update={
            "cues": cues,
            "speakers": [],
            "transcription": base.transcription.model_copy(
                update={
                    "words": words,
                    "audio_boundary_features": [],
                }
            ),
        }
    )
    step = localization_source.project_localization_source_lock_step_result(
        _lock(draft),
        sample_limit=4,
    )

    samples = step["sections"][0]["items"]
    assert [item["title"] for item in samples] == [
        "cue_0001",
        "cue_0004",
        "cue_0007",
        "cue_0010",
    ]
    assert step["coverage"] == {
        "mode": "sample",
        "shown_count": 4,
        "total_count": 10,
        "unit": "条原文字幕",
    }
    debug_metrics = {item["label"]: item["value"] for item in step["debug"]["metrics"]}
    assert debug_metrics["输出契约"] == "localization-source-lock-v1"
    assert debug_metrics["模型调用"] == "0 次"
    assert debug_metrics["模型费用"] == "0"
    assert json.dumps(step, ensure_ascii=False).count("/Users/") == 0


def test_localization_v3_exposes_guarded_incremental_development_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _configure(tmp_path, enabled=True)
    # This contract test checks admission only. A background worker must not
    # outlive its fixture or race the next test's database/settings root.
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _item: None)
    project = project_store.create_project(ProjectCreate(name="本土化 v3 公开契约"))
    service.save_video_localization(project.project_id, _draft())

    client = TestClient(app)
    workflow = client.get(f"/api/projects/{project.project_id}/video-localization/workflows/localization")
    assert workflow.status_code == 200
    assert workflow.json()["workflow_id"] == "localization-v3"
    assert workflow.json()["stages"][0]["atomic_tasks"][0]["id"] == ("lock_localization_source")
    assert workflow.json()["stages"][0]["atomic_tasks"][2]["id"] == ("analyze_localization_document")
    assert workflow.json()["stages"][0]["atomic_tasks"][1]["label"] == ("固定本次本土化要求")
    assert (
        workflow.json()["stages"][0]["atomic_tasks"][1]["output_contract_version"] == "localization-context-intent-v2"
    )
    assert workflow.json()["stages"][2]["atomic_tasks"][0]["id"] == ("lock_localization_creation_context")
    assert workflow.json()["stages"][2]["atomic_tasks"][1]["id"] == ("generate_localization_spoken_script")
    assert workflow.json()["stages"][4]["atomic_tasks"][0]["id"] == ("validate_localization_tracks")
    requirements = client.get(f"/api/projects/{project.project_id}/video-localization/localization-requirements")
    assert requirements.status_code == 200
    requirements_payload = requirements.json()
    assert requirements_payload["default_profile_id"] == ("zh_cn_native_creator_v1")
    assert requirements_payload["profiles"][0]["deliverables"]["label"] == ("中文字幕与配音台词")
    assert requirements_payload["profiles"][0]["timing"]["label"] == ("按画面语义时间对应")

    development_submit = client.post(
        f"/api/projects/{project.project_id}/video-localization/operations/localization",
        json={
            "execution_mode": "development_target",
            "development_target_step_id": "lock_localization_source",
            "development_session_id": "localization-session-1",
        },
    )
    assert development_submit.status_code == 200
    assert development_submit.json()["label"] == "本土化流程开发"
    assert development_submit.json()["parameters"]["development_target_step_id"] == "lock_localization_source"

    schema = app.openapi()
    request = schema["components"]["schemas"]["VideoLocalizationLocalizationOperationRequest"]
    assert "execution_mode" in request["properties"]
    assert "development_target_step_id" in request["properties"]
    assert "development_session_id" in request["properties"]
    assert "stop_after_step" not in request["properties"]
    assert {
        "source_language",
        "target_language",
        "profile_id",
        "localization_requirements_id",
    }.issubset(request["properties"])

    generic_submit = client.post(
        f"/api/projects/{project.project_id}/video-localization/operations",
        json={"kind": "localization_draft", "parameters": {}},
    )
    assert generic_submit.status_code == 400
    assert generic_submit.json()["error"]["code"] == "INVALID_REQUEST"


def test_recovered_candidates_are_explicit_development_only(tmp_path, monkeypatch):
    _configure(tmp_path, enabled=True)
    project = project_store.create_project(ProjectCreate(name="候选恢复契约"))
    service.save_video_localization(project.project_id, _draft())
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _item: None)
    client = TestClient(app)
    endpoint = f"/api/projects/{project.project_id}/video-localization/operations/localization"
    rejected = client.post(endpoint, json={"recover_verified_candidates": True})
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "INVALID_REQUEST"
    accepted = client.post(endpoint, json={
        "execution_mode": "development_target",
        "development_target_step_id": "lock_localization_source",
        "development_session_id": "receipt-session",
        "recover_verified_candidates": True,
    })
    assert accepted.status_code == 200, accepted.json()
    assert accepted.json()["parameters"]["recover_verified_candidates"] is True
    properties = app.openapi()["components"]["schemas"]["VideoLocalizationLocalizationOperationRequest"]["properties"]
    assert properties["recover_verified_candidates"]["default"] is False


def test_unknown_batch_retry_requires_explicit_target_and_persists_scope(tmp_path, monkeypatch):
    _configure(tmp_path, enabled=True)
    profile = LlmProviderProfile(profile_id="retry-fixture", name="Retry fixture",
                                 protocol="codex_cli", model_id="fixture-model", enabled=True)
    monkeypatch.setattr(settings_store, "llm_profiles", lambda: LlmProviderListResponse(
        profiles=[profile], default_profile_id=profile.profile_id))
    monkeypatch.setattr(settings_store, "llm_profile", lambda _id: profile)
    project = project_store.create_project(ProjectCreate(name="定点重试契约"))
    service.save_video_localization(project.project_id, _draft())
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _item: None)
    client = TestClient(app)
    endpoint = f"/api/projects/{project.project_id}/video-localization/operations/localization"
    target = "analyze_localization_document"
    step = target + ".llm_batch.fixture.a0." + "a" * 64
    with pytest.raises(ValueError, match="正式全流程不能授权"):
        VideoLocalizationLocalizationOperationRequest(
            retry_unknown_batch_step_id=step
        )
    body = {"execution_mode": "development_target", "development_target_step_id": target,
            "development_session_id": "explicit-retry-session", "retry_unknown_batch_step_id": step}
    for patch in ({"execution_mode": "full", "development_target_step_id": None, "development_session_id": None},
                  {"force_development_target": False},
                  {"retry_unknown_batch_step_id": "generate_localization_spoken_script.llm_batch.other"},
                  {"retry_unknown_batch_step_id": "../outside"}):
        rejected = client.post(endpoint, json={**body, **patch})
        assert rejected.status_code == 400, rejected.json()
    accepted = client.post(endpoint, json=body)
    assert accepted.status_code == 200, accepted.json()
    operation_id = accepted.json()["operation_id"]
    saved = client.get(endpoint.rsplit("/", 1)[0] + "/" + operation_id)
    assert saved.status_code == 200
    assert saved.json()["parameters"]["retry_unknown_batch_step_id"] == step
    operation_queue._mark_operation(project.project_id, operation_id, kind="localization_draft", status="failed")
    retried = client.post(endpoint.rsplit("/", 1)[0] + "/" + operation_id + "/retry")
    assert retried.status_code == 200, retried.json()
    assert "retry_unknown_batch_step_id" not in retried.json()["parameters"]
    properties = app.openapi()["components"]["schemas"]["VideoLocalizationLocalizationOperationRequest"]["properties"]
    assert properties["retry_unknown_batch_step_id"].get("default") is None
    assert "超时且未保存候选" in properties["retry_unknown_batch_step_id"]["description"]


def test_localization_development_nodes_reuse_upstream_and_never_commit(
    tmp_path: Path,
):
    _configure(tmp_path, enabled=True)
    project = project_store.create_project(ProjectCreate(name="本土化开发增量执行"))
    original = _draft()
    service.save_video_localization(project.project_id, original)
    before = service.get_video_localization(project.project_id)
    assert before is not None
    snapshot_root = tmp_path / "localization-materializations"

    _unchanged, first_summary = service.run_localization_v3_draft(
        project.project_id,
        operation_id="development-operation-1",
        development_execution=(
            localization_workflow_execution.LocalizationDevelopmentExecutionConfig(
                development_session_id="session-1",
                target_step_id="lock_localization_source",
                snapshot_root=snapshot_root,
            )
        ),
    )
    assert first_summary["executed_step_ids"] == ["lock_localization_source"]
    assert first_summary["reused_step_ids"] == []

    _unchanged, second_summary = service.run_localization_v3_draft(
        project.project_id,
        operation_id="development-operation-2",
        development_execution=(
            localization_workflow_execution.LocalizationDevelopmentExecutionConfig(
                development_session_id="session-1",
                target_step_id="lock_localization_context_intent",
                snapshot_root=snapshot_root,
            )
        ),
    )
    assert second_summary["reused_step_ids"] == ["lock_localization_source"]
    assert second_summary["executed_step_ids"] == ["lock_localization_context_intent"]
    assert second_summary["formal_project_data_changed"] is False
    assert (
        snapshot_root / "session-1" / "nodes" / "lock_localization_context_intent" / "materialization.json"
    ).is_file()
    saved = service.get_video_localization(project.project_id)
    assert saved is not None
    assert saved.model_dump(mode="json") == before.model_dump(mode="json")


def test_localization_development_session_invalidates_source_when_asr_changes(
    tmp_path: Path,
):
    _configure(tmp_path, enabled=True)
    project = project_store.create_project(ProjectCreate(name="本土化开发冻结输入"))
    original = _draft()
    service.save_video_localization(project.project_id, original)
    snapshot_root = tmp_path / "localization-materializations"

    _unchanged, source_summary = service.run_localization_v3_draft(
        project.project_id,
        operation_id="development-operation-source",
        development_execution=(
            localization_workflow_execution.LocalizationDevelopmentExecutionConfig(
                development_session_id="session-frozen-source",
                target_step_id="lock_localization_source",
                snapshot_root=snapshot_root,
            )
        ),
    )
    assert source_summary["executed_step_ids"] == ["lock_localization_source"]

    changed = original.model_copy(
        update={
            "cues": [
                original.cues[0],
                original.cues[1].model_copy(
                    update={"end_ms": 1_950},
                ),
            ],
            "transcription": original.transcription.model_copy(
                update={
                    "words": [
                        *original.transcription.words[:-1],
                        original.transcription.words[-1].model_copy(
                            update={"end_ms": 1_950},
                        ),
                    ],
                },
            ),
        },
        deep=True,
    )
    service.save_video_localization(project.project_id, changed)

    _unchanged, context_summary = service.run_localization_v3_draft(
        project.project_id,
        operation_id="development-operation-context",
        development_execution=(
            localization_workflow_execution.LocalizationDevelopmentExecutionConfig(
                development_session_id="session-frozen-source",
                target_step_id="lock_localization_context_intent",
                snapshot_root=snapshot_root,
            )
        ),
    )
    assert context_summary["reused_step_ids"] == []
    assert context_summary["executed_step_ids"] == [
        "lock_localization_source",
        "lock_localization_context_intent",
    ]


def test_localization_development_operation_runs_through_shared_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _configure(tmp_path, enabled=True)
    project = project_store.create_project(ProjectCreate(name="本土化开发任务队列"))
    service.save_video_localization(project.project_id, _draft())
    snapshot_root = tmp_path / "queued-localization-materializations"
    monkeypatch.setattr(
        operation_queue,
        "DEVELOPMENT_LOCALIZATION_WORKFLOW_CHECKPOINT_ROOT",
        snapshot_root,
    )
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _item: None)

    client = TestClient(app)
    submitted = client.post(
        f"/api/projects/{project.project_id}/video-localization/operations/localization",
        json={
            "execution_mode": "development_target",
            "development_target_step_id": "lock_localization_source",
            "development_session_id": "queue-session-1",
        },
    )
    assert submitted.status_code == 200
    operation_id = submitted.json()["operation_id"]

    operation_queue._process(project.project_id, operation_id)

    completed = operation_queue.get_operation(
        project.project_id,
        operation_id,
    )
    assert completed is not None
    assert completed.status == "success"
    assert completed.label == "本土化流程开发"
    assert completed.result_summary["execution_mode"] == ("development_target")
    assert completed.result_summary["executed_step_ids"] == ["lock_localization_source"]
    assert completed.result_summary["formal_project_data_changed"] is False
    assert (snapshot_root / "queue-session-1" / "nodes" / "lock_localization_source" / "materialization.json").is_file()


def test_localization_development_mode_is_guarded_and_can_target_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _configure(tmp_path, enabled=False)
    project = project_store.create_project(ProjectCreate(name="本土化开发安全边界"))
    service.save_video_localization(project.project_id, _draft())
    client = TestClient(app)
    request = {
        "execution_mode": "development_target",
        "development_target_step_id": "lock_localization_source",
        "development_session_id": "guard-session-1",
    }

    disabled = client.post(
        f"/api/projects/{project.project_id}/video-localization/operations/localization",
        json=request,
    )
    assert disabled.status_code == 400
    assert disabled.json()["error"]["code"] == ("VIDEO_LOCALIZATION_DEVELOPMENT_STEP_CONTROL_DISABLED")

    _configure(tmp_path, enabled=True)
    profile = LlmProviderProfile(
        profile_id="profile_current",
        name="Current profile",
        protocol="codex_cli",
        model_id="gpt-5.6",
        enabled=True,
    )
    monkeypatch.setattr(
        operation_queue.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(
            profiles=[profile],
            default_profile_id="profile_current",
        ),
    )
    monkeypatch.setattr(
        operation_queue.settings_store,
        "llm_profile",
        lambda profile_id: profile if profile_id == "profile_current" else None,
    )
    monkeypatch.setattr(operation_queue, "_enqueue", lambda _item: None)
    commit_request = {
        **request,
        "development_target_step_id": "commit_localization_tracks",
    }
    commit = client.post(
        f"/api/projects/{project.project_id}/video-localization/operations/localization",
        json=commit_request,
    )
    assert commit.status_code == 200, commit.json()
    assert commit.json()["parameters"]["development_target_step_id"] == ("commit_localization_tracks")
