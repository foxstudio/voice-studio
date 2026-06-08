#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_API = "http://127.0.0.1:8000/api"


@dataclass
class Candidate:
    voice_id: str
    name: str
    file_id: str
    tags: list[str]
    reference_text: str
    quality_notes: str


def request_json(method: str, url: str, data: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def request_audio(url: str, timeout: int) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        ext = mimetypes.guess_extension(content_type) or ".wav"
        if ext == ".mpga":
            ext = ".mp3"
        return resp.read(), ext


def post_multipart(url: str, fields: dict[str, str], file_field: str, filename: str, content: bytes, timeout: int) -> Any:
    boundary = f"----VoiceStudioBoundary{int(time.time() * 1000)}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    content_type = mimetypes.guess_type(filename)[0] or "audio/wav"
    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
    )
    chunks.append(content)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(url, data=b"".join(chunks), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def candidates_from_voices(
    voices: list[dict[str, Any]],
    voice_ids: set[str],
    overwrite: bool,
    limit: int | None,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for voice in voices:
        voice_id = str(voice.get("voice_id") or "")
        if voice_ids and voice_id not in voice_ids:
            continue
        reference_audio_ids = voice.get("reference_audio_ids") or []
        if not reference_audio_ids:
            continue
        reference_text = str(voice.get("reference_text") or "").strip()
        if reference_text and not overwrite:
            continue
        candidates.append(
            Candidate(
                voice_id=voice_id,
                name=str(voice.get("name") or voice_id),
                file_id=str(reference_audio_ids[0]),
                tags=list(voice.get("tags") or []),
                reference_text=reference_text,
                quality_notes=str(voice.get("quality_notes") or ""),
            )
        )
        if limit is not None and len(candidates) >= limit:
            break
    return candidates


def updated_notes(old_notes: str, transcription_id: str, engine_id: str) -> str:
    note = f"ASR 回填 reference_text，需人工复核；transcription_id={transcription_id}；engine={engine_id}"
    if not old_notes.strip():
        return note
    if note in old_notes:
        return old_notes
    return f"{old_notes.rstrip()}\n{note}"


def apply_candidate(args: argparse.Namespace, candidate: Candidate) -> tuple[str, str]:
    audio_url = api_url(args.api, f"/voices/{candidate.voice_id}/audio/{candidate.file_id}")
    audio, ext = request_audio(audio_url, args.timeout)
    filename = f"{candidate.voice_id}-{candidate.file_id}{ext if ext in {'.wav', '.mp3'} else '.wav'}"
    record = post_multipart(
        api_url(args.api, "/asr/transcribe"),
        {"language": args.language, "engine_id": args.engine},
        "file",
        filename,
        audio,
        args.timeout,
    )
    text = " ".join(str(record.get("text") or "").split()).strip()
    if not text:
        return "skipped", "ASR returned empty text"

    tags = list(candidate.tags)
    if args.review_tag and args.review_tag not in tags:
        tags.append(args.review_tag)

    payload: dict[str, Any] = {
        "reference_text": text,
        "quality_status": "needs_review",
        "quality_notes": updated_notes(candidate.quality_notes, str(record.get("transcription_id") or ""), args.engine),
    }
    if args.review_tag:
        payload["tags"] = tags

    request_json("PATCH", api_url(args.api, f"/voices/{candidate.voice_id}"), payload, args.timeout)
    return "updated", text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill VoiceAsset.reference_text from reference audio with ASR.")
    parser.add_argument("--api", default=DEFAULT_API, help=f"Voice Studio API base URL, default: {DEFAULT_API}")
    parser.add_argument("--engine", default="qwen3-asr-mlx", choices=["qwen3-asr-mlx", "mimo-v2.5-asr"])
    parser.add_argument("--language", default="zh", choices=["auto", "zh", "en"])
    parser.add_argument("--voice-id", action="append", default=[], help="Only process the given voice id. Can be repeated.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of candidates to inspect or apply.")
    parser.add_argument("--overwrite", action="store_true", help="Also process voices that already have reference_text.")
    parser.add_argument("--apply", action="store_true", help="Run ASR and patch matching voices. Default is dry-run only.")
    parser.add_argument("--review-tag", default="ASR待复核", help="Tag to add after ASR backfill. Empty string disables tagging.")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        voices = request_json("GET", api_url(args.api, "/voices"), timeout=args.timeout)
    except urllib.error.URLError as exc:
        print(f"API unavailable: {exc}", file=sys.stderr)
        return 2

    candidates = candidates_from_voices(voices, set(args.voice_id), args.overwrite, args.limit)
    print(f"Found {len(candidates)} candidate voice(s).")
    for candidate in candidates:
        tags = f" tags={','.join(candidate.tags)}" if candidate.tags else ""
        existing = " overwrite" if candidate.reference_text else ""
        print(f"- {candidate.voice_id} {candidate.name}{tags}{existing}")

    if not args.apply:
        print("Dry-run only. Add --apply to transcribe and update reference_text.")
        return 0

    updated = 0
    skipped = 0
    for candidate in candidates:
        print(f"Processing {candidate.voice_id} {candidate.name} ...")
        try:
            status, detail = apply_candidate(args, candidate)
        except Exception as exc:
            skipped += 1
            print(f"  failed: {exc}")
            continue
        if status == "updated":
            updated += 1
            print(f"  updated: {detail[:80]}")
        else:
            skipped += 1
            print(f"  skipped: {detail}")

    print(f"Done. updated={updated}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
