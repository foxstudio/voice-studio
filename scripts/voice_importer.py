"""
从 HuggingFace 按角色下载游戏语音，筛选优质片段，注册到 Voice Studio。

用法: .venv/bin/python3 scripts/voice_importer.py
"""

import os
import sys
import json
import tempfile
import urllib.request
import time

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from datasets import load_dataset
import soundfile as sf

VOICE_STUDIO_API = "http://localhost:8000/api"
MIN_DURATION = 5.0
MAX_DURATION = 15.0
MAX_CLIPS_PER_CHAR = 3

# 角色: (hf_dataset, config, speaker, 显示名, tags)
CHARACTERS = [
    # 星穹铁道 (simon3000/starrail-voice, speaker 用英文名)
    ("simon3000/starrail-voice", None, "Welt", "瓦尔特 Welt", ["星穹铁道", "二次元", "男声", "旁白", "低沉"]),
    ("simon3000/starrail-voice", None, "Dan Heng", "丹恒", ["星穹铁道", "二次元", "男声", "冷静"]),
    ("simon3000/starrail-voice", None, "March 7th", "三月七", ["星穹铁道", "二次元", "女声", "明亮"]),
    ("simon3000/starrail-voice", None, "Firefly", "流萤", ["星穹铁道", "二次元", "女声", "温柔"]),
    ("simon3000/starrail-voice", None, "Silver Wolf", "银狼", ["星穹铁道", "二次元", "女声", "酷"]),
    ("simon3000/starrail-voice", None, "Sparkle", "花火", ["星穹铁道", "二次元", "女声", "活泼"]),
]


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
        print(f"  API error {e.code}: {(e.read().decode())[:200]}")
        return None


def select_best_clips(ds_iter, speaker, max_clips=3):
    candidates = []
    count = 0
    for sample in ds_iter:
        count += 1
        if count > 1000:
            break
        spk = sample.get("speaker", "")
        if speaker not in spk and spk != speaker:
            continue
        lang = sample.get("language", "")
        if lang != "Chinese":
            continue
        audio = sample.get("audio", {})
        if not audio or "array" not in audio:
            continue
        arr = audio["array"]
        sr = audio.get("sampling_rate", 22050)
        duration = len(arr) / sr
        transcription = sample.get("transcription", "")
        if not transcription:
            continue
        if MIN_DURATION <= duration <= MAX_DURATION:
            candidates.append({
                "duration": duration,
                "transcription": transcription,
                "audio_array": arr,
                "sampling_rate": sr,
            })
        if len(candidates) >= max_clips * 3:
            break
    candidates.sort(key=lambda x: abs(x["duration"] - 9))
    return candidates[:max_clips]


def import_character(hf_dataset, hf_config, speaker, display_name, tags):
    print(f"\n{'='*50}")
    print(f"下载: {display_name} ({speaker})")
    print(f"{'='*50}")
    try:
        kwargs = {"split": "train", "streaming": True}
        if hf_config:
            kwargs["config"] = hf_config
        ds = load_dataset(hf_dataset, **kwargs)
    except Exception as e:
        print(f"  数据集加载失败: {e}")
        return None
    print(f"  筛选中...")
    clips = select_best_clips(ds, speaker, MAX_CLIPS_PER_CHAR)
    if not clips:
        print(f"  未找到合适片段")
        return None
    print(f"  找到 {len(clips)} 个片段:")
    for i, c in enumerate(clips):
        print(f"    [{i+1}] {c['duration']:.1f}s | {c['transcription'][:40]}...")

    file_ids = []
    for i, clip in enumerate(clips):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, clip["audio_array"], clip["sampling_rate"])
        tmp.close()
        style = ["自然", "明亮", "沉稳"][min(i, 2)]
        with open(tmp.name, "rb") as f:
            wav_data = f.read()
        os.unlink(tmp.name)
        result = api_post("/voices/upload", files={
            "file": (f"{display_name}_{style}.wav", wav_data),
            "name": f"{display_name}-{style}",
        })
        if result and "file_id" in result:
            file_ids.append(result["file_id"])
            print(f"  上传: {result['file_id']}")
        else:
            print(f"  上传失败")

    if not file_ids:
        return None
    voice = {
        "name": display_name,
        "voice_type": "virtual_character",
        "description": f"游戏角色中文配音，{len(file_ids)}个风格参考。",
        "default_language": "zh",
        "tags": tags + ["游戏"],
        "reference_text": clips[0]["transcription"],
        "reference_audio_ids": file_ids,
        "license_status": "test_only",
    }
    result = api_post("/voices", data=voice)
    if result and "voice_id" in result:
        print(f"  注册成功: {result['voice_id']}")
        return result["voice_id"]
    print(f"  注册失败")
    return None


def main():
    print("=" * 60)
    print("游戏语音音色导入器")
    print(f"角色: {len(CHARACTERS)} | 每角色最多: {MAX_CLIPS_PER_CHAR} 段")
    print(f"时长: {MIN_DURATION}-{MAX_DURATION}s")
    print("=" * 60)

    results = []
    for i, (dataset, config, speaker, name, tags) in enumerate(CHARACTERS):
        print(f"\n[{i+1}/{len(CHARACTERS)}]")
        try:
            vid = import_character(dataset, config, speaker, name, tags)
            if vid:
                results.append((name, vid))
        except Exception as e:
            print(f"  错误: {e}")

    print(f"\n{'='*60}")
    print(f"完成! 成功: {len(results)}/{len(CHARACTERS)}")
    for name, vid in results:
        print(f"  - {name} ({vid})")


if __name__ == "__main__":
    main()
