from __future__ import annotations

import base64
import json
import io
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.engines.base import EngineAdapter  # noqa: E402
from app.engines.registry import AdapterRegistry  # noqa: E402
from app.engines.seed_audio.adapter import SeedAudioAdapter  # noqa: E402
from app.engines.seed_audio.client import (  # noqa: E402
    SeedAudioClient,
    SeedAudioClientError,
    SeedAudioHTTPResponse,
    redact_sensitive,
    urllib_json_transport,
)
from app.engines.seed_audio import client as seed_audio_client  # noqa: E402
from app.engines.seed_audio.schemas import SeedAudioReference, SeedAudioRequest  # noqa: E402


VALID_WAV = b"RIFF\x04\x00\x00\x00WAVEdata"
VALID_MP3 = b"ID3\x04\x00\x00mock-mp3"


def _audio_request(**kwargs) -> SeedAudioRequest:
    values = {
        "input_mode": "audio",
        "text_prompt": "让@音频1平静地说：你好。",
        "references": [SeedAudioReference(audio_data="cmVmZXJlbmNlLWF1ZGlv")],
    }
    values.update(kwargs)
    return SeedAudioRequest(**values)


def test_adapter_builds_official_payload_without_local_only_fields():
    adapter = SeedAudioAdapter()
    request = _audio_request()

    assert adapter.engine_id == "doubao-seed-audio-1.0"
    assert isinstance(adapter, EngineAdapter)
    assert adapter.build_payload(request) == {
        "model": "seed-audio-1.0",
        "text_prompt": "让@音频1平静地说：你好。",
        "references": [{"audio_data": "cmVmZXJlbmNlLWF1ZGlv"}],
        "audio_config": {
            "format": "wav",
            "sample_rate": 24000,
            "speech_rate": 0,
            "loudness_rate": 0,
            "pitch_rate": 0,
            "enable_subtitle": False,
        },
    }


def test_adapter_omits_text_references_and_keeps_unknown_audio_source_mixes_allowed():
    adapter = SeedAudioAdapter()
    text_payload = adapter.build_payload(SeedAudioRequest(input_mode="text", text_prompt="生成一声钟响。"))
    assert "references" not in text_payload

    mixed_payload = adapter.build_payload(
        SeedAudioRequest(
            input_mode="audio",
            text_prompt="让@音频1和@音频2对话。",
            references=[
                SeedAudioReference(speaker="speaker-1"),
                SeedAudioReference(audio_url="https://assets.example.test/ref.mp3"),
            ],
        )
    )
    assert mixed_payload["references"] == [
        {"speaker": "speaker-1"},
        {"audio_url": "https://assets.example.test/ref.mp3"},
    ]


def test_adapter_registry_is_explicit_and_rejects_duplicates():
    registry = AdapterRegistry()
    adapter = SeedAudioAdapter()
    registry.register(adapter)
    assert registry.get("doubao-seed-audio-1.0") is adapter
    assert registry.get("unknown") is None
    with pytest.raises(ValueError, match="already registered"):
        registry.register(SeedAudioAdapter())


def test_client_builds_headers_and_never_exposes_secret_in_redacted_diagnostics():
    client = SeedAudioClient(api_key="super-secret-key", transport=lambda **_: None)
    headers, request_id = client.build_headers(request_id="request-1")
    assert headers == {
        "Content-Type": "application/json",
        "X-Api-Key": "super-secret-key",
        "X-Api-Request-Id": "request-1",
    }
    assert request_id == "request-1"

    redacted = redact_sensitive(
        {
            "headers": headers,
            "references": [{"audio_data": "very-long-base64", "speaker": "speaker-1"}],
            "audio": "generated-base64",
            "url": "https://temporary.example.test/private.wav",
        }
    )
    serialized = repr(redacted)
    assert "super-secret-key" not in serialized
    assert "very-long-base64" not in serialized
    assert "generated-base64" not in serialized
    assert "temporary.example.test" not in serialized
    assert redacted["references"][0]["speaker"] == "speaker-1"


