from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from app.schemas.history_scope import (
    history_scope_values,
)
from app.schemas.voice_studio import HistoryItem
from app.services import custom_reference_store, database as db, waveform_cache


_HISTORY_WRITE_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryDeleteReport:
    removed_records: int
    removed_files: int
    cleanup_failures: int


def add(item: HistoryItem) -> HistoryItem:
    with db.conn() as connection:
        add_from_connection(connection, item)
    return item


def add_from_connection(
    connection: Connection,
    item: HistoryItem,
) -> HistoryItem:
    """Persist history inside a caller-owned SQLite transaction."""

    with _HISTORY_WRITE_LOCK:
        db.upsert_from_connection(
            connection,
            "history",
            item.result_id,
            item.model_dump(),
        )
        _replace_history_read_index(connection, item)
    return item


def _replace_history_read_index(
    connection: Connection,
    item: HistoryItem,
) -> None:
    """Maintain the normalized query model in the history transaction."""

    parameters = item.parameter_snapshot or {}
    source = parameters.get("source") if isinstance(parameters, dict) else None
    connection.execute(
        """
        INSERT INTO history_metadata (
            result_id,
            project_id,
            source,
            created_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(result_id) DO UPDATE SET
            project_id = excluded.project_id,
            source = excluded.source,
            created_at = excluded.created_at
        """,
        (item.result_id, item.project_id, source, item.created_at),
    )
    connection.execute(
        "DELETE FROM history_scopes WHERE result_id = ?",
        (item.result_id,),
    )
    connection.executemany(
        """
        INSERT INTO history_scopes (result_id, scope_value)
        VALUES (?, ?)
        """,
        [
            (item.result_id, scope_value)
            for scope_value in history_scope_values(item)
        ],
    )


def list_history(
    limit: int = 100,
    offset: int = 0,
    *,
    project_id: str | None = None,
    segment_id: str | None = None,
    source: str | None = None,
) -> list[HistoryItem]:
    where, values = _history_filter(
        project_id=project_id,
        segment_id=segment_id,
        source=source,
    )
    query = (
        "SELECT history.data FROM history "
        "JOIN history_metadata "
        "ON history_metadata.result_id = history.result_id"
        f"{where} ORDER BY history.created_at DESC LIMIT ? OFFSET ?"
    )
    values.extend((limit if limit < 0 else max(0, limit), max(0, offset)))
    with db.conn() as connection:
        rows = connection.execute(query, values).fetchall()
    return [HistoryItem(**json.loads(row["data"])) for row in rows]


def count_history(
    *,
    project_id: str | None = None,
    segment_id: str | None = None,
    source: str | None = None,
) -> int:
    where, values = _history_filter(
        project_id=project_id,
        segment_id=segment_id,
        source=source,
    )
    with db.conn() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM history "
            "JOIN history_metadata "
            "ON history_metadata.result_id = history.result_id"
            f"{where}",
            values,
        ).fetchone()
    return int(row["total"] if row is not None else 0)


def _history_filter(
    *,
    project_id: str | None,
    segment_id: str | None,
    source: str | None,
) -> tuple[str, list[object]]:
    conditions: list[str] = []
    values: list[object] = []
    for expression, value in (
        ("history_metadata.project_id", project_id),
        ("history_metadata.source", source),
    ):
        if value is not None:
            conditions.append(f"{expression} = ?")
            values.append(value)

    if segment_id is not None:
        conditions.append(
            "EXISTS ("
            "SELECT 1 FROM history_scopes "
            "WHERE history_scopes.result_id = history.result_id "
            "AND history_scopes.scope_value = ?"
            ")"
        )
        values.append(segment_id)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, values


def get(result_id: str) -> HistoryItem | None:
    data = db.get_one("history", "result_id", result_id)
    return HistoryItem(**data) if data else None


def delete(result_id: str) -> None:
    delete_many([result_id])


