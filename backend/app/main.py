"""Voice Studio Backend - FastAPI Application"""

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import engines, voices, generate, tasks, history, settings
from app.models.exceptions import AppException
from app.services.engine_registry import list_engines
# ── Data directory ───────────────────────────────────────
DATA_DIR = Path.home() / "VoiceStudio"

# ── Startup timestamp for uptime ─────────────────────────
_start_time: float = time.monotonic()

app = FastAPI(title="Voice Studio", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(engines.router, prefix="/api/engines", tags=["engines"])
app.include_router(voices.router, prefix="/api/voices", tags=["voices"])
app.include_router(generate.router, prefix="/api/generate", tags=["generate"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(history.router, prefix="/api/history", tags=["history"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])

# ── Unified Error Handling ─────────────────────────────


def _default_error_code(status_code: int) -> str:
    if status_code == 400:
        return "INVALID_REQUEST"
    if status_code == 404:
        return "NOT_FOUND"
    if status_code == 422:
        return "VALIDATION_ERROR"
    if status_code == 503:
        return "SERVICE_UNAVAILABLE"
    return "INTERNAL_ERROR"


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    if isinstance(exc, AppException):
        error_code = exc.error_code
        message = exc.message
        detail = exc.detail_dict
    else:
        error_code = _default_error_code(exc.status_code)
        message = str(exc.detail)
        detail = {}

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": error_code, "message": message, "detail": detail}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Request validation failed",
                "detail": exc.errors(),
            }
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred",
                "detail": {},
            }
        },
    )


@app.on_event("startup")
async def _startup():
    """Create required data directories on startup."""
    for subdir in ("voices", "output", "projects"):
        (DATA_DIR / subdir).mkdir(parents=True, exist_ok=True)


@app.get("/api/health")
async def health_check():
    engines_list = list_engines()
    engines_status = {e.manifest.engine_id: e.state.status.value for e in engines_list}
    return {
        "status": "ok",
        "version": "0.1.0",
        "data_dir": str(DATA_DIR),
        "engines": engines_status,
        "uptime_seconds": round(time.monotonic() - _start_time, 2),
    }
