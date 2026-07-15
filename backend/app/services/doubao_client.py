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
DEFAULT_TTS_SAMPLE_RATE = 48000
DEFAULT_TTS_BIT_RATE = 160000
SUPPORTED_TTS_AUDIO_FORMATS = frozenset({"wav", "mp3", "pcm", "ogg_opus"})
DOUBAO_TTS_EXPLICIT_LANGUAGES = {
    "zh-cn": "中文（支持中英混读）",
    "en": "英语",
    "ja": "日语",
    "es-mx": "墨西哥西班牙语",
    "id": "印度尼西亚语",
    "pt-br": "巴西葡萄牙语",
    "pt": "葡萄牙语",
    "ko": "韩语",
    "it": "意大利语",
    "de": "德语",
    "fr": "法语",
    "th": "泰语",
    "vi": "越南语",
    "ru": "俄语",
    "fil": "菲律宾语",
    "ms": "马来语",
    "ar": "阿拉伯语",
    "pl": "波兰语",
    "tr": "土耳其语",
    "sv": "瑞典语",
}
DOUBAO_TTS_EXPLICIT_LANGUAGE_ALIASES = {
    "zh": "zh-cn",
    "cn": "zh-cn",
    "chinese": "zh-cn",
    "en-us": "en",
    "english": "en",
    "ja-jp": "ja",
    "japanese": "ja",
    "es": "es-mx",
    "spanish": "es-mx",
    "pt-pt": "pt",
    "portuguese": "pt",
    "ko-kr": "ko",
    "korean": "ko",
}
DOUBAO_VOICE_CLONE_LANGUAGE_CODES = {
    "zh": 0,
    "zh-cn": 0,
    "cn": 0,
    "chinese": 0,
    "中文": 0,
    "en": 1,
    "en-us": 1,
    "english": 1,
    "英文": 1,
}
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
    subtitle: dict[str, Any] | None = None


