from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, ContextManager

from fastapi import UploadFile

from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationCue
from app.services import (
    audio_tools,
    durable_files,
    settings_store,
    stem_separation_engine,
)
from app.services.keyed_lock_registry import KeyedLockRegistry

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
PROJECT_DIR_NAME_KEY = "video_localization_dir_name"
LEGACY_DIR_NAME = "video_localization"
MIGRATION_CONFLICT_DIR = "migration-conflicts"
AUDIO_PREVIEW_PROFILE = "audio-aac-128k-v1"
VISUAL_EVIDENCE_THUMBNAIL_PROFILE = "jpeg-w320-q72-v1"
PROJECT_PATH_PREFIX = "project://"


_AUDIO_PREVIEW_LOCKS = KeyedLockRegistry[str]()
_VISUAL_EVIDENCE_THUMBNAIL_LOCKS = KeyedLockRegistry[str]()
_PROJECT_DIRECTORY_NAME_LOCKS = KeyedLockRegistry[str]()
_PROJECT_DIRECTORY_NAMES: dict[str, str] = {}
_PROJECT_DIRECTORY_NAMES_GUARD = threading.Lock()
_PROJECT_MEDIA_PATHS: dict[str, dict[str, Path | None]] = {}
_PROJECT_MEDIA_PATHS_GUARD = threading.Lock()
_PROJECT_TIMELINE_AUDIO_PATHS: dict[str, dict[str, Path]] = {}
_PROJECT_TIMELINE_AUDIO_PATHS_GUARD = threading.Lock()
_PROCESS_POLL_SECONDS = 0.1
_PROCESS_TERMINATE_GRACE_SECONDS = 2.0
_PROBE_TIMEOUT_SECONDS = 15.0
_PROXY_TIMEOUT_SECONDS = 30 * 60.0
_PLAYBACK_SEGMENT_TIMEOUT_SECONDS = 180.0


def cache_project_directory_name(project_id: str, directory_name: str) -> str:
    with _PROJECT_DIRECTORY_NAMES_GUARD:
        _PROJECT_DIRECTORY_NAMES[project_id] = directory_name
    return directory_name


def invalidate_project_directory_name(project_id: str) -> None:
    with _PROJECT_DIRECTORY_NAMES_GUARD:
        _PROJECT_DIRECTORY_NAMES.pop(project_id, None)


def cached_project_media_paths(project_id: str) -> dict[str, Path | None] | None:
    with _PROJECT_MEDIA_PATHS_GUARD:
        paths = _PROJECT_MEDIA_PATHS.get(project_id)
        return dict(paths) if paths is not None else None


def cache_project_media_paths(project_id: str, paths: dict[str, Path | None]) -> dict[str, Path | None]:
    with _PROJECT_MEDIA_PATHS_GUARD:
        _PROJECT_MEDIA_PATHS[project_id] = dict(paths)
    return paths


def invalidate_project_media_paths(project_id: str) -> None:
    with _PROJECT_MEDIA_PATHS_GUARD:
        _PROJECT_MEDIA_PATHS.pop(project_id, None)


def cached_project_timeline_audio_paths(project_id: str) -> dict[str, Path] | None:
    with _PROJECT_TIMELINE_AUDIO_PATHS_GUARD:
        paths = _PROJECT_TIMELINE_AUDIO_PATHS.get(project_id)
        return dict(paths) if paths is not None else None


