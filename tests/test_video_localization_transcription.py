from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import (  # noqa: E402
    asr_timing_contracts,
    speaker_diarization,
    subtitle_entry_timing,
    transcript_quality_gate,
    transcription,
)
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationBoundaryReview,
    VideoLocalizationGlossaryEntry,
    VideoLocalizationResearchQuery,
    VideoLocalizationResearchSource,
    VideoLocalizationResearchState,
    VideoLocalizationTranscriptSegment,
    VideoLocalizationTranscriptionState,
)
from app.models.schemas import LlmProviderProfile  # noqa: E402
from app.errors import AppException  # noqa: E402
from app.schemas.voice_studio import TranscriptionSegment  # noqa: E402


def _segment(segment_id: str, text: str, start_ms: int = 0, end_ms: int = 1000):
    return VideoLocalizationTranscriptSegment(
        segment_id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        raw_text=text,
    )


def test_alignment_assigns_typed_diarization_segments_to_words(monkeypatch):
    aligned_word = VideoLocalizationAlignedWord(
        word_id="word_000001",
        segment_id="segment_0001",
        text="Hello",
        start_ms=100,
        end_ms=300,
        confidence=1.0,
    )
    monkeypatch.setattr(
        transcription,
        "align_segments",
        lambda *_args, **_kwargs: (
            [aligned_word],
            {"status": "completed"},
        ),
    )
    request = asr_timing_contracts.AsrAlignmentInput(
        alignment_audio_path="/tmp/vocals.wav",
        alignment_audio_sha256="audio-sha",
        alignment_source_track_id="vocals",
        segments=[_segment("segment_0001", "Hello")],
        diarization_segments=[
            speaker_diarization.SpeakerDiarizationSegment(
                start_ms=0,
                end_ms=500,
                speaker_cluster_id="cluster_01",
                source_speaker_label="speaker-a",
            )
        ],
        language="en",
        duration_ms=1_000,
    )

    result = transcription.run_alignment_step(request)

    assert result.words[0].speaker_cluster_id == "cluster_01"


def test_automatic_asr_context_terms_use_only_explicit_glossary():
    terms = transcription.automatic_asr_context_terms(
        source_video_path=(
            "/videos/How I made a film with (Seedance_2.0) [Director_Cut].mp4"
        ),
        glossary=[
            VideoLocalizationGlossaryEntry(
                source_text="seed ants",
                corrected_source_text="Seedance 2.0",
            ),
            VideoLocalizationGlossaryEntry(source_text="Dreamina"),
        ],
    )

    assert terms == ["Seedance 2.0", "Dreamina"]


def test_automatic_asr_context_terms_do_not_infer_spoken_words_from_filenames():
    terms = transcription.automatic_asr_context_terms(
        source_filename="I Added VFX in 4K (Seedance_2.0).webm",
        source_video_path="/projects/source/I_Added_VFX_in_4K_Seedance_2.0.webm",
        glossary=[],
    )

    assert terms == []


def test_transcribe_raw_passes_only_bounded_context_terms_to_asr_service(
    monkeypatch,
):
    received: dict[str, object] = {}

    def fake_transcribe(**kwargs):
        received.update(kwargs)
        return {
            "text": "Seedance 2.0",
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "Seedance 2.0",
                }
            ],
        }

    monkeypatch.setattr(transcription.asr_service, "transcribe", fake_transcribe)
    request = transcription.TranscribeRawInput(
        audio_path="/tmp/vocals.wav",
        audio_sha256="sha",
        engine_id="qwen3-asr-mlx",
        source_track_id="vocals",
        requested_language="en",
        context_terms=["Seedance 2.0", "Dreamina"],
    )

    result = transcription.transcribe_raw(request)

    assert received["hotwords"] == ("Seedance 2.0", "Dreamina")
    assert result.raw_text == "Seedance 2.0"


def _configure_llm(monkeypatch):
    profile = LlmProviderProfile(
        profile_id="review",
        name="Review",
        base_url="https://llm.example.com/v1",
        model_id="review-model",
        enabled=True,
        api_key_configured=True,
    )
    return profile


def _quality_gate_result(
    segments: list[VideoLocalizationTranscriptSegment],
    *,
    decision: str,
) -> transcript_quality_gate.AsrTranscriptQualityGateResult:
    advisory = decision == "ready_with_advisory"
    result_decision = "ready_for_alignment" if advisory else decision
    request = transcript_quality_gate.AsrTranscriptQualityGateInput(
        upstream_operation_id="whole-recheck-operation",
        source_track_id="vocals",
        source_audio_sha256="source-sha",
        language="en",
        round_index=1,
        segments=segments,
        upstream_status=(
            "completed"
            if result_decision == "ready_for_alignment" and not advisory
            else "partial"
            if advisory
            else "failed"
        ),
        upstream_passed=(result_decision == "ready_for_alignment" and not advisory),
        upstream_next_action=(
            "finish" if result_decision == "ready_for_alignment" and not advisory else "manual_review"
        ),
        unresolved_items=[],
        upstream_quality_summary={
            "status": (
                "passed"
                if result_decision == "ready_for_alignment" and not advisory
                else "warning"
                if advisory
                else "failed"
            ),
            "segment_count": len(segments),
            "source_text_unchanged": True,
            "segment_ids_unchanged": True,
            "source_timing_unchanged": True,
            "next_sections_cover_all_segments": True,
            "unresolved_items_reference_known_segments": True,
        },
    )
    return transcript_quality_gate.AsrTranscriptQualityGateResult(
        input=request,
        decision=result_decision,
        can_start_alignment=(result_decision == "ready_for_alignment"),
        blockers=(
            []
            if result_decision == "ready_for_alignment"
            else [
                {
                    "code": "upstream_failed",
                    "message": "全文复核未成功完成。",
                }
            ]
        ),
        warnings=(
            [
                {
                    "code": "review_recommended",
                    "message": "建议结合原音复听。",
                    "segment_id": segments[0].segment_id,
                }
            ]
            if advisory
            else []
        ),
        review_targets=(
            [
                {
                    "title": "确认专名发音",
                    "location": "00:00:00:00 – 00:00:01:00",
                    "detail": "请结合原音确认。",
                    "segment_id": segments[0].segment_id,
                }
            ]
            if advisory
            else []
        ),
        quality_summary={
            "status": (
                "passed"
                if result_decision == "ready_for_alignment" and not advisory
                else "warning"
                if advisory
                else "failed"
            ),
            "segment_count": len(segments),
            "complete_segments": True,
            "source_matches_upstream": True,
            "source_text_unchanged": True,
            "segment_ids_unchanged": True,
            "source_timing_unchanged": True,
            "manual_review_count": (1 if advisory else 0),
            "review_recommended_count": 1 if advisory else 0,
            "keep_original_count": 0,
            "next_round_count": 0,
        },
        duration_ms=1,
    )


def test_build_segments_clips_media_end_and_marks_unfinished_terminal_fragment():
    segments = transcription._build_segments(
        [
            TranscriptionSegment(
                start_ms=22_792,
                end_ms=24_234,
                text="Okay, that's the whole blueprint.",
                language="en",
            ),
            TranscriptionSegment(
                start_ms=24_234,
                end_ms=24_811,
                text="My real",
                language="en",
            ),
        ],
        "",
        24_791,
    )

    assert [segment.end_ms for segment in segments] == [24_234, 24_791]
    assert "media_end_clipped" in segments[-1].review_flags
    assert "terminal_fragment_review_required" in segments[-1].review_flags
    assert "terminal_fragment_review_required" not in segments[0].review_flags


def test_build_segments_does_not_mark_complete_terminal_sentence_for_review():
    segments = transcription._build_segments(
        [
            TranscriptionSegment(
                start_ms=24_234,
                end_ms=24_811,
                text="That is the whole blueprint.",
                language="en",
            )
        ],
        "",
        24_791,
    )

    assert segments[0].end_ms == 24_791
    assert "media_end_clipped" in segments[0].review_flags
    assert "terminal_fragment_review_required" not in segments[0].review_flags