def masked_identifier(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return value[:1] + "***"
    return f"{value[:3]}***{value[-3:]}"


def voice_clone_language_code(language: str | int | None) -> int:
    if language is None:
        return 0
    if isinstance(language, int):
        if language in {0, 1}:
            return language
        raise DoubaoAPIError("豆包声音复刻 2.0 只支持中文或英文训练音频")
    value = str(language).strip().lower()
    if not value:
        return 0
    if value.isdigit():
        return voice_clone_language_code(int(value))
    if value in DOUBAO_VOICE_CLONE_LANGUAGE_CODES:
        return DOUBAO_VOICE_CLONE_LANGUAGE_CODES[value]
    raise DoubaoAPIError(f"豆包声音复刻不支持语种：{language}")


def normalize_tts_explicit_language(language: str | None) -> str | None:
    """Return a documented TTS 2.0 language code, or None for auto-detect."""
    value = str(language or "").strip().lower()
    if value in {"", "auto", "automatic", "自动"}:
        return None
    value = DOUBAO_TTS_EXPLICIT_LANGUAGE_ALIASES.get(value, value)
    if value not in DOUBAO_TTS_EXPLICIT_LANGUAGES:
        supported = ", ".join(DOUBAO_TTS_EXPLICIT_LANGUAGES)
        raise DoubaoAPIError(f"豆包 TTS 不支持指定朗读语言：{language}；支持：{supported}")
    return value


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


def _normalize_parenthesis_filter(value: int | bool | None) -> int | None:
    """Map the product's clear on/off control to Volcengine's 0-100 value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 100 if value else 0
    if isinstance(value, int) and 0 <= value <= 100:
        return value
    raise DoubaoAPIError("圆括号过滤范围必须是 0 到 100")


def _apply_latex_parser_mode(additions: dict[str, Any], mode: str | None) -> None:
    normalized = str(mode or "off").strip().lower()
    if normalized in {"", "off", "none", "false"}:
        return
    if normalized == "basic":
        additions["enable_latex_tn"] = True
        return
    if normalized == "enhanced":
        # The provider requires Markdown filtering for its v2 formula parser.
        additions["disable_markdown_filter"] = True
        additions["latex_parser"] = "v2"
        return
    raise DoubaoAPIError("公式朗读模式必须是 off、basic 或 enhanced")


def _build_aigc_metadata(
    *,
    enabled: bool,
    content_producer: str | None,
    produce_id: str | None,
    content_propagator: str | None,
    propagate_id: str | None,
) -> dict[str, Any] | None:
    if not enabled:
        return None
    metadata: dict[str, Any] = {"enable": True}
    for key, value in {
        "content_producer": content_producer,
        "produce_id": produce_id,
        "content_propagator": content_propagator,
        "propagate_id": propagate_id,
    }.items():
        if value is not None and str(value).strip():
            metadata[key] = str(value).strip()
    return metadata


def build_tts_payload(
    *,
    text: str,
    speaker: str,
    audio_format: str = "mp3",
    sample_rate: int = DEFAULT_TTS_SAMPLE_RATE,
    bit_rate: int | None = DEFAULT_TTS_BIT_RATE,
    speed: float | None = None,
    loudness_rate: int | None = None,
    pitch_rate: int | None = None,
    style_instruction: str | None = None,
    explicit_language: str | None = None,
    enable_subtitle: bool = False,
    silence_duration: int = 0,
    aigc_watermark: bool = False,
    max_length_to_filter_parenthesis: int | bool | None = None,
    disable_markdown_filter: bool = False,
    latex_parser_mode: str | None = None,
    aigc_metadata_enable: bool = False,
    content_producer: str | None = None,
    produce_id: str | None = None,
    content_propagator: str | None = None,
    propagate_id: str | None = None,
    tone_fidelity: bool = False,
    user_id: str = "voice-studio",
) -> dict[str, Any]:
    audio_format = str(audio_format).strip().lower()
    if audio_format not in SUPPORTED_TTS_AUDIO_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_TTS_AUDIO_FORMATS))
        raise DoubaoAPIError(f"豆包 TTS 不支持输出格式：{audio_format or '(empty)'}；支持格式：{supported}")
    audio_params: dict[str, Any] = {
        "format": audio_format,
        "sample_rate": sample_rate,
    }
    if bit_rate is not None and audio_format == "mp3":
        audio_params["bit_rate"] = max(64000, min(160000, int(bit_rate)))
    if speed is not None:
        audio_params["speech_rate"] = max(-50, min(100, int(round((float(speed) - 1.0) * 100))))
    if loudness_rate is not None:
        audio_params["loudness_rate"] = max(-50, min(100, int(loudness_rate)))
    if enable_subtitle:
        audio_params["enable_subtitle"] = True

    req_params: dict[str, Any] = {
        "text": text,
        "speaker": speaker,
        "audio_params": audio_params,
    }
    additions: dict[str, Any] = {}
    normalized_explicit_language = normalize_tts_explicit_language(explicit_language)
    if normalized_explicit_language:
        additions["explicit_language"] = normalized_explicit_language
    if style_instruction and style_instruction.strip():
        additions["context_texts"] = [style_instruction.strip()]
    if pitch_rate is not None:
        additions["post_process"] = {"pitch": max(-12, min(12, int(pitch_rate)))}
    if silence_duration:
        additions["silence_duration"] = max(0, min(30000, int(silence_duration)))
    if aigc_watermark:
        additions["aigc_watermark"] = True
    parenthesis_filter = _normalize_parenthesis_filter(max_length_to_filter_parenthesis)
    if parenthesis_filter:
        additions["max_length_to_filter_parenthesis"] = parenthesis_filter
    if disable_markdown_filter:
        additions["disable_markdown_filter"] = True
    _apply_latex_parser_mode(additions, latex_parser_mode)
    metadata = _build_aigc_metadata(
        enabled=aigc_metadata_enable,
        content_producer=content_producer,
        produce_id=produce_id,
        content_propagator=content_propagator,
        propagate_id=propagate_id,
    )
    if metadata:
        if audio_format not in {"wav", "mp3", "ogg_opus"}:
            raise DoubaoAPIError("豆包隐藏来源信息只支持 WAV、MP3 或 OGG Opus 输出")
        additions["aigc_metadata"] = metadata
    if tone_fidelity:
        additions["tone_fidelity"] = True
    if additions:
        req_params["additions"] = json.dumps(additions, ensure_ascii=False)

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
    sample_rate: int = DEFAULT_TTS_SAMPLE_RATE,
    bit_rate: int | None = DEFAULT_TTS_BIT_RATE,
    speed: float | None = None,
    loudness_rate: int | None = None,
    pitch_rate: int | None = None,
    style_instruction: str | None = None,
    explicit_language: str | None = None,
    enable_subtitle: bool = False,
    silence_duration: int = 0,
    aigc_watermark: bool = False,
    max_length_to_filter_parenthesis: int | bool | None = None,
    disable_markdown_filter: bool = False,
    latex_parser_mode: str | None = None,
    aigc_metadata_enable: bool = False,
    content_producer: str | None = None,
    produce_id: str | None = None,
    content_propagator: str | None = None,
    propagate_id: str | None = None,
    tone_fidelity: bool = False,
    timeout: int = 120,
) -> dict[str, Any]:
    body = build_tts_payload(
        text=text,
        speaker=speaker,
        audio_format=audio_format,
        sample_rate=sample_rate,
        bit_rate=bit_rate,
        speed=speed,
        loudness_rate=loudness_rate,
        pitch_rate=pitch_rate,
        style_instruction=style_instruction,
        explicit_language=explicit_language,
        enable_subtitle=enable_subtitle,
        silence_duration=silence_duration,
        aigc_watermark=aigc_watermark,
        max_length_to_filter_parenthesis=max_length_to_filter_parenthesis,
        disable_markdown_filter=disable_markdown_filter,
        latex_parser_mode=latex_parser_mode,
        aigc_metadata_enable=aigc_metadata_enable,
        content_producer=content_producer,
        produce_id=produce_id,
        content_propagator=content_propagator,
        propagate_id=propagate_id,
        tone_fidelity=tone_fidelity,
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
    subtitle_sentences: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    for frame in frames:
        data = frame.get("data")
        if isinstance(data, str) and data:
            chunks.append(base64.b64decode(data))
        sentence = frame.get("sentence")
        if isinstance(sentence, dict):
            subtitle_sentences.append(sentence)
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
        subtitle={"source": "doubao", "sentences": subtitle_sentences} if subtitle_sentences else None,
    )
    return result.__dict__


def probe_tts_connection(
    *,
    base_url: str,
    api_key: str,
    resource_id: str = DEFAULT_TTS_RESOURCE_ID,
    timeout: int = 15,
) -> dict[str, Any]:
    """Verify TTS credentials with a one-character request and no file write."""

    body = build_tts_payload(
        text="测",
        speaker=DOUBAO_TTS_PRESET_SPEAKERS[0]["voice_id"],
        audio_format="mp3",
        sample_rate=24000,
        bit_rate=64000,
        user_id="voice-studio-connection-test",
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
            f"Doubao TTS probe failed: HTTP {exc.code}",
            status_code=exc.code,
            logid=logid,
            body=raw[:1200],
        ) from exc
    except urllib.error.URLError as exc:
        raise DoubaoAPIError("Doubao TTS probe network failure") from exc

    audio_bytes = 0
    final: dict[str, Any] | None = None
    for frame in iter_concatenated_json(raw):
        data = frame.get("data")
        if isinstance(data, str) and data:
            try:
                audio_bytes += len(base64.b64decode(data))
            except (ValueError, TypeError) as exc:
                raise DoubaoAPIError("Doubao TTS probe returned invalid audio", logid=logid) from exc
        if frame.get("code") is not None:
            final = frame
    if final and final.get("code") not in {20000000, "20000000"}:
        raise DoubaoAPIError(
            "Doubao TTS probe returned an error",
            logid=logid,
            body=json.dumps(final, ensure_ascii=False)[:1200],
        )
    if audio_bytes <= 0:
        raise DoubaoAPIError("Doubao TTS probe returned no audio", logid=logid, body=raw[:1200])
    return {"request_id": request_id, "logid": logid, "audio_bytes": audio_bytes}


def build_voice_clone_payload(
    *,
    speaker_id: str,
    audio_path: str,
    custom_speaker_id: str | None = None,
    text: str | None = None,
    language: str | int = "zh",
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
        "language": voice_clone_language_code(language),
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
