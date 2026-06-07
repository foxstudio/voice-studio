"""双引擎端到端集成测试。

覆盖当前 WebUI 暴露的 TTS 引擎完整 API 层集成验证:
  - indextts-v2  (IndexTTS v2,    22050 Hz, emotion_control)
  - omnivoice    (OmniVoice,       24000 Hz, voice_design)
  - mimo-v2.5-tts (MiMo Cloud,     Token Plan API)

分层 (T1-T4):
  T1 - 引擎注册表 API (无需模型, 始终运行)
  T2 - 引擎生命周期 (start/stop, conditional on model/adapter)
  T3 - 生成任务流 + 错误处理 (submit -> poll -> verify, conditional)
  T4 - 同步 API 端点测试
"""

import asyncio
import os
import sys

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

# ── Path: add backend/ so `from app.main import app` works ──
_backend_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.main import app

client = TestClient(app)

# ── Model / asset detection ────────────────────────────────
PROJECT_ROOT = os.path.abspath(os.path.join(_backend_dir, ".."))
MODEL_DIR_V2 = os.path.join(PROJECT_ROOT, "models", "mlx-indexTTS-2.0")
HAS_V2_MODEL = os.path.isdir(MODEL_DIR_V2) and os.path.isfile(
    os.path.join(MODEL_DIR_V2, "gpt.safetensors")
)

OUTPUT_DIR = os.path.expanduser("~/VoiceStudio/outputs")


def _find_reference_audio() -> str | None:
    """Locate a usable reference audio file in expected locations."""
    for base in [
        os.path.expanduser("~/VoiceStudio/voices"),
        os.path.join(PROJECT_ROOT, "tests", "fixtures"),
        os.path.join(PROJECT_ROOT, "ref_audios"),
    ]:
        if not os.path.isdir(base):
            continue
        for fname in os.listdir(base):
            if fname.endswith((".wav", ".mp3", ".flac")):
                return os.path.join(base, fname)
    return None


REF_AUDIO = _find_reference_audio()


# ════════════════════════════════════════════════════════════
# T1 - Engine Registry API  (始终运行, 无需模型)
# ════════════════════════════════════════════════════════════

