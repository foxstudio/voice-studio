from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


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


def generate_tts(
    *,
    base_url: str,
    api_key: str,
    text: str,
    output_path: str,
    voice: str = "mimo_default",
    instruction: str | None = None,
    audio_format: str = "wav",
    timeout: int = 120,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    messages = []
    if instruction:
        messages.append({"role": "user", "content": instruction})
    messages.append({"role": "assistant", "content": text})
    body = {
        "model": "mimo-v2.5-tts",
        "messages": messages,
        "audio": {"voice": voice, "format": audio_format},
    }
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
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"MiMo API 请求失败：HTTP {exc.code} {message}") from exc

    data = _audio_data(payload)
    if not data:
        raise RuntimeError("MiMo API 未返回可识别的音频数据")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(data))
    return {"output_path": str(path)}
