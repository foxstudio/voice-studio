#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.video_localization.subtitle_evaluation import evaluate_srt_pair  # noqa: E402
from app.services import audio_tools  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare generated subtitles with a human reference SRT.")
    parser.add_argument("predicted", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--tolerance-ms", type=int, default=250)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    audio_duration_ms = None
    if args.audio:
        audio_duration_ms = int(audio_tools.probe_audio(args.audio).get("duration_ms") or 0)
    report = evaluate_srt_pair(
        args.predicted.read_text(encoding="utf-8-sig"),
        args.reference.read_text(encoding="utf-8-sig"),
        audio_duration_ms=audio_duration_ms,
        boundary_tolerance_ms=args.tolerance_ms,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
