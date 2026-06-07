#!/usr/bin/env python3
"""Run a curated Voice Studio quality suite through the local FastAPI app.

The suite intentionally uses voice-library assets and keeps successful results
in history, so good examples become reusable instead of throwaway test text.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from httpx import ASGITransport, AsyncClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app  # noqa: E402
from app.services import audio_tools, voice_store  # noqa: E402


def _find_voice(*tags: str) -> str | None:
    for voice in voice_store.list_voices():
        if all(tag in voice.tags for tag in tags):
            return voice.voice_id
    return None


def _quality(path: str | Path) -> dict:
    metrics = audio_tools.quality_metrics(path)
    audio, sr = sf.read(str(path), always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    if len(audio) > 1:
        metrics["zero_crossing_rate"] = round(float(np.mean(np.abs(np.diff(np.signbit(audio))))), 6)
    else:
        metrics["zero_crossing_rate"] = 0.0
    return metrics


def build_cases() -> list[dict]:
    own_voice = _find_voice("user:lichao_agi_good_recording")
    official_female = _find_voice("seed:index_voice_01")
    official_male = _find_voice("seed:index_voice_02")
    official_sad = _find_voice("seed:index_emo_sad")
    documentary = _find_voice("voice_design:curated", "纪录片")
    suspense = _find_voice("voice_design:curated", "悬疑")

    return [
        {
            "id": "own_voice_knowledge_intro",
            "engine_id": "indextts-v2",
            "voice_id": own_voice,
            "text": "我们先从一个简单问题开始：神经网络为什么能够从大量样本里，学到看不见的规律？",
            "params": {"emotion": "calm", "emo_alpha": 0.45, "speed": 1.0, "temperature": 0.78},
        },
        {
            "id": "official_female_soft_explain",
            "engine_id": "indextts-v2",
            "voice_id": official_female,
            "text": "如果把模型训练比作一次远行，那么每一次误差回传，都是它重新校准方向的瞬间。",
            "params": {"emotion": "calm", "emo_alpha": 0.5, "speed": 0.96, "temperature": 0.8},
        },
        {
            "id": "official_male_fast_summary",
            "engine_id": "indextts-v2",
            "voice_id": official_male,
            "text": "总结一下，数据决定视野，结构决定表达，训练过程决定模型最终能走到哪里。",
            "params": {"emotion": "happy", "emo_alpha": 0.35, "speed": 1.14, "temperature": 0.82},
        },
        {
            "id": "official_sad_reflection",
            "engine_id": "indextts-v2",
            "voice_id": official_sad,
            "text": "当模型判断错误时，它并不是失败了，而是在告诉我们，世界还有一部分没有被它真正理解。",
            "params": {"emotion": "sad", "emo_alpha": 0.65, "speed": 0.92, "temperature": 0.76},
        },
        {
            "id": "design_documentary_omnivoice",
            "engine_id": "omnivoice",
            "voice_id": documentary,
            "text": "从第一台感知机到今天的大模型，智能的故事一直在参数、数据和算力之间展开。",
            "params": {"language": "zh", "speed": 1.0},
        },
        {
            "id": "design_suspense_omnivoice",
            "engine_id": "omnivoice",
            "voice_id": suspense,
            "text": "屏幕忽然暗了下去，日志里只剩下一行字：模型正在自己修改下一步计划。",
            "params": {"language": "zh", "speed": 0.95},
        },
    ]


async def wait_task(ac: AsyncClient, task_id: str, timeout: int = 900) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        task = (await ac.get(f"/api/tasks/{task_id}")).json()
        if task["status"] in {"success", "failed", "cancelled"}:
            return task
        await asyncio.sleep(2)
    raise TimeoutError(task_id)


async def main() -> None:
    cases = build_cases()
    missing = [case["id"] for case in cases if not case.get("voice_id")]
    if missing:
        raise RuntimeError(f"Missing required voice assets: {', '.join(missing)}")

    results = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for engine_id in sorted({case["engine_id"] for case in cases}):
            resp = await ac.post(f"/api/engines/{engine_id}/start")
            resp.raise_for_status()
            print("start", engine_id, resp.json()["state"]["status"], flush=True)

        for case in cases:
            body = {
                "text": case["text"],
                "engine_id": case["engine_id"],
                "voice_id": case["voice_id"],
                "output_format": "wav",
                "language": case["params"].get("language", "zh"),
                "emotion_mode": "follow_reference",
                "emotion": case["params"].get("emotion"),
                "emo_alpha": case["params"].get("emo_alpha", 0.6),
                "speed": case["params"].get("speed", 1.0),
                "temperature": case["params"].get("temperature", 0.8),
                "top_p": 0.8,
                "top_k": 30,
                "repetition_penalty": 10.0,
                "max_mel_tokens": 1500,
                "max_text_tokens_per_segment": 120,
                "interval_silence": 200,
                "segment_overlap_ms": 50,
                "diffusion_steps": 25,
                "cfg_rate": 0.7,
            }
            print("submit", case["id"], flush=True)
            submitted = await ac.post("/api/generate", json=body)
            submitted.raise_for_status()
            task = await wait_task(ac, submitted.json()["task_id"])
            row = {"id": case["id"], "task": task}
            if task["status"] == "success":
                history = (await ac.get("/api/history")).json()
                item = next((h for h in history if h["task_id"] == task["task_id"]), None)
                row["history"] = item
                if item and item.get("output_path"):
                    row["quality"] = _quality(item["output_path"])
            results.append(row)
            print("done", case["id"], task["status"], row.get("quality", task.get("error_message")), flush=True)

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
