#!/usr/bin/env python3
"""批量导入 Desktop/音色下载 中的角色 WAV 到 Voice Studio。"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE_DIR = Path(os.environ.get("VOICE_STUDIO_VOICE_SOURCE_DIR", str(Path.home() / "Desktop" / "音色下载")))
API_BASE = "http://localhost:8000"

# ── 角色元数据 ──────────────────────────────────────────────

CHARACTER_META = {
    # A-SOUL 成员
    "贝拉": {"gender": "female", "voice_type": "virtual_character", "source": "A-SOUL", "tags": ["虚拟主播", "A-SOUL", "女声"]},
    "嘉然": {"gender": "female", "voice_type": "virtual_character", "source": "A-SOUL", "tags": ["虚拟主播", "A-SOUL", "女声"]},
    "乃琳": {"gender": "female", "voice_type": "virtual_character", "source": "A-SOUL", "tags": ["虚拟主播", "A-SOUL", "女声"]},
    "向晚": {"gender": "female", "voice_type": "virtual_character", "source": "A-SOUL", "tags": ["虚拟主播", "A-SOUL", "女声"]},
    # VTuber / 虚拟主播
    "阿梓": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "冰糖": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "东雪莲": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "米诺": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "奶绿": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "尼奈": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "七海": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "扇宝": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "恬豆": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "文静": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "星瞳": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    "塔菲": {"gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
    # 游戏角色
    "爱莉希雅": {"gender": "female", "voice_type": "virtual_character", "source": "崩坏3", "tags": ["游戏角色", "崩坏3", "女声", "二次元"]},
    "涂山苏苏": {"gender": "female", "voice_type": "virtual_character", "source": "狐妖小红娘", "tags": ["动漫角色", "女声", "二次元"]},
    # 网红 / 真人
    "蔡徐坤": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声"]},
    "陈泽": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声"]},
    "电棍": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声"]},
    "丁真": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声"]},
    "科比": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声", "英文"]},
    "李老八": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声"]},
    "山泥若": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声"]},
    "孙笑川": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声"]},
    "炫神": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声"]},
    "张顺飞": {"gender": "male", "voice_type": "real_person", "source": "真人", "tags": ["真人", "男声"]},
}

CN_PREFIX_FOLDERS = {
    "[中文] 男 懒羊羊": {"name": "懒羊羊", "gender": "male", "voice_type": "virtual_character", "source": "喜羊羊与灰太狼", "tags": ["动漫角色", "男声", "二次元"]},
    "[中文] 女 苍风凌冽": {"name": "苍风凌冽", "gender": "female", "voice_type": "virtual_character", "source": "虚拟UP主", "tags": ["虚拟UP主", "女声"]},
    "[中文] 女 东海帝皇": {"name": "东海帝皇", "gender": "female", "voice_type": "virtual_character", "source": "虚拟UP主", "tags": ["虚拟UP主", "女声"]},
    "[中文] 女 哈娜酱": {"name": "哈娜酱", "gender": "female", "voice_type": "virtual_character", "source": "虚拟UP主", "tags": ["虚拟UP主", "女声"]},
    "[中文] 女 互联网福尔摩斯鱼老师": {"name": "鱼老师", "gender": "female", "voice_type": "virtual_character", "source": "虚拟UP主", "tags": ["虚拟UP主", "女声"]},
    "[中文] 女 欣宝(1)": {"name": "欣宝", "gender": "female", "voice_type": "virtual_character", "source": "虚拟UP主", "tags": ["虚拟UP主", "女声"]},
    "[中文] 女 显卡姬阿狸": {"name": "显卡姬阿狸", "gender": "female", "voice_type": "virtual_character", "source": "虚拟UP主", "tags": ["虚拟UP主", "女声"]},
}

SPECIAL_FOLDERS = {
    "扇宝（卖卖）": {"name": "扇宝（卖卖）", "gender": "female", "voice_type": "virtual_character", "source": "VTuber", "tags": ["虚拟主播", "女声"]},
}


def get_meta(folder_name: str) -> dict:
    if folder_name in CHARACTER_META:
        return {**CHARACTER_META[folder_name], "name": folder_name}
    if folder_name in CN_PREFIX_FOLDERS:
        return CN_PREFIX_FOLDERS[folder_name]
    if folder_name in SPECIAL_FOLDERS:
        return SPECIAL_FOLDERS[folder_name]
    return {"name": folder_name, "gender": "unknown", "voice_type": "virtual_character", "source": "未知", "tags": []}


def extract_text_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    if stem.startswith("【"):
        bracket_end = stem.find("】")
        if bracket_end != -1:
            return stem[bracket_end + 1:]
    if re.match(r"Ruo_\d+", stem):
        return ""
    if re.match(r"WaiMai_\d+", stem):
        return ""
    return stem


def extract_emotion_from_filename(filename: str) -> str | None:
    stem = Path(filename).stem
    m = re.match(r"【(.+?)】", stem)
    return m.group(1) if m else None


def api_get(path: str) -> dict | list:
    resp = urllib.request.urlopen(f"{API_BASE}{path}")
    return json.loads(resp.read())


def api_post_json(path: str, data: dict) -> dict:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def api_upload_wav(wav_path: str) -> str:
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
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    return result.get("file_id", "")


def get_existing_voices() -> set[str]:
    voices = api_get("/api/voices")
    names = set()
    for v in voices:
        n = v.get("name", "")
        names.add(n)
        if "（" in n:
            names.add(n.split("（")[0])
        if "(" in n:
            names.add(n.split("(")[0])
    return names


def main():
    print("=== Voice Studio 批量音色导入 ===\n")

    existing = get_existing_voices()
    print(f"Voice Studio 已有 {len(existing)} 个音色名称\n")

    results = {"success": [], "skipped": [], "failed": []}

    all_dirs = []
    for entry in sorted(BASE_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        if entry.name in ("原神", "绝区零", "诗歌剧"):
            continue
        wavs = list(entry.glob("*.wav"))
        if not wavs:
            continue
        all_dirs.append(entry)

    total_wavs = sum(len(list(d.glob("*.wav"))) for d in all_dirs)
    print(f"找到 {len(all_dirs)} 个角色文件夹，共 {total_wavs} 条 WAV\n")

    for char_dir in all_dirs:
        folder_name = char_dir.name
        meta = get_meta(folder_name)
        char_name = meta["name"]

        if char_name in existing:
            print(f"  ⏭️  {char_name} — 已存在，跳过")
            results["skipped"].append(char_name)
            continue

        wavs = sorted(char_dir.glob("*.wav"))
        if not wavs:
            print(f"  ⏭️  {char_name} — 无 WAV 文件")
            results["skipped"].append(char_name)
            continue

        # 选最佳参考音频：有文本 + 大小适中
        best_wav = None
        best_text = ""
        best_emotion = None

        for wav in wavs:
            text = extract_text_from_filename(wav.name)
            emotion = extract_emotion_from_filename(wav.name)
            size = wav.stat().st_size
            if text and 300_000 < size < 1_000_000:
                best_wav = wav
                best_text = text
                best_emotion = emotion
                break

        if not best_wav:
            for wav in wavs:
                text = extract_text_from_filename(wav.name)
                if text:
                    best_wav = wav
                    best_text = text
                    best_emotion = extract_emotion_from_filename(wav.name)
                    break
        if not best_wav:
            best_wav = wavs[0]
            best_text = ""
            best_emotion = None

        print(f"  📤 {char_name} ({len(wavs)} WAVs) → {best_wav.name[:60]}")

        try:
            audio_id = api_upload_wav(str(best_wav))
            if not audio_id:
                print(f"     ❌ 上传失败：未返回 audio_id")
                results["failed"].append({"name": char_name, "reason": "上传失败"})
                continue

            tags = list(meta.get("tags", []))
            tags.append(f"source:desktop-local:{folder_name}")
            if best_emotion:
                tags.append(f"emotion:{best_emotion}")

            description = f"来源：桌面/音色下载/{folder_name}。"
            if meta["source"] not in ("未知", "真人"):
                description = f"《{meta['source']}》角色参考音色。" + description
            description += "仅用于本地声音研究与测试。"

            voice_data = {
                "name": char_name,
                "voice_type": meta["voice_type"],
                "description": description,
                "default_language": "zh",
                "tags": tags,
                "reference_audio_ids": [audio_id],
                "reference_text": best_text[:500] if best_text else "",
                "recommended_engine_id": "indextts-v2",
                "license_status": "test_only",
            }

            result = api_post_json("/api/voices", voice_data)
            voice_id = result.get("voice_id", "")
            print(f"     ✅ voice_id={voice_id}")
            results["success"].append({
                "name": char_name,
                "voice_id": voice_id,
                "audio_id": audio_id,
                "wav_count": len(wavs),
                "source": meta["source"],
                "gender": meta["gender"],
                "has_text": bool(best_text),
            })

            time.sleep(0.3)

        except Exception as e:
            print(f"     ❌ 失败: {e}")
            results["failed"].append({"name": char_name, "reason": str(e)})

    # 摘要
    print("\n" + "=" * 60)
    print(f"导入完成！成功: {len(results['success'])} | 跳过: {len(results['skipped'])} | 失败: {len(results['failed'])}")
    print("=" * 60)

    if results["success"]:
        print("\n✅ 成功导入：")
        for r in results["success"]:
            print(f"   {r['name']} ({r['gender']}, {r['source']}, {r['wav_count']}WAVs, text={'有' if r['has_text'] else '无'}) → {r['voice_id']}")

    if results["skipped"]:
        print(f"\n⏭️ 跳过: {', '.join(results['skipped'])}")

    if results["failed"]:
        print("\n❌ 失败：")
        for r in results["failed"]:
            print(f"   {r['name']}: {r['reason']}")

    report_path = Path(__file__).parent / "import_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    main()
