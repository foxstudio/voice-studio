#!/usr/bin/env python3
"""检查所有原神音色的参考音频 ASR 质量。

对每条参考音频做 ASR 转录，分析：
1. 转录文本是否为空
2. 转录文本是否只有语气词（啊、嗯、哈等）
3. 转录文本是否太短（少于 4 个有效字符）
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

API_BASE = "http://localhost:8000"
REPORT_PATH = Path(__file__).parent / "genshin_asr_report.json"

# 无意义语气词模式
INTERJECTION_PATTERN = re.compile(
    r"^[啊哦嗯哈嘿呃唔哇噢咦呀哎唉哟嘟呜啵呵哼喵汪嗷噢]*[~～!！.。]*$"
)
# 纯标点/符号
PURE_SYMBOLS = re.compile(r"^[^a-zA-Z一-鿿0-9]*$")


def api_get(path: str):
    resp = urllib.request.urlopen(f"{API_BASE}{path}", timeout=15)
    return json.loads(resp.read())


def api_download(path: str) -> bytes:
    resp = urllib.request.urlopen(f"{API_BASE}{path}", timeout=30)
    return resp.read()


def api_asr(wav_bytes: bytes, filename: str, language: str = "zh") -> dict:
    """调用 ASR 转录接口"""
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    parts = []
    # file field
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    )
    body = parts[0].encode("utf-8") + wav_bytes
    # language field
    body += (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\n'
        f"{language}\r\n"
    ).encode("utf-8")
    # engine_id field
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="engine_id"\r\n\r\n'
        f"mimo-v2.5-asr\r\n"
    ).encode("utf-8")
    body += f"--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        f"{API_BASE}/api/asr/transcribe",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=60)
    return json.loads(resp.read())


def classify_text(text: str) -> str:
    """分析转录文本的质量类别"""
    if not text or not text.strip():
        return "empty"
    cleaned = text.strip()
    # 去掉标点和波浪号
    no_punct = re.sub(r"[~～!！.。，,？?、：:；;\x22\x27\s]", "", cleaned)
    if not no_punct:
        return "empty"
    if PURE_SYMBOLS.match(cleaned):
        return "empty"
    if INTERJECTION_PATTERN.match(no_punct):
        return "interjection"
    if len(no_punct) <= 3:
        return "too_short"
    return "good"


def main():
    print("=" * 70)
    print("原神音色参考音频 ASR 质量检查")
    print("=" * 70)

    # 获取所有音色
    all_voices = api_get("/api/voices")
    genshin_voices = [v for v in all_voices if "原神" in v.get("name", "")]
    print(f"📊 原神音色: {len(genshin_voices)} 个")
    print(f"📊 总音色: {len(all_voices)} 个\n")

    total_refs = sum(len(v.get("reference_audio_ids", [])) for v in genshin_voices)
    print(f"📊 需检查参考音频: {total_refs} 条\n")

    results = {
        "summary": {},
        "good_voices": [],        # 所有参考都 OK
        "warning_voices": [],     # 部分参考有问题
        "bad_voices": [],         # 所有参考都有问题
        "details": [],
    }

    stats = {"total_voices": len(genshin_voices), "total_refs": 0, "good": 0,
             "empty": 0, "interjection": 0, "too_short": 0, "asr_failed": 0}

    for vi, voice in enumerate(genshin_voices, 1):
        voice_name = voice["name"]
        voice_id = voice["voice_id"]
        ref_ids = voice.get("reference_audio_ids", [])

        if not ref_ids:
            print(f"  [{vi}/{len(genshin_voices)}] ⚠️ {voice_name} — 无参考音频")
            results["bad_voices"].append({"name": voice_name, "voice_id": voice_id, "reason": "无参考音频"})
            continue

        voice_detail = {
            "name": voice_name,
            "voice_id": voice_id,
            "refs": [],
            "issues": [],
        }

        for ri, file_id in enumerate(ref_ids, 1):
            stats["total_refs"] += 1
            # 下载音频
            try:
                wav_bytes = api_download(f"/api/voices/{voice_id}/audio/{file_id}")
            except Exception as e:
                print(f"  [{vi}/{len(genshin_voices)}] ❌ {voice_name} ref#{ri} — 下载失败: {e}")
                voice_detail["refs"].append({"file_id": file_id, "status": "download_failed", "error": str(e)})
                voice_detail["issues"].append(f"ref#{ri} 下载失败")
                stats["asr_failed"] += 1
                continue

            # ASR 转录
            try:
                asr_result = api_asr(wav_bytes, f"ref_{ri}.wav")
                asr_text = asr_result.get("text", "")
                duration_ms = asr_result.get("duration_ms")
            except Exception as e:
                err_str = str(e)
                print(f"  [{vi}/{len(genshin_voices)}] ❌ {voice_name} ref#{ri} — ASR 失败: {err_str[:80]}")
                voice_detail["refs"].append({"file_id": file_id, "status": "asr_failed", "error": err_str[:100]})
                voice_detail["issues"].append(f"ref#{ri} ASR失败")
                stats["asr_failed"] += 1
                continue

            category = classify_text(asr_text)
            stats[category] += 1

            ref_info = {
                "file_id": file_id,
                "status": category,
                "asr_text": asr_text,
                "duration_ms": duration_ms,
                "size_bytes": len(wav_bytes),
            }
            voice_detail["refs"].append(ref_info)

            if category != "good":
                emoji = {"empty": "🔴", "interjection": "🟡", "too_short": "🟠"}[category]
                label = {"empty": "空文本", "interjection": "纯语气词", "too_short": "过短"}[category]
                voice_detail["issues"].append(f"ref#{ri} {label}: 「{asr_text}」")
                print(f"  [{vi}/{len(genshin_voices)}] {emoji} {voice_name} ref#{ri} — {label}: 「{asr_text}」 ({duration_ms}ms)")
            else:
                # 只在第一个 good ref 时打印音色信息
                if ri == 1:
                    print(f"  [{vi}/{len(genshin_voices)}] ✅ {voice_name} — ref#{ri}: 「{asr_text[:30]}{'…' if len(asr_text) > 30 else ''}」")

            time.sleep(0.1)  # 避免 ASR 过载

        # 分类音色
        issue_count = len(voice_detail["issues"])
        good_count = len(voice_detail["refs"]) - issue_count

        if issue_count == 0:
            results["good_voices"].append(voice_detail)
        elif good_count > 0:
            results["warning_voices"].append(voice_detail)
        else:
            results["bad_voices"].append(voice_detail)

        results["details"].append(voice_detail)

        if vi % 10 == 0:
            print(f"\n  --- 进度: {vi}/{len(genshin_voices)} 音色, {stats['total_refs']} ASR ---\n")

    # 输出汇总
    print(f"\n{'='*70}")
    print(f"ASR 质量检查完成")
    print(f"{'='*70}")
    print(f"📊 检查音色: {stats['total_voices']} 个")
    print(f"📊 检查音频: {stats['total_refs']} 条")
    print(f"  ✅ 有效内容: {stats['good']} 条")
    print(f"  🔴 空转录:   {stats['empty']} 条")
    print(f"  🟡 纯语气词: {stats['interjection']} 条")
    print(f"  🟠 内容过短: {stats['too_short']} 条")
    print(f"  ❌ ASR失败:  {stats['asr_failed']} 条")
    print()
    print(f"🎤 音色状态:")
    print(f"  ✅ 全部OK:    {len(results['good_voices'])} 个")
    print(f"  ⚠️ 部分问题:  {len(results['warning_voices'])} 个")
    print(f"  ❌ 全部问题:  {len(results['bad_voices'])} 个")

    # 需要替换参考音频的音色
    needs_fix = results["warning_voices"] + results["bad_voices"]
    if needs_fix:
        print(f"\n{'='*70}")
        print(f"需要替换参考音频的音色 ({len(needs_fix)} 个):")
        print(f"{'='*70}")
        for v in needs_fix:
            issue_str = " | ".join(v["issues"])
            good_refs = sum(1 for r in v["refs"] if r.get("status") == "good")
            bad_refs = sum(1 for r in v["refs"] if r.get("status") != "good")
            print(f"  {v['name']}: {good_refs}✅ {bad_refs}❌ → {issue_str}")

    results["summary"] = stats

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📄 报告已保存: {REPORT_PATH}")


if __name__ == "__main__":
    main()
