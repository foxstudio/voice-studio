from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.services import audio_tools


ENGINE_ID = "campplus-modelscope"
MODEL_ID = "iic/speech_campplus_sv_zh-cn_16k-common"
DEFAULT_RUNTIME_ROOT = Path("~/VoiceStudio/engines/campplus-speaker-verifier").expanduser()
DEFAULT_MODEL_PATH = Path("~/VoiceStudio/models/campplus-speaker-verifier").expanduser()
AUTO_MERGE_THRESHOLD = 0.75
REVIEW_THRESHOLD = 0.60
MAX_CLIPS_PER_LABEL = 4
MAX_CLIP_MS = 8000
MIN_CLIP_MS = 1500


def health_check() -> dict[str, Any]:
    runtime_root = Path(os.environ.get("VOICE_STUDIO_CAMPPLUS_ROOT", DEFAULT_RUNTIME_ROOT)).expanduser()
    model_path = Path(os.environ.get("VOICE_STUDIO_CAMPPLUS_MODEL", DEFAULT_MODEL_PATH)).expanduser()
    python = runtime_root / ".venv" / "bin" / "python"
    worker = Path(__file__).with_name("speaker_verification_worker.py")
    missing = [str(path) for path in (python, worker, model_path / "campplus_cn_common.bin") if not path.exists()]
    return {
        "healthy": not missing,
        "status": "ready" if not missing else "runtime_missing",
        "engine_id": ENGINE_ID,
        "model_id": MODEL_ID,
        "runtime_root": str(runtime_root),
        "model_path": str(model_path),
        "missing": missing,
    }


