from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import storage_maintenance  # noqa: E402


def _cache_file(root: Path, relative: str, content: bytes, timestamp: float) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (timestamp, timestamp))
    return path


def test_maintenance_applies_ttl_then_lru_only_to_rebuildable_caches(tmp_path):
    cache = tmp_path / "cache"
    expired = _cache_file(cache, "waveforms/expired.json", b"old", 100)
    least_recent = _cache_file(cache, "qwen-align/least.log", b"1234", 800)
    newest = _cache_file(cache, "provider-catalogs/new.json", b"5678", 900)
    preserved = {
        _cache_file(cache, "asr_uploads/source.wav", b"asr", 100),
        _cache_file(cache, "projects/project.json", b"project", 100),
        _cache_file(cache, "outputs/result.wav", b"output", 100),
        _cache_file(cache, "voices/voice.wav", b"voice", 100),
    }

    result = storage_maintenance.maintain_rebuildable_caches(
        cache,
        ttl_seconds=500,
        max_bytes=4,
        now=1000,
    )

    assert result["ttl_removed_files"] == 1
    assert result["lru_removed_files"] == 1
    assert result["after_bytes"] == 4
    assert not expired.exists()
    assert not least_recent.exists()
    assert newest.exists()
    assert all(path.exists() for path in preserved)


def test_maintenance_skips_file_and_directory_symlinks(tmp_path):
    cache = tmp_path / "cache"
    waveform_dir = cache / "waveforms"
    waveform_dir.mkdir(parents=True)
    outside_file = tmp_path / "outside.json"
    outside_file.write_bytes(b"outside")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    nested = outside_dir / "nested.json"
    nested.write_bytes(b"nested")
    (waveform_dir / "file-link.json").symlink_to(outside_file)
    (waveform_dir / "dir-link").symlink_to(outside_dir, target_is_directory=True)

    result = storage_maintenance.maintain_rebuildable_caches(
        cache,
        ttl_seconds=0,
        max_bytes=0,
        now=1000,
    )

    assert result["skipped_symlinks"] == 2
    assert result["removed_files"] == 0
    assert outside_file.read_bytes() == b"outside"
    assert nested.read_bytes() == b"nested"


def test_maintenance_skips_symlinked_cache_root(tmp_path):
    outside_cache = tmp_path / "outside-cache"
    old = _cache_file(outside_cache, "waveforms/old.json", b"old", 100)
    linked_cache = tmp_path / "linked-cache"
    linked_cache.symlink_to(outside_cache, target_is_directory=True)

    result = storage_maintenance.maintain_rebuildable_caches(
        linked_cache,
        ttl_seconds=0,
        max_bytes=0,
        now=1000,
    )

    assert result["skipped_symlinks"] == 1
    assert result["scanned_files"] == 0
    assert old.exists()


def test_startup_maintenance_is_configurable_and_failure_safe(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    old = _cache_file(cache, "waveforms/old.json", b"old", 100)
    monkeypatch.setattr(storage_maintenance.settings_store, "cache_dir", lambda: cache)
    monkeypatch.setenv("VOICE_STUDIO_CACHE_MAINTENANCE_TTL_SECONDS", "10")
    monkeypatch.setenv("VOICE_STUDIO_CACHE_MAINTENANCE_MAX_BYTES", "0")
    monkeypatch.setattr(storage_maintenance.custom_reference_store, "cleanup_orphaned_uploads", lambda **kwargs: [])
    monkeypatch.setattr(storage_maintenance.seed_asset_store, "cleanup_orphaned_assets", lambda **kwargs: [])

    result = storage_maintenance.run_startup_maintenance()

    assert result["removed_files"] == 1
    assert not old.exists()

    monkeypatch.setattr(storage_maintenance, "maintain_rebuildable_caches", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")))
    failed = storage_maintenance.run_startup_maintenance()
    assert failed["enabled"] is True
    assert failed["errors"]
