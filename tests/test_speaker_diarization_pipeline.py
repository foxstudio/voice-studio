from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (
    asr_step_results,
    operation_queue,
    operation_state,
    speakers,
    subtitle_segmentation,
)
from app.models.schemas import (
    VideoLocalizationAlignedWord,
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationSpeakerCluster,
    VideoLocalizationTranscriptionState,
    VideoLocalizationTranscriptSegment,
)
from app.services import speaker_diarization_service
from app.services import speaker_verification_service
from app.schemas.voice_studio import AppSettings
from app.services.asr_providers.contracts import DiarizationSegment


def _word(index: int, speaker: str) -> VideoLocalizationAlignedWord:
    return VideoLocalizationAlignedWord(
        word_id=f"word_{index:06d}",
        segment_id="asr_0001",
        text=f"word{index}",
        start_ms=(index - 1) * 500,
        end_ms=index * 500,
        speaker_cluster_id=speaker,
    )


def _current_transcript_quality_cycle() -> dict:
    return {
        "prompt_version": "asr-flow-v5",
        "task_step_results": {},
        "step_result": {
            "status": "success",
            "purpose": "确认当前整篇听写可以进入校时。",
            "summary": "当前整篇听写已通过质量门。",
            "metrics": [],
            "sections": [],
            "notes": [],
        },
    }


