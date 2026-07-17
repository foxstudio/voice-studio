from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationCue
from app.services import audio_tools, settings_store

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
PROJECT_DIR_NAME_KEY = "video_localization_dir_name"
LEGACY_DIR_NAME = "video_localization"
MIGRATION_CONFLICT_DIR = "migration-conflicts"


async def save_uploaded_video(project_id: str, file: UploadFile) -> tuple[Path, int, str]:
    filename = file.filename or "source.mp4"
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise AppException(400, "VIDEO_LOCALIZATION_UNSUPPORTED_MEDIA", "Only mp4, mov, m4v, webm, and mkv videos are supported")

    settings_store.ensure_directories()
    source_dir = project_video_localization_dir(project_id) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_path(source_dir / safe_filename(filename))
    temporary = unique_path(destination.with_name(f".{destination.name}.part"))
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with temporary.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                handle.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
        if not size_bytes:
            raise AppException(400, "VIDEO_LOCALIZATION_EMPTY_UPLOAD", "Uploaded video is empty")
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination, size_bytes, digest.hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_video_localization_dir(project_id: str) -> Path:
    settings_store.ensure_directories()
    projects_root = projects_root_dir()
    destination = projects_root / _stored_project_dir_name(project_id)
    _migrate_legacy_project_layout(projects_root, project_id, destination)
    return destination


def project_video_localization_dir_for_name(project_id: str, project_name: str) -> Path:
    settings_store.ensure_directories()
    return projects_root_dir() / project_dir_name(project_id, project_name)


def projects_root_dir() -> Path:
    settings_store.ensure_directories()
    return settings_store.expand_path(settings_store.get().project_dir)


def project_dir_name(project_id: str, project_name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", project_name.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._-")
    if not cleaned:
        cleaned = "video-localization"
    if len(cleaned) > 96:
        cleaned = cleaned[:96].rstrip("._-") or "video-localization"
    return f"{cleaned}--{project_id}"


def _stored_project_dir_name(project_id: str) -> str:
    try:
        from app.services import project_store

        project = project_store.get_project(project_id)
    except Exception:
        project = None
    if not project:
        return project_id
    value = project.parameters.get(PROJECT_DIR_NAME_KEY)
    if value:
        return str(value).strip()
    directory_name = project_dir_name(project_id, project.name)
    project.parameters = {**project.parameters, PROJECT_DIR_NAME_KEY: directory_name}
    project_store.save_project(project)
    return directory_name


def legacy_project_roots(project_id: str) -> list[Path]:
    projects_root = settings_store.expand_path(settings_store.get().project_dir)
    destination = projects_root / _stored_project_dir_name(project_id)
    candidates = [destination / LEGACY_DIR_NAME, projects_root / project_id / LEGACY_DIR_NAME]
    if destination != projects_root / project_id:
        candidates.append(projects_root / project_id)
    unique: list[Path] = []
    for candidate in candidates:
        if candidate != destination and candidate not in unique:
            unique.append(candidate)
    return unique


def rebase_project_paths(project_id: str, value: Any) -> Any:
    destination = project_video_localization_dir(project_id)
    prefixes = sorted((path for path in legacy_project_roots(project_id) if path != destination), key=lambda path: len(str(path)), reverse=True)
    return _replace_path_prefixes(value, prefixes, destination)


def cleanup_unreferenced_stems(project_id: str, referenced_paths: list[str | None]) -> list[Path]:
    stems_dir = project_video_localization_dir(project_id) / "stems"
    if not stems_dir.exists():
        return []
    referenced = {Path(value).resolve() for value in referenced_paths if value}
    removed: list[Path] = []
    for path in stems_dir.iterdir():
        if not path.is_file() or path.resolve() in referenced:
            continue
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def adopt_tts_audio(project_id: str, source_path: Path, cue_id: str, identity: str) -> Path:
    root = project_video_localization_dir(project_id).resolve()
    source = source_path.resolve()
    try:
        source.relative_to(root)
        return source
    except ValueError:
        pass
    suffix = source.suffix.lower() or ".wav"
    destination_dir = root / "tts" / _safe_identifier(cue_id)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{_safe_identifier(identity)}{suffix}"
    if destination.exists():
        return destination
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.unlink(missing_ok=True)
    shutil.copy2(source, temporary)
    temporary.replace(destination)
    return destination