def test_raw_asr_usability_allows_a_missing_leading_chunk_when_later_speech_is_valid():
    raw_result = transcription.build_transcribe_raw_output(
        input=transcription.TranscribeRawInput(
            audio_path="/tmp/fixed-leading-gap.wav",
            audio_sha256="fixed-audio-sha",
            engine_id="qwen3-asr-mlx",
            source_track_id="vocals",
            requested_language="en",
            duration_ms=60_000,
        ),
        raw_text="The explanation starts here.",
        language="en",
        segments=[
            _segment(
                "segment_1",
                "The explanation starts here.",
                31_670,
                60_000,
            )
        ],
        incomplete_chunk_ranges=[
            {
                "start_ms": 0,
                "end_ms": 31_670,
                "reason": "missing_text",
            }
        ],
    )

    transcription.ensure_transcribe_raw_usable(raw_result)


def test_raw_asr_usability_rejects_synthetic_unverified_timing():
    segment = _segment("segment_1", "Unverified fallback.", 0, 60_000)
    segment.review_flags = ["segment_timing_missing"]
    raw_result = transcription.build_transcribe_raw_output(
        input=transcription.TranscribeRawInput(
            audio_path="/tmp/fixed-synthetic-timing.wav",
            audio_sha256="fixed-audio-sha",
            engine_id="fallback-asr",
            source_track_id="original",
            requested_language="en",
            duration_ms=60_000,
        ),
        raw_text="Unverified fallback.",
        language="en",
        segments=[segment],
    )

    with pytest.raises(AppException) as exc_info:
        transcription.ensure_transcribe_raw_usable(raw_result)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_INCOMPLETE"


def test_transcript_coverage_accepts_segments_covering_diarized_speech():
    segments = [_segment("segment_1", "complete speech", 0, 20_000)]
    diarization = {
        "status": "completed",
        "segments": [
            {"start_ms": 1000, "end_ms": 9000},
            {"start_ms": 10_000, "end_ms": 19_000},
        ],
    }

    transcription._ensure_transcript_covers_diarized_speech(segments, diarization)


def test_transcript_coverage_rejects_sparse_asr_result_before_it_is_committed():
    segments = [
        _segment("segment_1", "opening", 0, 5000),
        _segment("segment_2", "ending", 55_000, 60_000),
    ]
    diarization = {
        "status": "completed",
        "segments": [{"start_ms": 0, "end_ms": 60_000}],
    }

    with pytest.raises(AppException) as exc_info:
        transcription._ensure_transcript_covers_diarized_speech(segments, diarization)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_INCOMPLETE"
    assert "17%" in exc_info.value.message


def test_transcript_coverage_rejects_a_long_missing_speech_gap_even_when_total_coverage_passes():
    segments = [
        _segment("segment_1", "opening", 0, 24_000),
        _segment("segment_2", "ending", 39_000, 60_000),
    ]
    diarization = {
        "status": "completed",
        "segments": [{"start_ms": 0, "end_ms": 60_000}],
    }

    with pytest.raises(AppException) as exc_info:
        transcription._ensure_transcript_covers_diarized_speech(segments, diarization)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_INCOMPLETE"
    assert "最长遗漏约 15秒" in exc_info.value.message


def test_transcript_coverage_rejects_synthetic_full_duration_timing():
    segments = [_segment("segment_1", "unverified fallback", 0, 60_000)]
    segments[0].review_flags = ["segment_timing_missing"]
    diarization = {
        "status": "completed",
        "segments": [{"start_ms": 0, "end_ms": 60_000}],
    }

    with pytest.raises(AppException) as exc_info:
        transcription._ensure_transcript_covers_diarized_speech(segments, diarization)

    assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_INCOMPLETE"
    assert "可验证的分段时间戳" in exc_info.value.message


