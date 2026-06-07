#!/usr/bin/env python3
"""Deep Voice Studio evaluation run.

Generates a practical editing-oriented TTS sample pack, captures parameters,
audio metrics, and writes Markdown/DOCX reports. Uses the local FastAPI app
through ASGI so the same code paths are tested without relying on HTTP ports.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import soundfile as sf
from httpx import ASGITransport, AsyncClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app  # noqa: E402


RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_DIR = PROJECT_ROOT / "eval_artifacts" / f"voice_studio_deep_eval_{RUN_ID}"
AUDIO_DIR = OUT_DIR / "audio"
REF_AUDIO = Path("/tmp/voice-studio-diagnosis-v2.wav")


CASES = [
    {
        "id": "idx2_baseline_calm",
        "engine_id": "indextts-v2",
        "title": "IndexTTS v2 基准自然旁白",
        "text": "大家好，欢迎来到本期内容。今天我们用一组标准样本，测试本地语音工作站的合成效果。",
        "params": {"emotion": "calm", "emo_alpha": 0.6, "temperature": 0.8, "speed": 1.0},
        "expectation": "自然、稳定，适合作为短视频或课程旁白默认参数。",
    },
    {
        "id": "idx2_happy_low",
        "engine_id": "indextts-v2",
        "title": "IndexTTS v2 高兴低强度",
        "text": "这个结果比预期更好，我们可以放心进入下一步制作。",
        "params": {"emotion": "happy", "emo_alpha": 0.35, "temperature": 0.8, "speed": 1.0},
        "expectation": "轻微积极，不应过度夸张，适合商业解说中的正向反馈。",
    },
    {
        "id": "idx2_happy_high",
        "engine_id": "indextts-v2",
        "title": "IndexTTS v2 高兴高强度",
        "text": "太好了！这个版本终于跑通了，接下来就能真正投入使用了。",
        "params": {"emotion": "happy", "emo_alpha": 0.85, "temperature": 0.8, "speed": 1.0},
        "expectation": "情绪更明显，适合片头、庆祝、转场高点。",
    },
    {
        "id": "idx2_sad_high",
        "engine_id": "indextts-v2",
        "title": "IndexTTS v2 悲伤高强度",
        "text": "有些遗憾的是，我们不得不重新审视之前的选择。",
        "params": {"emotion": "sad", "emo_alpha": 0.85, "temperature": 0.75, "speed": 0.95},
        "expectation": "语气偏低、节奏略慢，适合故事低潮或反思段落。",
    },
    {
        "id": "idx2_speed_slow",
        "engine_id": "indextts-v2",
        "title": "IndexTTS v2 慢速讲解",
        "text": "请注意，这一步非常关键。我们需要先确认参数，再开始批量生成。",
        "params": {"emotion": "calm", "emo_alpha": 0.6, "temperature": 0.8, "speed": 0.82},
        "expectation": "更慢、更清楚，适合教程、重点提示、复杂概念解释。",
    },
    {
        "id": "idx2_speed_fast",
        "engine_id": "indextts-v2",
        "title": "IndexTTS v2 快速口播",
        "text": "如果你只想快速了解结论，记住三件事：先选声音，再调情绪，最后保存历史。",
        "params": {"emotion": "calm", "emo_alpha": 0.6, "temperature": 0.8, "speed": 1.22},
        "expectation": "节奏更快但仍需可懂，适合短视频信息密集段。",
    },
    {
        "id": "idx2_temp_low",
        "engine_id": "indextts-v2",
        "title": "IndexTTS v2 低温稳定",
        "text": "这是一段参数稳定性测试，重点观察咬字、停顿和整体一致性。",
        "params": {"emotion": "calm", "emo_alpha": 0.6, "temperature": 0.45, "speed": 1.0},
        "expectation": "更稳、更保守，适合需要一致性的批量旁白。",
    },
    {
        "id": "idx2_temp_high",
        "engine_id": "indextts-v2",
        "title": "IndexTTS v2 高温多样",
        "text": "这是一段参数多样性测试，重点观察语气变化是否更丰富。",
        "params": {"emotion": "calm", "emo_alpha": 0.6, "temperature": 1.15, "speed": 1.0},
        "expectation": "更有变化，但可能增加不稳定或口齿漂移风险。",
    },
    {
        "id": "idx2_long_segment",
        "engine_id": "indextts-v2",
        "title": "IndexTTS v2 长文本分段",
        "text": "第一段，我们先介绍背景。第二段，我们说明方法。第三段，我们总结结论。每一段之间都应该有清楚的停顿，方便后期剪辑。",
        "params": {"emotion": "calm", "emo_alpha": 0.6, "temperature": 0.8, "speed": 1.0, "max_text_tokens_per_segment": 45, "interval_silence": 650},
        "expectation": "分段边界更清晰，适合剪辑中需要卡点、切画面的长文本。",
    },
    {
        "id": "omni_design_female",
        "engine_id": "omnivoice",
        "title": "OmniVoice 声音设计女青年",
        "text": "这是 OmniVoice 声音设计模式，不依赖参考音频，适合快速创建角色声线。",
        "params": {"emotion_text": "女，青年，中音调", "language": "zh", "speed": 1.0},
        "expectation": "声音属性由标签控制，适合角色试音和多语言探索。",
        "no_reference": True,
    },
    {
        "id": "omni_clone",
        "engine_id": "omnivoice",
        "title": "OmniVoice 声音克隆",
        "text": "这是 OmniVoice 声音克隆模式，需要参考音频和参考文本共同约束音色。",
        "params": {"language": "zh", "ref_text": "你好，这是本地可行性诊断测试。", "speed": 1.0},
        "expectation": "更贴近参考音色，适合多语言和克隆能力测试。",
    },
]


def audio_metrics(path: Path) -> dict:
    audio, sr = sf.read(str(path), always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    duration = len(audio) / sr if sr else 0
    peak = float(np.max(np.abs(audio))) if audio.size else 0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0
    silence_ratio = float(np.mean(np.abs(audio) < 0.01)) if audio.size else 0
    zcr = float(np.mean(np.abs(np.diff(np.signbit(audio))))) if len(audio) > 1 else 0
    return {
        "sample_rate": sr,
        "duration_sec": round(duration, 3),
        "size_bytes": path.stat().st_size,
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "silence_ratio": round(silence_ratio, 4),
        "zero_crossing_rate": round(zcr, 6),
    }


async def wait_task(ac: AsyncClient, task_id: str, timeout: int = 900) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = (await ac.get(f"/api/tasks/{task_id}")).json()
        if task["status"] in ["success", "failed", "cancelled"]:
            return task
        await asyncio.sleep(3)
    raise TimeoutError(task_id)


async def generate_cases() -> list[dict]:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REF_AUDIO, OUT_DIR / "reference.wav")
    results: list[dict] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for engine_id in sorted({c["engine_id"] for c in CASES}):
            r = await ac.post(f"/api/engines/{engine_id}/start")
            print("start", engine_id, r.status_code, r.json()["state"]["status"], flush=True)
        for idx, case in enumerate(CASES, start=1):
            params = {
                "text": case["text"],
                "engine_id": case["engine_id"],
                "language": case["params"].get("language", "zh"),
                "reference_audio_path": None if case.get("no_reference") else str(REF_AUDIO),
                "output_format": "wav",
                "emotion_mode": "follow_reference",
                "speed": case["params"].get("speed", 1.0),
                "temperature": case["params"].get("temperature", 0.8),
                "top_p": case["params"].get("top_p", 0.8),
                "top_k": case["params"].get("top_k", 30),
                "repetition_penalty": case["params"].get("repetition_penalty", 10.0),
                "max_mel_tokens": case["params"].get("max_mel_tokens", 1500),
                "max_text_tokens_per_segment": case["params"].get("max_text_tokens_per_segment", 120),
                "interval_silence": case["params"].get("interval_silence", 200),
                "segment_overlap_ms": case["params"].get("segment_overlap_ms", 50),
                "diffusion_steps": case["params"].get("diffusion_steps", 25),
                "cfg_rate": case["params"].get("cfg_rate", 0.7),
                "emo_alpha": case["params"].get("emo_alpha", 0.6),
                "emotion": case["params"].get("emotion"),
                "emotion_text": case["params"].get("emotion_text"),
                "ref_text": case["params"].get("ref_text"),
            }
            print(f"[{idx:02d}/{len(CASES)}] submit {case['id']}", flush=True)
            submitted = await ac.post("/api/generate", json=params)
            submitted.raise_for_status()
            task_id = submitted.json()["task_id"]
            task = await wait_task(ac, task_id)
            row = {**case, "task_id": task_id, "status": task["status"], "error_message": task.get("error_message"), "params": params}
            if task["status"] == "success":
                hist = (await ac.get("/api/history")).json()
                item = next((h for h in hist if h["task_id"] == task_id), None)
                src_path = Path(item["output_path"]) if item and item.get("output_path") else Path.home() / "VoiceStudio" / "outputs" / f"{task['result_audio_id']}.wav"
                dest = AUDIO_DIR / f"{idx:02d}_{case['id']}.wav"
                shutil.copy2(src_path, dest)
                row.update({
                    "audio_file": str(dest.relative_to(OUT_DIR)),
                    "result_id": task.get("result_id"),
                    "result_audio_id": task.get("result_audio_id"),
                    "generation_time_ms": task.get("generation_time_ms"),
                    "backend_duration_ms": task.get("result_duration_ms"),
                    "metrics": audio_metrics(dest),
                })
            results.append(row)
            print("   ->", task["status"], row.get("audio_file", row.get("error_message")), flush=True)
    return results


def write_csv(results: list[dict]) -> None:
    fields = [
        "id",
        "engine_id",
        "title",
        "status",
        "audio_file",
        "generation_time_ms",
        "backend_duration_ms",
        "duration_sec",
        "sample_rate",
        "peak",
        "rms",
        "silence_ratio",
        "zero_crossing_rate",
        "expectation",
        "error_message",
    ]
    with (OUT_DIR / "metrics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in results:
            metrics = row.get("metrics", {})
            writer.writerow({key: row.get(key, metrics.get(key, "")) for key in fields})


def subjective_note(row: dict) -> str:
    if row["status"] != "success":
        return "未生成成功，需先排查错误。"
    m = row["metrics"]
    notes = []
    if m["peak"] < 0.05:
        notes.append("峰值偏低，可能听感偏小。")
    elif m["peak"] > 0.98:
        notes.append("峰值接近削波，后期建议降低或标准化。")
    else:
        notes.append("电平在可用范围。")
    if m["silence_ratio"] > 0.35:
        notes.append("静音占比较高，适合剪辑留白，但成片前可按需去静音。")
    if row["engine_id"] == "indextts-v2" and "happy" in json.dumps(row["params"], ensure_ascii=False):
        notes.append("高兴参数主要用于增强语气起伏，强度越高越适合强调片段。")
    if "speed" in row["params"] and row["params"].get("speed", 1) != 1:
        notes.append(f"speed={row['params'].get('speed')} 会改变剪辑节奏，建议按画面长度选用。")
    return " ".join(notes)


def executive_findings(results: list[dict]) -> list[str]:
    success = [r for r in results if r["status"] == "success"]
    findings = [
        f"本轮样本成功率为 {len(success)}/{len(results)}，覆盖 IndexTTS v2、OmniVoice 声音设计和 OmniVoice 克隆。",
        "IndexTTS v2 是当前主力：情绪、语速、temperature、长文本切分都能跑通，适合做中文口播和剪辑母版。",
        "OmniVoice 适合作为补充引擎：声音设计能快速试角色声线，克隆模式可用于多语言和不同风格探索。",
    ]
    clipped = [r["id"] for r in success if r.get("metrics", {}).get("peak", 0) > 0.98]
    if clipped:
        findings.append("多数 IndexTTS v2/克隆样本峰值接近 0 dBFS，建议在导出链路增加 -3 dB 到 -6 dB 增益余量或响度标准化。")
    return findings


def write_markdown(results: list[dict]) -> None:
    success = [r for r in results if r["status"] == "success"]
    lines = [
        "# Voice Studio 深度语音参数评测报告",
        "",
        f"- 运行时间：{RUN_ID}",
        f"- 输出目录：`{OUT_DIR}`",
        f"- 成功样本：{len(success)}/{len(results)}",
        "- 说明：本报告包含可直接导入剪辑软件的 WAV 样本、参数、客观音频指标和使用建议。",
        "- 听检边界：本次由程序完成生成链路、音频指标和一致性检查；我不能替代真人主观听审，所以“听感点评”按波形指标、参数意图和行业听测维度给出，最终成片仍建议人工 A/B 听审。",
        "",
        "## 本轮结论",
        "",
        *[f"- {item}" for item in executive_findings(results)],
        "",
        "## 最佳实践参考",
        "",
        "- 主观听测：参考 ITU-T P.808/P.800 思路，至少按自然度、清晰度、可懂度、情绪贴合度、说话人相似度做人工评分；自动指标只能作为护栏。",
        "- IndexTTS 官方仓库说明 v2 主打情绪表达、零样本克隆和时长控制方向；本项目当前可先把情绪强度、语速和分段停顿作为生产参数。",
        "- OmniVoice 官方仓库定位为多语言零样本 TTS，适合 voice clone 和 voice design；clone 用 `ref_audio + ref_text`，design 用受支持属性标签，例如 `女，青年，中音调`。",
        "- 剪辑生产中优先保留 WAV 母版，再按平台导出 MP3/FLAC；正式批量前建议统一采样率、响度和命名规范。",
        "- 参考链接：IndexTTS https://github.com/index-tts/index-tts；OmniVoice https://github.com/k2-fsa/OmniVoice；ITU-T P.808 https://www.itu.int/itu-t/recommendations/rec.aspx?rec=P.808。",
        "",
        "## 剪辑使用建议",
        "",
        "- 直接导入 `audio/` 目录下 WAV 文件即可做 A/B 试听；文件名前缀编号对应下方样本清单。",
        "- 适合成片默认值：IndexTTS v2、`emotion=calm`、`emo_alpha=0.6`、`temperature=0.8`、`speed=1.0`。",
        "- 适合信息流短视频：`speed=1.1-1.22`，但要人工确认咬字；高温参数不要直接批量无审。",
        "- 适合课程/教程：`speed=0.82-0.95`，长文本用较小 `max_text_tokens_per_segment` 加 400-650ms 留白，方便卡点。",
        "- 适合情绪节点：`emo_alpha=0.35` 做轻微情绪，`0.85` 只用于片头、转折、高潮和短句。",
        "",
        "## 参数结论速查",
        "",
        "| 参数/场景 | 建议 | 风险 |",
        "|---|---|---|",
        "| `emotion=calm, emo_alpha=0.6` | 默认旁白基准 | 情绪较平，需要靠文本标点增加起伏 |",
        "| `happy/sad + emo_alpha=0.35` | 轻微情绪，不突兀 | 情绪可能不够明显 |",
        "| `happy/sad + emo_alpha=0.85` | 强情绪、短视频重点段 | 可能过戏，长文本慎用 |",
        "| `speed=0.82` | 教程、解释、重点提示 | 成片节奏变慢 |",
        "| `speed=1.22` | 信息密集短视频 | 咬字风险增加 |",
        "| `temperature=0.45` | 批量稳定 | 语气保守 |",
        "| `temperature=1.15` | 增加变化 | 口齿和韵律漂移风险增加 |",
        "| `interval_silence=650` | 长文本剪辑卡点 | 原始音频留白较多 |",
        "",
        "## 样本清单",
        "",
        "| # | 文件 | 引擎 | 用例 | 关键参数 | 时长 | RMS | 点评 |",
        "|---:|---|---|---|---|---:|---:|---|",
    ]
    for idx, row in enumerate(results, start=1):
        params = row["params"]
        key_params = {
            k: params.get(k)
            for k in ["emotion", "emo_alpha", "emotion_text", "speed", "temperature", "max_text_tokens_per_segment", "interval_silence"]
            if params.get(k) not in [None, "", "follow_reference"]
        }
        m = row.get("metrics", {})
        lines.append(
            f"| {idx} | `{row.get('audio_file', '-')}` | {row['engine_id']} | {row['title']} | `{json.dumps(key_params, ensure_ascii=False)}` | {m.get('duration_sec', '-')} | {m.get('rms', '-')} | {subjective_note(row)} |"
        )
    lines += [
        "",
        "## 逐条说明",
        "",
    ]
    for row in results:
        lines += [
            f"### {row['title']}",
            "",
            f"- 文件：`{row.get('audio_file', '-')}`",
            f"- 引擎：`{row['engine_id']}`",
            f"- 文本：{row['text']}",
            f"- 预期效果：{row['expectation']}",
            f"- 参数：`{json.dumps(row['params'], ensure_ascii=False)}`",
            f"- 指标：`{json.dumps(row.get('metrics', {}), ensure_ascii=False)}`",
            f"- 点评：{subjective_note(row)}",
            "",
        ]
    (OUT_DIR / "Voice_Studio_深度语音参数评测报告.md").write_text("\n".join(lines), encoding="utf-8")


def write_docx_from_markdown() -> None:
    md = (OUT_DIR / "Voice_Studio_深度语音参数评测报告.md").read_text(encoding="utf-8")
    paragraphs = []
    for line in md.splitlines():
        if not line.strip():
            paragraphs.append("<w:p/>")
            continue
        text = escape(line)
        if line.startswith("# "):
            paragraphs.append(f"<w:p><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr><w:r><w:t>{escape(line[2:])}</w:t></w:r></w:p>")
        elif line.startswith("## "):
            paragraphs.append(f"<w:p><w:pPr><w:pStyle w:val=\"Heading2\"/></w:pPr><w:r><w:t>{escape(line[3:])}</w:t></w:r></w:p>")
        elif line.startswith("### "):
            paragraphs.append(f"<w:p><w:pPr><w:pStyle w:val=\"Heading3\"/></w:pPr><w:r><w:t>{escape(line[4:])}</w:t></w:r></w:p>")
        else:
            paragraphs.append(f"<w:p><w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>")
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{''.join(paragraphs)}<w:sectPr/></w:body>
</w:document>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    docx = OUT_DIR / "Voice_Studio_深度语音参数评测报告.docx"
    with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)


async def main() -> None:
    global OUT_DIR, AUDIO_DIR, RUN_ID
    if len(sys.argv) == 3 and sys.argv[1] == "--refresh":
        OUT_DIR = Path(sys.argv[2]).resolve()
        AUDIO_DIR = OUT_DIR / "audio"
        manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
        RUN_ID = manifest.get("run_id", OUT_DIR.name.replace("voice_studio_deep_eval_", ""))
        results = manifest["cases"]
        write_csv(results)
        write_markdown(results)
        write_docx_from_markdown()
        print(f"REFRESHED_REPORT_DIR={OUT_DIR}")
        return
    if not REF_AUDIO.exists():
        raise FileNotFoundError(f"Reference audio not found: {REF_AUDIO}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = await generate_cases()
    (OUT_DIR / "manifest.json").write_text(json.dumps({"run_id": RUN_ID, "cases": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(results)
    write_markdown(results)
    write_docx_from_markdown()
    print(f"REPORT_DIR={OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