def cache_project_timeline_audio_paths(
    project_id: str,
    draft: Any,
    *,
    resolved_media: dict[str, Path | None] | None = None,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for item in getattr(draft, "timeline_clips", []):
        clip = dict(item)
        value = clip.get("audio_path")
        if not value:
            continue
        path = Path(str(value))
        clip_id = str(clip.get("clip_id") or "")
        media_source_clip_id = str(clip.get("media_source_clip_id") or "")
        if clip_id:
            paths[clip_id] = path
        if media_source_clip_id:
            paths[media_source_clip_id] = path
    # Media clips can retain an obsolete path after a source replacement or
    # project-directory migration. The draft's current media fields are the
    # authoritative sources for these stable track IDs.
    for clip_id, value in (
        ("media_original", (resolved_media or {}).get("source_audio")),
        ("media_vocals", (resolved_media or {}).get("vocals")),
        ("media_background", (resolved_media or {}).get("background")),
    ):
        if value:
            paths[clip_id] = Path(str(value))
    with _PROJECT_TIMELINE_AUDIO_PATHS_GUARD:
        _PROJECT_TIMELINE_AUDIO_PATHS[project_id] = dict(paths)
    return paths


def invalidate_project_timeline_audio_paths(project_id: str) -> None:
    with _PROJECT_TIMELINE_AUDIO_PATHS_GUARD:
        _PROJECT_TIMELINE_AUDIO_PATHS.pop(project_id, None)


async def save_uploaded_video(project_id: str, file: UploadFile) -> tuple[Path, int, str]:
    filename = file.filename or "source.mp4"
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise AppException(400, "VIDEO_LOCALIZATION_UNSUPPORTED_MEDIA", "Only mp4, mov, m4v, webm, and mkv videos are supported")

    settings_store.ensure_directories()
    source_dir = ensure_project_video_localization_dir(project_id) / "source"
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


def recover_missing_project_media_locators(project_id: str, draft: Any) -> Any:
    """Recover absent managed-media paths from exact stored fingerprints.

    This is a read repair only: it returns an enriched draft without writing the
    database or project snapshot.  Only regular files inside the canonical
    ``source``, ``audio`` and ``stems`` directories are considered.  Ambiguous
    or unverifiable matches remain absent so a wrong file is never guessed.
    """

    package_root = project_video_localization_dir(project_id)
    if not package_root.is_dir() or package_root.is_symlink():
        return draft

    source_media = draft.source_media
    stems = draft.stems
    roles = (
        (
            "source_video",
            source_media.video_path,
            source_media.content_sha256,
            ("source",),
            VIDEO_EXTENSIONS,
        ),
        (
            "source_audio",
            source_media.audio_path,
            source_media.audio_sha256,
            ("audio", "stems"),
            None,
        ),
        (
            "original_audio",
            stems.original_audio_path,
            stems.original_audio_sha256,
            ("audio", "stems"),
            None,
        ),
        (
            "vocals",
            stems.vocals_clean_path,
            stems.vocals_clean_sha256,
            ("stems",),
            None,
        ),
        (
            "background",
            stems.background_path,
            stems.background_sha256,
            ("stems",),
            None,
        ),
    )
    recovered: dict[str, str] = {}
    for role, configured, expected_sha256, directory_names, extensions in roles:
        expected = str(expected_sha256 or "").strip().lower()
        if configured or not re.fullmatch(r"[0-9a-f]{64}", expected):
            continue
        candidates: list[Path] = []
        for directory_name in directory_names:
            directory = package_root / directory_name
            if not directory.is_dir() or directory.is_symlink():
                continue
            for candidate in directory.iterdir():
                if extensions is not None and candidate.suffix.lower() not in extensions:
                    continue
                managed = managed_project_file(project_id, candidate)
                if managed is not None and managed not in candidates:
                    candidates.append(managed)
        matches: list[Path] = []
        for candidate in candidates:
            try:
                if file_sha256(candidate).lower() == expected:
                    matches.append(candidate)
            except OSError:
                # A file can disappear between directory enumeration and the
                # read. Recovery is best-effort and must never break Draft reads.
                continue
        if len(matches) == 1:
            recovered[role] = str(matches[0])

    if not recovered:
        return draft
    next_source_media = source_media.model_copy(
        update={
            **(
                {"video_path": recovered["source_video"]}
                if "source_video" in recovered
                else {}
            ),
            **(
                {"audio_path": recovered["source_audio"]}
                if "source_audio" in recovered
                else {}
            ),
        }
    )
    next_stems = stems.model_copy(
        update={
            **(
                {"original_audio_path": recovered["original_audio"]}
                if "original_audio" in recovered
                else {}
            ),
            **(
                {"vocals_clean_path": recovered["vocals"]}
                if "vocals" in recovered
                else {}
            ),
            **(
                {"background_path": recovered["background"]}
                if "background" in recovered
                else {}
            ),
        }
    )
    return draft.model_copy(
        update={
            "source_media": next_source_media,
            "stems": next_stems,
        }
    )


def project_video_localization_dir(project_id: str) -> Path:
    """Resolve the indexed package path without creating, moving, or persisting."""

    return projects_root_dir() / _load_project_dir_name(project_id)


def managed_project_file(
    project_id: str,
    value: str | Path | None,
) -> Path | None:
    """Resolve one readable regular file confined to the indexed project package.

    Draft path strings are persistence details, not authorization. Public
    readers must pass through this boundary so an injected absolute path or a
    symlink cannot escape the project package.
    """

    if value is None or not str(value).strip():
        return None
    directory_name = _stored_project_dir_name(project_id)
    if (
        not directory_name
        or directory_name in {".", ".."}
        or Path(directory_name).name != directory_name
    ):
        return None
    projects_root = projects_root_dir().resolve(strict=False)
    package_path = projects_root / directory_name
    if package_path.is_symlink():
        return None
    package_root = package_path.resolve(strict=False)
    try:
        package_root.relative_to(projects_root)
    except ValueError:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = package_root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(package_root)
    except (OSError, RuntimeError, ValueError):
        return None
    cursor = candidate
    while cursor != package_root:
        if cursor.is_symlink():
            return None
        parent = cursor.parent
        if parent == cursor:
            return None
        cursor = parent
    try:
        return resolved if resolved.is_file() and os.access(resolved, os.R_OK) else None
    except OSError:
        return None


def ensure_project_video_localization_dir(project_id: str) -> Path:
    """Prepare storage for an explicit write or repair command."""

    settings_store.ensure_directories()
    projects_root = projects_root_dir()
    directory_name = _load_project_dir_name(project_id)
    destination = projects_root / directory_name
    _migrate_legacy_project_layout(projects_root, project_id, destination)
    return destination


def visual_evidence_frame_dir(
    project_id: str,
    operation_id: str,
) -> Path:
    """Return the isolated formal evidence directory for one ASR run."""

    value = str(operation_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid ASR operation ID")
    return (
        project_video_localization_dir(project_id)
        / "visual-evidence"
        / value
        / "frames"
    )


def visual_evidence_thumbnail(source_path: Path) -> Path:
    """Return a cached small reader preview without changing the evidence frame."""

    source_path = Path(source_path)
    if not source_path.is_file():
        return source_path
    destination = (
        source_path.parent
        / ".thumbnails"
        / f"{source_path.stem}-{VISUAL_EVIDENCE_THUMBNAIL_PROFILE}.jpg"
    )
    if _usable_thumbnail(destination, source_path):
        return destination

    with _VISUAL_EVIDENCE_THUMBNAIL_LOCKS.hold(str(destination)):
        if _usable_thumbnail(destination, source_path):
            return destination
        try:
            from PIL import Image
        except ImportError:
            return source_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.stem}.{os.getpid()}."
            f"{threading.get_ident()}.part.jpg"
        )
        temporary.unlink(missing_ok=True)
        try:
            try:
                with Image.open(source_path) as image:
                    image.thumbnail(
                        (320, 320),
                        Image.Resampling.LANCZOS,
                    )
                    if image.mode != "RGB":
                        image = image.convert("RGB")
                    image.save(
                        temporary,
                        format="JPEG",
                        quality=72,
                        optimize=True,
                    )
            except (OSError, ValueError):
                return source_path
            if temporary.is_file() and temporary.stat().st_size > 0:
                temporary.replace(destination)
                return destination
        finally:
            temporary.unlink(missing_ok=True)
    # A preview failure must not make the original evidence unavailable.
    return source_path


