from __future__ import annotations

from app.models.schemas import PresetTemplate


_COMMON = {
    "language": "zh",
    "output_format": "wav",
    "top_p": 0.8,
    "top_k": 30,
    "repetition_penalty": 10.0,
    "max_mel_tokens": 1500,
    "segment_overlap_ms": 50,
    "diffusion_steps": 25,
    "cfg_rate": 0.7,
}


PRESETS: list[PresetTemplate] = [
    PresetTemplate(
        preset_id="idx2_default_narration",
        name="默认自然旁白",
        scene="课程 / 口播 / 批量生成",
        description="最稳的 IndexTTS v2 基线参数，适合大多数中文旁白。",
        engine_id="indextts-v2",
        sample_text="大家好，欢迎来到本期内容。今天我们用一组标准样本，测试本地语音工作站的合成效果。",
        parameters={**_COMMON, "emotion": "calm", "emo_alpha": 0.6, "speed": 1.0, "temperature": 0.8, "max_text_tokens_per_segment": 120, "interval_silence": 200},
        source_test_id="idx2_baseline_calm",
        tags=["稳定", "旁白", "主力"],
    ),
    PresetTemplate(
        preset_id="idx2_happy_light",
        name="轻微开心",
        scene="商业解说 / 正向反馈",
        description="情绪轻微上扬，不会过度表演。",
        engine_id="indextts-v2",
        sample_text="这个结果比预期更好，我们可以放心进入下一步制作。",
        parameters={**_COMMON, "emotion": "happy", "emo_alpha": 0.35, "speed": 1.0, "temperature": 0.8, "max_text_tokens_per_segment": 120, "interval_silence": 200},
        source_test_id="idx2_happy_low",
        tags=["开心", "轻情绪"],
    ),
    PresetTemplate(
        preset_id="idx2_emphasis_happy",
        name="强情绪短句",
        scene="片头 / 转场 / 高潮",
        description="用于短句强调，长文本慎用。",
        engine_id="indextts-v2",
        sample_text="太好了！这个版本终于跑通了，接下来就能真正投入使用了。",
        parameters={**_COMMON, "emotion": "happy", "emo_alpha": 0.85, "speed": 1.0, "temperature": 0.8, "max_text_tokens_per_segment": 120, "interval_silence": 200},
        source_test_id="idx2_happy_high",
        tags=["强情绪", "短视频"],
    ),
    PresetTemplate(
        preset_id="idx2_tutorial_slow",
        name="教程慢讲",
        scene="教程 / 重点提示 / 复杂概念",
        description="降低语速，增强清晰度和字幕可读性。",
        engine_id="indextts-v2",
        sample_text="请注意，这一步非常关键。我们需要先确认参数，再开始批量生成。",
        parameters={**_COMMON, "emotion": "calm", "emo_alpha": 0.6, "speed": 0.82, "temperature": 0.8, "max_text_tokens_per_segment": 120, "interval_silence": 200},
        source_test_id="idx2_speed_slow",
        tags=["教程", "慢速"],
    ),
    PresetTemplate(
        preset_id="idx2_short_video_fast",
        name="信息流快讲",
        scene="短视频 / 高密度信息",
        description="提升节奏，适合短视频信息压缩，但要人工确认咬字。",
        engine_id="indextts-v2",
        sample_text="如果你只想快速了解结论，记住三件事：先选声音，再调情绪，最后保存历史。",
        parameters={**_COMMON, "emotion": "calm", "emo_alpha": 0.6, "speed": 1.22, "temperature": 0.8, "max_text_tokens_per_segment": 120, "interval_silence": 200},
        source_test_id="idx2_speed_fast",
        tags=["快节奏", "短视频"],
    ),
    PresetTemplate(
        preset_id="idx2_long_text_editing",
        name="长文本剪辑",
        scene="长文 / 卡点 / 分段剪辑",
        description="缩短文本分段并增加段间留白，方便剪辑和字幕卡点。",
        engine_id="indextts-v2",
        sample_text="第一段，我们先介绍背景。第二段，我们说明方法。第三段，我们总结结论。每一段之间都应该有清楚的停顿，方便后期剪辑。",
        parameters={**_COMMON, "emotion": "calm", "emo_alpha": 0.6, "speed": 1.0, "temperature": 0.8, "max_text_tokens_per_segment": 45, "interval_silence": 650},
        source_test_id="idx2_long_segment",
        tags=["长文本", "剪辑"],
    ),
    PresetTemplate(
        preset_id="omni_young_female_design",
        name="OmniVoice 女青年设计",
        scene="角色试音 / 无参考音频",
        description="不依赖克隆音频，通过声音属性标签快速创建角色声线。",
        engine_id="omnivoice",
        sample_text="这是 OmniVoice 声音设计模式，不依赖参考音频，适合快速创建角色声线。",
        parameters={"language": "zh", "output_format": "wav", "emotion_text": "女，青年，中音调", "speed": 1.0, "temperature": 0.8},
        source_test_id="omni_design_female",
        recommended_voice_type="voice_design",
        tags=["声音设计", "女声"],
    ),
]


def list_presets() -> list[PresetTemplate]:
    return PRESETS


def get_preset(preset_id: str) -> PresetTemplate | None:
    return next((preset for preset in PRESETS if preset.preset_id == preset_id), None)
