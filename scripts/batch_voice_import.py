"""
从 800+克隆音色素材合集中，批量筛选优质音色并注册到 Voice Studio。

筛选标准:
- 时长 5-15 秒（IndexTTS 最佳参考长度）
- WAV/MP3 格式
- 文件大小合理（排除损坏文件）
- 按分类精选，避免重复

用法: python3 scripts/batch_voice_import.py
"""

import os
import sys
import json
import wave
import subprocess
import urllib.request
import urllib.error

VOICE_STUDIO_API = "http://localhost:8000/api"
BASE_DIR = os.path.expanduser("~/Desktop/800+克隆必备音色素材合集")

# 目标采样率
TARGET_SR = 22050
MIN_DURATION = 5.0
MAX_DURATION = 15.0
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# 手动精选的高质量音色（文件相对路径 → 注册信息）
# 格式: (相对路径, 显示名, voice_type, tags, description)
CURATED_VOICES = [
    # === 影视角色 ===
    ("逗哥音色整理合集/影视角色/孙悟空.wav", "孙悟空", "virtual_character",
     ["影视", "二次元", "男声", "经典角色"], "西游记经典角色，活泼灵动的猴王声线"),
    ("逗哥音色整理合集/影视角色/海绵宝宝.wav", "海绵宝宝", "virtual_character",
     ["影视", "二次元", "男声", "活泼", "动画"], "经典动画角色，高亢活泼的独特声线"),
    ("逗哥音色整理合集/影视角色/唐僧.wav", "唐僧", "virtual_character",
     ["影视", "二次元", "男声", "沉稳"], "西游记经典角色，温和沉稳的僧侣声线"),
    ("逗哥音色整理合集/影视角色/哪吒.wav", "哪吒", "virtual_character",
     ["影视", "二次元", "男声", "少年"], "经典神话角色，少年气的英武声线"),
    ("逗哥音色整理合集/影视角色/周星星.wav", "周星星", "celebrity_voice",
     ["影视", "明星", "男声", "搞笑"], "周星驰经典声线，无厘头喜剧风格"),
    ("逗哥音色整理合集/影视角色/武则天.wav", "武则天", "virtual_character",
     ["影视", "女声", "威严", "霸气"], "一代女皇，威严霸气的女帝声线"),
    ("逗哥音色整理合集/影视角色/如来佛祖.wav", "如来佛祖", "virtual_character",
     ["影视", "男声", "低沉", "庄严"], "佛祖声线，庄严浑厚的低音"),
    ("逗哥音色整理合集/影视角色/刻晴.wav", "刻晴", "virtual_character",
     ["影视", "二次元", "女声", "干练"], "原神角色，干练果断的女声"),
    ("逗哥音色整理合集/影视角色/八重神子.wav", "八重神子", "virtual_character",
     ["影视", "二次元", "女声", "妩媚"], "原神角色，妩媚神秘的成熟女声"),

    # === 角色扮演（特色声线）===
    ("逗哥音色整理合集/角色扮演/冰山女王.wav", "冰山女王", "virtual_character",
     ["角色扮演", "女声", "冷艳", "高贵"], "冷艳高贵的女王声线，适合反派或上位者角色"),
    ("逗哥音色整理合集/角色扮演/温润君子.wav", "温润君子", "virtual_character",
     ["角色扮演", "男声", "温柔", "儒雅"], "温文尔雅的君子声线，适合古风旁白"),
    ("逗哥音色整理合集/角色扮演/傲娇女王.wav", "傲娇女王", "virtual_character",
     ["角色扮演", "女声", "傲娇", "活力"], "傲娇活力的女王声线"),
    ("逗哥音色整理合集/角色扮演/甜心少女.wav", "甜心少女", "virtual_character",
     ["角色扮演", "女声", "甜美", "少女"], "甜美可人的少女声线"),
    ("逗哥音色整理合集/角色扮演/少年统帅.wav", "少年统帅", "virtual_character",
     ["角色扮演", "男声", "少年", "英气"], "英气勃发的少年统帅声线"),
    ("逗哥音色整理合集/角色扮演/得道高僧.wav", "得道高僧", "virtual_character",
     ["角色扮演", "男声", "低沉", "禅意"], "超脱世俗的高僧声线，适合旁白解说"),

    # === 不同情绪音色（配音常用）===
    ("不同情绪音色/男-低音、正派、冷静.wav", "正派冷静男声", "voice_style",
     ["情绪", "男声", "低沉", "正派"], "低沉有力的正派男声，适合纪录片旁白"),
    ("不同情绪音色/男-中音、清亮、潇洒.wav", "潇洒清亮男声", "voice_style",
     ["情绪", "男声", "清亮", "潇洒"], "潇洒清亮的中音男声"),
    ("不同情绪音色/男-儒雅、温柔、体贴.wav", "儒雅温柔男声", "voice_style",
     ["情绪", "男声", "温柔", "儒雅"], "儒雅温柔的男声，适合情感类旁白"),
    ("不同情绪音色/男-温暖、智勇双全、正直.wav", "正直温暖男声", "voice_style",
     ["情绪", "男声", "温暖", "正直"], "温暖正直的男声，适合叙事解说"),
    ("不同情绪音色/男-中音，平静，柔和.wav", "平静柔和男声", "voice_style",
     ["情绪", "男声", "柔和", "平静"], "平静柔和的男声，适合ASMR或轻柔旁白"),
    ("不同情绪音色/女-高音、明亮、热情.wav", "明亮热情女声", "voice_style",
     ["情绪", "女声", "明亮", "热情"], "明亮热情的女声，适合活力口播"),
    ("不同情绪音色/女-温柔、姐姐.wav", "温柔姐姐女声", "voice_style",
     ["情绪", "女声", "温柔", "姐姐"], "温柔亲切的姐姐声线"),
    ("不同情绪音色/女-古灵精怪、活泼、师姐.wav", "古灵精怪女声", "voice_style",
     ["情绪", "女声", "活泼", "俏皮"], "古灵精怪的女声，适合活泼角色"),

    # === 常用配音 - 精选 ===
    ("克隆参考音色/常用配音/李云龙.WAV", "李云龙", "celebrity_voice",
     ["影视", "明星", "男声", "霸气"], "亮剑经典角色，粗犷霸气的军人声线"),
    ("克隆参考音色/常用配音/曹操.WAV", "曹操", "virtual_character",
     ["影视", "男声", "霸气", "低沉"], "一代枭雄，霸气深沉的声线"),

    # === 年龄段/热门音色 ===
    ("不同年龄人群音色/热门音色/专业解说配音.wav", "专业解说配音", "voice_style",
     ["解说", "男声", "专业", "旁白"], "专业级解说声线，清晰有力"),
]