def _usable_thumbnail(destination: Path, source_path: Path) -> bool:
    try:
        return (
            destination.is_file()
            and destination.stat().st_size > 0
            and destination.stat().st_mtime_ns
            >= source_path.stat().st_mtime_ns
        )
    except OSError:
        return False


def project_video_localization_dir_for_name(project_id: str, project_name: str) -> Path:
    return projects_root_dir() / project_dir_name(project_id, project_name)


def projects_root_dir() -> Path:
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
    with _PROJECT_DIRECTORY_NAMES_GUARD:
        cached = _PROJECT_DIRECTORY_NAMES.get(project_id)
    if cached is not None:
        return cached
    with _PROJECT_DIRECTORY_NAME_LOCKS.hold(project_id):
        with _PROJECT_DIRECTORY_NAMES_GUARD:
            cached = _PROJECT_DIRECTORY_NAMES.get(project_id)
        if cached is not None:
            return cached
        return cache_project_directory_name(
            project_id,
            _load_project_dir_name(project_id),
        )


def _load_project_dir_name(project_id: str) -> str:
    try:
        from app.services import project_store

        locator = project_store.get_video_localization_directory_locator(
            project_id
        )
    except Exception:
        locator = None
    if locator is None:
        return project_id
    stored_name, project_name = locator
    return stored_name or project_dir_name(project_id, project_name)


