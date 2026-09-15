from __future__ import annotations

from typing import Any


CLIENT_UI_STATE_FIELDS = frozenset(
    {
        "audio_track_order",
        "disabled_media_tracks",
        "dub_lane_states",
        "inspector_width",
        "playhead_ms",
        "selected_cue_id",
        "sidebar_collapsed",
        "subtitle_display_mode",
        "subtitle_preview",
        "subtitle_workflow_settings_open",
        "timeline_hover_scrub_enabled",
        "timeline_viewport_start_ms",
        "timeline_zoom",
        "track_states",
    }
)
BACKEND_UI_STATE_FIELDS = frozenset({"latest_tts_task_by_segment"})
SHARED_MONOTONIC_UI_STATE_FIELDS = frozenset({"discarded_tts_task_ids"})
TRANSIENT_UI_STATE_FIELDS = frozenset({"client_timeline_edit_intent"})
LEGACY_UI_STATE_FIELDS = frozenset({"automatic_dub_mix_configured", "initial_track_mix_configured"})
NESTED_UI_STATE_FIELDS = frozenset({"track_states", "dub_lane_states"})


def client_ui_state_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Keep the UI endpoint limited to fields owned by the interactive client."""
    return {key: value for key, value in patch.items() if key in CLIENT_UI_STATE_FIELDS}


def merge_client_ui_state_patch(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    sanitized = client_ui_state_patch(patch)
    merged = {
        **{key: value for key, value in current.items() if key not in LEGACY_UI_STATE_FIELDS},
        **sanitized,
    }
    for key in NESTED_UI_STATE_FIELDS:
        current_values = current.get(key)
        patch_values = sanitized.get(key)
        if not isinstance(current_values, dict) or not isinstance(patch_values, dict):
            continue
        nested = dict(current_values)
        for item_key, item_patch in patch_values.items():
            current_item = nested.get(item_key)
            nested[item_key] = (
                {**current_item, **item_patch}
                if isinstance(current_item, dict) and isinstance(item_patch, dict)
                else item_patch
            )
        merged[key] = nested
    return merged


def client_draft_ui_state(ui_state: dict[str, Any]) -> dict[str, Any]:
    """Remove fields whose values must come from backend state or merge policy."""
    protected = (
        BACKEND_UI_STATE_FIELDS
        | SHARED_MONOTONIC_UI_STATE_FIELDS
        | TRANSIENT_UI_STATE_FIELDS
        | LEGACY_UI_STATE_FIELDS
    )
    return {key: value for key, value in ui_state.items() if key not in protected}
