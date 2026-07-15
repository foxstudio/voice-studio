from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import batch_inference_runner, engine_health, inference_runner  # noqa: E402


def _create_snapshot(cache_dir: Path, revision: str = "local-revision") -> Path:
    repository = cache_dir / "models--k2-fsa--OmniVoice"
    snapshot = repository / "snapshots" / revision
    for name in inference_runner.OMNIVOICE_REQUIRED_FILES:
        path = snapshot / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cached")
    ref = repository / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(revision, encoding="utf-8")
    return snapshot


def _install_fake_omnivoice(monkeypatch, generated_audio: Path):
    calls: list[tuple[str, dict]] = []

    class FakeModel:
        sampling_rate = 24000

        def generate(self, **kwargs):
            return str(generated_audio)

    class FakeOmniVoice:
        @classmethod
        def from_pretrained(cls, model_path, **kwargs):
            calls.append((model_path, kwargs))
            return FakeModel()

    class FakeGenerationConfig:
        @classmethod
        def from_dict(cls, values):
            return values

    package = types.ModuleType("omnivoice")
    package.OmniVoice = FakeOmniVoice
    models = types.ModuleType("omnivoice.models")
    model_module = types.ModuleType("omnivoice.models.omnivoice")
    model_module.OmniVoiceGenerationConfig = FakeGenerationConfig
    monkeypatch.setitem(sys.modules, "omnivoice", package)
    monkeypatch.setitem(sys.modules, "omnivoice.models", models)
    monkeypatch.setitem(sys.modules, "omnivoice.models.omnivoice", model_module)
    return calls


def test_omnivoice_local_snapshot_uses_hf_cache_without_hub_lookup(tmp_path, monkeypatch):
    cache_dir = tmp_path / "hub"
    snapshot = _create_snapshot(cache_dir)
    monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir))

    assert inference_runner.omnivoice_local_snapshot() == snapshot.resolve()


def test_omnivoice_local_snapshot_missing_has_chinese_offline_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-hub"))

    with pytest.raises(RuntimeError) as exc_info:
        inference_runner.omnivoice_local_snapshot()

    message = str(exc_info.value)
    assert "未找到本地 OmniVoice 模型快照" in message
    assert "k2-fsa/OmniVoice" in message
    assert "不会自动联网下载" in message


def test_single_runner_loads_omnivoice_from_local_snapshot(tmp_path, monkeypatch):
    cache_dir = tmp_path / "hub"
    snapshot = _create_snapshot(cache_dir)
    generated = tmp_path / "generated.wav"
    generated.write_bytes(b"audio")
    calls = _install_fake_omnivoice(monkeypatch, generated)
    monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir))
    monkeypatch.setattr(inference_runner, "_audio_meta", lambda *_: {"duration_ms": 1, "sample_rate": 24000})
    inference_runner.evict_cache("omnivoice")

    output = tmp_path / "single.wav"
    inference_runner.run_omnivoice(text="测试", output_path=str(output), device="cpu")

    assert calls == [(str(snapshot.resolve()), {"device_map": "cpu"})]
    assert output.read_bytes() == b"audio"


def test_batch_runner_loads_omnivoice_from_local_snapshot(tmp_path, monkeypatch):
    cache_dir = tmp_path / "hub"
    snapshot = _create_snapshot(cache_dir)
    generated = tmp_path / "generated.wav"
    generated.write_bytes(b"audio")
    calls = _install_fake_omnivoice(monkeypatch, generated)
    monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir))
    monkeypatch.setattr(
        batch_inference_runner,
        "_finalize_wav",
        lambda wav, output, sample_rate: {"output_path": str(output), "duration_ms": 1, "sample_rate": sample_rate},
    )

    results = batch_inference_runner.run_omnivoice(
        {
            "common": {"device": "cpu"},
            "segments": [{"segment_id": "segment-1", "text": "测试", "output_path": str(tmp_path / "batch.wav")}],
        }
    )

    assert calls == [(str(snapshot.resolve()), {"device_map": "cpu"})]
    assert results[0]["status"] == "success"


def test_omnivoice_health_requires_local_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-hub"))

    health = engine_health._health_omnivoice()

    assert health["healthy"] is False
    assert health["status"] == "model_missing"
    assert "不会自动联网下载" in health["detail"]


def test_omnivoice_health_reports_verified_snapshot(tmp_path, monkeypatch):
    cache_dir = tmp_path / "hub"
    snapshot = _create_snapshot(cache_dir)
    monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir))
    monkeypatch.setitem(sys.modules, "omnivoice", types.ModuleType("omnivoice"))

    health = engine_health._health_omnivoice()

    assert health == {
        "healthy": True,
        "status": "ok",
        "model_id": "k2-fsa/OmniVoice",
        "model_path": str(snapshot.resolve()),
    }
