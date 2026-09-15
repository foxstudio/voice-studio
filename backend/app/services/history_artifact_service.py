from __future__ import annotations

from collections.abc import Callable

from app.schemas.voice_studio import HistoryItem, now_iso
from app.services import history_store, task_queue


def delete_result(result_id: str) -> history_store.HistoryDeleteReport:
    return delete_matching(lambda item: item.result_id == result_id)


def delete_matching(
    predicate: Callable[[HistoryItem], bool],
) -> history_store.HistoryDeleteReport:
    """Delete audio history and retire task artifact references atomically.

    History rows own generated audio files while generation tasks retain the
    immutable execution record.  Clearing both database projections in one
    transaction prevents a successful task from advertising an audio result
    that the history command has already removed.
    """

    removed_at = now_iso()
    return history_store.delete_matching(
        predicate,
        before_delete=lambda connection, result_ids: (
            task_queue.mark_artifacts_removed_from_connection(
                connection,
                result_ids=result_ids,
                removed_at=removed_at,
            )
        ),
    )
