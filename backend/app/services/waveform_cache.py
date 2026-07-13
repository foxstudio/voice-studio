from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from app.services import settings_store


def waveform_peaks(path: Path, *, result_id: str, bins: int = 320) -> dict[str, object]:
    bins = max(32, min(1200, int(bins)))
    stat = path.stat()
    cache_dir = settings_store.cache_dir() / "waveforms"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = f"{result_id}-{stat.st_mtime_ns}-{stat.st_size}-{bins}.json"
    cache_path = cache_dir / cache_key
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    payload = _read_peaks(path, bins)
    for stale in cache_dir.glob(f"{result_id}-*.json"):
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
        frames_per_bin = max(1, math.ceil(frame_count / bins))
        peaks: list[float] = []
        while len(peaks) < bins:
            chunk = audio.read(frames_per_bin, dtype="float32", always_2d=True)
            if chunk.size == 0:
                break
            peaks.append(round(float(np.max(np.abs(chunk))), 5))
    if len(peaks) < bins:
        peaks.extend([0.0] * (bins - len(peaks)))
    duration = frame_count / sample_rate if sample_rate else 0.0
    return {"peaks": peaks, "duration": round(duration, 6), "bins": bins}