def _migrate_legacy_project_layout(projects_root: Path, project_id: str, destination: Path) -> None:
    sources = [destination / LEGACY_DIR_NAME, projects_root / project_id / LEGACY_DIR_NAME]
    legacy_flat_root = projects_root / project_id
    if destination != legacy_flat_root and legacy_flat_root.exists() and not (legacy_flat_root / LEGACY_DIR_NAME).exists():
        sources.append(legacy_flat_root)
    for source in sources:
        if not source.exists() or source == destination:
            continue
        destination.mkdir(parents=True, exist_ok=True)
        _merge_legacy_tree(source, destination)
        _remove_empty_parents(source, projects_root)


def _merge_legacy_tree(source: Path, destination: Path) -> None:
    for child in list(source.iterdir()):
        target = destination / child.name
        if not target.exists():
            shutil.move(str(child), str(target))
            continue
        if child.is_dir() and target.is_dir():
            _merge_legacy_tree(child, target)
            try:
                child.rmdir()
            except OSError:
                pass
            continue
        conflict_root = destination / MIGRATION_CONFLICT_DIR / LEGACY_DIR_NAME
        conflict_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(child), str(unique_path(conflict_root / child.name)))


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _replace_path_prefixes(value: Any, old_roots: list[Path], new_root: Path) -> Any:
    if isinstance(value, str):
        for old_root in old_roots:
            old_prefix = str(old_root)
            if value == old_prefix or value.startswith(f"{old_prefix}{os.sep}"):
                return f"{new_root}{value[len(old_prefix):]}"
        return value
    if isinstance(value, list):
        return [_replace_path_prefixes(item, old_roots, new_root) for item in value]
    if isinstance(value, dict):
        return {key: _replace_path_prefixes(item, old_roots, new_root) for key, item in value.items()}
    return value


def open_project_video_localization_dir(project_id: str) -> dict[str, str]:
    path = project_video_localization_dir(project_id)
    if not path.is_dir():
        raise AppException(410, "VIDEO_LOCALIZATION_PROJECT_DIRECTORY_MISSING", "项目目录已不存在，请刷新历史项目列表")
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return {"status": "opened", "key": "video_localization_project", "path": str(path)}


def clear_project_video_localization_dir(project_id: str) -> None:
    path = project_video_localization_dir(project_id)
    if path.exists():
        shutil.rmtree(path)


def delete_project_video_localization_dir(project_id: str) -> None:
    root = projects_root_dir().resolve()
    path = project_video_localization_dir(project_id)
    if path.is_symlink():
        raise AppException(409, "VIDEO_LOCALIZATION_PROJECT_SYMLINK", "拒绝删除符号链接形式的项目目录")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AppException(409, "VIDEO_LOCALIZATION_PROJECT_PATH_INVALID", "项目目录不在受管理的项目根目录中") from exc
    if resolved == root:
        raise AppException(409, "VIDEO_LOCALIZATION_PROJECT_PATH_INVALID", "拒绝删除项目根目录")
    if resolved.exists():
        shutil.rmtree(resolved)


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


