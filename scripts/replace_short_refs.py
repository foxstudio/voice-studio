"""
替换短/劣质参考音频。
从桌面原神语音包中找到更好的 wav，用 ASR 识别后替换。
"""
import json, os, random, shutil, sqlite3, time, hashlib
from pathlib import Path

DB_PATH = Path.home() / "VoiceStudio" / "config" / "voice_studio.db"
VOICE_DIR = Path.home() / "VoiceStudio" / "voices"
PACK_DIR = Path.home() / "Desktop" / "音色下载" / "原神语音包6.3（中）"
ASR_URL = "http://localhost:8000/api/asr/transcribe"

# 角色名到语音包目录名的映射（大部分是直接匹配）
# 去掉（原神）后缀即可
def pack_dir_for(name: str) -> Path | None:
    base = name.replace("（原神）", "").strip()
    if (PACK_DIR / base).is_dir():
        return PACK_DIR / base
    return None


def get_short_voices():
    """获取所有 reference_text 太短的音色"""
    db = sqlite3.connect(str(DB_PATH))
    rows = db.execute("SELECT voice_id, data FROM voices").fetchall()
    db.close()

    results = []
    for vid, data in rows:
        v = json.loads(data)
        rt = v.get("reference_text", "")
        name = v.get("name", "")
        aids = v.get("reference_audio_ids", [])

        if not rt or not aids:
            continue

        # 筛选条件：太短或有重复字
        too_short = len(rt) < 10
        has_repeat = any(rt[i] == rt[i+1] == rt[i+2] for i in range(len(rt) - 2)) if len(rt) >= 3 else False

        if too_short or has_repeat:
            results.append({
                "voice_id": vid,
                "name": name,
                "old_text": rt,
                "audio_ids": aids,
                "data": v,
            })
    return results


def pick_best_wavs(char_dir: Path, count: int = 10) -> list[Path]:
    """从角色目录中选文件大小最大的几个 wav（通常越长越好）"""
    wavs = sorted(char_dir.glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True)
    # 取前 30 个最大的，再随机选 count 个（避免选到纯音效/战斗音）
    candidates = wavs[:30]
    if len(candidates) <= count:
        return candidates
    return random.sample(candidates, count)


def asr_transcribe(wav_path: str, engine_id: str = "qwen3-asr-mlx") -> str | None:
    """调用本地 ASR API 转写"""
    import urllib.request

    filename = os.path.basename(wav_path)
    boundary = hashlib.md5(str(time.time()).encode()).hexdigest()

    with open(wav_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + file_data + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="engine_id"\r\n\r\n'
        f"{engine_id}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\n'
        f"zh\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(
        ASR_URL,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result.get("text", "").strip()
    except Exception as e:
        print(f"  ASR 失败: {e}")
        return None


def score_text(text: str) -> float:
    """评估文本质量：越长越好，正常标点，没有重复字"""
    if not text:
        return 0
    score = len(text) * 2
    # 正常句子通常有句号/逗号
    if any(p in text for p in "，。！？、；："):
        score += 20
    # 太多感叹号/问号不好
    if text.count("！") + text.count("？") + text.count("!") + text.count("?") > 2:
        score -= 30
    # 重复字不好
    if any(text[i] == text[i+1] == text[i+2] for i in range(len(text) - 2)):
        score -= 50
    # 理想长度 15-60 字
    if 15 <= len(text) <= 60:
        score += 30
    elif 10 <= len(text) < 15:
        score += 10
    return score


def register_file(wav_src: Path) -> str:
    """复制 wav 到 VoiceStudio/voices/ 并注册到数据库"""
    file_id = hashlib.md5(f"{wav_src}{time.time()}".encode()).hexdigest()[:12]
    dst = VOICE_DIR / f"{file_id}.wav"
    shutil.copy2(wav_src, dst)

    db = sqlite3.connect(str(DB_PATH))
    file_data = json.dumps({
        "file_id": file_id,
        "original_name": wav_src.name,
        "path": str(dst),
        "mime_type": "audio/wav",
        "duration_ms": 0,
        "sample_rate": 0,
    }, ensure_ascii=False)
    db.execute("INSERT INTO voice_files (file_id, data, created_at) VALUES (?, ?, ?)",
               (file_id, file_data, time.strftime("%Y-%m-%dT%H:%M:%S")))
    db.commit()
    db.close()
    return file_id


def update_voice(voice_id: str, new_audio_id: str, new_text: str, old_audio_ids: list[str]):
    """更新音色：替换参考音频和文本"""
    db = sqlite3.connect(str(DB_PATH))

    # 删除旧音频文件
    for old_id in old_audio_ids:
        row = db.execute("SELECT data FROM voice_files WHERE file_id = ?", (old_id,)).fetchone()
        if row:
            fd = json.loads(row[0])
            old_path = fd.get("path", "")
            if old_path and os.path.exists(old_path):
                os.remove(old_path)
            db.execute("DELETE FROM voice_files WHERE file_id = ?", (old_id,))

    # 更新音色数据
    row = db.execute("SELECT data FROM voices WHERE voice_id = ?", (voice_id,)).fetchone()
    if not row:
        db.close()
        return
    v = json.loads(row[0])
    v["reference_audio_ids"] = [new_audio_id]
    v["reference_text"] = new_text
    db.execute("UPDATE voices SET data = ?, updated_at = ? WHERE voice_id = ?",
               (json.dumps(v, ensure_ascii=False), time.strftime("%Y-%m-%dT%H:%M:%S"), voice_id))
    db.commit()
    db.close()


def main():
    short_voices = get_short_voices()
    print(f"共 {len(short_voices)} 条短参考文本需要替换\n")

    replaced = 0
    skipped = 0

    for v in short_voices:
        name = v["name"]
        vid = v["voice_id"]
        old_text = v["old_text"]

        char_dir = pack_dir_for(name)
        if not char_dir:
            print(f"⏭️ {name}: 语音包中未找到目录")
            skipped += 1
            continue

        wav_count = len(list(char_dir.glob("*.wav")))
        if wav_count == 0:
            print(f"⏭️ {name}: 目录无 wav 文件")
            skipped += 1
            continue

        print(f"🔍 {name} ({wav_count} 个候选): 「{old_text}」→ 搜索中...")

        # 选候选文件
        candidates = pick_best_wavs(char_dir, count=8)

        # ASR 识别每个候选
        best_text = None
        best_score = -999
        best_wav = None

        for wav in candidates:
            text = asr_transcribe(str(wav))
            if not text:
                continue
            sc = score_text(text)
            if sc > best_score:
                best_score = sc
                best_text = text
                best_wav = wav

        if not best_text or best_score < 15:
            print(f"  ⚠️ 未找到合适替换 (best_score={best_score})")
            skipped += 1
            continue

        print(f"  ✅ 替换为: 「{best_text}」(score={best_score:.0f}, from {best_wav.name})")

        # 注册新文件并更新
        new_id = register_file(best_wav)
        update_voice(vid, new_id, best_text, v["audio_ids"])
        replaced += 1

        # 避免 ASR 过载
        time.sleep(0.3)

    print(f"\n完成：替换 {replaced} 条，跳过 {skipped} 条，共 {len(short_voices)} 条")


if __name__ == "__main__":
    main()