def test_transcript_coverage_rejects_incomplete_chunk_that_overlaps_diarized_speech():
    segments = [_segment("segment_1", "opening", 0, 30_000)]
    diarization = {
        "status": "completed",
        "segments": [{"start_ms": 0, "end_ms": 60_000}],
    }

    with pytest.raises(AppException) as exc_info:
        transcription._ensure_transcript_covers_diarized_speech(
            segments,
            diarization,
            incomplete_chunk_ranges=[{"start_ms": 30_000, "end_ms": 60_000}],
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_INCOMPLETE"
    assert "30秒 – 1分" in exc_info.value.message


def test_transcript_coverage_allows_incomplete_chunk_with_no_diarized_speech():
    segments = [_segment("segment_1", "spoken opening", 0, 20_000)]
    diarization = {
        "status": "completed",
        "segments": [{"start_ms": 0, "end_ms": 20_000}],
    }

    transcription._ensure_transcript_covers_diarized_speech(
        segments,
        diarization,
        incomplete_chunk_ranges=[{"start_ms": 30_000, "end_ms": 60_000}],
    )


def test_transcript_coverage_rejects_incomplete_chunk_when_diarization_failed():
    segments = [_segment("segment_1", "partial text", 0, 20_000)]

    with pytest.raises(AppException) as exc_info:
        transcription._ensure_transcript_covers_diarized_speech(
            segments,
            {"status": "failed", "segments": []},
            incomplete_chunk_ranges=[{"start_ms": 30_000, "end_ms": 60_000}],
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_INCOMPLETE"


def test_transcript_coverage_rejects_synthetic_timing_when_diarization_failed():
    segments = [_segment("segment_1", "fallback", 0, 10_000)]
    segments[0].review_flags = ["segment_timing_missing"]

    with pytest.raises(AppException) as exc_info:
        transcription._ensure_transcript_covers_diarized_speech(
            segments,
            {"status": "failed", "segments": []},
        )

    assert exc_info.value.code == "VIDEO_LOCALIZATION_ASR_INCOMPLETE"


def test_transcribe_and_process_returns_state_and_reports_stage_progress(monkeypatch, tmp_path):
    review_profile = _configure_llm(monkeypatch)
    audio_path = tmp_path / "source.wav"
    alignment_audio_path = tmp_path / "original.wav"
    audio_path.write_bytes(b"audio")
    alignment_audio_path.write_bytes(b"original")
    progress = []
    reports = []
    alignment_inputs = []
    boundary_inputs = []
    boundary_review_kwargs = {}
    review_kwargs = {}

    monkeypatch.setattr(
        transcription.asr_service,
        "transcribe",
        lambda **_kwargs: {
            "segments": [{"start_ms": 0, "end_ms": 1000, "text": "Hello world.", "language": "en"}],
            "text": "Hello world.",
        },
    )
    monkeypatch.setattr(
        transcription.asr_service,
        "normalize_segments",
        lambda _items: [TranscriptionSegment(start_ms=0, end_ms=1000, text="Hello world.", language="en")],
    )

    def fake_language_review(segments, **kwargs):
        review_kwargs.update(kwargs)
        kwargs["on_progress"](0.30, "flow:understand_document|理解全文并规划复查")
        kwargs["on_report"](
            "understand_document",
            {
                "status": "success",
                "summary": "已理解完整转写。",
                "purpose": "理解全文。",
                "metrics": [],
                "sections": [],
                "notes": [],
            },
        )
        report = {
            "status": "passed",
            "assessment_rounds": 2,
            "repair_rounds": 0,
            "total_repairs": 0,
            "changes": [],
            "warnings": [],
            "step_result": {"status": "success", "summary": "ASR 语言复核完成。"},
            "task_flow": [],
            "task_step_results": {},
        }
        return transcription.asr_flow.AsrReviewRun(
            segments=segments,
            research=VideoLocalizationResearchState(status="not_needed"),
            profile_id=review_profile.profile_id,
            model_id="review-model",
            report=report,
            stage_timings={
                "understand_document": {"duration_ms": 11},
                "research": {"duration_ms": 5},
            },
            review_meta={
                "status": "completed",
                "profile_id": review_profile.profile_id,
                "model_id": "review-model",
                "error": None,
                "quality_flags": ["asr_flow_reviewed"],
                "task_flow": [],
                "task_step_results": {},
            },
        )

    def fake_align(path, *_args, **_kwargs):
        alignment_inputs.append(Path(path))
        return (
            [],
            {"status": "failed", "timing_confidence": "low", "quality_flags": ["alignment_unavailable"]},
        )

    def fake_boundaries(path, *_args, **_kwargs):
        boundary_inputs.append(Path(path))
        return [], {"status": "skipped", "quality_flags": []}

    monkeypatch.setattr(transcription, "align_segments", fake_align)
    monkeypatch.setattr(transcription.audio_boundaries, "analyze_word_boundaries", fake_boundaries)

    def fake_boundary_review(*_args, **kwargs):
        boundary_review_kwargs.update(kwargs)
        return [], {
            "status": "completed",
            "review_batch_count": 1,
            "review_round_count": 1,
            "profile_id": "review-profile",
            "model_id": "review-model",
            "rounds": [
                {
                    "round": 1,
                    "candidate_count": 1,
                    "batch_count": 1,
                    "duration_ms": 13,
                    "batches": [
                        {
                            "round": 1,
                            "batch": 1,
                            "candidate_count": 1,
                            "duration_ms": 13,
                            "status": "success",
                            "attempt_count": 1,
                        }
                    ],
                }
            ],
            "quality_flags": [],
        }

    monkeypatch.setattr(transcription.boundary_review, "review_candidate_boundaries", fake_boundary_review)
    monkeypatch.setattr(transcription.media_assets, "file_sha256", lambda path: Path(path).name)
    monkeypatch.setattr(
        transcription.speaker_diarization_service,
        "diarize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("diarization unavailable")),
    )
    existing_review = VideoLocalizationBoundaryReview(
        boundary_id="word_000001:word_000002",
        left_word_id="word_000001",
        right_word_id="word_000002",
        decision="allow",
        confidence=0.8,
    )

    result = transcription.transcribe_and_process(
        audio_path=audio_path,
        alignment_audio_path=alignment_audio_path,
        engine_id="qwen3-asr-mlx",
        source_track_id="vocals",
        alignment_source_track_id="original",
        language="en",
        duration_ms=1000,
        existing_boundary_reviews=[existing_review],
        source_audio_sha256="cached-source",
        alignment_audio_sha256="cached-alignment",
        progress_callback=lambda value, stage: progress.append((value, stage)),
        report_callback=lambda step_id, result: reports.append((step_id, result)),
        diarization_engine_id="auto",
        transcript_review_runner=fake_language_review,
    )

    assert result.raw_text == "Hello world."
    assert review_kwargs["profile_id"] is None
    assert result.review_profile_id == review_profile.profile_id
    assert result.source_track_id == "vocals"
    assert result.source_audio_sha256 == "cached-source"
    assert result.alignment_source_track_id == "original"
    assert result.alignment_audio_sha256 == "cached-alignment"
    assert boundary_review_kwargs["existing_reviews"] == [existing_review]
    assert alignment_inputs == [alignment_audio_path]
    assert boundary_inputs == [audio_path]
    assert [value for value, _stage in progress] == [
        0.15,
        0.27,
        0.30,
        0.78,
        0.84,
        0.90,
        0.98,
    ]
    assert all(
        current >= previous
        for previous, current in zip(
            [value for value, _stage in progress],
            [value for value, _stage in progress][1:],
        )
    )
    assert progress[-1][1] == "正在生成字幕轨"
    assert [step_id for step_id, _result in reports] == [
        "asr",
        "diarization",
        "asr",
        "diarization",
        "initial_analysis_join",
        "understand_document",
        "alignment",
        "audio_boundaries",
        "boundary_review",
    ]
    assert reports[0][1]["status"] == "running"
    assert reports[1][1]["status"] == "running"
    assert reports[0][1]["metrics"] == [
        {"label": "执行方式", "value": "与说话人分析并行"}
    ]
    assert reports[1][1]["metrics"] == [
        {"label": "执行方式", "value": "与原始听写并行"}
    ]
    assert isinstance(reports[2][1]["duration_ms"], int)
    assert isinstance(reports[3][1]["duration_ms"], int)
    assert isinstance(reports[4][1]["duration_ms"], int)
    assert reports[-1][1]["status"] == "success"
    assert result.pipeline_timing["total_duration_ms"] >= 0
    assert set(result.pipeline_timing["stages"]) == {
        "asr",
        "diarization",
        "initial_analysis_join",
        "understand_document",
        "research",
        "alignment",
        "audio_boundaries",
        "boundary_review",
    }
    assert result.pipeline_timing["stages"]["understand_document"]["duration_ms"] == 11
    assert result.transcript_quality_cycle["assessment_rounds"] == 2
    assert result.pipeline_timing["stages"]["boundary_review"]["rounds"][0]["batches"][0]["duration_ms"] == 13
    assert result.pipeline_timing["stages"]["boundary_review"]["model_id"] == "review-model"
    assert result.diarization_status == "failed"
    assert result.diarization_error == "diarization unavailable"
    assert "speaker_diarization_failed" in result.quality_flags
    assert result.model_dump()["pipeline_timing"] == result.pipeline_timing

    monkeypatch.setattr(
        transcription.asr_service,
        "transcribe",
        lambda **_kwargs: {
            "segments": [
                {
                    "start_ms": 31_670,
                    "end_ms": 60_000,
                    "text": "The explanation starts here.",
                    "language": "en",
                }
            ],
            "text": "The explanation starts here.",
            "incomplete_chunk_ranges": [
                {
                    "start_ms": 0,
                    "end_ms": 31_670,
                    "reason": "missing_text",
                }
            ],
        },
    )
    monkeypatch.setattr(
        transcription.asr_service,
        "normalize_segments",
        lambda _items: [
            TranscriptionSegment(
                start_ms=31_670,
                end_ms=60_000,
                text="The explanation starts here.",
                language="en",
            )
        ],
    )
    partial_reports = []
    partial_result = transcription.transcribe_and_process(
        audio_path=audio_path,
        alignment_audio_path=alignment_audio_path,
        engine_id="qwen3-asr-mlx",
        source_track_id="vocals",
        alignment_source_track_id="original",
        language="en",
        duration_ms=60_000,
        source_audio_sha256="cached-source",
        alignment_audio_sha256="cached-alignment",
        progress_callback=lambda _value, _stage: None,
        report_callback=lambda step_id, step_result: partial_reports.append(
            (step_id, step_result)
        ),
        transcript_review_runner=fake_language_review,
    )

    assert partial_result.diarization_status == "not_run"
    assert partial_result.raw_asr_warning_codes == [
        "incomplete_chunk_ranges",
        "leading_gap_review_required",
    ]
    assert [
        item.model_dump()
        for item in partial_result.raw_asr_incomplete_ranges
    ] == [
        {
            "start_ms": 0,
            "end_ms": 31_670,
            "reason": "missing_text",
        }
    ]
    assert [
        step_result
        for step_id, step_result in partial_reports
        if step_id == "asr"
    ][-1]["status"] == "warning"
    assert "diarization" not in {
        step_id for step_id, _step_result in partial_reports
    }
    assert "initial_analysis_join" not in {
        step_id for step_id, _step_result in partial_reports
    }


@pytest.mark.parametrize(
    ("decision", "expected_error_code", "expected_align_calls"),
    [
        ("ready_for_alignment", None, 1),
        ("ready_with_advisory", None, 1),
        (
            "failed",
            "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_FAILED",
            0,
        ),
        (
            "missing",
            "VIDEO_LOCALIZATION_TRANSCRIPT_QUALITY_GATE_MISSING",
            0,
        ),
    ],
)
def test_transcript_quality_gate_controls_alignment_without_modifying_segments(
    monkeypatch,
    tmp_path,
    decision,
    expected_error_code,
    expected_align_calls,
):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"audio")
    segment = _segment("asr_0001", "Hello world.")
    before = segment.model_dump(mode="json")
    reports: list[tuple[str, dict]] = []
    align_calls = 0

    monkeypatch.setattr(
        transcription.asr_service,
        "transcribe",
        lambda **_kwargs: {
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "text": segment.raw_text,
                    "language": "en",
                }
            ],
            "text": segment.raw_text,
        },
    )
    monkeypatch.setattr(
        transcription.asr_service,
        "normalize_segments",
        lambda _items: [
            TranscriptionSegment(
                start_ms=0,
                end_ms=1_000,
                text=segment.raw_text,
                language="en",
            )
        ],
    )
    monkeypatch.setattr(
        transcription.media_assets,
        "file_sha256",
        lambda _path: "source-sha",
    )
    monkeypatch.setattr(
        transcription.speaker_diarization_service,
        "diarize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("diarization unavailable")),
    )

    def review_runner(segments, **_kwargs):
        return SimpleNamespace(
            segments=segments,
            research=VideoLocalizationResearchState(status="not_needed"),
            profile_id="review-profile",
            model_id="review-model",
            report={
                "status": "passed",
                "assessment_rounds": 1,
                "repair_rounds": 0,
                "total_repairs": 0,
                "changes": [],
                "warnings": [],
                "task_flow": [],
                "task_step_results": {},
            },
            stage_timings={},
            review_meta={
                "status": "completed",
                "profile_id": "review-profile",
                "model_id": "review-model",
                "error": None,
                "quality_flags": [],
                "task_flow": [],
                "task_step_results": {},
            },
            quality_gate=(
                None
                if decision == "missing"
                else _quality_gate_result(
                    segments,
                    decision=decision,
                )
            ),
        )

    def align(*_args, **_kwargs):
        nonlocal align_calls
        align_calls += 1
        return [], {
            "status": "failed",
            "timing_confidence": "low",
            "quality_flags": [],
        }

    monkeypatch.setattr(transcription, "align_segments", align)
    monkeypatch.setattr(
        transcription.audio_boundaries,
        "analyze_word_boundaries",
        lambda *_args, **_kwargs: (
            [],
            {"status": "skipped", "quality_flags": []},
        ),
    )
    monkeypatch.setattr(
        transcription.boundary_review,
        "review_candidate_boundaries",
        lambda *_args, **_kwargs: (
            [],
            {"status": "skipped", "quality_flags": []},
        ),
    )

    result = None
    if expected_error_code is None:
        result = transcription.transcribe_and_process(
            audio_path=audio_path,
            engine_id="qwen3-asr-mlx",
            source_track_id="vocals",
            language="en",
            duration_ms=1_000,
            transcript_review_runner=review_runner,
            report_callback=lambda step_id, result: reports.append((step_id, result)),
        )
    else:
        with pytest.raises(AppException) as exc_info:
            transcription.transcribe_and_process(
                audio_path=audio_path,
                engine_id="qwen3-asr-mlx",
                source_track_id="vocals",
                language="en",
                duration_ms=1_000,
                transcript_review_runner=review_runner,
                report_callback=lambda step_id, result: reports.append((step_id, result)),
            )
        assert exc_info.value.code == expected_error_code

    assert align_calls == expected_align_calls
    assert segment.model_dump(mode="json") == before
    gate_report = next(result for step_id, result in reports if step_id == "transcript_quality_gate")
    all_metrics = [
        *gate_report["metrics"],
        *gate_report["debug"]["metrics"],
    ]
    assert {
        "label": "模型调用",
        "value": "0",
    } in all_metrics
    if decision == "ready_with_advisory":
        assert gate_report["status"] == "warning"
        assert "已自动继续校时" in gate_report["summary"]
        assert gate_report["sections"][0]["title"] == "建议复听"
        assert result is not None
        persisted_gate_report = result.transcript_quality_cycle["task_step_results"]["transcript_quality_gate"]
        assert persisted_gate_report == gate_report
        assert {item["label"]: item["value"] for item in persisted_gate_report["metrics"]}["阻断项"] == "0"


