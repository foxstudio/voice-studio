from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization import audio_boundaries  # noqa: E402
from app.domains.video_localization.schemas import (  # noqa: E402
    VideoLocalizationAlignedWord,
    VideoLocalizationAudioBoundaryEvidence,
)


def _word(word_id: str, start_ms: int, end_ms: int):
    return VideoLocalizationAlignedWord(
        word_id=word_id,
        segment_id="asr_0001",
        text=word_id,
        start_ms=start_ms,
        end_ms=end_ms,
        timing_confidence="high",
        timing_source="forced_aligner",
    )


def test_audio_boundary_analysis_detects_a_real_low_energy_gap(monkeypatch):
    sample_rate = 1000
    audio = np.zeros(1400, dtype=np.float32)
    audio[100:500] = 0.3
    audio[800:1200] = 0.3
    monkeypatch.setattr(audio_boundaries.audio_tools, "read_audio", lambda _path: (audio, sample_rate))

    features, metadata = audio_boundaries.analyze_word_boundaries(
        "source.wav",
        [_word("word_000001", 100, 500), _word("word_000002", 800, 1200)],
    )

    assert metadata["status"] == "completed"
    assert len(features) == 1
    assert features[0].gap_ms == 300
    assert features[0].low_energy_ms >= 280
    assert features[0].low_energy_ratio >= 0.9
    assert features[0].confidence == "high"


def test_audio_boundary_analysis_rejects_a_loud_gap(monkeypatch):
    sample_rate = 1000
    audio = np.full(1400, 0.25, dtype=np.float32)
    monkeypatch.setattr(audio_boundaries.audio_tools, "read_audio", lambda _path: (audio, sample_rate))

    features, metadata = audio_boundaries.analyze_word_boundaries(
        "source.wav",
        [_word("word_000001", 100, 500), _word("word_000002", 800, 1200)],
    )

    assert metadata["status"] == "completed"
    assert len(features) == 1
    assert features[0].low_energy_ratio == 0
    assert features[0].confidence == "none"


def test_audio_boundary_analysis_failure_is_non_destructive(monkeypatch):
    def fail(_path):
        raise RuntimeError("cannot decode")

    monkeypatch.setattr(audio_boundaries.audio_tools, "read_audio", fail)

    features, metadata = audio_boundaries.analyze_word_boundaries(
        "source.wav",
        [_word("word_000001", 0, 300), _word("word_000002", 600, 900)],
    )

    assert features == []
    assert metadata["status"] == "failed"
    assert metadata["quality_flags"] == ["audio_boundary_analysis_failed"]


@pytest.mark.parametrize(
    "patch",
    [
        {"gap_ms": 301},
        {"low_energy_ms": 301},
        {"boundary_id": "wrong"},
        {"gap_rms_dbfs": float("nan")},
    ],
)
def test_audio_boundary_schema_rejects_impossible_evidence(patch):
    payload = {
        "boundary_id": "word_000001:word_000002",
        "left_word_id": "word_000001",
        "right_word_id": "word_000002",
        "start_ms": 500,
        "end_ms": 800,
        "gap_ms": 300,
        "low_energy_ms": 280,
        "low_energy_ratio": 0.9,
        "gap_rms_dbfs": -48,
        "speech_reference_dbfs": -18,
        "noise_floor_dbfs": -55,
        "energy_drop_db": 30,
        "confidence": "high",
        **patch,
    }

    with pytest.raises(ValidationError):
        VideoLocalizationAudioBoundaryEvidence(**payload)


def test_low_confidence_word_timing_caps_pause_confidence(monkeypatch):
    sample_rate = 1000
    audio = np.zeros(1400, dtype=np.float32)
    audio[100:500] = 0.3
    audio[800:1200] = 0.3
    monkeypatch.setattr(audio_boundaries.audio_tools, "read_audio", lambda _path: (audio, sample_rate))
    left = _word("word_000001", 100, 500)
    left.timing_confidence = "low"

    features, _metadata = audio_boundaries.analyze_word_boundaries(
        "source.wav",
        [left, _word("word_000002", 800, 1200)],
    )

    assert features[0].confidence == "low"
