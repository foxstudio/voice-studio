from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import numpy as np

from app.domains.video_localization import draft_store
from app.domains.video_localization import media_assets
from app.domains.video_localization import subtitles
from app.domains.video_localization.readiness import build_production_readiness_audit
from app.errors import AppException
from app.domains.video_localization.schemas import VideoLocalizationDraft, VideoLocalizationExport, now_iso
from app.services import audio_tools


def export_subtitles(draft: VideoLocalizationDraft, kind: str) -> str:
    return subtitles.export_srt(draft, kind)


def export_bundle(project_id: str, project_name: str, draft: VideoLocalizationDraft) -> VideoLocalizationExport | None:
    next_draft = draft_store.save(project_id, draft, updated_at=draft.updated_at)
    if not next_draft:
        return None
    summary = {
        "cue_count": len(next_draft.cues),
        "ready_cue_count": sum(1 for cue in next_draft.cues if cue.review_status in {"ready", "locked"}),
        "blocker_count": len(next_draft.quality_gate.blockers),
        "warning_count": len(next_draft.quality_gate.warnings),
    }
    return VideoLocalizationExport(
        project_id=project_id,
        project_name=project_name,
        exported_at=now_iso(),
        export_summary=summary,
        **next_draft.model_dump(),
    )


def timeline_edl(project_id: str, project_name: str, draft: VideoLocalizationDraft) -> dict:
    next_draft = draft_store.with_fresh_gate(draft, updated_at=draft.updated_at)
    return {
        "schema_version": 1,
        "kind": "video_localization_timeline_edl",
        "project_id": project_id,
        "project_name": project_name,
        "exported_at": now_iso(),
        "duration_ms": next_draft.source_media.duration_ms,
        "output_range": {"type": "full_project", "start_ms": 0, "end_ms": next_draft.source_media.duration_ms},
        "source_media": next_draft.source_media.model_dump(),
        "stems": next_draft.stems.model_dump(),
        "track_states": next_draft.ui_state.get("track_states", {}),
        "timeline_clips": [dict(clip) for clip in next_draft.timeline_clips],
        "cues": [
            {
                "cue_id": cue.cue_id,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "source_duration_ms": cue.source_duration_ms,
                "asr_text": cue.en_subtitle_text,
                "localized_text": cue.zh_localized_subtitle_text,
                "tts_text": cue.tts_recommended_text,
                "reference_clip_id": cue.reference_clip_id,
                "tts_audio_path": cue.tts_audio_path,
                "review_status": cue.review_status,
                "audio_route": cue.audio_route,
            }
            for cue in next_draft.cues
        ],
    }


