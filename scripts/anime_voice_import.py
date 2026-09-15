"""
从 HuggingFace 流式数据集批量导入原神 & 星穹铁道角色中文原声配音到 Voice Studio。

数据源:
  - 原神: simon3000/genshin-voice (Parquet, 流式)
  - 星穹铁道: simon3000/starrail-voice (Parquet, 流式)

用法: .venv/bin/python3 scripts/anime_voice_import.py
"""

import os
import sys
import json
import tempfile
import urllib.request
import urllib.error
import time

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from datasets import load_dataset
import soundfile as sf

VOICE_STUDIO_API = "http://localhost:8000/api"
MIN_DURATION = 4.0
MAX_DURATION = 15.0
MAX_CLIPS_PER_CHAR = 3
MAX_SAMPLES_SCAN = 2000  # 每个角色最多扫描的样本数

# ── 角色配置 ──────────────────────────────────────────────────
# 格式: (speaker英文名, 显示中文名, 游戏标签, 性别)
CHARACTERS = [
    # ── 原神 (simon3000/genshin-voice) ──
    # 女声
    ("Paimon", "派蒙", "原神", "女声"),
    ("Amber", "安柏", "原神", "女声"),
    ("Barbara", "芭芭拉", "原神", "女声"),
    ("Lisa", "丽莎", "原神", "女声"),
    ("Xiangling", "香菱", "原神", "女声"),
    ("Noelle", "诺艾尔", "原神", "女声"),
    ("Fischl", "菲谢尔", "原神", "女声"),
    ("Mona", "莫娜", "原神", "女声"),
    ("Keqing", "刻晴", "原神", "女声"),
    ("Qiqi", "七七", "原神", "女声"),
    ("Ningguang", "凝光", "原神", "女声"),
    ("Beidou", "北斗", "原神", "女声"),
    ("Ganyu", "甘雨", "原神", "女声"),
    ("Hu Tao", "胡桃", "原神", "女声"),
    ("Yanfei", "烟绯", "原神", "女声"),
    ("Rosaria", "罗莎莉亚", "原神", "女声"),
    ("Eula", "优菈", "原神", "女声"),
    ("Kamisato Ayaka", "神里绫华", "原神", "女声"),
    ("Yoimiya", "宵宫", "原神", "女声"),
    ("Sayu", "早柚", "原神", "女声"),
    ("Kujou Sara", "九条裟罗", "原神", "女声"),
    ("Sangonomiya Kokomi", "珊瑚宫心海", "原神", "女声"),
    ("Yun Jin", "云堇", "原神", "女声"),
    ("Yae Miko", "八重神子", "原神", "女声"),
    ("Yelan", "夜兰", "原神", "女声"),
    ("Kuki Shinobu", "久岐忍", "原神", "女声"),
    ("Nilou", "妮露", "原神", "女声"),
    ("Nahida", "纳西妲", "原神", "女声"),
    ("Layla", "莱依拉", "原神", "女声"),
    ("Faruzan", "珐露珊", "原神", "女声"),
    ("Dehya", "迪希雅", "原神", "女声"),
    ("Furina", "芙宁娜", "原神", "女声"),
    ("Charlotte", "夏洛蒂", "原神", "女声"),
    ("Navia", "娜维娅", "原神", "女声"),
    ("Shenhe", "申鹤", "原神", "女声"),
    ("Yaoyao", "瑶瑶", "原神", "女声"),
    ("Lynette", "琳妮特", "原神", "女声"),
    ("Sucrose", "砂糖", "原神", "女声"),
    ("Klee", "可莉", "原神", "女声"),
    ("Sigewinne", "希格雯", "原神", "女声"),
    ("Chiori", "千织", "原神", "女声"),
    ("Mualani", "玛拉妮", "原神", "女声"),
    ("Candace", "坎蒂丝", "原神", "女声"),
    ("Dori", "多莉", "原神", "女声"),
    ("Collei", "柯莱", "原神", "女声"),
    ("Shikimori", "夏沃蕾", "原神", "女声"),
    # 男声
    ("Kaeya", "凯亚", "原神", "男声"),
    ("Razor", "雷泽", "原神", "男声"),
    ("Bennett", "班尼特", "原神", "男声"),
    ("Diluc", "迪卢克", "原神", "男声"),
    ("Venti", "温迪", "原神", "男声"),
    ("Xingqiu", "行秋", "原神", "男声"),
    ("Chongyun", "重云", "原神", "男声"),
    ("Tartaglia", "达达利亚", "原神", "男声"),
    ("Zhongli", "钟离", "原神", "男声"),
    ("Albedo", "阿贝多", "原神", "男声"),
    ("Xiao", "魈", "原神", "男声"),
    ("Kaedehara Kazuha", "枫原万叶", "原神", "男声"),
    ("Arataki Itto", "荒泷一斗", "原神", "男声"),
    ("Gorou", "五郎", "原神", "男声"),
    ("Thoma", "托马", "原神", "男声"),
    ("Kamisato Ayato", "神里绫人", "原神", "男声"),
    ("Shikanoin Heizou", "鹿野院平藏", "原神", "男声"),
    ("Cyno", "赛诺", "原神", "男声"),
    ("Wanderer", "流浪者", "原神", "男声"),
    ("Alhaitham", "艾尔海森", "原神", "男声"),
    ("Mika", "米卡", "原神", "男声"),
    ("Baizhu", "白术", "原神", "男声"),
    ("Lyney", "林尼", "原神", "男声"),
    ("Freminet", "菲米尼", "原神", "男声"),
    ("Neuvillette", "那维莱特", "原神", "男声"),
    ("Wriothesley", "莱欧斯利", "原神", "男声"),
    ("Tighnari", "提纳里", "原神", "男声"),
    # ── 星穹铁道 (simon3000/starrail-voice) ──
    # 女声
    ("March 7th", "三月七", "星穹铁道", "女声"),
    ("Himeko", "姬子", "星穹铁道", "女声"),
    ("Bronya", "布洛妮娅", "星穹铁道", "女声"),
    ("Seele", "希儿", "星穹铁道", "女声"),
    ("Silver Wolf", "银狼", "星穹铁道", "女声"),
    ("Bailu", "白露", "星穹铁道", "女声"),
    ("Sparkle", "花火", "星穹铁道", "女声"),
    ("Black Swan", "黑天鹅", "星穹铁道", "女声"),
    ("Robin", "知更鸟", "星穹铁道", "女声"),
    ("Firefly", "流萤", "星穹铁道", "女声"),
    ("Jade", "翡翠", "星穹铁道", "女声"),
    ("Yunli", "云璃", "星穹铁道", "女声"),
    ("Tingyun", "停云", "星穹铁道", "女声"),
    ("Pela", "佩拉", "星穹铁道", "女声"),
    ("Asta", "艾丝妲", "星穹铁道", "女声"),
    ("Serval", "希露瓦", "星穹铁道", "女声"),
    ("Qingque", "青雀", "星穹铁道", "女声"),
    ("Sushang", "素裳", "星穹铁道", "女声"),
    ("Hook", "虎克", "星穹铁道", "女声"),
    ("Natasha", "娜塔莎", "星穹铁道", "女声"),
    ("Clara", "克拉拉", "星穹铁道", "女声"),
    ("Herta", "黑塔", "星穹铁道", "女声"),
    ("Ruan Mei", "阮梅", "星穹铁道", "女声"),
    ("Huo Huo", "藿藿", "星穹铁道", "女声"),
    ("Fu Xuan", "符华", "星穹铁道", "女声"),
    # 男声
    ("Dan Heng", "丹恒", "星穹铁道", "男声"),
    ("Welt", "瓦尔特", "星穹铁道", "男声"),
    ("Jing Yuan", "景元", "星穹铁道", "男声"),
    ("Luocha", "罗刹", "星穹铁道", "男声"),
    ("Blade", "刃", "星穹铁道", "男声"),
    ("Argenti", "银枝", "星穹铁道", "男声"),
    ("Dr. Ratio", "真理医生", "星穹铁道", "男声"),
    ("Boothill", "波提欧", "星穹铁道", "男声"),
    ("Aventurine", "砂金", "星穹铁道", "男声"),
    ("Yanqing", "彦卿", "星穹铁道", "男声"),
    ("Sampo", "桑博", "星穹铁道", "男声"),
    ("Jiaoqiu", "椒丘", "星穹铁道", "男声"),
]

