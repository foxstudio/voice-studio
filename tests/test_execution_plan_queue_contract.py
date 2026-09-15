from __future__ import annotations

import sys
import wave
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import (  # noqa: E402
    AppSettings,
    BatchGenerateRequest,
    BatchSegmentInput,
    GenerateRequest,
)
from app.services import (  # noqa: E402
    batch_inference_runner,
    batch_queue,
    database,
    runtime_capabilities,
    task_queue,
)


@pytest.fixture(autouse=True)
def _restore_database_path():
    original = database.DB_PATH
    try:
        yield
    finally:
        database.set_db_path(original)


def _reference_audio(tmp_path: Path) -> str:
    path = tmp_path / "reference.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00\x00" * 1_600)
    return str(path)


def test_single_and_batch_builders_forward_resolved_device(tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    monkeypatch.setattr(task_queue.settings_store, "model_path", lambda _engine_id: model_dir)
    monkeypatch.setattr(batch_queue.settings_store, "model_path", lambda _engine_id: model_dir)
    reference = _reference_audio(tmp_path)

    single = task_queue._kwargs(
        GenerateRequest(
            text="设备参数单次直达。",
            engine_id="indextts-v2",
            reference_audio_path=reference,
        ),
        str(tmp_path / "single.wav"),
        device="cpu",
    )
    batch = batch_queue._common_kwargs(
        BatchGenerateRequest(
            engine_id="indextts-v2",
            reference_audio_path=reference,
            segments=[BatchSegmentInput(text="设备参数批量直达。")],
        ),
        device="cpu",
    )
    omni = task_queue._kwargs(
        GenerateRequest(text="OmniVoice 设备直达。", engine_id="omnivoice"),
        str(tmp_path / "omni.wav"),
        device="cuda",
    )
    f5 = task_queue._kwargs(
        GenerateRequest(
            text="F5 设备直达。",
            engine_id="f5-tts",
            reference_audio_path=reference,
            ref_text="参考台词。",
        ),
        str(tmp_path / "f5.wav"),
        device="cpu",
    )

    assert single["device"] == "cpu"
    assert batch["device"] == "cpu"
    assert omni["device"] == "cuda"
    assert f5["device"] == "cpu"


def test_batch_runner_does_not_drop_device_before_model_construction(monkeypatch):
    calls: list[tuple[str, str]] = []

    class FakeIndexTTS:
        def __init__(self, model_dir: str, *, device: str) -> None:
            calls.append((model_dir, device))

    monkeypatch.setattr("mlx_indextts.generate_v2.IndexTTSv2", FakeIndexTTS)

    result = batch_inference_runner.run_indextts_v2(
        {
            "common": {"model_dir": "/managed/models/indextts-v2", "device": "cpu"},
            "segments": [],
        }
    )

    assert result == []
    assert calls == [("/managed/models/indextts-v2", "cpu")]


@pytest.mark.asyncio
async def test_task_submission_persists_versioned_execution_plan(tmp_path, monkeypatch):
    database.set_db_path(tmp_path / "voice_studio.db")
    snapshot = runtime_capabilities.RuntimeCapabilitySnapshot(
        schema_version=1,
        operating_system="windows",
        architecture="x86_64",
        python_version="3.12.0",
        available_devices=("cuda", "cpu"),
        preferred_device="cuda",
        frameworks={"mlx": False, "torch": True},
        optional_capabilities={},
        framework_devices={"mlx": (), "torch": ("cuda", "cpu")},
    )
    monkeypatch.setattr(
        task_queue.execution_plan.runtime_capabilities,
        "current_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(task_queue.settings_store, "get", lambda: AppSettings(device="cuda"))
    monkeypatch.setattr(task_queue, "start_worker", lambda: None)

    task_id = await task_queue.submit(
        GenerateRequest(text="保存执行计划。", engine_id="omnivoice")
    )
    task = task_queue.get_task(task_id)

    assert task is not None
    assert task.parameters["_execution_plan"] == {
        "schema_version": 1,
        "engine_id": "omnivoice",
        "operating_system": "windows",
        "architecture": "x86_64",
        "runtime_family": "pytorch",
        "requested_device": "cuda",
        "device": "cuda",
    }


@pytest.mark.asyncio
async def test_f5_submission_persists_external_pytorch_device_plan(
    tmp_path,
    monkeypatch,
):
    database.set_db_path(tmp_path / "voice_studio.db")
    snapshot = runtime_capabilities.RuntimeCapabilitySnapshot(
        schema_version=1,
        operating_system="windows",
        architecture="x86_64",
        python_version="3.12.0",
        available_devices=("cuda", "cpu"),
        preferred_device="cuda",
        frameworks={"mlx": False, "torch": True},
        optional_capabilities={},
        framework_devices={"mlx": (), "torch": ("cuda", "cpu")},
    )
    monkeypatch.setattr(
        task_queue.execution_plan.runtime_capabilities,
        "current_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        task_queue.settings_store,
        "get",
        lambda: AppSettings(device="auto"),
    )
    monkeypatch.setattr(task_queue, "start_worker", lambda: None)

    task_id = await task_queue.submit(
        GenerateRequest(
            text="保存 F5 执行计划。",
            engine_id="f5-tts",
            reference_audio_path=_reference_audio(tmp_path),
            ref_text="参考台词。",
        )
    )
    task = task_queue.get_task(task_id)

    assert task is not None
    assert task.parameters["_execution_plan"] == {
        "schema_version": 1,
        "engine_id": "f5-tts",
        "operating_system": "windows",
        "architecture": "x86_64",
        "runtime_family": "pytorch_external",
        "requested_device": "auto",
        "device": "cuda",
    }
