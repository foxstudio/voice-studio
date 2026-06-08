from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import asr, audio_tools, batches, community_voice_packs, engines, evaluations, exports, generate, history, longform, presets, projects, settings, tasks, text_tools, voice_seeds, voices
from app.models.exceptions import AppException
from app.services import asr_tasks, batch_queue, engine_registry, longform_queue, qwen_forced_aligner, settings_store, task_queue

START = time.monotonic()
app = FastAPI(title="Voice Studio", version="1.0.0")

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
app.include_router(longform.router, prefix="/api/longform", tags=["longform"])
app.include_router(batches.router, prefix="/api/batches", tags=["batches"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(history.router, prefix="/api/history", tags=["history"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(exports.router, prefix="/api/exports", tags=["exports"])
app.include_router(evaluations.router, prefix="/api/evaluations", tags=["evaluations"])
app.include_router(presets.router, prefix="/api/presets", tags=["presets"])
app.include_router(voice_seeds.router, prefix="/api/voice-seeds", tags=["voice-seeds"])
app.include_router(community_voice_packs.router, prefix="/api/community-voice-packs", tags=["community-voice-packs"])
app.include_router(text_tools.router, prefix="/api/text-tools", tags=["text-tools"])
app.include_router(audio_tools.router, prefix="/api/audio-tools", tags=["audio-tools"])
app.include_router(asr.router, prefix="/api/asr", tags=["asr"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])


@app.on_event("startup")
async def startup():
    settings_store.ensure_directories()
    task_queue.start_worker()
    longform_queue.start_worker()


@app.on_event("shutdown")
async def shutdown():
    await asr_tasks.shutdown()
    await longform_queue.shutdown()
    await task_queue.shutdown()
    await batch_queue.shutdown()
    qwen_forced_aligner.shutdown()


@app.exception_handler(AppException)
async def app_exception_handler(request, exc: AppException):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail_dict}})


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail), "detail": {}}})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": {"code": "INVALID_REQUEST", "message": "Request validation failed", "detail": exc.errors()}})


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "1.0.0",
        "uptime_seconds": round(time.monotonic() - START, 2),
        "engines": {x.manifest.engine_id: x.state.status.value for x in engine_registry.list_engines()},
        "data_dir": settings_store.get().data_dir,
    }