# 数据集映射: 游戏标签 → HuggingFace dataset
DATASET_MAP = {
    "原神": "simon3000/genshin-voice",
    "星穹铁道": "simon3000/starrail-voice",
}


def api_post(path, data=None, files=None):
    url = f"{VOICE_STUDIO_API}{path}"
    if files:
        boundary = "----FormBoundary7MA4YWxkTrZu0gW"
        body = b""
        for key, value in files.items():
            if isinstance(value, tuple):
                filename, filedata = value
                body += f"--{boundary}\r\n".encode()
                body += f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
                body += b"Content-Type: audio/wav\r\n\r\n"
                body += filedata
                body += b"\r\n"
            else:
                body += f"--{boundary}\r\n".encode()
                body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
                body += value.encode() if isinstance(value, str) else value
                body += b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    else:
        req_data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, method="POST")
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        if "already exists" in err or "unique" in err.lower():
            return {"_duplicate": True}
        print(f"    API {e.code}: {err[:100]}")
        return None
    except Exception as e:
        print(f"    API error: {e}")
        return None


def stream_clips_for_speaker(dataset_name, speaker, game_label):
    """流式加载数据集，筛选中文配音片段。"""
    print(f"  连接 {dataset_name} (streaming)...")
    ds = load_dataset(dataset_name, split="train", streaming=True)

    candidates = []
    scanned = 0
    for sample in ds:
        scanned += 1
        if scanned > MAX_SAMPLES_SCAN:
            break

        # 匹配说话人（部分匹配，处理 "Ganyu" vs "Ganyu (Souffle)" 等变体）
        spk = sample.get("speaker", "")
        if speaker.lower() not in spk.lower():
            continue

        # 只要中文
        lang = sample.get("language", "")
        if lang != "Chinese":
            continue

        # 必须有文本
        text = sample.get("transcription", "")
        if not text:
            continue

        audio = sample.get("audio", {})
        if not audio or "array" not in audio:
            continue

        arr = audio["array"]
        sr = audio.get("sampling_rate", 22050)
        duration = len(arr) / sr

        if MIN_DURATION <= duration <= MAX_DURATION:
            candidates.append({
                "duration": duration,
                "text": text,
                "audio_array": arr,
                "sampling_rate": sr,
            })

        if len(candidates) >= MAX_CLIPS_PER_CHAR * 5:
            break

    print(f"  扫描 {scanned} 条，找到 {len(candidates)} 个候选片段")
    # 优先选接近 8 秒的片段
    candidates.sort(key=lambda x: abs(x["duration"] - 8))
    return candidates[:MAX_CLIPS_PER_CHAR]


