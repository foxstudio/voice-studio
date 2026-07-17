from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import transcription  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationBoundaryReview,
    VideoLocalizationGlossaryEntry,
    VideoLocalizationTranscriptSegment,
)
from app.models.schemas import LlmProviderListResponse, LlmProviderProfile  # noqa: E402


def _segment(segment_id: str, text: str, start_ms: int = 0, end_ms: int = 1000):
    return VideoLocalizationTranscriptSegment(
        segment_id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        raw_text=text,
    )


def _configure_llm(monkeypatch):
    profile = LlmProviderProfile(
        profile_id="review",
        name="Review",
        base_url="https://llm.example.com/v1",
        model_id="review-model",
        enabled=True,
        api_key_configured=True,
    )
    monkeypatch.setattr(
        transcription.settings_store,
        "llm_profiles",
        lambda: LlmProviderListResponse(profiles=[profile], default_profile_id=profile.profile_id),
    )
    monkeypatch.setattr(transcription.settings_store, "llm_profile", lambda profile_id: profile)
    return profile


def test_transcribe_and_process_returns_state_and_reports_stage_progress(monkeypatch, tmp_path):
    audio_path = tmp_path / "source.wav"
    alignment_audio_path = tmp_path / "original.wav"
    audio_path.write_bytes(b"audio")
    alignment_audio_path.write_bytes(b"original")
    progress = []
    alignment_inputs = []
    boundary_inputs = []
    boundary_review_kwargs = {}

    monkeypatch.setattr(
        transcription.asr_service,
        "transcribe",
        lambda **_kwargs: {"segments": [], "text": "Hello world."},
    )
    monkeypatch.setattr(transcription.asr_service, "normalize_segments", lambda _items: [])
    monkeypatch.setattr(
        transcription,
        "review_segments",
        lambda segments, **_kwargs: (
            segments,
            {
                "status": "completed",
                "batch_count": 1,
                "profile_id": "review-profile",
                "model_id": "review-model",
                "batches": [
                    {
                        "batch": 1,
                        "item_count": 1,
                        "duration_ms": 11,
                        "status": "success",
                        "attempt_count": 1,
                    }
                ],
                "quality_flags": [],
            },
        ),
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
    )

    assert result.raw_text == "Hello world."
    assert result.source_track_id == "vocals"
    assert result.source_audio_sha256 == "cached-source"
    assert result.alignment_source_track_id == "original"
    assert result.alignment_audio_sha256 == "cached-alignment"
    assert boundary_review_kwargs["existing_reviews"] == [existing_review]
    assert alignment_inputs == [alignment_audio_path]
    assert boundary_inputs == [audio_path]
    assert [value for value, _stage in progress] == [0.15, 0.30, 0.40, 0.58, 0.74, 0.82, 0.96]
    assert progress[-1][1] == "正在生成字幕轨"
    assert result.pipeline_timing["total_duration_ms"] >= 0
    assert set(result.pipeline_timing["stages"]) == {
        "asr",
        "web_research",
        "text_review",
        "alignment",
        "audio_boundaries",
        "boundary_review",
    }
    assert result.pipeline_timing["stages"]["text_review"]["batches"][0]["duration_ms"] == 11
    assert result.pipeline_timing["stages"]["text_review"]["model_id"] == "review-model"
    assert result.pipeline_timing["stages"]["boundary_review"]["rounds"][0]["batches"][0]["duration_ms"] == 13
    assert result.pipeline_timing["stages"]["boundary_review"]["model_id"] == "review-model"
    assert result.model_dump()["pipeline_timing"] == result.pipeline_timing


@pytest.mark.parametrize(
    ("segment_language", "text", "expected"),
    [
        ("English", "Hello world", "en"),
        ("Chinese", "你好世界", "zh"),
        (None, "这是自动检测", "zh"),
        (None, "Automatic detection", "en"),
    ],
)
def test_auto_source_language_resolves_from_asr_output(segment_language, text, expected):
    class Segment:
        language = segment_language

    assert transcription._resolve_transcript_language("auto", [Segment()], text) == expected


