from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.schemas.voice_studio import AppSettings
from app.services import confucius4_paths
from app.services.paths import PROJECT_ROOT, expand_path


_ENGINE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MANAGED_DIRECTORY_NAMES = {
    "indextts-v2": "mlx-indexTTS-2.0",
    "moss-transcribe-diarize-mlx": "moss-transcribe-diarize-8bit",
    "campplus-modelscope": "campplus-speaker-verifier",
    "qwen3-forced-aligner": "Qwen3-ForcedAligner-0.6B",
    "semantic-alignment-labse": "sentence-transformers-labse",
}
_MODEL_DIRECTORY_ENV_VARS = {
    "indextts-v2": "VOICE_STUDIO_INDEXTTS_MODEL_DIR",
    "omnivoice": "VOICE_STUDIO_OMNIVOICE_MODEL_DIR",
    "qwen3-asr-mlx": "VOICE_STUDIO_QWEN3_ASR_MODEL_DIR",
    "qwen3-forced-aligner": "VOICE_STUDIO_QWEN_ALIGN_CHECKPOINT",
    "semantic-alignment-labse": "VOICE_STUDIO_LABSE_MODEL_DIR",
    "faster-whisper-turbo": "VOICE_STUDIO_FASTER_WHISPER_MODEL_DIR",
    "moss-transcribe-diarize-mlx": "VOICE_STUDIO_MOSS_MODEL_DIR",
    "campplus-modelscope": "VOICE_STUDIO_CAMPPLUS_MODEL",
}


@dataclass(frozen=True)
class ModelLocation:
    path: Path
    source: str
    managed: bool
    exists: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "source": self.source,
            "managed": self.managed,
            "exists": self.exists,
            "is_symlink": self.path.is_symlink(),
        }


def _validate_engine_id(engine_id: str) -> str:
    normalized = str(engine_id).strip().lower()
    if not _ENGINE_ID.fullmatch(normalized):
        raise ValueError(f"Invalid engine id: {engine_id!r}")
    return normalized


