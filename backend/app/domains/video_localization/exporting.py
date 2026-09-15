from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from app.domains.video_localization import draft_store
from app.domains.video_localization import media_assets
from app.domains.video_localization import media_health
from app.domains.video_localization import subtitles
from app.domains.video_localization import timeline_audio_renderer
from app.domains.video_localization import timeline_clip_timing
from app.domains.video_localization import timeline_audio_sources
from app.domains.video_localization.export_contracts import (
    VideoLocalizationMediaExportRequest,
)
from app.domains.video_localization.readiness import build_production_readiness_audit
from app.errors import AppException
from app.domains.video_localization.schemas import (
    VideoLocalizationCue,
    VideoLocalizationDraft,
    VideoLocalizationExport,
    VideoLocalizationSubtitleCue,
    now_iso,
)
from app.services import audio_tools


def export_subtitles(
    draft: VideoLocalizationDraft,
    kind: str,
    *,
    localized_variant: str = "localized",
) -> str:
    if kind == "zh" and localized_variant == "dub":
        projected = [
            VideoLocalizationSubtitleCue(
                subtitle_id=subtitle.subtitle_id,
                start_ms=subtitle.start_ms,
                end_ms=subtitle.end_ms,
                text=subtitle.text,
                quality_flags=list(subtitle.quality_flags),
            )
            for subtitle in draft.dub_subtitles
        ]
        return subtitles.export_srt(
            draft.model_copy(
                update={"localized_subtitles": projected}
            ),
            "zh",
        )
    if kind == "bilingual" and draft.localized_subtitles:
        return _export_bilingual_localized_track(draft)
    return subtitles.export_srt(draft, kind)


def _export_bilingual_localized_track(draft: VideoLocalizationDraft) -> str:
    cue_by_id = {cue.cue_id: cue for cue in draft.cues}
    projected_cues: list[VideoLocalizationCue] = []
    for subtitle in sorted(
        draft.localized_subtitles,
        key=lambda item: (item.start_ms, item.end_ms, item.subtitle_id),
    ):
        source_cues = _localized_subtitle_source_cues(subtitle, draft.cues, cue_by_id)
        english = " ".join(text for cue in source_cues if (text := (cue.en_subtitle_text or "").strip()))
        if not english:
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_INVALID",
                "本土化字幕无法映射到原文字幕，无法导出双语字幕。",
                {"subtitle_id": subtitle.subtitle_id, "kind": "bilingual"},
            )
        projected_cues.append(
            VideoLocalizationCue(
                cue_id=subtitle.subtitle_id,
                start_ms=subtitle.start_ms,
                end_ms=subtitle.end_ms,
                en_subtitle_text=english,
                zh_localized_subtitle_text=subtitle.text,
            )
        )

    return subtitles.export_srt(draft.model_copy(update={"cues": projected_cues}), "bilingual")


def _localized_subtitle_source_cues(
    subtitle: VideoLocalizationSubtitleCue,
    cues: list[VideoLocalizationCue],
    cue_by_id: dict[str, VideoLocalizationCue],
) -> list[VideoLocalizationCue]:
    if subtitle.source_cue_ids:
        return [cue_by_id[cue_id] for cue_id in subtitle.source_cue_ids if cue_id in cue_by_id]

    overlapping = [
        cue
        for cue in cues
        if cue.start_ms is not None
        and cue.end_ms is not None
        and cue.start_ms < subtitle.end_ms
        and cue.end_ms > subtitle.start_ms
    ]
    if overlapping:
        return overlapping
    if subtitle.linked_cue_id and subtitle.linked_cue_id in cue_by_id:
        return [cue_by_id[subtitle.linked_cue_id]]
    return []


def export_bundle(
    project_id: str,
    project_name: str,
    draft: VideoLocalizationDraft,
) -> VideoLocalizationExport:
    next_draft = draft_store.with_fresh_gate(
        draft,
        updated_at=draft.updated_at,
    )
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


