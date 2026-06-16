from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from fastapi import UploadFile

from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationCue
from app.services import audio_tools, settings_store

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


async def save_uploaded_video(project_id: str, file: UploadFile) -> tuple[Path, bytes]:
    filename = file.filename or "source.mp4"
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise AppException(400, "VIDEO_LOCALIZATION_UNSUPPORTED_MEDIA", "Only mp4, mov, m4v, webm, and mkv videos are supported")

    content = await file.read()
    if not content:
        raise AppException(400, "VIDEO_LOCALIZATION_EMPTY_UPLOAD", "Uploaded video is empty")

    settings_store.ensure_directories()
    source_dir = project_video_localization_dir(project_id) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_path(source_dir / safe_filename(filename))
    destination.write_bytes(content)
    return destination, content


def project_video_localization_dir(project_id: str) -> Path:
    settings_store.ensure_directories()
    return settings_store.expand_path(settings_store.get().project_dir) / project_id / "video_localization"


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "source.mp4"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._-") or "source"
    suffix = Path(name).suffix.lower() or ".mp4"
    return f"{stem}{suffix}"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise AppException(500, "VIDEO_LOCALIZATION_UPLOAD_COLLISION", "Could not allocate a unique video path")


def extract_audio_file(video_path: Path, audio_path: Path) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppException(500, "VIDEO_LOCALIZATION_FFMPEG_MISSING", "ffmpeg is required to extract source audio")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "48000",
        str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not audio_path.exists():
        audio_path.unlink(missing_ok=True)
        raise AppException(500, "VIDEO_LOCALIZATION_AUDIO_EXTRACT_FAILED", "Failed to extract source audio")
    return audio_tools.probe_audio(audio_path)


def separate_audio_file(audio_path: Path, stems_dir: Path) -> dict:
    demucs = shutil.which("demucs")
    if not demucs:
        raise AppException(500, "VIDEO_LOCALIZATION_DEMUCS_MISSING", "demucs is required to separate vocals and background")

    output_root = stems_dir / "demucs"
    command = [
        demucs,
        "--two-stems",
        "vocals",
        "--name",
        "htdemucs",
        "--out",
        str(output_root),
        str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    demucs_dir = output_root / "htdemucs" / audio_path.stem
    vocals_path = demucs_dir / "vocals.wav"
    background_path = demucs_dir / "no_vocals.wav"
    if result.returncode != 0 or not vocals_path.exists() or not background_path.exists():
        raise AppException(500, "VIDEO_LOCALIZATION_SEPARATION_FAILED", "Failed to separate vocals and background")

    vocals_clean_path = unique_path(stems_dir / f"{audio_path.stem}-vocals-clean.wav")
    background_dest = unique_path(stems_dir / f"{audio_path.stem}-background.wav")
    shutil.copy2(vocals_path, vocals_clean_path)
    shutil.copy2(background_path, background_dest)
    quality = audio_tools.quality_metrics(vocals_clean_path, min_duration_ms=1000)
    return {
        "vocals_clean_path": vocals_clean_path,
        "background_path": background_dest,
        "engine_id": "demucs:htdemucs",
        "quality_flags": quality.get("warnings", []),
    }


def cut_audio_clip(source_path: Path, destination: Path, start_ms: int, end_ms: int) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppException(500, "VIDEO_LOCALIZATION_FFMPEG_MISSING", "ffmpeg is required to create reference clips")
    if end_ms <= start_ms:
        raise AppException(400, "VIDEO_LOCALIZATION_REFERENCE_RANGE_INVALID", "Reference clip time range is invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(source_path),
        "-ss",
        f"{start_ms / 1000:.3f}",
        "-to",
        f"{end_ms / 1000:.3f}",
        "-ac",
        "1",
        "-ar",
        "24000",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not destination.exists():
        destination.unlink(missing_ok=True)
        raise AppException(500, "VIDEO_LOCALIZATION_REFERENCE_CLIP_FAILED", "Failed to create reference clip")
    return destination


def source_cue_cache_path(cache_dir: Path, source_path: Path, cue: VideoLocalizationCue) -> Path:
    stat = source_path.stat()
    signature = f"{stat.st_size}-{stat.st_mtime_ns}"
    name = f"{_safe_identifier(cue.cue_id)}-{cue.start_ms}-{cue.end_ms}-{signature}-source.wav"
    return cache_dir / name


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "item"