@pytest.mark.parametrize(
    ("segment_language", "text", "expected"),
    [
        ("English", "Hello world", "en"),
        ("Chinese", "你好世界", "zh"),
        (None, "这是自动检测", "zh"),
        (None, "Automatic detection", "en"),
    ],
)
def test_auto_source_language_resolves_from_asr_output(
    segment_language,
    text,
    expected,
):
    class Segment:
        language = segment_language

    assert transcription._resolve_transcript_language("auto", [Segment()], text) == expected


def test_auto_source_language_tie_uses_first_detected_segment():
    class Segment:
        def __init__(self, language: str):
            self.language = language

    segments = [Segment("English"), Segment("Chinese")]

    assert transcription._resolve_transcript_language("auto", segments, "Hello 你好") == "en"


def test_subtitle_entry_uses_first_stable_peak_inside_one_to_three_video_frames():
    from app.domains.video_localization.schemas import VideoLocalizationAlignedWord

    audio = np.zeros(500, dtype=np.float32)
    audio[40:60] = 0.08
    audio[60:80] = 0.35
    audio[80:100] = 0.90
    audio[100:120] = 0.42
    audio[120:300] = 0.25
    word = VideoLocalizationAlignedWord(
        word_id="word_000001",
        segment_id="asr_0001",
        text="老",
        start_ms=0,
        end_ms=300,
        timing_confidence="high",
        timing_source="forced_aligner",
    )

    entries = subtitle_entry_timing.detect_subtitle_entries_from_audio(
        audio,
        1000,
        [word],
        frame_rate=24.0,
    )

    assert 70 <= entries[word.word_id] <= 105
    assert 1 <= entries[word.word_id] * 24 / 1000 <= 3


def test_subtitle_entry_falls_back_to_sustained_onset_when_no_stable_peak_exists():
    from app.domains.video_localization.schemas import VideoLocalizationAlignedWord

    audio = np.zeros(600, dtype=np.float32)
    audio[200:500] = 0.8
    word = VideoLocalizationAlignedWord(
        word_id="word_000001",
        segment_id="asr_0001",
        text="Hello",
        start_ms=0,
        end_ms=500,
        timing_confidence="high",
        timing_source="forced_aligner",
    )

    entries = subtitle_entry_timing.detect_subtitle_entries_from_audio(
        audio,
        1000,
        [word],
        frame_rate=24.0,
    )

    assert 100 <= entries[word.word_id] < 200


def test_subtitle_entry_peak_search_is_not_clipped_by_short_fa_word_end():
    from app.domains.video_localization.schemas import VideoLocalizationAlignedWord

    audio = np.zeros(500, dtype=np.float32)
    audio[45:65] = 0.08
    audio[65:85] = 0.35
    audio[85:105] = 0.90
    audio[105:125] = 0.42
    word = VideoLocalizationAlignedWord(
        word_id="word_000001",
        segment_id="asr_0001",
        text="老",
        start_ms=0,
        end_ms=40,
        timing_confidence="medium",
        timing_source="forced_aligner",
    )

    entries = subtitle_entry_timing.detect_subtitle_entries_from_audio(
        audio,
        1000,
        [word],
        frame_rate=24.0,
    )

    assert 70 <= entries[word.word_id] <= 110
    assert entries[word.word_id] > word.end_ms


def test_subtitle_entry_does_not_invent_frame_rate_for_peak_search():
    from app.domains.video_localization.schemas import VideoLocalizationAlignedWord

    audio = np.zeros(500, dtype=np.float32)
    audio[40:60] = 0.08
    audio[60:80] = 0.35
    audio[80:100] = 0.90
    audio[100:120] = 0.42
    word = VideoLocalizationAlignedWord(
        word_id="word_000001",
        segment_id="asr_0001",
        text="老",
        start_ms=0,
        end_ms=300,
        timing_confidence="high",
        timing_source="forced_aligner",
    )

    entries = subtitle_entry_timing.detect_subtitle_entries_from_audio(
        audio,
        1000,
        [word],
        frame_rate=None,
    )

    assert 0 <= entries[word.word_id] <= 50


