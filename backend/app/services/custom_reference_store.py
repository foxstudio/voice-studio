"""Lifecycle helpers for uploaded reference audio that is not yet durable."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.schemas.voice_studio import VoiceFile
from app.services import database as db, settings_store
from app.services.paths import expand_path


def custom_reference_dir() -> Path:
    return expand_path(settings_store.get().data_dir) / "assets" / "reference-audio" / "custom"


def allocate_path(file_id: str, suffix: str) -> Path:
    root = custom_reference_dir()
    if root.is_symlink():
        raise RuntimeError("Managed custom reference root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    normalized_suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
    return root / f"{file_id}{normalized_suffix}"


def is_managed_custom_path(path: str | Path | None) -> bool:
    if not path:
        return False
    raw_candidate = Path(path).expanduser()
    raw_root = custom_reference_dir()
    if raw_candidate.is_symlink() or raw_root.is_symlink():
        return False
    try:
        candidate = _resolved(raw_candidate)
        root = _resolved(raw_root)
    except (OSError, RuntimeError, ValueError):
        return False
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False
    return len(relative.parts) == 1


def managed_paths_in(value: Any) -> set[Path]:
    paths: set[Path] = set()
    _collect_managed_paths(value, paths)
    return paths


def promote_voice_file(file_id: str) -> VoiceFile | None:
    """Move a temporary upload into the voice library and persist its new path."""

    voice_file = _get_voice_file(file_id)
    if voice_file is None or not _is_owned_voice_file(voice_file):
        return voice_file

    source = _resolved(voice_file.path)
    destination_root = settings_store.voice_dir().expanduser().resolve(strict=False)
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / source.name
    source_media = owned_source_media_path(voice_file)
    destination_media = destination_root / source_media.name if source_media else None

    if not source.exists():
        if destination.exists():
            updated = voice_file.model_copy(
                update={
                    "path": str(destination),
                    "source_media_path": str(destination_media) if destination_media and destination_media.exists() else voice_file.source_media_path,
                }
            )
            db.upsert("voice_files", updated.file_id, updated.model_dump())
            return updated
        raise FileNotFoundError(f"Custom reference audio not found: {source}")
    if destination.exists():
        raise FileExistsError(f"Voice library destination already exists: {destination}")
    if destination_media and destination_media.exists():
        raise FileExistsError(f"Voice library source-media destination already exists: {destination_media}")

    shutil.copy2(source, destination)
    try:
        if source_media and destination_media:
            shutil.copy2(source_media, destination_media)
        updated = voice_file.model_copy(
            update={
                "path": str(destination),
                "source_media_path": str(destination_media) if destination_media else voice_file.source_media_path,
            }
        )
    except Exception:
        destination.unlink(missing_ok=True)
        if destination_media:
            destination_media.unlink(missing_ok=True)
        raise
    try:
        with db.conn() as connection:
            _replace_path_references(connection, str(source), str(destination))
            connection.execute(
                "UPDATE voice_files SET data = ? WHERE file_id = ?",
                (json.dumps(updated.model_dump(), ensure_ascii=False), updated.file_id),
            )
    except Exception:
        destination.unlink(missing_ok=True)
        if destination_media:
            destination_media.unlink(missing_ok=True)
        raise
    source.unlink(missing_ok=True)
    if source_media:
        source_media.unlink(missing_ok=True)
    return updated


def delete_if_unreferenced(
    path: str | Path,
    *,
    task_rows: Iterable[dict] | None = None,
    history_rows: Iterable[dict] | None = None,
    voice_rows: Iterable[dict] | None = None,
) -> str | None:
    """Delete one owned custom upload only when no durable reference remains."""

    voice_file = _voice_file_for_path(path)
    if voice_file is None or not _is_owned_voice_file(voice_file):
        return None
    if is_referenced(
        voice_file,
        task_rows=task_rows,
        history_rows=history_rows,
        voice_rows=voice_rows,
    ):
        return None

    owned_path = _resolved(voice_file.path)
    owned_path.unlink(missing_ok=True)
    source_media = owned_source_media_path(voice_file)
    if source_media:
        source_media.unlink(missing_ok=True)
    db.delete_one("voice_files", "file_id", voice_file.file_id)
    return voice_file.file_id


def is_referenced(
    voice_file: VoiceFile,
    *,
    task_rows: Iterable[dict] | None = None,
    history_rows: Iterable[dict] | None = None,
    voice_rows: Iterable[dict] | None = None,
) -> bool:
    tasks = list(task_rows) if task_rows is not None else db.list_all("tasks", "created_at", limit=-1)
    history = list(history_rows) if history_rows is not None else db.list_all("history", "created_at", limit=-1)
    voices = list(voice_rows) if voice_rows is not None else db.list_all("voices", "updated_at", limit=-1)

    if any(_value_references(row.get("parameters", {}), voice_file) for row in tasks):
        return True
    if any(_value_references(row.get("parameter_snapshot", {}), voice_file) for row in history):
        return True
    for table, order_field in (
        ("longform_tasks", "created_at"),
        ("batches", "created_at"),
        ("presets", "updated_at"),
        ("transcriptions", "created_at"),
        ("asr_tasks", "created_at"),
        ("projects", "updated_at"),
        ("exports", "created_at"),
    ):
        if any(_value_references(row, voice_file) for row in db.list_all(table, order_field, limit=-1)):
            return True
    return any(voice_file.file_id in row.get("reference_audio_ids", []) for row in voices)


def cleanup_orphaned_uploads(
    *,
    ttl_seconds: float,
    now: datetime | None = None,
    task_rows: Iterable[dict] | None = None,
    history_rows: Iterable[dict] | None = None,
    voice_rows: Iterable[dict] | None = None,
) -> list[str]:
    """Explicit TTL cleanup for uploads that were never submitted or registered."""

    if ttl_seconds < 0:
        raise ValueError("TTL must not be negative")
    current = now or datetime.now()
    tasks = list(task_rows) if task_rows is not None else None
    history = list(history_rows) if history_rows is not None else None
    voices = list(voice_rows) if voice_rows is not None else None
    deleted: list[str] = []

    for row in db.list_all("voice_files", "created_at", limit=-1):
        voice_file = VoiceFile(**row)
        if not _is_owned_voice_file(voice_file) or not _older_than_ttl(voice_file.created_at, current, ttl_seconds):
            continue
        deleted_id = delete_if_unreferenced(
            voice_file.path,
            task_rows=tasks,
            history_rows=history,
            voice_rows=voices,
        )
        if deleted_id:
            deleted.append(deleted_id)
    return deleted


def _get_voice_file(file_id: str) -> VoiceFile | None:
    row = db.get_one("voice_files", "file_id", file_id)
    return VoiceFile(**row) if row else None


def _voice_file_for_path(path: str | Path) -> VoiceFile | None:
    if not is_managed_custom_path(path):
        return None
    candidate = _resolved(path)
    voice_file = _get_voice_file(candidate.stem)
    if voice_file is None or _resolved(voice_file.path) != candidate:
        return None
    return voice_file


def _is_owned_voice_file(voice_file: VoiceFile) -> bool:
    if not is_managed_custom_path(voice_file.path):
        return False
    return _resolved(voice_file.path).stem == voice_file.file_id


def owned_source_media_path(voice_file: VoiceFile) -> Path | None:
    """Return the paired source video only when it belongs to this voice file.

    The pair can live in the temporary custom-upload directory or, after
    registration, in the durable voice-library directory.  Callers must still
    verify the audio file belongs to the directory they are allowed to delete.
    """

    if not voice_file.source_media_path:
        return None
    candidate = _resolved(voice_file.source_media_path)
    audio_path = _resolved(voice_file.path)
    if candidate.parent != audio_path.parent or candidate.stem != voice_file.file_id:
        return None
    return candidate


def _collect_managed_paths(value: Any, output: set[Path]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_managed_paths(item, output)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_managed_paths(item, output)
    elif isinstance(value, str) and is_managed_custom_path(value):
        output.add(_resolved(value))


def _value_references(value: Any, voice_file: VoiceFile) -> bool:
    if isinstance(value, dict):
        return any(_value_references(item, voice_file) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_value_references(item, voice_file) for item in value)
    if not isinstance(value, str):
        return False
    if value == voice_file.file_id:
        return True
    return is_managed_custom_path(value) and _resolved(value) == _resolved(voice_file.path)


def _replace_path_references(connection: Any, old_path: str, new_path: str) -> None:
    for table, key_field in (
        ("tasks", "task_id"),
        ("history", "result_id"),
        ("longform_tasks", "longform_task_id"),
        ("batches", "batch_task_id"),
        ("presets", "preset_id"),
        ("transcriptions", "transcription_id"),
        ("asr_tasks", "task_id"),
        ("projects", "project_id"),
        ("exports", "export_id"),
    ):
        rows = connection.execute(f"SELECT {key_field}, data FROM {table}").fetchall()
        for row in rows:
            value = json.loads(row["data"])
            replaced, changed = _replace_exact_string(value, old_path, new_path)
            if changed:
                connection.execute(
                    f"UPDATE {table} SET data = ? WHERE {key_field} = ?",
                    (json.dumps(replaced, ensure_ascii=False), row[key_field]),
                )


def _replace_exact_string(value: Any, old: str, new: str) -> tuple[Any, bool]:
    if isinstance(value, dict):
        changed = False
        result = {}
        for key, item in value.items():
            replacement, item_changed = _replace_exact_string(item, old, new)
            result[key] = replacement
            changed = changed or item_changed
        return result, changed
    if isinstance(value, list):
        changed = False
        result = []
        for item in value:
            replacement, item_changed = _replace_exact_string(item, old, new)
            result.append(replacement)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, tuple):
        replaced, changed = _replace_exact_string(list(value), old, new)
        return tuple(replaced), changed
    if isinstance(value, str) and value == old:
        return new, True
    return value, False


def _older_than_ttl(created_at: str, now: datetime, ttl_seconds: float) -> bool:
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return False
    comparison_now = now
    if created.tzinfo is not None and comparison_now.tzinfo is None:
        comparison_now = comparison_now.replace(tzinfo=created.tzinfo)
    elif created.tzinfo is None and comparison_now.tzinfo is not None:
        comparison_now = comparison_now.replace(tzinfo=None)
    return created <= comparison_now - timedelta(seconds=ttl_seconds)


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)