def import_character(speaker, display_name, game_label, gender):
    """导入单个角色的中文配音。"""
    dataset_name = DATASET_MAP[game_label]
    clips = stream_clips_for_speaker(dataset_name, speaker, game_label)

    if not clips:
        print(f"    无合适片段 (需 {MIN_DURATION}-{MAX_DURATION}s 中文)")
        return None

    print(f"    选中 {len(clips)} 条:")
    for i, c in enumerate(clips):
        print(f"      [{i+1}] {c['duration']:.1f}s | {c['text'][:35]}...")

    # 上传音频片段
    file_ids = []
    style_names = ["自然", "明亮", "沉稳"]
    for i, clip in enumerate(clips):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, clip["audio_array"], clip["sampling_rate"])
        tmp.close()
        style = style_names[min(i, 2)]
        with open(tmp.name, "rb") as f:
            wav_data = f.read()
        os.unlink(tmp.name)

        result = api_post("/voices/upload", files={
            "file": (f"{display_name}_{style}.wav", wav_data),
            "name": f"{display_name}-{style}",
        })
        if result and "file_id" in result:
            file_ids.append(result["file_id"])
        else:
            print(f"    上传失败 ({style})")

    if not file_ids:
        return None

    # 注册音色
    voice_data = {
        "name": display_name,
        "voice_type": "virtual_character",
        "description": f"{game_label}角色中文原声配音，{len(file_ids)}个风格参考。",
        "default_language": "zh",
        "tags": [game_label, "二次元", gender, "游戏配音", "原声"],
        "reference_text": clips[0]["text"],
        "reference_audio_ids": file_ids,
        "license_status": "test_only",
    }
    result = api_post("/voices", data=voice_data)
    if result and "voice_id" in result:
        print(f"    注册成功: {result['voice_id']}")
        return result["voice_id"]
    if result and result.get("_duplicate"):
        print(f"    已存在，跳过")
        return "duplicate"
    print(f"    注册失败")
    return None


