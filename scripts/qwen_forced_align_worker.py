#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from qwen_asr import Qwen3ForcedAligner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--device", default="mps")
    return parser.parse_args()


def choose_device(raw: str) -> str:
    if raw == "cpu":
        return "cpu"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main() -> int:
    args = parse_args()
    device = choose_device(args.device)
    dtype = torch.float16 if device != "cpu" else torch.float32

    try:
        aligner = Qwen3ForcedAligner.from_pretrained(args.checkpoint, dtype=dtype)
        aligner.model.to(device)
        aligner.model.eval()
        aligner.device = torch.device(device)
    except Exception as exc:
        print(json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False), flush=True)
        return 1

    print(
        json.dumps(
            {
                "type": "ready",
                "device": device,
                "checkpoint": args.checkpoint,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            result = aligner.align(
                audio=str(Path(payload["audio_path"])),
                text=str(payload["text"]),
                language=str(payload["language"]),
            )[0]
            items = [
                {
                    "text": item.text,
                    "start_time": item.start_time,
                    "end_time": item.end_time,
                }
                for item in result
            ]
            print(json.dumps({"id": payload["id"], "ok": True, "items": items}, ensure_ascii=False), flush=True)
        except Exception as exc:
            response_id = None
            try:
                response_id = payload.get("id")
            except Exception:
                response_id = None
            print(
                json.dumps(
                    {
                        "id": response_id,
                        "ok": False,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