def legacy_project_roots(project_id: str) -> list[Path]:
    projects_root = settings_store.expand_path(settings_store.get().project_dir)
    destination = projects_root / _load_project_dir_name(project_id)
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


def rebase_paths_between_project_roots(
    value: Any,
    *,
    previous_root: Path,
    current_root: Path,
) -> Any:
    """Rebase managed paths after a project package is moved externally."""

    if previous_root == current_root:
        return value
    return _replace_path_prefixes(value, [previous_root], current_root)


def cleanup_unreferenced_stems(project_id: str, referenced_paths: list[str | None]) -> list[Path]:
    stems_dir = ensure_project_video_localization_dir(project_id) / "stems"
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


def automatic_reference_clip_path(
    project_id: str,
    operation_id: str,
    reference_clip_id: str,
) -> Path:
    """Derive one bounded, operation-owned automatic reference path."""

    normalized_operation_id = _safe_media_identity(
        operation_id,
        "operation ID",
    )
    normalized_reference_id = str(
        reference_clip_id or ""
    ).strip()
    if not normalized_reference_id:
        raise ValueError("reference clip ID must not be empty")
    reference_digest = hashlib.sha256(
        normalized_reference_id.encode("utf-8")
    ).hexdigest()[:20]
    return (
        project_video_localization_dir(project_id)
        / "references"
        / (
            f"auto-ref-{normalized_operation_id}-"
            f"{reference_digest}.wav"
        )
    )


def cleanup_unreferenced_automatic_reference_clips(
    project_id: str,
    referenced_paths: list[str | None],
) -> list[Path]:
    references_dir = (
        project_video_localization_dir(project_id)
        / "references"
    )
    if not references_dir.exists():
        return []
    referenced = {
        Path(value).resolve()
        for value in referenced_paths
        if value
    }
    removed: list[Path] = []
    for path in references_dir.glob("auto-ref-*.wav"):
        if (
            not path.is_file()
            or path.resolve() in referenced
        ):
            continue
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def _safe_media_identity(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789-_"
            for character in normalized
        )
    ):
        raise ValueError(f"{label} is not safe for a media filename")
    return normalized