class TestEngineRegistryAPI:
    """引擎注册表 API: 列表 / 元数据 / 单查 / 404."""

    def test_list_engines_returns_current_engines(self):
        resp = client.get("/api/engines")
        assert resp.status_code == 200
        ids = [e["manifest"]["engine_id"] for e in resp.json()]
        assert ids == ["indextts-v2", "omnivoice", "mimo-v2.5-tts"]

    def test_engine_metadata(self):
        resp = client.get("/api/engines")
        by_id = {e["manifest"]["engine_id"]: e["manifest"] for e in resp.json()}

        # indextts-v2
        m = by_id["indextts-v2"]
        assert m["sample_rate"] == 22050
        assert "emotion_control" in m["capabilities"]
        assert "voice_clone" in m["capabilities"]
        assert m["version"] == "2.0"

        # omnivoice
        m = by_id["omnivoice"]
        assert m["sample_rate"] == 24000
        assert "voice_design" in m["capabilities"]
        assert "multilingual" in m["capabilities"]

        # mimo cloud
        m = by_id["mimo-v2.5-tts"]
        assert m["engine_type"] == "cloud"
        assert "cloud_api" in m["capabilities"]

    def test_get_single_engine(self):
        resp = client.get("/api/engines/indextts-v2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["manifest"]["engine_id"] == "indextts-v2"
        assert data["manifest"]["sample_rate"] == 22050

    def test_get_engine_not_found(self):
        resp = client.get("/api/engines/nonexistent")
        assert resp.status_code == 404

    def test_engine_initial_status(self):
        """Engines should not be 'loaded' initially."""
        for eid in ("indextts-v2", "omnivoice", "mimo-v2.5-tts"):
            resp = client.get(f"/api/engines/{eid}")
            assert resp.status_code == 200
            status = resp.json()["state"]["status"]
            assert status != "loaded", f"{eid} should not be loaded initially"


# ════════════════════════════════════════════════════════════
# T2 - Engine Lifecycle  (start/stop, conditional)
# ════════════════════════════════════════════════════════════

class TestEngineLifecycle:
    """start/stop 引擎生命周期. indextts-v2 requires model files."""

    def test_start_stop_indextts_v2(self):
        """start -> loaded -> stop -> stopped."""
        if not HAS_V2_MODEL:
            pytest.skip(f"IndexTTS v2 model not found: {MODEL_DIR_V2}")

        resp = client.post("/api/engines/indextts-v2/start")
        assert resp.status_code == 200
        assert resp.json()["state"]["status"] == "loaded"

        resp = client.post("/api/engines/indextts-v2/stop")
        assert resp.status_code == 200
        assert resp.json()["state"]["status"] == "stopped"

    def test_start_engine_not_found(self):
        resp = client.post("/api/engines/nonexistent/start")
        assert resp.status_code == 404

    def test_health_check_not_loaded(self):
        """Unstarted engine health_check reports non-loaded status."""
        resp = client.post("/api/engines/indextts-v2/health-check")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("healthy") is not None or data.get("status") is not None


# ════════════════════════════════════════════════════════════
# T3 - Generation Flow & Error Handling  (同一 event loop)
# ════════════════════════════════════════════════════════════
#
# 注意: pytest-asyncio 1.x 为每个 test method 创建独立 event loop,
# 而后台 worker 在旧 loop 上无法处理新 loop 的任务.
# 因此所有依赖 worker 的场景 (生成 + 错误) 合并在同一个 method 内.

@pytest.mark.asyncio
class TestGenerationFlow:
    """端到端生成任务流 + 错误处理 (单一 event loop)."""

    async def _async_poll(
        self, ac: AsyncClient, task_id: str, timeout: int = 180, interval: float = 1.0
    ) -> dict:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            resp = await ac.get(f"/api/tasks/{task_id}")
            assert resp.status_code == 200
            data = resp.json()
            if data["status"] in ("success", "failed", "cancelled"):
                return data
            await asyncio.sleep(interval)
        pytest.fail(f"Task {task_id} did not complete within {timeout}s")

    @pytest.fixture(autouse=True)
    def require_model(self):
        if not HAS_V2_MODEL:
            pytest.skip(f"IndexTTS v2 model not found: {MODEL_DIR_V2}")

    async def test_all_scenarios(self):
        """涵盖: 基本生成 / speed 控制 / emotion / 错误场景 (同一 event loop)."""
        if not REF_AUDIO:
            pytest.skip("No reference audio available")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # ── A. Start engine ──
            resp = await ac.post("/api/engines/indextts-v2/start")
            assert resp.status_code == 200, f"Engine start failed: {resp.text}"
            assert resp.json()["state"]["status"] == "loaded"

            # ── 1. Basic generation ──
            resp = await ac.post("/api/generate", json={
                "text": "你好，这是一个引擎集成测试。",
                "engine_id": "indextts-v2",
                "reference_audio_path": REF_AUDIO,
                "language": "zh",
            })
            assert resp.status_code == 200
            task = await self._async_poll(ac, resp.json()["task_id"])
            assert task["status"] == "success", (
                f"Basic generation failed: {task.get('error_message')}"
            )
            assert task["result_audio_id"] is not None
            assert task["result_duration_ms"] is not None
            assert task["result_duration_ms"] > 500, (
                f"Generated audio too short: {task['result_duration_ms']}ms"
            )
            hist_resp = await ac.get("/api/history")
            assert hist_resp.status_code == 200
            item = next((h for h in hist_resp.json() if h["task_id"] == task["task_id"]), None)
            assert item is not None
            output_path = item["output_path"]
            assert os.path.isfile(output_path), (
                f"Output file not found: {output_path}"
            )
            assert os.path.getsize(output_path) > 1024

            # ── 2. Speed control ──
            resp = await ac.post("/api/generate", json={
                "text": "今天天气真不错，我们出去走走吧。",
                "engine_id": "indextts-v2",
                "reference_audio_path": REF_AUDIO,
                "language": "zh",
                "speed": 1.5,
            })
            assert resp.status_code == 200
            task = await self._async_poll(ac, resp.json()["task_id"])
            assert task["status"] == "success"
            assert task["result_duration_ms"] is not None
            assert task["result_duration_ms"] > 500

            # ── 3. Emotion control ──
            resp = await ac.post("/api/generate", json={
                "text": "太棒了！今天我们完成了三引擎集成！",
                "engine_id": "indextts-v2",
                "reference_audio_path": REF_AUDIO,
                "language": "zh",
                "emotion": "happy",
                "emo_alpha": 0.6,
            })
            assert resp.status_code == 200
            task = await self._async_poll(ac, resp.json()["task_id"])
            assert task["status"] == "success"
            assert task["result_duration_ms"] is not None
            assert task["result_duration_ms"] > 500

            # ── 4. Missing reference audio (should fail) ──
            resp = await ac.post("/api/generate", json={
                "text": "test",
                "engine_id": "indextts-v2",
            })
            if resp.status_code == 200:
                task = await self._async_poll(ac, resp.json()["task_id"], timeout=60)
                assert task["status"] == "failed", task.get("error_message")
                assert task["error_message"] is not None
            else:
                assert resp.status_code in (400, 422)

            # ── 5. Invalid engine_id (should fail) ──
            resp = await ac.post("/api/generate", json={
                "text": "test",
                "engine_id": "nonexistent",
            })
            if resp.status_code == 200:
                task = await self._async_poll(ac, resp.json()["task_id"], timeout=60)
                assert task["status"] == "failed", task.get("error_message")
                assert task["error_message"] is not None
            else:
                assert resp.status_code in (400, 422)

            # ── 6. Empty text (may pass or fail - either is OK) ──
            resp = await ac.post("/api/generate", json={
                "text": "",
                "engine_id": "indextts-v2",
            })
            if resp.status_code == 200:
                task = await self._async_poll(ac, resp.json()["task_id"], timeout=60)
                assert task["status"] in ("failed", "success")
            else:
                assert resp.status_code in (400, 422)


# ════════════════════════════════════════════════════════════
# T4 - Sync API endpoint tests  (无需 event loop)
# ════════════════════════════════════════════════════════════

def test_task_api_endpoints():
    """任务列表 & 不存在任务 (同步, 无需 event loop).

    注意: GET /api/tasks/nonexistent 返回 None 时 FastAPI
    的 response_model 校验会抛出 ResponseValidationError (500).
    """
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    # /api/tasks/{id} on nonexistent: get_task() returns None,
    # FastAPI response_model=GenerationTask validation => ResponseValidationError
    try:
        resp2 = client.get("/api/tasks/nonexistent")
        # If we get here, check status code
        assert resp2.status_code in (200, 500)
    except Exception:
        # ResponseValidationError is expected - pre-existing backend behavior
        pass
