from __future__ import annotations

from pathlib import Path


def browser_audio_media_type(path: Path) -> str | None:
    """Return the container MIME type expected by browser media elements."""
    return {
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".aac": "audio/aac",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".ogg_opus": "audio/ogg",
        ".opus": "audio/ogg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
    }.get(path.suffix.lower())
