"""Managed-media reads shared by dubbing CQC and delivery entrypoints."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domains.video_localization import media_assets, timeline_audio_sources


@contextmanager
def rendered_candidate_projection(source_path: Path, clips: list[dict]):
    """Render the exact retained sequence through the shared playback renderer."""
    from app.domains.video_localization.timeline_audio_renderer import (
        TimelineAudioRenderInput, TimelineAudioRenderItem, render_timeline_audio,
    )
    origin = min(int(clip["start_ms"]) for clip in clips)
    request = TimelineAudioRenderInput(
        timeline_duration_ms=max(int(clip["end_ms"]) for clip in clips) - origin,
        items=[TimelineAudioRenderItem.from_editorial_ranges(
            item_id=clip["clip_id"], source_id="candidate", track_id="dub",
            timeline_start_ms=int(clip["start_ms"]) - origin,
            timeline_end_ms=int(clip["end_ms"]) - origin,
            source_start_ms=int(clip["source_start_ms"]),
            source_end_ms=int(clip["source_end_ms"]),
        ) for clip in clips],
    )
    with TemporaryDirectory(prefix="voice-retained-content-") as directory:
        output = Path(directory) / "retained.wav"
        render_timeline_audio(request, source_paths={"candidate": source_path}, output_path=output)
        yield output


def current_timeline_audio_sha256s(
    project_id: str,
    draft,
) -> dict[str, str | None]:
    """Hash current dub clip bytes through the managed project boundary."""

    resolved = timeline_audio_sources.resolve_dub_clip_audio_paths(draft)
    fingerprints: dict[str, str | None] = {}
    hashes_by_path: dict[str, str | None] = {}
    for raw in draft.timeline_clips:
        clip = dict(raw)
        if str(clip.get("track_id") or "") != "dub":
            continue
        clip_id = str(clip.get("clip_id") or "")
        if not clip_id:
            continue
        path = media_assets.managed_project_file(
            project_id,
            str(resolved[clip_id]) if clip_id in resolved else clip.get("audio_path"),
        )
        if path is None:
            fingerprints[clip_id] = None
            continue
        key = str(path)
        if key not in hashes_by_path:
            try:
                hashes_by_path[key] = media_assets.file_sha256(path)
            except OSError:
                hashes_by_path[key] = None
        fingerprints[clip_id] = hashes_by_path[key]
    return fingerprints


__all__ = ["current_timeline_audio_sha256s", "current_timeline_audio_durations"]


def current_timeline_audio_durations(project_id: str, draft) -> dict[str, int | None]:
    """Read headers once per managed file; never decode or transcribe content."""
    from app.services import audio_tools

    resolved = timeline_audio_sources.resolve_dub_clip_audio_paths(draft)
    durations = {}
    paths = {}
    for raw in draft.timeline_clips:
        if raw.get("track_id") != "dub":
            continue
        clip_id = str(raw.get("clip_id") or "")
        path = media_assets.managed_project_file(project_id, str(resolved[clip_id]) if clip_id in resolved else raw.get("audio_path"))
        if path is None:
            durations[clip_id] = None
            continue
        key = str(path)
        if key not in paths:
            try:
                paths[key] = audio_tools.probe_audio_duration_ceil_ms(path)
            except (OSError, ValueError, RuntimeError):
                paths[key] = None
        durations[clip_id] = paths[key]
    return durations
