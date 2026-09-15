from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.domains.video_localization import public_payload, timeline_clip_identity
from app.domains.video_localization.schemas import (
    VideoLocalizationDraft,
    VideoLocalizationTimelineEditReceipt,
)

if TYPE_CHECKING:
    from app.schemas.video_localization_timeline_edit import (
        VideoLocalizationTimelineEditPatchRequest,
    )


MAX_RECEIPTS = 64
MAX_RECEIPT_BYTES = 2 * 1024 * 1024


def project_result(
    updated: VideoLocalizationDraft,
    patch: "VideoLocalizationTimelineEditPatchRequest",
) -> dict[str, Any]:
    """Return the one bounded public projection used by commit and replay."""

    changed_clip_ids = {
        item.clip_id
        for item in [*patch.clip_patches, *patch.added_clips]
    }
    lane_states = updated.ui_state.get("dub_lane_states")
    if not isinstance(lane_states, dict):
        lane_states = {}
    return {
        "timeline_clips": [
            _public_receipt_clip(clip)
            for clip in updated.timeline_clips
            if str(clip.get("clip_id") or "") in changed_clip_ids
        ],
        "dub_lane_states": {
            str(item.lane): dict(lane_states[str(item.lane)])
            for item in patch.dub_lane_state_patches
            if isinstance(lane_states.get(str(item.lane)), dict)
        },
        "cues": (
            public_payload.without_internal_locators(
                [item.model_dump() for item in updated.cues]
            )
            if patch.cue_collection_change is not None
            or patch.localized_subtitle_collection_change is not None
            else None
        ),
        "localized_subtitles": (
            public_payload.without_internal_locators(
                [item.model_dump() for item in updated.localized_subtitles]
            )
            if patch.localized_subtitle_collection_change is not None
            else None
        ),
    }


def build_receipt(
    updated: VideoLocalizationDraft,
    patch: "VideoLocalizationTimelineEditPatchRequest",
    *,
    request_fingerprint: str,
    repository_revision: int,
    updated_at: str,
) -> VideoLocalizationTimelineEditReceipt:
    projection = project_result(updated, patch)
    return VideoLocalizationTimelineEditReceipt(
        request_fingerprint=request_fingerprint,
        repository_revision=repository_revision,
        updated_at=updated_at,
        **projection,
    )


def retain_recent(
    existing: dict[str, VideoLocalizationTimelineEditReceipt],
    request_id: str,
    receipt: VideoLocalizationTimelineEditReceipt,
) -> dict[str, VideoLocalizationTimelineEditReceipt]:
    """Bound receipts by count and serialized bytes while retaining the newest."""

    retained = dict(existing)
    retained.pop(request_id, None)
    retained[request_id] = receipt
    sizes = {
        retained_id: _serialized_entry_size(retained_id, retained_receipt)
        for retained_id, retained_receipt in retained.items()
    }
    total_bytes = 2 + sum(sizes.values())
    while len(retained) > 1 and (
        len(retained) > MAX_RECEIPTS
        or total_bytes > MAX_RECEIPT_BYTES
    ):
        oldest_id = next(iter(retained))
        retained.pop(oldest_id)
        total_bytes -= sizes.pop(oldest_id)
    return retained


def _public_receipt_clip(clip: dict[str, Any]) -> dict[str, Any]:
    projected = dict(
        public_payload.without_internal_locators(
            timeline_clip_identity.with_generation_identity(clip)
        )
    )
    projected.pop("timeline_edit_gate", None)
    projected.pop("cqc_report", None)
    return projected


def _serialized_entry_size(
    request_id: str,
    receipt: VideoLocalizationTimelineEditReceipt,
) -> int:
    return len(
        json.dumps(
            {request_id: receipt.model_dump(mode="json")},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    )


__all__ = [
    "MAX_RECEIPTS",
    "MAX_RECEIPT_BYTES",
    "build_receipt",
    "project_result",
    "retain_recent",
]