def test_zero_duration_aligner_word_uses_real_waveform_end_boundary():
    audio = np.zeros(2_000, dtype=np.float32)
    audio[600:840] = 0.8
    words = [
        VideoLocalizationAlignedWord(
            word_id="word_000001",
            segment_id="asr_0001",
            text="对，",
            start_ms=600,
            end_ms=600,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word_000002",
            segment_id="asr_0001",
            text="好。",
            start_ms=1_080,
            end_ms=1_240,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
    ]

    repaired, count = (
        subtitle_entry_timing.repair_zero_duration_word_ends_from_audio(
            audio,
            1_000,
            words,
        )
    )

    assert count == 1
    assert 820 <= repaired[0].end_ms <= 860
    assert repaired[0].start_ms == 600
    assert repaired[0].timing_source == "forced_aligner"
    assert repaired[1] == words[1]


def test_zero_duration_aligner_word_without_acoustic_boundary_stays_invalid():
    word = VideoLocalizationAlignedWord(
        word_id="word_000001",
        segment_id="asr_0001",
        text="对，",
        start_ms=600,
        end_ms=600,
        timing_confidence="high",
        timing_source="forced_aligner",
    )
    next_word = word.model_copy(
        update={
            "word_id": "word_000002",
            "text": "好。",
            "start_ms": 1_080,
            "end_ms": 1_240,
        }
    )

    repaired, count = (
        subtitle_entry_timing.repair_zero_duration_word_ends_from_audio(
            np.zeros(2_000, dtype=np.float32),
            1_000,
            [word, next_word],
        )
    )

    assert count == 0
    assert repaired[0].end_ms == repaired[0].start_ms


def test_legacy_transcription_speech_onsets_normalize_to_subtitle_entries():
    state = VideoLocalizationTranscriptionState.model_validate(
        {"speech_onset_by_word_id": {"word_000001": 140}}
    )

    assert state.subtitle_entry_by_word_id == {"word_000001": 140}
    assert "speech_onset_by_word_id" not in state.model_dump(mode="json")


def test_align_segments_offsets_each_crop_and_preserves_monotonic_timing(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(48000), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "align_audio",
        lambda **kwargs: [
            {"text": "First", "start_time": 1.1, "end_time": 1.6},
            {"text": "Second", "start_time": 1.5, "end_time": 2.1},
        ],
    )
    segments = [
        _segment("asr_0001", "First.", 1000, 2000),
        _segment("asr_0002", "Second.", 1500, 2500),
    ]

    words, metadata = transcription.align_segments(audio_path, segments, language="en")

    assert metadata["status"] == "completed"
    assert words[0].start_ms == 1100
    assert words[0].end_ms == 1600
    assert words[1].start_ms == words[0].end_ms
    assert words[1].end_ms > words[1].start_ms
    assert words[0].timing_confidence == "high"
    assert words[1].timing_confidence == "medium"
    assert metadata["timing_confidence"] == "medium"
    assert "alignment_monotonic_repaired" in metadata["quality_flags"]


def test_align_segments_uses_continuous_window_instead_of_coarse_segment_crops(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(5000), 1000))
    written_lengths = []
    monkeypatch.setattr(
        transcription.audio_tools,
        "write_audio",
        lambda _path, audio, _sample_rate, **_kwargs: written_lengths.append(len(audio)),
    )
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})
    requests = []

    def align(**kwargs):
        requests.append(kwargs)
        return [
            {"text": "First", "start_time": 0.4, "end_time": 0.9},
            {"text": "Second", "start_time": 3.1, "end_time": 3.8},
        ]

    monkeypatch.setattr(transcription.qwen_forced_aligner, "align_audio", align)

    words, metadata = transcription.align_segments(
        audio_path,
        [
            _segment("asr_0001", "First.", 1000, 1800),
            _segment("asr_0002", "Second.", 2200, 3200),
        ],
        language="en",
    )

    assert len(requests) == 1
    assert written_lengths == [5000]
    assert [item.start_ms for item in words] == [400, 3100]
    assert metadata["status"] == "completed"


def test_strict_alignment_preserves_full_audio_offsets_and_leading_silence(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "full-dub.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        transcription.audio_tools,
        "read_audio",
        lambda _path: (np.zeros(70_000), 1_000),
    )
    monkeypatch.setattr(
        transcription.audio_tools,
        "write_audio",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "health_check",
        lambda: {"healthy": True},
    )
    calls = 0

    def align(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [
                {
                    "text": "你",
                    "start_time": 13.0,
                    "end_time": 14.0,
                },
                {
                    "text": "好",
                    "start_time": 16.0,
                    "end_time": 17.0,
                }
            ]
        return [
            {
                "text": "继",
                "start_time": 12.0,
                "end_time": 13.0,
            },
            {
                "text": "续",
                "start_time": 15.0,
                "end_time": 16.0,
            }
        ]

    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "align_audio",
        align,
    )

    words, metadata = transcription.align_segments_strict(
        audio_path,
        [
            _segment("chunk-1", "你好", 0, 30_000),
            _segment("chunk-2", "继续", 30_000, 60_000),
        ],
        language="zh",
        max_duration_ms=70_000,
    )

    assert [(item.start_ms, item.end_ms) for item in words] == [
        (13_000, 14_000),
        (16_000, 17_000),
        (42_000, 43_000),
        (45_000, 46_000),
    ]
    assert all(
        item.timing_source == "forced_aligner"
        for item in words
    )
    assert metadata["alignment_call_count"] == 2


def test_strict_alignment_preserves_zero_width_model_token_without_inventing_time(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "full-dub.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        transcription.audio_tools,
        "read_audio",
        lambda _path: (np.zeros(30_741), 1_000),
    )
    monkeypatch.setattr(
        transcription.audio_tools,
        "write_audio",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "health_check",
        lambda: {"healthy": True},
    )
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "align_audio",
        lambda **_kwargs: [
            {
                "text": "这",
                "start_time": 13.52,
                "end_time": 13.60,
            },
            {
                "text": "是",
                "start_time": 13.60,
                "end_time": 13.60,
            },
            {
                "text": "我",
                "start_time": 13.60,
                "end_time": 13.76,
            },
        ],
    )

    words, metadata = transcription.align_segments_strict(
        audio_path,
        [_segment("dub-asr-0001", "这是我", 0, 30_741)],
        language="zh",
        max_duration_ms=30_741,
    )

    assert [
        (item.text, item.start_ms, item.end_ms)
        for item in words
    ] == [
        ("这", 13_520, 13_600),
        ("是", 13_600, 13_600),
        ("我", 13_600, 13_760),
    ]
    assert metadata["alignment_call_count"] == 1