def timeline_audio_package(project_id: str, project_name: str, draft: VideoLocalizationDraft) -> dict:
    next_draft = draft_store.with_fresh_gate(draft, updated_at=draft.updated_at)
    clips = _renderable_dub_clips(next_draft)
    if not clips:
        raise AppException(400, "VIDEO_LOCALIZATION_RENDER_NO_CLIPS", "No routed speech clips are available to render")

    export_root = media_assets.project_video_localization_dir(project_id) / "exports"
    package_dir = media_assets.unique_path(export_root / f"timeline-audio-{_timestamp_slug()}")
    segments_dir = package_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    rendered_segments: list[dict] = []
    missing_segments: list[dict] = []
    output_duration_ms = int(next_draft.source_media.duration_ms or 0)
    mix_chunks: list[tuple[int, np.ndarray, int]] = []

    for index, clip in enumerate(clips, start=1):
        source_path = _clip_audio_path(next_draft, clip)
        clip_id = str(clip.get("clip_id") or f"clip_{index:04d}")
        cue_id = str(clip.get("cue_id") or "")
        if not source_path or not source_path.exists():
            missing_segments.append({"clip_id": clip_id, "cue_id": cue_id or None, "reason": "audio_source_missing"})
            continue

        start_ms = _int_value(clip.get("start_ms"), 0)
        source_start_ms = _int_value(clip.get("source_start_ms"), 0)
        clip_end_ms = _clip_end_ms(next_draft, clip, source_path, start_ms)
        source_end_ms = _source_end_ms(clip, source_path, source_start_ms, clip_end_ms - start_ms)
        if clip_end_ms <= start_ms:
            missing_segments.append({"clip_id": clip_id, "cue_id": cue_id or None, "reason": "invalid_timeline_range"})
            continue
        if source_end_ms <= source_start_ms:
            missing_segments.append({"clip_id": clip_id, "cue_id": cue_id or None, "reason": "invalid_source_range"})
            continue

        segment_name = f"{index:03d}_{_safe_identifier(clip_id)}.wav"
        segment_path = segments_dir / segment_name
        audio_tools.crop_file(source_path, segment_path, source_start_ms, source_end_ms, fmt="wav")
        probe = audio_tools.probe_audio(segment_path)
        rendered_segments.append(
            {
                "index": index,
                "clip_id": clip_id,
                "cue_id": cue_id or None,
                "candidate_id": clip.get("candidate_id"),
                "audio_route": clip.get("audio_route") or _cue_audio_route(next_draft, cue_id),
                "track_id": clip.get("track_id", "dub"),
                "timeline_start_ms": start_ms,
                "timeline_end_ms": clip_end_ms,
                "source_start_ms": source_start_ms,
                "source_end_ms": source_end_ms,
                "duration_ms": probe.get("duration_ms", max(0, source_end_ms - source_start_ms)),
                "audio_path": str(segment_path),
                "relative_path": f"segments/{segment_name}",
            }
        )
        audio, sr = audio_tools.read_audio(segment_path)
        mix_chunks.append((start_ms, audio, sr))
        output_duration_ms = max(output_duration_ms, clip_end_ms)

    if not rendered_segments:
        raise AppException(400, "VIDEO_LOCALIZATION_RENDER_AUDIO_MISSING", "No timeline audio clips could be rendered")

    dub_track_path = package_dir / "dub-track.wav"
    _write_aligned_dub_track(dub_track_path, mix_chunks, output_duration_ms)
    manifest = {
        "schema_version": 1,
        "kind": "video_localization_timeline_audio_package",
        "project_id": project_id,
        "project_name": project_name,
        "exported_at": now_iso(),
        "duration_ms": output_duration_ms,
        "dub_track_path": str(dub_track_path),
        "dub_track_relative_path": dub_track_path.name,
        "segments": rendered_segments,
        "missing_segments": missing_segments,
        "edl": timeline_edl(project_id, project_name, next_draft),
    }
    manifest_path = package_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = package_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(manifest_path, "manifest.json")
        archive.write(dub_track_path, dub_track_path.name)
        for segment in rendered_segments:
            archive.write(Path(segment["audio_path"]), segment["relative_path"])
    manifest["package_dir"] = str(package_dir)
    manifest["package_path"] = str(zip_path)
    manifest["package_filename"] = zip_path.name
    return manifest


def localized_video_file(project_id: str, project_name: str, draft: VideoLocalizationDraft) -> dict:
    source_video_path = Path(draft.source_media.video_path) if draft.source_media.video_path else None
    if not source_video_path or not source_video_path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_RENDER_SOURCE_VIDEO_MISSING", "Source video is required to render localized video")

    manifest = timeline_audio_package(project_id, project_name, draft)
    package_dir = Path(str(manifest["package_dir"]))
    dub_track_path = Path(str(manifest["dub_track_path"]))
    mixdown_path = package_dir / "mixdown-track.wav"
    mixed_tracks = _write_localized_mixdown(mixdown_path, draft, dub_track_path, int(manifest.get("duration_ms") or draft.source_media.duration_ms or 0))
    video_path = package_dir / "localized-video.mp4"
    _mux_localized_video(source_video_path, mixdown_path, None, video_path)
    manifest.update(
        {
            "kind": "video_localization_localized_video",
            "localized_video_path": str(video_path),
            "localized_video_relative_path": video_path.name,
            "audio_mix": "track_state_mixdown",
            "mixed_tracks": mixed_tracks,
            "mixdown_track_path": str(mixdown_path),
        }
    )
    manifest_path = package_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def production_readiness(project_id: str, project_name: str, draft: VideoLocalizationDraft) -> dict:
    next_draft = draft_store.with_fresh_gate(draft, updated_at=draft.updated_at)
    return build_production_readiness_audit(project_id=project_id, project_name=project_name, draft=next_draft)


