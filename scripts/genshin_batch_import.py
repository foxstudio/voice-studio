#!/usr/bin/env python3
"""批量导入原神可游玩角色到 Voice Studio（基于 genshin_analysis.json）。"""

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

API_BASE = "http://localhost:8000"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GENSHIN_DIR = Path.home() / "Desktop" / "音色下载" / "原神语音包6.3（中）"
GENSHIN_DIR = Path(os.environ.get("VOICE_STUDIO_GENSHIN_DIR", str(DEFAULT_GENSHIN_DIR)))
ANALYSIS_PATH = Path(os.environ.get("VOICE_STUDIO_GENSHIN_ANALYSIS", str(PROJECT_ROOT / "scripts" / "genshin_analysis.json")))
REPORT_PATH = Path(os.environ.get("VOICE_STUDIO_GENSHIN_IMPORT_REPORT", str(PROJECT_ROOT / "scripts" / "genshin_import_report.json")))

# 每个角色选多少条参考音频
REF_COUNT = 3
# 文件大小范围（字节）
MIN_WAV_SIZE = 100_000   # 100KB
MAX_WAV_SIZE = 1_500_000  # 1.5MB


def api_upload_wav(wav_path: str) -> str | None:
    filename = os.path.basename(wav_path)
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    with open(wav_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        f"{API_BASE}/api/voices/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    return result.get("file_id", "") or None


def api_post_json(path: str, data: dict) -> dict:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def api_get(path: str) -> list | dict:
    resp = urllib.request.urlopen(f"{API_BASE}{path}", timeout=10)
    return json.loads(resp.read())


def get_existing_names() -> set[str]:
    voices = api_get("/api/voices")
    names = set()
    for v in voices:
        n = v.get("name", "")
        names.add(n)
        if "（" in n:
            names.add(n.split("（")[0])
        if "(" in n:
            names.add(n.split("(")[0].strip())
    return names


def select_diverse_wavs(wav_files: list[Path], count: int = REF_COUNT) -> list[Path]:
    """按文件大小选 diverse WAV：小 / 中 / 大"""
    valid = []
    for w in wav_files:
        try:
            size = w.stat().st_size
            if MIN_WAV_SIZE <= size <= MAX_WAV_SIZE:
                valid.append((w, size))
        except OSError:
            continue

    if not valid:
        valid = [(w, w.stat().st_size) for w in wav_files if w.stat().st_size > 10_000]

    if not valid:
        return []

    valid.sort(key=lambda x: x[1])

    if len(valid) <= count:
        return [w for w, _ in valid]

    # 均匀采样：小/中/大
    indices = sorted(set([0, len(valid) // 2, len(valid) - 1]))
    return [valid[i][0] for i in indices[:count]]


def main():
    print("=" * 70)
    print("原神可游玩角色 → Voice Studio 批量导入")
    print(f"策略: 每角色 {REF_COUNT} 条参考音频 (大小: {MIN_WAV_SIZE//1000}KB–{MAX_WAV_SIZE//1000}KB)")
    print("=" * 70)

    with open(ANALYSIS_PATH, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    importable = [c for c in analysis["matched_characters"] if c.get("importable")]
    print(f"\n📊 可导入角色: {len(importable)} 个")

    existing = get_existing_names()
    print(f"📊 Voice Studio 已有音色: {len(existing)} 个名称\n")

    results = {"success": [], "skipped": [], "failed": []}

    for i, char in enumerate(importable, 1):
        cn_name = char["name"]
        voice_name = f"{cn_name}（原神）"

        if voice_name in existing or cn_name in existing:
            print(f"  [{i}/{len(importable)}] ⏭️ {voice_name} — 已存在，跳过")
            results["skipped"].append(voice_name)
            continue

        source_dir = GENSHIN_DIR / char["source_dir"]
        if not source_dir.exists():
            print(f"  [{i}/{len(importable)}] ❌ {voice_name} — 目录不存在: {source_dir}")
            results["failed"].append({"name": voice_name, "reason": f"目录不存在: {char['source_dir']}"})
            continue

        wav_files = sorted(source_dir.glob("*.wav"))
        if not wav_files:
            print(f"  [{i}/{len(importable)}] ❌ {voice_name} — 无 WAV 文件")
            results["failed"].append({"name": voice_name, "reason": "无WAV文件"})
            continue

        selected = select_diverse_wavs(wav_files, REF_COUNT)

        if not selected:
            print(f"  [{i}/{len(importable)}] ❌ {voice_name} — 无合适 WAV")
            results["failed"].append({"name": voice_name, "reason": "无合适WAV"})
            continue

        print(f"  [{i}/{len(importable)}] 📤 {voice_name} ({len(wav_files)} WAVs, 选 {len(selected)} 条)")

        file_ids = []
        for wav in selected:
            try:
                fid = api_upload_wav(str(wav))
                if fid:
                    file_ids.append(fid)
                    print(f"           ✅ 上传 {wav.name[:50]} → {fid[:12]}...")
                else:
                    print(f"           ⚠️ 上传返回空: {wav.name[:50]}")
                time.sleep(0.2)
            except Exception as e:
                print(f"           ❌ 上传失败 {wav.name[:50]}: {e}")

        if not file_ids:
            print(f"           ❌ 全部上传失败，跳过注册")
            results["failed"].append({"name": voice_name, "reason": "全部上传失败"})
            continue

        voice_data = {
            "name": voice_name,
            "voice_type": "virtual_character",
            "description": char["description"],
            "default_language": "zh",
            "tags": char["tags"],
            "reference_audio_ids": file_ids,
            "reference_text": "",
            "recommended_engine_id": "indextts-v2",
            "license_status": "test_only",
        }

        try:
            result = api_post_json("/api/voices", voice_data)
            vid = result.get("voice_id", "")
            print(f"           🎤 注册成功 → {vid[:12]}... ({len(file_ids)} refs)")
            results["success"].append({
                "name": voice_name,
                "voice_id": vid,
                "reference_count": len(file_ids),
                "wav_count": len(wav_files),
                "source_dir": char["source_dir"],
                "element": char["element"],
                "gender": char["gender"],
                "va_cn": char.get("va_cn", ""),
            })
        except Exception as e:
            print(f"           ❌ 注册失败: {e}")
            results["failed"].append({"name": voice_name, "reason": f"注册失败: {e}"})

        time.sleep(0.3)

    print(f"\n{'='*70}")
    print(f"导入完成！成功: {len(results['success'])} | 跳过: {len(results['skipped'])} | 失败: {len(results['failed'])}")
    print(f"{'='*70}")

    if results["success"]:
        print(f"\n✅ 成功导入 ({len(results['success'])} 个):")
        for r in results["success"]:
            print(f"   {r['name']} | {r['element']} | {r['gender']} | {r['reference_count']}refs / {r['wav_count']}WAVs | CV:{r['va_cn']}")

    if results["skipped"]:
        print(f"\n⏭️ 跳过 ({len(results['skipped'])} 个): {', '.join(results['skipped'])}")

    if results["failed"]:
        print(f"\n❌ 失败 ({len(results['failed'])} 个):")
        for r in results["failed"]:
            print(f"   {r['name']}: {r['reason']}")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📄 报告已保存: {REPORT_PATH}")


if __name__ == "__main__":
    main()