def _media_export_source_fingerprint(
    draft: VideoLocalizationDraft,
    *,
    include_video: bool,
) -> str:
    """Fingerprint every persisted or file-backed media export input."""

    advisory_timeline_fields = {
        "cqc_status",
        "cqc_report_version",
        "cqc_report",
        "timeline_edit_gate",
    }

    def render_clip_input(clip: dict) -> dict:
        return {
            key: value
            for key, value in dict(clip).items()
            if key not in advisory_timeline_fields
        }

    def output_mix_state() -> dict[str, object]:
        def without_audition_solo(value: object) -> object:
            if not isinstance(value, dict):
                return value
            return {
                key: (
                    {
                        state_key: state_value
                        for state_key, state_value in state.items()
                        if state_key != "solo"
                    }
                    if isinstance(state, dict)
                    else state
                )
                for key, state in value.items()
            }

        return {
            "track_states": without_audition_solo(
                draft.ui_state.get("track_states")
            ),
            "dub_lane_states": without_audition_solo(
                draft.ui_state.get("dub_lane_states")
            ),
            "disabled_media_tracks": draft.ui_state.get(
                "disabled_media_tracks"
            ),
        }

    resolution = media_health.inspect_project_media(draft)
    audio_paths = {
        value
        for value in [
            *(str(clip.get("audio_path") or "") for clip in draft.timeline_clips),
            *(str(cue.tts_audio_path or "") for cue in draft.cues),
            *(
                str(subtitle.tts_audio_path or "")
                for subtitle in draft.localized_subtitles
            ),
        ]
        if value
    }
    media_payload = {
        "source_audio": resolution.health.source_audio.model_dump(mode="json"),
        "vocals": resolution.health.vocals.model_dump(mode="json"),
        "background": resolution.health.background.model_dump(mode="json"),
    }
    if include_video:
        media_payload["source_video"] = (
            resolution.health.source_video.model_dump(mode="json")
        )
    payload = {
        "contract_version": "video-localization-media-source-input-v1",
        "source_media": draft.source_media.model_dump(mode="json"),
        "stems": draft.stems.model_dump(mode="json"),
        "cues": [cue.model_dump(mode="json") for cue in draft.cues],
        "localized_subtitles": [
            subtitle.model_dump(mode="json")
            for subtitle in draft.localized_subtitles
        ],
        "dub_subtitles": [
            subtitle.model_dump(mode="json")
            for subtitle in draft.dub_subtitles
        ],
        "dub_subtitle_source_revision": (
            draft.dub_subtitle_source_revision
        ),
        "timeline_clips": [
            render_clip_input(clip)
            for clip in draft.timeline_clips
        ],
        "mix_state": output_mix_state(),
        "media": media_payload,
        "audio_files": [
            _file_input_revision(path)
            for path in sorted(audio_paths)
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def media_export_input_fingerprint(
    draft: VideoLocalizationDraft,
    request: VideoLocalizationMediaExportRequest,
) -> str:
    payload = {
        "contract_version": "video-localization-media-export-v4",
        "render_input": _media_export_source_fingerprint(
            draft,
            include_video=request.kind == "video",
        ),
        "request": request.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_input_revision(value: str) -> dict[str, object]:
    try:
        path = Path(value).expanduser().resolve()
        stat = path.stat()
        if not path.is_file():
            raise OSError("not a regular file")
        return {
            "locator": str(path),
            "available": True,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": media_assets.file_sha256(path),
        }
    except OSError:
        return {
            "locator": value,
            "available": False,
        }


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
        "localized_subtitles": [subtitle.model_dump(mode="json") for subtitle in next_draft.localized_subtitles],
        "dub_subtitles": [
            subtitle.model_dump(mode="json")
            for subtitle in next_draft.dub_subtitles
        ],
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


def media_export_file(
    project_id: str,
    project_name: str,
    draft: VideoLocalizationDraft,
    request: VideoLocalizationMediaExportRequest,
    *,
    fingerprint: str,
    destination_path: Path | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Render the explicitly selected tracks into one delivery file."""

    _report_export_progress(on_progress, 0.02, "正在检查导出内容")
    _raise_if_export_cancelled(is_cancelled)
    export_root = (
        media_assets.ensure_project_video_localization_dir(project_id)
        / "exports"
    )
    export_root.mkdir(parents=True, exist_ok=True)
    if request.kind == "subtitle":
        destination = destination_path or (
            export_root / f"subtitles-{fingerprint[:16]}.srt"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        selected_tracks = set(request.subtitle_tracks)
        subtitle_kind = (
            "bilingual"
            if selected_tracks == {"asr", "localized"}
            else "en"
            if selected_tracks == {"asr"}
            else "zh"
        )
        _report_export_progress(on_progress, 0.36, "正在生成字幕文件")
        subtitle_text = export_subtitles(
            draft,
            subtitle_kind,
            localized_variant=request.localized_subtitle_variant,
        )
        _raise_if_export_cancelled(is_cancelled)
        destination.write_text(subtitle_text, encoding="utf-8")
        _report_export_progress(on_progress, 0.97, "正在检查导出字幕")
        if not destination.is_file() or destination.stat().st_size <= 0:
            raise AppException(
                500,
                "VIDEO_LOCALIZATION_SUBTITLE_EXPORT_EMPTY",
                "字幕文件生成失败，请重试。",
            )
        _report_export_progress(on_progress, 1.0, "字幕已保存")
        return {
            "kind": "video_localization_media_export",
            "exported_at": now_iso(),
            "project_id": project_id,
            "project_name": project_name,
            "output_path": str(destination),
            "mixed_tracks": [],
            "request": request.model_dump(mode="json"),
        }

    project_media = media_health.inspect_project_media(draft).paths
    source_video_probe = (
        media_assets.probe_video(project_media.source_video)
        if project_media.source_video is not None
        else {}
    )
    duration_ms = int(source_video_probe.get("duration_ms") or 0)
    if duration_ms <= 0:
        duration_ms = int(draft.source_media.duration_ms or 0)
    if duration_ms <= 0:
        source_probe_path = (
            project_media.source_audio
            or project_media.vocals
            or project_media.background
        )
        if source_probe_path:
            duration_ms = int(
                audio_tools.probe_audio(source_probe_path).get(
                    "duration_ms"
                )
                or 0
            )
    if duration_ms <= 0:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_EXPORT_DURATION_MISSING",
            "项目时长不可用，暂时无法导出。",
        )

    selected_audio_tracks = set(request.audio_tracks)
    selected_dub_lanes = set(request.dub_lanes)
    mixdown_path: Path | None = None
    mixed_tracks: list[dict] = []
    if selected_audio_tracks:
        _report_export_progress(on_progress, 0.08, "正在混合所选音轨")
        mixdown_path = export_root / f".mixed-audio-{fingerprint[:16]}.wav"
        mixed_tracks = _write_localized_mixdown(
            mixdown_path,
            draft,
            None,
            duration_ms,
            selected_tracks=selected_audio_tracks,
            selected_dub_lanes=selected_dub_lanes,
        )
        if not mixed_tracks:
            mixdown_path.unlink(missing_ok=True)
            raise AppException(
                400,
                "VIDEO_LOCALIZATION_EXPORT_AUDIO_UNAVAILABLE",
                "勾选的音频轨道目前没有可用声音，请重新选择。",
            )
        _raise_if_export_cancelled(is_cancelled)
        _report_export_progress(on_progress, 0.28, "音轨混合完成")

    if request.kind == "audio":
        suffix = request.audio_format
        destination = destination_path or (
            export_root / f"mixed-audio-{fingerprint[:16]}.{suffix}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size > 0:
            if mixdown_path:
                mixdown_path.unlink(missing_ok=True)
        elif request.audio_format == "wav":
            assert mixdown_path is not None
            mixdown_path.replace(destination)
            mixdown_path = None
        else:
            assert mixdown_path is not None
            _transcode_audio_export(
                mixdown_path,
                destination,
                bitrate_kbps=_resolved_export_audio_bitrate(
                    request.audio_bitrate_kbps,
                    project_media.source_video or project_media.source_audio,
                ),
            )
        _raise_if_export_cancelled(is_cancelled)
        _report_export_progress(on_progress, 0.92, "正在检查导出音频")
        _validate_audio_export(
            destination,
            expected_duration_ms=duration_ms,
        )
        _report_export_progress(on_progress, 1.0, "音频已保存")
        return {
            "kind": "video_localization_media_export",
            "exported_at": now_iso(),
            "project_id": project_id,
            "project_name": project_name,
            "output_path": str(destination),
            "mixed_tracks": mixed_tracks,
            "request": request.model_dump(mode="json"),
        }

    source_video_path = project_media.source_video
    if source_video_path is None:
        if mixdown_path:
            mixdown_path.unlink(missing_ok=True)
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_RENDER_SOURCE_VIDEO_MISSING",
            "导出视频前需要先导入源视频。",
        )
    destination = destination_path or (
        export_root / f"mixed-video-{fingerprint[:16]}.mp4"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    subtitle_timeline_path: Path | None = None
    subtitle_overlay_dir: Path | None = None
    if request.subtitle_tracks:
        _report_export_progress(on_progress, 0.32, "正在准备字幕画面")
        video_probe = media_assets.probe_video(source_video_path)
        source_width = int(video_probe.get("width") or 1920)
        source_height = int(video_probe.get("height") or 1080)
        output_height = {
            "1080p": 1080,
            "720p": 720,
        }.get(request.video_size, source_height)
        output_width = max(
            2,
            int(round(source_width * output_height / max(1, source_height))) // 2 * 2,
        )
        subtitle_overlay_dir = (
            export_root
            / f".mixed-subtitles-{fingerprint[:16]}"
        )
        subtitle_timeline_path = _write_subtitle_overlay_timeline(
            subtitle_overlay_dir,
            draft,
            selected_tracks=set(request.subtitle_tracks),
            localized_subtitle_variant=(
                request.localized_subtitle_variant
            ),
            duration_ms=duration_ms,
            width=output_width,
            height=output_height,
        )
        _raise_if_export_cancelled(is_cancelled)
    try:
        if not destination.is_file() or destination.stat().st_size <= 0:
            _report_export_progress(on_progress, 0.38, "正在渲染视频")
            _mux_media_export(
                source_video_path,
                mixdown_path,
                subtitle_timeline_path,
                destination,
                request=request,
                duration_ms=duration_ms,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
            )
        _raise_if_export_cancelled(is_cancelled)
        _report_export_progress(on_progress, 0.97, "正在检查导出视频")
        _validate_video_export(
            destination,
            expected_duration_ms=duration_ms,
        )
        _report_export_progress(on_progress, 1.0, "视频已保存")
    finally:
        if mixdown_path:
            mixdown_path.unlink(missing_ok=True)
        if subtitle_overlay_dir:
            shutil.rmtree(subtitle_overlay_dir, ignore_errors=True)
    return {
        "kind": "video_localization_media_export",
        "exported_at": now_iso(),
        "project_id": project_id,
        "project_name": project_name,
        "output_path": str(destination),
        "mixed_tracks": mixed_tracks,
        "request": request.model_dump(mode="json"),
    }


def production_readiness(project_id: str, project_name: str, draft: VideoLocalizationDraft) -> dict:
    next_draft = draft_store.with_fresh_gate(draft, updated_at=draft.updated_at)
    return build_production_readiness_audit(project_id=project_id, project_name=project_name, draft=next_draft)


def _renderable_dub_clips(draft: VideoLocalizationDraft) -> list[dict]:
    return sorted(
        [
            dict(clip)
            for clip in draft.timeline_clips
            if dict(clip).get("track_id") == "dub"
            and str(dict(clip).get("status") or "").lower()
            != "queued"
        ],
        key=lambda item: (
            _int_value(item.get("start_ms"), 0),
            str(item.get("clip_id") or ""),
        ),
    )


def _existing_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_file() else None


def _source_end_ms(clip: dict, source_path: Path, source_start_ms: int, timeline_duration_ms: int) -> int:
    explicit = _int_value(clip.get("source_end_ms"), -1)
    if explicit > source_start_ms:
        return explicit
    source_duration_ms = int(audio_tools.probe_audio(source_path).get("duration_ms", 0) or 0)
    if timeline_duration_ms > 0:
        return min(source_duration_ms, source_start_ms + timeline_duration_ms)
    return source_duration_ms


def _timeline_clip_range(
    clip: dict,
    *,
    clip_id: str,
    duration_ms: int,
) -> tuple[int, int]:
    start_ms = max(0, _int_value(clip.get("start_ms"), 0))
    end_ms = _int_value(clip.get("end_ms"), duration_ms)
    if end_ms <= start_ms or end_ms > duration_ms:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_TIMELINE_AUDIO_RANGE_INVALID",
            f"时间线音频片段 {clip_id} 的位置超出视频范围，已停止导出。",
            {
                "item_ids": [clip_id],
                "timeline_duration_ms": duration_ms,
                "start_ms": start_ms,
                "end_ms": end_ms,
            },
        )
    return start_ms, end_ms


def _int_value(value: object, fallback: int) -> int:
    try:
        if value is None or value == "":
            return fallback
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _write_localized_mixdown(
    path: Path,
    draft: VideoLocalizationDraft,
    dub_track_path: Path | None,
    duration_ms: int,
    *,
    selected_tracks: set[str] | None = None,
    selected_dub_lanes: set[int] | None = None,
) -> list[dict]:
    draft = timeline_clip_timing.normalize_draft(draft)
    track_states = _resolved_audio_track_states(draft)
    explicit_selection = selected_tracks is not None
    dub_clips = (
        _renderable_dub_clips(draft)
        if explicit_selection
        else [
            dict(clip)
            for clip in draft.timeline_clips
            if dict(clip).get("track_id") == "dub"
        ]
    )
    resolved_dub_clips = _resolved_dub_clip_lanes(dub_clips)
    resolved_dub_paths = (
        timeline_audio_sources.resolve_dub_clip_audio_paths(
            draft,
            [clip for clip, _ in resolved_dub_clips],
        )
    )
    dub_lane_states = _resolved_dub_lane_states(
        draft,
        track_states["dub"],
        {lane for _, lane in resolved_dub_clips},
    )
    disabled_media_tracks = {
        str(track_id)
        for track_id in draft.ui_state.get("disabled_media_tracks", [])
        if isinstance(track_id, str)
    }
    project_media = media_health.inspect_project_media(draft).paths
    track_paths = {
        "original": project_media.source_audio,
        "vocals": project_media.vocals,
        "background": project_media.background,
    }
    render_items: list[
        timeline_audio_renderer.TimelineAudioRenderItem
    ] = []
    source_paths: dict[str, Path] = {}
    mixed_tracks: list[dict] = []
    missing_track_ids: list[str] = []
    for track_id in ("original", "vocals", "background"):
        state = track_states[track_id]
        source_path = track_paths[track_id]
        active = (
            track_id in selected_tracks
            if explicit_selection
            else (
                track_id not in disabled_media_tracks
                and not state["muted"]
            )
        )
        if not active:
            continue
        if source_path is None:
            if explicit_selection:
                missing_track_ids.append(track_id)
            continue
        gain = state["volume"]
        editable_clips = [dict(clip) for clip in draft.timeline_clips if dict(clip).get("track_id") == track_id]
        if not editable_clips:
            source_duration_ms = int(
                audio_tools.probe_audio(source_path).get(
                    "duration_ms",
                    0,
                )
                or 0
            )
            end_ms = min(duration_ms, source_duration_ms)
            if end_ms <= 0:
                if explicit_selection:
                    missing_track_ids.append(track_id)
                continue
            source_id = f"track:{track_id}"
            source_paths[source_id] = source_path
            render_items.append(
                timeline_audio_renderer.TimelineAudioRenderItem.from_editorial_ranges(
                    item_id=source_id,
                    source_id=source_id,
                    track_id=track_id,
                    timeline_start_ms=0,
                    timeline_end_ms=end_ms,
                    source_start_ms=0,
                    source_end_ms=end_ms,
                    gain=float(gain),
                )
            )
            mixed_tracks.append({"track_id": track_id, "volume": gain, "source_path": str(source_path)})
            continue
        for clip in editable_clips:
            clip_path = _existing_path(clip.get("audio_path")) or source_path
            if not clip_path:
                if explicit_selection:
                    missing_track_ids.append(
                        str(clip.get("clip_id") or track_id)
                    )
                continue
            source_start_ms = max(0, _int_value(clip.get("source_start_ms"), 0))
            clip_id = str(
                clip.get("clip_id")
                or f"{track_id}:{len(render_items)}"
            )
            timeline_start_ms, timeline_end_ms = (
                _timeline_clip_range(
                    clip,
                    clip_id=clip_id,
                    duration_ms=duration_ms,
                )
            )
            source_end_ms = _source_end_ms(clip, clip_path, source_start_ms, timeline_end_ms - timeline_start_ms)
            if source_end_ms <= source_start_ms:
                if explicit_selection:
                    missing_track_ids.append(clip_id)
                continue
            source_id = f"{track_id}:{clip_id}"
            source_paths[source_id] = clip_path
            render_items.append(
                timeline_audio_renderer.TimelineAudioRenderItem.from_editorial_ranges(
                    item_id=source_id,
                    source_id=source_id,
                    track_id=track_id,
                    timeline_start_ms=timeline_start_ms,
                    timeline_end_ms=timeline_end_ms,
                    source_start_ms=source_start_ms,
                    source_end_ms=source_end_ms,
                    gain=float(gain),
                )
            )
            mixed_tracks.append(
                {
                    "track_id": track_id,
                    "clip_id": clip_id,
                    "volume": gain,
                    "source_path": str(clip_path),
                    "start_ms": timeline_start_ms,
                    "end_ms": timeline_end_ms,
                    "source_start_ms": source_start_ms,
                    "source_end_ms": source_end_ms,
                }
            )

    dub_selected = (
        "dub" in selected_tracks
        if explicit_selection
        else "dub" not in disabled_media_tracks
    )
    if dub_selected:
        if dub_clips:
            selected_clips: list[tuple[dict, int, dict]] = []
            for clip, lane in resolved_dub_clips:
                state = dub_lane_states.get(lane, dub_lane_states[0])
                active = (
                    lane in (selected_dub_lanes or set())
                    if explicit_selection
                    else (
                        not state["muted"]
                    )
                )
                if not active:
                    continue
                selected_clips.append((clip, lane, state))
            missing_clip_ids = [
                str(clip.get("clip_id") or "")
                for clip, _, _ in selected_clips
                if str(clip.get("clip_id") or "")
                not in resolved_dub_paths
            ]
            if missing_clip_ids:
                raise AppException(
                    400,
                    "VIDEO_LOCALIZATION_EXPORT_DUB_AUDIO_INCOMPLETE",
                    (
                        f"有 {len(missing_clip_ids)} 个合成配音片段"
                        "找不到音频，已停止导出。"
                    ),
                    {"clip_ids": missing_clip_ids},
                )
            for clip, lane, state in selected_clips:
                clip_id = str(clip.get("clip_id") or "")
                clip_path = resolved_dub_paths[clip_id]
                source_start_ms = max(0, _int_value(clip.get("source_start_ms"), 0))
                timeline_start_ms, timeline_end_ms = (
                    _timeline_clip_range(
                        clip,
                        clip_id=clip_id,
                        duration_ms=duration_ms,
                    )
                )
                source_end_ms = _source_end_ms(clip, clip_path, source_start_ms, timeline_end_ms - timeline_start_ms)
                if source_end_ms <= source_start_ms:
                    missing_track_ids.append(clip_id)
                    continue
                source_id = f"dub:{clip_id}"
                source_paths[source_id] = clip_path
                render_items.append(
                    timeline_audio_renderer.TimelineAudioRenderItem.from_editorial_ranges(
                        item_id=source_id,
                        source_id=source_id,
                        track_id="dub",
                        timeline_start_ms=timeline_start_ms,
                        timeline_end_ms=timeline_end_ms,
                        source_start_ms=source_start_ms,
                        source_end_ms=source_end_ms,
                        gain=float(state["volume"]),
                    )
                )
                mixed_tracks.append(
                    {
                        "track_id": "dub",
                        "dub_lane": lane,
                        "clip_id": clip_id,
                        "volume": state["volume"],
                        "source_path": str(clip_path),
                        "start_ms": timeline_start_ms,
                        "end_ms": timeline_end_ms,
                        "source_start_ms": source_start_ms,
                        "source_end_ms": source_end_ms,
                    }
                )
        else:
            state = dub_lane_states[0]
            active = (
                0 in (selected_dub_lanes or set())
                if explicit_selection
                else (
                    not state["muted"]
                )
            )
            if active and dub_track_path is not None and dub_track_path.is_file():
                source_duration_ms = int(
                    audio_tools.probe_audio(dub_track_path).get(
                        "duration_ms",
                        0,
                    )
                    or 0
                )
                end_ms = min(duration_ms, source_duration_ms)
                source_id = "dub:prepared-track"
                source_paths[source_id] = dub_track_path
                render_items.append(
                    timeline_audio_renderer.TimelineAudioRenderItem.from_editorial_ranges(
                        item_id=source_id,
                        source_id=source_id,
                        track_id="dub",
                        timeline_start_ms=0,
                        timeline_end_ms=end_ms,
                        source_start_ms=0,
                        source_end_ms=end_ms,
                        gain=float(state["volume"]),
                    )
                )
                mixed_tracks.append({"track_id": "dub", "dub_lane": 0, "volume": state["volume"], "source_path": str(dub_track_path)})
    if missing_track_ids:
        raise AppException(
            400,
            "VIDEO_LOCALIZATION_EXPORT_AUDIO_INCOMPLETE",
            (
                f"有 {len(missing_track_ids)} 个已选音频片段不可用，"
                "已停止导出。"
            ),
            {"item_ids": missing_track_ids},
        )
    if not render_items:
        return []
    timeline_audio_renderer.render_timeline_audio(
        timeline_audio_renderer.TimelineAudioRenderInput(
            timeline_duration_ms=duration_ms,
            channel_mode="preserve",
            items=render_items,
        ),
        source_paths=source_paths,
        output_path=path,
    )
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
        default["volume"] = max(0.0, min(4.0, _float_value(raw.get("volume"), 1.0)))
    return defaults


def _resolved_dub_lane_states(
    draft: VideoLocalizationDraft,
    legacy_state: dict[str, float | bool],
    resolved_lane_ids: set[int] | None = None,
) -> dict[int, dict[str, float | bool]]:
    raw_states = draft.ui_state.get("dub_lane_states", {})
    if not isinstance(raw_states, dict):
        raw_states = {}
    lane_ids = {0}
    lane_ids.update(resolved_lane_ids or set())
    lane_ids.update(max(0, _int_value(dict(clip).get("dub_lane"), 0)) for clip in draft.timeline_clips if dict(clip).get("track_id") == "dub")
    lane_ids.update(int(key) for key in raw_states if isinstance(key, str) and key.isdigit())
    states: dict[int, dict[str, float | bool]] = {}
    for lane in sorted(lane_ids):
        fallback = legacy_state if lane == 0 else {"muted": False, "solo": False, "volume": 1.0}
        raw = raw_states.get(str(lane), {})
        if not isinstance(raw, dict):
            raw = {}
        states[lane] = {
            "muted": raw.get("muted", fallback["muted"]) is True,
            "solo": raw.get("solo", fallback["solo"]) is True,
            "volume": max(0.0, min(4.0, _float_value(raw.get("volume"), float(fallback["volume"])))),
        }
    return states


def _resolved_dub_clip_lanes(dub_clips: list[dict]) -> list[tuple[dict, int]]:
    ordered = sorted(
        enumerate(dub_clips),
        key=lambda item: (_int_value(item[1].get("start_ms"), 0), item[0]),
    )
    lanes: list[list[dict]] = []
    lane_by_index: dict[int, int] = {}
    for input_index, clip in ordered:
        start_ms = _int_value(clip.get("start_ms"), 0)
        end_ms = max(start_ms + 1, _int_value(clip.get("end_ms"), start_ms + 1))
        raw_lane = clip.get("dub_lane")
        explicit_lane = raw_lane if isinstance(raw_lane, int) and not isinstance(raw_lane, bool) and raw_lane >= 0 else None

        def lane_available(lane: int) -> bool:
            return not any(
                start_ms < _int_value(item.get("end_ms"), 0)
                and end_ms > _int_value(item.get("start_ms"), 0)
                for item in (lanes[lane] if lane < len(lanes) else [])
            )

        lane = explicit_lane if explicit_lane is not None and lane_available(explicit_lane) else None
        if lane is None:
            lane = next((candidate for candidate in range(len(lanes)) if lane_available(candidate)), len(lanes))
        while len(lanes) <= lane:
            lanes.append([])
        lanes[lane].append(clip)
        lane_by_index[input_index] = lane
    return [(clip, lane_by_index[index]) for index, clip in enumerate(dub_clips)]


def _float_value(value: object, fallback: float) -> float:
    try:
        return float(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _transcode_audio_export(
    source: Path,
    destination: Path,
    *,
    bitrate_kbps: int,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppException(
            500,
            "VIDEO_LOCALIZATION_FFMPEG_MISSING",
            "当前设备没有找到 ffmpeg，无法导出音频。",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{bitrate_kbps}k",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    source.unlink(missing_ok=True)
    if result.returncode == 0 and destination.is_file() and destination.stat().st_size > 0:
        return
    destination.unlink(missing_ok=True)
    raise AppException(
        500,
        "VIDEO_LOCALIZATION_RENDER_AUDIO_FAILED",
        "音频导出失败，请检查所选轨道后重试。",
    )


def _resolved_export_audio_bitrate(
    requested: str | int,
    source_media: Path | None,
) -> int:
    if requested != "source":
        return int(requested)
    if source_media is None:
        return 192
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 192
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=bit_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source_media),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        bitrate_kbps = int((result.stdout or "").strip()) // 1000
    except ValueError:
        return 192
    return max(96, min(512, bitrate_kbps))


def _mux_media_export(
    source_video: Path,
    mixdown_audio: Path | None,
    subtitle_timeline_path: Path | None,
    destination: Path,
    *,
    request: VideoLocalizationMediaExportRequest,
    duration_ms: int,
    on_progress: Callable[[float, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppException(
            500,
            "VIDEO_LOCALIZATION_FFMPEG_MISSING",
            "当前设备没有找到 ffmpeg，无法导出视频。",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    target_height = {"1080p": 1080, "720p": 720}.get(request.video_size)
    quality_crf = {
        "source": "18",
        "high": "18",
        "balanced": "22",
        "compact": "27",
    }[request.video_quality]
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
        "-i",
        str(source_video),
    ]
    if mixdown_audio is not None:
        command.extend(["-i", str(mixdown_audio)])
    subtitle_input_index: int | None = None
    if subtitle_timeline_path is not None:
        subtitle_input_index = 2 if mixdown_audio is not None else 1
        command.extend(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(subtitle_timeline_path),
            ]
        )
    if subtitle_input_index is not None:
        base_filter = (
            f"[0:v]scale=-2:{target_height},setpts=PTS-STARTPTS[base]"
            if target_height
            else "[0:v]setpts=PTS-STARTPTS[base]"
        )
        command.extend(
            [
                "-filter_complex",
                (
                    f"{base_filter};"
                    f"[{subtitle_input_index}:v]format=rgba,setpts=PTS-STARTPTS[sub];"
                    "[base][sub]overlay=0:0:eof_action=pass:shortest=0[vout]"
                ),
                "-map",
                "[vout]",
            ]
        )
    else:
        command.extend(["-map", "0:v:0"])
        if target_height:
            command.extend(["-vf", f"scale=-2:{target_height}"])
    if mixdown_audio is not None:
        command.extend(["-map", "1:a:0"])
    else:
        command.append("-an")
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            quality_crf,
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if mixdown_audio is not None:
        command.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                f"{_resolved_export_audio_bitrate(request.audio_bitrate_kbps, source_video)}k",
                "-shortest",
            ]
        )
    command.extend(
        [
            "-t",
            f"{max(1, duration_ms) / 1000:.3f}",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )
    with tempfile.TemporaryFile(
        mode="w+",
        encoding="utf-8",
    ) as stderr_file:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
        )
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                _raise_if_export_cancelled(is_cancelled)
                key, _, value = raw_line.strip().partition("=")
                if key not in {"out_time_ms", "out_time_us"}:
                    continue
                try:
                    rendered_us = max(0, int(value))
                except ValueError:
                    continue
                fraction = min(
                    1.0,
                    rendered_us / max(1, duration_ms * 1_000),
                )
                _report_export_progress(
                    on_progress,
                    0.38 + fraction * 0.57,
                    f"正在渲染视频 {fraction * 100:.0f}%",
                )
            return_code = process.wait()
        except Exception:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            destination.unlink(missing_ok=True)
            raise
        stderr_file.seek(0)
        stderr = stderr_file.read()
    if (
        return_code == 0
        and destination.is_file()
        and destination.stat().st_size > 0
    ):
        return
    destination.unlink(missing_ok=True)
    raise AppException(
        500,
        "VIDEO_LOCALIZATION_RENDER_VIDEO_FAILED",
        (
            "视频导出失败，请检查所选轨道和导出设置后重试。"
            if not stderr.strip()
            else "视频导出失败，请查看任务调试信息。"
        ),
        {"ffmpeg_error": stderr.strip()[-2_000:]},
    )


def _report_export_progress(
    callback: Callable[[float, str], None] | None,
    progress: float,
    stage: str,
) -> None:
    if callback is not None:
        callback(max(0.0, min(1.0, progress)), stage)


def _raise_if_export_cancelled(
    is_cancelled: Callable[[], bool] | None,
) -> None:
    if is_cancelled is not None and is_cancelled():
        raise AppException(
            409,
            "VIDEO_LOCALIZATION_MEDIA_EXPORT_CANCELLED",
            "导出已取消，未保留未完成文件。",
        )


def _write_subtitle_overlay_timeline(
    directory: Path,
    draft: VideoLocalizationDraft,
    *,
    selected_tracks: set[str],
    localized_subtitle_variant: str = "localized",
    duration_ms: int,
    width: int,
    height: int,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    selected_asr = "asr" in selected_tracks
    selected_localized = "localized" in selected_tracks
    asr_cues = [
        (int(cue.start_ms), int(cue.end_ms), (cue.en_subtitle_text or "").strip())
        for cue in draft.cues
        if selected_asr
        and cue.start_ms is not None
        and cue.end_ms is not None
        and cue.end_ms > cue.start_ms
        and (cue.en_subtitle_text or "").strip()
    ]
    localized_cues = (
        _selected_localized_subtitle_cues(
            draft,
            localized_subtitle_variant,
        )
        if selected_localized
        else []
    )
    boundaries = {0, max(1, duration_ms)}
    for start_ms, end_ms, _ in [*asr_cues, *localized_cues]:
        boundaries.add(max(0, min(duration_ms, start_ms)))
        boundaries.add(max(0, min(duration_ms, end_ms)))
    ordered_boundaries = sorted(boundaries)
    intervals = [
        (start_ms, end_ms)
        for start_ms, end_ms in zip(
            ordered_boundaries,
            ordered_boundaries[1:],
        )
        if end_ms > start_ms
    ]
    if not intervals:
        intervals = [(0, max(1, duration_ms))]
    font_path = _subtitle_font_path()
    font_scale = max(0.55, min(1.4, height / 1080))
    localized_font = ImageFont.truetype(
        str(font_path),
        max(18, int(48 * font_scale)),
    )
    asr_font = ImageFont.truetype(
        str(font_path),
        max(16, int(40 * font_scale)),
    )
    concat_lines = ["ffconcat version 1.0"]
    last_path: Path | None = None
    for index, (start_ms, end_ms) in enumerate(intervals):
        midpoint = start_ms + (end_ms - start_ms) // 2
        localized_text = _active_subtitle_text(localized_cues, midpoint)
        asr_text = _active_subtitle_text(asr_cues, midpoint)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        dual_track = selected_asr and selected_localized
        if localized_text:
            _draw_subtitle_text(
                draw,
                localized_text,
                font=localized_font,
                width=width,
                center_y=height - int((130 if dual_track else 72) * font_scale),
                fill=(255, 231, 89, 255),
            )
        if asr_text:
            _draw_subtitle_text(
                draw,
                asr_text,
                font=asr_font,
                width=width,
                center_y=height - int(62 * font_scale),
                fill=(255, 255, 255, 255),
            )
        image_path = directory / f"{index:05d}.png"
        image.save(image_path)
        concat_lines.append(f"file '{_ffconcat_path(image_path)}'")
        concat_lines.append(
            f"duration {(end_ms - start_ms) / 1000:.3f}"
        )
        last_path = image_path
    if last_path is not None:
        concat_lines.append(f"file '{_ffconcat_path(last_path)}'")
    timeline_path = directory / "timeline.ffconcat"
    timeline_path.write_text(
        "\n".join([*concat_lines, ""]),
        encoding="utf-8",
    )
    return timeline_path


def _selected_localized_subtitle_cues(
    draft: VideoLocalizationDraft,
    variant: str,
) -> list[tuple[int, int, str]]:
    subtitles = (
        draft.dub_subtitles
        if variant == "dub"
        else draft.localized_subtitles
    )
    return [
        (cue.start_ms, cue.end_ms, (cue.text or "").strip())
        for cue in subtitles
        if cue.end_ms > cue.start_ms and (cue.text or "").strip()
    ]


def _active_subtitle_text(
    cues: list[tuple[int, int, str]],
    time_ms: int,
) -> str:
    return next(
        (
            text
            for start_ms, end_ms, text in cues
            if start_ms <= time_ms < end_ms
        ),
        "",
    )


def _draw_subtitle_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: ImageFont.FreeTypeFont,
    width: int,
    center_y: int,
    fill: tuple[int, int, int, int],
) -> None:
    wrapped = _wrap_subtitle_text(
        draw,
        text,
        font,
        max_width=max(120, int(width * 0.86)),
    )
    draw.multiline_text(
        (width // 2, center_y),
        wrapped,
        font=font,
        fill=fill,
        anchor="mm",
        align="center",
        spacing=max(2, int(font.size * 0.16)),
        stroke_width=max(2, int(font.size * 0.075)),
        stroke_fill=(4, 6, 8, 255),
    )


def _wrap_subtitle_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    max_width: int,
) -> str:
    normalized = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if not normalized:
        return ""
    word_mode = " " in normalized and not re.search(r"[\u3400-\u9fff]", normalized)
    units = normalized.split() if word_mode else list(normalized)
    separator = " " if word_mode else ""
    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else current + separator + unit
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(current)
            current = unit
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def _validate_video_export(
    path: Path,
    *,
    expected_duration_ms: int,
) -> None:
    probe = media_assets.probe_video(path)
    actual_duration_ms = int(probe.get("duration_ms") or 0)
    frame_rate = float(probe.get("frame_rate") or 0)
    tolerance_ms = max(
        80,
        int(2_000 / frame_rate) if frame_rate > 0 else 80,
    )
    if (
        actual_duration_ms > 0
        and abs(actual_duration_ms - expected_duration_ms)
        <= tolerance_ms
    ):
        return
    path.unlink(missing_ok=True)
    raise AppException(
        500,
        "VIDEO_LOCALIZATION_EXPORT_VIDEO_INCOMPLETE",
        "导出视频时长与源视频不一致，已停止交付该文件。",
        {
            "expected_duration_ms": expected_duration_ms,
            "actual_duration_ms": actual_duration_ms,
        },
    )


def _validate_audio_export(
    path: Path,
    *,
    expected_duration_ms: int,
) -> None:
    try:
        probe = audio_tools.probe_audio(path)
    except Exception:
        probe = {}
    actual_duration_ms = int(probe.get("duration_ms") or 0)
    if (
        actual_duration_ms > 0
        and abs(actual_duration_ms - expected_duration_ms) <= 80
    ):
        return
    path.unlink(missing_ok=True)
    raise AppException(
        500,
        "VIDEO_LOCALIZATION_EXPORT_AUDIO_INCOMPLETE",
        "导出音频时长与视频时间线不一致，已停止交付该文件。",
        {
            "expected_duration_ms": expected_duration_ms,
            "actual_duration_ms": actual_duration_ms,
        },
    )


def _subtitle_font_path() -> Path:
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    return next((path for path in candidates if path.is_file()), candidates[-1])


def _ffconcat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", r"'\''")
