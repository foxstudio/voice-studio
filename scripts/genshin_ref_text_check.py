#!/usr/bin/env python3
"""检查原神音色的 reference_text（台词）质量。"""

import json
import re
import urllib.request

API_BASE = "http://localhost:8000"

INTERJECTION_RE = re.compile(
    r"^[啊哦嗯哈嘿呃唔哇噢咦呀哎唉哟嘟呜啵呵哼喵汪嗷噢~～!！.。，,？?、：:；;\x22\x27\s]*$"
)
PURE_SYMBOL_RE = re.compile(r"^[^a-zA-Z一-鿿0-9]*$")


def api_get(path):
    resp = urllib.request.urlopen(f"{API_BASE}{path}", timeout=15)
    return json.loads(resp.read())


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


def main():
    all_voices = api_get("/api/voices")
    genshin = [v for v in all_voices if "原神" in v.get("name", "")]
    print(f"原神音色: {len(genshin)} 个\n")

    stats = {"good": 0, "empty": 0, "interjection": 0, "too_short": 0}
    problem_voices = []

    for v in sorted(genshin, key=lambda x: x["name"]):
        name = v["name"]
        ref_text = v.get("reference_text", "")
        cat = classify(ref_text)

        if cat == "good":
            stats["good"] += 1
        else:
            stats[cat] += 1
            label = {"empty": "空", "interjection": "语气词", "too_short": "过短"}[cat]
            ref_count = len(v.get("reference_audio_ids", []))
            tags = v.get("tags", [])
            is_playable = "可操控角色" in tags
            problem_voices.append({
                "name": name,
                "voice_id": v["voice_id"],
                "issue": cat,
                "label": label,
                "reference_text": ref_text,
                "ref_count": ref_count,
                "is_playable": is_playable,
            })

    print(f"✅ 台词正常: {stats['good']} 个")
    print(f"🔴 台词为空: {stats['empty']} 个")
    print(f"🟡 纯语气词: {stats['interjection']} 个")
    print(f"🟠 内容过短: {stats['too_short']} 个")
    print(f"合计问题: {sum(v for k, v in stats.items() if k != 'good')} 个\n")

    if problem_voices:
        print("=" * 60)
        print("问题音色列表:")
        print("=" * 60)
        for p in problem_voices:
            tag = "🎮" if p["is_playable"] else "📋"
            print(f"  {tag} {p['name']}")
            print(f"     台词: 「{p['reference_text']}」")
            print(f"     问题: {p['label']} | 参考: {p['ref_count']} 条")
            print()


if __name__ == "__main__":
    main()
