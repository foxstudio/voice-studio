from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import engine_runner, inference_runner, local_engine_worker  # noqa: E402


def _fake_omnivoice_package(root: Path) -> None:
    package = root / "omnivoice"
    models = package / "models"
    models.mkdir(parents=True)
    (package / "__init__.py").write_text(
        """
import os
from pathlib import Path


class OmniVoice:
    sampling_rate = 24000

    @classmethod
    def from_pretrained(cls, _model_path, **_kwargs):
        counter = Path(os.environ["VOICE_STUDIO_TEST_LOAD_COUNTER"])
        current = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
        counter.write_text(str(current + 1), encoding="utf-8")
        return cls()

    def generate(self, **_kwargs):
        return os.environ["VOICE_STUDIO_TEST_GENERATED_AUDIO"]
""",
        encoding="utf-8",
    )
    (models / "__init__.py").write_text("", encoding="utf-8")
    (models / "omnivoice.py").write_text(
        """
class OmniVoiceGenerationConfig:
    @classmethod
    def from_dict(cls, values):
        return values
""",
        encoding="utf-8",
    )


def _complete_omnivoice_model(root: Path) -> Path:
    model = root / "models" / "omnivoice"
    for name in inference_runner.OMNIVOICE_REQUIRED_FILES:
        path = model / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")
    return model


def test_local_worker_idle_timeout_defaults_to_fifteen_minutes(monkeypatch):
    monkeypatch.delenv("VOICE_STUDIO_LOCAL_WORKER_IDLE_SECONDS", raising=False)
    assert local_engine_worker._idle_timeout_seconds() == 900

    monkeypatch.setenv("VOICE_STUDIO_LOCAL_WORKER_IDLE_SECONDS", "0")
    assert local_engine_worker._idle_timeout_seconds() is None


def test_local_worker_reuses_omnivoice_model_across_requests(tmp_path, monkeypatch):
    _fake_omnivoice_package(tmp_path)
    model_dir = _complete_omnivoice_model(tmp_path)
    generated = tmp_path / "generated.wav"
    generated.write_bytes(b"audio")
    counter = tmp_path / "loads.txt"
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("VOICE_STUDIO_TEST_LOAD_COUNTER", str(counter))
    monkeypatch.setenv("VOICE_STUDIO_TEST_GENERATED_AUDIO", str(generated))
    common = {"text": "测试", "model_dir": str(model_dir), "device": "cpu"}

    local_engine_worker.shutdown("omnivoice")
    try:
        first = local_engine_worker.run(
            "omnivoice",
            {**common, "output_path": str(tmp_path / "first.wav")},
            state_root=tmp_path / "logs",
            python=sys.executable,
            timeout=10,
        )
        second = local_engine_worker.run(
            "omnivoice",
            {**common, "output_path": str(tmp_path / "second.wav")},
            state_root=tmp_path / "logs",
            python=sys.executable,
            timeout=10,
        )
    finally:
        local_engine_worker.shutdown("omnivoice")

    assert Path(first["output_path"]).read_bytes() == b"audio"
    assert Path(second["output_path"]).read_bytes() == b"audio"
    assert counter.read_text(encoding="utf-8") == "1"


def test_engine_runner_uses_local_worker_by_default(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_run(engine_id, kwargs, **options):
        captured.update({"engine_id": engine_id, "kwargs": kwargs, **options})
        return {"output_path": kwargs["output_path"]}

    monkeypatch.delenv("VOICE_STUDIO_LOCAL_PERSISTENT_WORKER", raising=False)
    monkeypatch.setattr(engine_runner.local_engine_worker, "run", fake_run)
    monkeypatch.setattr(engine_runner.settings_store, "log_dir", lambda: tmp_path / "logs")

    result = engine_runner.run_isolated(
        "omnivoice",
        {"text": "测试", "output_path": str(tmp_path / "output.wav")},
        timeout=123,
    )

    assert result["output_path"] == str(tmp_path / "output.wav")
    assert captured["engine_id"] == "omnivoice"
    assert captured["state_root"] == tmp_path / "logs"
    assert captured["timeout"] == 123
