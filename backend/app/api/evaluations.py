from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.models.exceptions import AppException

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVAL_ROOT = PROJECT_ROOT / "eval_artifacts"


def _latest_dir() -> Path:
    if not EVAL_ROOT.exists():
        raise AppException(404, "EVALUATION_NOT_FOUND", "No evaluation artifacts found")
    candidates = sorted(
        [p for p in EVAL_ROOT.glob("voice_studio_deep_eval_*") if p.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        raise AppException(404, "EVALUATION_NOT_FOUND", "No evaluation artifacts found")
    return candidates[0]


def _safe_file(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()) or not target.exists():
        raise AppException(404, "EVALUATION_FILE_NOT_FOUND", "Evaluation file not found")
    return target


@router.get("/latest")
async def latest_evaluation():
    base = _latest_dir()
    manifest_path = _safe_file(base, "manifest.json")
    metrics_path = _safe_file(base, "metrics.csv")
    report_path = _safe_file(base, "Voice_Studio_深度语音参数评测报告.md")
    docx_path = _safe_file(base, "Voice_Studio_深度语音参数评测报告.docx")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = manifest.get("cases", [])
    success = [c for c in cases if c.get("status") == "success"]
    audio_samples = []
    for case in cases:
        audio_file = case.get("audio_file")
        if not audio_file:
            continue
        filename = Path(audio_file).name
        audio_samples.append(
            {
                "id": case.get("id"),
                "title": case.get("title"),
                "engine_id": case.get("engine_id"),
                "text": case.get("text"),
                "expectation": case.get("expectation"),
                "status": case.get("status"),
                "params": case.get("params", {}),
                "metrics": case.get("metrics", {}),
                "audio_file": audio_file,
                "audio_url": f"/api/evaluations/latest/audio/{filename}",
            }
        )
    return {
        "run_id": manifest.get("run_id", base.name.replace("voice_studio_deep_eval_", "")),
        "report_dir": str(base),
        "success_count": len(success),
        "total_count": len(cases),
        "report_markdown": report_path.read_text(encoding="utf-8"),
        "files": {
            "markdown": "/api/evaluations/latest/files/markdown",
            "docx": "/api/evaluations/latest/files/docx",
            "metrics": "/api/evaluations/latest/files/metrics",
            "manifest": "/api/evaluations/latest/files/manifest",
        },
        "audio_samples": audio_samples,
        "file_sizes": {
            "markdown": report_path.stat().st_size,
            "docx": docx_path.stat().st_size,
            "metrics": metrics_path.stat().st_size,
            "manifest": manifest_path.stat().st_size,
        },
    }


@router.get("/latest/files/{kind}")
async def evaluation_file(kind: Literal["markdown", "docx", "metrics", "manifest"]):
    base = _latest_dir()
    names = {
        "markdown": "Voice_Studio_深度语音参数评测报告.md",
        "docx": "Voice_Studio_深度语音参数评测报告.docx",
        "metrics": "metrics.csv",
        "manifest": "manifest.json",
    }
    path = _safe_file(base, names[kind])
    return FileResponse(path)


@router.get("/latest/audio/{filename}")
async def evaluation_audio(filename: str):
    if Path(filename).name != filename:
        raise AppException(400, "INVALID_AUDIO_NAME", "Invalid audio filename")
    path = _safe_file(_latest_dir() / "audio", filename)
    return FileResponse(path)