def test_auto_source_language_tie_uses_first_detected_segment():
    class Segment:
        def __init__(self, language: str):
            self.language = language

    segments = [Segment("English"), Segment("Chinese")]

    assert transcription._resolve_transcript_language("auto", segments, "Hello 你好") == "en"


def test_review_segments_applies_conservative_correction(monkeypatch):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(
        transcription,
        "REVIEW_BATCH_SIZE",
        40,
    )
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [
                        {
                            "start_word_id": "source_word_000002",
                            "end_word_id": "source_word_000002",
                            "replacement_text": "shipped",
                            "reason": "tense correction",
                            "confidence": 0.96,
                        }
                    ],
                    "confidence": 0.96,
                    "issues": ["near-homophone"],
                }
            ]
        },
    )

    reviewed, metadata = transcription.review_segments(
        [_segment("asr_0001", "We ship the localization pass.")],
        language="en",
    )

    assert metadata["status"] == "completed"
    assert reviewed[0].corrected_text == "We shipped the localization pass."
    assert "llm_corrected" in reviewed[0].review_flags
    assert reviewed[0].raw_text == "We ship the localization pass."
    assert reviewed[0].review_operations[0].status == "accepted"
    assert reviewed[0].review_operations[0].start_word_id == "source_word_000002"


def test_review_segments_accepts_provider_top_level_array(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: [
            {
                "segment_id": "asr_0001",
                "edits": [
                    {
                        "start_word_id": "source_word_000001",
                        "end_word_id": "source_word_000001",
                        "replacement_text": "Corrected",
                        "reason": "word form",
                        "confidence": 0.95,
                    }
                ],
                "confidence": 0.95,
                "issues": [],
            }
        ],
    )

    reviewed, metadata = transcription.review_segments(
        [_segment("asr_0001", "Correct sentence.")],
        language="en",
    )

    assert metadata["status"] == "completed"
    assert reviewed[0].corrected_text == "Corrected sentence."


def test_review_segments_sends_glossary_and_stable_source_word_ids(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    captured = {}

    def complete(**kwargs):
        captured.update(kwargs)
        return {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [],
                    "confidence": 0.9,
                    "issues": [],
                }
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete)

    transcription.review_segments(
        [_segment("asr_0001", "Built with Cinean 2.0.")],
        language="en",
        scene_context="源视频标题：Seedance 2.0 VFX workflow",
        glossary=[
            VideoLocalizationGlossaryEntry(
                source_text="Cinean",
                corrected_source_text="Seedance",
                zh_text="即梦 Seedance",
                notes="产品型号按官方写法",
            )
        ],
    )

    payload = captured["user_payload"]
    assert payload["segments"][0]["words"][0] == {"word_id": "source_word_000001", "text": "Built"}
    assert payload["scene_context"] == "源视频标题：Seedance 2.0 VFX workflow"
    assert payload["glossary"][0]["corrected_source_text"] == "Seedance"
    assert "timestamps" in payload["rules"]["forbidden"]


def test_review_segments_rejects_empty_candidate_without_failing_the_batch(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [
                        {
                            "start_word_id": "source_word_000001",
                            "end_word_id": "source_word_000001",
                            "replacement_text": "",
                            "reason": "uncertain deletion",
                            "confidence": 0.2,
                        }
                    ],
                    "confidence": 0.2,
                    "issues": ["uncertain"],
                }
            ]
        },
    )

    reviewed, metadata = transcription.review_segments(
        [_segment("asr_0001", "Keep the original sentence.")],
        language="en",
    )

    assert metadata["status"] == "completed"
    assert reviewed[0].corrected_text == "Keep the original sentence."
    assert reviewed[0].review_candidate_text == "Keep the original sentence."
    assert reviewed[0].review_rejection_reason == "llm_review_rejected:content_deletion"
    assert reviewed[0].review_operations[0].status == "rejected"
    assert "llm_review_rejected_changes" in metadata["quality_flags"]


