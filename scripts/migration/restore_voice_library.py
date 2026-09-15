#!/usr/bin/env python3
"""Restore a verified Voice Studio voice-library backup into a fresh data root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCHEMA_VERSION = "voice-studio-voice-library-backup-v1"
MANIFEST_NAME = "音色库完整清单.json"
CHECKSUM_NAME = "SHA256SUMS.txt"
VOICE_FILES_DIR_NAME = "音色文件"
IGNORED_NAMES = {".DS_Store"}


class RestoreError(RuntimeError):
    """The backup cannot be restored without weakening integrity guarantees."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(raw_value: str) -> Path:
    relative = Path(raw_value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RestoreError(f"备份包含不安全路径：{raw_value}")
    return relative


def _read_checksums(backup_dir: Path) -> dict[Path, str]:
    checksum_path = backup_dir / CHECKSUM_NAME
    if not checksum_path.is_file():
        raise RestoreError(f"缺少校验文件：{CHECKSUM_NAME}")
    checksums: dict[Path, str] = {}
    for line_number, raw_line in enumerate(
        checksum_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            expected, raw_relative = line.split("  ", 1)
        except ValueError as exc:
            raise RestoreError(f"校验文件第 {line_number} 行格式不正确") from exc
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected.lower()):
            raise RestoreError(f"校验文件第 {line_number} 行 SHA256 不正确")
        relative = _safe_relative_path(raw_relative)
        if relative in checksums:
            raise RestoreError(f"校验文件包含重复路径：{relative}")
        checksums[relative] = expected.lower()
    if not checksums:
        raise RestoreError("校验文件为空")
    return checksums


def _included_backup_files(backup_dir: Path) -> set[Path]:
    included: set[Path] = set()
    for path in backup_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name == CHECKSUM_NAME or path.name in IGNORED_NAMES or path.name.startswith("._"):
            continue
        included.add(path.relative_to(backup_dir))
    return included


def verify_backup(backup_dir: Path) -> tuple[dict[str, Any], dict[Path, str]]:
    backup_dir = backup_dir.expanduser().resolve(strict=True)
    if not backup_dir.is_dir() or backup_dir.is_symlink():
        raise RestoreError("备份位置必须是真实目录，不能是软链接")
    checksums = _read_checksums(backup_dir)
    included = _included_backup_files(backup_dir)
    if included != set(checksums):
        missing = sorted(str(path) for path in set(checksums) - included)
        untracked = sorted(str(path) for path in included - set(checksums))
        raise RestoreError(
            "备份文件清单不一致："
            f"缺失={missing[:5]}，未登记={untracked[:5]}"
        )
    for relative, expected in checksums.items():
        source = backup_dir / relative
        if source.is_symlink() or not source.is_file():
            raise RestoreError(f"备份文件不可用：{relative}")
        actual = _sha256(source)
        if actual != expected:
            raise RestoreError(f"备份文件校验失败：{relative}")

    manifest_path = backup_dir / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RestoreError("音色库完整清单无法读取") from exc
    if manifest.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise RestoreError(
            f"不支持的音色备份版本：{manifest.get('schema_version')!r}"
        )
    voices = manifest.get("voices")
    voice_files = manifest.get("voice_files")
    if not isinstance(voices, list) or not isinstance(voice_files, list):
        raise RestoreError("音色清单缺少 voices 或 voice_files")

    voice_ids = [str(item.get("voice_id") or "") for item in voices]
    file_ids = [str(item.get("file_id") or "") for item in voice_files]
    if not all(voice_ids) or len(voice_ids) != len(set(voice_ids)):
        raise RestoreError("音色清单包含空或重复的 voice_id")
    if not all(file_ids) or len(file_ids) != len(set(file_ids)):
        raise RestoreError("音色文件清单包含空或重复的 file_id")
    known_file_ids = set(file_ids)
    for voice in voices:
        unknown = set(voice.get("reference_audio_ids") or []) - known_file_ids
        if unknown:
            raise RestoreError(
                f"音色 {voice.get('voice_id')} 引用了未登记文件：{sorted(unknown)}"
            )
    for voice_file in voice_files:
        relative = _safe_relative_path(str(voice_file.get("path") or ""))
        if not relative.parts or relative.parts[0] != VOICE_FILES_DIR_NAME:
            raise RestoreError(f"音色文件路径不在受管目录：{relative}")
        if relative not in checksums:
            raise RestoreError(f"音色文件没有 SHA256 记录：{relative}")

    summary = manifest.get("summary") or {}
    expected_summary = {
        "voices": len(voices),
        "voice_file_records": len(voice_files),
        "physical_files": len(
            [path for path in checksums if path.parts and path.parts[0] == VOICE_FILES_DIR_NAME]
        ),
    }
    for key, actual in expected_summary.items():
        if summary.get(key) != actual:
            raise RestoreError(
                f"备份摘要 {key}={summary.get(key)!r}，实际为 {actual}"
            )
    return manifest, checksums


def restore_voice_library(
    *,
    backup_dir: Path,
    data_dir: Path,
    report_path: Path | None = None,
) -> dict[str, Any]:
    backup_dir = backup_dir.expanduser().resolve(strict=True)
    data_dir = data_dir.expanduser().resolve(strict=False)
    manifest, checksums = verify_backup(backup_dir)

    os.environ["VOICE_STUDIO_DATA_DIR"] = str(data_dir)
    os.environ["VOICE_STUDIO_DB_PATH"] = str(data_dir / "config" / "voice_studio.db")
    backend_root = PROJECT_ROOT / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    from app.schemas.voice_studio import VoiceAsset, VoiceFile
    from app.services import database as db

    voice_dir = data_dir / "voices"
    voice_dir.mkdir(parents=True, exist_ok=True)
    with db.conn() as connection:
        existing_voices = int(connection.execute("SELECT COUNT(*) FROM voices").fetchone()[0])
        existing_files = int(connection.execute("SELECT COUNT(*) FROM voice_files").fetchone()[0])
    if existing_voices or existing_files:
        raise RestoreError(
            "目标数据库已有音色数据，已停止以免覆盖："
            f"voices={existing_voices}, voice_files={existing_files}"
        )

    staged_root = voice_dir / f".restore-staging-{uuid.uuid4().hex}"
    created_targets: list[Path] = []
    physical_relatives = sorted(
        path.relative_to(VOICE_FILES_DIR_NAME)
        for path in checksums
        if path.parts and path.parts[0] == VOICE_FILES_DIR_NAME
    )
    try:
        for relative in physical_relatives:
            source = backup_dir / VOICE_FILES_DIR_NAME / relative
            staged = staged_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
            expected = checksums[Path(VOICE_FILES_DIR_NAME) / relative]
            if _sha256(staged) != expected:
                raise RestoreError(f"复制后校验失败：{relative}")

        for relative in physical_relatives:
            staged = staged_root / relative
            target = voice_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                expected = checksums[Path(VOICE_FILES_DIR_NAME) / relative]
                if not target.is_file() or _sha256(target) != expected:
                    raise RestoreError(f"目标存在不同内容，拒绝覆盖：{target}")
                continue
            staged.replace(target)
            created_targets.append(target)

        validated_files: list[VoiceFile] = []
        for raw_file in manifest["voice_files"]:
            relative = _safe_relative_path(str(raw_file["path"]))
            payload = dict(raw_file)
            payload["path"] = str(voice_dir / relative.relative_to(VOICE_FILES_DIR_NAME))
            validated_files.append(VoiceFile(**payload))
        validated_voices = [VoiceAsset(**raw_voice) for raw_voice in manifest["voices"]]

        with db.conn() as connection:
            for voice_file in validated_files:
                db.upsert_from_connection(
                    connection,
                    "voice_files",
                    voice_file.file_id,
                    voice_file.model_dump(mode="json"),
                    time_field="created_at",
                )
            for voice in validated_voices:
                db.upsert_from_connection(
                    connection,
                    "voices",
                    voice.voice_id,
                    voice.model_dump(mode="json", exclude={"engine_bindings"}),
                )
    except Exception:
        for target in reversed(created_targets):
            target.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staged_root, ignore_errors=True)

    report = {
        "schema_version": "voice-studio-voice-library-restore-report-v1",
        "backup_schema_version": manifest["schema_version"],
        "backup_created_at": manifest.get("created_at"),
        "backup_dir": str(backup_dir),
        "data_dir": str(data_dir),
        "voice_dir": str(voice_dir),
        "database_path": str(db.DB_PATH),
        "checksum_files_verified": len(checksums),
        "voices_restored": len(manifest["voices"]),
        "voice_file_records_restored": len(manifest["voice_files"]),
        "physical_files_restored": len(physical_relatives),
        "total_bytes": sum((voice_dir / relative).stat().st_size for relative in physical_relatives),
    }
    if report_path is not None:
        report_path = report_path.expanduser().resolve(strict=False)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = report_path.with_suffix(f"{report_path.suffix}.tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(report_path)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio")),
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify checksums and the manifest; do not write the target data root.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.verify_only:
            manifest, checksums = verify_backup(args.backup_dir)
            result = {
                "schema_version": manifest["schema_version"],
                "voices": len(manifest["voices"]),
                "voice_file_records": len(manifest["voice_files"]),
                "checksum_files_verified": len(checksums),
            }
        else:
            result = restore_voice_library(
                backup_dir=args.backup_dir,
                data_dir=args.data_dir,
                report_path=args.report,
            )
    except (OSError, RestoreError, ValueError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"success": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