def test_delivery_alignment_coalesces_zero_width_tokens_without_inventing_internal_boundaries():
    words = [
        VideoLocalizationAlignedWord(
            word_id="word_000001",
            segment_id="segment-1",
            text="不",
            start_ms=100,
            end_ms=180,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word_000002",
            segment_id="segment-1",
            text="是",
            start_ms=180,
            end_ms=180,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word_000003",
            segment_id="segment-1",
            text="说",
            start_ms=180,
            end_ms=340,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word_000004",
            segment_id="segment-1",
            text="完",
            start_ms=420,
            end_ms=580,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
    ]

    coalesced, metadata = (
        transcription.coalesce_zero_width_alignment_words(words)
    )

    assert [item.text for item in coalesced] == ["不", "是说", "完"]
    assert [
        (item.start_ms, item.end_ms) for item in coalesced
    ] == [(100, 180), (180, 340), (420, 580)]
    assert metadata == {
        "coalesced_group_count": 1,
        "zero_width_token_count": 1,
    }


def test_delivery_alignment_rejects_segment_made_only_of_zero_width_tokens():
    words = [
        VideoLocalizationAlignedWord(
            word_id="word_000001",
            segment_id="segment-1",
            text="啊",
            start_ms=100,
            end_ms=100,
            timing_confidence="high",
            timing_source="forced_aligner",
        )
    ]

    with pytest.raises(
        RuntimeError,
        match="没有可用的正时长相邻声学单元",
    ):
        transcription.coalesce_zero_width_alignment_words(words)


def test_delivery_alignment_uses_left_neighbour_for_trailing_point_anchor():
    words = [
        VideoLocalizationAlignedWord(
            word_id="word_000001",
            segment_id="segment-1",
            text="好",
            start_ms=100,
            end_ms=260,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word_000002",
            segment_id="segment-1",
            text="啊",
            start_ms=260,
            end_ms=260,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
    ]

    coalesced, metadata = (
        transcription.coalesce_zero_width_alignment_words(words)
    )

    assert [item.text for item in coalesced] == ["好啊"]
    assert (coalesced[0].start_ms, coalesced[0].end_ms) == (100, 260)
    assert metadata["coalesced_group_count"] == 1


def test_delivery_alignment_keeps_point_punctuation_with_left_phrase():
    words = [
        VideoLocalizationAlignedWord(
            word_id="word_000001",
            segment_id="segment-1",
            text="好",
            start_ms=100,
            end_ms=260,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word_000002",
            segment_id="segment-1",
            text="的，",
            start_ms=260,
            end_ms=260,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        VideoLocalizationAlignedWord(
            word_id="word_000003",
            segment_id="segment-1",
            text="下",
            start_ms=420,
            end_ms=580,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
    ]

    coalesced, _metadata = (
        transcription.coalesce_zero_width_alignment_words(words)
    )

    assert [item.text for item in coalesced] == ["好的，", "下"]
    assert [
        (item.start_ms, item.end_ms) for item in coalesced
    ] == [(100, 260), (420, 580)]


def test_strict_alignment_clamps_final_token_quantization_to_audio_window(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "full-dub.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        transcription.audio_tools,
        "read_audio",
        lambda _path: (np.zeros(31_839), 1_000),
    )
    monkeypatch.setattr(
        transcription.audio_tools,
        "write_audio",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "health_check",
        lambda: {"healthy": True},
    )
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "align_audio",
        lambda **_kwargs: [
            {
                "text": "吹",
                "start_time": 31.36,
                "end_time": 31.52,
            },
            {
                "text": "落",
                "start_time": 31.52,
                "end_time": 31.84,
            },
        ],
    )

    words, metadata = transcription.align_segments_strict(
        audio_path,
        [_segment("dub-asr-0059", "吹落", 0, 31_839)],
        language="zh",
        max_duration_ms=31_839,
    )

    assert [(item.start_ms, item.end_ms) for item in words] == [
        (31_360, 31_520),
        (31_520, 31_839),
    ]
    assert metadata["alignment_call_count"] == 1


def test_strict_alignment_rejects_token_mismatch_without_interpolation(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "full-dub.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        transcription.audio_tools,
        "read_audio",
        lambda _path: (np.zeros(30_000), 1_000),
    )
    monkeypatch.setattr(
        transcription.audio_tools,
        "write_audio",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "health_check",
        lambda: {"healthy": True},
    )
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "align_audio",
        lambda **_kwargs: [
            {
                "text": "错",
                "start_time": 1.0,
                "end_time": 2.0,
            }
        ],
    )

    with pytest.raises(
        RuntimeError,
        match="声学字词与校对文字不一致",
    ):
        transcription.align_segments_strict(
            audio_path,
            [_segment("chunk-1", "对", 0, 30_000)],
            language="zh",
            max_duration_ms=30_000,
        )


def test_strict_alignment_accepts_exact_latin_token_join_without_inventing_boundary():
    segment = _segment(
        "dub-asr-0074",
        "资本涌入 Anthropic、OpenAI 等模型",
        0,
        30_000,
    )
    expected = transcription.display_tokens(segment.raw_text)
    aligned_items = [
        {"text": "资", "start_time": 0.1, "end_time": 0.2},
        {"text": "本", "start_time": 0.2, "end_time": 0.3},
        {"text": "涌", "start_time": 0.3, "end_time": 0.4},
        {"text": "入", "start_time": 0.4, "end_time": 0.5},
        {
            "text": "AnthropicOpenAI",
            "start_time": 0.5,
            "end_time": 1.2,
        },
        {"text": "等", "start_time": 1.2, "end_time": 1.3},
        {"text": "模", "start_time": 1.3, "end_time": 1.4},
        {"text": "型", "start_time": 1.4, "end_time": 1.5},
    ]

    words = transcription._strict_aligned_words(
        segment,
        aligned_items,
        offset=0,
        window_start_ms=0,
        window_end_ms=30_000,
    )

    assert [item.text for item in words] == expected
    latin_words = [
        item for item in words if "Anthropic" in item.text or "OpenAI" in item.text
    ]
    assert len(latin_words) == 2
    assert {
        (item.start_ms, item.end_ms) for item in latin_words
    } == {(500, 1_200)}
    assert all(
        item.timing_source == "forced_aligner" for item in words
    )


def test_align_segments_splits_mismatched_window_and_recovers_smaller_groups(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(5000), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})
    requests = []

    def align(**kwargs):
        requests.append(kwargs["transcript_text"])
        if kwargs["transcript_text"] == "First. Second.":
            return [
                {"text": "Wrong", "start_time": 0.4, "end_time": 0.9},
                {"text": "Tokens", "start_time": 2.1, "end_time": 2.7},
            ]
        token = "First" if kwargs["transcript_text"] == "First." else "Second"
        return [{"text": token, "start_time": 1.0, "end_time": 1.5}]

    monkeypatch.setattr(transcription.qwen_forced_aligner, "align_audio", align)

    words, metadata = transcription.align_segments(
        audio_path,
        [
            _segment("asr_0001", "First.", 1000, 1800),
            _segment("asr_0002", "Second.", 2200, 3200),
        ],
        language="en",
    )

    assert requests == ["First. Second.", "First.", "Second."]
    assert [word.text for word in words] == ["First.", "Second."]
    assert all(word.timing_source == "forced_aligner" for word in words)
    assert metadata["status"] == "completed"
    assert "alignment_window_split_recovered" in metadata["quality_flags"]


def test_align_segments_does_not_claim_globally_scaled_leaf_timing_as_acoustic_truth(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(3000), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})

    def align(**kwargs):
        if kwargs["transcript_text"] == "First. Two three four five.":
            return [
                {"text": token, "start_time": index * 0.2, "end_time": index * 0.2 + 0.1}
                for index, token in enumerate(("Wrong", "token", "sequence", "right", "size"))
            ]
        if kwargs["transcript_text"] == "First.":
            return [{"text": "First", "start_time": 0.1, "end_time": 0.4}]
        return [
            {"text": "Two", "start_time": 1.2, "end_time": 1.4},
            {"text": "three", "start_time": 1.6, "end_time": 1.8},
            {"text": "four", "start_time": 2.2, "end_time": 2.2},
            {"text": "five", "start_time": 2.2, "end_time": 2.2},
        ]

    monkeypatch.setattr(transcription.qwen_forced_aligner, "align_audio", align)

    words, metadata = transcription.align_segments(
        audio_path,
        [
            _segment("asr_0001", "First.", 0, 800),
            _segment("asr_0002", "Two three four five.", 1000, 2000),
        ],
        language="en",
    )

    second = [word for word in words if word.segment_id == "asr_0002"]
    assert [word.text for word in second] == ["Two", "three", "four", "five."]
    assert second[0].start_ms == 1000
    assert second[-1].end_ms == 2000
    assert all(right.start_ms >= left.end_ms for left, right in zip(second, second[1:]))
    assert all(word.timing_source == "asr_segment_interpolation" for word in second)
    assert all(word.timing_confidence == "low" for word in second)
    assert metadata["status"] == "partial"
    assert "alignment_leaf_interpolated" in metadata["quality_flags"]
    assert "alignment_boundary_scaled" not in metadata["quality_flags"]


def test_alignment_window_never_extends_past_real_audio_duration():
    windows = transcription._alignment_windows(
        [
            _segment("asr_0001", "First.", 0, 2000),
            _segment("asr_0002", "Overshoot.", 8000, 9000),
        ],
        audio_duration_ms=5000,
    )

    assert len(windows) == 1
    assert windows[0][1] == 0
    assert windows[0][2] == 5000


def test_align_segments_respects_media_duration_when_audio_container_is_longer(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(24_811), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "align_audio",
        lambda **kwargs: [
            {"text": "My", "start_time": 24.504, "end_time": 24.664},
            {"text": "real", "start_time": 24.664, "end_time": 24.811},
        ],
    )

    words, _metadata = transcription.align_segments(
        audio_path,
        [_segment("asr_0001", "My real", 24_234, 24_791)],
        language="en",
        max_duration_ms=24_791,
    )

    assert words
    assert max(word.end_ms for word in words) <= 24_791
    assert words[0].timing_confidence == "high"
    assert words[-1].timing_confidence == "medium"


