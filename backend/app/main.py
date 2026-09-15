from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mlx_indextts.version import __version__ as APP_VERSION

from app.api import (
    asr,
    audio_tools,
    batches,
    community_voice_packs,
    engines,
    evaluations,
    exports,
    generate,
    history,
    longform,
    presets,
    projects,
    seed_audio_assets,
    ser,
    settings,
    tasks,
    text_tools,
    video_localization,
    voice_seeds,
    voices,
)
from app.domains.video_localization import operation_queue as video_localization_operation_queue
from app.domains.video_localization import project_lifecycle_cleanup
from app.domains.video_localization import project_snapshot_projection
from app.domains.video_localization import service as video_localization_service
from app.domains.video_localization.dubbing_speed_policy import (
    decide_dubbing_capacity_repair_speed,
    decide_dubbing_speed,
)
from app.domains.video_localization.dubbing_production_service import (
    dubbing_production,
)
from app.errors import AppException, ProjectRevisionConflict
from app.openapi_docs import install_openapi_docs
from app.services import (
    asr_tasks,
    backend_instance,
    batch_queue,
    engine_registry,
    frontend_distribution,
    local_api_security,
    longform_queue,
    qwen_forced_aligner,
    runtime_capabilities,
    runtime_roles,
    settings_store,
    stem_separation_tasks,
    storage_maintenance,
    task_queue,
    video_localization_dubbing_executor,
    video_localization_operations,
    video_localization_tts_handoff,
)

START = time.monotonic()
logger = logging.getLogger(__name__)

video_localization_tts_handoff.configure_projection(
    video_localization_tts_handoff.VideoLocalizationTtsProjection(
        finalize_submission=(
            video_localization_service.finalize_single_tts_submission
        ),
        register_task=video_localization_service.register_single_tts_task,
        sync_result=video_localization_service.sync_single_tts_result,
        mark_workflow_terminal=(
            video_localization_service.mark_prepared_tts_workflow_terminal
        ),
        resolve_generation_priority=dubbing_production.resolve_generation_priority,
    )
)


video_localization_dubbing_executor.configure_projection(
    video_localization_dubbing_executor.DubbingExecutionProjection(
        read_production_run=dubbing_production.read_production_run,
        read_completion=dubbing_production.read_completion,
        get_video_localization=(
            video_localization_service.get_video_localization
        ),
        frozen_group_request=(
            video_localization_service.frozen_group_request
        ),
        compatible_generated_identities=(
            video_localization_service.compatible_generated_identities
        ),
        retry_runtime_fields=(
            video_localization_service.DUBBING_GENERATION_RUNTIME_FIELDS
        ),
        reserve_single_tts_handoff=(
            video_localization_service.reserve_single_tts_handoff
        ),
        build_single_tts_handoff=(
            video_localization_service.build_single_tts_handoff
        ),
        finalize_generated_candidate=(
            dubbing_production.finalize_generated_candidate
        ),
        recover_and_finalize_generated_group=(
            dubbing_production.recover_and_finalize_generated_group
        ),
        find_continuous_boundary_underfill_groups=(
            dubbing_production.find_continuous_boundary_underfill_groups
        ),
        mark_workflow_placement_failed=(
            video_localization_service.mark_tts_workflow_placement_failed
        ),
        reconcile_workflow_terminal_states=(
            video_localization_service.reconcile_dubbing_workflow_terminal_states
        ),
        record_group_failure=dubbing_production.record_group_failure,
        decide_generation_speed=decide_dubbing_speed,
        decide_capacity_repair_speed=(
            decide_dubbing_capacity_repair_speed
        ),
        set_group_scheduling_priority=dubbing_production.set_group_scheduling_priority,
        validate_recovery_decision=dubbing_production.validate_recovery_decision,
        assess_group_preflight=dubbing_production.assess_group_preflight,
        group_evidence_context_fingerprint=(
            dubbing_production.group_evidence_context_fingerprint
        ),
    )
)

video_localization_service.configure_tts_closeout_owner_lookup(
    video_localization_dubbing_executor.has_active_task_closeout
)


