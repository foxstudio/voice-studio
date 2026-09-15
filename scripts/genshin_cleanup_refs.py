#!/usr/bin/env python3
"""删除所有音色中多余的参考音频，只保留 [0]。

TTS 引擎只用 reference_audio_ids[0]，其余完全浪费空间。
此脚本：
1. 遍历所有有多条参考音频的音色
2. 保留 [0]，删除 [1+] 对应的 voice_files 记录和磁盘文件
3. 更新 reference_audio_ids 为只含 [0]
"""

import json
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path

API_BASE = "http://localhost:8000"
DB_PATH = Path.home() / "VoiceStudio" / "config" / "voice_studio.db"


def api_get(path):
    resp = urllib.request.urlopen(f"{API_BASE}{path}", timeout=15)
    return json.loads(resp.read())


def api_patch(path, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=body,
        headers={"Content-Type": "application/json"}, method="PATCH",
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def get_file_path(file_id: str) -> str | None:
    """从 voice_files 表获取文件路径"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT data FROM voice_files WHERE file_id = ?", (file_id,)).fetchone()
    db.close()
    if not row:
        return None
    data = json.loads(row["data"])
    return data.get("path")


def delete_file_record(file_id: str) -> bool:
    """删除 voice_files 记录和磁盘文件"""
    path_str = get_file_path(file_id)
    deleted_disk = False
    if path_str:
        p = Path(path_str)
        if p.exists():
            p.unlink()
            deleted_disk = True
    db = sqlite3.connect(DB_PATH)
    db.execute("DELETE FROM voice_files WHERE file_id = ?", (file_id,))
    db.commit()
    db.close()
    return deleted_disk


def main():
    print("=" * 70)
    print("清理多余参考音频 — 只保留 [0]")
    print("=" * 70)

    all_voices = api_get("/api/voices")
    multi = [v for v in all_voices if len(v.get("reference_audio_ids", [])) >= 2]
    total_extra = sum(len(v["reference_audio_ids"]) - 1 for v in multi)
    print(f"总音色: {len(all_voices)} | 多参考: {len(multi)} | 待删: {total_extra} 条\n")

    cleaned = 0
    disk_freed = 0
    db_freed = 0
    total_bytes = 0

    for i, voice in enumerate(multi, 1):
        name = voice["name"]
        vid = voice["voice_id"]
        ref_ids = voice.get("reference_audio_ids", [])
        keep = ref_ids[0]
        drop = ref_ids[1:]

        # 先删除文件和记录，再更新关联
        for fid in drop:
            path_str = get_file_path(fid)
            file_size = 0
            if path_str:
                p = Path(path_str)
                if p.exists():
                    file_size = p.stat().st_size
                    total_bytes += file_size

            deleted_disk = delete_file_record(fid)
            if deleted_disk:
                disk_freed += 1
            db_freed += 1

        # 更新 reference_audio_ids
        try:
            api_patch(f"/api/voices/{vid}", {"reference_audio_ids": [keep]})
        except Exception as e:
            print(f"  [{i}/{len(multi)}] ❌ {name}: 更新失败 {str(e)[:50]}")
            continue

        cleaned += 1
        if i % 20 == 0 or i == len(multi):
            print(f"  [{i}/{len(multi)}] 进度... 已清理 {cleaned} 个")

    print(f"\n{'='*70}")
    print(f"清理完成:")
    print(f"  音色: {cleaned}/{len(multi)}")
    print(f"  磁盘文件删除: {disk_freed} ({total_bytes / 1024 / 1024:.1f} MB)")
    print(f"  DB 记录删除: {db_freed}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