def test_mock_http_url_result_is_saved_under_controlled_output_directory(tmp_path: Path):
    calls: list[dict] = []

    def transport(**kwargs):
        calls.append(kwargs)
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={"X-Tt-Logid": "log-1"},
            body={
                "code": 0,
                "message": "ok",
                "url": "https://temporary.example.test/result.wav",
                "audio": base64.b64encode(VALID_WAV).decode(),
                "duration": 1.2,
                "original_duration": 1.0,
            },
        )

    downloaded: list[str] = []

    def download(url: str, timeout: float) -> bytes:
        downloaded.append(url)
        assert timeout == 30
        return VALID_WAV

    client = SeedAudioClient(
        api_key="secret",
        transport=transport,
        url_downloader=download,
        allow_test_host=True,
    )
    result = client.create_and_save(
        SeedAudioRequest(input_mode="text", text_prompt="测试钟声。"),
        output_dir=tmp_path / "managed-output",
        output_name="result",
        request_id="request-1",
        timeout=30,
    )

    output = Path(result.output_path)
    assert output == tmp_path / "managed-output" / "result.wav"
    assert output.read_bytes() == VALID_WAV
    assert result.source == "url"
    assert result.logid == "log-1"
    assert downloaded == ["https://temporary.example.test/result.wav"]
    assert calls[0]["url"] == "https://openspeech.bytedance.com/api/v3/tts/create"
    assert calls[0]["headers"]["X-Api-Key"] == "secret"


def test_url_failure_uses_base64_fallback_and_rejects_path_traversal(tmp_path: Path):
    encoded = base64.b64encode(VALID_MP3).decode()

    def transport(**_):
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={"x-tt-logid": "log-2"},
            body={"code": 0, "url": "https://expired.example.test/a.mp3", "audio": encoded},
        )

    def failed_download(_url: str, _timeout: float) -> bytes:
        raise OSError("expired")

    client = SeedAudioClient(
        api_key="secret",
        transport=transport,
        url_downloader=failed_download,
        allow_test_host=True,
    )
    result = client.create_and_save(
        SeedAudioRequest(
            input_mode="text",
            text_prompt="测试",
            audio_config={"format": "mp3"},
        ),
        output_dir=tmp_path,
        output_name="safe-name",
    )
    assert Path(result.output_path).read_bytes() == VALID_MP3
    assert result.source == "base64"

    with pytest.raises(SeedAudioClientError, match="安全文件名"):
        client.create_and_save(
            SeedAudioRequest(input_mode="text", text_prompt="测试"),
            output_dir=tmp_path,
            output_name="../escape",
        )


def test_invalid_or_empty_mock_response_does_not_leave_output(tmp_path: Path):
    secret = "secret-key-that-must-not-leak"
    echoed_url = "https://temporary.example.test/private.wav"
    echoed_base64 = "VGhpcy1pcy1hLWxvbmctc2Vuc2l0aXZlLWJhc2U2NC12YWx1ZQ=="

    def transport(**_):
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={},
            body={
                "code": 500,
                "message": f"bad key={secret} url={echoed_url} data={echoed_base64}",
                "audio": "sensitive-audio",
            },
        )

    client = SeedAudioClient(api_key=secret, transport=transport)
    with pytest.raises(SeedAudioClientError, match="code=500") as exc:
        client.create_and_save(
            SeedAudioRequest(input_mode="text", text_prompt="测试"),
            output_dir=tmp_path,
            output_name="failed",
        )
    error = str(exc.value)
    assert "sensitive-audio" not in error
    assert secret not in error
    assert echoed_url not in error
    assert echoed_base64 not in error
    assert "<redacted-key>" in error
    assert "<redacted-url>" in error
    assert "<redacted-data>" in error
    assert not list(tmp_path.glob("failed.*"))


