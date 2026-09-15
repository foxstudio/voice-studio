"""
全量导入 800+克隆音色素材合集到 Voice Studio。
自动分类、转换、去重、批量注册。

用法: python3 scripts/full_voice_import.py
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.error

VOICE_STUDIO_API = "http://localhost:8000/api"
BASE_DIR = os.path.expanduser("~/Desktop/800+克隆必备音色素材合集")
TARGET_SR = 22050
MIN_DURATION = 2.0
MAX_DURATION = 60.0

# 目录 → voice_type 映射
DIR_VOICE_TYPE = {
    "影视角色": "virtual_character",
    "角色扮演": "virtual_character",
    "豆哥方言": "virtual_character",
    "逗哥热门音色": "narrator",
    "情绪音色": "emotion_reference",
    "热门音色": "narrator",
    "中年-男声": "narrator",
    "孩童": "virtual_character",
    "老年": "narrator",
    "常用配音": "narrator",
    "火爆音色": "narrator",
    "王也": "virtual_character",
    "音色文件": "virtual_character",
    "马宝国": "real_person",
}

# 目录 → tags 映射
DIR_TAGS = {
    "影视角色": ["影视", "二次元"],
    "角色扮演": ["角色扮演"],
    "豆哥方言": ["方言"],
    "逗哥热门音色": ["热门", "口播"],
    "情绪音色": ["情绪"],
    "热门音色": ["热门"],
    "中年-男声": ["中年", "男声"],
    "孩童": ["孩童", "童声"],
    "老年": ["老年"],
    "常用配音": ["配音", "经典"],
    "火爆音色": ["火爆", "热门"],
    "王也": ["一人之下", "二次元"],
    "音色文件": ["人物"],
    "马宝国": ["马保国", "搞笑", "梗"],
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


def get_duration_ffprobe(filepath):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            capture_output=True, text=True, timeout=5
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def to_wav_bytes(filepath):
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", filepath,
             "-ar", str(TARGET_SR), "-ac", "1", "-f", "wav",
             "-loglevel", "error", "pipe:1"],
            capture_output=True, timeout=30
        )
        if r.returncode == 0 and len(r.stdout) > 1000:
            return r.stdout
    except Exception:
        pass
    return None


def classify_file(rel_path):
    """根据目录路径推断 voice_type 和 tags"""
    parts = rel_path.replace("\\", "/").split("/")
    voice_type = "narrator"
    tags = []
    for part in parts:
        if part in DIR_VOICE_TYPE:
            voice_type = DIR_VOICE_TYPE[part]
        if part in DIR_TAGS:
            tags = DIR_TAGS[part]
    return voice_type, tags


def make_display_name(filename, parent_dir):
    """从文件名生成显示名"""
    name = os.path.splitext(filename)[0]
    name = name.strip()
    if not name:
        name = parent_dir
    return name


def scan_all_files():
    """扫描所有音频文件"""
    files = []
    for root, _, fs in os.walk(BASE_DIR):
        for f in sorted(fs):
            if f.lower().endswith(('.wav', '.mp3')):
                filepath = os.path.join(root, f)
                rel = os.path.relpath(filepath, BASE_DIR)
                parent = os.path.basename(os.path.dirname(filepath))
                files.append((filepath, rel, f, parent))
    return files


def main():
    print("=" * 60)
    print("全量音色导入 - 800+克隆音色素材合集")
    print("=" * 60)

    try:
        resp = urllib.request.urlopen(f"{VOICE_STUDIO_API}/voices")
        existing = json.loads(resp.read())
        existing_names = {v["name"] for v in existing}
        print(f"Voice Studio 运行中，已有 {len(existing)} 个音色")
    except Exception as e:
        print(f"Voice Studio 未运行: {e}")
        sys.exit(1)

    all_files = scan_all_files()
    print(f"扫描到 {len(all_files)} 个音频文件\n")

    # 过滤已存在的
    to_import = []
    for filepath, rel, filename, parent in all_files:
        display_name = make_display_name(filename, parent)
        if display_name in existing_names:
            continue
        to_import.append((filepath, rel, filename, parent, display_name))

    print(f"已有/跳过: {len(all_files) - len(to_import)} 个")
    print(f"待导入: {len(to_import)} 个\n")

    success = 0
    failed = 0
    skipped = 0
    name_counter = {}

    for i, (filepath, rel, filename, parent, base_name) in enumerate(to_import):
        # 处理重名
        if base_name in name_counter:
            name_counter[base_name] += 1
            display_name = f"{base_name}-{name_counter[base_name]}"
        else:
            name_counter[base_name] = 0
            display_name = base_name

        if i % 50 == 0 or i == len(to_import) - 1:
            print(f"[{i+1}/{len(to_import)}] 已导入:{success} 跳过:{skipped} 失败:{failed}")

        # 文件大小检查
        fsize = os.path.getsize(filepath)
        if fsize < 5000 or fsize > 10 * 1024 * 1024:
            skipped += 1
            continue

        # 时长检查
        duration = get_duration_ffprobe(filepath)
        if duration and (duration < MIN_DURATION or duration > MAX_DURATION):
            skipped += 1
            continue

        # 转换
        wav_bytes = to_wav_bytes(filepath)
        if not wav_bytes:
            failed += 1
            continue

        # 分类
        voice_type, tags = classify_file(rel)

        # 上传
        result = api_post("/voices/upload", files={
            "file": (f"{display_name}.wav", wav_bytes),
            "name": display_name,
        })
        if not result or "file_id" not in result:
            if result and result.get("_duplicate"):
                skipped += 1
                continue
            failed += 1
            continue

        file_id = result["file_id"]

        # 注册
        voice_data = {
            "name": display_name,
            "voice_type": voice_type,
            "description": f"来源: 800+克隆音色包/{rel}",
            "default_language": "zh",
            "tags": tags,
            "reference_audio_ids": [file_id],
            "license_status": "test_only",
        }
        result = api_post("/voices", data=voice_data)
        if result and "voice_id" in result:
            success += 1
        elif result and result.get("_duplicate"):
            skipped += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"导入完成!")
    print(f"  成功: {success}")
    print(f"  跳过(太短/太长/重名/已存在): {skipped}")
    print(f"  失败: {failed}")
    print(f"  总计处理: {len(to_import)}")

    resp = urllib.request.urlopen(f"{VOICE_STUDIO_API}/voices")
    final_count = len(json.loads(resp.read()))
    print(f"\n音色库总量: {final_count}")


if __name__ == "__main__":
    main()