def test_review_segments_accepts_named_confidence_levels(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [],
                    "confidence": "high",
                    "issues": [],
                }
            ]
        },
    )

    reviewed, metadata = transcription.review_segments(
        [_segment("asr_0001", "Keep this sentence.")],
        language="en",
    )

    assert metadata["status"] == "completed"
    assert reviewed[0].review_confidence == 0.9


def test_review_segments_rejects_number_and_negation_changes(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [
                        {
                            "start_word_id": "source_word_000003",
                            "end_word_id": "source_word_000003",
                            "replacement_text": "12",
                            "reason": "number",
                            "confidence": 0.9,
                        }
                    ],
                    "confidence": 0.9,
                    "issues": [],
                },
                {
                    "segment_id": "asr_0002",
                    "edits": [
                        {
                            "start_word_id": "source_word_000006",
                            "end_word_id": "source_word_000006",
                            "replacement_text": "can",
                            "reason": "negation",
                            "confidence": 0.9,
                        }
                    ],
                    "confidence": 0.9,
                    "issues": [],
                },
            ]
        },
    )
    source = [
        _segment("asr_0001", "We shipped 10 files."),
        _segment("asr_0002", "I cannot approve this.", 1000, 2000),
    ]

    reviewed, metadata = transcription.review_segments(source, language="en")

    assert metadata["status"] == "completed"
    assert [item.corrected_text for item in reviewed] == [item.raw_text for item in source]
    assert "llm_review_rejected:numbers_changed" in reviewed[0].review_flags
    assert "llm_review_rejected:negation_changed" in reviewed[1].review_flags
    assert reviewed[0].review_candidate_text == "We shipped 12 files."
    assert reviewed[0].review_rejection_reason == "llm_review_rejected:numbers_changed"
    assert "llm_review_rejected_changes" in metadata["quality_flags"]


def test_review_segments_accepts_explicit_decimal_spacing_cleanup(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [
                        {
                            "start_word_id": "source_word_000003",
                            "end_word_id": "source_word_000003",
                            "replacement_text": "Seedance",
                            "reason": "proper noun",
                            "confidence": 0.96,
                        },
                        {
                            "start_word_id": "source_word_000004",
                            "end_word_id": "source_word_000005",
                            "replacement_text": "2.0",
                            "reason": "decimal spacing",
                            "confidence": 0.99,
                        },
                    ],
                    "confidence": 0.96,
                    "issues": ["proper-noun", "number-format"],
                }
            ]
        },
    )

    reviewed, metadata = transcription.review_segments(
        [_segment("asr_0001", "Generated with Cinean 2. 0 in 4K.")],
        language="en",
    )

    assert metadata["status"] == "completed"
    assert reviewed[0].corrected_text == "Generated with Seedance 2.0 in 4K."
    assert reviewed[0].review_rejection_reason is None


def test_review_segments_accepts_cross_segment_decimal_repair_when_batch_numbers_are_preserved(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [
                        {
                            "start_word_id": "source_word_000002",
                            "end_word_id": "source_word_000002",
                            "replacement_text": "2.0",
                            "reason": "decimal split across ASR segments",
                            "confidence": 0.98,
                        }
                    ],
                    "confidence": 0.98,
                    "issues": ["number-format"],
                },
                {
                    "segment_id": "asr_0002",
                    "edits": [
                        {
                            "start_word_id": "source_word_000003",
                            "end_word_id": "source_word_000003",
                            "replacement_text": "",
                            "reason": "moved to previous segment",
                            "confidence": 0.98,
                        }
                    ],
                    "confidence": 0.98,
                    "issues": ["number-format"],
                },
            ]
        },
    )
    source = [
        _segment("asr_0001", "Seedance 2.", 0, 800),
        _segment("asr_0002", "0 in 4K.", 800, 1600),
    ]

    reviewed, metadata = transcription.review_segments(source, language="en")

    assert metadata["status"] == "completed"
    assert [item.corrected_text for item in reviewed] == ["Seedance 2.0", "in 4K."]
    assert all(operation.status == "accepted" for item in reviewed for operation in item.review_operations)
    assert "llm_review_rejected_changes" not in metadata["quality_flags"]


