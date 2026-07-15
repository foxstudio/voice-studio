from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


REFERENCE_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv"})


def is_reference_video(path_or_name: str | Path) -> bool:
    return Path(path_or_name).suffix.lower() in REFERENCE_VIDEO_SUFFIXES


def extract_reference_audio(video_path: str | Path, audio_path: str | Path, *, timeout_seconds: int = 120) -> dict:
    """Extract a mono 24 kHz PCM WAV suitable for all reference-audio paths."""

    source = Path(video_path)
    destination = Path(audio_path)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("REFERENCE_VIDEO_FFMPEG_MISSING")

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "json", str(source)],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        try:
            streams = json.loads(probe.stdout or "{}").get("streams") or []
        except json.JSONDecodeError:
            streams = []
        if probe.returncode == 0 and not streams:
            raise ValueError("REFERENCE_VIDEO_NO_AUDIO")

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destination),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        destination.unlink(missing_ok=True)
        raise RuntimeError("REFERENCE_VIDEO_AUDIO_EXTRACT_TIMEOUT") from exc
    if result.returncode != 0 or not destination.exists() or not destination.stat().st_size:
        destination.unlink(missing_ok=True)
        if "matches no streams" in (result.stderr or "").lower():
            raise ValueError("REFERENCE_VIDEO_NO_AUDIO")
        raise RuntimeError("REFERENCE_VIDEO_AUDIO_EXTRACT_FAILED")
    return probe_audio(destination)


def probe_audio(path: str | Path) -> dict:
    info = sf.info(str(path))
    return {
        "duration_ms": int(info.frames / info.samplerate * 1000) if info.samplerate else 0,
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "format": info.format,
        "size_bytes": Path(path).stat().st_size,
    }


def quality_metrics(path: str | Path, min_duration_ms: int = 500, min_peak: float = 0.05, min_rms: float = 0.01) -> dict:
    audio, sr = read_audio(path)
    duration_ms = int(len(audio) / sr * 1000) if sr else 0
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
    silence_ratio = float(np.mean(np.abs(audio) < 0.01)) if audio.size else 1.0
    warnings: list[str] = []
    if duration_ms < min_duration_ms:
        warnings.append("音频时长过短")
    if peak < min_peak:
        warnings.append("峰值过低，可能听起来像无声")
    if rms < min_rms:
        warnings.append("平均响度过低，建议标准化或排查引擎")
    if silence_ratio > 0.75:
        warnings.append("静音占比过高")
    return {
        "duration_ms": duration_ms,
        "sample_rate": sr,
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "silence_ratio": round(silence_ratio, 4),
        "size_bytes": Path(path).stat().st_size if Path(path).exists() else 0,
        "passed": not warnings,
        "warnings": warnings,
    }


def read_audio(path: str | Path) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path), always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.astype(np.float32), sr


def write_audio(path: str | Path, audio: np.ndarray, sr: int, fmt: str = "wav") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = np.clip(audio, -1.0, 1.0)
    if fmt == "mp3":
        try:
            from pydub import AudioSegment

            tmp = path.with_suffix(".tmp.wav")
            sf.write(str(tmp), audio, sr, subtype="PCM_16")
            AudioSegment.from_wav(str(tmp)).export(str(path), format="mp3", bitrate="192k")
            tmp.unlink(missing_ok=True)
            return path
        except Exception:
            path = path.with_suffix(".wav")
    sf.write(str(path), audio, sr, subtype="PCM_16")
    return path


def time_stretch_file(path: str | Path, rate: float) -> dict:
    """Apply speech-friendly time stretching in-place without changing pitch."""
    if abs(rate - 1.0) < 1e-3:
        return probe_audio(path)

    from mlx_indextts.generate import time_stretch_wsola

    path = Path(path)
    audio, sr = sf.read(str(path), always_2d=False, dtype="float32")
    if audio.ndim <= 1:
        stretched = time_stretch_wsola(audio.astype(np.float32), rate=rate, sample_rate=sr)
    else:
        channels = [
            time_stretch_wsola(audio[:, index].astype(np.float32), rate=rate, sample_rate=sr)
            for index in range(audio.shape[1])
        ]
        target_len = min(len(channel) for channel in channels)
        stretched = np.stack([channel[:target_len] for channel in channels], axis=1)

    sf.write(str(path), np.clip(stretched, -1.0, 1.0), sr, subtype="PCM_16")
    return probe_audio(path)


def normalize(audio: np.ndarray, target_peak: float = 0.92) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 0:
        return audio
    return audio * min(target_peak / peak, 8.0)


def trim_silence(audio: np.ndarray, threshold: float = 0.01, padding: int = 1200) -> np.ndarray:
    if not audio.size:
        return audio
    mask = np.abs(audio) > threshold
    if not mask.any():
        return audio
    idx = np.where(mask)[0]
    start = max(0, int(idx[0]) - padding)
    end = min(len(audio), int(idx[-1]) + padding)
    return audio[start:end]


def convert_file(src: str | Path, dest: str | Path, fmt: str = "wav", do_normalize: bool = False) -> Path:
    audio, sr = read_audio(src)
    if do_normalize:
        audio = normalize(audio)
    return write_audio(dest, audio, sr, fmt)


def crop_file(src: str | Path, dest: str | Path, start_ms: int, end_ms: int, fmt: str = "wav") -> Path:
    if end_ms <= start_ms:
        raise ValueError("end_ms must be greater than start_ms")
    audio, sr = read_audio(src)
    start_frame = max(0, int(sr * start_ms / 1000))
    end_frame = min(len(audio), max(start_frame + 1, int(sr * end_ms / 1000)))
    return write_audio(dest, audio[start_frame:end_frame], sr, fmt)


def merge_files(paths: list[str | Path], dest: str | Path, fmt: str = "wav", silence_ms: int = 300, do_normalize: bool = False) -> Path:
    if not paths:
        raise ValueError("No audio files to merge")
    chunks: list[np.ndarray] = []
    target_sr: int | None = None
    for path in paths:
        audio, sr = read_audio(path)
        if target_sr is None:
            target_sr = sr
        elif sr != target_sr:
            duration = len(audio) / sr
            new_len = max(1, int(duration * target_sr))
            audio = np.interp(np.linspace(0, len(audio), new_len, endpoint=False), np.arange(len(audio)), audio).astype(np.float32)
        chunks.append(audio)
        if silence_ms > 0:
            chunks.append(np.zeros(int(target_sr * silence_ms / 1000), dtype=np.float32))
    merged = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
    if do_normalize:
        merged = normalize(merged)
    return write_audio(dest, merged, target_sr or 24000, fmt)


def copy_or_convert(src: str | Path, dest: str | Path, fmt: str) -> Path:
    src = Path(src)
    dest = Path(dest)
    if src.suffix.lower().lstrip(".") == fmt:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest
    return convert_file(src, dest, fmt)
