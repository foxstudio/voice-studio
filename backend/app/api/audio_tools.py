from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.errors import AppException
from app.schemas.stem_separation_task import (
    StemSeparationCancelResult,
    StemSeparationDeleteResult,
    StemSeparationTask,
)
from app.services import (
    audio_tools,
    settings_store,
    stem_separation_tasks,
)

router = APIRouter()


class AudioToolRequest(BaseModel):
    audio_ids: list[str]
    format: str = "wav"
    normalize: bool = False
    trim_silence: bool = False
    silence_ms: int = 300


@router.post("/merge")
async def merge_audio(req: AudioToolRequest):
    paths = []
    for audio_id in req.audio_ids:
        for ext in ["wav", "mp3", "flac"]:
            path = settings_store.output_dir() / f"{audio_id}.{ext}"
            if path.exists():
                paths.append(path)
                break
    if not paths:
        raise AppException(400, "AUDIO_NOT_FOUND", "No audio files found")
    dest = settings_store.export_dir() / f"merge-{len(paths)}-{req.silence_ms}.{req.format}"
    audio_tools.merge_files(paths, dest, req.format, req.silence_ms, req.normalize)
    return {"path": str(dest), "count": len(paths)}


@router.post(
    "/stem-tasks",
    response_model=StemSeparationTask,
    summary="创建人声分离任务",
)
async def create_stem_separation_task(file: UploadFile = File(...)):
    return await stem_separation_tasks.submit(file)


@router.get(
    "/stem-tasks/{task_id}",
    response_model=StemSeparationTask,
    summary="获取人声分离任务",
)
async def get_stem_separation_task(task_id: str):
    task = stem_separation_tasks.get_task(task_id)
    if not task:
        raise AppException(404, "STEM_TASK_NOT_FOUND", "Stem task not found")
    return task


@router.get(
    "/stem-tasks/{task_id}/audio/{kind}",
    summary="下载人声分离音轨",
)
async def get_stem_separation_audio(task_id: str, kind: str):
    task = stem_separation_tasks.get_task(task_id)
    if not task:
        raise AppException(404, "STEM_TASK_NOT_FOUND", "Stem task not found")
    path = stem_separation_tasks.artifact_path(task_id, kind)
    ready = task.vocals_ready if kind == "vocals" else task.background_ready
    if task.status.value != "success" or not ready or not path.is_file():
        raise AppException(409, "STEM_ARTIFACT_NOT_READY", "Stem artifact is not ready")
    return FileResponse(path, media_type="audio/wav", filename=f"{kind}.wav")


@router.post(
    "/stem-tasks/{task_id}/cancel",
    response_model=StemSeparationCancelResult,
    summary="取消人声分离任务",
)
async def cancel_stem_separation_task(task_id: str):
    task = stem_separation_tasks.cancel_task(task_id)
    if not task:
        raise AppException(404, "STEM_TASK_NOT_FOUND", "Stem task not found")
    return StemSeparationCancelResult(task_id=task.task_id, status=task.status)


@router.delete(
    "/stem-tasks/{task_id}",
    response_model=StemSeparationDeleteResult,
    summary="删除人声分离任务",
)
async def delete_stem_separation_task(task_id: str):
    if not stem_separation_tasks.delete_task(task_id):
        raise AppException(404, "STEM_TASK_NOT_FOUND", "Stem task not found")
    return StemSeparationDeleteResult(task_id=task_id, status="deleted")