def _renderable_dub_clips(draft: VideoLocalizationDraft) -> list[dict]:
    cue_routes = {cue.cue_id: cue.audio_route for cue in draft.cues}
    timeline_clips = [
        dict(clip)
        for clip in draft.timeline_clips
        if dict(clip).get("track_id", "dub") == "dub"
        and cue_routes.get(str(dict(clip).get("cue_id") or ""), "clone_from_source") != "preserve_original_audio"
    ]
    generated_cues = list(timeline_clips)
    timeline_cue_ids = {str(clip.get("cue_id") or "") for clip in timeline_clips}
    for cue in draft.cues:
        if cue.cue_id in timeline_cue_ids or cue.audio_route not in {"clone_from_source", "preset_tts"}:
            continue
        if not cue.tts_audio_path:
            continue
        start_ms = cue.start_ms or 0
        end_ms = cue.end_ms or start_ms + (cue.generated_duration_ms or cue.source_duration_ms or 0)
        generated_cues.append(
            {
                "clip_id": f"clip_{cue.cue_id}",
                "cue_id": cue.cue_id,
                "track_id": "dub",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_start_ms": 0,
                "source_end_ms": cue.generated_duration_ms,
                "audio_path": cue.tts_audio_path,
                "status": "ready",
                "audio_route": cue.audio_route,
            }
        )
    preserve_source = _existing_path(draft.stems.vocals_clean_path) or _existing_path(draft.source_media.audio_path) or _existing_path(draft.stems.original_audio_path)
    if preserve_source:
        for cue in draft.cues:
            if cue.audio_route != "preserve_original_audio" or cue.start_ms is None or cue.end_ms is None or cue.end_ms <= cue.start_ms:
                continue
            generated_cues.append(
                {
                    "clip_id": f"preserve_{cue.cue_id}",
                    "cue_id": cue.cue_id,
                    "track_id": "dub",
                    "start_ms": cue.start_ms,
                    "end_ms": cue.end_ms,
                    "source_start_ms": cue.start_ms,
                    "source_end_ms": cue.end_ms,
                    "audio_path": str(preserve_source),
                    "status": "ready",
                    "audio_route": "preserve_original_audio",
                }
            )
    return sorted(generated_cues, key=lambda item: (_int_value(item.get("start_ms"), 0), str(item.get("clip_id") or "")))


def _existing_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.exists() else None


def _clip_audio_path(draft: VideoLocalizationDraft, clip: dict) -> Path | None:
    explicit = clip.get("audio_path")
    if isinstance(explicit, str) and explicit.strip():
        path = Path(explicit)
        if path.exists():
            return path
    cue_id = clip.get("cue_id")
    if isinstance(cue_id, str):
        cue = next((item for item in draft.cues if item.cue_id == cue_id), None)
        if cue and cue.tts_audio_path:
            path = Path(cue.tts_audio_path)
            if path.exists():
                return path
    return None


def _cue_audio_route(draft: VideoLocalizationDraft, cue_id: str) -> str | None:
    cue = next((item for item in draft.cues if item.cue_id == cue_id), None)
    return cue.audio_route if cue else None


def _clip_end_ms(draft: VideoLocalizationDraft, clip: dict, source_path: Path, start_ms: int) -> int:
    end_ms = _int_value(clip.get("end_ms"), -1)
    if end_ms > start_ms:
        return end_ms
    duration_ms = audio_tools.probe_audio(source_path).get("duration_ms", 0)
    cue_id = clip.get("cue_id")
    if isinstance(cue_id, str):
        cue = next((item for item in draft.cues if item.cue_id == cue_id), None)
        if cue and cue.end_ms is not None and cue.end_ms > start_ms:
            return cue.end_ms
    return start_ms + int(duration_ms or 0)


def _source_end_ms(clip: dict, source_path: Path, source_start_ms: int, timeline_duration_ms: int) -> int:
    explicit = _int_value(clip.get("source_end_ms"), -1)
    if explicit > source_start_ms:
        return explicit
    source_duration_ms = int(audio_tools.probe_audio(source_path).get("duration_ms", 0) or 0)
    if timeline_duration_ms > 0:
        return min(source_duration_ms, source_start_ms + timeline_duration_ms)
    return source_duration_ms


def _write_aligned_dub_track(path: Path, chunks: list[tuple[int, np.ndarray, int]], duration_ms: int) -> None:
    target_sr = 48000
    total_frames = max(1, int(target_sr * max(duration_ms, 1) / 1000))
    mixed = np.zeros(total_frames, dtype=np.float32)
    for start_ms, audio, sr in chunks:
        if sr != target_sr:
            audio = _resample_mono(audio, sr, target_sr)
        start_frame = max(0, int(target_sr * start_ms / 1000))
        if start_frame >= len(mixed):
            continue
        end_frame = min(len(mixed), start_frame + len(audio))
        if end_frame <= start_frame:
            continue
        mixed[start_frame:end_frame] += audio[: end_frame - start_frame]
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.98:
        mixed = mixed * (0.98 / peak)
    audio_tools.write_audio(path, mixed, target_sr, fmt="wav")


