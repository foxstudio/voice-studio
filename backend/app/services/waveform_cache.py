from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from app.services import settings_store

MIN_BINS = 32
DEFAULT_MAX_BINS = 1200
MAX_BINS = 180_000
DEFAULT_PEAKS_PER_SECOND = 100
_READ_TARGET_FRAMES = 1_048_576
_CACHE_VERSION = 2


def waveform_peaks(
    path: Path,
    *,
    result_id: str,
    bins: int | None = 320,
    max_bins: int = DEFAULT_MAX_BINS,
    peaks_per_second: int = DEFAULT_PEAKS_PER_SECOND,
) -> dict[str, object]:
    effective_max = max(MIN_BINS, min(MAX_BINS, int(max_bins)))
    if bins is None:
        info = sf.info(str(path))
        duration = info.frames / info.samplerate if info.samplerate else 0.0
        bins = math.ceil(duration * max(1, int(peaks_per_second)))
    bins = max(MIN_BINS, min(effective_max, int(bins)))
    stat = path.stat()
    cache_dir = settings_store.cache_dir() / "waveforms"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = f"{result_id}-v{_CACHE_VERSION}-{stat.st_mtime_ns}-{stat.st_size}-{bins}.json"
    cache_path = cache_dir / cache_key
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    payload = _read_peaks(path, bins)
    for stale in cache_dir.glob(f"{result_id}-*-{bins}.json"):
        if stale != cache_path:
            stale.unlink(missing_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=cache_dir, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
        temp_path = Path(handle.name)
    temp_path.replace(cache_path)
    return payload


def delete_result_cache(result_id: str) -> int:
    cache_dir = settings_store.cache_dir() / "waveforms"
    if not cache_dir.exists() or cache_dir.is_symlink():
        return 0
    removed = 0
    for path in cache_dir.glob(f"{result_id}-*.json"):
        if path.is_file() and not path.is_symlink():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _read_peaks(path: Path, bins: int) -> dict[str, object]:
    with sf.SoundFile(str(path)) as audio:
        frame_count = max(0, int(audio.frames))
        sample_rate = max(0, int(audio.samplerate))
        peak_values = np.zeros(bins, dtype=np.float32)
        frame_offset = 0
        while frame_offset < frame_count:
            chunk = audio.read(min(_READ_TARGET_FRAMES, frame_count - frame_offset), dtype="float32", always_2d=True)
            if chunk.size == 0:
                break
            frame_peaks = np.max(np.abs(chunk), axis=1)
            frame_indices = np.arange(frame_offset, frame_offset + len(chunk), dtype=np.int64)
            bin_indices = np.minimum(bins - 1, (frame_indices * bins) // max(1, frame_count))
            np.maximum.at(peak_values, bin_indices, frame_peaks)
            frame_offset += len(chunk)
    peaks = [round(float(value), 5) for value in peak_values]
    duration = frame_count / sample_rate if sample_rate else 0.0
    return {"peaks": peaks, "duration": round(duration, 6), "bins": bins}
