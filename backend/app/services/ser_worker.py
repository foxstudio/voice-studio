"""emotion2vec+ 情绪识别的子进程入口。

父进程把请求写进 stdin，worker 在 funasr 运行时里加载本地模型并批量推理，
结果以 JSON 写到 stdout。torch 与 funasr 只在这个子进程里导入，主进程不会被
引擎依赖污染；模型路径由父进程传入，这里不做任何联网下载。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TARGET_SAMPLE_RATE = 16000


def _load_audio(path: Path) -> Any:
    import librosa
    import numpy as np
    import soundfile as sf

    wav, sr = sf.read(str(path), dtype="float32")
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    if sr != TARGET_SAMPLE_RATE:
        wav = librosa.resample(np.asarray(wav), orig_sr=sr, target_sr=TARGET_SAMPLE_RATE)
    return np.asarray(wav, dtype="float32")


def _raw_scores(output: Any) -> dict[str, float]:
    """兼容不同 funasr 版本的返回结构，取出原始情绪分数字典。"""
    if not output:
        return {}
    first = output[0]
    scores: dict[str, float] = {}
    if isinstance(first, dict):
        if "emo" in first:
            scores = dict(first["emo"] or {})
        elif "scores" in first and "labels" in first:
            labels = first["labels"] or []
            values = first["scores"] or []
            scores = {str(label): float(score) for label, score in zip(labels, values)}
    elif isinstance(first, (list, tuple)):
        for item in first:
            if isinstance(item, dict) and "emo" in item:
                scores = dict(item["emo"] or {})
    return {str(key): float(value) for key, value in scores.items()}


def main() -> None:
    payload = json.loads(sys.stdin.read())
    model_dir = Path(payload["model_dir"])
    device = str(payload.get("device") or "cpu")
    clips = [str(item) for item in payload.get("clips") or []]

    if not clips:
        print(json.dumps({"results": []}))
        return

    from funasr import AutoModel

    model = AutoModel(model=str(model_dir), hub="ms", device=device)

    results: list[dict[str, Any]] = []
    for clip in clips:
        path = Path(clip)
        if not path.exists():
            results.append({"error": f"Audio file not found: {path}"})
            continue
        try:
            wav = _load_audio(path)
            scores = _raw_scores(model.generate(wav, sr=TARGET_SAMPLE_RATE, batch_size=1))
        except Exception as exc:  # 单个音频失败不影响同批其它音频
            results.append({"error": f"{type(exc).__name__}: {exc}"})
            continue
        if not scores:
            results.append({"error": "Model returned empty result"})
            continue
        results.append({"raw_scores": scores})

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