def test_align_segments_repairs_zero_duration_tokens_without_claiming_high_precision(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(1200), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "align_audio",
        lambda **kwargs: [
            {"text": "Hi", "start_time": 0.0, "end_time": 0.0},
            {"text": "I'm", "start_time": 0.0, "end_time": 0.16},
            {"text": "Adil", "start_time": 0.16, "end_time": 0.16},
        ],
    )

    words, metadata = transcription.align_segments(
        audio_path,
        [_segment("asr_0001", "Hi, I'm Adil.", 0, 962)],
        language="en",
    )

    assert [item.text for item in words] == ["Hi,", "I'm", "Adil."]
    assert all(item.end_ms > item.start_ms for item in words)
    assert all(right.start_ms >= left.end_ms for left, right in zip(words, words[1:]))
    assert all(item.timing_confidence == "medium" for item in words)
    assert metadata["status"] == "completed"
    assert metadata["timing_confidence"] == "medium"
    assert "alignment_timing_adjusted" in metadata["quality_flags"]
    assert "alignment_zero_duration_repaired" in metadata["quality_flags"]
    assert "alignment_shared_anchor_repaired" in metadata["quality_flags"]


def test_align_segments_recovers_failed_leaf_with_neighbor_context(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(5000), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})

    full_context_calls = 0

    def align(**kwargs):
        nonlocal full_context_calls
        text = kwargs["transcript_text"]
        if text == "Before. Target words. After.":
            full_context_calls += 1
            if full_context_calls == 1:
                return [{"text": "mismatch", "start_time": 0.0, "end_time": 0.1}]
            tokens = ("Before", "Target", "words", "After")
            return [
                {"text": token, "start_time": 0.4 + index * 0.7, "end_time": 0.8 + index * 0.7}
                for index, token in enumerate(tokens)
            ]
        if text == "Before.":
            return [{"text": "Before", "start_time": 0.4, "end_time": 0.8}]
        if text == "Target words. After.":
            return [{"text": "mismatch", "start_time": 0.0, "end_time": 0.1}]
        if text == "Target words.":
            return [{"text": "mismatch", "start_time": 0.0, "end_time": 0.1}]
        return [{"text": "After", "start_time": 0.4, "end_time": 0.8}]

    monkeypatch.setattr(transcription.qwen_forced_aligner, "align_audio", align)

    words, metadata = transcription.align_segments(
        audio_path,
        [
            _segment("asr_0001", "Before.", 0, 1000),
            _segment("asr_0002", "Target words.", 1000, 2500),
            _segment("asr_0003", "After.", 2500, 3500),
        ],
        language="en",
    )

    target_words = [word for word in words if word.segment_id == "asr_0002"]
    assert [word.text for word in target_words] == ["Target", "words."]
    assert all(word.timing_source == "forced_aligner" for word in target_words)
    assert metadata["status"] == "completed"
    assert "alignment_context_recovered" in metadata["quality_flags"]
    assert "alignment_leaf_interpolated" not in metadata["quality_flags"]


def test_aligned_words_caps_zero_duration_anchor_before_long_silence():
    flags: set[str] = set()

    words = transcription._aligned_window_words(
        [_segment("asr_0001", "One two.", 0, 6000)],
        [
            {"text": "One", "start_time": 0.0, "end_time": 0.0},
            {"text": "two", "start_time": 5.0, "end_time": 5.2},
        ],
        offset=0,
        window_start_ms=0,
        window_end_ms=6000,
        quality_flags=flags,
    )

    assert [word.text for word in words] == ["One", "two."]
    assert words[0].start_ms == 0
    assert words[0].end_ms == transcription.ZERO_DURATION_MAX_TOKEN_MS
    assert words[1].start_ms == 5000
    assert "alignment_zero_duration_repaired" in flags
    assert "alignment_zero_duration_capped" in flags


def test_alignment_transcript_separates_attached_em_dash_tokens():
    assert (
        transcription._alignment_transcript_text("the full cinematic showcase—the stuff")
        == "the full cinematic showcase— the stuff"
    )


def test_repair_collapsed_word_run_uses_following_in_segment_gap():
    flags: set[str] = set()
    words = [
        VideoLocalizationAlignedWord(
            word_id=f"word_{index:04d}",
            segment_id="asr_0001" if index < 5 else "asr_0002",
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            timing_confidence="medium",
            timing_source="forced_aligner",
        )
        for index, (text, start_ms, end_ms) in enumerate(
            [
                ("before", 0, 400),
                ("That's", 400, 401),
                ("months", 401, 402),
                ("of", 402, 403),
                ("work", 403, 404),
                ("and", 404, 405),
                ("a", 405, 406),
                ("massive", 406, 450),
                ("budget", 450, 700),
                ("next", 2400, 2700),
            ]
        )
    ]

    repaired = transcription._repair_collapsed_word_runs(
        words,
        segments=[
            _segment("asr_0001", "Before. That's months of work.", 0, 1200),
            _segment("asr_0002", "And a massive budget next.", 1200, 3000),
        ],
        quality_flags=flags,
    )

    assert repaired[1].start_ms == 400
    assert repaired[8].end_ms == 2400
    assert all(word.timing_source == "asr_segment_interpolation" for word in repaired[1:9])
    assert all(word.timing_confidence == "low" for word in repaired[1:9])
    assert "alignment_collapsed_run_repaired" in flags


