from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import soundfile as sf


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
