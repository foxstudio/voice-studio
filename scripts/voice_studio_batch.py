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
    if args.ref_audio:
        payload["reference_audio_path"] = args.ref_audio
    if args.ref_text:
        payload["ref_text"] = args.ref_text
    if args.language:
        payload["language"] = args.language
    if args.output_dir:
        payload["output_dir"] = args.output_dir
    if args.format:
        payload["output_format"] = args.format
    parameters = dict(payload.get("parameters") or {})
    parameter_args = {
        "emotion": args.emotion,
        "emotion_text": args.emotion_text,
        "style_instruction": args.style_instruction,
        "voice_design_prompt": args.voice_design_prompt,
        "mimo_voice": args.mimo_voice,
        "speaker_id": args.speaker_id,
        "prompt": args.prompt,
        "speed": args.speed,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_text_tokens_per_segment": args.max_text_tokens_per_segment,
        "interval_silence": args.interval_silence,
        "diffusion_steps": args.diffusion_steps,
        "cfg_rate": args.cfg_rate,
        "nfe_step": args.nfe_step,
        "cfg_strength": args.cfg_strength,
        "target_rms": args.target_rms,
        "cross_fade_duration": args.cross_fade_duration,
        "ref_text": args.ref_text,
    }
    parameters.update({key: value for key, value in parameter_args.items() if value is not None})
    if args.optimize_text_preview:
        parameters["optimize_text_preview"] = True
    if args.remove_silence:
        parameters["remove_silence"] = True
    if parameters:
        payload["parameters"] = parameters
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
    parser.add_argument("--ref-audio", help="本批次默认参考音频路径。")
    parser.add_argument("--ref-text", help="本批次默认参考音频台词。")
    parser.add_argument("--language", default=None, help="默认语言，例如 zh/en/auto。")
    parser.add_argument("--output-dir", help="输出目录，例如 presentation/public/audio。")
    parser.add_argument("--format", default=None, choices=["wav", "mp3", "flac"], help="默认输出格式。")
    parser.add_argument("--emotion", help="IndexTTS 情绪，例如 happy/calm/sad。")
    parser.add_argument("--emotion-text", help="OmniVoice 声音设计指令。")
    parser.add_argument("--style-instruction", help="MiMo 风格指令。")
    parser.add_argument("--voice-design-prompt", help="MiMo VoiceDesign 音色描述。")
    parser.add_argument("--optimize-text-preview", action="store_true", help="开启 MiMo VoiceDesign 文本润色。")
    parser.add_argument("--mimo-voice", help="MiMo preset 官方音色。")
    parser.add_argument("--speaker-id", help="EmotiVoice / CosyVoice SFT 官方说话人 ID。")
    parser.add_argument("--prompt", help="EmotiVoice 情绪提示词。")
    parser.add_argument("--speed", type=float, help="默认语速倍率。")
    parser.add_argument("--temperature", type=float, help="采样温度。")
    parser.add_argument("--top-p", type=float, help="Top-P。")
    parser.add_argument("--top-k", type=int, help="Top-K。")
    parser.add_argument("--max-text-tokens-per-segment", type=int, help="IndexTTS 分段长度。")
    parser.add_argument("--interval-silence", type=int, help="IndexTTS 段间静默 ms。")
    parser.add_argument("--diffusion-steps", type=int, help="IndexTTS 扩散步数。")
    parser.add_argument("--cfg-rate", type=float, help="IndexTTS CFG。")
    parser.add_argument("--nfe-step", type=int, help="F5-TTS 采样步数 NFE。")
    parser.add_argument("--cfg-strength", type=float, help="F5-TTS CFG 引导强度。")
    parser.add_argument("--target-rms", type=float, help="F5-TTS 响度目标 RMS。")
    parser.add_argument("--cross-fade-duration", type=float, help="F5-TTS 分段交叉淡化秒数。")
    parser.add_argument("--remove-silence", action="store_true", help="F5-TTS 生成后移除静音。")
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
