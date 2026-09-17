"""废纸篓工具的测试。

默认用假的命令跑，不去动系统废纸篓；真实废纸篓那一条只在命令可用时执行，
并且会把文件恢复回临时目录，避免给系统留下垃圾。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import trash_bin


def _fake_run(calls: list[list[str]], *, fail_first: bool = False, fail_all: bool = False):
    """构造一个假的 subprocess.run：记录调用，并按需返回成功或失败。"""

    def run(command, **kwargs):
        calls.append(list(command))
        batch = len(command) - 1
        if fail_all:
            return subprocess.CompletedProcess(command, 1, "", "boom")
        if fail_first and batch > 1:
            # 批量调用失败，逐个调用成功 —— 用来验证回退重试
            return subprocess.CompletedProcess(command, 1, "", "too many files")
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_reports_available_command(monkeypatch):
    monkeypatch.setattr(trash_bin, "trash_command", lambda: ["/usr/bin/trash"])
    assert trash_bin.trash_available() is True


def test_reports_unavailable_command(monkeypatch):
    monkeypatch.setattr(trash_bin, "trash_command", lambda: None)
    assert trash_bin.trash_available() is False


def test_moves_existing_files_and_skips_missing(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(trash_bin, "trash_command", lambda: ["/usr/bin/trash"])
    monkeypatch.setattr(trash_bin.subprocess, "run", _fake_run(calls))

    real = tmp_path / "keep.wav"
    real.write_bytes(b"data")
    absent = tmp_path / "gone.wav"

    result = trash_bin.move_to_trash([real, absent])

    assert result.moved == [str(real)]
    assert result.failed == []
    # 不存在的路径不该出现在命令里
    assert calls == [["/usr/bin/trash", str(real)]]


def test_returns_everything_when_no_target_exists(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(trash_bin, "trash_command", lambda: ["/usr/bin/trash"])
    monkeypatch.setattr(trash_bin.subprocess, "run", _fake_run(calls))

    result = trash_bin.move_to_trash([tmp_path / "absent.wav"])

    assert result.moved == []
    assert result.failed == []
    assert calls == []


def test_unavailable_command_reports_failure_instead_of_deleting(tmp_path, monkeypatch):
    monkeypatch.setattr(trash_bin, "trash_command", lambda: None)
    target = tmp_path / "keep.wav"
    target.write_bytes(b"data")

    result = trash_bin.move_to_trash([target])

    assert result.moved == []
    assert len(result.failed) == 1
    assert result.failed[0][0] == str(target)
    assert target.exists(), "废纸篓不可用时不能把文件删掉"


def test_batches_large_input(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(trash_bin, "trash_command", lambda: ["/usr/bin/trash"])
    monkeypatch.setattr(trash_bin.subprocess, "run", _fake_run(calls))
    monkeypatch.setattr(trash_bin, "MAX_PATHS_PER_CALL", 10)

    files = []
    for index in range(23):
        path = tmp_path / f"clip{index}.wav"
        path.write_bytes(b"x")
        files.append(path)

    result = trash_bin.move_to_trash(files)

    assert result.moved_count == 23
    assert [len(call) - 1 for call in calls] == [10, 10, 3]


def test_retries_individually_when_batch_fails(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(trash_bin, "trash_command", lambda: ["/usr/bin/trash"])
    monkeypatch.setattr(trash_bin.subprocess, "run", _fake_run(calls, fail_first=True))

    files = []
    for index in range(3):
        path = tmp_path / f"clip{index}.wav"
        path.write_bytes(b"x")
        files.append(path)

    result = trash_bin.move_to_trash(files)

    # 一次批量失败 + 三次逐个成功
    assert result.moved_count == 3
    assert result.failed == []
    assert [len(call) - 1 for call in calls] == [3, 1, 1, 1]


def test_reports_reason_when_every_attempt_fails(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(trash_bin, "trash_command", lambda: ["/usr/bin/trash"])
    monkeypatch.setattr(trash_bin.subprocess, "run", _fake_run(calls, fail_all=True))

    target = tmp_path / "clip.wav"
    target.write_bytes(b"x")

    result = trash_bin.move_to_trash([target])

    assert result.moved == []
    assert len(result.failed) == 1
    assert "boom" in result.failed[0][1]
    assert target.exists()


def test_directory_contents_only_moves_children(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(trash_bin, "trash_command", lambda: ["/usr/bin/trash"])
    monkeypatch.setattr(trash_bin.subprocess, "run", _fake_run(calls))

    root = tmp_path / "cache"
    root.mkdir()
    (root / "a.wav").write_bytes(b"a")
    (root / "b.wav").write_bytes(b"b")

    result = trash_bin.trash_directory_contents(root)

    assert result.moved_count == 2
    assert root.exists(), "只清内容，目录本身保留"
    assert all(str(root) in path for path in result.moved)


def test_result_serialises_for_api(tmp_path, monkeypatch):
    monkeypatch.setattr(trash_bin, "trash_command", lambda: ["/usr/bin/trash"])
    monkeypatch.setattr(trash_bin.subprocess, "run", _fake_run([]))
    target = tmp_path / "clip.wav"
    target.write_bytes(b"x")

    payload = trash_bin.move_to_trash([target]).to_dict()

    assert payload["moved_count"] == 1
    assert payload["failed_count"] == 0
    assert payload["moved"] == [str(target)]


@pytest.mark.skipif(not trash_bin.trash_available(), reason="系统废纸篓命令不可用")
def test_real_trash_and_restore_round_trip(tmp_path):
    """真实走一遍系统废纸篓：移走、确认在里面、再恢复回来。"""
    import shutil

    trash_root = Path.home() / ".Trash"
    payload = b"RIFF-real-trash-probe"
    name = f"voice-studio-trash-probe-{tmp_path.name}.wav"
    source = tmp_path / name
    source.write_bytes(payload)
    trashed = trash_root / name
    try:
        result = trash_bin.move_to_trash([source])

        assert result.moved_count == 1, f"未能移入废纸篓：{result.failed}"
        assert not source.exists()
        assert trashed.exists(), "文件没有出现在系统废纸篓里"

        # 恢复：把文件搬回原位，内容必须原样
        shutil.move(str(trashed), str(source))
        assert source.read_bytes() == payload
    finally:
        # 无论断言是否失败，都不要在系统废纸篓里留垃圾
        if trashed.exists():
            shutil.move(str(trashed), str(source))