def delete_many(result_ids: list[str] | set[str]) -> int:
    return delete_many_with_report(result_ids).removed_records


def delete_many_with_report(
    result_ids: list[str] | set[str],
    *,
    before_delete: Callable[[Connection, set[str]], None] | None = None,
) -> HistoryDeleteReport:
    selected_ids = {str(value) for value in result_ids if value}
    if not selected_ids:
        return HistoryDeleteReport(0, 0, 0)
    with _HISTORY_WRITE_LOCK:
        all_items = list_history(limit=-1)
        return _delete_selected_from_inventory(
            all_items,
            selected_ids,
            before_delete=before_delete,
        )


def delete_matching(
    predicate: Callable[[HistoryItem], bool],
    *,
    before_delete: Callable[[Connection, set[str]], None] | None = None,
) -> HistoryDeleteReport:
    with _HISTORY_WRITE_LOCK:
        all_items = list_history(limit=-1)
        return _delete_selected_from_inventory(
            all_items,
            {
                item.result_id
                for item in all_items
                if predicate(item)
            },
            before_delete=before_delete,
        )


def delete_project_history(project_id: str) -> int:
    return delete_matching(
        lambda item: item.project_id == project_id
    ).removed_records


def _delete_selected_from_inventory(
    all_items: list[HistoryItem],
    selected_ids: set[str],
    *,
    before_delete: Callable[[Connection, set[str]], None] | None = None,
) -> HistoryDeleteReport:
    selected = [item for item in all_items if item.result_id in selected_ids]
    if not selected:
        return HistoryDeleteReport(0, 0, 0)
    selected_ids = {item.result_id for item in selected}
    surviving_outputs = {
        Path(item.output_path).resolve()
        for item in all_items
        if item.result_id not in selected_ids and item.output_path
    }
    managed_paths: set[Path] = set()
    deletable_outputs: set[Path] = set()
    for item in selected:
        managed_paths.update(
            custom_reference_store.managed_paths_in(item.parameter_snapshot)
        )
        if item.output_path:
            path = Path(item.output_path)
            if (
                path.resolve() not in surviving_outputs
                and path.exists()
                and not path.is_symlink()
            ):
                deletable_outputs.add(path)
    with db.conn() as connection:
        if before_delete is not None:
            before_delete(connection, selected_ids)
        connection.executemany(
            "DELETE FROM history_scopes WHERE result_id = ?",
            [(result_id,) for result_id in sorted(selected_ids)],
        )
        connection.executemany(
            "DELETE FROM history_metadata WHERE result_id = ?",
            [(result_id,) for result_id in sorted(selected_ids)],
        )
        connection.executemany(
            "DELETE FROM history WHERE result_id = ?",
            [(result_id,) for result_id in sorted(selected_ids)],
        )
    removed_files = 0
    cleanup_failures = 0
    for path in deletable_outputs:
        try:
            path.unlink(missing_ok=True)
            removed_files += 1
        except OSError:
            cleanup_failures += 1
    for item in selected:
        try:
            removed_files += waveform_cache.delete_result_cache(
                item.result_id
            )
        except OSError:
            cleanup_failures += 1
    for path in managed_paths:
        try:
            custom_reference_store.delete_if_unreferenced(path)
        except OSError:
            cleanup_failures += 1
    if cleanup_failures:
        logger.warning(
            "History records deleted with %d artifact cleanup failure(s)",
            cleanup_failures,
        )
    return HistoryDeleteReport(
        removed_records=len(selected),
        removed_files=removed_files,
        cleanup_failures=cleanup_failures,
    )


def audio_path(result_id: str) -> Path | None:
    item = get(result_id)
    if not item or not item.output_path:
        return None
    path = Path(item.output_path)
    if path.exists():
        return path
    for suffix in (".wav", ".mp3", ".flac"):
        alternate = path.with_suffix(suffix)
        if alternate.exists():
            return alternate
    return None
