from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.schemas.voice_studio import EngineSpeaker
from app.services import doubao_client, settings_store


CATALOG_SCHEMA_VERSION = 1
CATALOG_TTL_SECONDS = 24 * 60 * 60
PREVIEW_TTL_SECONDS = 6 * 60 * 60
PREVIEW_MAX_BYTES = 5 * 1024 * 1024
AUTO_REFRESH_RETRY_SECONDS = 5 * 60
OPENAPI_HOST = "open.volcengineapi.com"
OPENAPI_SERVICE = "speech_saas_prod"
OPENAPI_REGION = "cn-beijing"
OPENAPI_VERSION = "2025-05-20"
ALLOWED_PREVIEW_HOST_SUFFIXES = (
    ".volces.com",
    ".volcengine.com",
    ".bytecdn.cn",
    ".byteimg.com",
    ".bytedance.com",
    ".bytedance.net",
    ".bytednsdoc.com",
)

_sync_lock = threading.Lock()
_refresh_state_lock = threading.Lock()
_refresh_thread: threading.Thread | None = None
_last_auto_refresh_attempt = 0.0


class DoubaoSpeakerCatalogError(RuntimeError):
    pass


class DoubaoCatalogCredentialsRequired(DoubaoSpeakerCatalogError):
    pass


class DoubaoSpeakerPreviewUnavailable(DoubaoSpeakerCatalogError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _catalog_dir() -> Path:
    return settings_store.cache_dir() / "provider-catalogs"


def cache_path() -> Path:
    return _catalog_dir() / "doubao-seed-tts-2.0.json"


def _preview_dir() -> Path:
    return _catalog_dir() / "doubao-seed-tts-2.0-previews"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def fallback_speakers() -> list[EngineSpeaker]:
    speakers: list[EngineSpeaker] = []
    for item in doubao_client.DOUBAO_TTS_PRESET_SPEAKERS:
        speaker_id = str(item["voice_id"])
        label = str(item["label"])
        raw_gender = str(item.get("gender") or "").lower()
        speakers.append(
            EngineSpeaker(
                speaker_id=speaker_id,
                name=label.split(" 2.0", 1)[0].strip() or speaker_id,
                gender={"female": "F", "male": "M"}.get(raw_gender, ""),
                description="内置 TTS 2.0 兜底目录；账号授权状态以实际生成为准",
                label=label,
                languages=[str(item.get("language") or "zh")],
                resource_id="seed-tts-2.0",
                catalog_source="bundled",
                catalog_stale=True,
            )
        )
    return speakers


def _read_cache() -> dict[str, Any] | None:
    path = cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != CATALOG_SCHEMA_VERSION or not isinstance(payload.get("items"), list):
            return None
        payload["items"] = [EngineSpeaker(**item).model_dump() for item in payload["items"]]
        return payload
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def catalog_status(*, now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    cached = _read_cache()
    fetched_at = _parse_time(cached.get("fetched_at")) if cached else None
    stale = fetched_at is None or (current - fetched_at).total_seconds() >= CATALOG_TTL_SECONDS
    items = cached["items"] if cached else [item.model_dump() for item in fallback_speakers()]
    source = "cache" if cached else "bundled"
    last_error = cached.get("last_error") if cached else None
    last_synced_at = cached.get("fetched_at") if cached else None
    return {
        "source": source,
        "total": len(items),
        "count": len(items),
        "complete": bool(cached and fetched_at),
        "last_synced_at": last_synced_at,
        "fetched_at": last_synced_at,
        "stale": stale,
        "ttl_seconds": CATALOG_TTL_SECONDS,
        "last_error": last_error,
        "message": last_error,
        "sync_available": bool(_credentials()),
        "credentials_configured": bool(_credentials()),
    }


def list_speakers() -> list[EngineSpeaker]:
    cached = _read_cache()
    if not cached:
        speakers = fallback_speakers()
        maybe_sync_catalog()
        return speakers
    fetched_at = _parse_time(cached.get("fetched_at"))
    stale = fetched_at is None or (_now() - fetched_at).total_seconds() >= CATALOG_TTL_SECONDS
    speakers = [EngineSpeaker(**item) for item in cached["items"]]
    for speaker in speakers:
        speaker.catalog_source = "cache"
        speaker.catalog_updated_at = cached.get("fetched_at")
        speaker.catalog_stale = stale
    if stale:
        maybe_sync_catalog()
    return speakers


def maybe_sync_catalog() -> bool:
    """Start one non-blocking refresh when the catalog is stale and AK/SK exist."""
    global _last_auto_refresh_attempt, _refresh_thread
    status = catalog_status()
    if not status["stale"] or not status["credentials_configured"]:
        return False
    with _refresh_state_lock:
        if _refresh_thread and _refresh_thread.is_alive():
            return False
        monotonic_now = time.monotonic()
        if _last_auto_refresh_attempt and monotonic_now - _last_auto_refresh_attempt < AUTO_REFRESH_RETRY_SECONDS:
            return False

        def refresh() -> None:
            try:
                sync_catalog()
            except Exception:
                # sync_catalog preserves the last-known-good cache and status error.
                return

        _refresh_thread = threading.Thread(target=refresh, name="doubao-speaker-catalog-refresh", daemon=True)
        _last_auto_refresh_attempt = monotonic_now
        _refresh_thread.start()
        return True


def _credentials() -> tuple[str, str, str | None] | None:
    access_key_provider = getattr(settings_store, "volcengine_access_key_id", None)
    secret_key_provider = getattr(settings_store, "volcengine_secret_access_key", None)
    if callable(access_key_provider) and callable(secret_key_provider):
        access_key = access_key_provider()
        secret_key = secret_key_provider()
        if access_key and secret_key:
            token_provider = getattr(settings_store, "volcengine_session_token", None)
            token = token_provider() if callable(token_provider) else os.environ.get("VOLCENGINE_SESSION_TOKEN")
            return str(access_key), str(secret_key), str(token) if token else None
    provider = getattr(settings_store, "doubao_catalog_credentials", None)
    if callable(provider):
        value = provider()
        if value and len(value) >= 2:
            return str(value[0]), str(value[1]), str(value[2]) if len(value) > 2 and value[2] else None
    access_key = os.environ.get("VOLCENGINE_ACCESS_KEY_ID") or os.environ.get("VOLCENGINE_ACCESS_KEY")
    secret_key = os.environ.get("VOLCENGINE_SECRET_ACCESS_KEY") or os.environ.get("VOLCENGINE_SECRET_KEY")
    token = os.environ.get("VOLCENGINE_SESSION_TOKEN")
    return (access_key, secret_key, token) if access_key and secret_key else None


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="-_.~")


def _canonical_query(params: dict[str, Any]) -> str:
    pairs: list[tuple[str, str]] = []
    for key in sorted(params):
        values = params[key] if isinstance(params[key], list) else [params[key]]
        pairs.extend((str(key), str(value)) for value in values if value is not None)
    return "&".join(f"{_quote(key)}={_quote(value)}" for key, value in pairs)


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


class VolcengineOpenAPIClient:
    def __init__(
        self,
        access_key: str,
        secret_key: str,
        session_token: str | None = None,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ):
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token
        self.urlopen = urlopen

    def signed_request(
        self,
        action: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        now: datetime | None = None,
        timeout: int = 15,
    ) -> dict[str, Any]:
        request_time = (now or _now()).astimezone(timezone.utc)
        x_date = request_time.strftime("%Y%m%dT%H%M%SZ")
        short_date = request_time.strftime("%Y%m%d")
        params = {"Action": action, "Version": OPENAPI_VERSION, **(query or {})}
        canonical_query = _canonical_query(params)
        body_bytes = json.dumps(body or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        payload_hash = hashlib.sha256(body_bytes).hexdigest()
        headers = {
            "Host": OPENAPI_HOST,
            "X-Content-Sha256": payload_hash,
            "X-Date": x_date,
        }
        if self.session_token:
            headers["X-Security-Token"] = self.session_token
        signed_header_names = sorted(name.lower() for name in headers)
        canonical_headers = "".join(f"{name}:{headers[next(key for key in headers if key.lower() == name)].strip()}\n" for name in signed_header_names)
        signed_headers = ";".join(signed_header_names)
        canonical_request = "\n".join(["POST", "/", canonical_query, canonical_headers, signed_headers, payload_hash])
        credential_scope = f"{short_date}/{OPENAPI_REGION}/{OPENAPI_SERVICE}/request"
        string_to_sign = "\n".join(
            ["HMAC-SHA256", x_date, credential_scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()]
        )
        signing_key = _hmac(_hmac(_hmac(_hmac(self.secret_key.encode("utf-8"), short_date), OPENAPI_REGION), OPENAPI_SERVICE), "request")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["Authorization"] = (
            f"HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        url = f"https://{OPENAPI_HOST}/?{canonical_query}"
        headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
        try:
            with self.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise DoubaoSpeakerCatalogError(f"ListSpeakers HTTP {exc.code}: {body[:500]}") from exc
        except urllib.error.URLError as exc:
            raise DoubaoSpeakerCatalogError(f"ListSpeakers network error: {exc.reason}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DoubaoSpeakerCatalogError("ListSpeakers returned invalid JSON") from exc
        metadata = payload.get("ResponseMetadata") or {}
        error = metadata.get("Error") or payload.get("Error")
        if error:
            raise DoubaoSpeakerCatalogError(f"ListSpeakers failed: {error}")
        return payload


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _strings(value: Any, *preferred_keys: str) -> list[str]:
    result: list[str] = []

    def append(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, dict):
            matched = False
            for key in preferred_keys:
                if key in item:
                    matched = True
                    append(item[key])
            if not matched:
                for nested in item.values():
                    append(nested)
            return
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                append(nested)
            return
        text = str(item).strip()
        if not text:
            return
        separator = "," if "," in text else "/" if "/" in text else None
        values = [part.strip() for part in text.split(separator)] if separator else [text]
        for part in values:
            if part and part not in result:
                result.append(part)

    append(value)
    return result


def _nested_text(value: Any, key: str) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        direct = value.get(key)
        if direct not in (None, ""):
            return str(direct).strip()
        for nested in value.values():
            found = _nested_text(nested, key)
            if found:
                return found
    if isinstance(value, (list, tuple, set)):
        for item in value:
            found = _nested_text(item, key)
            if found:
                return found
    return ""


def _map_speaker(raw: dict[str, Any]) -> EngineSpeaker | None:
    speaker_id = str(_first(raw, "SpeakerID", "SpeakerId", "speaker_id", "VoiceType", "voice_type", "VoiceID", "voice_id") or "").strip()
    if not speaker_id:
        return None
    name = str(_first(raw, "SpeakerName", "Name", "DisplayName", "speaker_name", "name") or speaker_id).strip()
    raw_gender = str(_first(raw, "Gender", "gender") or "").strip().lower()
    gender = {"female": "F", "woman": "F", "女": "F", "f": "F", "male": "M", "man": "M", "男": "M", "m": "M"}.get(raw_gender, raw_gender.upper())
    description = str(_first(raw, "Description", "Desc", "description", "desc") or "").strip()
    raw_languages = _first(raw, "Languages", "SupportLanguages", "Language", "languages", "language")
    languages = _strings(raw_languages, "Language", "Code", "Name", "Value", "language", "code", "name", "value")
    emotions = _strings(
        _first(raw, "Emotions", "SupportEmotions", "Emotion", "emotions", "emotion"),
        "Emotion", "Value", "Name", "Label", "emotion", "value", "name", "label",
    )
    categories = _strings(
        _first(raw, "Categories", "Category", "Scene", "categories", "category", "scene"),
        "Categories", "Category", "Name", "Label", "Value", "categories", "category", "name", "label", "value",
    )
    age = str(_first(raw, "Age", "AgeGroup", "age", "age_group") or "").strip()
    normal_labels = _strings(
        _first(raw, "NormalLabels", "Tags", "Tag", "Labels", "normal_labels", "tags", "tag", "labels"),
        "Name", "Label", "Value", "name", "label", "value",
    )
    special_labels = _strings(
        _first(raw, "SpecialLabels", "special_labels"), "Name", "Label", "Value", "name", "label", "value"
    )
    trial_url = _first(raw, "TrialURL", "TrialUrl", "PreviewURL", "PreviewUrl", "DemoAudio", "demo_audio", "trial_url")
    short_trial_url = _first(raw, "ShortTrialURL", "ShortTrialUrl", "short_trial_url")
    preview_text = str(_first(raw, "PreviewText", "TrialText", "preview_text", "trial_text") or "").strip() or _nested_text(raw_languages, "Text")
    avatar_url = _first(raw, "AvatarURL", "AvatarUrl", "avatar_url")
    resource_id = _first(raw, "ResourceID", "ResourceId", "resource_id")
    authorization = _first(raw, "AuthorizationStatus", "Authorized", "authorization_status", "authorized")
    if isinstance(authorization, bool):
        authorization_status = "verified" if authorization else "denied"
    else:
        normalized_authorization = str(authorization or "").strip().lower()
        authorization_status = {
            "authorized": "verified",
            "available": "verified",
            "true": "verified",
            "unauthorized": "denied",
            "unavailable": "denied",
            "false": "denied",
        }.get(normalized_authorization, normalized_authorization or "unknown")
    deprecated = bool(_first(raw, "Deprecated", "IsDeprecated", "deprecated", "is_deprecated") or False)
    gender_label = {"F": "女声", "M": "男声"}.get(gender, gender)
    label = " · ".join(part for part in [name, gender_label] if part)
    return EngineSpeaker(
        speaker_id=speaker_id,
        name=name,
        gender=gender,
        description=description,
        label=label or speaker_id,
        age=age,
        languages=languages,
        emotions=emotions,
        categories=categories,
        normal_labels=normal_labels,
        special_labels=special_labels,
        trial_url=str(trial_url).strip() if trial_url else None,
        short_trial_url=str(short_trial_url).strip() if short_trial_url else None,
        preview_text=preview_text,
        avatar_url=str(avatar_url).strip() if avatar_url else None,
        resource_id=str(resource_id).strip() if resource_id else None,
        catalog_source="official",
        catalog_stale=False,
        authorization_status=authorization_status,
        deprecated=deprecated,
    )


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("Result")
    return result if isinstance(result, dict) else payload


def _page_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = _result(payload)
    for key in ("Speakers", "SpeakerList", "Items", "List", "speakers", "items"):
        value = result.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if isinstance(result.get("Data"), dict):
        return _page_items({"Result": result["Data"]})
    return []


def fetch_all_speakers(
    client: VolcengineOpenAPIClient,
    *,
    max_pages: int = 100,
    page_limit: int = 100,
) -> list[EngineSpeaker]:
    page = 1
    limit = max(1, min(int(page_limit), 100))
    merged: dict[str, EngineSpeaker] = {}
    seen_pages: set[str] = set()
    for _ in range(max_pages):
        payload = client.signed_request(
            "ListSpeakers",
            body={"ResourceIDs": ["seed-tts-2.0"], "Page": page, "Limit": limit},
        )
        raw_items = _page_items(payload)
        marker = hashlib.sha256(json.dumps(raw_items, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if marker in seen_pages and raw_items:
            raise DoubaoSpeakerCatalogError("ListSpeakers pagination repeated the same page")
        seen_pages.add(marker)
        for raw in raw_items:
            speaker = _map_speaker(raw)
            if not speaker:
                continue
            previous = merged.get(speaker.speaker_id)
            if previous:
                speaker.languages = list(dict.fromkeys([*previous.languages, *speaker.languages]))
                speaker.emotions = list(dict.fromkeys([*previous.emotions, *speaker.emotions]))
                speaker.categories = list(dict.fromkeys([*previous.categories, *speaker.categories]))
                speaker.normal_labels = list(dict.fromkeys([*previous.normal_labels, *speaker.normal_labels]))
                speaker.special_labels = list(dict.fromkeys([*previous.special_labels, *speaker.special_labels]))
                for field in ("description", "trial_url", "short_trial_url", "preview_text", "avatar_url", "resource_id"):
                    if not getattr(speaker, field):
                        setattr(speaker, field, getattr(previous, field))
            merged[speaker.speaker_id] = speaker
        result = _result(payload)
        total = _first(result, "Total", "TotalCount", "total", "total_count")
        try:
            total_int = int(total)
        except (TypeError, ValueError):
            total_int = None
        if not raw_items or len(raw_items) < limit or (total_int is not None and page * limit >= total_int):
            return list(merged.values())
        page += 1
    raise DoubaoSpeakerCatalogError(f"ListSpeakers exceeded {max_pages} pages")


def sync_catalog(*, client: VolcengineOpenAPIClient | None = None, now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    with _sync_lock:
        if client is None:
            credentials = _credentials()
            if not credentials:
                raise DoubaoCatalogCredentialsRequired("豆包官方音色目录同步需要独立的火山引擎 AK/SK")
            client = VolcengineOpenAPIClient(*credentials)
        previous = _read_cache()
        try:
            speakers = fetch_all_speakers(client)
            if not speakers:
                raise DoubaoSpeakerCatalogError("ListSpeakers returned an empty catalog")
            fetched_at = _iso(current)
            for speaker in speakers:
                speaker.catalog_source = "official"
                speaker.catalog_updated_at = fetched_at
                speaker.catalog_stale = False
            payload = {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "fetched_at": fetched_at,
                "last_error": None,
                "items": [speaker.model_dump(mode="json") for speaker in speakers],
            }
            _atomic_json(cache_path(), payload)
        except Exception as exc:
            if previous:
                previous["last_error"] = str(exc)
                _atomic_json(cache_path(), previous)
            raise
    return catalog_status(now=current)


def _allowed_preview_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and bool(host)
        and not parsed.username
        and not parsed.password
        and parsed.port in (None, 443)
        and any(host == suffix[1:] or host.endswith(suffix) for suffix in ALLOWED_PREVIEW_HOST_SUFFIXES)
    )


def _preview_extension(content_type: str) -> str:
    return {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/ogg": ".ogg",
        "audio/aac": ".aac",
        "audio/mp4": ".m4a",
    }.get(content_type, ".audio")


def get_preview(
    speaker_id: str,
    *,
    now: datetime | None = None,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[Path, str]:
    speaker = next((item for item in list_speakers() if item.speaker_id == speaker_id), None)
    if not speaker:
        raise DoubaoSpeakerPreviewUnavailable("豆包官方音色不存在")
    if not speaker.trial_url:
        raise DoubaoSpeakerPreviewUnavailable("该豆包官方音色没有 TrialURL，暂时无法试听")
    if not _allowed_preview_url(speaker.trial_url):
        raise DoubaoSpeakerPreviewUnavailable("豆包官方音色 TrialURL 不在允许的域名范围内")

    current = now or _now()
    key = hashlib.sha256(f"{speaker.speaker_id}\0{speaker.trial_url}".encode("utf-8")).hexdigest()
    directory = _preview_dir()
    metadata_path = directory / f"{key}.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            path = directory / str(metadata["filename"])
            fetched_at = _parse_time(metadata.get("fetched_at"))
            if path.exists() and fetched_at and (current - fetched_at).total_seconds() < PREVIEW_TTL_SECONDS:
                return path, str(metadata["content_type"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass

    request = urllib.request.Request(speaker.trial_url, headers={"User-Agent": "VoiceStudio/1.0"}, method="GET")
    try:
        with urlopen(request, timeout=10) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else speaker.trial_url
            if not _allowed_preview_url(final_url):
                raise DoubaoSpeakerPreviewUnavailable("豆包试听地址重定向到了不允许的域名")
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if not content_type.startswith("audio/"):
                raise DoubaoSpeakerPreviewUnavailable(f"豆包试听返回了非音频 MIME：{content_type or 'unknown'}")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > PREVIEW_MAX_BYTES:
                raise DoubaoSpeakerPreviewUnavailable("豆包试听音频超过 5MB 限制")
            content = response.read(PREVIEW_MAX_BYTES + 1)
    except DoubaoSpeakerPreviewUnavailable:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        raise DoubaoSpeakerPreviewUnavailable(f"豆包试听下载失败：{exc}") from exc
    if len(content) > PREVIEW_MAX_BYTES:
        raise DoubaoSpeakerPreviewUnavailable("豆包试听音频超过 5MB 限制")
    if not content:
        raise DoubaoSpeakerPreviewUnavailable("豆包试听返回了空音频")

    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{key}{_preview_extension(content_type)}"
    path = directory / filename
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=directory, delete=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    _atomic_json(
        metadata_path,
        {"filename": filename, "content_type": content_type, "fetched_at": _iso(current), "source_url": speaker.trial_url},
    )
    return path, content_type
