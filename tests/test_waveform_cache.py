from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from app.services import waveform_cache


def test_waveform_peaks_streams_and_reuses_cache(tmp_path, monkeypatch):
    audio_path = tmp_path / "sample.wav"
    sample_rate = 24000
    signal = np.sin(np.linspace(0, np.pi * 16, sample_rate, dtype=np.float32)) * 0.7
    sf.write(audio_path, signal, sample_rate)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: cache_dir)

    first = waveform_cache.waveform_peaks(audio_path, result_id="result-a", bins=64)
    cache_files = list((cache_dir / "waveforms").glob("*.json"))
    assert len(first["peaks"]) == 64
    assert first["duration"] == 1.0
    assert max(first["peaks"]) > 0.69
    assert len(cache_files) == 1

    second = waveform_cache.waveform_peaks(audio_path, result_id="result-a", bins=64)
    assert second == first
    assert list((cache_dir / "waveforms").glob("*.json")) == cache_files