def cleanup_unreferenced_tts(
    project_id: str,
    referenced_paths: list[str | None],
    *,
    candidate_paths: list[str | None] | None = None,
) -> list[Path]:
    tts_dir = ensure_project_video_localization_dir(project_id) / "tts"
    if not tts_dir.exists() or tts_dir.is_symlink():
        return []
    tts_root = tts_dir.resolve()
    referenced = {Path(value).resolve() for value in referenced_paths if value}
    candidates = tts_dir.rglob("*") if candidate_paths is None else (Path(value) for value in candidate_paths if value)
    removed: list[Path] = []
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(tts_root)
        except ValueError:
            continue
        if resolved in referenced:
            continue
        path.unlink(missing_ok=True)
        removed.append(path)
    for directory in sorted(
        (path for path in tts_dir.rglob("*") if path.is_dir() and not path.is_symlink()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed


def adopt_tts_audio(project_id: str, source_path: Path, cue_id: str, identity: str) -> Path:
    root = ensure_project_video_localization_dir(project_id).resolve()
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
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
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
        if value.startswith(PROJECT_PATH_PREFIX):
            relative = value[len(PROJECT_PATH_PREFIX):]
            if relative in {"", "."}:
                return str(new_root.resolve())
            if "\\" in relative or Path(relative).is_absolute():
                raise ValueError("Unsafe project-relative path")
            resolved = (new_root / relative).resolve()
            try:
                resolved.relative_to(new_root.resolve())
            except ValueError as exc:
                raise ValueError(
                    "Project-relative path escapes project root"
                ) from exc
            return str(resolved)
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
    path = ensure_project_video_localization_dir(project_id)
    if path.exists():
        shutil.rmtree(path)


def stage_project_video_localization_dir(
    *,
    directory_name: str,
    cleanup_id: str,
) -> Path | None:
    """Atomically move one managed package outside package discovery."""

    root = projects_root_dir().resolve()
    source = (root / directory_name).resolve(strict=False)
    if (
        not directory_name
        or Path(directory_name).name != directory_name
        or source.parent != root
        or source == root
        or source.is_symlink()
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_PROJECT_PATH_INVALID",
            "项目目录不在受管理的项目根目录中",
        )
    trash_root = root / ".trash"
    trash_root.mkdir(parents=True, exist_ok=True)
    destination = trash_root / cleanup_id
    if destination.exists():
        return destination
    if not source.exists():
        return None
    durable_files.durable_replace(source, destination)
    return destination


def purge_staged_project_video_localization_dir(
    cleanup_id: str,
) -> None:
    root = projects_root_dir().resolve()
    trash_root = (root / ".trash").resolve(strict=False)
    destination = (trash_root / cleanup_id).resolve(strict=False)
    if (
        not cleanup_id
        or Path(cleanup_id).name != cleanup_id
        or destination.parent != trash_root
    ):
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_PROJECT_PATH_INVALID",
            "项目清理目录不在受管理的回收区中",
        )
    if destination.is_symlink():
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_PROJECT_SYMLINK",
            "拒绝删除符号链接形式的项目清理目录",
        )
    if destination.exists():
        shutil.rmtree(destination)
        durable_files.fsync_directory(trash_root)


def delete_project_video_localization_dir(project_id: str) -> None:
    root = projects_root_dir().resolve()
    path = ensure_project_video_localization_dir(project_id)
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
    if result.returncode != 0 or not audio_path.is_file():
        audio_path.unlink(missing_ok=True)
        raise AppException(500, "VIDEO_LOCALIZATION_AUDIO_EXTRACT_FAILED", "Failed to extract source audio")
    return audio_tools.probe_audio(audio_path)