def consolidate_clusters(
    *,
    audio_path: str | Path,
    segments: list[dict[str, Any]],
    timeout: int = 180,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    labels = sorted({str(item.get("speaker") or "").strip() for item in segments if item.get("speaker")})
    if len(labels) <= 1:
        mapping = {label: "cluster_01" for label in labels}
        return _result(mapping, [], [], status="skipped", reason="single_cluster")

    health = health_check()
    if not health["healthy"]:
        mapping = {label: f"cluster_{index:02d}" for index, label in enumerate(labels, start=1)}
        return _result(mapping, [], [], status="failed", reason="campplus_unavailable", error=", ".join(health["missing"]))

    selected = _representative_segments(segments)
    if len({item["speaker"] for item in selected}) < len(labels):
        mapping = {label: f"cluster_{index:02d}" for index, label in enumerate(labels, start=1)}
        return _result(mapping, [], [], status="partial", reason="insufficient_clean_audio")

    with tempfile.TemporaryDirectory(prefix="voice-studio-campplus-") as temp_dir:
        clip_paths, clip_labels = _write_clips(Path(audio_path), selected, Path(temp_dir))
        embeddings, worker_meta = _extract_embeddings(
            clip_paths,
            python=Path(health["runtime_root"]) / ".venv" / "bin" / "python",
            model_path=Path(health["model_path"]),
            timeout=timeout,
            cancel_check=cancel_check,
        )

    centroids = _centroids(embeddings, clip_labels)
    similarities = _pairwise_similarities(centroids)
    parent = {label: label for label in labels}

    def find(label: str) -> str:
        while parent[label] != label:
            parent[label] = parent[parent[label]]
            label = parent[label]
        return label

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    auto_merged: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    for pair in similarities:
        if pair["cosine"] >= AUTO_MERGE_THRESHOLD:
            union(pair["left"], pair["right"])
            auto_merged.append(pair)
        elif pair["cosine"] >= REVIEW_THRESHOLD:
            needs_review.append(pair)

    groups: dict[str, list[str]] = defaultdict(list)
    for label in labels:
        groups[find(label)].append(label)
    ordered_groups = sorted(groups.values(), key=lambda group: min(_first_start(segments, label) for label in group))
    mapping = {
        label: f"cluster_{index:02d}"
        for index, group in enumerate(ordered_groups, start=1)
        for label in group
    }
    return _result(
        mapping,
        auto_merged,
        needs_review,
        status="completed",
        reason="verified",
        similarities=similarities,
        worker_metadata=worker_meta,
    )


def _representative_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overlapping: set[int] = set()
    ordered = sorted(enumerate(segments), key=lambda item: (int(item[1]["start_ms"]), int(item[1]["end_ms"])))
    for position, (left_index, left) in enumerate(ordered):
        for right_index, right in ordered[position + 1 :]:
            if int(right["start_ms"]) >= int(left["end_ms"]):
                break
            if left.get("speaker") != right.get("speaker"):
                overlapping.update({left_index, right_index})
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, segment in enumerate(segments):
        duration = int(segment["end_ms"]) - int(segment["start_ms"])
        label = str(segment.get("speaker") or "").strip()
        if label and index not in overlapping and duration >= MIN_CLIP_MS:
            grouped[label].append(segment)
    selected: list[dict[str, Any]] = []
    for label in sorted(grouped):
        selected.extend(sorted(grouped[label], key=lambda item: int(item["end_ms"]) - int(item["start_ms"]), reverse=True)[:MAX_CLIPS_PER_LABEL])
    return selected


def _write_clips(audio_path: Path, segments: list[dict[str, Any]], temp_dir: Path) -> tuple[list[Path], list[str]]:
    audio, sample_rate = audio_tools.read_audio(audio_path)
    paths: list[Path] = []
    labels: list[str] = []
    for index, segment in enumerate(segments, start=1):
        start_ms = max(0, int(segment["start_ms"]))
        end_ms = min(int(segment["end_ms"]), start_ms + MAX_CLIP_MS)
        start_frame = max(0, round(start_ms * sample_rate / 1000))
        end_frame = min(len(audio), max(start_frame + 1, round(end_ms * sample_rate / 1000)))
        path = temp_dir / f"clip-{index:03d}.wav"
        audio_tools.write_audio(path, audio[start_frame:end_frame], sample_rate, fmt="wav")
        paths.append(path)
        labels.append(str(segment["speaker"]))
    return paths, labels


def _extract_embeddings(
    clips: list[Path],
    *,
    python: Path,
    model_path: Path,
    timeout: int,
    cancel_check: Callable[[], bool] | None,
) -> tuple[np.ndarray, dict[str, Any]]:
    if cancel_check and cancel_check():
        raise RuntimeError("Speaker verification cancelled")
    payload = json.dumps({"model_path": str(model_path), "clips": [str(path) for path in clips]})
    completed = subprocess.run(
        [str(python), str(Path(__file__).with_name("speaker_verification_worker.py"))],
        input=payload,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "CAM++ worker failed")[-1200:])
    response = json.loads(completed.stdout.strip().splitlines()[-1])
    embeddings = np.asarray(response.get("embeddings") or [], dtype=np.float32)
    if embeddings.shape != (len(clips), 192):
        raise RuntimeError(f"CAM++ returned an unexpected embedding shape: {embeddings.shape}")
    return embeddings, dict(response.get("metadata") or {})


def _centroids(embeddings: np.ndarray, labels: list[str]) -> dict[str, np.ndarray]:
    grouped: dict[str, list[np.ndarray]] = defaultdict(list)
    for embedding, label in zip(embeddings, labels):
        grouped[label].append(embedding)
    centroids = {}
    for label, items in grouped.items():
        centroid = np.mean(np.asarray(items), axis=0)
        centroids[label] = centroid / max(float(np.linalg.norm(centroid)), 1e-8)
    return centroids


def _pairwise_similarities(centroids: dict[str, np.ndarray]) -> list[dict[str, Any]]:
    labels = sorted(centroids)
    return [
        {"left": left, "right": right, "cosine": float(np.dot(centroids[left], centroids[right]))}
        for index, left in enumerate(labels)
        for right in labels[index + 1 :]
    ]


def _first_start(segments: list[dict[str, Any]], label: str) -> int:
    return min(int(item["start_ms"]) for item in segments if item.get("speaker") == label)


def _result(mapping: dict[str, str], auto_merged: list[dict[str, Any]], needs_review: list[dict[str, Any]], *, status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "engine_id": ENGINE_ID,
        "model_id": MODEL_ID,
        "mapping": mapping,
        "auto_merged": auto_merged,
        "needs_review": needs_review,
        "thresholds": {"auto_merge": AUTO_MERGE_THRESHOLD, "review": REVIEW_THRESHOLD},
        **extra,
    }
