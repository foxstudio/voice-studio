from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import socket
import uuid
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .adapter import SeedAudioAdapter
from .schemas import SeedAudioRequest


DEFAULT_BASE_URL = "https://openspeech.bytedance.com"
CREATE_PATH = "/api/v3/tts/create"
SAFE_OUTPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
OUTPUT_EXTENSIONS = {"wav": "wav", "mp3": "mp3", "pcm": "pcm", "ogg_opus": "ogg"}
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_JSON_RESPONSE_BYTES = 96 * 1024 * 1024
TRUSTED_RESULT_HOST_SUFFIXES = (
    ".bytedance.com",
    ".bytecdn.cn",
    ".volces.com",
    ".volcengine.com",
    ".volccdn.com",
)
TEST_HOST_SUFFIX = ".example.test"
REMOTE_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
LONG_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9+/=_-])")
SENSITIVE_KEYS = frozenset(
    {
        "x-api-key",
        "audio_data",
        "image_data",
        "audio",
        "url",
        "audio_url",
        "image_url",
    }
)


class SeedAudioClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeedAudioHTTPResponse:
    status_code: int
    headers: Mapping[str, str]
    body: Mapping[str, Any]


class SeedAudioTransport(Protocol):
    def __call__(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        timeout: float,
    ) -> SeedAudioHTTPResponse: ...


@dataclass(frozen=True)
class SeedAudioResult:
    output_path: str
    request_id: str
    logid: str | None
    source: str
    audio_bytes: int
    duration: float | None
    original_duration: float | None
    subtitle: Mapping[str, Any] | None


