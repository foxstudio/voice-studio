"""
精选 25 个高人气角色中文配音 → Voice Studio 导入脚本。
数据源: simon3000/genshin-voice, simon3000/starrail-voice
用法: .venv/bin/python3 scripts/curated_voice_import.py
"""

import os, sys, json, tempfile, subprocess, time, io
import urllib.request, urllib.error

os.environ.pop("HF_ENDPOINT", None)

from datasets import load_dataset
import numpy as np

API = "http://localhost:8000/api"
MIN_DUR, MAX_DUR = 4.0, 15.0
CLIPS_PER_CHAR = 4

# ── 精选角色 ──
# (speaker英文名, 中文名, 游戏, 性别, 音色风格标签)
CHARACTERS = [
    # 星穹铁道 (16)
    ("Firefly",        "流萤",     "星穹铁道", "女声", ["温柔","坚定","少女音"]),
    ("Silver Wolf",    "银狼",     "星穹铁道", "女声", ["慵懒","酷","少女音"]),
    ("Sparkle",        "花火",     "星穹铁道", "女声", ["戏谑","神秘","御姐"]),
    ("March 7th",      "三月七",   "星穹铁道", "女声", ["活泼","元气","少女音"]),
    ("Black Swan",     "黑天鹅",   "星穹铁道", "女声", ["优雅","神秘","御姐"]),
    ("Robin",          "知更鸟",   "星穹铁道", "女声", ["温柔","明亮","歌姬"]),
    ("Seele",          "希儿",     "星穹铁道", "女声", ["冷静","凌厉","少女音"]),
    ("Bronya",         "布洛妮娅", "星穹铁道", "女声", ["冷静","沉稳","少女音"]),
    ("Himeko",         "姬子",     "星穹铁道", "女声", ["成熟","温柔","御姐"]),
    ("Ruan Mei",       "阮梅",     "星穹铁道", "女声", ["温柔","知性","御姐"]),
    ("Fu Xuan",        "符华",     "星穹铁道", "女声", ["严肃","古风","御姐"]),
    ("Blade",          "刃",       "星穹铁道", "男声", ["冷酷","低沉","沉稳"]),
    ("Aventurine",     "砂金",     "星穹铁道", "男声", ["潇洒","自信","少年音"]),
    ("Jing Yuan",      "景元",     "星穹铁道", "男声", ["温和","慵懒","青年音"]),
    ("Dan Heng",       "丹恒",     "星穹铁道", "男声", ["冷静","沉稳","青年音"]),
    ("Dr. Ratio",      "真理医生", "星穹铁道", "男声", ["严肃","理性","青年音"]),
    # 原神 (9)
    ("Nahida",         "纳西妲",   "原神", "女声", ["稚嫩","温柔","少女音"]),
    ("Shenhe",         "申鹤",     "原神", "女声", ["清冷","淡然","少女音"]),
    ("Yelan",          "夜兰",     "原神", "女声", ["从容","干练","御姐"]),
    ("Yoimiya",        "宵宫",     "原神", "女声", ["热情","活泼","少女音"]),
    ("Nilou",          "妮露",     "原神", "女声", ["温柔","甜美","少女音"]),
    ("Diluc",          "迪卢克",   "原神", "男声", ["沉稳","低沉","青年音"]),
    ("Xiao",           "魈",       "原神", "男声", ["冷淡","凌厉","少年音"]),
    ("Arataki Itto",   "荒泷一斗", "原神", "男声", ["豪爽","热血","粗犷"]),
    ("Neuvillette",    "那维莱特", "原神", "男声", ["沉稳","庄重","低沉"]),
]

DATASET_MAP = {
    "原神":     "simon3000/genshin-voice",
    "星穹铁道": "simon3000/starrail-voice",
}


def api_get(path):
    url = f"{API}{path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def api_post(path, data=None, files=None):
    url = f"{API}{path}"
    if files:
        boundary = "----FormBoundary7MA4YWxkTrZu0gW"
        body = b""
        for key, val in files.items():
            if isinstance(val, tuple):
                fname, fdata = val
                body += (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{key}"; filename="{fname}"\r\n'
                    f"Content-Type: audio/wav\r\n\r\n"
                ).encode() + fdata + b"\r\n"
            else:
                body += (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                    f"{val}\r\n"
                ).encode()
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    else:
        req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST")
        req.add_header("Content-Type", "application/json")

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                return {"error": str(e)}