def test_review_segments_rejects_negation_moved_between_segments(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [
                        {
                            "start_word_id": "source_word_000003",
                            "end_word_id": "source_word_000003",
                            "replacement_text": "really",
                            "reason": "move negation",
                            "confidence": 0.99,
                        }
                    ],
                    "confidence": 0.99,
                    "issues": [],
                },
                {
                    "segment_id": "asr_0002",
                    "edits": [
                        {
                            "start_word_id": "source_word_000006",
                            "end_word_id": "source_word_000006",
                            "replacement_text": "can not",
                            "reason": "move negation",
                            "confidence": 0.99,
                        }
                    ],
                    "confidence": 0.99,
                    "issues": [],
                },
            ]
        },
    )
    source = [
        _segment("asr_0001", "I do not agree.", 0, 800),
        _segment("asr_0002", "We can continue.", 800, 1600),
    ]

    reviewed, metadata = transcription.review_segments(source, language="en")

    assert [item.corrected_text for item in reviewed] == [item.raw_text for item in source]
    assert all(item.review_rejection_reason == "llm_review_rejected:negation_changed" for item in reviewed)
    assert "llm_review_rejected_changes" in metadata["quality_flags"]


def test_review_segments_rejects_number_moved_between_segments(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [
                        {
                            "start_word_id": "source_word_000002",
                            "end_word_id": "source_word_000002",
                            "replacement_text": "two",
                            "reason": "move number",
                            "confidence": 0.99,
                        }
                    ],
                    "confidence": 0.99,
                    "issues": [],
                },
                {
                    "segment_id": "asr_0002",
                    "edits": [
                        {
                            "start_word_id": "source_word_000004",
                            "end_word_id": "source_word_000004",
                            "replacement_text": "Version 2",
                            "reason": "move number",
                            "confidence": 0.99,
                        }
                    ],
                    "confidence": 0.99,
                    "issues": [],
                },
            ]
        },
    )
    source = [
        _segment("asr_0001", "Version 2 ships.", 0, 800),
        _segment("asr_0002", "Version 3 stays.", 800, 1600),
    ]

    reviewed, metadata = transcription.review_segments(source, language="en")

    assert [item.corrected_text for item in reviewed] == [item.raw_text for item in source]
    assert all(item.review_rejection_reason == "llm_review_rejected:numbers_changed" for item in reviewed)
    assert "llm_review_rejected_changes" in metadata["quality_flags"]


def test_review_segments_rejects_missing_or_reordered_ids(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **kwargs: {
            "segments": [
                {
                    "segment_id": "wrong",
                    "edits": [],
                    "confidence": 0.9,
                    "issues": [],
                }
            ]
        },
    )
    source = [_segment("asr_0001", "Original text.")]

    reviewed, metadata = transcription.review_segments(source, language="en")

    assert metadata["status"] == "failed"
    assert reviewed[0].corrected_text == "Original text."
    assert "llm_review_failed" in reviewed[0].review_flags
    assert metadata["batches"][0]["status"] == "failed"
    assert metadata["batches"][0]["item_count"] == 1
    assert metadata["batches"][0]["attempt_count"] == transcription.REVIEW_MAX_ATTEMPTS
    assert metadata["batches"][0]["duration_ms"] >= 0


