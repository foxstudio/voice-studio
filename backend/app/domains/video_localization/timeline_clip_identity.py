"""Canonical media-generation identity for timeline edit fencing."""

from __future__ import annotations

import hashlib
from typing import Any


def generation_identity(clip: dict[str, Any]) -> str:
    """Return the one server-owned token that changes with clip media."""

    direct = str(
        clip.get("task_id")
        or clip.get("generation_id")
        or clip.get("result_id")
        or clip.get("candidate_id")
        or clip.get("media_source_clip_id")
        or clip.get("optimistic_tts_workflow_id")
        or ""
    )
    if direct:
        return direct
    audio_path = str(clip.get("audio_path") or "")
    if audio_path:
        return "media:" + hashlib.sha256(audio_path.encode("utf-8")).hexdigest()
    return str(clip.get("clip_id") or "")


def with_generation_identity(clip: dict[str, Any]) -> dict[str, Any]:
    """Project an explicit token without mutating the stored clip."""

    projected = dict(clip)
    projected["generation_identity"] = generation_identity(projected)
    # Public edit receipts remove internal paths. Preserve source availability
    # before that projection so an ordinary timing edit cannot revoke playback.
    projected["has_audio_source"] = bool(
        projected.get("audio_path")
        or projected.get("media_source_clip_id")
        or projected.get("has_audio_source")
    )
    return projected


__all__ = ["generation_identity", "with_generation_identity"]
