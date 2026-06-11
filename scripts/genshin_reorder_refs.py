#!/usr/bin/env python3
"""检查所有原神音色的 [0] 参考音频 ASR 质量，重排不好的。

TTS 引擎只用 reference_audio_ids[0] 的音频文件做参考。
如果 [0] 是"啊"、"嗯"这种语气词音频，TTS 质量会很差。
需要 ASR [0]，如果不好就从其他参考中找好的替换到 [0]。
"""

import json
import re
import time
import urllib.request
import urllib.error

API_BASE = "http://localhost:8000"

INTERJECTION_RE = re.compile(
    r"^[啊哦嗯哈嘿呃唔哇噢咦呀哎唉哟嘟呜啵呵哼喵汪嗷噢~～!！.。，,？?、：:；;\x22\x27\s]*$"
)
PURE_SYMBOL_RE = re.compile(r"^[^a-zA-Z一-鿿0-9]*$")


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
    """音频内容质量评分：越高越好"""
    if not text or not text.strip():
        return -1
    cleaned = re.sub(r"[~～!！.。，,？?、：:；;\x22\x27\s]", "", text.strip())
    if not cleaned or PURE_SYMBOL_RE.match(text.strip()):
        return -1
    if INTERJECTION_RE.match(text.strip()):
        return 0
    return len(cleaned)


def main():
    print("=" * 70)
    print("原神音色 — 检查 [0] 参考音频质量 & 重排")
    print("=" * 70)

    all_voices = api_get("/api/voices")
    genshin = [v for v in all_voices if "原神" in v.get("name", "")]
    multi = [v for v in genshin if len(v.get("reference_audio_ids", [])) >= 2]
    print(f"原神音色: {len(genshin)} | 多参考: {len(multi)}\n")

    # Phase 1: 只 ASR [0]，快速筛查
    print("Phase 1: 筛查 [0] 音频质量...")
    bad_first = []

    for i, voice in enumerate(multi, 1):
        name = voice["name"]
        vid = voice["voice_id"]
        ref_ids = voice.get("reference_audio_ids", [])
        fid0 = ref_ids[0]

        try:
            wav = api_download(f"/api/voices/{vid}/audio/{fid0}")
            asr = api_asr(wav, "ref_0.wav")
            txt = asr.get("text", "")
            sc = text_score(txt)
            dur = asr.get("duration_ms", 0)

            if sc < 4:
                bad_first.append({
                    "name": name, "voice_id": vid, "ref_ids": ref_ids,
                    "first_text": txt, "first_score": sc,
                })
                print(f"  [{i}/{len(multi)}] ⚠️ {name}: [0] score={sc} 「{txt}」")
            else:
                if i % 20 == 0:
                    print(f"  [{i}/{len(multi)}] ... 已检查 {i} 个 ...")

            time.sleep(0.1)
        except Exception as e:
            print(f"  [{i}/{len(multi)}] ❌ {name}: ASR 失败 {str(e)[:50]}")

    print(f"\n[0] 质量不好: {len(bad_first)} 个\n")

    if not bad_first:
        print("所有音色的 [0] 参考音频质量都 OK!")
        return

    # Phase 2: 对不好的，ASR 所有参考，找最好的重排
    print("Phase 2: ASR 全部参考并重排...")
    reordered = []

    for i, info in enumerate(bad_first, 1):
        name = info["name"]
        vid = info["voice_id"]
        ref_ids = info["ref_ids"]

        print(f"\n  [{i}/{len(bad_first)}] {name}")
        print(f"     [0] 当前: score={info['first_score']} 「{info['first_text']}」")

        scored = []
        for ri, fid in enumerate(ref_ids):
            try:
                wav = api_download(f"/api/voices/{vid}/audio/{fid}")
                asr = api_asr(wav, f"ref_{ri}.wav")
                txt = asr.get("text", "")
                sc = text_score(txt)
                dur = asr.get("duration_ms", 0)
                scored.append({"file_id": fid, "text": txt, "score": sc, "duration_ms": dur})
                label = "✅" if sc >= 4 else "⚠️"
                print(f"     [{ri}] {label} score={sc:>3} ({dur:>6}ms) 「{txt[:50]}{'…' if len(txt)>50 else ''}」")
                time.sleep(0.15)
            except Exception as e:
                print(f"     [{ri}] ❌ 失败: {str(e)[:40]}")

        if not scored:
            continue

        scored.sort(key=lambda x: x["score"], reverse=True)
        best = scored[0]

        if best["score"] < 4:
            print(f"     ❌ 所有参考都不理想 (best={best['score']})")
            continue

        new_order = [s["file_id"] for s in scored]
        if new_order == ref_ids:
            print(f"     → 顺序已正确")
            continue

        try:
            api_patch(f"/api/voices/{vid}", {
                "reference_audio_ids": new_order,
                "reference_text": best["text"],
            })
            print(f"     🔄 重排完成: [0] score {info['first_score']} → {best['score']}")
            reordered.append({
                "name": name,
                "old_score": info["first_score"],
                "new_score": best["score"],
                "new_text": best["text"],
            })
        except Exception as e:
            print(f"     ❌ 更新失败: {str(e)[:60]}")

        time.sleep(0.3)

    print(f"\n{'='*70}")
    print(f"结果: 重排 {len(reordered)} / {len(bad_first)} 个")
    print(f"{'='*70}")

    if reordered:
        for r in reordered:
            print(f"  {r['name']}: score {r['old_score']} → {r['new_score']}")


if __name__ == "__main__":
    main()
