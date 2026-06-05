"""引擎中心 API"""

from fastapi import APIRouter

from app.models.schemas import EngineDetail, EngineManifest, EngineState
from app.services import engine_registry

router = APIRouter()


@router.get("", response_model=list[EngineDetail])
async def list_engines():
    return engine_registry.list_engines()


@router.get("/{engine_id}", response_model=EngineDetail)
async def get_engine(engine_id: str):
    return engine_registry.get_engine(engine_id)


@router.post("/{engine_id}/start")
async def start_engine(engine_id: str):
    engine_registry.start_engine(engine_id)
    return {"status": "starting"}


@router.post("/{engine_id}/stop")
async def stop_engine(engine_id: str):
    engine_registry.stop_engine(engine_id)
    return {"status": "stopped"}


@router.post("/{engine_id}/health-check")
async def health_check_engine(engine_id: str):
    result = engine_registry.health_check(engine_id)
    return result
