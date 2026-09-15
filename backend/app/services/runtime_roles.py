from __future__ import annotations

import os
from collections.abc import Mapping


VIDEO_LOCALIZATION_WORKER_MODE_ENV = (
    "VOICE_STUDIO_VIDEO_LOCALIZATION_WORKER_MODE"
)
_VIDEO_LOCALIZATION_WORKER_MODES = frozenset(
    {"embedded", "external"}
)


def video_localization_worker_mode(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the explicit process role for video-localization operations."""

    source = os.environ if environ is None else environ
    value = str(
        source.get(
            VIDEO_LOCALIZATION_WORKER_MODE_ENV,
            "embedded",
        )
    ).strip().lower()
    if value not in _VIDEO_LOCALIZATION_WORKER_MODES:
        allowed = ", ".join(
            sorted(_VIDEO_LOCALIZATION_WORKER_MODES)
        )
        raise RuntimeError(
            f"{VIDEO_LOCALIZATION_WORKER_MODE_ENV} "
            f"must be one of: {allowed}"
        )
    return value


def embedded_video_localization_worker_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    return video_localization_worker_mode(environ) == "embedded"
