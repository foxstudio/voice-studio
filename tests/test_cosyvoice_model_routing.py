from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import cosyvoice_worker, engine_health, inference_runner, model_catalog  # noqa: E402


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _install_runtime_surface(root: Path) -> None:
    _touch(root / ".venv" / "bin" / "python")
    _touch(root / "cosyvoice" / "cli" / "cosyvoice.py")
    (root / "third_party" / "Matcha-TTS").mkdir(parents=True)


def _install_model_files(root: Path, engine_id: str) -> Path:
    model_dir = cosyvoice_worker.model_directory(root, engine_id)
    for name in cosyvoice_worker.required_model_files(engine_id):
        _touch(model_dir / name)
    return model_dir


def _install_fake_worker_runtime(root: Path) -> None:
    package = root / "cosyvoice" / "cli"
    package.mkdir(parents=True)
    (root / "cosyvoice" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (root / "torch.py").write_text("", encoding="utf-8")
    (root / "torchaudio.py").write_text(
        "from pathlib import Path\n\ndef save(path, _speech, _sample_rate):\n    Path(path).write_bytes(b'fake-wave')\n",
        encoding="utf-8",
    )
    (package / "cosyvoice.py").write_text(
        """
from pathlib import Path


class _Speech:
    ndim = 2

    def detach(self):
        return self

    def cpu(self):
        return self


class AutoModel:
    sample_rate = 22050

    def __init__(self, model_dir):
        history = Path(model_dir).parents[1] / "load-history.txt"
        with history.open("a", encoding="utf-8") as handle:
            handle.write(str(model_dir) + "\\n")

    def list_available_spks(self):
        return ["中文女"]

    def inference_sft(self, *_args, **_kwargs):
        yield {"tts_speech": _Speech()}

    def inference_zero_shot(self, *_args, **_kwargs):
        yield {"tts_speech": _Speech()}
""",
        encoding="utf-8",
    )


def test_cosyvoice_model_directory_contract_is_engine_specific(tmp_path: Path):
    assert cosyvoice_worker.model_directory(tmp_path, "cosyvoice-sft") == (
        tmp_path / "pretrained_models" / "CosyVoice-300M-SFT"
    )
    assert cosyvoice_worker.model_directory(tmp_path, "cosyvoice-zero-shot") == (
        tmp_path / "pretrained_models" / "CosyVoice-300M"
    )
    assert "spk2info.pt" in cosyvoice_worker.required_model_files("cosyvoice-sft")
    assert "spk2info.pt" not in cosyvoice_worker.required_model_files("cosyvoice-zero-shot")


def test_cosyvoice_health_does_not_use_the_other_models_directory(tmp_path: Path, monkeypatch):
    root = tmp_path / "CosyVoice"
    monkeypatch.setenv("VOICE_STUDIO_COSYVOICE_ROOT", str(root))
    _install_runtime_surface(root)
    sft_dir = _install_model_files(root, "cosyvoice-sft")

    sft_health = engine_health.health_check("cosyvoice-sft")
    zero_shot_health = engine_health.health_check("cosyvoice-zero-shot")

    assert sft_health["healthy"] is True
    assert sft_health["model_path"] == str(sft_dir)
    assert zero_shot_health["healthy"] is False
    assert any("pretrained_models/CosyVoice-300M/" in item for item in zero_shot_health["missing"])

    zero_shot_dir = _install_model_files(root, "cosyvoice-zero-shot")
    zero_shot_health = engine_health.health_check("cosyvoice-zero-shot")
    assert zero_shot_health["healthy"] is True
    assert zero_shot_health["model_path"] == str(zero_shot_dir)


def test_cosyvoice_persistent_worker_payload_uses_the_requested_models_directory(tmp_path: Path, monkeypatch):
    payloads: list[dict[str, object]] = []

    def fake_run(payload, **_kwargs):
        payloads.append(payload)
        return {"output_path": payload["output_path"]}

    monkeypatch.setattr(cosyvoice_worker._worker, "run", fake_run)

    cosyvoice_worker.run(
        "cosyvoice-sft",
        {"text": "SFT", "output_path": "sft.wav"},
        root=tmp_path,
        python=sys.executable,
        timeout=10,
    )
    cosyvoice_worker.run(
        "cosyvoice-zero-shot",
        {"text": "Zero-Shot", "output_path": "zero.wav"},
        root=tmp_path,
        python=sys.executable,
        timeout=10,
    )

    assert payloads[0]["model_dir"] == str(tmp_path / "pretrained_models" / "CosyVoice-300M-SFT")
    assert payloads[1]["model_dir"] == str(tmp_path / "pretrained_models" / "CosyVoice-300M")


def test_cosyvoice_persistent_worker_switches_models_between_engines(tmp_path: Path):
    _install_fake_worker_runtime(tmp_path)
    cosyvoice_worker.shutdown()
    try:
        cosyvoice_worker.run(
            "cosyvoice-sft",
            {"text": "SFT", "speaker_id": "中文女", "output_path": str(tmp_path / "sft.wav")},
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
        cosyvoice_worker.run(
            "cosyvoice-zero-shot",
            {
                "text": "Zero-Shot",
                "reference_audio": "reference.wav",
                "ref_text": "参考文本",
                "output_path": str(tmp_path / "zero.wav"),
            },
            root=tmp_path,
            python=sys.executable,
            timeout=10,
        )
    finally:
        cosyvoice_worker.shutdown()

    assert (tmp_path / "load-history.txt").read_text(encoding="utf-8").splitlines() == [
        str(tmp_path / "pretrained_models" / "CosyVoice-300M-SFT"),
        str(tmp_path / "pretrained_models" / "CosyVoice-300M"),
    ]


def test_cosyvoice_nonpersistent_runners_use_engine_specific_model_directories(tmp_path: Path, monkeypatch):
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(inference_runner, "_external_root", lambda _engine_id: tmp_path)
    monkeypatch.setattr(inference_runner, "_external_python", lambda _root: sys.executable)

    def fake_run_external(cmd, _cwd, _env=None):
        captured.append(json.loads(Path(cmd[-1]).read_text(encoding="utf-8")))

    monkeypatch.setattr(inference_runner, "_run_external", fake_run_external)

    inference_runner.run_cosyvoice_sft(
        text="SFT",
        output_path=str(tmp_path / "sft.wav"),
        speaker_id="中文女",
    )
    inference_runner.run_cosyvoice_zero_shot(
        text="Zero-Shot",
        output_path=str(tmp_path / "zero.wav"),
        reference_audio="reference.wav",
        ref_text="参考文本",
    )

    assert captured[0]["model_dir"] == str(tmp_path / "pretrained_models" / "CosyVoice-300M-SFT")
    assert captured[1]["model_dir"] == str(tmp_path / "pretrained_models" / "CosyVoice-300M")


def test_model_catalog_points_each_modelscope_source_at_its_install_directory(tmp_path: Path, monkeypatch):
    root = tmp_path / "CosyVoice"
    monkeypatch.setenv("VOICE_STUDIO_COSYVOICE_ROOT", str(root))
    entries = {item["engine_id"]: item for item in model_catalog.list_installations()}

    sft = entries["cosyvoice-sft"]
    zero_shot = entries["cosyvoice-zero-shot"]
    assert sft["preferred_path"] == str(root / "pretrained_models" / "CosyVoice-300M-SFT")
    assert zero_shot["preferred_path"] == str(root / "pretrained_models" / "CosyVoice-300M")
    assert sft["download_sources"][0]["url"].endswith("/CosyVoice-300M-SFT")
    assert zero_shot["download_sources"][0]["url"].endswith("/CosyVoice-300M")
    assert "pretrained_models/CosyVoice-300M-SFT" in sft["download_sources"][0]["compatibility_note"]
    assert "pretrained_models/CosyVoice-300M" in zero_shot["download_sources"][0]["compatibility_note"]
