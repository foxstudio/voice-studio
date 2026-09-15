from __future__ import annotations

import base64
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.engines.seed_audio.assets import (  # noqa: E402
    SeedAudioAssetError,
    SeedAudioAssetResolver,
)


def _file(file_id: str, path: Path, *, original_name: str | None = None):
    return SimpleNamespace(
        file_id=file_id,
        path=str(path),
        original_name=original_name or path.name,
        size_bytes=path.stat().st_size if path.exists() else 0,
        duration_ms=None,
    )


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(10, 20, 30)).save(output, format=image_format)
    return output.getvalue()


def _resolver(root: Path, *, voices: dict | None = None, files: dict | None = None, duration: float = 2.5):
    voices = voices or {}
    files = files or {}
    return SeedAudioAssetResolver(
        get_voice=voices.get,
        get_file=files.get,
        managed_roots=lambda: (root,),
        audio_duration_probe=lambda _path: duration,
    )


def test_resolves_designated_voice_library_file(tmp_path: Path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"wav-one")
    second.write_bytes(b"mp3-two")
    voice = SimpleNamespace(
        voice_id="voice-1",
        name="示例音色",
        license_status="self_voice",
        reference_audio_ids=["file-1", "file-2"],
    )
    resolver = _resolver(
        tmp_path,
        voices={"voice-1": voice},
        files={"file-1": _file("file-1", first), "file-2": _file("file-2", second)},
    )

    asset = resolver.resolve_voice_audio(voice_id="voice-1", file_id="file-2")

    assert asset.file_id == "file-2"
    assert asset.voice_id == "voice-1"
    assert asset.media_format == "mp3"
    assert asset.duration_seconds == 2.5


@pytest.mark.parametrize("license_status", ["unknown", "restricted", "public_domain"])
def test_rejects_voice_without_cloud_upload_authorization(tmp_path: Path, license_status: str):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"voice")
    voice = SimpleNamespace(
        voice_id="voice-1",
        name="未授权",
        license_status=license_status,
        reference_audio_ids=["file-1"],
    )
    resolver = _resolver(tmp_path, voices={"voice-1": voice}, files={"file-1": _file("file-1", audio)})

    with pytest.raises(SeedAudioAssetError, match="未授权") as exc:
        resolver.resolve_voice_audio(voice_id="voice-1")
    assert exc.value.code == "ASSET_UPLOAD_NOT_AUTHORIZED"


def test_rejects_file_not_owned_by_selected_voice(tmp_path: Path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"voice")
    voice = SimpleNamespace(
        voice_id="voice-1",
        name="音色",
        license_status="authorized",
        reference_audio_ids=["file-1"],
    )
    resolver = _resolver(tmp_path, voices={"voice-1": voice}, files={"file-2": _file("file-2", audio)})

    with pytest.raises(SeedAudioAssetError, match="不属于") as exc:
        resolver.resolve_voice_audio(voice_id="voice-1", file_id="file-2")
    assert exc.value.code == "VOICE_FILE_MISMATCH"


@pytest.mark.parametrize("unsafe_id", ["../file-1", "/tmp/file-1", r"..\file-1", "folder/file-1"])
def test_rejects_path_like_identifiers_before_lookup(tmp_path: Path, unsafe_id: str):
    resolver = _resolver(tmp_path)

    with pytest.raises(SeedAudioAssetError, match="标识") as exc:
        resolver.resolve_upload(file_id=unsafe_id, media_kind="audio", authorized=True)
    assert exc.value.code == "INVALID_ASSET_ID"


def test_custom_upload_requires_explicit_authorization(tmp_path: Path):
    audio = tmp_path / "upload.wav"
    audio.write_bytes(b"voice")
    resolver = _resolver(tmp_path, files={"upload-1": _file("upload-1", audio)})

    with pytest.raises(SeedAudioAssetError, match="确认") as exc:
        resolver.resolve_upload(file_id="upload-1", media_kind="audio", authorized=False)
    assert exc.value.code == "ASSET_UPLOAD_NOT_AUTHORIZED"


def test_rejects_missing_and_outside_managed_files(tmp_path: Path):
    root = tmp_path / "managed"
    root.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"voice")
    missing = root / "missing.wav"
    resolver = _resolver(
        root,
        files={"outside": _file("outside", outside), "missing": _file("missing", missing)},
    )

    with pytest.raises(SeedAudioAssetError) as outside_exc:
        resolver.resolve_upload(file_id="outside", media_kind="audio", authorized=True)
    assert outside_exc.value.code == "ASSET_PATH_NOT_MANAGED"

    with pytest.raises(SeedAudioAssetError) as missing_exc:
        resolver.resolve_upload(file_id="missing", media_kind="audio", authorized=True)
    assert missing_exc.value.code == "ASSET_FILE_MISSING"


@pytest.mark.parametrize("suffix", ["wav", "mp3", "pcm", "ogg", "opus"])
def test_accepts_supported_audio_formats(tmp_path: Path, suffix: str):
    audio = tmp_path / f"upload.{suffix}"
    audio.write_bytes(b"voice")
    resolver = _resolver(tmp_path, files={"upload": _file("upload", audio)})

    asset = resolver.resolve_upload(file_id="upload", media_kind="audio", authorized=True)

    expected = "ogg_opus" if suffix in {"ogg", "opus"} else suffix
    assert asset.media_format == expected


