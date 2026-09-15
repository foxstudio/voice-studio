"""Safe resolution of managed local inputs for Seed Audio.

The public request contract accepts Base64, while the application stores only
opaque ``voice_id``/``file_id`` references.  This module is the narrow boundary
between those two representations: paths never come from request data and
Base64 exists only while an outbound request is being built.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.services import audio_tools, seed_asset_store, settings_store, voice_store

from .schemas import SeedAudioReference
from .validation import MAX_REFERENCE_AUDIO_SECONDS, MAX_REFERENCE_BYTES


SeedAudioMediaKind = Literal["audio", "image"]
SeedAudioAssetSource = Literal["voice_library", "upload", "preset"]

_AUDIO_FORMATS = {
    ".wav": "wav",
    ".mp3": "mp3",
    ".pcm": "pcm",
    ".ogg": "ogg_opus",
    ".opus": "ogg_opus",
}
_IMAGE_FORMATS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp"}
_CLOUD_ALLOWED_LICENSES = frozenset({"self_voice", "authorized", "company_authorized"})


class SeedAudioAssetError(ValueError):
    """Expected, user-correctable error while resolving a managed asset."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManagedSeedAudioAsset:
    """Validated input whose local path remains an implementation detail."""

    source: SeedAudioAssetSource
    media_kind: SeedAudioMediaKind
    file_id: str
    voice_id: str | None
    name: str
    media_format: str
    size_bytes: int
    duration_seconds: float | None
    license_status: str | None = None
    _content: bytes = field(repr=False, compare=False, default=b"")

    def build_reference(self) -> SeedAudioReference:
        """Read and encode only at the provider-call boundary, without a temp file."""

        encoded = base64.b64encode(self._content).decode("ascii")
        metadata = {
            "media_format": self.media_format,
            "size_bytes": len(self._content),
        }
        if self.media_kind == "audio":
            return SeedAudioReference(
                audio_data=encoded,
                duration_seconds=self.duration_seconds,
                **metadata,
            )
        return SeedAudioReference(image_data=encoded, **metadata)

    def history_summary(self) -> dict[str, str | int | float | None]:
        """Return durable audit metadata, intentionally excluding path and Base64."""

        summary: dict[str, str | int | float | None] = {
            "source": self.source,
            "media_kind": self.media_kind,
            "file_id": self.file_id,
            "voice_id": self.voice_id,
            "name": self.name,
            "media_format": self.media_format,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
        }
        if self.license_status is not None:
            summary["license_status"] = self.license_status
        return summary