@asynccontextmanager
async def _runtime_lifespan(app: FastAPI):
    embedded_video_localization_worker = (
        runtime_roles
        .embedded_video_localization_worker_enabled()
    )
    settings_store.ensure_directories()
    try:
        storage_maintenance.start_background_maintenance()
    except Exception:
        logger.exception("Could not schedule background storage maintenance")
    try:
        project_lifecycle_cleanup.start_worker(replay_limit=100)
    except Exception:
        logger.exception("Startup project cleanup worker failed")
    try:
        project_snapshot_projection.replay_pending(limit=100)
    except Exception:
        logger.exception("Startup project snapshot replay failed")
    task_queue.start_worker()
    longform_queue.start_worker()
    if embedded_video_localization_worker:
        video_localization_operation_queue.start_worker()
    try:
        video_localization_tts_handoff.start_replay_worker(limit=100)
    except Exception:
        logger.exception(
            "Could not start video-localization TTS handoff replay"
        )
    try:
        yield
    finally:
        await asr_tasks.shutdown()
        await stem_separation_tasks.shutdown()
        await project_lifecycle_cleanup.shutdown()
        if embedded_video_localization_worker:
            await video_localization_operation_queue.shutdown()
        await longform_queue.shutdown()
        await task_queue.shutdown()
        await batch_queue.shutdown()
        engine_registry.shutdown_workers()
        qwen_forced_aligner.shutdown()


@asynccontextmanager
async def lifespan(app: FastAPI):
    with backend_instance.single_backend_instance():
        async with _runtime_lifespan(app):
            yield


app = FastAPI(
    title="Voice Studio",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(local_api_security.DEVELOPMENT_CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(local_api_security.LocalBrowserOriginMiddleware)

app.include_router(engines.router, prefix="/api/engines", tags=["engines"])
app.include_router(voices.router, prefix="/api/voices", tags=["voices"])
app.include_router(generate.router, prefix="/api/generate", tags=["generate"])
app.include_router(longform.router, prefix="/api/longform", tags=["longform"])
app.include_router(batches.router, prefix="/api/batches", tags=["batches"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(history.router, prefix="/api/history", tags=["history"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(video_localization.router, prefix="/api/projects", tags=["video-localization"])
app.include_router(exports.router, prefix="/api/exports", tags=["exports"])
app.include_router(evaluations.router, prefix="/api/evaluations", tags=["evaluations"])
app.include_router(presets.router, prefix="/api/presets", tags=["presets"])
app.include_router(voice_seeds.router, prefix="/api/voice-seeds", tags=["voice-seeds"])
app.include_router(community_voice_packs.router, prefix="/api/community-voice-packs", tags=["community-voice-packs"])
app.include_router(text_tools.router, prefix="/api/text-tools", tags=["text-tools"])
app.include_router(audio_tools.router, prefix="/api/audio-tools", tags=["audio-tools"])
app.include_router(seed_audio_assets.router, prefix="/api/seed-audio/assets", tags=["seed-audio-assets"])
app.include_router(asr.router, prefix="/api/asr", tags=["asr"])
app.include_router(ser.router, prefix="/api/ser", tags=["ser"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
install_openapi_docs(app)


@app.exception_handler(AppException)
async def app_exception_handler(request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail_dict}},
    )


@app.exception_handler(ProjectRevisionConflict)
async def project_revision_conflict_handler(
    request,
    exc: ProjectRevisionConflict,
):
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "PROJECT_REVISION_CONFLICT",
                "message": "项目刚刚被其他操作更新，请刷新后重试。",
                "detail": {},
            }
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code, content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail), "detail": {}}}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    detail = [
        {
            "type": str(error.get("type", "validation_error")),
            "loc": list(error.get("loc", ())),
            "msg": str(error.get("msg", "Invalid value")),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Request validation failed",
                "detail": detail,
            }
        },
    )


@app.get("/api/health")
async def health():
    capability_snapshot = runtime_capabilities.current_snapshot()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "uptime_seconds": round(time.monotonic() - START, 2),
        "engines": {x.manifest.engine_id: x.state.status.value for x in engine_registry.list_engines()},
        "data_dir": "<redacted>",
        "runtime_ready": capability_snapshot.core_ready,
        "optional_runtime_ready": capability_snapshot.optional_runtime_ready,
        "runtime_capabilities": capability_snapshot.optional_capabilities,
        "platform_capabilities": capability_snapshot.to_dict(),
        "startup_maintenance": storage_maintenance.background_maintenance_status(),
    }


frontend_distribution.install_frontend_distribution(app)