@pytest.mark.parametrize(("suffix", "expected"), [("jpg", "jpeg"), ("jpeg", "jpeg"), ("png", "png"), ("webp", "webp")])
def test_accepts_supported_image_formats(tmp_path: Path, suffix: str, expected: str):
    image = tmp_path / f"upload.{suffix}"
    image.write_bytes(_image_bytes("JPEG" if expected == "jpeg" else expected.upper()))
    resolver = _resolver(tmp_path, files={"upload": _file("upload", image)})

    asset = resolver.resolve_upload(file_id="upload", media_kind="image", authorized=True)

    assert asset.media_format == expected
    assert asset.duration_seconds is None


@pytest.mark.parametrize(("filename", "media_kind"), [("voice.m4a", "audio"), ("image.gif", "image")])
def test_rejects_unsupported_formats(tmp_path: Path, filename: str, media_kind: str):
    path = tmp_path / filename
    path.write_bytes(b"data")
    resolver = _resolver(tmp_path, files={"upload": _file("upload", path)})

    with pytest.raises(SeedAudioAssetError, match="格式") as exc:
        resolver.resolve_upload(file_id="upload", media_kind=media_kind, authorized=True)
    assert exc.value.code == "UNSUPPORTED_ASSET_FORMAT"


def test_rejects_oversize_and_overlong_assets(tmp_path: Path):
    oversized = tmp_path / "large.png"
    oversized.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    audio = tmp_path / "long.wav"
    audio.write_bytes(b"voice")
    files = {"large": _file("large", oversized), "long": _file("long", audio)}

    with pytest.raises(SeedAudioAssetError) as size_exc:
        _resolver(tmp_path, files=files).resolve_upload(file_id="large", media_kind="image", authorized=True)
    assert size_exc.value.code == "ASSET_TOO_LARGE"

    with pytest.raises(SeedAudioAssetError) as duration_exc:
        _resolver(tmp_path, files=files, duration=30.01).resolve_upload(
            file_id="long", media_kind="audio", authorized=True
        )
    assert duration_exc.value.code == "AUDIO_TOO_LONG"


def test_builds_reference_in_memory_and_history_summary_is_sanitized(tmp_path: Path):
    audio = tmp_path / "upload.wav"
    raw = b"private-audio-bytes"
    audio.write_bytes(raw)
    resolver = _resolver(tmp_path, files={"upload": _file("upload", audio, original_name="/private/我的声音.wav")})
    before = {item.name for item in tmp_path.iterdir()}

    asset = resolver.resolve_upload(file_id="upload", media_kind="audio", authorized=True)
    reference = asset.build_reference()
    summary = asset.history_summary()

    assert reference.audio_data == base64.b64encode(raw).decode("ascii")
    assert reference.image_data is None
    assert reference.size_bytes == len(raw)
    assert {item.name for item in tmp_path.iterdir()} == before
    assert summary == {
        "source": "upload",
        "media_kind": "audio",
        "file_id": "upload",
        "voice_id": None,
        "name": "我的声音.wav",
        "media_format": "wav",
        "size_bytes": len(raw),
        "duration_seconds": 2.5,
    }
    assert "path" not in summary
    assert "audio_data" not in summary
    assert "image_data" not in summary


def test_image_reference_uses_image_data_only(tmp_path: Path):
    image = tmp_path / "upload.webp"
    raw = _image_bytes("WEBP")
    image.write_bytes(raw)
    resolver = _resolver(tmp_path, files={"image-1": _file("image-1", image)})

    reference = resolver.resolve_upload(
        file_id="image-1", media_kind="image", authorized=True
    ).build_reference()

    assert reference.image_data == base64.b64encode(raw).decode("ascii")
    assert reference.audio_data is None
    assert reference.media_format == "webp"


def test_preset_image_keeps_preset_source_in_sanitized_summary(tmp_path: Path):
    image = tmp_path / "preset.png"
    image.write_bytes(_image_bytes("PNG"))
    resolver = _resolver(tmp_path, files={"preset-1": _file("preset-1", image)})

    asset = resolver.resolve_upload(
        file_id="preset-1",
        media_kind="image",
        authorized=True,
        source="preset",
    )

    assert asset.source == "preset"
    assert asset.history_summary()["source"] == "preset"


def test_rejects_file_changed_during_audio_validation(tmp_path: Path):
    audio = tmp_path / "upload.wav"
    audio.write_bytes(b"original-audio")

    def mutate_during_probe(path: Path) -> float:
        path.write_bytes(b"changed-audio-with-a-different-size")
        return 2.0

    resolver = SeedAudioAssetResolver(
        get_voice=lambda _voice_id: None,
        get_file={"upload": _file("upload", audio)}.get,
        managed_roots=lambda: (tmp_path,),
        audio_duration_probe=mutate_during_probe,
    )

    with pytest.raises(SeedAudioAssetError) as exc:
        resolver.resolve_upload(file_id="upload", media_kind="audio", authorized=True)
    assert exc.value.code == "ASSET_CHANGED"


def test_rejects_unreasonably_long_asset_identifier(tmp_path: Path):
    resolver = _resolver(tmp_path)

    with pytest.raises(SeedAudioAssetError) as exc:
        resolver.resolve_upload(file_id="x" * 129, media_kind="image", authorized=True)
    assert exc.value.code == "INVALID_ASSET_ID"
