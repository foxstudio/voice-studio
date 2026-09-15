from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote

from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.errors import AppException
from app.services.media_types import browser_audio_media_type

T = TypeVar("T")


def require_resource(value: T | None, *, code: str = "PROJECT_NOT_FOUND", message: str = "Project not found") -> T:
    if value is None:
        raise AppException(404, code, message)
    return value


def audio_file_response(path: Path | None, *, code: str, message: str, cache_control: str | None = None) -> FileResponse:
    if not path:
        raise AppException(404, code, message)
    response = FileResponse(path, media_type=browser_audio_media_type(path))
    if cache_control:
        response.headers["Cache-Control"] = cache_control
    return response


def media_file_response(
    path: Path | None,
    *,
    code: str,
    message: str,
    cache_control: str | None = None,
) -> FileResponse:
    if not path:
        raise AppException(404, code, message)
    response = FileResponse(path)
    if cache_control:
        response.headers["Cache-Control"] = cache_control
    return response


def download_file_response(path: Path | None, *, filename: str | None = None, code: str, message: str) -> FileResponse:
    if not path or not path.exists():
        raise AppException(404, code, message)
    return FileResponse(path, filename=filename or path.name)


def srt_attachment(content: str, *, filename: str) -> PlainTextResponse:
    return PlainTextResponse(
        content=content,
        media_type="application/x-subrip; charset=utf-8",
        headers={
            "Content-Disposition": _attachment_content_disposition(
                filename
            )
        },
    )


def json_attachment(content: BaseModel | dict, *, filename: str) -> JSONResponse:
    if isinstance(content, BaseModel):
        payload = json.loads(content.model_dump_json())
    else:
        payload = content
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": _attachment_content_disposition(
                filename
            )
        },
    )


def _attachment_content_disposition(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    ascii_fallback = f"download{extension}" if extension else "download"
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