def test_review_segments_retries_once_after_invalid_json(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    calls = []

    def flaky_completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise llm_runtime.LlmRuntimeError("无效 JSON", code="llm_json_invalid", status_code=502)
        return {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [],
                    "confidence": 0.95,
                    "issues": [],
                }
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", flaky_completion)

    reviewed, metadata = transcription.review_segments([_segment("asr_0001", "Corrected text.")], language="en")

    assert len(calls) == 2
    assert metadata["status"] == "completed"
    assert reviewed[0].corrected_text == "Corrected text."
    assert metadata["batches"] == [
        {
            "batch": 1,
            "item_count": 1,
            "duration_ms": metadata["batches"][0]["duration_ms"],
            "status": "success",
            "attempt_count": 2,
        }
    ]
    assert metadata["batches"][0]["duration_ms"] >= 0


def test_review_segments_runs_independent_batches_in_parallel(monkeypatch):
    _configure_llm(monkeypatch)
    monkeypatch.setattr(transcription, "REVIEW_BATCH_SIZE", 1)
    from app.services import llm_runtime

    barrier = threading.Barrier(3, timeout=2)

    def complete_json(**kwargs):
        barrier.wait()
        segment = kwargs["user_payload"]["segments"][0]
        return {
            "segments": [
                {
                    "segment_id": segment["segment_id"],
                    "edits": [],
                    "confidence": 0.9,
                    "issues": [],
                }
            ]
        }

    monkeypatch.setattr(llm_runtime, "complete_json", complete_json)
    source = [
        _segment("asr_0001", "First."),
        _segment("asr_0002", "Second."),
        _segment("asr_0003", "Third."),
    ]

    reviewed, metadata = transcription.review_segments(source, language="en")

    assert metadata["status"] == "completed"
    assert [item.corrected_text for item in reviewed] == [item.raw_text for item in source]


def test_review_segments_checks_cancellation_between_llm_attempts(monkeypatch):
    _configure_llm(monkeypatch)
    from app.errors import AppException
    from app.services import llm_runtime

    cancelled = False
    calls = 0

    def fail_once(**_kwargs):
        nonlocal cancelled, calls
        calls += 1
        cancelled = True
        raise llm_runtime.LlmRuntimeError("retry", code="llm_json_invalid", status_code=502)

    monkeypatch.setattr(llm_runtime, "complete_json", fail_once)

    with pytest.raises(AppException) as exc_info:
        transcription.review_segments(
            [_segment("asr_0001", "Original text.")],
            language="en",
            is_cancelled=lambda: cancelled,
        )

    assert calls == 1
    assert exc_info.value.code == "VIDEO_LOCALIZATION_OPERATION_CANCELLED"


def test_effective_word_onset_keeps_preroll_instead_of_hard_snapping_to_energy_peak():
    from app.domains.video_localization.schemas import VideoLocalizationAlignedWord

    audio = np.zeros(1000, dtype=np.float32)
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

    onsets = transcription._detect_effective_word_onsets_from_audio(audio, 1000, [word])

    assert 100 <= onsets[word.word_id] < 200


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
    assert "timing_review_required" in flags


def test_aligned_words_rejects_token_count_mismatch():
    words = transcription._aligned_words(
        _segment("asr_0001", "One two three.", 0, 1000),
        "One two three.",
        [{"text": "One", "start_time": 0.0, "end_time": 0.2}],
        0,
    )

    assert words == []


def test_aligned_words_accepts_repeated_tokens_without_losing_order():
    words = transcription._aligned_words(
        _segment("asr_0001", "Go go now.", 0, 1000),
        "Go go now.",
        [
            {"text": "go", "start_time": 0.0, "end_time": 0.2},
            {"text": "GO", "start_time": 0.25, "end_time": 0.45},
            {"text": "now", "start_time": 0.5, "end_time": 0.8},
        ],
        0,
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


def test_align_segments_stops_restarting_worker_after_first_failure(monkeypatch, tmp_path):
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

    assert len(calls) == 1
    assert metadata["status"] == "failed"
    assert all(item.timing_source == "asr_segment_interpolation" for item in words)


def test_display_tokens_keep_decimal_version_as_one_alignment_token():
    assert transcription._display_tokens("Seedance 2.0 in 4K.") == ["Seedance", "2.0", "in", "4K."]


@pytest.mark.parametrize(
    ("source_text", "source_title", "source_snippet", "expected_text", "expected_error", "evidence_ids"),
    [
        ("seed ants", "Seedance 2.0", "AI video generation model", "Seedance", None, ["source_1"]),
        ("seed ants", "Unrelated video tools", "A general editing guide", "Seedance", None, []),
        (
            "scale",
            "Seedance 2.0",
            "AI video generation model",
            "scale",
            "llm_review_rejected:unsupported_proper_noun",
            ["source_1"],
        ),
    ],
)
def test_review_research_is_audited_but_cannot_override_acoustic_guard(
    source_text,
    source_title,
    source_snippet,
    expected_text,
    expected_error,
    evidence_ids,
):
    segment = _segment("asr_0001", source_text)
    source_tokens = transcription._review_tokens([segment])[segment.segment_id]
    proposed = [
        transcription.ProposedTranscriptEdit(
            start_word_id=source_tokens[0][0],
            end_word_id=source_tokens[-1][0],
            replacement_text="Seedance",
            reason="search evidence",
            confidence=0.9,
            evidence_source_ids=["source_1"],
        )
    ]

    candidate, operations, error = transcription._apply_review_operations(
        segment,
        source_tokens,
        proposed,
        research_sources={
            "source_1": {
                "source_id": "source_1",
                "title": source_title,
                "snippet": source_snippet,
            }
        },
    )

    assert candidate == expected_text
    assert error == expected_error
    assert operations[0].evidence_source_ids == evidence_ids


def test_review_segments_rejects_unrelated_brand_promotion_of_common_word(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [
                        {
                            "start_word_id": "source_word_000006",
                            "end_word_id": "source_word_000006",
                            "replacement_text": "Seedance",
                            "reason": "brand appears in scene context",
                            "confidence": 0.9,
                        }
                    ],
                    "confidence": 0.9,
                    "issues": ["possible proper noun"],
                }
            ]
        },
    )

    reviewed, metadata = transcription.review_segments(
        [_segment("asr_0001", "Based on my request, the scale writes everything down.")],
        language="en",
        scene_context="Seedance 2.0 VFX workflow",
    )

    assert reviewed[0].corrected_text == reviewed[0].raw_text
    assert reviewed[0].review_candidate_text == reviewed[0].raw_text
    assert reviewed[0].review_rejection_reason == "llm_review_rejected:unsupported_proper_noun"
    assert reviewed[0].review_operations[0].status == "rejected"
    assert reviewed[0].review_operations[0].replacement_text == "Seedance"
    assert "llm_review_rejected_changes" in metadata["quality_flags"]


