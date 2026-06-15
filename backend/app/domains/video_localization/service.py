from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from fastapi import UploadFile

from app.domains.video_localization.quality_gate import evaluate_quality_gate
from app.errors import AppException
from app.schemas.voice_studio import VideoLocalizationDraft, VideoLocalizationExport, now_iso
from app.services import audio_tools, project_store, settings_store

VIDEO_LOCALIZATION_KEY = "video_localization"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def get_video_localization(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    raw = project.parameters.get(VIDEO_LOCALIZATION_KEY) or {}
    return VideoLocalizationDraft(**raw)


def save_video_localization(project_id: str, draft: VideoLocalizationDraft) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    next_draft = _with_fresh_gate(draft, updated_at=now_iso())
    project.parameters = {**project.parameters, VIDEO_LOCALIZATION_KEY: next_draft.model_dump()}
    project_store.save_project(project)
    return next_draft


async def import_source_media(project_id: str, file: UploadFile) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    source_path, content = await _save_uploaded_video(project_id, file)
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    source_media = draft.source_media.model_copy(
        update={
            "filename": file.filename or source_path.name,
            "video_path": str(source_path),
            "size_bytes": len(content),
            "imported_at": now_iso(),
            "metadata": {
                **draft.source_media.metadata,
                "content_type": file.content_type,
                "upload_status": "stored",
            },
        }
    )
    next_draft = draft.model_copy(update={"source_media": source_media, "status": "draft"})
    return save_video_localization(project_id, next_draft)


def extract_source_audio(project_id: str) -> VideoLocalizationDraft | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id) or VideoLocalizationDraft()
    if not draft.source_media.video_path:
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_MISSING", "Import a source video before extracting audio")

    video_path = Path(draft.source_media.video_path)
    if not video_path.exists():
        raise AppException(400, "VIDEO_LOCALIZATION_SOURCE_NOT_FOUND", "Source video file is missing")

    audio_dir = _project_video_localization_dir(project_id) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = _unique_path(audio_dir / f"{video_path.stem}-source.wav")
    audio_meta = _extract_audio_file(video_path, audio_path)
    source_media = draft.source_media.model_copy(
        update={
            "audio_path": str(audio_path),
            "duration_ms": draft.source_media.duration_ms or audio_meta.get("duration_ms"),
            "metadata": {
                **draft.source_media.metadata,
                "audio_extract_status": "completed",
                "audio_sample_rate": audio_meta.get("sample_rate"),
                "audio_channels": audio_meta.get("channels"),
            },
        }
    )
    stems = draft.stems.model_copy(update={"original_audio_path": str(audio_path)})
    return save_video_localization(project_id, draft.model_copy(update={"source_media": source_media, "stems": stems}))


def export_video_localization(project_id: str) -> VideoLocalizationExport | None:
    project = project_store.get_project(project_id)
    if not project:
        return None
    draft = get_video_localization(project_id)
    if not draft:
        return None
    next_draft = _with_fresh_gate(draft, updated_at=draft.updated_at)
    project.parameters = {**project.parameters, VIDEO_LOCALIZATION_KEY: next_draft.model_dump()}
    project_store.save_project(project)
    summary = {
        "cue_count": len(next_draft.cues),
        "ready_cue_count": sum(1 for cue in next_draft.cues if cue.review_status in {"ready", "locked"}),
        "blocker_count": len(next_draft.quality_gate.blockers),
        "warning_count": len(next_draft.quality_gate.warnings),
    }
    return VideoLocalizationExport(
        project_id=project.project_id,
        project_name=project.name,
        exported_at=now_iso(),
        export_summary=summary,
        **next_draft.model_dump(),
    )


def _with_fresh_gate(draft: VideoLocalizationDraft, updated_at: str | None) -> VideoLocalizationDraft:
    gate = evaluate_quality_gate(draft)
    status = _status_for_gate(draft, gate.status)
    return draft.model_copy(update={"quality_gate": gate, "status": status, "updated_at": updated_at})


def _status_for_gate(draft: VideoLocalizationDraft, gate_status: str) -> str:
    if gate_status == "blocked":
        return "blocked"
    if draft.status in {"tts_running", "candidate"}:
        return draft.status
    if gate_status == "pass" and draft.cues:
        return "ready_for_tts"
    if draft.cues:
        return "reviewing"
    return "draft"


async def _save_uploaded_video(project_id: str, file: UploadFile) -> tuple[Path, bytes]:
    filename = file.filename or "source.mp4"
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise AppException(400, "VIDEO_LOCALIZATION_UNSUPPORTED_MEDIA", "Only mp4, mov, m4v, webm, and mkv videos are supported")

    content = await file.read()
    if not content:
        raise AppException(400, "VIDEO_LOCALIZATION_EMPTY_UPLOAD", "Uploaded video is empty")

    settings_store.ensure_directories()
    source_dir = _project_video_localization_dir(project_id) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_path(source_dir / _safe_filename(filename))
    destination.write_bytes(content)
    return destination, content


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "source.mp4"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._-") or "source"
    suffix = Path(name).suffix.lower() or ".mp4"
    return f"{stem}{suffix}"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise AppException(500, "VIDEO_LOCALIZATION_UPLOAD_COLLISION", "Could not allocate a unique video path")


def _project_video_localization_dir(project_id: str) -> Path:
    settings_store.ensure_directories()
    return settings_store.expand_path(settings_store.get().project_dir) / project_id / "video_localization"


def _extract_audio_file(video_path: Path, audio_path: Path) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AppException(500, "VIDEO_LOCALIZATION_FFMPEG_MISSING", "ffmpeg is required to extract source audio")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "48000",
        str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not audio_path.exists():
        audio_path.unlink(missing_ok=True)
        raise AppException(500, "VIDEO_LOCALIZATION_AUDIO_EXTRACT_FAILED", "Failed to extract source audio")
    return audio_tools.probe_audio(audio_path)
