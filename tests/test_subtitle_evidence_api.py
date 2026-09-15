from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import asr_timing_contracts  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
)
from app.errors import AppException  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.voice_studio import TranscriptionRecord, TranscriptionSegment  # noqa: E402
from app.services import (  # noqa: E402
    database,
    subtitle_evidence,
    video_localization_operations,
)


class _FakeEvidenceRunner:
    def __init__(self) -> None:
        self.alignment_input = None
        self.boundary_input = None

    def __call__(
        self,
        *,
        audio_path,
        audio_sha256,
        source_track_id,
        segments,
        language,
        duration_ms,
        video_frame_rate,
    ):
        self.alignment_input = asr_timing_contracts.AsrAlignmentInput(
            alignment_audio_path=audio_path,
            alignment_audio_sha256=audio_sha256,
            alignment_source_track_id=source_track_id,
            segments=segments,
            language=language,
            duration_ms=duration_ms,
        )
        request = self.alignment_input
        alignment = (
            asr_timing_contracts.AsrAlignmentResult(
                input=request,
                words=[
                    VideoLocalizationAlignedWord(
                        word_id="word-0001",
                        segment_id=request.segments[0].segment_id,
                        text="你好",
                        start_ms=120,
                        end_ms=620,
                        timing_confidence="high",
                        timing_source="forced_aligner",
                    )
                ],
                metadata={
                    "status": "completed",
                    "engine_id": "qwen3-forced-aligner-0.6B",
                    "timing_confidence": "high",
                    "quality_flags": ["timing:forced-aligner"],
                    "alignment_call_count": 1,
                },
            )
        )
        self.boundary_input = asr_timing_contracts.AsrAudioBoundariesInput(
            audio_path=audio_path,
            audio_sha256=audio_sha256,
            source_track_id=source_track_id,
            words=alignment.words,
            video_frame_rate=video_frame_rate,
        )
        boundaries = asr_timing_contracts.AsrAudioBoundariesResult(
            input=self.boundary_input,
            boundary_features=[],
            subtitle_entry_by_word_id={"word-0001": 140},
            metadata={"status": "completed"},
        )
        return alignment, boundaries

def _store_record(tmp_path: Path) -> tuple[TranscriptionRecord, Path]:
    database.set_db_path(tmp_path / "voice_studio.db")
    audio_path = tmp_path / "retained.wav"
    audio_path.write_bytes(b"RIFF" + b"\0" * 128)
    record = TranscriptionRecord(
        engine_id="qwen3-asr-mlx",
        filename="source.wav",
        language="zh",
        text="你好",
        segments=[
            TranscriptionSegment(
                start_ms=0,
                end_ms=1_000,
                text="你好",
                language="zh",
            )
        ],
        has_source_audio=True,
        duration_ms=1_000,
        size_bytes=audio_path.stat().st_size,
    )
    database.upsert(
        "transcriptions",
        record.transcription_id,
        {**record.model_dump(), "source_audio_path": str(audio_path)},
        "created_at",
    )
    return record, audio_path


def test_subtitle_evidence_uses_domain_facade_and_hides_local_path(tmp_path: Path):
    record, audio_path = _store_record(tmp_path)
    runner = _FakeEvidenceRunner()
    service = subtitle_evidence.SubtitleEvidenceApplicationService(
        evidence_runner=runner
    )

    result = service.analyze(
        record.transcription_id,
        subtitle_evidence.SubtitleEvidenceRequest(
            language="zh",
            video_frame_rate=25,
        ),
    )

    assert runner.alignment_input.alignment_audio_path == str(audio_path)
    assert runner.boundary_input.audio_path == str(audio_path)
    assert result.aligned_words[0].timing_source == "forced_aligner"
    assert result.subtitle_entry_by_word_id == {"word-0001": 140}
    assert str(audio_path) not in result.model_dump_json()


