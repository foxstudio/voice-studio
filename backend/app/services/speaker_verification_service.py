from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.services import audio_tools, settings_store
from app.services.paths import expand_path
from app.services.python_runtime import engine_virtualenv_python


ENGINE_ID = "campplus-modelscope"
MODEL_ID = "iic/speech_campplus_sv_zh-cn_16k-common"
AUTO_MERGE_THRESHOLD = 0.75
REVIEW_THRESHOLD = 0.60
MAX_CLIPS_PER_LABEL = 4
MAX_CLIP_MS = 8000
MIN_CLIP_MS = 1500
_PROCESS_POLL_SECONDS = 0.05
_PROCESS_TERMINATE_GRACE_SECONDS = 2


def runtime_root() -> Path:
    if configured := os.environ.get("VOICE_STUDIO_CAMPPLUS_ROOT"):
        return expand_path(configured)
    return expand_path(settings_store.get().data_dir) / "engines" / "campplus-speaker-verifier"


def model_path() -> Path:
    if configured := os.environ.get("VOICE_STUDIO_CAMPPLUS_MODEL"):
        return expand_path(configured)
    return settings_store.model_path(ENGINE_ID)


def health_check() -> dict[str, Any]:
    resolved_runtime_root = runtime_root()
    resolved_model_path = model_path()
    python = engine_virtualenv_python(resolved_runtime_root)
    worker = Path(__file__).with_name("speaker_verification_worker.py")
    missing = [
        str(path)
        for path in (python, worker, resolved_model_path / "campplus_cn_common.bin")
        if not path.exists()
    ]
    return {
        "healthy": not missing,
        "status": "ready" if not missing else "runtime_missing",
        "engine_id": ENGINE_ID,
        "model_id": MODEL_ID,
        "runtime_root": str(resolved_runtime_root),
        "model_path": str(resolved_model_path),
        "missing": missing,
    }


def consolidate_clusters(
    *,
    audio_path: str | Path,
    segments: list[dict[str, Any]],
    timeout: float = 180,
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
    selected_labels = {
        str(item["speaker"])
        for item in selected
    }
    missing_labels = set(labels) - selected_labels
    if not selected_labels:
        mapping = {label: f"cluster_{index:02d}" for index, label in enumerate(labels, start=1)}
        return _result(mapping, [], [], status="partial", reason="insufficient_clean_audio")

    with tempfile.TemporaryDirectory(prefix="voice-studio-campplus-") as temp_dir:
        clip_paths, clip_labels = _write_clips(Path(audio_path), selected, Path(temp_dir))
        embeddings, worker_meta = _extract_embeddings(
            clip_paths,
            python=engine_virtualenv_python(Path(health["runtime_root"])),
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
        status=("partial" if missing_labels else "completed"),
        reason=(
            "insufficient_clean_audio"
            if missing_labels
            else "verified"
        ),
        unverified_labels=sorted(missing_labels),
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
    timeout: float,
    cancel_check: Callable[[], bool] | None,
) -> tuple[np.ndarray, dict[str, Any]]:
    if cancel_check and cancel_check():
        raise RuntimeError("Speaker verification cancelled")
    payload = json.dumps({"model_path": str(model_path), "clips": [str(path) for path in clips]})
    command = [str(python), str(Path(__file__).with_name("speaker_verification_worker.py"))]
    with tempfile.TemporaryDirectory(prefix="voice-studio-campplus-transport-") as temp_dir:
        transport_dir = Path(temp_dir)
        input_path = transport_dir / "request.json"
        stdout_path = transport_dir / "response.stdout"
        stderr_path = transport_dir / "worker.stderr"
        input_path.write_text(payload, encoding="utf-8")
        process: subprocess.Popen[bytes] | None = None
        with (
            input_path.open("rb") as stdin,
            stdout_path.open("wb") as stdout,
            stderr_path.open("wb") as stderr,
        ):
            try:
                # CAM++ can print import failures before it starts consuming input.  Keep all
                # three streams as regular files so no pipe writer can block the parent.
                process = subprocess.Popen(
                    command,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if cancel_check and cancel_check():
                        raise RuntimeError("Speaker verification cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(f"CAM++ worker timed out after {timeout:g}s")
                    time.sleep(min(_PROCESS_POLL_SECONDS, remaining))
            finally:
                if process is not None:
                    _terminate_worker(process)
        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
        returncode = process.returncode if process is not None else None
    if returncode != 0:
        raise RuntimeError((stderr_text or stdout_text or "CAM++ worker failed")[-1200:])
    response = json.loads(stdout_text.strip().splitlines()[-1])
    embeddings = np.asarray(response.get("embeddings") or [], dtype=np.float32)
    if embeddings.shape != (len(clips), 192):
        raise RuntimeError(f"CAM++ returned an unexpected embedding shape: {embeddings.shape}")
    return embeddings, dict(response.get("metadata") or {})


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_PROCESS_TERMINATE_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_PROCESS_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        raise RuntimeError("CAM++ worker did not exit after forced termination") from None


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