def probe_video(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    stream = (payload.get("streams") or [{}])[0] or {}
    duration = _float_or_none(stream.get("duration")) or _float_or_none((payload.get("format") or {}).get("duration"))
    return {
        "duration_ms": int(duration * 1000) if duration is not None else None,
        "width": _int_or_none(stream.get("width")),
        "height": _int_or_none(stream.get("height")),
        "frame_rate": _frame_rate(stream.get("avg_frame_rate")),
    }


def separate_audio_file(audio_path: Path, stems_dir: Path) -> dict:
    runtime = _load_demucs_runtime()
    outputs = _separate_with_demucs(audio_path, runtime)

    vocals_clean_path = unique_path(stems_dir / f"{audio_path.stem}-vocals-clean.wav")
    background_dest = unique_path(stems_dir / f"{audio_path.stem}-background.wav")
    audio_tools.write_audio(vocals_clean_path, outputs["vocals"], outputs["sample_rate"])
    audio_tools.write_audio(background_dest, outputs["background"], outputs["sample_rate"])
    quality = audio_tools.quality_metrics(vocals_clean_path, min_duration_ms=1000)
    return {
        "vocals_clean_path": vocals_clean_path,
        "background_path": background_dest,
        "engine_id": f"demucs:{outputs['model_name']}",
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


def extract_video_frame(source_path: Path, destination: Path, at_ms: int) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppException(500, "VIDEO_LOCALIZATION_FFMPEG_MISSING", "ffmpeg is required to capture reference covers")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{max(0, at_ms) / 1000:.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size <= 0:
        destination.unlink(missing_ok=True)
        raise AppException(500, "VIDEO_LOCALIZATION_REFERENCE_COVER_FAILED", "Failed to capture reference cover frame")
    return destination


def source_cue_cache_path(cache_dir: Path, source_path: Path, cue: VideoLocalizationCue) -> Path:
    stat = source_path.stat()
    signature = f"{stat.st_size}-{stat.st_mtime_ns}"
    name = f"{_safe_identifier(cue.cue_id)}-{cue.start_ms}-{cue.end_ms}-{signature}-source.wav"
    return cache_dir / name


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "item"


def _float_or_none(value: object) -> float | None:
    try:
        if value in (None, "", "N/A"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    try:
        if value in (None, "", "N/A"):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _frame_rate(value: object) -> float | None:
    if not isinstance(value, str) or value in {"", "0/0", "N/A"}:
        return None
    if "/" not in value:
        return _float_or_none(value)
    numerator, denominator = value.split("/", 1)
    top = _float_or_none(numerator)
    bottom = _float_or_none(denominator)
    if not top or not bottom:
        return None
    return top / bottom


def _load_demucs_runtime() -> dict[str, Any]:
    try:
        import soundfile as sf
        import torch
        import torchaudio.functional as torchaudio_functional
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
    except ImportError as exc:
        raise AppException(
            500,
            "VIDEO_LOCALIZATION_DEMUCS_MISSING",
            "demucs extra is required to separate vocals and background",
        ) from exc
    return {
        "sf": sf,
        "torch": torch,
        "torchaudio_functional": torchaudio_functional,
        "apply_model": apply_model,
        "get_model": get_model,
    }


def _separate_with_demucs(audio_path: Path, runtime: dict[str, Any], model_name: str = "htdemucs") -> dict[str, Any]:
    sf = runtime["sf"]
    torch = runtime["torch"]
    torchaudio_functional = runtime["torchaudio_functional"]
    get_model = runtime["get_model"]
    apply_model = runtime["apply_model"]

    audio, sample_rate = sf.read(str(audio_path), always_2d=True, dtype="float32")
    if audio.size == 0:
        raise AppException(500, "VIDEO_LOCALIZATION_SEPARATION_FAILED", "Source audio is empty")

    model = get_model(model_name)
    model_sample_rate = int(getattr(model, "samplerate", sample_rate) or sample_rate)
    expected_channels = int(getattr(model, "audio_channels", 2) or 2)
    mix = torch.from_numpy(audio.T)
    if sample_rate != model_sample_rate:
        mix = torchaudio_functional.resample(mix, sample_rate, model_sample_rate)
    mix = _match_audio_channels(mix, expected_channels)

    device = _demucs_device(torch)
    try:
        with torch.no_grad():
            separated = apply_model(model, mix[None], device=device, progress=False)
    except Exception as exc:
        raise AppException(500, "VIDEO_LOCALIZATION_SEPARATION_FAILED", "Failed to separate vocals and background") from exc

    sources = list(getattr(model, "sources", []))
    if separated.ndim != 4 or "vocals" not in sources:
        raise AppException(500, "VIDEO_LOCALIZATION_SEPARATION_FAILED", "Failed to separate vocals and background")
    vocals_index = sources.index("vocals")
    vocals = separated[0, vocals_index]
    background = separated[0].sum(dim=0) - vocals
    return {
        "vocals": vocals.detach().cpu().numpy().T,
        "background": background.detach().cpu().numpy().T,
        "sample_rate": model_sample_rate,
        "model_name": model_name,
    }


def _match_audio_channels(mix, expected_channels: int):
    channels = int(mix.shape[0])
    if channels == expected_channels:
        return mix
    if channels == 1 and expected_channels == 2:
        return mix.repeat(2, 1)
    if channels > expected_channels:
        return mix[:expected_channels]
    if channels < expected_channels:
        repeats = (expected_channels + channels - 1) // channels
        return mix.repeat(repeats, 1)[:expected_channels]
    return mix


def _demucs_device(torch) -> str:
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"
