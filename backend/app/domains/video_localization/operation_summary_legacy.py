from __future__ import annotations

import json

from app.domains.video_localization import (
    operation_summary_projection,
)
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationOperation,
)
from app.schemas.video_localization_operation_summary_migration import (
    ExpectedOperationSummary,
    LegacyOperationSummarySource,
    LegacySummaryDecodeError,
)


def decode_legacy_summary_source(
    project_id: str,
    raw_project_json: str,
    project_updated_at: str,
    normalize_project_id: bool,
) -> LegacyOperationSummarySource | None:
    """Decode the legacy Project mirror without storage side effects."""

    try:
        payload = json.loads(raw_project_json)
    except (TypeError, ValueError) as exc:
        raise LegacySummaryDecodeError(
            "legacy_project_invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise LegacySummaryDecodeError("legacy_project_invalid")
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        raise LegacySummaryDecodeError("legacy_project_invalid")
    if "video_localization" not in parameters:
        return None
    localization = parameters.get("video_localization")
    if not isinstance(localization, dict):
        raise LegacySummaryDecodeError("legacy_draft_invalid")
    normalized = False
    if normalize_project_id:
        raw_operations = localization.get("operations", [])
        if not isinstance(raw_operations, list):
            raise LegacySummaryDecodeError(
                "legacy_draft_invalid"
            )
        for operation in raw_operations:
            if not isinstance(operation, dict):
                raise LegacySummaryDecodeError(
                    "legacy_draft_invalid"
                )
            if str(operation.get("project_id") or "") != project_id:
                operation["project_id"] = project_id
                normalized = True
    try:
        draft = VideoLocalizationDraft.model_validate(localization)
    except (TypeError, ValueError) as exc:
        raise LegacySummaryDecodeError(
            "legacy_draft_invalid"
        ) from exc
    if normalize_project_id:
        _require_migratable_operations(draft.operations)
        draft, media_normalized = (
            _with_durable_media_facts(draft)
        )
        if media_normalized:
            localization["operations"] = [
                operation.model_dump(mode="json")
                for operation in draft.operations
            ]
            normalized = True
    cores = tuple(
        operation_summary_projection
        .operation_summary_core_from_draft_operation(
            draft,
            operation,
        )
        for operation in draft.operations
    )
    return LegacyOperationSummarySource(
        project_payload=payload,
        operation_payloads=tuple(
            operation.model_dump(mode="json")
            for operation in draft.operations
        ),
        cores=cores,
        expected_operations=tuple(
            ExpectedOperationSummary(
                core=core,
                kind=operation.kind,
                status=operation.status,
                cancel_requested=operation.cancel_requested,
                created_at=operation.created_at,
                completed_at=operation.completed_at,
            )
            for operation, core in zip(
                draft.operations,
                cores,
                strict=True,
            )
        ),
        projected_at=draft.updated_at or project_updated_at,
        normalized=normalized,
    )


def _require_migratable_operations(
    operations: list[VideoLocalizationOperation],
) -> None:
    operation_ids = [
        operation.operation_id for operation in operations
    ]
    if len(set(operation_ids)) != len(operation_ids):
        raise LegacySummaryDecodeError(
            "duplicate_operation_identity"
        )
    active_kinds: set[str] = set()
    for operation in operations:
        if operation.status not in {"queued", "running"}:
            continue
        if operation.kind in active_kinds:
            raise LegacySummaryDecodeError(
                "active_kind_conflict"
            )
        active_kinds.add(operation.kind)


def _with_durable_media_facts(
    draft: VideoLocalizationDraft,
) -> tuple[VideoLocalizationDraft, bool]:
    changed = False
    operations: list[VideoLocalizationOperation] = []
    for operation in draft.operations:
        media_summary = (
            operation_summary_projection.durable_media_summary(
                draft,
                operation,
            )
        )
        additions = {
            key: value
            for key, value in media_summary.items()
            if key not in operation.result_summary
        }
        if additions:
            operation = operation.model_copy(
                update={
                    "result_summary": {
                        **operation.result_summary,
                        **additions,
                    }
                }
            )
            changed = True
        operations.append(operation)
    return (
        draft.model_copy(update={"operations": operations})
        if changed
        else draft,
        changed,
    )


__all__ = ["decode_legacy_summary_source"]