def build_playback_proxy_segment(
    source_path: Path,
    destination: Path,
    *,
    start_ms: int,
    duration_ms: int,
    cancel_event: threading.Event,
) -> None:
    """Create one independently decodable, video-only fMP4 cache segment."""

    if not source_path.is_file():
        raise AppException(
            404,
            "VIDEO_LOCALIZATION_SOURCE_VIDEO_NOT_FOUND",
            "Source video file not found",
        )
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppException(
            500,
            "VIDEO_LOCALIZATION_FFMPEG_MISSING",
            "ffmpeg is required to create playback segments",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.stem}.{os.getpid()}.{threading.get_ident()}.part.mp4"
    )
    temporary.unlink(missing_ok=True)
    errors: list[str] = []
    try:
        for encoder in ("h264_videotoolbox", "libx264"):
            if cancel_event.is_set():
                raise InterruptedError("playback segment creation cancelled")
            temporary.unlink(missing_ok=True)
            result = _run_bounded_process(
                playback_proxy_segment_command(
                    ffmpeg,
                    source_path,
                    temporary,
                    encoder=encoder,
                    start_ms=start_ms,
                    duration_ms=duration_ms,
                ),
                timeout_seconds=max(
                    30.0,
                    min(
                        _PLAYBACK_SEGMENT_TIMEOUT_SECONDS,
                        duration_ms / 1000 * 12,
                    ),
                ),
                cancel_event=cancel_event,
            )
            if (
                result.returncode == 0
                and temporary.is_file()
                and temporary.stat().st_size > 0
            ):
                temporary.replace(destination)
                return
            errors.append((result.stderr or result.stdout or encoder)[-1200:])
        raise AppException(
            500,
            "VIDEO_LOCALIZATION_PLAYBACK_SEGMENT_FAILED",
            "Failed to create a browser playback segment",
            detail={"ffmpeg": "\n".join(errors)},
        )
    finally:
        temporary.unlink(missing_ok=True)


def playback_proxy_segment_command(
    ffmpeg: str,
    source_path: Path,
    destination: Path,
    *,
    encoder: str,
    start_ms: int,
    duration_ms: int,
) -> list[str]:
    """Build the deterministic ffmpeg command used by the playback cache."""

    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0, start_ms) / 1000:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{max(1, duration_ms) / 1000:.3f}",
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
        "-c:v",
        encoder,
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level:v",
        "4.0",
        "-tag:v",
        "avc1",
        "-force_key_frames",
        "expr:gte(t,n_forced*4)",
    ]
    if encoder == "h264_videotoolbox":
        command.extend(
            ["-b:v", "2500k", "-maxrate", "4M", "-bufsize", "6M", "-realtime", "true"]
        )
    else:
        command.extend(["-preset", "veryfast", "-crf", "23", "-sc_threshold", "0"])
    command.extend(
        [
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+frag_keyframe+empty_moov+default_base_moof",
            str(destination),
        ]
    )
    return command


def audio_preview_proxy_path(project_id: str, source_path: Path) -> Path:
    stat = source_path.stat()
    source_identity = hashlib.sha256(str(source_path.resolve()).encode()).hexdigest()[:10]
    signature = hashlib.sha256(
        f"{source_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{AUDIO_PREVIEW_PROFILE}".encode()
    ).hexdigest()[:16]
    stem = _safe_identifier(source_path.stem)[:48]
    return project_video_localization_dir(project_id) / "preview" / "audio" / f"{stem}-{source_identity}-{signature}.m4a"


def existing_audio_preview_proxy(project_id: str, source_path: Path) -> Path | None:
    try:
        candidate = audio_preview_proxy_path(project_id, source_path)
    except OSError:
        return None
    return candidate if candidate.is_file() and candidate.stat().st_size > 0 else None


def clear_audio_preview_proxies(project_id: str) -> None:
    preview_dir = (
        ensure_project_video_localization_dir(project_id)
        / "preview"
        / "audio"
    )
    if preview_dir.exists():
        shutil.rmtree(preview_dir, ignore_errors=True)