class SeedAudioAssetResolver:
    """Resolve opaque IDs through stores and enforce Seed Audio input limits."""

    def __init__(
        self,
        *,
        get_voice: Callable[[str], Any | None] = voice_store.get_voice,
        get_file: Callable[[str], Any | None] | None = None,
        managed_roots: Callable[[], Iterable[Path]] | None = None,
        audio_duration_probe: Callable[[Path], float] | None = None,
    ) -> None:
        self._get_voice = get_voice
        self._get_file = get_file or _get_managed_file
        self._managed_roots = managed_roots or (lambda: (settings_store.voice_dir(), seed_asset_store.asset_dir()))
        self._audio_duration_probe = audio_duration_probe or _probe_audio_duration

    def resolve_voice_audio(self, *, voice_id: str, file_id: str | None = None) -> ManagedSeedAudioAsset:
        """Resolve one audio file belonging to an upload-authorized library voice."""

        _validate_opaque_id(voice_id)
        if file_id is not None:
            _validate_opaque_id(file_id)

        voice = self._get_voice(voice_id)
        if voice is None:
            raise SeedAudioAssetError("VOICE_NOT_FOUND", "音色不存在")
        if _enum_value(getattr(voice, "license_status", None)) not in _CLOUD_ALLOWED_LICENSES:
            raise SeedAudioAssetError("ASSET_UPLOAD_NOT_AUTHORIZED", "该音色未授权上传到云端")

        reference_ids = tuple(getattr(voice, "reference_audio_ids", ()) or ())
        if not reference_ids:
            raise SeedAudioAssetError("VOICE_HAS_NO_REFERENCE", "该音色没有参考音频")
        selected_file_id = file_id or reference_ids[0]
        _validate_opaque_id(selected_file_id)
        if selected_file_id not in reference_ids:
            raise SeedAudioAssetError("VOICE_FILE_MISMATCH", "指定文件不属于所选音色")

        return self._resolve_file(
            file_id=selected_file_id,
            media_kind="audio",
            source="voice_library",
            voice_id=voice_id,
            authorized=True,
        )

    def resolve_upload(
        self,
        *,
        file_id: str,
        media_kind: SeedAudioMediaKind,
        authorized: bool,
        source: Literal["upload", "preset"] = "upload",
    ) -> ManagedSeedAudioAsset:
        """Resolve a registered custom upload after explicit upload authorization."""

        _validate_opaque_id(file_id)
        if media_kind not in {"audio", "image"}:
            raise SeedAudioAssetError("INVALID_MEDIA_KIND", "素材类型必须是 audio 或 image")
        if source not in {"upload", "preset"}:
            raise SeedAudioAssetError("INVALID_ASSET_SOURCE", "素材来源必须是 upload 或 preset")
        return self._resolve_file(
            file_id=file_id,
            media_kind=media_kind,
            source=source,
            voice_id=None,
            authorized=authorized,
        )

    def _resolve_file(
        self,
        *,
        file_id: str,
        media_kind: SeedAudioMediaKind,
        source: SeedAudioAssetSource,
        voice_id: str | None,
        authorized: bool,
    ) -> ManagedSeedAudioAsset:
        record = self._get_file(file_id)
        if record is None:
            raise SeedAudioAssetError("ASSET_NOT_FOUND", "受管理素材不存在")

        record_source = _enum_value(getattr(record, "source", None)) or None
        record_license = _enum_value(getattr(record, "license_status", None)) or None
        is_seed_record = _enum_value(getattr(record, "asset_type", None)) == "seed_audio_image"
        if is_seed_record:
            if record_source not in {"upload", "preset"} or source != record_source:
                raise SeedAudioAssetError("ASSET_SOURCE_MISMATCH", "请求中的素材来源与受管理记录不一致")
            if record_license not in _CLOUD_ALLOWED_LICENSES:
                raise SeedAudioAssetError("ASSET_UPLOAD_NOT_AUTHORIZED", "受管理素材记录未授权上传到云端")
            source = record_source
        elif not authorized:
            raise SeedAudioAssetError("ASSET_UPLOAD_NOT_AUTHORIZED", "请先确认有权上传该素材到云端")

        raw_path = str(getattr(record, "path", "") or "")
        if not raw_path:
            raise SeedAudioAssetError("ASSET_FILE_MISSING", "素材文件不存在")
        path = Path(raw_path).expanduser()
        try:
            resolved_path = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SeedAudioAssetError("ASSET_FILE_MISSING", "素材文件不存在") from exc
        if not resolved_path.is_file():
            raise SeedAudioAssetError("ASSET_FILE_MISSING", "素材文件不存在")
        if not _is_within_managed_roots(resolved_path, self._managed_roots()):
            raise SeedAudioAssetError("ASSET_PATH_NOT_MANAGED", "素材文件不在受管理目录中")

        format_map = _AUDIO_FORMATS if media_kind == "audio" else _IMAGE_FORMATS
        media_format = format_map.get(resolved_path.suffix.lower())
        if media_format is None:
            label = "音频" if media_kind == "audio" else "图片"
            raise SeedAudioAssetError("UNSUPPORTED_ASSET_FORMAT", f"不支持的参考{label}格式")
        record_format = _enum_value(getattr(record, "media_format", None)) or None
        if is_seed_record and record_format != media_format:
            raise SeedAudioAssetError("ASSET_FORMAT_MISMATCH", "受管理记录中的格式与文件不一致")

        try:
            stat_before = resolved_path.stat()
            content = resolved_path.read_bytes()
        except OSError as exc:
            raise SeedAudioAssetError("ASSET_FILE_MISSING", "素材文件不存在") from exc
        size_bytes = len(content)
        if size_bytes <= 0:
            raise SeedAudioAssetError("ASSET_FILE_EMPTY", "参考素材不能为空")
        if size_bytes > MAX_REFERENCE_BYTES:
            raise SeedAudioAssetError("ASSET_TOO_LARGE", "参考素材不能超过 10 MB")

        duration_seconds: float | None = None
        if media_kind == "audio":
            try:
                duration_seconds = float(self._audio_duration_probe(resolved_path))
            except SeedAudioAssetError:
                raise
            except Exception as exc:
                raise SeedAudioAssetError("AUDIO_UNREADABLE", "无法读取参考音频时长") from exc
            if duration_seconds <= 0:
                raise SeedAudioAssetError("AUDIO_UNREADABLE", "参考音频时长无效")
            if duration_seconds > MAX_REFERENCE_AUDIO_SECONDS:
                raise SeedAudioAssetError("AUDIO_TOO_LONG", "参考音频不能超过 30 秒")
        else:
            try:
                decoded_format = seed_asset_store.decode_image_format(content)
            except seed_asset_store.SeedAssetStoreError as exc:
                raise SeedAudioAssetError(exc.code, str(exc)) from exc
            if decoded_format != media_format:
                raise SeedAudioAssetError("ASSET_FORMAT_MISMATCH", "图片内容与受管理格式不一致")

        try:
            stat_after = resolved_path.stat()
        except OSError as exc:
            raise SeedAudioAssetError("ASSET_CHANGED", "素材在校验期间发生变化，请重试") from exc
        if _stat_identity(stat_before) != _stat_identity(stat_after) or stat_before.st_size != size_bytes:
            raise SeedAudioAssetError("ASSET_CHANGED", "素材在校验期间发生变化，请重试")

        return ManagedSeedAudioAsset(
            source=source,
            media_kind=media_kind,
            file_id=file_id,
            voice_id=voice_id,
            name=_safe_display_name(getattr(record, "original_name", ""), fallback=resolved_path.name),
            media_format=media_format,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
            license_status=record_license,
            _content=content,
        )


def _probe_audio_duration(path: Path) -> float:
    metadata = audio_tools.probe_audio(path)
    return float(metadata["duration_ms"]) / 1000.0


def _get_managed_file(file_id: str) -> Any | None:
    return voice_store.get_file(file_id) or seed_asset_store.get_asset(file_id)


def _validate_opaque_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SeedAudioAssetError("INVALID_ASSET_ID", "素材标识不能为空")
    if len(value) > 128 or value != value.strip() or value in {".", ".."} or "\x00" in value or "/" in value or "\\" in value:
        raise SeedAudioAssetError("INVALID_ASSET_ID", "素材标识不能包含路径")
    if Path(value).is_absolute():
        raise SeedAudioAssetError("INVALID_ASSET_ID", "素材标识不能是绝对路径")


def _is_within_managed_roots(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(Path(root).expanduser().resolve(strict=True))
        except (ValueError, OSError, RuntimeError):
            continue
        return True
    return False


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _safe_display_name(value: Any, *, fallback: str) -> str:
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return (name or fallback)[:255]


def _stat_identity(value) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