def test_external_subtitle_evidence_coalesces_point_anchors_before_audio_analysis(
    monkeypatch,
):
    class Pipeline:
        def run_strict_alignment(self, request):
            return asr_timing_contracts.AsrAlignmentResult(
                input=request,
                words=[
                    VideoLocalizationAlignedWord(
                        word_id="word-1",
                        segment_id="segment-1",
                        text="不",
                        start_ms=100,
                        end_ms=180,
                        timing_confidence="high",
                        timing_source="forced_aligner",
                    ),
                    VideoLocalizationAlignedWord(
                        word_id="word-2",
                        segment_id="segment-1",
                        text="是",
                        start_ms=180,
                        end_ms=180,
                        timing_confidence="high",
                        timing_source="forced_aligner",
                    ),
                    VideoLocalizationAlignedWord(
                        word_id="word-3",
                        segment_id="segment-1",
                        text="说",
                        start_ms=180,
                        end_ms=340,
                        timing_confidence="high",
                        timing_source="forced_aligner",
                    ),
                ],
                metadata={
                    "status": "completed",
                    "timing_confidence": "high",
                    "quality_flags": ["timing:forced-aligner"],
                    "alignment_call_count": 1,
                },
            )

        def run_audio_boundaries(self, request):
            assert [item.text for item in request.words] == ["不", "是说"]
            assert all(
                item.end_ms > item.start_ms
                for item in request.words
            )
            return asr_timing_contracts.AsrAudioBoundariesResult(
                input=request,
                boundary_features=[],
                subtitle_entry_by_word_id={"word-1": 120},
                metadata={"status": "completed"},
            )

    monkeypatch.setattr(
        video_localization_operations.asr_pipeline,
        "AsrPipeline",
        Pipeline,
    )

    alignment, _boundaries = (
        video_localization_operations.run_external_subtitle_evidence(
            audio_path="source.wav",
            audio_sha256="audio-sha",
            source_track_id="track-1",
            segments=[],
            language="zh",
            duration_ms=1_000,
            video_frame_rate=25,
        )
    )

    assert [item.text for item in alignment.words] == ["不", "是说"]
    assert (
        "alignment_zero_width_tokens_coalesced"
        in alignment.metadata["quality_flags"]
    )
    assert alignment.metadata["zero_width_token_count"] == 1


def test_subtitle_evidence_rejects_zero_width_public_word(tmp_path: Path):
    record, _audio_path = _store_record(tmp_path)

    def invalid_runner(**kwargs):
        runner = _FakeEvidenceRunner()
        alignment, boundaries = runner(**kwargs)
        invalid = alignment.words[0].model_copy(
            update={"end_ms": alignment.words[0].start_ms}
        )
        return (
            alignment.model_copy(update={"words": [invalid]}),
            boundaries,
        )

    service = subtitle_evidence.SubtitleEvidenceApplicationService(
        evidence_runner=invalid_runner
    )

    with pytest.raises(AppException) as captured:
        service.analyze(
            record.transcription_id,
            subtitle_evidence.SubtitleEvidenceRequest(language="zh"),
        )

    assert captured.value.code == "ASR_STRICT_ALIGNMENT_INCOMPLETE"


def test_subtitle_evidence_api_accepts_locked_caller_text(tmp_path: Path):
    record, _audio_path = _store_record(tmp_path)
    runner = _FakeEvidenceRunner()
    previous = subtitle_evidence._SERVICE
    subtitle_evidence._SERVICE = (
        subtitle_evidence.SubtitleEvidenceApplicationService(
            evidence_runner=runner
        )
    )
    try:
        response = TestClient(app).post(
            f"/api/asr/{record.transcription_id}/subtitle-evidence",
            json={
                "language": "zh",
                "segments": [
                    {
                        "segment_id": "locked-1",
                        "start_ms": 0,
                        "end_ms": 1_000,
                        "text": "你 好",
                    }
                ],
            },
        )
    finally:
        subtitle_evidence._SERVICE = previous

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract_version"] == "subtitle-evidence-v1"
    assert payload["segments"][0]["segment_id"] == "locked-1"
    assert payload["segments"][0]["text"] == "你 好"
    assert "alignment_audio_path" not in response.text
    assert "source_audio_path" not in response.text


def test_openapi_exposes_subtitle_evidence_contract():
    schema = app.openapi()
    operation = schema["paths"][
        "/api/asr/{transcription_id}/subtitle-evidence"
    ]["post"]
    assert operation["summary"] == "生成严格字幕时间证据"
    assert "SubtitleEvidenceRequest" in str(operation)
    assert "SubtitleEvidenceResult" in str(operation)
