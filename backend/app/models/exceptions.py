"""Custom exceptions for structured error responses."""

from fastapi import HTTPException


class AppException(HTTPException):
    """Application exception with structured error code.

    Usage:
        raise AppException(404, "VOICE_NOT_FOUND", "Voice not found")
    """

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        detail: dict | None = None,
    ):
        self.error_code = error_code
        self.message = message
        self.detail_dict = detail or {}
        super().__init__(status_code=status_code, detail=message)
