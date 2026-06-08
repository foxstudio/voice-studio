"""
精选 25 个高人气角色中文配音 → Voice Studio 导入脚本。
数据源: simon3000/genshin-voice, simon3000/starrail-voice
方式: 单次全量扫描 rows API 构建 speaker 索引，再匹配角色
用法: .venv/bin/python3 scripts/curated_voice_import.py
"""

import os, sys, json, io, time, wave
import urllib.request, urllib.error, ssl

import numpy as np
import soundfile as sf

API = "http://localhost:8000/api"
MIN_DUR, MAX_DUR = 4.0, 15.0
CLIPS_PER_CHAR = 4
ROWS_PAGE = 100
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ── 精选角色 ──
CHARACTERS = [
    ("Firefly",      "流萤",   "星穹铁道", "女声", ["温柔","坚定","少女音"]),
    ("Silver Wolf",  "银狼",   "星穹铁道", "女声", ["慵懒","酷","少女音"]),
    ("Sparkle",      "花火",   "星穹铁道", "女声", ["戏谑","神秘","御姐"]),
    ("March 7th",    "三月七", "星穹铁道", "女声", ["活泼","元气","少女音"]),
    ("Black Swan",   "黑天鹅", "星穹铁道", "女声", ["优雅","神秘","御姐"]),
    ("Robin",        "知更鸟", "星穹铁道", "女声", ["温柔","明亮","歌姬"]),
    ("Seele",        "希儿",   "星穹铁道", "女声", ["冷静","凌厉","少女音"]),
    ("Bronya",       "布洛妮娅", "星穹铁道", "女声", ["冷静","沉稳","少女音"]),
    ("Himeko",       "姬子",   "星穹铁道", "女声", ["成熟","温柔","御姐"]),
    ("Ruan Mei",     "阮梅",   "星穹铁道", "女声", ["温柔","知性","御姐"]),
    ("Fu Xuan",      "符华",   "星穹铁道", "女声", ["严肃","古风","御姐"]),
    ("Blade",        "刃",     "星穹铁道", "男声", ["冷酷","低沉","沉稳"]),
    ("Aventurine",   "砂金",   "星穹铁道", "男声", ["潇洒","自信","少年音"]),
    ("Jing Yuan",    "景元",   "星穹铁道", "男声", ["温和","慵懒","青年音"]),
    ("Dan Heng",     "丹恒",   "星穹铁道", "男声", ["冷静","沉稳","青年音"]),
    ("Dr. Ratio",    "真理医生", "星穹铁道", "男声", ["严肃","理性","青年音"]),
    ("Nahida",       "纳西妲", "原神", "女声", ["稚嫩","温柔","少女音"]),
    ("Shenhe",       "申鹤",   "原神", "女声", ["清冷","淡然","少女音"]),
    ("Yelan",        "夜兰",   "原神", "女声", ["从容","干练","御姐"]),
    ("Yoimiya",      "宵宫",   "原神", "女声", ["热情","活泼","少女音"]),
    ("Nilou",        "妮露",   "原神", "女声", ["温柔","甜美","少女音"]),
    ("Diluc",        "迪卢克", "原神", "男声", ["沉稳","低沉","青年音"]),
    ("Xiao",         "魈",     "原神", "男声", ["冷淡","凌厉","少年音"]),
    ("Arataki Itto", "荒泷一斗", "原神", "男声", ["豪爽","热血","粗犷"]),
    ("Neuvillette",  "那维莱特", "原神", "男声", ["沉稳","庄重","低沉"]),
]

DATASETS = {
    "星穹铁道": "simon3000/starrail-voice",
    "原神":     "simon3000/genshin-voice",
}

# 需要索引的 speaker 集合
WANTED_SPEAKERS = {char[0] for char in CHARACTERS}


