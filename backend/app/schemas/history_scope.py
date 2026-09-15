from __future__ import annotations

from typing import Any


HISTORY_SCOPE_DIRECT_FIELDS = (
    "segment_id",
    "subtitle_id",
    "localized_subtitle_id",
    "cue_id",
)
HISTORY_SCOPE_ARRAY_FIELDS = ("source_cue_ids",)
HISTORY_SCOPE_PARAMETER_ARRAY_FIELDS = (
    "video_localization_target_subtitle_ids",
    "video_localization_source_cue_ids",
)


def history_scope_values(item: Any) -> set[str]:
    if isinstance(item, dict):
        read = item.get
    else:
        def read(field: str) -> Any:
            return getattr(item, field, None)

    values = [read(field) for field in HISTORY_SCOPE_DIRECT_FIELDS]
    for field in HISTORY_SCOPE_ARRAY_FIELDS:
        field_values = read(field)
        if isinstance(field_values, (list, tuple, set)):
            values.extend(field_values)

    parameters = read("parameter_snapshot") or {}
    if isinstance(parameters, dict):
        for field in HISTORY_SCOPE_PARAMETER_ARRAY_FIELDS:
            field_values = parameters.get(field)
            if isinstance(field_values, (list, tuple, set)):
                values.extend(field_values)

    return {str(value) for value in values if value}
