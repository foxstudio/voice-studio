"""Voice Studio Backend - FastAPI Application"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import engines, voices, generate, tasks, history, settings

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


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}
