from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migration" / "restore_voice_library.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup(tmp_path: Path) -> Path:
    backup = tmp_path / "backup"
    voice_files_dir = backup / "音色文件"
    voice_files_dir.mkdir(parents=True)
    audio = voice_files_dir / "file-a.wav"
    audio.write_bytes(b"RIFF-test-voice-data")
    readme = backup / "README.md"
    readme.write_text("test backup\n", encoding="utf-8")
    csv_path = backup / "音色库可读清单.csv"
    csv_path.write_text("音色名称,音色ID\n测试音色,voice-a\n", encoding="utf-8")
    manifest = {
        "schema_version": "voice-studio-voice-library-backup-v1",
        "created_at": "2026-09-01T00:00:00+08:00",
        "summary": {
            "voices": 1,
            "voice_file_records": 1,
            "physical_files": 1,
            "unregistered_or_legacy_files": 0,
            "total_bytes": audio.stat().st_size,
        },
        "voices": [
            {
                "voice_id": "voice-a",
                "name": "测试音色",
                "reference_audio_ids": ["file-a"],
            }
        ],
        "voice_files": [
            {
                "file_id": "file-a",
                "original_name": "source.wav",
                "path": "音色文件/file-a.wav",
                "mime_type": "audio/wav",
                "duration_ms": 1000,
                "sample_rate": 16000,
                "size_bytes": audio.stat().st_size,
                "created_at": "2026-09-01T00:00:00+08:00",
            }
        ],
        "unregistered_or_legacy_files": [],
    }
    manifest_path = backup / "音色库完整清单.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    included = [readme, csv_path, manifest_path, audio]
    checksum_lines = [
        f"{_sha256(path)}  {path.relative_to(backup)}"
        for path in included
    ]
    (backup / "SHA256SUMS.txt").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )
    return backup


def test_restore_verified_backup_into_fresh_data_root(tmp_path: Path):
    backup = _backup(tmp_path)
    data_dir = tmp_path / "VoiceStudio"
    report = tmp_path / "restore-report.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--backup-dir",
            str(backup),
            "--data-dir",
            str(data_dir),
            "--report",
            str(report),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (data_dir / "voices" / "file-a.wav").read_bytes() == b"RIFF-test-voice-data"
    restored_report = json.loads(report.read_text(encoding="utf-8"))
    assert restored_report["voices_restored"] == 1
    assert restored_report["voice_file_records_restored"] == 1
    with sqlite3.connect(data_dir / "config" / "voice_studio.db") as connection:
        voice_payload = json.loads(connection.execute("SELECT data FROM voices").fetchone()[0])
        file_payload = json.loads(connection.execute("SELECT data FROM voice_files").fetchone()[0])
    assert voice_payload["voice_id"] == "voice-a"
    assert file_payload["path"] == str(data_dir / "voices" / "file-a.wav")


def test_restore_refuses_to_overwrite_existing_voice_rows(tmp_path: Path):
    backup = _backup(tmp_path)
    data_dir = tmp_path / "VoiceStudio"
    command = [
        sys.executable,
        str(SCRIPT),
        "--backup-dir",
        str(backup),
        "--data-dir",
        str(data_dir),
    ]
    first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)

    assert first.returncode == 0
    assert second.returncode == 1
    assert "目标数据库已有音色数据" in second.stdout
