from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import model_catalog  # noqa: E402


def test_catalog_distinguishes_present_files_from_missing_runtime(tmp_path, monkeypatch):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"model")
    monkeypatch.setattr(model_catalog, "_candidates", lambda _engine_id: [model_dir])
    monkeypatch.setattr(
        model_catalog.engine_health,
        "health_check",
        lambda _engine_id: {
            "healthy": False,
            "status": "package_missing",
            "detail": "Python package is missing",
        },
    )

    entry = model_catalog._entry("emotivoice", {"source_url": "https://example.test"})

    assert entry["installed"] is True
    assert entry["runtime_ready"] is False
    assert entry["runtime_status"] == "package_missing"
    assert entry["installation_status"] == "files_present_runtime_unavailable"
    assert entry["runtime_detail"] == "Python package is missing"


def test_catalog_does_not_treat_a_precreated_empty_model_directory_as_installed(
    tmp_path,
    monkeypatch,
):
    empty_managed = tmp_path / "managed" / "emotivoice"
    empty_managed.mkdir(parents=True)
    monkeypatch.setattr(model_catalog, "_candidates", lambda _engine_id: [empty_managed])
    monkeypatch.setattr(
        model_catalog.engine_health,
        "health_check",
        lambda _engine_id: {"healthy": False, "status": "package_missing"},
    )

    entry = model_catalog._entry("emotivoice", {"source_url": "https://example.test"})

    assert entry["installed"] is False
    assert entry["discovered_paths"][0]["exists"] is True
    assert entry["discovered_paths"][0]["has_payload"] is False
    assert entry["installation_status"] == "package_missing"


def test_catalog_prefers_the_first_directory_with_files(tmp_path, monkeypatch):
    empty_managed = tmp_path / "managed"
    empty_managed.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "model.bin").write_bytes(b"model")
    monkeypatch.setattr(
        model_catalog,
        "_candidates",
        lambda _engine_id: [empty_managed, external],
    )
    monkeypatch.setattr(
        model_catalog.engine_health,
        "health_check",
        lambda _engine_id: {"healthy": False, "status": "package_missing"},
    )

    entry = model_catalog._entry("emotivoice", {"source_url": "https://example.test"})

    assert entry["installed"] is True
    assert entry["preferred_path"] == str(external)


def test_catalog_does_not_call_a_partial_model_installed(tmp_path, monkeypatch):
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "one-file.bin").write_bytes(b"partial")
    monkeypatch.setattr(model_catalog, "_candidates", lambda _engine_id: [partial])
    monkeypatch.setattr(
        model_catalog.engine_health,
        "health_check",
        lambda _engine_id: {"healthy": False, "status": "model_missing"},
    )

    entry = model_catalog._entry("emotivoice", {"source_url": "https://example.test"})

    assert entry["installed"] is False
    assert entry["installation_status"] == "model_missing"


def test_catalog_reports_missing_files_separately_from_runtime_status(tmp_path, monkeypatch):
    monkeypatch.setattr(model_catalog, "_candidates", lambda _engine_id: [tmp_path / "missing"])
    monkeypatch.setattr(
        model_catalog.engine_health,
        "health_check",
        lambda _engine_id: {"healthy": False, "status": "model_missing"},
    )

    entry = model_catalog._entry("emotivoice", {"source_url": "https://example.test"})

    assert entry["installed"] is False
    assert entry["runtime_ready"] is False
    assert entry["installation_status"] == "model_missing"


def test_indextts_catalog_requires_wav_reference_assets_for_product_readiness(
    tmp_path,
    monkeypatch,
):
    model_dir = tmp_path / "indextts"
    model_dir.mkdir()
    (model_dir / "gpt.safetensors").write_bytes(b"model")
    monkeypatch.setattr(model_catalog, "_candidates", lambda _engine_id: [model_dir])
    monkeypatch.setattr(
        model_catalog.engine_health,
        "health_check",
        lambda _engine_id: {
            "healthy": True,
            "product_ready": False,
            "status": "reference_preprocessing_missing",
            "model_path": str(model_dir),
            "detail": "上传 WAV 参考音频所需的配套模型尚未安装。",
        },
    )

    entry = model_catalog._entry(
        "indextts-v2",
        model_catalog.SOURCES["indextts-v2"],
    )

    assert entry["installed"] is True
    assert entry["runtime_ready"] is False
    assert entry["runtime_status"] == "reference_preprocessing_missing"


def test_qwen3_catalog_lists_managed_model_root_before_legacy_runtime(
    tmp_path,
    monkeypatch,
):
    managed = tmp_path / "models" / "qwen3-tts-mlx-0.6b"
    legacy = tmp_path / "runtime" / "models"
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(
        model_catalog.qwen3_tts_paths,
        "model_root_candidates",
        lambda: [managed, legacy],
    )
    monkeypatch.setattr(
        model_catalog.engine_runtime_paths,
        "engine_root_candidates",
        lambda _engine_id: [runtime],
    )

    assert model_catalog._candidates("qwen3-tts-mlx-0.6b") == [
        managed,
        legacy,
        runtime,
    ]
