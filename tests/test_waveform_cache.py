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


def test_waveform_peaks_supports_auto_density_and_distinct_high_resolution_cache(tmp_path, monkeypatch):
    audio_path = tmp_path / "sample.wav"
    sample_rate = 1000
    signal = np.sin(np.linspace(0, np.pi * 8, sample_rate * 2, dtype=np.float32))
    sf.write(audio_path, signal, sample_rate)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: cache_dir)

    automatic = waveform_cache.waveform_peaks(
        audio_path,
        result_id="timeline-a",
        bins=None,
        max_bins=waveform_cache.MAX_BINS,
    )
    explicit = waveform_cache.waveform_peaks(
        audio_path,
        result_id="timeline-a",
        bins=1501,
        max_bins=waveform_cache.MAX_BINS,
    )

    assert automatic["duration"] == 2.0
    assert automatic["bins"] == 200
    assert len(automatic["peaks"]) == 200
    assert explicit["bins"] == 1501
    assert len(explicit["peaks"]) == 1501
    assert len(list((cache_dir / "waveforms").glob("timeline-a-*.json"))) == 2


def test_waveform_peaks_distributes_short_audio_without_padded_silence(tmp_path, monkeypatch):
    audio_path = tmp_path / "short.wav"
    sf.write(audio_path, np.ones(100, dtype=np.float32) * 0.25, 1000)
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: tmp_path / "cache")

    payload = waveform_cache.waveform_peaks(audio_path, result_id="short", bins=32)

    assert len(payload["peaks"]) == 32
    assert min(payload["peaks"]) > 0.2