def _resample_mono(audio: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    if sr == target_sr or not audio.size:
        return audio.astype(np.float32)
    duration = len(audio) / sr
    next_len = max(1, int(duration * target_sr))
    return np.interp(np.linspace(0, len(audio), next_len, endpoint=False), np.arange(len(audio)), audio).astype(np.float32)


def _int_value(value: object, fallback: int) -> int:
    try:
        if value is None or value == "":
            return fallback
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "clip"


def _timestamp_slug() -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", now_iso())[:18] or "export"


def _write_localized_mixdown(path: Path, draft: VideoLocalizationDraft, dub_track_path: Path, duration_ms: int) -> list[dict]:
    track_states = _resolved_audio_track_states(draft)
    solo_tracks = {track_id for track_id, state in track_states.items() if state["solo"]}
    disabled_media_tracks = {
        str(track_id)
        for track_id in draft.ui_state.get("disabled_media_tracks", [])
        if isinstance(track_id, str)
    }
    source_audio_path = _existing_path(draft.source_media.audio_path) or _existing_path(draft.stems.original_audio_path)
    track_paths = {
        "original": source_audio_path,
        "vocals": _existing_path(draft.stems.vocals_clean_path),
        "background": _existing_path(draft.stems.background_path),
        "dub": dub_track_path if dub_track_path.exists() else None,
    }
    chunks: list[tuple[int, np.ndarray, int]] = []
    mixed_tracks: list[dict] = []
    for track_id in ("original", "vocals", "background", "dub"):
        state = track_states[track_id]
        source_path = track_paths[track_id]
        active = track_id not in disabled_media_tracks and not state["muted"] and (not solo_tracks or track_id in solo_tracks)
        if not active or not source_path:
            continue
        gain = state["volume"]
        editable_clips = [dict(clip) for clip in draft.timeline_clips if dict(clip).get("track_id") == track_id]
        if track_id == "dub" or not editable_clips:
            audio, sr = audio_tools.read_audio(source_path)
            chunks.append((0, audio.astype(np.float32) * gain, sr))
            mixed_tracks.append({"track_id": track_id, "volume": gain, "source_path": str(source_path)})
            continue
        for clip in editable_clips:
            clip_path = _existing_path(clip.get("audio_path")) or source_path
            if not clip_path:
                continue
            audio, sr = audio_tools.read_audio(clip_path)
            source_start_ms = max(0, _int_value(clip.get("source_start_ms"), 0))
            timeline_start_ms = max(0, _int_value(clip.get("start_ms"), 0))
            timeline_end_ms = max(timeline_start_ms, _int_value(clip.get("end_ms"), duration_ms))
            source_end_ms = _source_end_ms(clip, clip_path, source_start_ms, timeline_end_ms - timeline_start_ms)
            start_frame = max(0, int(sr * source_start_ms / 1000))
            end_frame = min(len(audio), int(sr * source_end_ms / 1000))
            if end_frame <= start_frame:
                continue
            chunks.append((timeline_start_ms, audio[start_frame:end_frame].astype(np.float32) * gain, sr))
            mixed_tracks.append(
                {
                    "track_id": track_id,
                    "clip_id": clip.get("clip_id"),
                    "volume": gain,
                    "source_path": str(clip_path),
                    "start_ms": timeline_start_ms,
                    "end_ms": timeline_end_ms,
                    "source_start_ms": source_start_ms,
                    "source_end_ms": source_end_ms,
                }
            )
    _write_aligned_dub_track(path, chunks, duration_ms)
    return mixed_tracks


def _resolved_audio_track_states(draft: VideoLocalizationDraft) -> dict[str, dict[str, float | bool]]:
    defaults: dict[str, dict[str, float | bool]] = {
        "original": {"muted": True, "solo": False, "volume": 1.0},
        "vocals": {"muted": True, "solo": False, "volume": 1.0},
        "background": {"muted": False, "solo": False, "volume": 1.0},
        "dub": {"muted": False, "solo": False, "volume": 1.0},
    }
    raw_states = draft.ui_state.get("track_states", {})
    if not isinstance(raw_states, dict):
        return defaults
    for track_id, default in defaults.items():
        raw = raw_states.get(track_id, {})
        if not isinstance(raw, dict):
            continue
        default["muted"] = raw.get("muted") is True
        default["solo"] = raw.get("solo") is True
        default["volume"] = max(0.0, min(2.0, _float_value(raw.get("volume"), 1.0)))
    return defaults


def _float_value(value: object, fallback: float) -> float:
    try:
        return float(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _mux_localized_video(source_video_path: Path, dub_track_path: Path, background_path: Path | None, destination: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppException(500, "VIDEO_LOCALIZATION_FFMPEG_MISSING", "ffmpeg is required to render localized video")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if background_path:
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(source_video_path),
            "-i",
            str(background_path),
            "-i",
            str(dub_track_path),
            "-filter_complex",
            "[1:a][2:a]amix=inputs=2:duration=longest:normalize=0[aout]",
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(destination),
        ]
    else:
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(source_video_path),
            "-i",
            str(dub_track_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(destination),
        ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
        return
    destination.unlink(missing_ok=True)
    raise AppException(500, "VIDEO_LOCALIZATION_RENDER_VIDEO_FAILED", "Failed to render localized video")
