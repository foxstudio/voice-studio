from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from app.errors import AppException
from app.schemas.voice_studio import (
    TimestampSupplementRequest,
    TranscriptionBatchDeleteRequest,
    TranscriptionBatchSupplementRequest,
    TranscriptionRecord,
    TranscriptionTask,
)
from app.schemas.subtitle_evidence import (
    SubtitleEvidenceRequest,
    SubtitleEvidenceResult,
)
from app.services import (
    asr_service,
    asr_tasks,
    audio_tools,
    custom_reference_store,
    database as db,
    settings_store,
    subtitle_evidence,
)

router = APIRouter()


@router.post("/transcribe", response_model=TranscriptionRecord)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    engine_id: str = Form("mimo-v2.5-asr"),
):
    suffix = Path(file.filename or "transcribe.wav").suffix.lower() or ".wav"
    asr_service.validate_request(engine_id, language, suffix)

    record = TranscriptionRecord(engine_id=engine_id, filename=file.filename or f"upload{suffix}", language=language, text="")
    upload_path = asr_service.upload_path_for(engine_id, record.transcription_id, suffix)
    content = await file.read()
    upload_path.write_bytes(content)

    duration_ms = None
    try:
        duration_ms = audio_tools.probe_audio(upload_path).get("duration_ms")
    except Exception:
        duration_ms = None

    try:
        result = asr_service.transcribe(engine_id=engine_id, audio_path=str(upload_path), language=language)
    except Exception:
        upload_path.unlink(missing_ok=True)
        raise

    record.text = result["text"]
    record.segments = asr_service.normalize_segments(result.get("segments"))
    for key, value in asr_service.timestamp_metadata_for(record.engine_id, record.segments).items():
        setattr(record, key, value)
    record.duration_ms = duration_ms
    record.size_bytes = len(content)
    record.usage_seconds = result.get("usage_seconds")
    record.provider_response_id = result.get("provider_response_id")
    db.upsert(
        "transcriptions",
        record.transcription_id,
        {**record.model_dump(), "source_audio_path": str(upload_path)},
        "created_at",
    )
    return record


class TranscriptionHistoryPage(BaseModel):
    items: list[TranscriptionRecord]
    total: int


@router.get("/history", response_model=TranscriptionHistoryPage)
async def transcription_history(
    limit: int = Query(0, ge=0, le=500),
    offset: int = Query(0, ge=0),
):
    """按创建时间倒序返回；limit=0 表示不分页，返回偏移之后的所有记录。"""
    # limit=-1 表示取全部：否则 db.list_all 会默认只给 100 条，总数也会跟着算错。
    items = db.list_all("transcriptions", "created_at", limit=-1)
    total = len(items)
    page = items[offset:offset + limit] if limit else items[offset:]
    return TranscriptionHistoryPage(
        items=[TranscriptionRecord(**item) for item in page],
        total=total,
    )


@router.post("/tasks", response_model=TranscriptionTask)
async def create_transcription_task(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    engine_id: str = Form("mimo-v2.5-asr"),
):
    return await asr_tasks.submit(file, language, engine_id)


@router.get("/tasks", response_model=list[TranscriptionTask])
async def list_transcription_tasks():
    return asr_tasks.list_tasks()


@router.get("/tasks/{task_id}", response_model=TranscriptionTask)
async def get_transcription_task(task_id: str):
    task = asr_tasks.get_task(task_id)
    if not task:
        raise AppException(404, "ASR_TASK_NOT_FOUND", "ASR task not found")
    return task


@router.post(
    "/{transcription_id}/subtitle-evidence",
    response_model=SubtitleEvidenceResult,
    summary="生成严格字幕时间证据",
    description=(
        "复用已上传的音频，返回真实逐字时间、停顿和字幕入点。"
        "调用方可以提交已经锁定的字幕文字；本接口不决定最终字幕文案。"
    ),
)
async def create_subtitle_evidence(
    transcription_id: str,
    body: SubtitleEvidenceRequest,
):
    return await asyncio.to_thread(
        subtitle_evidence.get_service().analyze,
        transcription_id,
        body,
    )


@router.post("/tasks/{task_id}/cancel")
async def cancel_transcription_task(task_id: str):
    result = asr_tasks.cancel_task(task_id)
    if result["status"] == "not_found":
        raise AppException(404, "ASR_TASK_NOT_FOUND", "ASR task not found")
    return result


@router.post("/tasks/{task_id}/retry")
async def retry_transcription_task(task_id: str):
    task = asr_tasks.retry_task(task_id)
    return {"task_id": task.task_id, "status": task.status}


@router.delete("/tasks/{task_id}")
async def delete_transcription_task(task_id: str):
    result = asr_tasks.delete_task(task_id)
    if result["status"] == "not_found":
        raise AppException(404, "ASR_TASK_NOT_FOUND", "ASR task not found")
    if result["status"] == "active_task":
        raise AppException(409, "ASR_TASK_ACTIVE", "ASR task is still active")
    return result


