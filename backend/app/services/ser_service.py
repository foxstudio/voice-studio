"""SER (Speech Emotion Recognition) 服务

使用 emotion2vec+ 模型对音频进行情绪分类。
懒加载模型，首次推理时初始化。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MODEL = None
_MODEL_LOCK = threading.Lock()

# emotion2vec+ 的 9 类情绪标签
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


def _load_model():
    """懒加载 emotion2vec+ 模型"""
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    from funasr import AutoModel

    log.info("Loading emotion2vec+ model (first call, may take a while)...")
    model = AutoModel(
        model="iic/emotion2vec_plus_large",
        hub="ms",  # ModelScope
        device="cuda:0" if _has_cuda() else "mps" if _has_mps() else "cpu",
    )
    _MODEL = model
    log.info("emotion2vec+ model loaded.")
    return model


def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _has_mps() -> bool:
    try:
        import torch
        return torch.backends.mps.is_available()
    except Exception:
        return False


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

    model = _load_model()

    # 用 soundfile 加载音频，转成 16kHz
    import numpy as np
    import soundfile as sf

    wav, sr = sf.read(str(path), dtype="float32")
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    if sr != 16000:
        try:
            import librosa
            wav = librosa.resample(wav, orig_sr=sr, target_sr=16000)
        except ImportError:
            ratio = 16000 / sr
            n_out = int(len(wav) * ratio)
            indices = np.linspace(0, len(wav) - 1, n_out).astype(int)
            wav = wav[indices]
        sr = 16000

    # emotion2vec+ 推理
    result = model.generate(wav, sr=sr, batch_size=1)

    if not result:
        return {"error": "Model returned empty result"}

    first = result[0]
    raw_scores: dict[str, float] = {}

    # 兼容不同版本的返回格式
    if isinstance(first, dict):
        if "emo" in first:
            raw_scores = first["emo"]
        elif "scores" in first and "labels" in first:
            for label, score in zip(first["labels"], first["scores"]):
                raw_scores[label] = float(score)
    elif isinstance(first, (list, tuple)) and len(first) >= 2:
        for item in first:
            if isinstance(item, dict) and "emo" in item:
                raw_scores = item["emo"]

    if raw_scores:
        raw_top = max(raw_scores, key=lambda k: raw_scores[k])
    else:
        raw_top = "unknown"

    # 映射到项目情绪标签，合并分数
    mapped_scores: dict[str, float] = {}
    for label, score in raw_scores.items():
        mapped = _LABEL_MAP.get(label, "calm")
        mapped_scores[mapped] = mapped_scores.get(mapped, 0.0) + float(score)

    top_emotion = max(mapped_scores, key=lambda k: mapped_scores[k]) if mapped_scores else "calm"

    return {
        "top_emotion": top_emotion,
        "raw_top_emotion": raw_top,
        "emotion_scores": mapped_scores,
        "raw_scores": raw_scores,
    }
