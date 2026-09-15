from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from app.services import settings_store
from app.services.keyed_lock_registry import KeyedLockRegistry

MIN_BINS = 32
DEFAULT_MAX_BINS = 1200
MAX_BINS = 180_000
DEFAULT_PEAKS_PER_SECOND = 100
_READ_TARGET_FRAMES = 1_048_576
_CACHE_VERSION = 2
_CACHE_LOCKS = KeyedLockRegistry[Path]()


def waveform_peaks(
    path: Path,
    *,
    result_id: str,
    bins: int | None = 320,
    max_bins: int = DEFAULT_MAX_BINS,
    peaks_per_second: int = DEFAULT_PEAKS_PER_SECOND,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict[str, object]:
    effective_max = max(MIN_BINS, min(MAX_BINS, int(max_bins)))
    window = _normalize_window(path, start_ms=start_ms, end_ms=end_ms)
    if bins is None:
        info = sf.info(str(path))
        duration = (
            (window[1] - window[0]) / 1000
            if window is not None
            else info.frames / info.samplerate if info.samplerate else 0.0
        )
        bins = math.ceil(duration * max(1, int(peaks_per_second)))
    bins = max(MIN_BINS, min(effective_max, int(bins)))
    stat = path.stat()
    cache_dir = settings_store.cache_dir() / "waveforms"
    cache_dir.mkdir(parents=True, exist_ok=True)
    window_suffix = f"-r{window[0]}-{window[1]}" if window is not None else ""
    cache_key = f"{result_id}-v{_CACHE_VERSION}-{stat.st_mtime_ns}-{stat.st_size}{window_suffix}-{bins}.json"
    cache_path = cache_dir / cache_key
    cached = _read_cached_payload(cache_path, bins, window=window)
    if cached is not None:
        return cached

    with _CACHE_LOCKS.hold(cache_path):
        cached = _read_cached_payload(cache_path, bins, window=window)
        if cached is not None:
            return cached
        payload = _read_finer_cached_payload(
            cache_dir,
            cache_prefix=f"{result_id}-v{_CACHE_VERSION}-{stat.st_mtime_ns}-{stat.st_size}{window_suffix}-",
            bins=bins,
            window=window,
        )
        if payload is None:
            payload = (
                _read_peaks(path, bins)
                if window is None
                else _read_peaks(path, bins, start_ms=window[0], end_ms=window[1])
            )
        for stale in cache_dir.glob(f"{result_id}-*-{bins}.json"):
            cache_tail = stale.name.removeprefix(f"{result_id}-")
            is_range_cache = "-r" in cache_tail.removesuffix(f"-{bins}.json")
            same_window = (
                not is_range_cache
                if window is None
                else stale.name.endswith(f"{window_suffix}-{bins}.json")
            )
            if stale != cache_path and same_window:
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


def delete_result_cache_prefix(result_id_prefix: str) -> int:
    cache_dir = settings_store.cache_dir() / "waveforms"
    if not result_id_prefix or not cache_dir.exists() or cache_dir.is_symlink():
        return 0
    removed = 0
    for path in cache_dir.iterdir():
        if path.is_file() and not path.is_symlink() and path.name.startswith(result_id_prefix) and path.suffix == ".json":
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _read_cached_payload(cache_path: Path, bins: int, *, window: tuple[int, int] | None = None) -> dict[str, object] | None:
    if not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        peaks = payload.get("peaks") if isinstance(payload, dict) else None
        if (
            not isinstance(peaks, list)
            or len(peaks) != bins
            or int(payload.get("bins") or 0) != bins
        ):
            raise ValueError("waveform cache payload is incomplete")
        cached_window = (
            (payload.get("window_start_ms"), payload.get("window_end_ms"))
            if "window_start_ms" in payload or "window_end_ms" in payload else None
        )
        if cached_window != window:
            raise ValueError("waveform cache time window does not match request")
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        cache_path.unlink(missing_ok=True)
        return None


def _read_finer_cached_payload(
    cache_dir: Path,
    *,
    cache_prefix: str,
    bins: int,
    window: tuple[int, int] | None = None,
) -> dict[str, object] | None:
    candidates: list[tuple[int, Path]] = []
    for path in cache_dir.glob(f"{cache_prefix}*.json"):
        try:
            candidate_bins = int(path.stem.removeprefix(cache_prefix))
        except (TypeError, ValueError):
            continue
        if candidate_bins > bins:
            candidates.append((candidate_bins, path))
    for candidate_bins, path in sorted(candidates):
        payload = _read_cached_payload(path, candidate_bins, window=window)
        if payload is None:
            continue
        peaks = np.asarray(payload["peaks"], dtype=np.float32)
        target = np.zeros(bins, dtype=np.float32)
        source_indices = np.arange(candidate_bins, dtype=np.int64)
        target_indices = np.minimum(bins - 1, (source_indices * bins) // candidate_bins)
        np.maximum.at(target, target_indices, peaks)
        return {
            "peaks": [round(float(value), 5) for value in target],
            "duration": payload.get("duration", 0.0),
            "bins": bins,
            **(
                {
                    "window_start_ms": payload["window_start_ms"],
                    "window_end_ms": payload["window_end_ms"],
                }
                if "window_start_ms" in payload and "window_end_ms" in payload
                else {}
            ),
        }
    return None


def _normalize_window(
    path: Path,
    *,
    start_ms: int | None,
    end_ms: int | None,
) -> tuple[int, int] | None:
    if start_ms is None and end_ms is None:
        return None
    info = sf.info(str(path))
    duration_ms = math.ceil((info.frames * 1000) / info.samplerate) if info.samplerate else 0
    start = max(0, min(duration_ms, int(start_ms or 0)))
    end = max(start, min(duration_ms, int(end_ms if end_ms is not None else duration_ms)))
    if end <= start:
        raise ValueError("waveform time window must have positive duration")
    return start, end


def _read_peaks(
    path: Path,
    bins: int,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict[str, object]:
    with sf.SoundFile(str(path)) as audio:
        frame_count = max(0, int(audio.frames))
        sample_rate = max(0, int(audio.samplerate))
        window_start_frame = 0
        window_end_frame = frame_count
        if start_ms is not None or end_ms is not None:
            window_start_frame = min(frame_count, max(0, math.floor((int(start_ms or 0) * sample_rate) / 1000)))
            window_end_frame = min(
                frame_count,
                max(window_start_frame, math.ceil((int(end_ms or 0) * sample_rate) / 1000)),
            )
            audio.seek(window_start_frame)
        window_frame_count = max(0, window_end_frame - window_start_frame)
        peak_values = np.zeros(bins, dtype=np.float32)
        frame_offset = 0
        while frame_offset < window_frame_count:
            chunk = audio.read(min(_READ_TARGET_FRAMES, window_frame_count - frame_offset), dtype="float32", always_2d=True)
            if chunk.size == 0:
                break
            frame_peaks = np.max(np.abs(chunk), axis=1)
            frame_indices = np.arange(frame_offset, frame_offset + len(chunk), dtype=np.int64)
            bin_indices = np.minimum(bins - 1, (frame_indices * bins) // max(1, window_frame_count))
            np.maximum.at(peak_values, bin_indices, frame_peaks)
            frame_offset += len(chunk)
    peaks = [round(float(value), 5) for value in peak_values]
    duration = frame_count / sample_rate if sample_rate else 0.0
    payload: dict[str, object] = {"peaks": peaks, "duration": round(duration, 6), "bins": bins}
    if start_ms is not None or end_ms is not None:
        payload["window_start_ms"] = int(start_ms or 0)
        payload["window_end_ms"] = int(end_ms or 0)
    return payload
