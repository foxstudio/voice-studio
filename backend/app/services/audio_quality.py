"""参考音频质量检测"""

import os
import struct
import wave


def analyze_audio(file_path: str) -> dict:
    """分析音频文件基础质量指标"""
    result = {
        "format": os.path.splitext(file_path)[1].lower(),
        "file_size_bytes": 0,
        "duration_seconds": 0,
        "sample_rate": 0,
        "channels": 0,
        "bit_depth": 0,
        "has_long_silence": False,
        "warnings": [],
        "passed": True,
    }

    if not os.path.exists(file_path):
        result["passed"] = False
        result["warnings"].append("文件不存在")
        return result

    result["file_size_bytes"] = os.path.getsize(file_path)

    # 文件大小检查（< 1KB 或 > 50MB）
    if result["file_size_bytes"] < 1024:
        result["warnings"].append("文件过小，可能无效")
        result["passed"] = False
    if result["file_size_bytes"] > 50 * 1024 * 1024:
        result["warnings"].append("文件过大（>50MB），建议裁剪")

    ext = result["format"]
    if ext == ".wav":
        _analyze_wav(file_path, result)
    elif ext in (".mp3", ".flac", ".ogg", ".m4a"):
        _analyze_generic(file_path, result)
    else:
        result["warnings"].append(f"不支持的格式: {ext}")

    # 时长检查
    dur = result["duration_seconds"]
    if dur < 1:
        result["warnings"].append("音频过短（<1秒），建议至少3秒")
        result["passed"] = False
    elif dur < 3:
        result["warnings"].append("音频较短（<3秒），建议3-10秒效果最佳")
    elif dur > 30:
        result["warnings"].append("音频过长（>30秒），建议裁剪到10秒以内")

    return result


def _analyze_wav(path: str, result: dict) -> None:
    try:
        with wave.open(path, "rb") as w:
            channels = w.getnchannels()
            sample_width = w.getsampwidth()
            framerate = w.getframerate()
            nframes = w.getnframes()

            result["channels"] = channels
            result["sample_rate"] = framerate
            result["bit_depth"] = sample_width * 8
            result["duration_seconds"] = round(nframes / framerate, 2)

            if channels != 1:
                result["warnings"].append(f"非单声道（{channels}声道），建议使用单声道")

            if sample_width < 2:
                result["warnings"].append("位深度过低（<16bit）")

            # 采样率检查
            if framerate < 16000:
                result["warnings"].append(f"采样率过低（{framerate}Hz），建议≥16000Hz")

            # 检测超长静音（读取前 10 秒采样）
            check_frames = min(nframes, framerate * 10)
            frames = w.readframes(check_frames)
            samples = struct.unpack(f"<{check_frames * channels}h", frames)

            # 简单静音检测：连续 0.5 秒以上幅度 < 100 视为长静音
            silence_threshold = 100
            silence_count = 0
            max_silence = 0
            for s in samples:
                if abs(s) < silence_threshold:
                    silence_count += 1
                    max_silence = max(max_silence, silence_count)
                else:
                    silence_count = 0

            # 0.5 秒静音 = framerate/2 个采样
            if max_silence > framerate * 0.5:
                result["has_long_silence"] = True
                result["warnings"].append("检测到超长静音段，建议裁剪")
    except Exception as e:
        result["warnings"].append(f"WAV解析失败: {e}")
        result["passed"] = False


def _analyze_generic(path: str, result: dict) -> None:
    """非 WAV 格式的粗略分析"""
    ext = result["format"]
    file_size = result["file_size_bytes"]

    # 粗略估算时长（基于文件大小）
    if ext == ".mp3":
        # 128kbps mp3 ≈ 16KB/s
        result["duration_seconds"] = round(file_size / 16000, 1)
    elif ext == ".flac":
        # FLAC ≈ WAV/2
        result["duration_seconds"] = round(file_size / 32000, 1)
    else:
        result["duration_seconds"] = round(file_size / 16000, 1)

    result["warnings"].append(f"{ext} 格式，详细参数需转换后检测")
