"""Local LaBSE provider for video-localization semantic alignment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from safetensors.torch import load_file
from transformers import AutoModel, AutoTokenizer

from app.domains.video_localization.localization_semantic_alignment import (
    MODEL_ID,
)
from app.services import settings_store


MODEL_ENV = "VOICE_STUDIO_LABSE_MODEL_DIR"
MODEL_DIRECTORY_NAME = "sentence-transformers-labse"


def model_candidates() -> list[Path]:
    return settings_store.model_candidates("semantic-alignment-labse")


def resolve_model_dir() -> Path:
    for candidate in model_candidates():
        if _is_complete(candidate):
            return candidate.expanduser().resolve()
    raise ValueError(
        "缺少本土化语义映射模型 LaBSE。请在模型目录安装 "
        f"{MODEL_DIRECTORY_NAME}，或设置 {MODEL_ENV}。"
    )


def health_check() -> dict[str, object]:
    candidates = model_candidates()
    for candidate in candidates:
        if _is_complete(candidate):
            return {
                "healthy": True,
                "status": "ready",
                "model_path": str(candidate),
            }
    return {
        "healthy": False,
        "status": "model_missing",
        "model_path": str(candidates[0]),
        "detail": "LaBSE 模型尚未下载到统一模型目录。",
    }


class LabseTextEncoder:
    """Lazy local encoder backed by a verified LaBSE model directory."""

    model_id = MODEL_ID

    def __init__(
        self,
        model_dir: Path | None = None,
        *,
        device: str = "cpu",
    ) -> None:
        self.model_dir = (
            model_dir.expanduser().resolve()
            if model_dir is not None
            else resolve_model_dir()
        )
        self.device = torch.device(device)
        self.model_fingerprint = _model_fingerprint(self.model_dir)
        self._tokenizer = None
        self._model = None
        self._dense = None

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 768), dtype=np.float32)
        self._ensure_loaded()
        batches = []
        with torch.inference_mode():
            for offset in range(0, len(texts), 24):
                encoded = self._tokenizer(
                    texts[offset : offset + 24],
                    max_length=256,
                    padding=True,
                    truncation=True,
                    return_tensors="pt",
                )
                encoded = {
                    key: value.to(self.device)
                    for key, value in encoded.items()
                }
                output = self._model(**encoded)
                vector = output.last_hidden_state[:, 0]
                weight, bias = self._dense
                vector = torch.tanh(F.linear(vector, weight, bias))
                batches.append(
                    F.normalize(vector.float(), p=2, dim=1).cpu()
                )
        return torch.cat(batches, dim=0).numpy()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if not _is_complete(self.model_dir):
            raise ValueError("LaBSE 模型目录不完整。")
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_dir,
            local_files_only=True,
        )
        self._model = AutoModel.from_pretrained(
            self.model_dir,
            local_files_only=True,
            dtype=torch.float32,
        ).eval()
        self._model.to(self.device)
        dense = load_file(
            self.model_dir / "2_Dense" / "model.safetensors"
        )
        self._dense = (
            dense["linear.weight"].to(self.device),
            dense["linear.bias"].to(self.device),
        )


def _is_complete(model_dir: Path) -> bool:
    return all(
        path.is_file()
        for path in [
            model_dir / "config.json",
            model_dir / "model.safetensors",
            model_dir / "2_Dense" / "model.safetensors",
        ]
    )


def is_complete(model_dir: Path) -> bool:
    return _is_complete(model_dir)


def _model_fingerprint(model_dir: Path) -> str:
    manifest = model_dir / "download-manifest.json"
    if manifest.is_file():
        return hashlib.sha256(manifest.read_bytes()).hexdigest()
    payload = [
        {
            "name": str(path.relative_to(model_dir)),
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in [
            model_dir / "config.json",
            model_dir / "model.safetensors",
            model_dir / "2_Dense" / "model.safetensors",
        ]
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "LabseTextEncoder",
    "MODEL_DIRECTORY_NAME",
    "MODEL_ENV",
    "model_candidates",
    "health_check",
    "is_complete",
    "resolve_model_dir",
]