def api_post(path, data=None, files=None):
    """调用 Voice Studio API"""
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
        err_body = e.read().decode()[:300]
        print(f"  API error {e.code}: {err_body}")
        return None
    except Exception as e:
        print(f"  API error: {e}")
        return None


def get_wav_duration(filepath):
    """获取 WAV 文件时长"""
    try:
        with wave.open(filepath, 'r') as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return None


def get_mp3_duration(filepath):
    """用 ffprobe 获取 MP3 时长"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            capture_output=True, text=True, timeout=5
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def convert_to_wav_bytes(filepath):
    """将音频文件转为 WAV bytes（22050Hz mono）"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", filepath,
             "-ar", str(TARGET_SR), "-ac", "1", "-f", "wav",
             "-loglevel", "error", "pipe:1"],
            capture_output=True, timeout=30
        )
        if result.returncode == 0 and len(result.stdout) > 1000:
            return result.stdout
    except Exception as e:
        print(f"  ffmpeg 转换失败: {e}")
    return None


def get_duration(filepath):
    """获取音频文件时长"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.wav':
        return get_wav_duration(filepath)
    elif ext == '.mp3':
        return get_mp3_duration(filepath)
    return None


def find_file(rel_path):
    """查找文件，处理大小写变体"""
    filepath = os.path.join(BASE_DIR, rel_path)
    if os.path.exists(filepath):
        return filepath
    # 尝试扩展名大小写变体
    base, ext = os.path.splitext(filepath)
    alt = base + ext.swapcase()
    if os.path.exists(alt):
        return alt
    # 尝试在整个目录中搜索文件名
    filename = os.path.basename(rel_path)
    for root, dirs, files in os.walk(BASE_DIR):
        for f in files:
            if f.lower() == filename.lower():
                return os.path.join(root, f)
    return None


def main():
    print("=" * 60)
    print("批量音色导入器 - 800+克隆音色素材合集")
    print(f"待导入: {len(CURATED_VOICES)} 个精选音色")
    print(f"时长要求: {MIN_DURATION}-{MAX_DURATION}s")
    print("=" * 60)

    # 先检查 Voice Studio
    try:
        resp = urllib.request.urlopen(f"{VOICE_STUDIO_API}/voices")
        existing = json.loads(resp.read())
        existing_names = {v["name"] for v in existing}
        print(f"Voice Studio 运行中，已有 {len(existing)} 个音色")
    except Exception as e:
        print(f"错误: Voice Studio 未运行 - {e}")
        sys.exit(1)

    success = []
    skipped = []
    failed = []

    for i, (rel_path, display_name, voice_type, tags, description) in enumerate(CURATED_VOICES):
        print(f"\n[{i+1}/{len(CURATED_VOICES)}] {display_name}")

        # 检查是否已存在
        if display_name in existing_names:
            print(f"  跳过: 已存在")
            skipped.append(display_name)
            continue

        # 查找文件
        filepath = find_file(rel_path)
        if not filepath:
            print(f"  跳过: 文件不存在 - {rel_path}")
            skipped.append(display_name)
            continue

        # 检查文件大小
        file_size = os.path.getsize(filepath)
        if file_size > MAX_FILE_SIZE or file_size < 10000:
            print(f"  跳过: 文件大小异常 ({file_size/1024:.0f}KB)")
            skipped.append(display_name)
            continue

        # 检查时长
        duration = get_duration(filepath)
        if duration is None:
            print(f"  跳过: 无法读取时长")
            skipped.append(display_name)
            continue

        # 损坏检测：时长超过1小时视为损坏
        if duration > 3600:
            print(f"  跳过: 时长异常 ({duration:.0f}s，可能损坏)")
            skipped.append(display_name)
            continue

        print(f"  时长: {duration:.1f}s | 大小: {file_size/1024:.0f}KB")

        # 转换为标准 WAV
        wav_bytes = convert_to_wav_bytes(filepath)
        if not wav_bytes:
            print(f"  跳过: ffmpeg 转换失败")
            failed.append(display_name)
            continue

        # 上传音频文件
        result = api_post("/voices/upload", files={
            "file": (f"{display_name}.wav", wav_bytes),
            "name": display_name,
        })
        if not result or "file_id" not in result:
            print(f"  上传失败")
            failed.append(display_name)
            continue

        file_id = result["file_id"]
        print(f"  上传成功: {file_id}")

        # 注册音色
        voice_data = {
            "name": display_name,
            "voice_type": voice_type,
            "description": description,
            "default_language": "zh",
            "tags": tags,
            "reference_audio_ids": [file_id],
            "license_status": "test_only",
        }
        result = api_post("/voices", data=voice_data)
        if result and "voice_id" in result:
            voice_id = result["voice_id"]
            print(f"  注册成功: {voice_id[:12]}")
            success.append(display_name)
        else:
            print(f"  注册失败")
            failed.append(display_name)

    print(f"\n{'='*60}")
    print(f"完成!")
    print(f"  成功: {len(success)}")
    print(f"  跳过: {len(skipped)}")
    print(f"  失败: {len(failed)}")
    if success:
        print(f"\n已导入:")
        for name in success:
            print(f"  ✓ {name}")
    if failed:
        print(f"\n失败:")
        for name in failed:
            print(f"  x {name}")


if __name__ == "__main__":
    main()
