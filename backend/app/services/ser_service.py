"""SER（语音情绪识别）服务。

用 emotion2vec+ 模型给音色参考音频打情绪标签。模型权重放在受管模型目录
（``VOICE_STUDIO_MODELS_DIR`` 下的 ``emotion2vec-plus-large``），推理在子进程
里完成，主进程不导入 torch 或 funasr。

emotion2vec 与 CAM++ 同属 funasr 框架，因此复用 CAM++ 引擎运行时里的 Python
环境，不再单独安装一份 torch。运行时或模型缺失时由 :func:`health_check` 明确
报告，不做静默回退。
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from app.services import settings_store, speaker_verification_service
from app.services.paths import expand_path
from app.services.python_runtime import engine_virtualenv_python

log = logging.getLogger(__name__)

MODEL_ID = "emotion2vec-plus-large"
MODEL_REPO = "iic/emotion2vec_plus_large"
MODEL_LABEL = "emotion2vec+ large 情绪识别"

# 模型目录里必须齐全的文件，缺任何一个 funasr 都加载不了。
REQUIRED_MODEL_FILES = ("model.pt", "config.yaml", "configuration.json", "tokens.txt")

_PROCESS_POLL_SECONDS = 0.05
_PROCESS_TERMINATE_GRACE_SECONDS = 2
DEFAULT_TIMEOUT_SECONDS = 900

# emotion2vec+ 的 9 类情绪标签（英文键，模型也可能返回「中文/英文」形式）
EMOTION2VEC_LABELS = [
    "angry", "disgusted", "fearful", "happy", "neutral",
    "other", "sad", "surprised", "unknown",
]

# 映射到项目内置 EMOTIONS 列表
_LABEL_MAP: dict[str, str] = {
    "angry": "angry",
    "disgusted": "disgusted",
    "fearful": "afraid",
    "happy": "happy",
    "neutral": "calm",
    "sad": "sad",
    "surprised": "surprised",
    "other": "calm",
    "unknown": "calm",
}


def runtime_root() -> Path:
    """情绪识别复用的 funasr 引擎运行时目录。"""
    return speaker_verification_service.runtime_root()


def runtime_python() -> Path:
    return engine_virtualenv_python(runtime_root())


def worker_script() -> Path:
    return Path(__file__).with_name("ser_worker.py")


def model_path() -> Path:
    if configured := os.environ.get("VOICE_STUDIO_EMOTION2VEC_MODEL"):
        return expand_path(configured)
    return settings_store.model_path(MODEL_ID)


def missing_model_files(path: Path | None = None) -> list[str]:
    root = path or model_path()
    return [name for name in REQUIRED_MODEL_FILES if not (root / name).exists()]


def health_check() -> dict[str, Any]:
    """报告情绪识别是否可用，语义与其它引擎的健康检查保持一致。"""
    root = model_path()
    python = runtime_python()
    worker = worker_script()

    runtime_missing = [str(item) for item in (python, worker) if not item.exists()]
    if runtime_missing:
        return {
            "healthy": False,
            "status": "runtime_missing",
            "model_id": MODEL_ID,
            "model_path": str(root),
            "runtime_path": str(runtime_root()),
            "python": str(python),
            "missing": runtime_missing,
        }

    model_missing = missing_model_files(root)
    if model_missing:
        return {
            "healthy": False,
            "status": "model_missing",
            "model_id": MODEL_ID,
            "model_path": str(root),
            "runtime_path": str(runtime_root()),
            "python": str(python),
            "missing": model_missing,
        }

    return {
        "healthy": True,
        "status": "ok",
        "model_id": MODEL_ID,
        "model_path": str(root),
        "runtime_path": str(runtime_root()),
        "python": str(python),
        "missing": [],
    }


def unavailable_detail(health: dict[str, Any]) -> str:
    """把健康检查结果翻成可以直接给用户看的一句话。"""
    status = str(health.get("status") or "unknown")
    if status == "runtime_missing":
        return "情绪识别缺少 funasr 运行环境或 worker 脚本。"
    if status == "model_missing":
        missing = "、".join(str(item) for item in health.get("missing") or [])
        return f"情绪识别模型尚未安装完整，缺少：{missing}。"
    return "情绪识别当前不可用。"


def _normalize_label(label: str) -> str:
    """把模型标签归一成英文键。

    emotion2vec+ 在不同版本里会返回 ``angry`` 或 ``生气/angry`` 两种写法，
    统一取最后一段的英文名，避免正确识别出的情绪被当成未知而丢弃。
    """
    text = str(label).strip()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.strip().lower()


def _map_scores(raw_scores: dict[str, float]) -> tuple[str, str, dict[str, float]]:
    raw_top = max(raw_scores, key=lambda key: raw_scores[key]) if raw_scores else "unknown"
    mapped: dict[str, float] = {}
    for label, score in raw_scores.items():
        # <unk> 这类特殊 token 不属于任何情绪，直接丢弃比归到「平静」更准确。
        target = _LABEL_MAP.get(_normalize_label(label))
        if target is None:
            continue
        mapped[target] = mapped.get(target, 0.0) + float(score)
    top = max(mapped, key=lambda key: mapped[key]) if mapped else "calm"
    return top, raw_top, mapped


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
        raise RuntimeError("SER worker did not exit after forced termination") from None


def _run_worker(clips: Sequence[Path], *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    payload = json.dumps(
        {
            "model_dir": str(model_path()),
            "device": os.environ.get("VOICE_STUDIO_SER_DEVICE", "cpu"),
            "clips": [str(clip) for clip in clips],
        }
    )
    command = [str(runtime_python()), str(worker_script())]
    with tempfile.TemporaryDirectory(prefix="voice-studio-ser-transport-") as temp_dir:
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
                # 模型加载与 funasr 的导入提示都可能先于推理输出，因此三条流都用
                # 普通文件，避免任何一端写满管道后把父进程卡住。
                process = subprocess.Popen(
                    command,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(f"情绪识别 worker 超过 {timeout:g}s 未完成")
                    time.sleep(min(_PROCESS_POLL_SECONDS, remaining))
            finally:
                if process is not None:
                    _terminate_worker(process)
        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
        returncode = process.returncode if process is not None else None

    if returncode != 0:
        raise RuntimeError((stderr_text or stdout_text or "情绪识别 worker 执行失败")[-1200:])

    lines = [line for line in stdout_text.strip().splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("情绪识别 worker 没有返回结果")
    response = json.loads(lines[-1])
    results = response.get("results")
    if not isinstance(results, list):
        raise RuntimeError("情绪识别 worker 返回结果格式不正确")
    return [dict(item) for item in results]


def predict_emotions(
    audio_paths: Sequence[str | Path],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """批量识别情绪。只启动一次 worker，模型只加载一次。

    每一项要么是识别结果，要么是 ``{"error": ...}``。
    """
    clips = [Path(item) for item in audio_paths]
    missing = [str(clip) for clip in clips if not clip.exists()]
    if missing and len(missing) == len(clips):
        return [{"error": f"Audio file not found: {path}"} for path in missing]

    try:
        raw_results = _run_worker(clips, timeout=timeout)
    except Exception as exc:
        log.warning("SER worker failed: %s", exc)
        message = str(exc)
        return [{"error": message} for _ in clips]

    results: list[dict[str, Any]] = []
    for index, item in enumerate(raw_results):
        if "error" in item:
            results.append({"error": str(item["error"])})
            continue
        scores = {str(key): float(value) for key, value in (item.get("raw_scores") or {}).items()}
        top, raw_top, mapped = _map_scores(scores)
        results.append(
            {
                "top_emotion": top,
                "raw_top_emotion": raw_top,
                "emotion_scores": mapped,
                "raw_scores": scores,
            }
        )
    # worker 少返回结果时补齐占位，保证调用方能按位置对应回原音频。
    while len(results) < len(clips):
        results.append({"error": "情绪识别 worker 返回结果数量不足"})
    return results


def predict_emotion(audio_path: str | Path) -> dict[str, Any]:
    """对单个音频文件预测情绪。

    返回:
        {
            "top_emotion": "happy",
            "raw_top_emotion": "happy",
            "emotion_scores": {"happy": 0.82, "calm": 0.10, ...},
            "raw_scores": {"happy": 0.82, ...},
        }
    """
    path = Path(audio_path)
    if not path.exists():
        return {"error": f"Audio file not found: {path}"}
    return predict_emotions([path])[0]