@router.get("/{transcription_id}", response_model=TranscriptionRecord)
async def get_transcription(transcription_id: str):
    data = db.get_one("transcriptions", "transcription_id", transcription_id)
    if not data:
        raise AppException(404, "TRANSCRIPTION_NOT_FOUND", "Transcription not found")
    return TranscriptionRecord(**data)


@router.delete("/{transcription_id}")
async def delete_transcription(transcription_id: str):
    data = db.get_one("transcriptions", "transcription_id", transcription_id)
    if not data:
        raise AppException(404, "TRANSCRIPTION_NOT_FOUND", "Transcription not found")
    source_audio_path = data.get("source_audio_path")
    if source_audio_path:
        Path(source_audio_path).unlink(missing_ok=True)
    db.delete_one("transcriptions", "transcription_id", transcription_id)
    return {"status": "deleted", "transcription_id": transcription_id}


@router.post("/batch-delete")
async def batch_delete_transcriptions(body: TranscriptionBatchDeleteRequest):
    deleted: list[str] = []
    for transcription_id in body.transcription_ids:
        data = db.get_one("transcriptions", "transcription_id", transcription_id)
        if not data:
            continue
        source_audio_path = data.get("source_audio_path")
        if source_audio_path:
            Path(source_audio_path).unlink(missing_ok=True)
        db.delete_one("transcriptions", "transcription_id", transcription_id)
        deleted.append(transcription_id)
    return {"status": "deleted", "deleted_ids": deleted}


@router.post("/{transcription_id}/timestamps", response_model=TranscriptionRecord)
async def supplement_transcription_timestamps(transcription_id: str, body: TimestampSupplementRequest):
    data = db.get_one("transcriptions", "transcription_id", transcription_id)
    if not data:
        raise AppException(404, "TRANSCRIPTION_NOT_FOUND", "Transcription not found")
    source_audio_path = data.get("source_audio_path")
    if not source_audio_path:
        raise AppException(400, "ASR_SOURCE_AUDIO_MISSING", "This transcription does not retain its source audio")
    record = asr_service.supplement_timestamps(
        record=TranscriptionRecord(**data),
        source_audio_path=source_audio_path,
        strategy=body.strategy,
        overwrite=body.overwrite,
    )
    db.upsert("transcriptions", transcription_id, {**data, **record.model_dump()}, "created_at")
    return record


@router.post("/timestamps/batch", response_model=list[TranscriptionRecord])
async def supplement_transcription_timestamps_batch(body: TranscriptionBatchSupplementRequest):
    records: list[TranscriptionRecord] = []
    for transcription_id in body.transcription_ids:
        data = db.get_one("transcriptions", "transcription_id", transcription_id)
        if not data:
            continue
        source_audio_path = data.get("source_audio_path")
        if not source_audio_path:
            continue
        record = asr_service.supplement_timestamps(
            record=TranscriptionRecord(**data),
            source_audio_path=source_audio_path,
            strategy=body.strategy,
            overwrite=body.overwrite,
        )
        db.upsert("transcriptions", transcription_id, {**data, **record.model_dump()}, "created_at")
        records.append(record)
    return records


@router.get("/{transcription_id}/audio")
async def transcription_source_audio(transcription_id: str):
    """返回转写时保留的原始音频，供结果区边听边看字幕。"""
    data = db.get_one("transcriptions", "transcription_id", transcription_id)
    if not data:
        raise AppException(404, "TRANSCRIPTION_NOT_FOUND", "Transcription not found")
    raw_path = data.get("source_audio_path")
    if not raw_path:
        raise AppException(404, "SOURCE_AUDIO_UNAVAILABLE", "这条转写没有保留原始音频")
    path = Path(raw_path)
    # 源音频可能来自两处：转写时直接上传的（cache/asr_uploads），
    # 或者转写的是音色库/参考音频（assets/reference-audio/custom）。
    # 只允许这两类受管目录，避免记录被篡改后变成任意文件读取。
    resolved = path.resolve()
    upload_root = (settings_store.cache_dir() / "asr_uploads").resolve()
    in_uploads = resolved == upload_root or upload_root in resolved.parents
    if not in_uploads and not custom_reference_store.is_managed_custom_path(resolved):
        raise AppException(403, "SOURCE_AUDIO_FORBIDDEN", "源音频不在允许的目录内")
    if not path.is_file():
        raise AppException(404, "SOURCE_AUDIO_MISSING", "源音频文件已不存在")
    return FileResponse(path)


@router.get("/{transcription_id}/export")
async def export_transcription(transcription_id: str, format: str = "txt"):
    data = db.get_one("transcriptions", "transcription_id", transcription_id)
    if not data:
        raise AppException(404, "TRANSCRIPTION_NOT_FOUND", "Transcription not found")
    record = TranscriptionRecord(**data)
    body = asr_service.export_text(record, format)
    stem = Path(record.filename).stem or "transcript"
    media_type = "application/x-subrip" if format == "srt" else "text/plain; charset=utf-8"
    headers = {"Content-Disposition": f'attachment; filename="{stem}.{format}"'}
    return PlainTextResponse(body, media_type=media_type, headers=headers)