def managed_model_root(
    settings: AppSettings,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the only write root for application-managed model weights.

    The process environment intentionally wins over persisted settings so an
    operator can move one shared model library without editing every database
    that points at it.  Read-only legacy/cache fallbacks are handled separately
    by :func:`model_candidates`.
    """

    env = os.environ if environ is None else environ
    configured = env.get("VOICE_STUDIO_MODELS_DIR") or settings.model_dir
    return expand_path(configured, PROJECT_ROOT)


def managed_model_path(
    engine_id: str,
    settings: AppSettings,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    normalized = _validate_engine_id(engine_id)
    directory_name = _MANAGED_DIRECTORY_NAMES.get(normalized, normalized)
    return managed_model_root(settings, environ=environ) / directory_name


def model_candidates(
    engine_id: str,
    settings: AppSettings,
    *,
    environ: Mapping[str, str] | None = None,
) -> list[Path]:
    normalized = _validate_engine_id(engine_id)
    env = os.environ if environ is None else environ
    managed = managed_model_path(normalized, settings, environ=env)
    data_root = expand_path(env.get("VOICE_STUDIO_DATA_DIR", settings.data_dir))
    repo_models = PROJECT_ROOT / "models"
    configured = _configured_model_path(normalized, environ=env)

    if normalized == "indextts-v2":
        return _dedupe_paths(
            [
                *([configured] if configured else []),
                managed,
                data_root / "models" / "mlx-indexTTS-2.0",
                repo_models / "mlx-indexTTS-2.0",
            ]
        )
    if normalized == confucius4_paths.ENGINE_ID:
        return confucius4_paths.model_candidates(
            managed_model_root(settings, environ=env),
            environ=env,
        )
    if normalized == "qwen3-asr-mlx":
        candidates: list[Path] = [configured] if configured else []
        candidates.extend(
            [
                managed,
                data_root / "models" / normalized,
                managed.parent / "mlx-community_Qwen3-ASR-1.7B-8bit",
                managed.parent
                / "mlx-audio"
                / "mlx-community_Qwen3-ASR-1.7B-8bit",
                repo_models / normalized,
                repo_models / "mlx-community_Qwen3-ASR-1.7B-8bit",
                *_huggingface_snapshots(
                    ("models--mlx-community--Qwen3-ASR-1.7B-8bit", "models--*--*Qwen3*ASR*"),
                    environ=env,
                ),
            ]
        )
        return _dedupe_paths(candidates)
    if normalized == "faster-whisper-turbo":
        return _dedupe_paths(
            [
                *([configured] if configured else []),
                managed,
                *_huggingface_snapshots(
                    ("models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",),
                    latest_only=True,
                    environ=env,
                ),
            ]
        )
    if normalized == "omnivoice":
        return _dedupe_paths(
            [
                *([configured] if configured else []),
                managed,
                *_huggingface_snapshots(
                    ("models--k2-fsa--OmniVoice",),
                    environ=env,
                ),
            ]
        )
    if normalized in {"qwen3-forced-aligner", "semantic-alignment-labse"}:
        return _dedupe_paths(
            [
                *([configured] if configured else []),
                managed,
                data_root / "models" / _MANAGED_DIRECTORY_NAMES[normalized],
            ]
        )
    return _dedupe_paths([*([configured] if configured else []), managed])


def locate(
    engine_id: str,
    settings: AppSettings,
    *,
    environ: Mapping[str, str] | None = None,
) -> ModelLocation:
    locations = describe_locations(engine_id, settings, environ=environ)
    return next(
        (
            location
            for location in locations
            if location.exists
            and _is_usable_candidate(engine_id, location.path)
        ),
        next(location for location in locations if location.managed),
    )


def describe_locations(
    engine_id: str,
    settings: AppSettings,
    *,
    environ: Mapping[str, str] | None = None,
) -> list[ModelLocation]:
    managed = managed_model_path(engine_id, settings, environ=environ)
    return [
        ModelLocation(
            path=path,
            source="managed" if path == managed else "external_or_legacy",
            managed=path == managed,
            exists=path.exists(),
        )
        for path in model_candidates(engine_id, settings, environ=environ)
    ]


def _huggingface_snapshots(
    repo_patterns: tuple[str, ...],
    *,
    latest_only: bool = False,
    environ: Mapping[str, str] | None = None,
) -> list[Path]:
    hub_dir = _huggingface_hub_dir(environ=environ)
    snapshots: list[Path] = []
    seen: set[str] = set()
    for pattern in repo_patterns:
        try:
            repositories = hub_dir.glob(pattern)
            for repository in repositories:
                snapshot_root = repository / "snapshots"
                if not snapshot_root.is_dir():
                    continue
                for snapshot in snapshot_root.iterdir():
                    if snapshot.is_dir() and str(snapshot) not in seen:
                        seen.add(str(snapshot))
                        snapshots.append(snapshot)
        except OSError:
            continue

    def modified_at(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return -1

    snapshots.sort(key=lambda path: (modified_at(path), str(path)), reverse=True)
    return snapshots[:1] if latest_only else snapshots


def _huggingface_hub_dir(*, environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if configured := env.get("HF_HUB_CACHE") or env.get("HUGGINGFACE_HUB_CACHE"):
        return expand_path(configured)
    if configured := env.get("HF_HOME"):
        return expand_path(configured) / "hub"
    cache_home = expand_path(env.get("XDG_CACHE_HOME", "~/.cache"))
    return cache_home / "huggingface" / "hub"


def _configured_model_path(
    engine_id: str,
    *,
    environ: Mapping[str, str],
) -> Path | None:
    env_name = _MODEL_DIRECTORY_ENV_VARS.get(engine_id)
    configured = environ.get(env_name) if env_name else None
    return expand_path(configured, PROJECT_ROOT) if configured else None


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _has_local_payload(path: Path) -> bool:
    """Ignore empty/staging directories when resolving a readable model.

    Engine health checks remain responsible for validating their exact model
    manifests.  This small common guard prevents an abandoned empty managed
    directory from hiding a usable legacy/cache candidate.
    """

    if path.is_file():
        return True
    if not path.is_dir():
        return False
    try:
        return any(
            child.name not in {".locks", ".downloads", ".DS_Store"}
            for child in path.iterdir()
        )
    except OSError:
        return False


def _is_usable_candidate(engine_id: str, path: Path) -> bool:
    """Apply lightweight per-model manifests before choosing a read path."""

    normalized = _validate_engine_id(engine_id)
    try:
        if normalized == "indextts-v2":
            from app.services import indextts_model

            return indextts_model.core_files_available(path)
        if normalized == "omnivoice":
            from app.services import omnivoice_model

            return omnivoice_model.is_complete_directory(
                path,
                verify_integrity=False,
            )
        if normalized == "qwen3-asr-mlx":
            from app.services import qwen_mlx_asr

            return qwen_mlx_asr.model_files_available(path)
        if normalized == "faster-whisper-turbo":
            from app.services import faster_whisper_asr

            return faster_whisper_asr.model_files_available(path)
        if normalized == "qwen3-forced-aligner":
            from app.services import qwen_forced_aligner

            return qwen_forced_aligner.checkpoint_files_available(path)
        if normalized == "semantic-alignment-labse":
            from app.services import semantic_alignment_labse

            return semantic_alignment_labse.is_complete(path)
        if normalized == confucius4_paths.ENGINE_ID:
            return not confucius4_paths.missing_model_files(path)
    except OSError:
        return False
    return _has_local_payload(path)
