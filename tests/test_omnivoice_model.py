from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.errors import AppException  # noqa: E402
from app.services import omnivoice_model  # noqa: E402
from app.schemas.voice_studio import AppSettings  # noqa: E402


def _small_file_specs() -> dict[str, tuple[int, str | None]]:
    payloads = {
        "config.json": b"config",
        "model.safetensors": b"model",
        "tokenizer.json": b"tokenizer",
        "tokenizer_config.json": b"tokenizer-config",
        "audio_tokenizer/config.json": b"audio-config",
        "audio_tokenizer/model.safetensors": b"audio-model",
        "audio_tokenizer/preprocessor_config.json": b"audio-preprocessor",
    }
    return {
        name: (len(payload), hashlib.sha256(payload).hexdigest())
        for name, payload in payloads.items()
    }


def _payload_for(relative: str) -> bytes:
    values = {
        "config.json": b"config",
        "model.safetensors": b"model",
        "tokenizer.json": b"tokenizer",
        "tokenizer_config.json": b"tokenizer-config",
        "audio_tokenizer/config.json": b"audio-config",
        "audio_tokenizer/model.safetensors": b"audio-model",
        "audio_tokenizer/preprocessor_config.json": b"audio-preprocessor",
    }
    return values[relative]


def test_managed_download_target_does_not_reuse_external_cache(tmp_path, monkeypatch):
    settings = AppSettings(model_dir=str(tmp_path / "managed-models"))
    monkeypatch.setenv("VOICE_STUDIO_MODELS_DIR", settings.model_dir)
    monkeypatch.setattr(omnivoice_model.settings_store, "get", lambda: settings)
    monkeypatch.setattr(
        omnivoice_model.settings_store,
        "model_path",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the installer must not use the legacy read resolver")
        ),
    )

    assert omnivoice_model.model_dir() == tmp_path / "managed-models" / "omnivoice"


def test_install_requires_explicit_noncommercial_license_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(omnivoice_model, "model_dir", lambda: tmp_path / "omnivoice")

    with pytest.raises(AppException) as exc_info:
        omnivoice_model.install_now(None, downloader=lambda **_kwargs: "")

    assert exc_info.value.code == "MODEL_LICENSE_ACCEPTANCE_REQUIRED"
    assert not (tmp_path / "omnivoice").exists()


def test_install_pins_revision_and_verifies_every_required_file(tmp_path, monkeypatch):
    target = tmp_path / "models" / "omnivoice"
    monkeypatch.setattr(omnivoice_model, "model_dir", lambda: target)
    monkeypatch.setattr(omnivoice_model, "FILE_SPECS", _small_file_specs())
    monkeypatch.setattr(
        omnivoice_model,
        "TOTAL_BYTES",
        sum(size for size, _sha in omnivoice_model.FILE_SPECS.values()),
    )
    captured = {}

    def fake_download(**kwargs):
        captured.update(kwargs)
        local_dir = Path(kwargs["local_dir"])
        for relative in kwargs["allow_patterns"]:
            path = local_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_payload_for(relative))
        return str(local_dir)

    result = omnivoice_model.install_now(
        omnivoice_model.LICENSE_ACCEPTANCE_ID,
        downloader=fake_download,
    )

    assert result["installed"] is True
    assert result["integrity"] == "verified"
    assert captured["repo_id"] == omnivoice_model.REPO_ID
    assert captured["revision"] == omnivoice_model.REVISION
    assert set(captured["allow_patterns"]) == set(omnivoice_model.FILE_SPECS)
    manifest = json.loads(
        (target / omnivoice_model.INSTALL_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["revision"] == omnivoice_model.REVISION
    assert manifest["model_license"] == "CC-BY-NC"
    assert manifest["code_license"] == "Apache-2.0"


def test_corrupt_download_fails_closed_and_reports_failure(tmp_path, monkeypatch):
    target = tmp_path / "models" / "omnivoice"
    monkeypatch.setattr(omnivoice_model, "model_dir", lambda: target)
    monkeypatch.setattr(omnivoice_model, "FILE_SPECS", _small_file_specs())
    monkeypatch.setattr(
        omnivoice_model,
        "TOTAL_BYTES",
        sum(size for size, _sha in omnivoice_model.FILE_SPECS.values()),
    )

    def corrupt_download(**kwargs):
        local_dir = Path(kwargs["local_dir"])
        for relative in kwargs["allow_patterns"]:
            path = local_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_payload_for(relative))
        (local_dir / "model.safetensors").write_bytes(b"wrong")
        return str(local_dir)

    with pytest.raises(RuntimeError, match="校验失败"):
        omnivoice_model.install_now(
            omnivoice_model.LICENSE_ACCEPTANCE_ID,
            downloader=corrupt_download,
        )

    status = omnivoice_model.installation_status()
    assert status["installed"] is False
    assert status["installation_status"] == "failed"
    assert status["integrity"] == "invalid"
    assert status["error"]
