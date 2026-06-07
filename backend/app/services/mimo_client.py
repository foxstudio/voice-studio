from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MIMO_PRESET_VOICES = [
    {"voice_id": "mimo_default", "label": "MiMo 默认", "language": "auto", "gender": ""},
    {"voice_id": "冰糖", "label": "冰糖 · 中文女声", "language": "zh", "gender": "female"},
    {"voice_id": "茉莉", "label": "茉莉 · 中文女声", "language": "zh", "gender": "female"},
    {"voice_id": "苏打", "label": "苏打 · 中文男声", "language": "zh", "gender": "male"},
    {"voice_id": "白桦", "label": "白桦 · 中文男声", "language": "zh", "gender": "male"},
    {"voice_id": "Mia", "label": "Mia · English Female", "language": "en", "gender": "female"},
    {"voice_id": "Chloe", "label": "Chloe · English Female", "language": "en", "gender": "female"},
    {"voice_id": "Milo", "label": "Milo · English Male", "language": "en", "gender": "male"},
    {"voice_id": "Dean", "label": "Dean · English Male", "language": "en", "gender": "male"},
]
MIMO_PRESET_VOICE_IDS = {item["voice_id"] for item in MIMO_PRESET_VOICES}
MIMO_TTS_MODELS = {"mimo-v2.5-tts", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"}
_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg"}
_MAX_AUDIO_DATA_URL_BYTES = 10 * 1024 * 1024


def _audio_data(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices") or []
    for choice in choices:
        message = choice.get("message") or {}
        audio = message.get("audio") or {}
        data = audio.get("data") or audio.get("content")
        if isinstance(data, str):
            return data
        if isinstance(message.get("content"), list):
            for item in message["content"]:
                if isinstance(item, dict):
                    audio = item.get("audio") or {}
                    data = audio.get("data")
                    if isinstance(data, str):
                        return data
    return None


def _message_text(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices") or []
    for choice in choices:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return "".join(parts)
    return None


def audio_file_data_url(path: str, *, error_prefix: str = "MIMO_VOICECLONE") -> str:
    audio_path = Path(path)
    if not audio_path.exists():
        raise ValueError(f"{error_prefix}_AUDIO_NOT_FOUND")
    mime_type = _AUDIO_MIME_TYPES.get(audio_path.suffix.lower())
    if not mime_type:
        raise ValueError(f"{error_prefix}_AUDIO_FORMAT_UNSUPPORTED")
    data = audio_path.read_bytes()
    if len(data) > _MAX_AUDIO_DATA_URL_BYTES:
        raise ValueError(f"{error_prefix}_AUDIO_TOO_LARGE")
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('utf-8')}"


def build_tts_payload(
    *,
    model: str,
    text: str,
    audio_format: str = "wav",
    voice: str | None = None,
    style_instruction: str | None = None,
    voice_design_prompt: str | None = None,
    reference_audio_path: str | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
) -> dict[str, Any]:
    if model not in MIMO_TTS_MODELS:
        raise ValueError("MIMO_TTS_MODEL_UNSUPPORTED")
    messages: list[dict[str, str]] = []
    audio: dict[str, str] = {"format": audio_format}

    if model == "mimo-v2.5-tts":
        resolved_voice = voice or "mimo_default"
        if resolved_voice not in MIMO_PRESET_VOICE_IDS:
            raise ValueError("MIMO_PRESET_VOICE_UNSUPPORTED")
        if style_instruction:
            messages.append({"role": "user", "content": style_instruction})
        audio["voice"] = resolved_voice
    elif model == "mimo-v2.5-tts-voicedesign":
        if not voice_design_prompt or not voice_design_prompt.strip():
            raise ValueError("MIMO_VOICE_DESIGN_PROMPT_REQUIRED")
        messages.append({"role": "user", "content": voice_design_prompt.strip()})
    else:
        if not reference_audio_path:
            raise ValueError("MIMO_VOICECLONE_REFERENCE_AUDIO_REQUIRED")
        if style_instruction:
            messages.append({"role": "user", "content": style_instruction})
        audio["voice"] = audio_file_data_url(reference_audio_path)

    messages.append({"role": "assistant", "content": text})
    body: dict[str, Any] = {"model": model, "messages": messages, "audio": audio}
    if temperature is not None:
        body["temperature"] = temperature
    if top_p is not None:
        body["top_p"] = top_p
    return body


def build_asr_payload(audio_path: str, *, language: str = "auto", stream: bool = False) -> dict[str, Any]:
    if language not in {"auto", "zh", "en"}:
        raise ValueError("MIMO_ASR_LANGUAGE_UNSUPPORTED")
    body: dict[str, Any] = {
        "model": "mimo-v2.5-asr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio_file_data_url(audio_path, error_prefix="MIMO_ASR")},
                    }
                ],
            }
        ],
        "asr_options": {"language": language},
    }
    if stream:
        body["stream"] = True
    return body


def _post_chat_completion(*, base_url: str, api_key: str, body: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"MiMo API 请求失败：HTTP {exc.code} {message}") from exc


def generate_tts(
    *,
    base_url: str,
    api_key: str,
    text: str,
    output_path: str,
    voice: str = "mimo_default",
    instruction: str | None = None,
    model: str = "mimo-v2.5-tts",
    voice_design_prompt: str | None = None,
    reference_audio_path: str | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    audio_format: str = "wav",
    timeout: int = 120,
) -> dict[str, Any]:
    body = build_tts_payload(
        model=model,
        text=text,
        audio_format=audio_format,
        voice=voice,
        style_instruction=instruction,
        voice_design_prompt=voice_design_prompt,
        reference_audio_path=reference_audio_path,
        temperature=temperature,
        top_p=top_p,
    )
    payload = _post_chat_completion(base_url=base_url, api_key=api_key, body=body, timeout=timeout)
    data = _audio_data(payload)
    if not data:
        raise RuntimeError("MiMo API 未返回可识别的音频数据")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(data))
    return {"output_path": str(path)}


def transcribe_audio(
    *,
    base_url: str,
    api_key: str,
    audio_path: str,
    language: str = "auto",
    timeout: int = 120,
) -> dict[str, Any]:
    body = build_asr_payload(audio_path, language=language)
    payload = _post_chat_completion(base_url=base_url, api_key=api_key, body=body, timeout=timeout)
    text = _message_text(payload)
    if not text:
        raise RuntimeError("MiMo ASR 未返回可识别的文本内容")
    usage = payload.get("usage") or {}
    return {
        "text": text,
        "usage_seconds": usage.get("seconds"),
        "provider_response_id": payload.get("id"),
        "raw_response": payload,
    }
