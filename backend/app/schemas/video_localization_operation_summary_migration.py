from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.schemas.video_localization_operation_summary import (
    OperationSummaryCoreV1,
)


SummaryBackfillCategory = Literal[
    "none",
    "not_video_localization",
    "project_missing",
    "legacy_project_invalid",
    "legacy_draft_invalid",
    "duplicate_operation_identity",
    "active_kind_conflict",
    "projection_conflict",
    "storage_error",
]


@dataclass(frozen=True)
class ExpectedOperationSummary:
    core: OperationSummaryCoreV1
    kind: str
    status: str
    cancel_requested: bool
    created_at: str
    completed_at: str | None

    @property
    def operation_id(self) -> str:
        return self.core.operation_id


@dataclass(frozen=True)
class LegacyOperationSummarySource:
    project_payload: dict
    operation_payloads: tuple[dict, ...]
    cores: tuple[OperationSummaryCoreV1, ...]
    expected_operations: tuple[ExpectedOperationSummary, ...]
    projected_at: str
    normalized: bool


LegacySummaryDecoder = Callable[
    [str, str, str, bool],
    LegacyOperationSummarySource | None,
]


class LegacySummaryDecodeError(RuntimeError):
    def __init__(self, category: SummaryBackfillCategory):
        super().__init__(category)
        self.category = category


__all__ = [
    "ExpectedOperationSummary",
    "LegacyOperationSummarySource",
    "LegacySummaryDecodeError",
    "LegacySummaryDecoder",
    "SummaryBackfillCategory",
]
