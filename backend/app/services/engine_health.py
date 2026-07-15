from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.services import confucius4_paths, cosyvoice_worker, engine_manifests, engine_policy, engine_runtime_paths, faster_whisper_asr, qwen3_tts_paths, qwen_mlx_asr, settings_store


DEFAULT_EXTERNAL_ROOTS = {
    engine_id: next(
        (path for path in engine_runtime_paths.engine_root_candidates(engine_id) if path.exists()),
        engine_runtime_paths.managed_engine_root(engine_id),
    )
    for engine_id in ["emotivoice", "f5-tts", "cosyvoice-sft", "cosyvoice-zero-shot"]
}


def mlx_audio_runtime_available() -> tuple[bool, str | None]:
    return qwen_mlx_asr.runtime_available()


def external_engine_root(engine_id: str) -> Path:
    env_name = engine_runtime_paths.ENGINE_LAYOUT[engine_id][0]
    if env_value := os.environ.get(env_name):
        return Path(env_value).expanduser()
    fallback = DEFAULT_EXTERNAL_ROOTS.get(engine_id)
    if fallback and fallback.exists():
        return fallback.resolve()
    raise RuntimeError(
        f"{engine_id} runtime is not installed. Put it at "
        f"{engine_runtime_paths.managed_engine_root(engine_id)} or set {env_name}."
    )


def health_check(engine_id: str) -> dict[str, Any]:
    engine_id = engine_policy.resolve_engine_id(engine_id)
    if engine_id not in engine_manifests.ENGINES:
        return {"healthy": False, "status": "not_found"}
    if engine_id.startswith("indextts"):
        return _health_indextts(engine_id)
    if engine_policy.is_cloud_engine(engine_id):
        return _health_cloud_engine(engine_id)
    if engine_id == "qwen3-asr-mlx":
        return _health_qwen_asr(engine_id)
    if engine_id == "faster-whisper-turbo":
        return _health_faster_whisper_asr(engine_id)
    if engine_id == confucius4_paths.ENGINE_ID:
        return _health_confucius4_mlx()
    if engine_id == qwen3_tts_paths.ENGINE_ID:
        return _health_qwen3_tts()
    if engine_id in engine_policy.EXTERNAL_SUBPROCESS_ENGINES:
        return _health_external_engine(engine_id)
    return _health_omnivoice()


def _health_indextts(engine_id: str) -> dict[str, Any]:
    model_dir = settings_store.model_path(engine_id)
    required = ["tokenizer.model", "gpt.safetensors"]
    missing = [name for name in required if not (model_dir / name).exists()]
    return {
        "healthy": not missing,
        "status": "ok" if not missing else "model_missing",
        "model_path": str(model_dir),
        "missing": missing,
    }


def _health_cloud_engine(engine_id: str) -> dict[str, Any]:
    settings = settings_store.get()
    if not settings.cloud_enabled:
        return {"healthy": False, "status": "cloud_disabled", "detail": "云端引擎未启用"}
    if engine_policy.is_doubao_engine(engine_id):
        if not settings.doubao_api_key_configured:
            return {"healthy": False, "status": "api_key_missing", "detail": "豆包 API Key 未配置"}
        return {"healthy": True, "status": "configured", "base_url": settings.doubao_base_url}
    if not settings.mimo_api_key_configured:
        return {"healthy": False, "status": "api_key_missing", "detail": "MiMo Token Plan API Key 未配置"}
    return {"healthy": True, "status": "configured", "base_url": settings.mimo_base_url}


def _health_qwen_asr(engine_id: str) -> dict[str, Any]:
    model_path = settings_store.model_path(engine_id)
    health = qwen_mlx_asr.model_health(model_path)
    if health.get("status") == "runtime_missing":
        ok, detail = mlx_audio_runtime_available()
        health["healthy"] = ok
        health["detail"] = f"mlx-audio runtime is unavailable: {detail}" if detail else health.get("detail")
    return health


def _health_faster_whisper_asr(engine_id: str) -> dict[str, Any]:
    model_path = settings_store.model_path(engine_id)
    return faster_whisper_asr.model_health(model_path)


