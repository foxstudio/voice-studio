from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.schemas.history_scope import history_scope_values
from app.services import history_artifact_service, project_store


TtsHistoryDeleteScope = Literal["result_ids", "segment", "project"]


@dataclass(frozen=True)
class TtsHistoryDeleteResult:
    removed_records: int
    cleanup_failures: int


scope_values = history_scope_values


def belongs_to_segment(item: Any, segment_id: str | None) -> bool:
    return not segment_id or segment_id in scope_values(item)


def delete_history(
    project_id: str,
    *,
    scope: TtsHistoryDeleteScope,
    result_ids: list[str] | None = None,
    segment_id: str | None = None,
) -> TtsHistoryDeleteResult | None:
    if not project_store.get_project(project_id):
        return None

    requested_ids = {str(value) for value in result_ids or [] if value}

    def selected(item: Any) -> bool:
        if (
            item.project_id != project_id
            or item.parameter_snapshot.get("source") != "video_localization"
        ):
            return False
        if scope == "result_ids":
            return item.result_id in requested_ids
        if scope == "segment":
            return belongs_to_segment(item, segment_id)
        return True

    report = history_artifact_service.delete_matching(selected)
    return TtsHistoryDeleteResult(
        removed_records=report.removed_records,
        cleanup_failures=report.cleanup_failures,
    )
