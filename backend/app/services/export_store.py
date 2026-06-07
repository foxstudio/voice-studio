from __future__ import annotations

from pathlib import Path

from app.models.schemas import ExportRecord, ExportRequest
from app.services import audio_tools, database as db, history_store, settings_store


def create_export(req: ExportRequest) -> ExportRecord:
    paths: list[Path] = []
    for result_id in req.result_ids:
        path = history_store.audio_path(result_id)
        if path:
            paths.append(path)
    for audio_id in req.audio_ids:
        for ext in ["wav", "mp3", "flac"]:
            path = settings_store.output_dir() / f"{audio_id}.{ext}"
            if path.exists():
                paths.append(path)
                break
    if req.project_id:
        for item in history_store.list_history(limit=1000):
            if item.project_id == req.project_id and item.output_path:
                paths.append(Path(item.output_path))
    if not paths:
        raise ValueError("No exportable audio found")
    record = ExportRecord(path="", format=req.format, source_count=len(paths))
    dest = settings_store.export_dir() / f"{record.export_id}.{req.format}"
    if len(paths) == 1:
        audio_tools.copy_or_convert(paths[0], dest, req.format)
    else:
        audio_tools.merge_files(paths, dest, req.format, req.silence_ms, req.normalize)
    record.path = str(dest if dest.exists() else dest.with_suffix(".wav"))
    db.upsert("exports", record.export_id, record.model_dump())
    return record


def list_exports() -> list[ExportRecord]:
    return [ExportRecord(**d) for d in db.list_all("exports", "created_at")]

