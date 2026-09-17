"""保留策略与过程产物清理的测试。

全部用假的废纸篓调用，不会动系统废纸篓，也不会碰用户的真实数据。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.schemas import AppSettings
from app.services import storage_retention, trash_bin


def _settings(tmp_path: Path, **overrides) -> AppSettings:
    base = {
        "data_dir": str(tmp_path),
        "cache_dir": str(tmp_path / "cache"),
        "output_dir": str(tmp_path / "outputs"),
        "voice_dir": str(tmp_path / "voices"),
        "export_dir": str(tmp_path / "exports"),
        "project_dir": str(tmp_path / "projects"),
        "model_dir": str(tmp_path / "models"),
    }
    base.update(overrides)
    return AppSettings(**base)


def _file(path: Path, *, age_days: float, size: int = 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def fake_trash(monkeypatch):
    """把移入废纸篓替换成记账调用，并记录被移走的路径。"""
    moved: list[str] = []

    def move(paths):
        result = trash_bin.TrashResult()
        for path in paths:
            candidate = Path(path)
            if candidate.exists():
                moved.append(str(candidate))
                result.moved.append(str(candidate))
        return result

    monkeypatch.setattr(trash_bin, "move_to_trash", move)
    monkeypatch.setattr(trash_bin, "trash_available", lambda: True)
    return moved


def test_policy_defaults_are_documented_values(tmp_path):
    settings = _settings(tmp_path)
    assert storage_retention.policy_days(settings, "rebuildable_cache") == 30
    assert storage_retention.policy_days(settings, "process_artifacts") == 30
    # 生成结果默认永不自动清理
    assert storage_retention.policy_days(settings, "generated_outputs") == 0


def test_policy_rejects_unknown_category(tmp_path):
    with pytest.raises(ValueError):
        storage_retention.policy_days(_settings(tmp_path), "not_a_category")


def test_scan_counts_only_files_older_than_policy(tmp_path):
    settings = _settings(tmp_path)
    _file(tmp_path / "cache" / "waveforms" / "old.json", age_days=40, size=2048)
    _file(tmp_path / "cache" / "waveforms" / "fresh.json", age_days=1, size=1024)

    row = storage_retention.scan_category(settings, "rebuildable_cache")

    assert row["total_files"] == 2
    assert row["reclaimable_files"] == 1
    assert row["reclaimable_bytes"] == 2048
    assert row["retention_days"] == 30


def test_scan_reports_nothing_reclaimable_when_policy_is_forever(tmp_path):
    settings = _settings(tmp_path, storage_retention_output_days=0)
    _file(tmp_path / "outputs" / "ancient.wav", age_days=500)

    row = storage_retention.scan_category(settings, "generated_outputs")

    assert row["total_files"] == 1
    assert row["reclaimable_files"] == 0


def test_scan_skips_output_diagnostics(tmp_path):
    settings = _settings(tmp_path)
    _file(tmp_path / "outputs" / "result.wav", age_days=100)
    _file(tmp_path / "outputs" / "diagnostics" / "probe.wav", age_days=100)

    row = storage_retention.scan_category(settings, "generated_outputs")

    assert row["total_files"] == 1, "诊断目录有自己的清理入口，不应算进生成结果"


def test_scan_ignores_symlinks(tmp_path):
    settings = _settings(tmp_path)
    target = _file(tmp_path / "outside" / "real.wav", age_days=100)
    link = tmp_path / "cache" / "waveforms" / "link.wav"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)

    row = storage_retention.scan_category(settings, "rebuildable_cache")

    assert row["total_files"] == 0, "符号链接不应被当成可清理的缓存文件"
    assert target.exists()


def test_cleanup_trashes_only_expired_files(tmp_path, fake_trash):
    settings = _settings(tmp_path, storage_retention_output_days=30)
    old = _file(tmp_path / "outputs" / "old.wav", age_days=45)
    fresh = _file(tmp_path / "outputs" / "fresh.wav", age_days=2)

    result = storage_retention.cleanup_category(settings, "generated_outputs")

    assert result["trashed_files"] == 1
    assert result["trashed_bytes"] == 1024
    assert fake_trash == [str(old)]
    assert old.exists() and fresh.exists(), "测试用的是假废纸篓，文件本身不动"


def test_cleanup_skips_category_with_forever_policy(tmp_path, fake_trash):
    settings = _settings(tmp_path, storage_retention_output_days=0)
    _file(tmp_path / "outputs" / "ancient.wav", age_days=900)

    result = storage_retention.cleanup_category(settings, "generated_outputs")

    assert result["trashed_files"] == 0
    assert result["skipped"]
    assert fake_trash == []


def test_cleanup_keeps_files_when_trash_unavailable(tmp_path, monkeypatch):
    settings = _settings(tmp_path, storage_retention_output_days=1)
    old = _file(tmp_path / "outputs" / "old.wav", age_days=10)
    monkeypatch.setattr(trash_bin, "trash_available", lambda: False)

    result = storage_retention.cleanup_category(settings, "generated_outputs")

    assert result["trashed_files"] == 0
    assert result["failed"], "废纸篓不可用时要报告原因"
    assert old.exists(), "废纸篓不可用时不能删掉文件"


def test_run_all_returns_per_category_results(tmp_path, fake_trash):
    settings = _settings(tmp_path, storage_retention_cache_days=5, storage_retention_output_days=5)
    _file(tmp_path / "cache" / "waveforms" / "old.json", age_days=10)
    _file(tmp_path / "outputs" / "old.wav", age_days=10)

    result = storage_retention.run_all(settings, categories=["rebuildable_cache", "generated_outputs"])

    assert {item["key"] for item in result["categories"]} == {"rebuildable_cache", "generated_outputs"}
    assert result["trashed_files"] == 2
    assert result["trash_available"] is True


def test_run_all_rejects_unknown_category(tmp_path, fake_trash):
    with pytest.raises(ValueError):
        storage_retention.run_all(_settings(tmp_path), categories=["nope"])


def test_report_covers_every_category(tmp_path):
    rows = storage_retention.report(_settings(tmp_path))

    assert [row["key"] for row in rows] == [category.key for category in storage_retention.CATEGORIES]
    assert all("reclaimable_bytes" in row for row in rows)
    assert all("retention_days" in row for row in rows)