def preview_proxy_storage_stats(projects_root: Path | None = None) -> dict[str, int | bool]:
    roots = _preview_proxy_roots(projects_root or projects_root_dir())
    size_bytes = 0
    file_count = 0
    truncated = False
    for root in roots:
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_symlink():
                                truncated = True
                            elif entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                size_bytes += entry.stat(follow_symlinks=False).st_size
                                file_count += 1
                        except OSError:
                            truncated = True
            except OSError:
                truncated = True
    return {
        "size_bytes": size_bytes,
        "file_count": file_count,
        "truncated": truncated,
    }


def clear_all_preview_proxies(projects_root: Path | None = None) -> dict[str, int | str]:
    root = projects_root or projects_root_dir()
    before = preview_proxy_storage_stats(root)
    for preview_root in _preview_proxy_roots(root):
        shutil.rmtree(preview_root, ignore_errors=True)
    after = preview_proxy_storage_stats(root)
    return {
        "path": str(root),
        "before_bytes": int(before["size_bytes"]),
        "after_bytes": int(after["size_bytes"]),
        "removed_bytes": max(0, int(before["size_bytes"]) - int(after["size_bytes"])),
        "before_files": int(before["file_count"]),
        "after_files": int(after["file_count"]),
    }


def _preview_proxy_roots(projects_root: Path) -> list[Path]:
    if not projects_root.exists() or not projects_root.is_dir() or projects_root.is_symlink():
        return []
    roots: list[Path] = []
    try:
        entries = list(projects_root.iterdir())
    except OSError:
        return []
    for project_root in entries:
        if not project_root.is_dir() or project_root.is_symlink():
            continue
        preview_root = project_root / "preview"
        if preview_root.is_dir() and not preview_root.is_symlink():
            roots.append(preview_root)
    return roots


def ensure_audio_preview_proxy(
    project_id: str,
    source_path: Path,
    *,
    cancel_event: threading.Event | None = None,
) -> Path:
    if not source_path.is_file():
        raise AppException(404, "VIDEO_LOCALIZATION_AUDIO_NOT_FOUND", "Audio file not found")
    if source_path.suffix.lower() in {".m4a", ".mp3", ".aac", ".ogg", ".opus"}:
        return source_path

    # Long PCM stems are excellent editing masters but expensive browser seek targets.
    destination = audio_preview_proxy_path(project_id, source_path)
    existing = existing_audio_preview_proxy(project_id, source_path)
    if existing:
        return existing
    with _audio_preview_lock(destination):
        existing = existing_audio_preview_proxy(project_id, source_path)
        if existing:
            return existing
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise AppException(500, "VIDEO_LOCALIZATION_FFMPEG_MISSING", "ffmpeg is required to create the audio preview proxy")
        destination.parent.mkdir(parents=True, exist_ok=True)
        _remove_stale_proxy_parts(destination.parent, f".{destination.stem}.*.part.m4a")
        temporary = destination.with_name(f".{destination.stem}.{os.getpid()}.{threading.get_ident()}.part.m4a")
        temporary.unlink(missing_ok=True)
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        try:
            result = _run_bounded_process(
                command,
                timeout_seconds=_PROXY_TIMEOUT_SECONDS,
                cancel_event=cancel_event,
            )
            if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
                raise AppException(
                    500,
                    "VIDEO_LOCALIZATION_AUDIO_PREVIEW_FAILED",
                    "Failed to create the audio preview proxy",
                    detail={"ffmpeg": (result.stderr or result.stdout or "ffmpeg failed")[-1200:]},
                )
            temporary.replace(destination)
            _remove_stale_audio_preview_proxies(destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)


def _audio_preview_lock(destination: Path) -> ContextManager[None]:
    return _AUDIO_PREVIEW_LOCKS.hold(str(destination))


def _remove_stale_audio_preview_proxies(current: Path) -> None:
    prefix = current.stem.rsplit("-", 1)[0]
    for candidate in current.parent.glob(f"{prefix}-*.m4a"):
        if candidate != current:
            candidate.unlink(missing_ok=True)


