#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class ChineseArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        return super().format_help().replace("usage:", "用法：", 1)

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法：", 1)


def request(method: str, url: str, payload: Any | None = None) -> Any:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc


def normalize(raw: Any, args: argparse.Namespace) -> dict[str, Any]:
    payload = {"segments": raw} if isinstance(raw, list) else dict(raw)
    if args.engine:
        payload["engine_id"] = args.engine
    if args.voice:
        payload["voice_id"] = args.voice
    if args.output_dir:
        payload["output_dir"] = args.output_dir
    if args.format:
        payload["output_format"] = args.format
    return payload


def main() -> None:
    parser = ChineseArgumentParser(description="提交 Voice Studio 批量 TTS 任务。", add_help=False)
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示帮助并退出。")
    parser.add_argument("input", help="兼容 audio-segments.json 的输入文件路径。")
    parser.add_argument("--api", default="http://127.0.0.1:8000/api", help="Voice Studio API 基础地址。")
    parser.add_argument("--voice", help="本批次默认使用的 voice_id。")
    parser.add_argument("--engine", default="indextts-v2", help="本批次默认使用的 engine_id。")
    parser.add_argument("--output-dir", help="输出目录，例如 presentation/public/audio。")
    parser.add_argument("--format", default="mp3", choices=["wav", "mp3", "flac"], help="默认输出格式。")
    parser.add_argument("--wait", action="store_true", help="等待批处理完成后再退出。")
    parser.add_argument("--manifest", help="把最终批处理结果写入这个 manifest 文件。")
    parser.add_argument("--poll", type=float, default=2.0, help="轮询间隔，单位秒。")
    args = parser.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    payload = normalize(raw, args)
    batch = request("POST", f"{args.api.rstrip('/')}/batches/generate", payload)
    batch_id = batch["batch_task_id"]
    if args.wait:
        while batch["status"] in {"pending", "queued", "running", "postprocessing"}:
            time.sleep(args.poll)
            batch = request("GET", f"{args.api.rstrip('/')}/batches/{batch_id}")
    if args.manifest:
        Path(args.manifest).write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(batch, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