def redact_sensitive(value: Any, *, key: str | None = None) -> Any:
    """Return a recursively sanitized diagnostic representation."""

    if key and key.lower() in SENSITIVE_KEYS:
        return "<redacted>"
    if isinstance(value, Mapping):
        return {item_key: redact_sensitive(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    return next((value for key, value in headers.items() if key.lower() == target), None)


def _read_limited(response: Any, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise SeedAudioClientError("Seed Audio 返回内容超过安全大小限制")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_base_url(base_url: str, *, allow_test_host: bool) -> str:
    parsed = urllib.parse.urlparse(base_url)
    allowed_host = parsed.hostname == "openspeech.bytedance.com"
    if allow_test_host and parsed.hostname and parsed.hostname.endswith(TEST_HOST_SUFFIX):
        allowed_host = True
    if (
        parsed.scheme != "https"
        or not allowed_host
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SeedAudioClientError("Seed Audio 服务地址必须是官方 HTTPS 地址")
    return base_url.rstrip("/")


def _validate_result_url(url: str, *, allow_test_host: bool) -> None:
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    trusted = any(hostname.endswith(suffix) for suffix in TRUSTED_RESULT_HOST_SUFFIXES)
    is_test = allow_test_host and hostname.endswith(TEST_HOST_SUFFIX)
    if (
        parsed.scheme != "https"
        or not hostname
        or (not trusted and not is_test)
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise SeedAudioClientError("Seed Audio 结果地址不在可信 HTTPS 域名中")
    if is_test:
        return
    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SeedAudioClientError("Seed Audio 结果地址无法解析") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise SeedAudioClientError("Seed Audio 结果地址指向非公网地址")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, allow_test_host: bool):
        super().__init__()
        self._allow_test_host = allow_test_host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_result_url(newurl, allow_test_host=self._allow_test_host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_url_download(url: str, timeout: float, *, allow_test_host: bool) -> bytes:
    _validate_result_url(url, allow_test_host=allow_test_host)
    opener = urllib.request.build_opener(_SafeRedirectHandler(allow_test_host=allow_test_host))
    with opener.open(url, timeout=timeout) as response:
        return _read_limited(response, MAX_OUTPUT_BYTES)


def urllib_json_transport(
    *,
    url: str,
    headers: Mapping[str, str],
    json_body: Mapping[str, Any],
    timeout: float,
) -> SeedAudioHTTPResponse:
    """Production HTTP transport; callers own authorization to invoke it."""

    request = urllib.request.Request(
        url,
        data=json.dumps(json_body, ensure_ascii=False).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = int(getattr(response, "status", 200))
            response_headers = dict(response.headers.items())
            raw = _read_limited(response, MAX_JSON_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        response_headers = dict(exc.headers.items()) if exc.headers else {}
        raw = _read_limited(exc, MAX_JSON_RESPONSE_BYTES)
    except urllib.error.URLError as exc:
        raise SeedAudioClientError("Seed Audio 网络请求失败") from exc

    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeedAudioClientError("Seed Audio 返回了无法解析的 JSON") from exc
    if not isinstance(body, Mapping):
        raise SeedAudioClientError("Seed Audio 返回的 JSON 结构无效")
    return SeedAudioHTTPResponse(status_code=status_code, headers=response_headers, body=body)


class SeedAudioClient:
    """Seed Audio HTTP boundary with an explicitly injected POST transport.

    Requiring ``transport`` keeps production wiring explicit and lets tests
    exercise the complete request lifecycle without making billable calls.
    """

    def __init__(
        self,
        *,
        api_key: str,
        transport: SeedAudioTransport,
        base_url: str = DEFAULT_BASE_URL,
        url_downloader: Callable[[str, float], bytes] | None = None,
        adapter: SeedAudioAdapter | None = None,
        allow_test_host: bool = False,
    ) -> None:
        if not api_key.strip():
            raise SeedAudioClientError("Seed Audio API Key 未配置")
        self._api_key = api_key.strip()
        self._transport = transport
        self._allow_test_host = allow_test_host
        self._base_url = _validate_base_url(base_url, allow_test_host=allow_test_host)
        self._url_downloader = url_downloader or (
            lambda url, timeout: _safe_url_download(url, timeout, allow_test_host=self._allow_test_host)
        )
        self._adapter = adapter or SeedAudioAdapter()

    def build_headers(self, *, request_id: str | None = None) -> tuple[dict[str, str], str]:
        resolved_request_id = request_id or str(uuid.uuid4())
        return (
            {
                "Content-Type": "application/json",
                "X-Api-Key": self._api_key,
                "X-Api-Request-Id": resolved_request_id,
            },
            resolved_request_id,
        )

    def create_and_save(
        self,
        request: SeedAudioRequest,
        *,
        output_dir: str | Path,
        output_name: str,
        request_id: str | None = None,
        timeout: float = 300,
        cancel_check: Callable[[], bool] | None = None,
    ) -> SeedAudioResult:
        output_path = self._output_path(output_dir, output_name, request.audio_config.format)
        headers, resolved_request_id = self.build_headers(request_id=request_id)
        response = self._transport(
            url=self._base_url + CREATE_PATH,
            headers=headers,
            json_body=self._adapter.build_payload(request),
            timeout=timeout,
        )
        body = response.body
        code = body.get("code")
        if response.status_code < 200 or response.status_code >= 300 or code not in (None, 0):
            message = self._sanitize_remote_message(str(body.get("message") or "unknown error"))
            raise SeedAudioClientError(
                f"Seed Audio 请求失败：HTTP {response.status_code}, code={code}, message={message[:300]}"
            )

        audio_bytes, source = self._resolve_audio(body, timeout=timeout)
        if not audio_bytes:
            raise SeedAudioClientError("Seed Audio 返回了空音频")
        self._validate_audio_bytes(audio_bytes, request.audio_config.format)
        if cancel_check and cancel_check():
            raise SeedAudioClientError("Generation cancelled")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio_bytes)

        subtitle = body.get("subtitle")
        return SeedAudioResult(
            output_path=str(output_path),
            request_id=resolved_request_id,
            logid=_header(response.headers, "X-Tt-Logid"),
            source=source,
            audio_bytes=len(audio_bytes),
            duration=_optional_float(body.get("duration")),
            original_duration=_optional_float(body.get("original_duration")),
            subtitle=subtitle if isinstance(subtitle, Mapping) else None,
        )

    @staticmethod
    def _output_path(output_dir: str | Path, output_name: str, audio_format: str) -> Path:
        if not SAFE_OUTPUT_NAME.fullmatch(output_name) or output_name in {".", ".."}:
            raise SeedAudioClientError("输出名称必须是安全文件名")
        root = Path(output_dir).expanduser().resolve()
        extension = OUTPUT_EXTENSIONS.get(audio_format)
        if not extension:
            raise SeedAudioClientError("Seed Audio 输出格式无效")
        output_path = (root / f"{output_name}.{extension}").resolve()
        if output_path.parent != root:
            raise SeedAudioClientError("输出路径必须位于受控输出目录内")
        return output_path

    def _resolve_audio(self, body: Mapping[str, Any], *, timeout: float) -> tuple[bytes, str]:
        url = body.get("url")
        if isinstance(url, str) and url:
            try:
                _validate_result_url(url, allow_test_host=self._allow_test_host)
                downloaded = self._url_downloader(url, timeout)
                if downloaded:
                    return downloaded, "url"
            except Exception:
                # A valid Base64 response is the documented fallback. Do not put
                # the temporary URL or downloader details into the exception.
                pass

        encoded = body.get("audio")
        if not isinstance(encoded, str) or not encoded:
            raise SeedAudioClientError("Seed Audio 响应既没有可用 URL，也没有 Base64 音频")
        if len(encoded) > ((MAX_OUTPUT_BYTES + 2) // 3) * 4 + 4:
            raise SeedAudioClientError("Seed Audio Base64 音频超过安全大小限制")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SeedAudioClientError("Seed Audio 返回的 Base64 音频无效") from exc
        if len(decoded) > MAX_OUTPUT_BYTES:
            raise SeedAudioClientError("Seed Audio Base64 音频超过安全大小限制")
        return decoded, "base64"

    @staticmethod
    def _validate_audio_bytes(content: bytes, audio_format: str) -> None:
        valid = False
        if audio_format == "wav":
            valid = len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE"
        elif audio_format == "mp3":
            valid = len(content) >= 4 and (
                content.startswith(b"ID3") or (content[0] == 0xFF and content[1] & 0xE0 == 0xE0)
            )
        elif audio_format == "ogg_opus":
            valid = len(content) >= 27 and content.startswith(b"OggS") and b"OpusHead" in content[:256]
        elif audio_format == "pcm":
            valid = len(content) >= 2 and len(content) % 2 == 0
        if not valid:
            raise SeedAudioClientError(f"Seed Audio 返回的 {audio_format} 音频格式无效")

    def _sanitize_remote_message(self, message: str) -> str:
        sanitized = message.replace(self._api_key, "<redacted-key>")
        sanitized = REMOTE_URL_PATTERN.sub("<redacted-url>", sanitized)
        return LONG_TOKEN_PATTERN.sub("<redacted-data>", sanitized)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