def to_wav_bytes(audio_array, sr):
    buf = io.BytesIO()
    audio_np = np.array(audio_array, dtype=np.float32)
    if audio_np.ndim > 1:
        audio_np = audio_np.mean(axis=1)
    target_sr = 22050
    if sr != target_sr:
        ratio = target_sr / sr
        n_out = int(len(audio_np) * ratio)
        indices = np.linspace(0, len(audio_np) - 1, n_out).astype(int)
        audio_np = audio_np[indices]
    max_val = np.abs(audio_np).max()
    if max_val > 0:
        audio_np = audio_np / max_val * 0.95
    pcm = (audio_np * 32767).astype(np.int16)
    import wave
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_sr)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def scan_dataset(dataset_name, speaker_en, max_scan=3000):
    print(f"  加载数据集 {dataset_name} (流式)...")
    try:
        ds = load_dataset(dataset_name, split="train", streaming=True)
    except Exception as e:
        print(f"  ✗ 数据集加载失败: {e}")
        return []

    clips = []
    scanned = 0
    for row in ds:
        scanned += 1
        if scanned > max_scan:
            break
        if row.get("speaker") != speaker_en:
            continue
        if row.get("language") != "zh":
            continue
        audio = row.get("audio")
        if not audio or "array" not in audio:
            continue
        arr = audio["array"]
        sr = audio["sampling_rate"]
        dur = len(arr) / sr
        if dur < MIN_DUR or dur > MAX_DUR:
            continue
        text = row.get("transcription", "").strip()
        if not text:
            continue
        score = -abs(dur - 8.0)
        clips.append({"score": score, "dur": dur, "arr": arr, "sr": sr, "text": text})
        if len(clips) >= 20:
            break

    clips.sort(key=lambda c: c["score"], reverse=True)
    return clips[:CLIPS_PER_CHAR]


def main():
    print(f"═══ 精选角色导入 ═══")
    print(f"共 {len(CHARACTERS)} 个角色，每个最多 {CLIPS_PER_CHAR} 条片段\n")

    imported, failed = [], []
    existing_names = set()
    try:
        resp = api_get("/voices")
        if isinstance(resp, dict):
            voices = resp.get("voices", [])
        else:
            voices = resp
        existing_names = {v["name"] for v in voices if isinstance(v, dict)}
    except Exception:
        pass

    by_game = {}
    for char in CHARACTERS:
        game = char[2]
        by_game.setdefault(game, []).append(char)

    for game, chars in by_game.items():
        dataset = DATASET_MAP[game]
        print(f"\n{'='*50}")
        print(f"游戏: {game} | 数据集: {dataset}")
        print(f"{'='*50}")

        for speaker_en, cn_name, game_tag, gender, style_tags in chars:
            display_name = f"{cn_name}（{game_tag}）"
            print(f"\n── {display_name} ({speaker_en}) ──")

            if display_name in existing_names:
                print(f"  ⊘ 已存在，跳过")
                continue

            clips = scan_dataset(dataset, speaker_en)
            if not clips:
                print(f"  ✗ 未找到符合条件的中文片段")
                failed.append(display_name)
                continue

            print(f"  找到 {len(clips)} 条片段")

            best = clips[0]
            wav_bytes = to_wav_bytes(best["arr"], best["sr"])
            dur = best["dur"]
            text = best["text"]

            if len(wav_bytes) < 5120 or len(wav_bytes) > 10 * 1024 * 1024:
                print(f"  ✗ 音频大小异常 ({len(wav_bytes)} bytes)")
                failed.append(display_name)
                continue

            upload = api_post("/voices/upload", files={
                "file": (f"{speaker_en}_zh.wav", wav_bytes),
            })
            if "error" in upload:
                print(f"  ✗ 上传失败: {upload['error']}")
                failed.append(display_name)
                continue
            audio_id = upload.get("file_id")
            if not audio_id:
                print(f"  ✗ 上传返回无 file_id")
                failed.append(display_name)
                continue
            print(f"  ↑ 音频已上传: {audio_id} ({dur:.1f}s)")

            tags = [game_tag, cn_name, "游戏角色", "二次元", "中文角色音", gender] + style_tags
            reg = api_post("/voices", data={
                "name": display_name,
                "voice_type": "virtual_character",
                "description": f"《{game_tag}》中文角色参考音色，角色：{cn_name}。来源：{dataset} HuggingFace 开源数据集。参考台词：{text}",
                "default_language": "zh",
                "tags": tags,
                "reference_text": text,
                "recommended_engine_id": "indextts-v2",
                "reference_audio_ids": [audio_id],
                "license_status": "test_only",
            })

            if "error" in reg:
                print(f"  ✗ 注册失败: {reg['error']}")
                failed.append(display_name)
                continue

            vid = reg.get("voice_id", "?")
            print(f"  ✓ {display_name} → {vid}")
            print(f"    台词: {text[:50]}...")
            imported.append(display_name)
            time.sleep(0.5)

    print(f"\n{'='*50}")
    print(f"导入完成: {len(imported)} 成功 / {len(failed)} 失败")
    if failed:
        print(f"失败: {', '.join(failed)}")


if __name__ == "__main__":
    main()