def test_normalize_aligned_word_times_repairs_collapse_created_by_monotonic_clamp():
    segment = _segment("asr_0001", "Before here it is stable after.")
    segment.end_ms = 2_000
    words = [
        VideoLocalizationAlignedWord(
            word_id="word_000001",
            segment_id=segment.segment_id,
            text="Before",
            start_ms=0,
            end_ms=100,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
        *[
            VideoLocalizationAlignedWord(
                word_id=f"word_{index + 2:06d}",
                segment_id=segment.segment_id,
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                timing_confidence="high",
                timing_source="forced_aligner",
            )
            for index, (text, start_ms, end_ms) in enumerate(
                [
                    ("here", 50, 80),
                    ("it", 60, 90),
                    ("is", 70, 95),
                ]
            )
        ],
        VideoLocalizationAlignedWord(
            word_id="word_000005",
            segment_id=segment.segment_id,
            text="stable",
            start_ms=1_000,
            end_ms=1_200,
            timing_confidence="high",
            timing_source="forced_aligner",
        ),
    ]
    flags: set[str] = set()

    normalized = transcription._normalize_aligned_word_times(
        words,
        segments=[segment],
        quality_flags=flags,
    )

    repaired = normalized[1:4]
    assert all(word.end_ms - word.start_ms > 1 for word in repaired)
    assert repaired[0].start_ms == 100
    assert repaired[-1].end_ms == 1_000
    assert "alignment_collapsed_run_repaired" in flags
    assert "timing_review_required" in flags


def test_aligned_window_words_rejects_token_count_mismatch():
    words = transcription._aligned_window_words(
        [_segment("asr_0001", "One two three.", 0, 1000)],
        [{"text": "One", "start_time": 0.0, "end_time": 0.2}],
        offset=0,
        window_start_ms=0,
        window_end_ms=1000,
    )

    assert words == []


def test_aligned_window_words_reconciles_decimal_tokenizer_boundary():
    flags: set[str] = set()

    words = transcription._aligned_window_words(
        [_segment("asr_0001", "controlled Cines2.5 videos", 0, 1000)],
        [
            {"text": "controlled", "start_time": 0.0, "end_time": 0.2},
            {"text": "Cines2.5", "start_time": 0.25, "end_time": 0.55},
            {"text": "videos", "start_time": 0.6, "end_time": 0.8},
        ],
        offset=0,
        window_start_ms=0,
        window_end_ms=1000,
        quality_flags=flags,
    )

    assert [word.text for word in words] == [
        "controlled",
        "Cines2.",
        "5",
        "videos",
    ]
    assert words[1].start_ms == 250
    assert words[2].end_ms == 550
    assert all(word.timing_source == "forced_aligner" for word in words)
    assert all(word.timing_confidence == "medium" for word in words[1:3])
    assert "alignment_token_boundary_reconciled" in flags
    assert "alignment_token_count_mismatch" not in flags


def test_aligned_window_words_reconciles_domain_tokenizer_boundary():
    flags: set[str] = set()

    words = transcription._aligned_window_words(
        [_segment("asr_0002", "go to blender.org now", 0, 1000)],
        [
            {"text": "go", "start_time": 0.0, "end_time": 0.15},
            {"text": "to", "start_time": 0.2, "end_time": 0.3},
            {"text": "blender.org", "start_time": 0.35, "end_time": 0.65},
            {"text": "now", "start_time": 0.7, "end_time": 0.9},
        ],
        offset=0,
        window_start_ms=0,
        window_end_ms=1000,
        quality_flags=flags,
    )

    assert [word.text for word in words] == ["go", "to", "blender.", "org", "now"]
    assert words[2].start_ms == 350
    assert words[3].end_ms == 650
    assert "alignment_token_boundary_reconciled" in flags


def test_aligned_window_words_accepts_repeated_tokens_without_losing_order():
    words = transcription._aligned_window_words(
        [_segment("asr_0001", "Go go now.", 0, 1000)],
        [
            {"text": "go", "start_time": 0.0, "end_time": 0.2},
            {"text": "GO", "start_time": 0.25, "end_time": 0.45},
            {"text": "now", "start_time": 0.5, "end_time": 0.8},
        ],
        offset=0,
        window_start_ms=0,
        window_end_ms=1000,
    )

    assert [word.text for word in words] == ["Go", "go", "now."]
    assert [word.start_ms for word in words] == [0, 250, 500]
    assert all(word.timing_confidence == "high" for word in words)


def test_aligned_words_accepts_close_spelling_correction_at_medium_confidence():
    flags: set[str] = set()
    words = transcription._aligned_window_words(
        [_segment("asr_0001", "Artists sculpting every scale.", 0, 1200)],
        [
            {"text": "Artists", "start_time": 0.0, "end_time": 0.2},
            {"text": "sculpturing", "start_time": 0.25, "end_time": 0.55},
            {"text": "every", "start_time": 0.6, "end_time": 0.8},
            {"text": "scale", "start_time": 0.85, "end_time": 1.1},
        ],
        offset=0,
        window_start_ms=0,
        window_end_ms=1200,
        quality_flags=flags,
    )

    assert [word.text for word in words] == ["Artists", "sculpting", "every", "scale."]
    assert words[1].timing_confidence == "medium"
    assert "alignment_fuzzy_token_match" in flags


def test_align_segments_rejects_same_count_wrong_or_reordered_tokens(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(1000), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})

    for aligned_tokens in [("One", "wrong", "three"), ("two", "One", "three")]:
        monkeypatch.setattr(
            transcription.qwen_forced_aligner,
            "align_audio",
            lambda **kwargs: [
                {"text": token, "start_time": index * 0.25, "end_time": index * 0.25 + 0.2}
                for index, token in enumerate(aligned_tokens)
            ],
        )

        words, metadata = transcription.align_segments(
            audio_path,
            [_segment("asr_0001", "One two three.", 0, 1000)],
            language="en",
        )

        assert [word.text for word in words] == ["One", "two", "three."]
        assert all(word.timing_source == "asr_segment_interpolation" for word in words)
        assert all(word.timing_confidence == "low" for word in words)
        assert metadata["status"] == "failed"
        assert "alignment_token_mismatch" in metadata["quality_flags"]


def test_align_segments_clips_to_media_boundary_and_downgrades_confidence(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(1000), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})
    monkeypatch.setattr(
        transcription.qwen_forced_aligner,
        "align_audio",
        lambda **kwargs: [{"text": "Boundary", "start_time": -0.1, "end_time": 1.4}],
    )

    words, metadata = transcription.align_segments(
        audio_path,
        [_segment("asr_0001", "Boundary.", 0, 1400)],
        language="en",
    )

    assert [(word.start_ms, word.end_ms) for word in words] == [(0, 1000)]
    assert words[0].timing_confidence == "medium"
    assert metadata["timing_confidence"] == "medium"
    assert "alignment_boundary_clipped" in metadata["quality_flags"]
    assert "alignment_timing_adjusted" in metadata["quality_flags"]


def test_align_segments_falls_back_to_low_confidence_interpolation(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(1000), 1000))
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": False})

    words, metadata = transcription.align_segments(
        audio_path,
        [_segment("asr_0001", "One two three.", 0, 1200)],
        language="en",
    )

    assert metadata["status"] == "failed"
    assert metadata["timing_confidence"] == "low"
    assert [item.text for item in words] == ["One", "two", "three."]
    assert all(item.timing_source == "asr_segment_interpolation" for item in words)


def test_align_segments_retries_once_then_opens_circuit(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(2500), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})
    calls = []

    def fail_alignment(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("worker unavailable")

    monkeypatch.setattr(transcription.qwen_forced_aligner, "align_audio", fail_alignment)

    words, metadata = transcription.align_segments(
        audio_path,
        [
            _segment("asr_0001", "First segment.", 0, 1000),
            _segment("asr_0002", "Second segment.", 1200, 2200),
        ],
        language="en",
    )

    assert len(calls) == 2
    assert metadata["status"] == "failed"
    assert "alignment_runtime_retried" in metadata["quality_flags"]
    assert "alignment_runtime_circuit_open" in metadata["quality_flags"]
    assert all(item.timing_source == "asr_segment_interpolation" for item in words)


def test_align_segments_recovers_when_runtime_retry_succeeds(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"placeholder")
    monkeypatch.setattr(transcription.audio_tools, "read_audio", lambda path: (np.zeros(1400), 1000))
    monkeypatch.setattr(transcription.audio_tools, "write_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(transcription.qwen_forced_aligner, "health_check", lambda: {"healthy": True})
    calls = []

    def recover_alignment(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("temporary worker restart")
        return [
            {"text": "Recovered", "start_time": 0.0, "end_time": 0.45},
            {"text": "segment.", "start_time": 0.46, "end_time": 0.95},
        ]

    monkeypatch.setattr(transcription.qwen_forced_aligner, "align_audio", recover_alignment)

    words, metadata = transcription.align_segments(
        audio_path,
        [_segment("asr_0001", "Recovered segment.", 0, 1000)],
        language="en",
    )

    assert len(calls) == 2
    assert metadata["status"] == "completed"
    assert "alignment_runtime_retried" in metadata["quality_flags"]
    assert "alignment_runtime_recovered" in metadata["quality_flags"]
    assert "alignment_runtime_circuit_open" not in metadata["quality_flags"]
    assert all(item.timing_source == "forced_aligner" for item in words)


def test_display_tokens_keep_decimal_version_as_one_alignment_token():
    assert transcription._display_tokens("Seedance 2.0 in 4K.") == ["Seedance", "2.0", "in", "4K."]


def test_display_tokens_attach_leading_punctuation_to_first_spoken_token():
    assert transcription._display_tokens('" It hands me this.') == ['"It', "hands", "me", "this."]
    assert transcription._normalize_alignment_token('"It') == "it"


def test_display_tokens_preserve_japanese_kana_during_alignment_fallback():
    text = "終わったぜ。全員仕留めたな。"

    tokens = transcription.display_tokens(text)

    assert "".join(tokens) == text
    assert any("わ" in token for token in tokens)


def test_transcript_quality_operations_preserve_exact_unresolved_excerpt():
    source = _segment(
        "asr_0001",
        "Normal speech. Fartifact's no smell. Continue safely.",
    )

    result = transcription._record_final_transcript_quality_operations(
        [source],
        [source.model_copy(update={"review_flags": ["asr_unresolved_text"]})],
        [],
        [
            {
                "code": "needs_confirmation",
                "segment_id": "asr_0001",
                "excerpt": "Fartifact's no smell.",
                "message": "现有证据不足以安全改写。",
            }
        ],
    )

    assert len(result[0].review_operations) == 1
    operation = result[0].review_operations[0]
    assert operation.status == "rejected"
    assert operation.source_text == "Fartifact's no smell."
    assert operation.start_word_id == "source_word_000003"
    assert operation.end_word_id == "source_word_000005"