def main():
    print("=" * 60)
    print("动漫游戏原声导入器 (流式数据集)")
    print("原神: simon3000/genshin-voice")
    print("星穹铁道: simon3000/starrail-voice")
    print(f"角色: {len(CHARACTERS)} | 每角色最多: {MAX_CLIPS_PER_CHAR} 段")
    print(f"时长: {MIN_DURATION}-{MAX_DURATION}s")
    print("=" * 60)

    # 检查 Voice Studio
    try:
        resp = urllib.request.urlopen(f"{VOICE_STUDIO_API}/voices")
        existing = json.loads(resp.read())
        existing_names = {v["name"] for v in existing}
        print(f"Voice Studio 运行中，已有 {len(existing)} 个音色")
    except Exception as e:
        print(f"Voice Studio 未运行: {e}")
        sys.exit(1)

    # 过滤已存在的
    to_import = [
        (spk, name, game, gender)
        for spk, name, game, gender in CHARACTERS
        if name not in existing_names
    ]
    skipped = len(CHARACTERS) - len(to_import)
    print(f"已存在/跳过: {skipped} 个")
    print(f"待导入: {len(to_import)} 个角色\n")

    success = []
    duplicates = 0
    failed = 0

    for i, (speaker, display_name, game_label, gender) in enumerate(to_import):
        print(f"\n[{i+1}/{len(to_import)}] {display_name} ({speaker}) [{game_label}]")
        try:
            vid = import_character(speaker, display_name, game_label, gender)
            if vid and vid != "duplicate":
                success.append(display_name)
            elif vid == "duplicate":
                duplicates += 1
            else:
                failed += 1
        except Exception as e:
            print(f"    异常: {str(e)[:120]}")
            failed += 1

        # 每 3 个角色短暂休息
        if (i + 1) % 3 == 0:
            time.sleep(1)

    # 汇总
    print(f"\n{'='*60}")
    print(f"导入完成!")
    print(f"  成功: {len(success)}")
    print(f"  已存在/跳过: {duplicates}")
    print(f"  失败: {failed}")
    if success:
        print(f"\n成功导入的角色:")
        for name in success:
            print(f"  - {name}")

    # 最终音色库数量
    try:
        resp = urllib.request.urlopen(f"{VOICE_STUDIO_API}/voices")
        final_count = len(json.loads(resp.read()))
        print(f"\n音色库总量: {final_count}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
