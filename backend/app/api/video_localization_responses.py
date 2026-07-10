from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.errors import AppException

T = TypeVar("T")


def require_resource(value: T | None, *, code: str = "PROJECT_NOT_FOUND", message: str = "Project not found") -> T:
    if value is None:
        raise AppException(404, code, message)
    return value


def audio_file_response(path: Path | None, *, code: str, message: str) -> FileResponse:
    if not path:
        raise AppException(404, code, message)
    return FileResponse(path, filename=path.name)


def download_file_response(path: Path | None, *, filename: str | None = None, code: str, message: str) -> FileResponse:
    if not path or not path.exists():
        raise AppException(404, code, message)
    return FileResponse(path, filename=filename or path.name)


def srt_attachment(content: str, *, filename: str) -> PlainTextResponse:
    return PlainTextResponse(
        content=content,
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def json_attachment(content: BaseModel | dict, *, filename: str) -> JSONResponse:
    if isinstance(content, BaseModel):
        payload = json.loads(content.model_dump_json())
    else:
        payload = content
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