def test_review_segments_accepts_explicit_glossary_mapping_for_common_word(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [
                        {
                            "start_word_id": "source_word_000001",
                            "end_word_id": "source_word_000001",
                            "replacement_text": "Seedance",
                            "reason": "project glossary",
                            "confidence": 0.99,
                        }
                    ],
                    "confidence": 0.99,
                    "issues": [],
                }
            ]
        },
    )

    reviewed, _metadata = transcription.review_segments(
        [_segment("asr_0001", "sedans renders the clip.")],
        language="en",
        glossary=[
            VideoLocalizationGlossaryEntry(
                source_text="sedans",
                corrected_source_text="Seedance",
            )
        ],
    )

    assert reviewed[0].corrected_text == "Seedance renders the clip."


def test_review_segments_applies_explicit_glossary_when_llm_returns_no_edit(monkeypatch):
    _configure_llm(monkeypatch)
    from app.services import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "complete_json",
        lambda **_kwargs: {
            "segments": [
                {
                    "segment_id": "asr_0001",
                    "edits": [],
                    "confidence": 1.0,
                    "issues": [],
                }
            ]
        },
    )

    reviewed, metadata = transcription.review_segments(
        [_segment("asr_0001", "The scale I use for every prompt is free.")],
        language="en",
        glossary=[
            VideoLocalizationGlossaryEntry(
                source_text="scale I use",
                corrected_source_text="skill I use",
            )
        ],
    )

    assert reviewed[0].raw_text == "The scale I use for every prompt is free."
    assert reviewed[0].corrected_text == "The skill I use for every prompt is free."
    assert reviewed[0].review_operations[0].status == "accepted"
    assert reviewed[0].review_operations[0].source_text == "scale I use"
    assert reviewed[0].review_operations[0].replacement_text == "skill I use"
    assert reviewed[0].review_operations[0].reason.startswith("project_glossary:")
    assert "glossary_corrected" in metadata["quality_flags"]
