from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import engine_health, indextts_model  # noqa: E402
from mlx_indextts.model_artifacts import INDEXTTS_W2V_BERT_ARTIFACTS  # noqa: E402


def _write_core_model(model_dir: Path, *, legacy_layout: bool = False) -> None:
    layout = indextts_model.CORE_WEIGHT_LAYOUTS[1 if legacy_layout else 0]
    for name in (*indextts_model.CORE_COMMON_FILES, *layout):
        path = model_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")


def _write_wav_preprocessing(model_dir: Path) -> None:
    (model_dir / "wav2vec2bert_stats.pt").write_bytes(b"stats")
    for artifact in indextts_model.WAV_PREPROCESSING_ARTIFACTS:
        path = model_dir / artifact.managed_relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"artifact")


def test_missing_and_partial_indextts_models_are_not_healthy(tmp_path, monkeypatch):
    missing = indextts_model.health(tmp_path / "not-installed")
    assert missing["healthy"] is False
    assert missing["status"] == "model_missing"

    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "tokenizer.model").write_bytes(b"tokenizer")
    monkeypatch.setattr(
        indextts_model,
        "_artifact_available",
        lambda *_args, **_kwargs: False,
    )

    result = indextts_model.health(partial)

    assert result["healthy"] is False
    assert result["status"] == "model_incomplete"
    assert "config.yaml" in result["missing"]
    assert result["runtime_downloads_enabled"] is False


def test_core_model_reports_wav_reference_dependencies_separately(
    tmp_path,
    monkeypatch,
):
    _write_core_model(tmp_path)
    monkeypatch.setattr(
        indextts_model,
        "_artifact_available",
        lambda *_args, **_kwargs: False,
    )

    result = indextts_model.health(tmp_path)

    assert result["healthy"] is True
    assert result["product_ready"] is False
    assert result["status"] == "reference_preprocessing_missing"
    assert result["core_ready"] is True
    assert result["wav_reference_ready"] is False
    assert result["supported_reference_formats"] == ["npz"]
    assert "wav2vec2bert_stats.pt" in result["optional_missing"]


def test_complete_managed_preprocessing_supports_wav_and_legacy_core_layout(
    tmp_path,
):
    _write_core_model(tmp_path, legacy_layout=True)
    _write_wav_preprocessing(tmp_path)

    result = indextts_model.health(tmp_path)

    assert result["healthy"] is True
    assert result["product_ready"] is True
    assert result["status"] == "ok"
    assert result["wav_reference_ready"] is True
    assert result["supported_reference_formats"] == ["npz", "wav"]
    assert result["optional_missing"] == []


def test_engine_health_uses_layered_indextts_model_health(tmp_path, monkeypatch):
    _write_core_model(tmp_path)
    _write_wav_preprocessing(tmp_path)
    monkeypatch.setattr(engine_health.settings_store, "model_path", lambda _id: tmp_path)

    result = engine_health.health_check("indextts-v2")

    assert result["healthy"] is True
    assert result["core_ready"] is True
    assert result["wav_reference_ready"] is True


def test_health_requires_w2v_bundle_from_one_complete_location(tmp_path, monkeypatch):
    _write_core_model(tmp_path)
    (tmp_path / "wav2vec2bert_stats.pt").write_bytes(b"stats")
    for artifact in indextts_model.WAV_PREPROCESSING_ARTIFACTS:
        if artifact not in INDEXTTS_W2V_BERT_ARTIFACTS:
            path = tmp_path / artifact.managed_relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"artifact")
    managed_config = tmp_path / INDEXTTS_W2V_BERT_ARTIFACTS[0].managed_relative_path
    managed_config.parent.mkdir(parents=True, exist_ok=True)
    managed_config.write_bytes(b"partial")
    separate_cache_dirs = {
        artifact.filename: tmp_path / "cache" / artifact.artifact_id / artifact.filename
        for artifact in INDEXTTS_W2V_BERT_ARTIFACTS
    }
    for path in separate_cache_dirs.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cached")
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda _repo, filename, revision: str(separate_cache_dirs[filename]),
    )

    result = indextts_model.health(tmp_path)

    assert result["healthy"] is True
    assert result["wav_reference_ready"] is False
    assert all(
        artifact.managed_relative_path in result["optional_missing"]
        for artifact in INDEXTTS_W2V_BERT_ARTIFACTS
    )
