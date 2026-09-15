from __future__ import annotations

import threading
import time
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


def test_waveform_peaks_reads_only_the_requested_time_window(tmp_path, monkeypatch):
    audio_path = tmp_path / "windowed.wav"
    sample_rate = 1000
    signal = np.concatenate(
        [
            np.full(sample_rate, 0.1, dtype=np.float32),
            np.full(sample_rate, 0.8, dtype=np.float32),
            np.full(sample_rate, 0.2, dtype=np.float32),
        ]
    )
    sf.write(audio_path, signal, sample_rate)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: cache_dir)

    payload = waveform_cache.waveform_peaks(
        audio_path,
        result_id="windowed-a",
        bins=32,
        start_ms=1000,
        end_ms=2000,
    )

    assert payload["duration"] == 3.0
    assert payload["window_start_ms"] == 1000
    assert payload["window_end_ms"] == 2000
    assert min(payload["peaks"]) > 0.79
    assert len(list((cache_dir / "waveforms").glob("windowed-a-*.json"))) == 1


def test_waveform_peaks_derives_coarse_preview_from_finer_cache(tmp_path, monkeypatch):
    audio_path = tmp_path / "sample.wav"
    sf.write(audio_path, np.linspace(-1, 1, 1000, dtype=np.float32), 1000)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: cache_dir)

    finer = waveform_cache.waveform_peaks(audio_path, result_id="lod-a", bins=128)

    def fail_read(_path, _bins):
        raise AssertionError("coarse waveform should reuse the finer cache")

    monkeypatch.setattr(waveform_cache, "_read_peaks", fail_read)
    coarse = waveform_cache.waveform_peaks(audio_path, result_id="lod-a", bins=32)

    assert coarse["duration"] == finer["duration"]
    assert coarse["bins"] == 32
    assert len(coarse["peaks"]) == 32
    for index, peak in enumerate(coarse["peaks"]):
        assert peak == max(finer["peaks"][index * 4:(index + 1) * 4])
    assert len(list((cache_dir / "waveforms").glob("lod-a-*.json"))) == 2


def test_waveform_peaks_distributes_short_audio_without_padded_silence(tmp_path, monkeypatch):
    audio_path = tmp_path / "short.wav"
    sf.write(audio_path, np.ones(100, dtype=np.float32) * 0.25, 1000)
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: tmp_path / "cache")

    payload = waveform_cache.waveform_peaks(audio_path, result_id="short", bins=32)

    assert len(payload["peaks"]) == 32
    assert min(payload["peaks"]) > 0.2


def test_waveform_peaks_deduplicates_concurrent_generation(tmp_path, monkeypatch):
    audio_path = tmp_path / "sample.wav"
    sf.write(audio_path, np.ones(100, dtype=np.float32), 1000)
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: tmp_path / "cache")
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_read(_path, bins):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=1)
        return {"peaks": [0.5] * bins, "duration": 0.1, "bins": bins}

    monkeypatch.setattr(waveform_cache, "_read_peaks", fake_read)
    results: list[dict[str, object]] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                waveform_cache.waveform_peaks(audio_path, result_id="shared", bins=32)
            )
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=1)
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(timeout=1)

    assert all(not thread.is_alive() for thread in threads)
    assert calls == 1
    assert len(results) == 2
    assert results[0] == results[1]
    assert waveform_cache._CACHE_LOCKS.active_key_count == 0


def test_waveform_peaks_rebuilds_corrupt_cache_file(tmp_path, monkeypatch):
    audio_path = tmp_path / "sample.wav"
    sf.write(audio_path, np.ones(100, dtype=np.float32), 1000)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: cache_dir)
    stat = audio_path.stat()
    corrupt = (
        cache_dir
        / "waveforms"
        / f"broken-v{waveform_cache._CACHE_VERSION}-{stat.st_mtime_ns}-{stat.st_size}-32.json"
    )
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{not-json", encoding="utf-8")

    payload = waveform_cache.waveform_peaks(audio_path, result_id="broken", bins=32)

    assert len(payload["peaks"]) == 32
    assert payload["bins"] == 32


def test_full_waveform_never_reuses_partial_finer_cache(tmp_path, monkeypatch):
    audio = tmp_path / "two-levels.wav"
    sf.write(audio, np.concatenate([np.full(8000, .2), np.full(8000, .8)]), 8000)
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: tmp_path / "cache")
    waveform_cache.waveform_peaks(audio, result_id="ranges", bins=128, start_ms=0, end_ms=1000)
    full = waveform_cache.waveform_peaks(audio, result_id="ranges", bins=32)
    assert "window_end_ms" not in full
    assert max(full["peaks"][:16]) < .21
    assert min(full["peaks"][16:]) > .79


def test_full_waveform_repairs_existing_range_polluted_cache(tmp_path, monkeypatch):
    import json
    audio = tmp_path / "two-levels.wav"
    sf.write(audio, np.concatenate([np.full(8000, .2), np.full(8000, .8)]), 8000)
    cache = tmp_path / "cache"
    monkeypatch.setattr(waveform_cache.settings_store, "cache_dir", lambda: cache)
    partial = waveform_cache.waveform_peaks(audio, result_id="polluted", bins=32, start_ms=0, end_ms=1000)
    stat = audio.stat()
    target = cache / "waveforms" / f"polluted-v{waveform_cache._CACHE_VERSION}-{stat.st_mtime_ns}-{stat.st_size}-32.json"
    target.write_text(json.dumps(partial))
    full = waveform_cache.waveform_peaks(audio, result_id="polluted", bins=32)
    assert "window_end_ms" not in full
    assert min(full["peaks"][16:]) > .79
    assert json.loads(target.read_text()) == full