def fetch_json(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                return json.loads(r.read())
        except Exception as e:
            if i < retries - 1:
                wait = 2 ** i + 1
                print(f"    重试 {i+1}/{retries} ({wait}s): {e}")
                time.sleep(wait)
            else:
                raise


def fetch_bytes(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return r.read()
        except Exception as e:
            if i < retries - 1:
                wait = 2 ** i + 1
                print(f"    音频重试 {i+1}/{retries} ({wait}s): {e}")
                time.sleep(wait)
            else:
                raise


def api_get(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=30) as r:
        return json.loads(r.read())


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
    arr = np.array(audio_array, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    target_sr = 22050
    if sr != target_sr:
        ratio = target_sr / sr
        n_out = int(len(arr) * ratio)
        indices = np.linspace(0, len(arr) - 1, n_out).astype(int)
        arr = arr[indices]
    mx = np.abs(arr).max()
    if mx > 0:
        arr = arr / mx * 0.95
    pcm = (arr * 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_sr)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _cache_path(dataset_id):
    safe = dataset_id.replace("/", "_")
    return os.path.join(CACHE_DIR, f"{safe}.json")


def build_index(dataset_id):
    """全量扫描数据集，返回 {speaker: [clips]} 索引。带 JSON 缓存。"""
    cache = _cache_path(dataset_id)
    if os.path.exists(cache):
        print(f"  从缓存加载: {cache}")
        with open(cache) as f:
            return json.load(f)

    index = {}
    offset = 0
    total_rows = None
    page_count = 0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 10

    while True:
        url = (
            f"https://datasets-server.huggingface.co/rows"
            f"?dataset={dataset_id}&config=default&split=train"
            f"&offset={offset}&length={ROWS_PAGE}"
        )
        try:
            data = fetch_json(url)
        except Exception as e:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"    连续 {consecutive_failures} 次失败，终止扫描 (offset={offset})")
                break
            wait = min(30, 5 * consecutive_failures)
            print(f"    失败 {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES} (offset={offset}), {wait}s 后重试: {e}")
            time.sleep(wait)
            continue

        consecutive_failures = 0
        if total_rows is None:
            total_rows = data.get("num_rows_total", "?")
            print(f"  数据集总行数: {total_rows}")

        rows = data.get("rows", [])
        if not rows:
            break

        for item in rows:
            row = item["row"]
            speaker = row.get("speaker", "")
            if speaker not in WANTED_SPEAKERS:
                continue
            if row.get("language") not in ("Chinese", "zh"):
                continue
            text = row.get("transcription", "").strip()
            if not text:
                continue
            audio = row.get("audio")
            if not isinstance(audio, list) or not audio:
                continue
            src = audio[0].get("src")
            if not src:
                continue
            index.setdefault(speaker, []).append({"src": src, "text": text})

        offset += len(rows)
        page_count += 1

        if page_count % 100 == 0:
            found = {s: len(v) for s, v in index.items()}
            print(f"  已扫描 {offset}/{total_rows} 行, 索引: {found}")

        all_found = all(
            len(index.get(s, [])) >= CLIPS_PER_CHAR * 3
            for s in WANTED_SPEAKERS
        )
        if all_found:
            print(f"  所有角色已找到足够片段，提前退出 (offset={offset})")
            break

        # 请求间隔，避免限速
        time.sleep(0.5)

    # 保存缓存
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache, "w") as f:
        json.dump(index, f, ensure_ascii=False)
    print(f"  缓存已保存: {cache} ({sum(len(v) for v in index.values())} 条)")

    return index


def download_and_check(clip):
    try:
        raw = fetch_bytes(clip["src"])
    except Exception as e:
        print(f"    下载失败: {e}")
        return None
    try:
        buf = io.BytesIO(raw)
        arr, sr = sf.read(buf, dtype="float32")
    except Exception:
        return None
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    dur = len(arr) / sr
    if dur < MIN_DUR or dur > MAX_DUR:
        return None
    wav = to_wav_bytes(arr, sr)
    return wav, dur, clip["text"]


def main():
    print(f"═══ 精选角色导入 (全量索引) ═══")
    print(f"共 {len(CHARACTERS)} 个角色，每个最多 {CLIPS_PER_CHAR} 条片段\n")

    # 检查 Voice Studio
    try:
        resp = api_get("/voices")
        voices = resp.get("voices", []) if isinstance(resp, dict) else resp
        existing_names = {v["name"] for v in voices if isinstance(v, dict)}
    except Exception:
        print("Voice Studio 未运行!")
        sys.exit(1)

    # 阶段1: 构建索引
    all_indices = {}
    for game, dataset_id in DATASETS.items():
        print(f"\n{'='*50}")
        print(f"扫描数据集: {game} ({dataset_id})")
        print(f"{'='*50}")
        idx = build_index(dataset_id)
        for speaker, clips in idx.items():
            all_indices[speaker] = clips
        print(f"  {game} 索引完成: {len(idx)} 个角色命中")

    # 阶段2: 导入
    print(f"\n{'='*50}")
    print(f"开始导入 ({len(CHARACTERS)} 个角色)")
    print(f"{'='*50}")

    imported, failed = [], []
    for speaker_en, cn_name, game_tag, gender, style_tags in CHARACTERS:
        display_name = f"{cn_name}（{game_tag}）"
        print(f"\n── {display_name} ({speaker_en}) ──")

        if display_name in existing_names:
            print(f"  ⊘ 已存在，跳过")
            continue

        clips = all_indices.get(speaker_en, [])
        if not clips:
            print(f"  ✗ 索引中无中文片段")
            failed.append(display_name)
            continue

        print(f"  索引中有 {len(clips)} 个候选，下载筛选...")
        valid = []
        for clip in clips:
            result = download_and_check(clip)
            if result:
                valid.append(result)
            if len(valid) >= CLIPS_PER_CHAR:
                break

        if not valid:
            print(f"  ✗ 无符合时长要求的片段")
            failed.append(display_name)
            continue

        best_wav, best_dur, best_text = valid[0]
        print(f"  选中 {len(valid)} 条，最佳 {best_dur:.1f}s")

        if len(best_wav) < 5120 or len(best_wav) > 10 * 1024 * 1024:
            print(f"  ✗ 音频大小异常 ({len(best_wav)} bytes)")
            failed.append(display_name)
            continue

        upload = api_post("/voices/upload", files={
            "file": (f"{speaker_en}_zh.wav", best_wav),
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
        print(f"  ↑ 音频已上传: {audio_id} ({best_dur:.1f}s)")

        tags = [game_tag, cn_name, "游戏角色", "二次元", "中文角色音", gender] + style_tags
        reg = api_post("/voices", data={
            "name": display_name,
            "voice_type": "virtual_character",
            "description": f"《{game_tag}》中文角色参考音色，角色：{cn_name}。来源：{DATASETS[game_tag]} HuggingFace 开源数据集。参考台词：{best_text}",
            "default_language": "zh",
            "tags": tags,
            "reference_text": best_text,
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
        print(f"    台词: {best_text[:50]}...")
        imported.append(display_name)
        time.sleep(0.3)

    print(f"\n{'='*50}")
    print(f"导入完成: {len(imported)} 成功 / {len(failed)} 失败")
    if failed:
        print(f"失败: {', '.join(failed)}")


if __name__ == "__main__":
    main()
