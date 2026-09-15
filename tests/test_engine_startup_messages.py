from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import engine_registry  # noqa: E402
from app.schemas.voice_studio import EngineStatus  # noqa: E402


@pytest.mark.parametrize("status, expected", [
    ("model_missing", "尚未安装模型"),
    ("api_key_missing", "尚未配置云端密钥"),
    ("package_missing", "缺少引擎所需的程序依赖"),
    ("unexpected_provider_failure", "引擎启动检查未通过"),
])
def test_startup_and_generation_share_safe_actionable_failure(monkeypatch, status, expected):
    engine_id = "indextts-v2"
    detail = deepcopy(engine_registry._ENGINES[engine_id])
    detail.state.status = EngineStatus.stopped
    monkeypatch.setitem(engine_registry._ENGINES, engine_id, detail)
    health = {"healthy": False, "status": status, "model_path": "/private/internal-model", "detail": "private diagnostic"}
    monkeypatch.setattr(engine_registry, "health_check", lambda _: health)
    started = engine_registry.start_engine(engine_id)
    assert started.state.status == EngineStatus.error
    assert expected in started.state.error_message
    assert "private" not in started.state.error_message
    with pytest.raises(RuntimeError, match=expected):
        engine_registry.ensure_loaded(engine_id)
    assert engine_registry.health_check(engine_id) == health


def test_successful_start_clears_previous_failure(monkeypatch):
    engine_id = "indextts-v2"
    detail = deepcopy(engine_registry._ENGINES[engine_id])
    detail.state.error_message = "previous failure"
    monkeypatch.setitem(engine_registry._ENGINES, engine_id, detail)
    monkeypatch.setattr(engine_registry, "health_check", lambda _: {"healthy": True, "model_path": "model"})
    started = engine_registry.start_engine(engine_id)
    assert started.state.status == EngineStatus.loaded
    assert started.state.error_message is None