def test_production_transport_serializes_json_but_is_fully_mocked(monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200
        headers = {"X-Tt-Logid": "transport-log"}

        def __init__(self):
            self._body = io.BytesIO(b'{"code": 0, "message": "ok", "audio": "YQ=="}')

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            return self._body.read(size)

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.engines.seed_audio.client.urllib.request.urlopen", fake_urlopen)
    response = urllib_json_transport(
        url="https://openspeech.example.test/api/v3/tts/create",
        headers={"X-Api-Key": "mock-key", "X-Api-Request-Id": "request-1"},
        json_body={"model": "seed-audio-1.0", "text_prompt": "测试"},
        timeout=12,
    )
    assert response.status_code == 200
    assert response.headers["X-Tt-Logid"] == "transport-log"
    assert response.body["code"] == 0
    assert captured["url"] == "https://openspeech.example.test/api/v3/tts/create"
    assert captured["body"]["text_prompt"] == "测试"
    assert captured["timeout"] == 12

    monkeypatch.setattr(
        "app.engines.seed_audio.client.urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    with pytest.raises(SeedAudioClientError, match="网络请求失败") as exc:
        urllib_json_transport(
            url="https://openspeech.example.test/api/v3/tts/create",
            headers={},
            json_body={},
            timeout=1,
        )
    assert "openspeech.example.test" not in str(exc.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://openspeech.bytedance.com",
        "https://evil.example.com",
        "https://user:password@openspeech.bytedance.com",
        "https://openspeech.bytedance.com:8443",
    ],
)
def test_client_rejects_non_official_or_non_https_base_url(base_url: str):
    with pytest.raises(SeedAudioClientError, match="官方 HTTPS"):
        SeedAudioClient(api_key="secret", transport=lambda **_: None, base_url=base_url)


def test_test_host_requires_explicit_opt_in():
    with pytest.raises(SeedAudioClientError):
        SeedAudioClient(
            api_key="secret",
            transport=lambda **_: None,
            base_url="https://openspeech.example.test",
        )
    SeedAudioClient(
        api_key="secret",
        transport=lambda **_: None,
        base_url="https://openspeech.example.test",
        allow_test_host=True,
    )


def test_untrusted_result_url_is_never_downloaded_and_safe_base64_fallback_is_used(tmp_path: Path):
    downloaded: list[str] = []

    def transport(**_):
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={},
            body={
                "code": 0,
                "url": "https://127.0.0.1/private.wav",
                "audio": base64.b64encode(VALID_WAV).decode(),
            },
        )

    client = SeedAudioClient(
        api_key="secret",
        transport=transport,
        url_downloader=lambda url, _timeout: downloaded.append(url) or VALID_WAV,
    )
    result = client.create_and_save(
        SeedAudioRequest(input_mode="text", text_prompt="测试"),
        output_dir=tmp_path,
        output_name="safe",
    )
    assert result.source == "base64"
    assert downloaded == []


def test_redirect_to_private_or_untrusted_host_is_rejected():
    handler = seed_audio_client._SafeRedirectHandler(allow_test_host=False)
    with pytest.raises(SeedAudioClientError, match="可信 HTTPS"):
        handler.redirect_request(None, None, 302, "redirect", {}, "https://127.0.0.1/private.wav")


def test_output_size_and_container_integrity_are_enforced(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(seed_audio_client, "MAX_OUTPUT_BYTES", 8)

    def oversized(**_):
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={},
            body={"code": 0, "audio": base64.b64encode(b"x" * 10).decode()},
        )

    with pytest.raises(SeedAudioClientError, match="大小限制"):
        SeedAudioClient(api_key="secret", transport=oversized).create_and_save(
            SeedAudioRequest(input_mode="text", text_prompt="测试"),
            output_dir=tmp_path,
            output_name="oversized",
        )

    def invalid_wav(**_):
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={},
            body={"code": 0, "audio": base64.b64encode(b"not-a-wave-file").decode()},
        )

    monkeypatch.setattr(seed_audio_client, "MAX_OUTPUT_BYTES", 64 * 1024 * 1024)
    with pytest.raises(SeedAudioClientError, match="wav 音频格式无效"):
        SeedAudioClient(api_key="secret", transport=invalid_wav).create_and_save(
            SeedAudioRequest(input_mode="text", text_prompt="测试"),
            output_dir=tmp_path,
            output_name="invalid",
        )
    assert not list(tmp_path.glob("invalid.*"))


def test_pcm_uses_minimal_even_length_integrity_rule(tmp_path: Path):
    def transport(**_):
        return SeedAudioHTTPResponse(
            status_code=200,
            headers={},
            body={"code": 0, "audio": base64.b64encode(b"\x00\x01\x02\x03").decode()},
        )

    result = SeedAudioClient(api_key="secret", transport=transport).create_and_save(
        SeedAudioRequest(input_mode="text", text_prompt="测试", audio_config={"format": "pcm"}),
        output_dir=tmp_path,
        output_name="pcm",
    )
    assert Path(result.output_path).read_bytes() == b"\x00\x01\x02\x03"
