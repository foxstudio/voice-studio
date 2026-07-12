from __future__ import annotations

import json
import sys
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services import database, doubao_client, doubao_speaker_catalog_store as store, settings_store  # noqa: E402


NOW = datetime(2026, 7, 12, 8, 9, 10, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, body: bytes, *, headers: dict[str, str] | None = None, url: str = "https://open.volcengineapi.com/"):
        self.body = body
        self.headers = headers or {}
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]

    def geturl(self) -> str:
        return self.url


@pytest.fixture
def isolated_catalog(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings_store, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.delenv("VOLCENGINE_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("VOLCENGINE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("VOLCENGINE_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("VOLCENGINE_SECRET_KEY", raising=False)
    monkeypatch.delenv("VOLCENGINE_SESSION_TOKEN", raising=False)
    yield tmp_path


def test_first_run_uses_six_fallback_speakers_and_reports_stale(isolated_catalog):
    speakers = store.list_speakers()
    assert len(speakers) == len(doubao_client.DOUBAO_TTS_PRESET_SPEAKERS) == 6
    assert all(item.catalog_source == "bundled" and item.catalog_stale for item in speakers)
    status = store.catalog_status(now=NOW)
    assert status["source"] == "bundled"
    assert status["total"] == status["count"] == 6
    assert status["complete"] is False
    assert status["last_synced_at"] is None
    assert status["stale"] is True
    assert status["ttl_seconds"] == 86400
    assert status["last_error"] is None
    assert status["credentials_configured"] is False


def test_list_speakers_maps_pages_and_deduplicates_metadata():
    calls: list[dict] = []

    class Client:
        def signed_request(self, action, *, body):
            calls.append({"action": action, "body": body})
            if body["Page"] == 1:
                return {
                    "Result": {
                        "Speakers": [
                            {
                                "SpeakerID": "voice-a",
                                "SpeakerName": "清亮女声",
                                "Gender": "female",
                                "Languages": [{"Language": "zh", "Text": "这是一段试听文本"}, {"Language": "en"}],
                                "Emotions": [{"Name": "happy"}],
                                "Categories": {"Categories": [{"Name": "通用场景"}]},
                                "TrialURL": "https://voice.volces.com/a.mp3",
                                "ShortTrialURL": "https://voice.volces.com/a-short.mp3",
                                "AvatarURL": "https://lf3-static.bytednsdoc.com/avatar.png",
                                "ResourceID": "seed-tts-2.0",
                            },
                            {"SpeakerID": "voice-b", "SpeakerName": "沉稳男声", "Gender": "M"},
                        ],
                        "Total": 3,
                    }
                }
            return {
                "Result": {
                    "Items": [
                        {"SpeakerID": "voice-a", "Tags": ["旁白"], "SupportEmotions": ["sad"]},
                        {"SpeakerId": "voice-c", "Name": "童声", "Gender": "女", "Language": "zh/en"},
                    ],
                    "Total": 3,
                }
            }

    speakers = store.fetch_all_speakers(Client(), page_limit=2)
    assert [item.speaker_id for item in speakers] == ["voice-a", "voice-b", "voice-c"]
    voice_a = speakers[0]
    assert voice_a.languages == ["zh", "en"]
    assert voice_a.emotions == ["happy", "sad"]
    assert voice_a.categories == ["通用场景"]
    assert voice_a.normal_labels == ["旁白"]
    assert voice_a.trial_url == "https://voice.volces.com/a.mp3"
    assert voice_a.short_trial_url == "https://voice.volces.com/a-short.mp3"
    assert voice_a.preview_text == "这是一段试听文本"
    assert voice_a.avatar_url == "https://lf3-static.bytednsdoc.com/avatar.png"
    assert voice_a.catalog_source == "official"
    assert calls == [
        {"action": "ListSpeakers", "body": {"ResourceIDs": ["seed-tts-2.0"], "Page": 1, "Limit": 2}},
        {"action": "ListSpeakers", "body": {"ResourceIDs": ["seed-tts-2.0"], "Page": 2, "Limit": 2}},
    ]


def test_numeric_pagination_and_repeated_cursor_are_guarded():
    class NumericClient:
        def signed_request(self, _action, *, body):
            page = body["Page"]
            return {
                "Result": {
                    "SpeakerList": [{"VoiceType": f"voice-{page}", "DisplayName": f"音色 {page}"}],
                    "Total": 2,
                }
            }

    assert [item.speaker_id for item in store.fetch_all_speakers(NumericClient(), page_limit=1)] == ["voice-1", "voice-2"]

    class LoopClient:
        def signed_request(self, _action, *, body):
            return {"Result": {"Speakers": [{"SpeakerID": "same"}], "Total": 1000}}

    with pytest.raises(store.DoubaoSpeakerCatalogError, match="repeated"):
        store.fetch_all_speakers(LoopClient(), page_limit=1)


def test_sync_is_atomic_stale_after_24h_and_failure_preserves_last_good(isolated_catalog):
    class GoodClient:
        def signed_request(self, _action, *, body):
            return {"Result": {"Speakers": [{"SpeakerID": "voice-a", "SpeakerName": "音色 A"}]}}

    status = store.sync_catalog(client=GoodClient(), now=NOW)
    assert status["source"] == "cache"
    assert status["stale"] is False
    assert [item.speaker_id for item in store.list_speakers()] == ["voice-a"]
    cached_before = json.loads(store.cache_path().read_text(encoding="utf-8"))
    assert not list(store.cache_path().parent.glob("tmp*"))
    assert store.catalog_status(now=NOW + timedelta(hours=23, minutes=59))["stale"] is False
    assert store.catalog_status(now=NOW + timedelta(hours=24))["stale"] is True

    class FailedClient:
        def signed_request(self, _action, *, body):
            raise store.DoubaoSpeakerCatalogError("provider unavailable")

    with pytest.raises(store.DoubaoSpeakerCatalogError, match="provider unavailable"):
        store.sync_catalog(client=FailedClient(), now=NOW + timedelta(hours=25))
    cached_after = json.loads(store.cache_path().read_text(encoding="utf-8"))
    assert cached_after["items"] == cached_before["items"]
    assert cached_after["fetched_at"] == cached_before["fetched_at"]
    assert cached_after["last_error"] == "provider unavailable"
    assert [item.speaker_id for item in store.list_speakers()] == ["voice-a"]


def test_openapi_request_uses_separate_aksk_hmac_headers():
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured.update({"request": request, "timeout": timeout})
        return FakeResponse(b'{"Result":{"Speakers":[]}}')

    client = store.VolcengineOpenAPIClient("AKID", "SECRET", "SESSION", urlopen=fake_urlopen)
    request_body = {"ResourceIDs": ["seed-tts-2.0"], "Page": 1, "Limit": 100}
    assert client.signed_request("ListSpeakers", body=request_body, now=NOW) == {"Result": {"Speakers": []}}
    request = captured["request"]
    assert request.full_url == "https://open.volcengineapi.com/?Action=ListSpeakers&Version=2025-05-20"
    assert request.method == "POST"
    assert json.loads(request.data) == request_body
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["x-date"] == "20260712T080910Z"
    assert headers["x-security-token"] == "SESSION"
    assert "Credential=AKID/20260712/cn-beijing/speech_saas_prod/request" in headers["authorization"]
    assert "SignedHeaders=host;x-content-sha256;x-date;x-security-token" in headers["authorization"]
    assert headers["authorization"].endswith("Signature=f89bbcd1cb16be3a95654e5c25b2cec4eb8f8ebd61a95634b4de75cc447abc92")


def test_catalog_credentials_are_separate_from_synthesis_api_key(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_API_KEY", "synthesis-only")
    monkeypatch.setattr(settings_store, "volcengine_access_key_id", lambda: None)
    monkeypatch.setattr(settings_store, "volcengine_secret_access_key", lambda: None)
    monkeypatch.delenv("VOLCENGINE_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("VOLCENGINE_SECRET_ACCESS_KEY", raising=False)
    assert store._credentials() is None

    monkeypatch.setattr(settings_store, "volcengine_access_key_id", lambda: "catalog-ak")
    monkeypatch.setattr(settings_store, "volcengine_secret_access_key", lambda: "catalog-sk")
    assert store._credentials() == ("catalog-ak", "catalog-sk", None)


def test_stale_catalog_starts_only_one_background_sync(isolated_catalog, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_sync():
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(store, "_credentials", lambda: ("AK", "SK", None))
    monkeypatch.setattr(store, "sync_catalog", fake_sync)
    monkeypatch.setattr(store, "_refresh_thread", None)
    monkeypatch.setattr(store, "_last_auto_refresh_attempt", 0.0)
    assert store.maybe_sync_catalog() is True
    assert started.wait(timeout=1)
    assert store.maybe_sync_catalog() is False
    release.set()
    assert store._refresh_thread is not None
    store._refresh_thread.join(timeout=1)
    assert calls == 1
    assert store.maybe_sync_catalog() is False


def _cache_preview_speaker(preview_url: str | None) -> None:
    speaker = {
        "speaker_id": "voice-a",
        "name": "音色 A",
        "gender": "F",
        "description": "",
        "label": "音色 A · 女声",
        "trial_url": preview_url,
        "catalog_source": "official",
    }
    store._atomic_json(
        store.cache_path(),
        {"schema_version": 1, "fetched_at": "2026-07-12T08:00:00Z", "last_error": None, "items": [speaker]},
    )


def test_preview_is_same_origin_cache_with_mime_size_and_host_guards(isolated_catalog):
    assert store._allowed_preview_url("https://lf3-static.bytednsdoc.com/avatar.png") is True
    _cache_preview_speaker("https://voice.volces.com/trial/a.mp3")
    downloads = 0

    def fake_urlopen(_request, timeout):
        nonlocal downloads
        downloads += 1
        assert timeout == 10
        return FakeResponse(
            b"audio-bytes",
            headers={"Content-Type": "audio/mpeg", "Content-Length": "11"},
            url="https://voice.volces.com/trial/a.mp3",
        )

    first_path, first_mime = store.get_preview("voice-a", now=NOW, urlopen=fake_urlopen)
    second_path, second_mime = store.get_preview("voice-a", now=NOW + timedelta(hours=5), urlopen=fake_urlopen)
    assert downloads == 1
    assert first_path == second_path
    assert first_path.read_bytes() == b"audio-bytes"
    assert first_mime == second_mime == "audio/mpeg"

    _cache_preview_speaker("https://127.0.0.1/private.mp3")
    with pytest.raises(store.DoubaoSpeakerPreviewUnavailable, match="域名范围"):
        store.get_preview("voice-a", now=NOW, urlopen=fake_urlopen)

    _cache_preview_speaker("https://voice.volces.com/redirect.mp3")
    with pytest.raises(store.DoubaoSpeakerPreviewUnavailable, match="重定向"):
        store.get_preview(
            "voice-a",
            now=NOW,
            urlopen=lambda *_args, **_kwargs: FakeResponse(
                b"private", headers={"Content-Type": "audio/mpeg"}, url="https://127.0.0.1/private.mp3"
            ),
        )


def test_preview_missing_trial_url_non_audio_and_oversize_are_rejected(isolated_catalog):
    _cache_preview_speaker(None)
    with pytest.raises(store.DoubaoSpeakerPreviewUnavailable, match="没有 TrialURL"):
        store.get_preview("voice-a", now=NOW)

    _cache_preview_speaker("https://voice.volces.com/a.mp3")
    with pytest.raises(store.DoubaoSpeakerPreviewUnavailable, match="非音频 MIME"):
        store.get_preview(
            "voice-a",
            now=NOW,
            urlopen=lambda *_args, **_kwargs: FakeResponse(
                b"html", headers={"Content-Type": "text/html"}, url="https://voice.volces.com/a.mp3"
            ),
        )
    with pytest.raises(store.DoubaoSpeakerPreviewUnavailable, match="超过 5MB"):
        store.get_preview(
            "voice-a",
            now=NOW,
            urlopen=lambda *_args, **_kwargs: FakeResponse(
                b"x", headers={"Content-Type": "audio/mpeg", "Content-Length": str(store.PREVIEW_MAX_BYTES + 1)}, url="https://voice.volces.com/a.mp3"
            ),
        )


def test_catalog_status_sync_and_preview_api_contract(isolated_catalog, tmp_path: Path, monkeypatch):
    database.set_db_path(tmp_path / "voice_studio.db")
    client = TestClient(app)

    status = client.get("/api/engines/doubao-tts-preset/speaker-catalog/status")
    assert status.status_code == 200
    assert status.json()["source"] == "bundled"

    monkeypatch.setattr(store, "sync_catalog", lambda: {"source": "cache", "count": 2, "stale": False})
    synced = client.post("/api/engines/doubao-tts-preset/speaker-catalog/sync")
    assert synced.status_code == 200
    assert synced.json()["count"] == 2

    preview = tmp_path / "preview.mp3"
    preview.write_bytes(b"mp3")
    monkeypatch.setattr(store, "get_preview", lambda _speaker_id: (preview, "audio/mpeg"))
    response = client.get("/api/engines/doubao-tts-preset/speakers/voice-a/preview")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert "content-disposition" not in response.headers
    assert response.content == b"mp3"


def test_sync_api_reports_missing_credentials(isolated_catalog, tmp_path: Path, monkeypatch):
    database.set_db_path(tmp_path / "voice_studio.db")
    monkeypatch.setattr(
        store,
        "sync_catalog",
        lambda: (_ for _ in ()).throw(store.DoubaoCatalogCredentialsRequired("AK/SK required")),
    )
    response = TestClient(app).post("/api/engines/doubao-tts-preset/speaker-catalog/sync")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DOUBAO_CATALOG_CREDENTIALS_REQUIRED"
