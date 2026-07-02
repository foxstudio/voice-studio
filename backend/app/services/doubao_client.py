from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://openspeech.bytedance.com"
DEFAULT_TTS_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_ICL_RESOURCE_ID = "seed-icl-2.0"
DOUBAO_TTS_PRESET_SPEAKERS = [
    {"voice_id": "zh_female_xiaohe_uranus_bigtts", "label": "小何 2.0 · 女声", "language": "zh", "gender": "female"},
    {"voice_id": "zh_female_vv_uranus_bigtts", "label": "Vivi 2.0 · 女声", "language": "zh", "gender": "female"},
    {"voice_id": "zh_male_m191_uranus_bigtts", "label": "云舟 2.0 · 男声", "language": "zh", "gender": "male"},
    {"voice_id": "zh_male_taocheng_uranus_bigtts", "label": "小天 2.0 · 男声", "language": "zh", "gender": "male"},
    {"voice_id": "zh_female_peiqi_uranus_bigtts", "label": "佩奇猪 2.0 · 角色音", "language": "zh", "gender": "female"},
    {"voice_id": "zh_male_ruyayichen_uranus_bigtts", "label": "儒雅逸辰 2.0 · 男声", "language": "zh", "gender": "male"},
]


class DoubaoAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, logid: str | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.logid = logid
        self.body = body


@dataclass(frozen=True)
class DoubaoResponse:
    body: dict[str, Any]
    logid: str | None
    request_id: str


@dataclass(frozen=True)
class DoubaoTTSResult:
    output_path: str
    request_id: str
    logid: str | None
    final_code: int | None
    final_message: str | None
    chunk_count: int
    audio_bytes: int


