#!/usr/bin/env python3
"""对问题音色做 ASR，找到有意义台词并更新 reference_text。

策略：
1. 对每个问题音色的 3 条参考音频做 ASR
2. 选出 ASR 文本最好的那条作为新 reference_text
3. 如果 3 条都不好，从源目录重新挑选更长的 WAV，替换参考音频
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

API_BASE = "http://localhost:8000"
DEFAULT_GENSHIN_DIR = Path.home() / "Desktop" / "音色下载" / "原神语音包6.3（中）"
GENSHIN_DIR = Path(os.environ.get("VOICE_STUDIO_GENSHIN_DIR", str(DEFAULT_GENSHIN_DIR)))

INTERJECTION_RE = re.compile(
    r"^[啊哦嗯哈嘿呃唔哇噢咦呀哎唉哟嘟呜啵呵哼喵汪嗷噢~～!！.。，,？?、：:；;\x22\x27\s]*$"
)
PURE_SYMBOL_RE = re.compile(r"^[^a-zA-Z一-鿿0-9]*$")

MIN_WAV_SIZE = 100_000
MAX_WAV_SIZE = 1_500_000


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


def api_download(path):
    resp = urllib.request.urlopen(f"{API_BASE}{path}", timeout=30)
    return resp.read()


def api_upload_wav(wav_path):
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
        f"{API_BASE}/api/voices/upload", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    return result.get("file_id", "") or None


def api_asr(wav_bytes, filename, language="zh"):
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + wav_bytes
    body += (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\n{language}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="engine_id"\r\n\r\nmimo-v2.5-asr\r\n'
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/api/asr/transcribe", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=60)
    return json.loads(resp.read())


def text_score(text):
    """给转录文本打分，越高越好"""
    if not text or not text.strip():
        return -1
    cleaned = re.sub(r"[~～!！.。，,？?、：:；;\x22\x27\s]", "", text.strip())
    if not cleaned or PURE_SYMBOL_RE.match(text.strip()):
        return -1
    if INTERJECTION_RE.match(text.strip()):
        return 0
    return len(cleaned)


def classify(text):
    if not text or not text.strip():
        return "empty"
    cleaned = re.sub(r"[~～!！.。，,？?、：:；;\x22\x27\s]", "", text.strip())
    if not cleaned:
        return "empty"
    if PURE_SYMBOL_RE.match(text.strip()):
        return "empty"
    if INTERJECTION_RE.match(text.strip()):
        return "interjection"
    if len(cleaned) <= 3:
        return "too_short"
    return "good"


def select_diverse_wavs(wav_files, count=3):
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
    indices = sorted(set([0, len(valid) // 2, len(valid) - 1]))
    return [valid[i][0] for i in indices[:count]]


def main():
    print("=" * 70)
    print("原神音色 — ASR 修复台词")
    print("=" * 70)

    all_voices = api_get("/api/voices")
    genshin = [v for v in all_voices if "原神" in v.get("name", "")]
    problems = [v for v in genshin if classify(v.get("reference_text", "")) != "good"]
    print(f"📊 原神音色: {len(genshin)} | 需修复: {len(problems)}\n")

    fixed = []
    still_bad = []

    for i, voice in enumerate(problems, 1):
        name = voice["name"]
        vid = voice["voice_id"]
        ref_ids = voice.get("reference_audio_ids", [])
        old_text = voice.get("reference_text", "")

        print(f"  [{i}/{len(problems)}] {name}")
        print(f"     旧台词: 「{old_text}」")

        # Step 1: ASR 现有 3 条参考
        best_text = ""
        best_score = -2
        asr_results = []

        for ri, fid in enumerate(ref_ids, 1):
            try:
                wav = api_download(f"/api/voices/{vid}/audio/{fid}")
                asr = api_asr(wav, f"ref_{ri}.wav")
                txt = asr.get("text", "")
                sc = text_score(txt)
                asr_results.append({"file_id": fid, "text": txt, "score": sc})
                print(f"     ref#{ri} ASR: 「{txt}」 (score={sc})")
                if sc > best_score:
                    best_score = sc
                    best_text = txt
                time.sleep(0.15)
            except Exception as e:
                print(f"     ref#{ri} ASR 失败: {str(e)[:60]}")

        # Step 2: 如果现有参考都不好，从源目录重选
        if best_score < 4:
            # 找源目录
            source_name = name.replace("（原神）", "")
            source_dir = GENSHIN_DIR / source_name
            if not source_dir.exists():
                # 尝试带引号的名称
                for alt in [f"「{source_name}」", source_name.replace("「", "").replace("」", "")]:
                    alt_dir = GENSHIN_DIR / alt
                    if alt_dir.exists():
                        source_dir = alt_dir
                        break

            if source_dir.exists():
                all_wavs = sorted(source_dir.glob("*.wav"))
                # 偏好更大的文件（更可能包含长句子）
                large_wavs = sorted(
                    [w for w in all_wavs if 200_000 <= w.stat().st_size <= 2_000_000],
                    key=lambda w: w.stat().st_size, reverse=True,
                )
                # ASR 最多 8 个新 WAV，找最好的
                new_best_text = ""
                new_best_score = best_score
                new_file_id = None

                for w in large_wavs[:8]:
                    try:
                        wav = w.read_bytes()
                        asr = api_asr(wav, w.name)
                        txt = asr.get("text", "")
                        sc = text_score(txt)
                        if sc > new_best_score:
                            new_best_score = sc
                            new_best_text = txt
                            new_file_id_candidate = None
                            # 上传这个更好的文件
                            fid_new = api_upload_wav(str(w))
                            if fid_new:
                                new_file_id = fid_new
                                new_best_text = txt
                        time.sleep(0.15)
                    except Exception:
                        continue

                if new_file_id and new_best_score > best_score:
                    # 替换最差的参考音频
                    worst_idx = 0
                    worst_score = 999
                    for j, ar in enumerate(asr_results):
                        if ar["score"] < worst_score:
                            worst_score = ar["score"]
                            worst_idx = j
                    new_ref_ids = list(ref_ids)
                    new_ref_ids[worst_idx] = new_file_id
                    best_text = new_best_text
                    best_score = new_best_score

                    # 更新参考音频和台词
                    try:
                        api_patch(f"/api/voices/{vid}", {
                            "reference_audio_ids": new_ref_ids,
                            "reference_text": best_text,
                        })
                        print(f"     🔄 替换 ref#{worst_idx + 1} + 更新台词")
                    except Exception as e:
                        print(f"     ❌ 替换失败: {str(e)[:60]}")

        # Step 3: 更新 reference_text
        if best_score >= 4:
            try:
                api_patch(f"/api/voices/{vid}", {"reference_text": best_text})
                cat = classify(best_text)
                emoji = "✅" if cat == "good" else "⚠️"
                print(f"     {emoji} 新台词: 「{best_text}」 (score={best_score})")
                fixed.append({"name": name, "old_text": old_text, "new_text": best_text, "score": best_score})
            except Exception as e:
                print(f"     ❌ 更新失败: {str(e)[:60]}")
                still_bad.append({"name": name, "reason": f"更新失败: {str(e)[:80]}"})
        else:
            print(f"     ❌ 未找到好台词 (best score={best_score})")
            still_bad.append({"name": name, "reason": f"所有参考音频ASR均不理想 (best={best_score})"})

        print()
        time.sleep(0.3)

    print(f"\n{'='*70}")
    print(f"结果: 修复 {len(fixed)} 个 | 仍问题 {len(still_bad)} 个")
    print(f"{'='*70}")

    if fixed:
        print(f"\n✅ 已修复 ({len(fixed)} 个):")
        for f in fixed:
            print(f"   {f['name']}")
            print(f"      「{f['old_text']}」 → 「{f['new_text']}」")

    if still_bad:
        print(f"\n❌ 仍需处理 ({len(still_bad)} 个):")
        for s in still_bad:
            print(f"   {s['name']}: {s['reason']}")


if __name__ == "__main__":
    main()
