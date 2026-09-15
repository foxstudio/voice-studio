from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import (  # noqa: E402
    settings_store,
    stem_separation_engine,
    stem_separation_model,
)
from app.schemas.voice_studio import AppSettings  # noqa: E402


def _configure_paths(tmp_path: Path) -> None:
    settings_store.update(
        AppSettings(
            data_dir=str(tmp_path),
            model_dir=str(tmp_path / "models"),
            voice_dir=str(tmp_path / "voices"),
            output_dir=str(tmp_path / "outputs"),
            export_dir=str(tmp_path / "exports"),
            project_dir=str(tmp_path / "projects"),
            cache_dir=str(tmp_path / "cache"),
            log_dir=str(tmp_path / "logs"),
        )
    )


def test_model_install_verifies_managed_files_and_reports_disk_usage(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", str(tmp_path / "models"))
    _configure_paths(tmp_path)
    checkpoint = b"verified-bs-roformer"
    config = (
        b"training:\n  target_instrument: Vocals\n"
    )
    registry = json.dumps(
        {
            "roformer_download_list": {
                "Roformer Model: BS-Roformer-Viperx-1297": {
                    stem_separation_model.MODEL_FILENAME:
                        stem_separation_model.CONFIG_FILENAME
                }
            }
        }
    ).encode()
    payloads = {
        stem_separation_model.MODEL_URL: checkpoint,
        stem_separation_model.CONFIG_URL: config,
        stem_separation_model.REGISTRY_URL: registry,
    }
    monkeypatch.setattr(
        stem_separation_model,
        "MODEL_SHA256",
        hashlib.sha256(checkpoint).hexdigest(),
    )
    monkeypatch.setattr(
        stem_separation_model,
        "CONFIG_SHA256",
        hashlib.sha256(config).hexdigest(),
    )
    monkeypatch.setattr(
        stem_separation_model,
        "MODEL_SIZE_BYTES",
        len(checkpoint),
    )

    def fake_download(url, destination, progress):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payloads[url])
        progress(len(payloads[url]), len(payloads[url]))

    monkeypatch.setattr(
        stem_separation_model,
        "_download_file",
        fake_download,
    )

    result = stem_separation_model.install_now()

    assert result["installed"] is True
    assert result["integrity"] == "verified"
    assert result["size_bytes"] == len(checkpoint) + len(config) + len(registry)
    model_path, config_path = stem_separation_model.require_model_files()
    assert model_path.read_bytes() == checkpoint
    assert config_path.read_bytes() == config
    assert model_path.parent == tmp_path / "models" / stem_separation_model.ENGINE_ID


def test_residual_background_is_original_mix_minus_vocals():
    mix = np.array(
        [[0.8, -0.4], [0.2, 0.6]],
        dtype=np.float32,
    )
    vocals = np.array(
        [[0.3, -0.1], [0.05, 0.4]],
        dtype=np.float32,
    )

    background = stem_separation_engine.residual_background(
        mix,
        vocals,
    )

    np.testing.assert_allclose(background, mix - vocals)


def test_explicit_cuda_setting_keeps_bs_roformer_on_cuda(monkeypatch):
    separator = SimpleNamespace(
        torch_device="auto",
        torch_device_cpu="cpu",
        torch_device_mps="mps",
        onnx_execution_provider=["CPUExecutionProvider"],
    )
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        backends=SimpleNamespace(
            mps=SimpleNamespace(is_available=lambda: False)
        ),
        device=lambda name: f"device:{name}",
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(
        stem_separation_engine.settings_store,
        "get",
        lambda: SimpleNamespace(device="cuda"),
    )

    stem_separation_engine._apply_device_setting(separator)

    assert separator.torch_device == "device:cuda"
    assert separator.torch_device_mps is None


def test_model_download_resumes_a_truncated_file(
    tmp_path: Path,
    monkeypatch,
):
    payload = b"complete-model"
    destination = tmp_path / "model.ckpt"
    calls = 0

    def truncated_then_resumed(_url, path, progress):
        nonlocal calls
        calls += 1
        if calls == 1:
            path.write_bytes(payload[:5])
        else:
            with path.open("ab") as handle:
                handle.write(payload[5:])
        progress(path.stat().st_size, len(payload))

    monkeypatch.setattr(
        stem_separation_model,
        "_download_file",
        truncated_then_resumed,
    )

    stem_separation_model._ensure_download(
        "https://example.invalid/model",
        destination,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_size=len(payload),
        progress=lambda _current, _total: None,
    )

    assert calls == 2
    assert destination.read_bytes() == payload


def test_engine_writes_float_vocals_and_residual_background(
    tmp_path: Path,
    monkeypatch,
):
    _configure_paths(tmp_path)
    source = tmp_path / "source.wav"
    samples = np.array(
        [[0.75, -0.25], [0.25, 0.5], [-0.5, 0.125]],
        dtype=np.float32,
    )
    sf.write(source, samples, 48_000, subtype="FLOAT")
    fake_model = tmp_path / "model.ckpt"
    fake_config = tmp_path / "model.yaml"
    fake_model.write_bytes(b"model")
    fake_config.write_text("model: {}", encoding="utf-8")
    monkeypatch.setattr(
        stem_separation_model,
        "require_model_files",
        lambda: (fake_model, fake_config),
    )
    estimated_vocals = np.array(
        [[0.25, -0.05], [0.1, 0.25], [-0.2, 0.025]],
        dtype=np.float32,
    )

    class FakeSeparator:
        def __init__(self, **kwargs):
            self.output_dir = Path(kwargs["output_dir"])

        def load_model(self, model_filename):
            assert model_filename == stem_separation_model.MODEL_FILENAME

        def separate(self, audio_file_path, custom_output_names):
            output = self.output_dir / "estimated-vocals.wav"
            sf.write(output, estimated_vocals, 48_000, subtype="FLOAT")
            return [str(output)]

    monkeypatch.setattr(
        stem_separation_engine,
        "_separator_class",
        lambda: FakeSeparator,
    )
    vocals_path = tmp_path / "result" / "vocals.wav"
    background_path = tmp_path / "result" / "background.wav"

    result = stem_separation_engine.separate(
        source,
        vocals_path,
        background_path,
        overlap=8,
        chunk_duration_seconds=600,
    )

    vocals, vocals_sr = sf.read(vocals_path, always_2d=True, dtype="float32")
    background, background_sr = sf.read(
        background_path,
        always_2d=True,
        dtype="float32",
    )
    assert result["engine_id"] == stem_separation_engine.ENGINE_ID
    assert vocals_sr == background_sr == 48_000
    assert sf.info(vocals_path).subtype == "FLOAT"
    assert sf.info(background_path).subtype == "FLOAT"
    np.testing.assert_allclose(vocals, estimated_vocals)
    np.testing.assert_allclose(background, samples - estimated_vocals)


def test_relative_runtime_output_selects_the_current_chunk(
    tmp_path: Path,
):
    first = np.full((4, 2), 0.1, dtype=np.float32)
    second = np.full((4, 2), 0.7, dtype=np.float32)
    sf.write(
        tmp_path / "estimated-vocals-0000.wav",
        first,
        48_000,
        subtype="FLOAT",
    )
    sf.write(
        tmp_path / "estimated-vocals-0001.wav",
        second,
        48_000,
        subtype="FLOAT",
    )

    selected = stem_separation_engine._read_vocals_output(
        ["estimated-vocals-0001.wav"],
        tmp_path,
        2,
    )

    np.testing.assert_allclose(selected, second)
