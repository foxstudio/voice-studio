from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import qwen_forced_aligner  # noqa: E402


def test_read_worker_line_returns_on_timeout():
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.2); print('ready', flush=True)"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert qwen_forced_aligner._read_worker_line(worker, 0.02) == ""
        assert qwen_forced_aligner._read_worker_line(worker, 1).strip() == "ready"
    finally:
        worker.wait(timeout=2)


def test_align_audio_resets_worker_after_response_timeout(monkeypatch):
    class FakeWorker:
        stdin = io.StringIO()

    reset = []
    monkeypatch.setattr(qwen_forced_aligner, "_ensure_worker", lambda: FakeWorker())
    monkeypatch.setattr(qwen_forced_aligner, "_read_worker_line", lambda worker, timeout: "")
    monkeypatch.setattr(qwen_forced_aligner, "_reset_worker", lambda: reset.append(True))

    with pytest.raises(RuntimeError, match="响应超时"):
        qwen_forced_aligner.align_audio(audio_path="audio.wav", transcript_text="text", language="English")

    assert reset == [True]


def test_checkpoint_completeness_requires_config_and_full_weights(monkeypatch, tmp_path):
    checkpoint = tmp_path / "aligner"
    checkpoint.mkdir()
    for name in qwen_forced_aligner.REQUIRED_CHECKPOINT_FILES:
        (checkpoint / name).write_text("{}")
    weights = checkpoint / "model.safetensors"
    weights.write_bytes(b"small")
    monkeypatch.setattr(qwen_forced_aligner, "MIN_CHECKPOINT_BYTES", 10)
    monkeypatch.setattr(qwen_forced_aligner.model_integrity, "verify_model_file", lambda *args, **kwargs: (True, {}))

    assert not qwen_forced_aligner._checkpoint_is_complete(checkpoint)

    weights.write_bytes(b"large-enough")

    assert qwen_forced_aligner._checkpoint_is_complete(checkpoint)


def test_checkpoint_completeness_rejects_missing_tokenizer_assets(monkeypatch, tmp_path):
    checkpoint = tmp_path / "aligner"
    checkpoint.mkdir()
    for name in qwen_forced_aligner.REQUIRED_CHECKPOINT_FILES:
        if name != "merges.txt":
            (checkpoint / name).write_text("{}")
    (checkpoint / "model.safetensors").write_bytes(b"large-enough")
    monkeypatch.setattr(qwen_forced_aligner, "MIN_CHECKPOINT_BYTES", 10)
    monkeypatch.setattr(qwen_forced_aligner.model_integrity, "verify_model_file", lambda *args, **kwargs: (True, {}))

    assert not qwen_forced_aligner._checkpoint_is_complete(checkpoint)


def test_runtime_missing_still_exposes_download_sources(monkeypatch):
    monkeypatch.setattr(qwen_forced_aligner, "runtime_python", lambda: None)

    result = qwen_forced_aligner.health_check()

    assert result["status"] == "runtime_missing"
    assert result["download_sources"][0]["provider"] == "modelscope"
    assert "不会自动下载" in result["download_policy"]


def test_missing_checkpoint_stays_local_and_exposes_domestic_source(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE_STUDIO_QWEN_ALIGN_CHECKPOINT", raising=False)
    monkeypatch.setattr(
        qwen_forced_aligner.settings_store,
        "get",
        lambda: SimpleNamespace(model_dir=str(tmp_path), device="mps"),
    )

    checkpoint = qwen_forced_aligner.checkpoint_path()
    sources = qwen_forced_aligner.download_sources()

    assert checkpoint == str(tmp_path / "Qwen3-ForcedAligner-0.6B")
    assert sources[0]["provider"] == "modelscope"
    assert sources[0]["preferred"] is True
    assert sources[0]["url"].startswith("https://modelscope.cn/")
    assert qwen_forced_aligner.DEFAULT_CHECKPOINT not in checkpoint
