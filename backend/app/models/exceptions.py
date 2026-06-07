from __future__ import annotations

from fastapi import HTTPException


class AppException(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, detail: dict | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.detail_dict = detail or {}