def test_speaker_model_paths_follow_configured_data_and_model_roots(tmp_path, monkeypatch):
    settings = AppSettings(
        data_dir=str(tmp_path / "runtime-data"),
        model_dir=str(tmp_path / "model-store"),
    )
    model_names = {
        speaker_diarization_service.ENGINE_ID: "moss-transcribe-diarize-8bit",
        speaker_verification_service.ENGINE_ID: "campplus-speaker-verifier",
    }
    monkeypatch.setattr(speaker_diarization_service.settings_store, "get", lambda: settings)
    monkeypatch.setattr(
        speaker_diarization_service.settings_store,
        "model_path",
        lambda engine_id: tmp_path / "model-store" / model_names[engine_id],
    )
    monkeypatch.delenv("VOICE_STUDIO_MOSS_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("VOICE_STUDIO_MOSS_MODEL_DIR", raising=False)
    monkeypatch.delenv("VOICE_STUDIO_CAMPPLUS_ROOT", raising=False)
    monkeypatch.delenv("VOICE_STUDIO_CAMPPLUS_MODEL", raising=False)

    assert speaker_diarization_service.runtime_root() == (
        tmp_path / "runtime-data" / "engines" / "moss-transcribe-diarize"
    )
    assert speaker_diarization_service.model_path() == (tmp_path / "model-store" / "moss-transcribe-diarize-8bit")
    assert speaker_verification_service.runtime_root() == (
        tmp_path / "runtime-data" / "engines" / "campplus-speaker-verifier"
    )
    assert speaker_verification_service.model_path() == (tmp_path / "model-store" / "campplus-speaker-verifier")


def test_subtitle_boundaries_never_cross_speaker_clusters():
    words = [_word(1, "cluster_01"), _word(2, "cluster_01"), _word(3, "cluster_02"), _word(4, "cluster_02")]

    boundaries = subtitle_segmentation._optimal_boundaries(words)

    assert 2 in boundaries
    start = 0
    for end in boundaries:
        assert len({word.speaker_cluster_id for word in words[start:end]}) == 1
        start = end


def test_word_assignment_marks_overlapping_speakers_for_review():
    word = _word(1, "cluster_old")
    mapped = speaker_diarization_service.assign_words(
        [word],
        [
            {"start_ms": 0, "end_ms": 500, "speaker": "cluster_01"},
            {"start_ms": 300, "end_ms": 700, "speaker": "cluster_02"},
        ],
    )[0]

    assert mapped.speaker_cluster_id == "cluster_01"
    assert mapped.has_speaker_overlap is True
    assert mapped.speaker_confidence == 1.0
    assert (
        mapped.acoustic_support_start_ms,
        mapped.acoustic_support_end_ms,
        mapped.acoustic_support_source,
    ) == (0, 500, "speaker_diarization")


def test_word_assignment_does_not_invent_one_support_range_across_two_runs():
    word = _word(1, "cluster_old").model_copy(update={"start_ms": 0, "end_ms": 2_000})

    mapped = speaker_diarization_service.assign_words(
        [word],
        [
            {"start_ms": 100, "end_ms": 300, "speaker": "cluster_01"},
            {"start_ms": 1_500, "end_ms": 1_800, "speaker": "cluster_01"},
        ],
    )[0]

    assert mapped.speaker_cluster_id == "cluster_01"
    assert mapped.acoustic_support_start_ms is None
    assert mapped.acoustic_support_end_ms is None
    assert mapped.acoustic_support_source is None


def test_long_movie_diarization_windows_cover_the_full_timeline():
    duration_ms = 95 * 60 * 1000

    windows = speaker_diarization_service._long_audio_windows(duration_ms)

    assert len(windows) == 7
    assert windows[0] == (0, 900_000, 0, 902_000)
    assert windows[-1] == (
        5_400_000,
        duration_ms,
        5_398_000,
        duration_ms,
    )
    assert [item[0] for item in windows[1:]] == [item[1] for item in windows[:-1]]


def test_chunked_speaker_labels_are_unique_and_overlap_is_deduplicated():
    segments = (
        DiarizationSegment(0, 3_000, "S01", 0.9),
        DiarizationSegment(4_000, 8_000, "S02", 0.8),
    )

    globalized = speaker_diarization_service._globalize_chunk_segments(
        segments,
        chunk_index=2,
        read_start_ms=898_000,
        core_start_ms=900_000,
        core_end_ms=1_800_000,
        duration_ms=5_700_000,
    )

    assert globalized == (
        DiarizationSegment(
            902_000,
            906_000,
            "chunk_002:S02",
            0.8,
        ),
    )


def test_speaker_verification_keeps_useful_merges_when_one_label_is_too_short(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        speaker_verification_service,
        "health_check",
        lambda: {
            "healthy": True,
            "runtime_root": str(tmp_path),
            "model_path": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        speaker_verification_service,
        "_write_clips",
        lambda *_args: (
            [tmp_path / "a.wav", tmp_path / "b.wav"],
            ["S01", "S02"],
        ),
    )
    embeddings = np.zeros((2, 192), dtype=np.float32)
    embeddings[:, 0] = 1.0
    monkeypatch.setattr(
        speaker_verification_service,
        "_extract_embeddings",
        lambda *_args, **_kwargs: (embeddings, {}),
    )

    result = speaker_verification_service.consolidate_clusters(
        audio_path=tmp_path / "source.wav",
        segments=[
            {"start_ms": 0, "end_ms": 2_000, "speaker": "S01"},
            {"start_ms": 3_000, "end_ms": 5_000, "speaker": "S02"},
            {"start_ms": 6_000, "end_ms": 6_500, "speaker": "S03"},
        ],
    )

    assert result["status"] == "partial"
    assert result["reason"] == "insufficient_clean_audio"
    assert result["unverified_labels"] == ["S03"]
    assert result["mapping"]["S01"] == result["mapping"]["S02"]
    assert result["mapping"]["S03"] != result["mapping"]["S01"]


def test_diarization_clusters_bind_to_stable_business_speakers():
    draft = VideoLocalizationDraft()
    cluster = VideoLocalizationSpeakerCluster(
        cluster_id="cluster_01",
        source_label="S01",
        source_engine_id="moss-transcribe-diarize-mlx",
        start_ms=0,
        end_ms=1000,
        duration_ms=1000,
        segment_count=1,
    )
    cue = VideoLocalizationCue(cue_id="cue_0001", speaker_cluster_id="cluster_01", start_ms=0, end_ms=1000)

    created, bound_cues, bound_clusters = speakers.bind_diarization_clusters(draft, [cue], [cluster])
    reused, rebound_cues, _ = speakers.bind_diarization_clusters(
        draft.model_copy(update={"speakers": created}),
        [cue],
        [cluster],
    )

    assert len(created) == 1
    assert created[0].speaker_id == "speaker_01"
    assert created[0].acoustic_cluster_ids == ["cluster_01"]
    assert bound_cues[0].speaker_id == "speaker_01"
    assert bound_clusters[0].business_speaker_id == "speaker_01"
    assert len(reused) == 1
    assert rebound_cues[0].speaker_id == "speaker_01"


def test_asr_operation_defaults_to_nonfatal_auto_diarization_and_reports_existing_review():
    cluster = VideoLocalizationSpeakerCluster(
        cluster_id="cluster_01",
        source_label="S01",
        source_engine_id="moss-transcribe-diarize-mlx",
        start_ms=0,
        end_ms=1000,
        duration_ms=1000,
        segment_count=1,
        merge_status="needs_review",
    )
    draft = VideoLocalizationDraft(
        transcription=VideoLocalizationTranscriptionState(
            diarization_status="partial",
            diarization_engine_id="moss-transcribe-diarize-mlx",
            speaker_clusters=[cluster],
            quality_flags=["speaker_cluster_review_required"],
            transcript_quality_cycle=(_current_transcript_quality_cycle()),
        )
    )

    parameters = operation_queue._normalized_operation_parameters("english_asr", {}, draft)
    summary = operation_state.english_asr_summary(draft)

    assert parameters["diarization_engine_id"] == "auto"
    assert summary["diarization_status"] == "partial"
    assert summary["speaker_count"] == 1
    assert summary["speaker_review_required"] is True


def test_asr_task_detail_exposes_speaker_clusters_and_overlap_review():
    cluster = VideoLocalizationSpeakerCluster(
        cluster_id="cluster_01",
        source_label="S01",
        source_engine_id="moss-transcribe-diarize-mlx",
        start_ms=0,
        end_ms=1000,
        duration_ms=1000,
        segment_count=1,
        merge_status="needs_review",
    )
    segment = VideoLocalizationTranscriptSegment(
        segment_id="asr_0001",
        start_ms=0,
        end_ms=1000,
        raw_text="Hello.",
        speaker_cluster_id="cluster_01",
        has_speaker_overlap=True,
    )
    draft = VideoLocalizationDraft(
        transcription=VideoLocalizationTranscriptionState(
            engine_id="qwen3-asr-mlx",
            segments=[segment],
            diarization_status="partial",
            diarization_engine_id="moss-transcribe-diarize-mlx",
            speaker_clusters=[cluster],
            quality_flags=["speaker_overlap_review_required"],
            transcript_quality_cycle=(_current_transcript_quality_cycle()),
        )
    )

    result = asr_step_results.build_asr_step_results(
        draft,
        {"diarization": {"cluster_count": 1}},
    )["diarization"]

    assert result["status"] == "warning"
    assert "1 位匿名说话人" in result["summary"]
    assert {"label": "重叠片段", "value": "1"} in result["metrics"]
    assert result["sections"][0]["items"][0]["facts"][2]["value"] == "需要人工复核"
