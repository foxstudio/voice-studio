from __future__ import annotations

from pathlib import Path
from typing import Any

from mlx_indextts.model_artifacts import (
    INDEXTTS_W2V_BERT_ARTIFACTS,
    INDEXTTS_WAV_PREPROCESSING_ARTIFACTS,
    IndexTTSPreprocessingArtifact,
)


CORE_COMMON_FILES = (
    "config.yaml",
    "tokenizer.model",
    "vq2emb.safetensors",
)
CORE_WEIGHT_LAYOUTS = (
    (
        "gpt.safetensors",
        "s2mel.safetensors",
        "bigvgan.safetensors",
    ),
    (
        "gpt_v2/gpt_v2.safetensors",
        "s2mel_v2/s2mel.safetensors",
        "bigvgan_v2/bigvgan_v2.safetensors",
    ),
)


WAV_PREPROCESSING_ARTIFACTS = INDEXTTS_WAV_PREPROCESSING_ARTIFACTS


def health(model_dir: Path) -> dict[str, Any]:
    model_dir = Path(model_dir)
    missing_core = _missing_core_files(model_dir)
    missing_preprocessing = _missing_wav_preprocessing(model_dir)
    core_ready = not missing_core
    wav_reference_ready = core_ready and not missing_preprocessing

    if not model_dir.exists():
        status = "model_missing"
    elif not core_ready:
        status = "model_incomplete"
    elif not wav_reference_ready:
        status = "reference_preprocessing_missing"
    else:
        status = "ok"

    reference_formats = ["npz"] if core_ready else []
    if wav_reference_ready:
        reference_formats.append("wav")

    return {
        "healthy": core_ready,
        "product_ready": wav_reference_ready,
        "status": status,
        "detail": (
            "IndexTTS 核心模型可用，但上传 WAV 参考音频所需的配套模型尚未安装。"
            if core_ready and not wav_reference_ready
            else None
        ),
        "model_path": str(model_dir),
        "missing": missing_core,
        "optional_missing": missing_preprocessing,
        "core_ready": core_ready,
        "wav_reference_ready": wav_reference_ready,
        "supported_reference_formats": reference_formats,
        "runtime_downloads_enabled": False,
    }


def core_files_available(model_dir: Path) -> bool:
    return not _missing_core_files(Path(model_dir))


def _missing_core_files(model_dir: Path) -> list[str]:
    missing = [name for name in CORE_COMMON_FILES if not (model_dir / name).is_file()]
    if not any(
        all((model_dir / name).is_file() for name in layout)
        for layout in CORE_WEIGHT_LAYOUTS
    ):
        missing.extend(
            f"one_of:{'|'.join(layout)}"
            for layout in CORE_WEIGHT_LAYOUTS
        )
    return missing


def _missing_wav_preprocessing(model_dir: Path) -> list[str]:
    missing: list[str] = []
    if not (model_dir / "wav2vec2bert_stats.pt").is_file():
        missing.append("wav2vec2bert_stats.pt")
    w2v_ids = {artifact.artifact_id for artifact in INDEXTTS_W2V_BERT_ARTIFACTS}
    for artifact in WAV_PREPROCESSING_ARTIFACTS:
        if artifact.artifact_id in w2v_ids:
            continue
        if not _artifact_available(model_dir, artifact):
            missing.append(artifact.managed_relative_path)
    if not _bundle_available(model_dir, INDEXTTS_W2V_BERT_ARTIFACTS):
        missing.extend(
            artifact.managed_relative_path
            for artifact in INDEXTTS_W2V_BERT_ARTIFACTS
        )
    return missing


def _artifact_available(
    model_dir: Path,
    artifact: IndexTTSPreprocessingArtifact,
) -> bool:
    if (model_dir / artifact.managed_relative_path).is_file():
        return True
    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache(
            artifact.repo_id,
            artifact.filename,
            revision=artifact.revision,
        )
        return isinstance(cached, str) and Path(cached).is_file()
    except Exception:
        return False


def _bundle_available(
    model_dir: Path,
    artifacts: tuple[IndexTTSPreprocessingArtifact, ...],
) -> bool:
    managed_paths = [model_dir / item.managed_relative_path for item in artifacts]
    if all(path.is_file() for path in managed_paths):
        return len({path.parent for path in managed_paths}) == 1
    try:
        from huggingface_hub import try_to_load_from_cache

        cached_paths = [
            Path(cached)
            for artifact in artifacts
            if isinstance(
                cached := try_to_load_from_cache(
                    artifact.repo_id,
                    artifact.filename,
                    revision=artifact.revision,
                ),
                str,
            )
            and Path(cached).is_file()
        ]
    except Exception:
        return False
    return (
        len(cached_paths) == len(artifacts)
        and len({path.parent for path in cached_paths}) == 1
    )
