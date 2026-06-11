from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.errors import AppException
from app.schemas.voice_studio import ExportRecord, ExportRequest
from app.services import export_store

router = APIRouter()


@router.get("", response_model=list[ExportRecord])
async def list_exports():
    return export_store.list_exports()


@router.post("", response_model=ExportRecord)
async def create_export(req: ExportRequest):
    try:
        return export_store.create_export(req)
    except ValueError as exc:
        raise AppException(400, "EXPORT_FAILED", str(exc)) from exc


@router.get("/{export_id}/download")
async def download_export(export_id: str):
    record = next((x for x in export_store.list_exports() if x.export_id == export_id), None)
    if not record:
        raise AppException(404, "EXPORT_NOT_FOUND", "Export not found")
    return FileResponse(record.path)