def _health_confucius4_mlx() -> dict[str, Any]:
    model_path = settings_store.model_path(confucius4_paths.ENGINE_ID)
    runtime_root = confucius4_paths.runtime_root()
    missing = [
        *(f"model:{item}" for item in confucius4_paths.missing_model_files(model_path)),
        *(f"runtime:{item}" for item in confucius4_paths.missing_runtime_files(runtime_root)),
    ]
    return {
        "healthy": not missing,
        "status": "ok" if not missing else "runtime_or_model_missing",
        "model_path": str(model_path),
        "runtime_path": str(runtime_root),
        "missing": missing,
    }


def _health_qwen3_tts() -> dict[str, Any]:
    root = qwen3_tts_paths.root()
    missing = qwen3_tts_paths.missing_required_files()
    optional_missing = qwen3_tts_paths.missing_optional_files()
    return {
        "healthy": not missing,
        "status": "ok" if not missing else "external_runtime_missing",
        "model_path": str(root),
        "python": str(root / ".venv" / "bin" / "python"),
        "missing": missing,
        "optional_missing": optional_missing,
        "voice_design_available": not optional_missing,
    }


def _health_external_engine(engine_id: str) -> dict[str, Any]:
    try:
        root = external_engine_root(engine_id)
    except RuntimeError as exc:
        return {
            "healthy": False,
            "status": "external_runtime_unconfigured",
            "detail": str(exc),
            "missing": ["engine_root_env"],
        }
    python = root / ".venv" / "bin" / "python"
    required: list[Path] = [python]
    model_path = root
    if engine_id == "emotivoice":
        required.extend(
            [
                root / "frontend.py",
                root / "inference_am_vocoder_joint.py",
                root / "WangZeJun" / "simbert-base-chinese" / "pytorch_model.bin",
                root / "outputs" / "prompt_tts_open_source_joint" / "ckpt" / "g_00140000",
            ]
        )
    elif engine_id == "f5-tts":
        required.extend(
            [
                root / "src" / "f5_tts" / "api.py",
                root / "local_smoke" / "modelscope" / "F5-TTS_Emilia-ZH-EN" / "model_1250000.safetensors",
                root / "local_smoke" / "modelscope" / "F5-TTS_Emilia-ZH-EN" / "vocab.txt",
            ]
        )
    elif engine_id in {"cosyvoice-sft", "cosyvoice-zero-shot"}:
        model_path = cosyvoice_worker.model_directory(root, engine_id)
        required.extend(
            [
                root / "cosyvoice" / "cli" / "cosyvoice.py",
                root / "third_party" / "Matcha-TTS",
                *(model_path / name for name in cosyvoice_worker.required_model_files(engine_id)),
            ]
        )
    missing = [str(path.relative_to(root)) if path.is_relative_to(root) else str(path) for path in required if not path.exists()]
    result = {
        "healthy": not missing,
        "status": "ok" if not missing else "external_runtime_missing",
        "model_path": str(model_path),
        "python": str(python),
        "missing": missing,
    }
    if engine_id in {"cosyvoice-sft", "cosyvoice-zero-shot"}:
        result["runtime_path"] = str(root)
    return result


def _health_omnivoice() -> dict[str, Any]:
    from app.services.inference_runner import OMNIVOICE_MODEL_ID, omnivoice_local_snapshot

    try:
        model_path = omnivoice_local_snapshot()
    except RuntimeError as exc:
        return {
            "healthy": False,
            "status": "model_missing",
            "model_id": OMNIVOICE_MODEL_ID,
            "detail": str(exc),
        }
    try:
        import omnivoice  # noqa: F401

        return {
            "healthy": True,
            "status": "ok",
            "model_id": OMNIVOICE_MODEL_ID,
            "model_path": str(model_path),
        }
    except Exception as exc:
        return {
            "healthy": False,
            "status": "package_missing",
            "model_id": OMNIVOICE_MODEL_ID,
            "model_path": str(model_path),
            "detail": str(exc),
        }