def masked_identifier(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return value[:1] + "***"
    return f"{value[:3]}***{value[-3:]}"


def build_headers(
    *,
    api_key: str,
    resource_id: str | None = None,
    request_id: str | None = None,
    content_type: str = "application/json",
) -> tuple[dict[str, str], str]:
    resolved_request_id = request_id or str(uuid.uuid4())
    headers = {
        "Content-Type": content_type,
        "X-Api-Key": api_key,
        "X-Api-Request-Id": resolved_request_id,
    }
    if resource_id:
        headers["X-Api-Resource-Id"] = resource_id
    return headers, resolved_request_id


def post_json(
    *,
    base_url: str,
    path: str,
    api_key: str,
    body: dict[str, Any],
    resource_id: str | None = None,
    timeout: int = 60,
) -> DoubaoResponse:
    headers, request_id = build_headers(api_key=api_key, resource_id=resource_id)
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            logid = resp.headers.get("X-Tt-Logid")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        logid = exc.headers.get("X-Tt-Logid")
        raise DoubaoAPIError(
            f"Doubao API 请求失败：HTTP {exc.code}",
            status_code=exc.code,
            logid=logid,
            body=raw[:1200],
        ) from exc
    except urllib.error.URLError as exc:
        raise DoubaoAPIError(f"Doubao API 请求失败：{exc.reason}") from exc

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise DoubaoAPIError("Doubao API 返回了无法解析的 JSON", logid=logid, body=raw[:1200]) from exc
    return DoubaoResponse(body=payload, logid=logid, request_id=request_id)


def iter_concatenated_json(raw: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    i = 0
    while i < len(raw):
        while i < len(raw) and raw[i] in " \r\n\t":
            i += 1
        if i >= len(raw):
            break
        item, i = decoder.raw_decode(raw, i)
        if isinstance(item, dict):
            items.append(item)
    return items


def build_tts_payload(
    *,
    text: str,
    speaker: str,
    audio_format: str = "mp3",
    sample_rate: int = 24000,
    speed: float | None = None,
    loudness_rate: int | None = None,
    pitch_rate: int | None = None,
    style_instruction: str | None = None,
    enable_subtitle: bool = False,
    user_id: str = "voice-studio",
) -> dict[str, Any]:
    audio_params: dict[str, Any] = {
        "format": audio_format,
        "sample_rate": sample_rate,
    }
    if speed is not None:
        audio_params["speech_rate"] = max(-50, min(100, int(round((float(speed) - 1.0) * 100))))
    if loudness_rate is not None:
        audio_params["loudness_rate"] = max(-50, min(100, int(loudness_rate)))
    if pitch_rate is not None:
        audio_params["pitch_rate"] = max(-12, min(12, int(pitch_rate)))
    if enable_subtitle:
        audio_params["enable_subtitle"] = True

    req_params: dict[str, Any] = {
        "text": text,
        "speaker": speaker,
        "audio_params": audio_params,
    }
    if style_instruction and style_instruction.strip():
        req_params["additions"] = json.dumps({"context_texts": [style_instruction.strip()]}, ensure_ascii=False)

    return {
        "user": {"uid": user_id},
        "req_params": req_params,
    }


def generate_tts_unidirectional_http(
    *,
    base_url: str,
    api_key: str,
    text: str,
    output_path: str,
    speaker: str,
    resource_id: str = DEFAULT_TTS_RESOURCE_ID,
    audio_format: str = "mp3",
    sample_rate: int = 24000,
    speed: float | None = None,
    style_instruction: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    body = build_tts_payload(
        text=text,
        speaker=speaker,
        audio_format=audio_format,
        sample_rate=sample_rate,
        speed=speed,
        style_instruction=style_instruction,
    )
    headers, request_id = build_headers(api_key=api_key, resource_id=resource_id)
    url = base_url.rstrip("/") + "/api/v3/tts/unidirectional"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            logid = resp.headers.get("X-Tt-Logid")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        logid = exc.headers.get("X-Tt-Logid")
        raise DoubaoAPIError(
            f"Doubao TTS 请求失败：HTTP {exc.code}",
            status_code=exc.code,
            logid=logid,
            body=raw[:1200],
        ) from exc
    except urllib.error.URLError as exc:
        raise DoubaoAPIError(f"Doubao TTS 请求失败：{exc.reason}") from exc

    frames = iter_concatenated_json(raw)
    chunks: list[bytes] = []
    final: dict[str, Any] | None = None
    for frame in frames:
        data = frame.get("data")
        if isinstance(data, str) and data:
            chunks.append(base64.b64decode(data))
        code = frame.get("code")
        if code is not None:
            final = frame
    if final and final.get("code") not in {20000000, "20000000"}:
        raise DoubaoAPIError(
            f"Doubao TTS 返回错误：{final.get('code')} {final.get('message') or ''}".strip(),
            logid=logid,
            body=json.dumps(final, ensure_ascii=False)[:1200],
        )
    if not chunks:
        raise DoubaoAPIError("Doubao TTS 未返回音频数据", logid=logid, body=raw[:1200])

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = b"".join(chunks)
    path.write_bytes(audio)
    result = DoubaoTTSResult(
        output_path=str(path),
        request_id=request_id,
        logid=logid,
        final_code=final.get("code") if final else None,
        final_message=final.get("message") if final else None,
        chunk_count=len(chunks),
        audio_bytes=len(audio),
    )
    return result.__dict__


def build_voice_clone_payload(
    *,
    speaker_id: str,
    audio_path: str,
    custom_speaker_id: str | None = None,
    text: str | None = None,
    language: str = "zh",
    demo_text: str | None = None,
    enable_audio_denoise: bool | None = None,
    disable_volume_normalization: bool | None = None,
) -> dict[str, Any]:
    path = Path(audio_path)
    suffix = path.suffix.lower().lstrip(".") or "wav"
    if suffix == "oga":
        suffix = "ogg"
    audio_data = base64.b64encode(path.read_bytes()).decode("ascii")
    body: dict[str, Any] = {
        "speaker_id": speaker_id,
        "audio": {
            "data": audio_data,
            "format": suffix,
        },
        "language": language,
    }
    if custom_speaker_id:
        body["custom_speaker_id"] = custom_speaker_id
    if text and text.strip():
        body["text"] = text.strip()
    extra_params: dict[str, Any] = {}
    if demo_text and demo_text.strip():
        extra_params["demo_text"] = demo_text.strip()
    if enable_audio_denoise is not None:
        extra_params["enable_audio_denoise"] = bool(enable_audio_denoise)
    if disable_volume_normalization is not None:
        extra_params["disable_volume_normalization"] = bool(disable_volume_normalization)
    if extra_params:
        body["extra_params"] = extra_params
    return body


def train_voice_clone(
    *,
    base_url: str,
    api_key: str,
    speaker_id: str,
    audio_path: str,
    custom_speaker_id: str | None = None,
    text: str | None = None,
    language: str = "zh",
    demo_text: str | None = None,
    resource_id: str = DEFAULT_ICL_RESOURCE_ID,
    enable_audio_denoise: bool | None = None,
    disable_volume_normalization: bool | None = None,
    timeout: int = 120,
) -> DoubaoResponse:
    body = build_voice_clone_payload(
        speaker_id=speaker_id,
        custom_speaker_id=custom_speaker_id,
        audio_path=audio_path,
        text=text,
        language=language,
        demo_text=demo_text,
        enable_audio_denoise=enable_audio_denoise,
        disable_volume_normalization=disable_volume_normalization,
    )
    return post_json(
        base_url=base_url,
        path="/api/v3/tts/voice_clone",
        api_key=api_key,
        resource_id=resource_id,
        body=body,
        timeout=timeout,
    )


def get_voice(
    *,
    base_url: str,
    api_key: str,
    speaker_id: str,
    custom_speaker_id: str | None = None,
    resource_id: str | None = DEFAULT_ICL_RESOURCE_ID,
    timeout: int = 60,
) -> DoubaoResponse:
    body: dict[str, Any] = {"speaker_id": speaker_id}
    if custom_speaker_id:
        body["custom_speaker_id"] = custom_speaker_id
    return post_json(
        base_url=base_url,
        path="/api/v3/tts/get_voice",
        api_key=api_key,
        resource_id=resource_id,
        body=body,
        timeout=timeout,
    )


def summarize_voice_status(response: DoubaoResponse, *, speaker_id: str) -> dict[str, Any]:
    body = response.body
    speaker_status = body.get("speaker_status") or []
    return {
        "speaker_id": masked_identifier(speaker_id),
        "status": body.get("status"),
        "language": body.get("language"),
        "available_training_times": body.get("available_training_times"),
        "model_types": [item.get("model_type") for item in speaker_status if isinstance(item, dict)],
        "has_demo_audio": any(bool(item.get("demo_audio")) for item in speaker_status if isinstance(item, dict)),
        "request_id": response.request_id,
        "logid": response.logid,
    }