def _remove_stale_proxy_parts(parent: Path, pattern: str) -> None:
    if parent.is_symlink():
        return
    for candidate in parent.glob(pattern):
        if candidate.is_file() and not candidate.is_symlink():
            candidate.unlink(missing_ok=True)


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
        "stream=codec_name,width,height,avg_frame_rate,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = _run_bounded_process(command, timeout_seconds=_PROBE_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, InterruptedError):
        return {}
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
        "codec_name": str(stream.get("codec_name") or "") or None,
    }


def _run_bounded_process(
    command: list[str],
    *,
    timeout_seconds: float,
    cancel_event: threading.Event | None = None,
) -> subprocess.CompletedProcess[str]:
    if cancel_event is None:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while process.poll() is None:
        if cancel_event.wait(_PROCESS_POLL_SECONDS):
            _terminate_media_process(process)
            stdout, stderr = process.communicate()
            raise InterruptedError((stderr or stdout or "media process cancelled")[-1200:])
        if time.monotonic() >= deadline:
            timed_out = True
            _terminate_media_process(process)
            break
    stdout, stderr = process.communicate()
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout_seconds, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(command, int(process.returncode or 0), stdout, stderr)


def _terminate_media_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=_PROCESS_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def stem_separation_output_paths(
    project_id: str,
    operation_id: str,
) -> tuple[Path, Path]:
    """Return stable, operation-owned output paths without creating files."""

    normalized_operation_id = str(operation_id or "").strip()
    if (
        not normalized_operation_id
        or len(normalized_operation_id) > 128
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789-_"
            for character in normalized_operation_id
        )
    ):
        raise ValueError("operation ID is not safe for a media filename")
    stems_dir = (
        project_video_localization_dir(project_id) / "stems"
    )
    return (
        stems_dir
        / f"{normalized_operation_id}-vocals-clean.wav",
        stems_dir
        / f"{normalized_operation_id}-background.wav",
    )


def separate_audio_file(
    audio_path: Path,
    stems_dir: Path,
    *,
    output_prefix: str | None = None,
) -> dict:
    if output_prefix is None:
        vocals_clean_path = unique_path(
            stems_dir / f"{audio_path.stem}-vocals-clean.wav"
        )
        background_dest = unique_path(
            stems_dir / f"{audio_path.stem}-background.wav"
        )
    else:
        normalized_prefix = str(output_prefix or "").strip()
        if (
            not normalized_prefix
            or len(normalized_prefix) > 128
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789-_"
                for character in normalized_prefix
            )
        ):
            raise ValueError(
                "stem separation output prefix is invalid"
            )
        vocals_clean_path = (
            stems_dir / f"{normalized_prefix}-vocals-clean.wav"
        )
        background_dest = (
            stems_dir / f"{normalized_prefix}-background.wav"
        )
        vocals_clean_path.unlink(missing_ok=True)
        background_dest.unlink(missing_ok=True)
    try:
        settings = settings_store.get()
        separation = stem_separation_engine.separate(
            audio_path,
            vocals_clean_path,
            background_dest,
            overlap=settings.stem_separation_overlap,
            chunk_duration_seconds=(
                settings.stem_separation_chunk_duration_seconds
            ),
        )
        quality = audio_tools.quality_metrics(
            vocals_clean_path,
            min_duration_ms=1000,
        )
    except Exception:
        if output_prefix is not None:
            vocals_clean_path.unlink(missing_ok=True)
            background_dest.unlink(missing_ok=True)
        raise
    return {
        "vocals_clean_path": vocals_clean_path,
        "background_path": background_dest,
        "engine_id": separation["engine_id"],
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
    if result.returncode != 0 or not destination.is_file():
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
    if (
        result.returncode != 0
        or not destination.is_file()
        or destination.stat().st_size <= 0
    ):
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
