from __future__ import annotations

from app.models.schemas import PresetTemplate, PresetTemplateUpsert, new_id, now_iso
from app.services import database


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


BUILTIN_PRESETS: list[PresetTemplate] = [
    PresetTemplate(
        preset_id="idx2_default_narration",
        name="贴近参考音色",
        scene="课程 / 口播 / 批量生成",
        description="不额外叠加情绪向量，优先贴近参考音色本身。",
        engine_id="indextts-v2",
        sample_text="大家好，欢迎来到本期内容。今天我们用一组标准样本，测试本地语音工作站的合成效果。",
        parameters={**_COMMON, "emotion": None, "emo_alpha": 0.0, "speed": 1.0, "temperature": 0.8, "max_text_tokens_per_segment": 120, "interval_silence": 200},
        source_test_id="idx2_reference_follow",
        tags=["贴近参考", "旁白", "主力"],
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
    PresetTemplate(
        preset_id="emotivoice_official_clear_female",
        name="清晰女声开心",
        scene="EmotiVoice 官方 speaker / 情绪短句",
        description="使用官方 voice wiki 中的 8051 Maria Kasper，配合开心情绪提示；这是预置 speaker，不使用本地参考音色。",
        engine_id="emotivoice",
        sample_text="大家好，欢迎来到本期内容。今天我们用轻松一点的语气开始。",
        parameters={"language": "zh", "output_format": "wav", "speaker_id": "8051", "prompt": "开心"},
        source_test_id="emotivoice_8051_happy",
        recommended_voice_type="preset_voice",
        tags=["官方音色", "女声", "开心"],
    ),
    PresetTemplate(
        preset_id="emotivoice_official_rich_male",
        name="浑厚男声中立",
        scene="EmotiVoice 官方 speaker / 稳定旁白",
        description="使用官方 voice wiki 中的 9017 John Van Stan，配合中立提示，适合稳一点的试音。",
        engine_id="emotivoice",
        sample_text="这是一段稳定旁白测试，用来确认音色、节奏和清晰度。",
        parameters={"language": "zh", "output_format": "wav", "speaker_id": "9017", "prompt": "中立"},
        source_test_id="emotivoice_9017_neutral",
        recommended_voice_type="preset_voice",
        tags=["官方音色", "男声", "中立"],
    ),
    PresetTemplate(
        preset_id="emotivoice_lively_female",
        name="活泼女声兴奋",
        scene="EmotiVoice 官方 speaker / 角色试音",
        description="使用官方 voice wiki 中的 92 Cori Samuel，配合兴奋提示，适合短句角色感测试。",
        engine_id="emotivoice",
        sample_text="太好了，这个版本终于跑通了，我们可以继续往下测试。",
        parameters={"language": "zh", "output_format": "wav", "speaker_id": "92", "prompt": "兴奋"},
        source_test_id="emotivoice_92_excited",
        recommended_voice_type="preset_voice",
        tags=["官方音色", "女声", "兴奋"],
    ),
    PresetTemplate(
        preset_id="f5_official_default_clone",
        name="官方默认复刻",
        scene="F5-TTS 官方默认参数",
        description="使用 F5-TTS 官方默认推理参数：NFE 32、CFG 2.0、RMS 0.1、交叉淡化 0.15。需要本地参考音频和准确参考台词。",
        engine_id="f5-tts",
        sample_text="这是 F5 TTS 的参考音色复刻测试。请确认音色库里已经补全参考台词。",
        parameters={"language": "zh", "output_format": "wav", "speed": 1.0, "nfe_step": 32, "cfg_strength": 2.0, "target_rms": 0.1, "cross_fade_duration": 0.15, "remove_silence": False},
        source_test_id="f5_official_basic",
        recommended_voice_type="reference_voice",
        tags=["官方默认", "参考音色", "F5"],
    ),
    PresetTemplate(
        preset_id="f5_fast_preview",
        name="快速试听",
        scene="F5-TTS 本地快速测试",
        description="降低采样步数以加快本地试听；质量和贴近度可能低于官方默认。",
        engine_id="f5-tts",
        sample_text="这是一段快速试听文本，用来先判断参考音色是否可用。",
        parameters={"language": "zh", "output_format": "wav", "speed": 1.0, "nfe_step": 16, "cfg_strength": 1.5, "target_rms": 0.1, "cross_fade_duration": 0.15, "remove_silence": False},
        source_test_id="f5_fast_preview",
        recommended_voice_type="reference_voice",
        tags=["快速", "参考音色", "F5"],
    ),
    PresetTemplate(
        preset_id="f5_clean_cut",
        name="短句去静音",
        scene="F5-TTS 短句 / 素材剪辑",
        description="开启生成后静音裁剪，适合短句素材；需要自然停顿时不要使用。",
        engine_id="f5-tts",
        sample_text="请把这句话生成得干净一些，方便后期剪辑。",
        parameters={"language": "zh", "output_format": "wav", "speed": 1.0, "nfe_step": 32, "cfg_strength": 2.0, "target_rms": 0.1, "cross_fade_duration": 0.15, "remove_silence": True},
        source_test_id="f5_remove_silence",
        recommended_voice_type="reference_voice",
        tags=["剪辑", "去静音", "F5"],
    ),
    PresetTemplate(
        preset_id="cosy_sft_zh_female",
        name="中文女声",
        scene="CosyVoice SFT 官方预置音色",
        description="使用 CosyVoice SFT 官方预置 speaker：中文女。不使用本地参考音色。",
        engine_id="cosyvoice-sft",
        sample_text="大家好，这是 CosyVoice 官方预置中文女声的试听。",
        parameters={"language": "zh", "output_format": "wav", "speaker_id": "中文女", "speed": 1.0},
        source_test_id="cosy_sft_zh_female",
        recommended_voice_type="preset_voice",
        tags=["官方预置", "中文女"],
    ),
    PresetTemplate(
        preset_id="cosy_sft_zh_male",
        name="中文男声",
        scene="CosyVoice SFT 官方预置音色",
        description="使用 CosyVoice SFT 官方预置 speaker：中文男。不使用本地参考音色。",
        engine_id="cosyvoice-sft",
        sample_text="大家好，这是 CosyVoice 官方预置中文男声的试听。",
        parameters={"language": "zh", "output_format": "wav", "speaker_id": "中文男", "speed": 1.0},
        source_test_id="cosy_sft_zh_male",
        recommended_voice_type="preset_voice",
        tags=["官方预置", "中文男"],
    ),
    PresetTemplate(
        preset_id="cosy_sft_yue_female",
        name="粤语女声",
        scene="CosyVoice SFT 官方预置音色",
        description="使用 CosyVoice SFT 官方预置 speaker：粤语女，适合粤语或粤语风格短句测试。",
        engine_id="cosyvoice-sft",
        sample_text="大家好，这是 CosyVoice 粤语女声的试听。",
        parameters={"language": "yue", "output_format": "wav", "speaker_id": "粤语女", "speed": 1.0},
        source_test_id="cosy_sft_yue_female",
        recommended_voice_type="preset_voice",
        tags=["官方预置", "粤语"],
    ),
    PresetTemplate(
        preset_id="cosy_zero_reference_default",
        name="参考音色复刻",
        scene="CosyVoice Zero-Shot",
        description="使用本地参考音频和准确参考台词进行 CosyVoice zero-shot 复刻；目标文本不要明显短于参考文本。",
        engine_id="cosyvoice-zero-shot",
        sample_text="这是 CosyVoice Zero-Shot 的参考音色复刻测试，请确认参考台词已经填写准确。",
        parameters={"language": "zh", "output_format": "wav", "speed": 1.0},
        source_test_id="cosy_zero_reference_default",
        recommended_voice_type="reference_voice",
        tags=["参考音色", "zero-shot"],
    ),
    PresetTemplate(
        preset_id="cosy_zero_slow_clear",
        name="慢速清晰",
        scene="CosyVoice Zero-Shot / 教程",
        description="降低语速以提升清晰度；仍然需要本地参考音频和准确参考台词。",
        engine_id="cosyvoice-zero-shot",
        sample_text="请用更清楚的节奏读出这句话，方便我们检查发音和音色贴近度。",
        parameters={"language": "zh", "output_format": "wav", "speed": 0.9},
        source_test_id="cosy_zero_slow_clear",
        recommended_voice_type="reference_voice",
        tags=["参考音色", "慢速"],
    ),
]


def list_presets() -> list[PresetTemplate]:
    custom = [PresetTemplate(**row) for row in database.list_all("presets", "updated_at")]
    return BUILTIN_PRESETS + custom


def get_preset(preset_id: str) -> PresetTemplate | None:
    builtin = next((preset for preset in BUILTIN_PRESETS if preset.preset_id == preset_id), None)
    if builtin:
        return builtin
    row = database.get_one("presets", "preset_id", preset_id)
    return PresetTemplate(**row) if row else None


def is_builtin(preset_id: str) -> bool:
    return any(preset.preset_id == preset_id for preset in BUILTIN_PRESETS)


def save_preset(payload: PresetTemplateUpsert) -> PresetTemplate:
    preset_id = payload.preset_id or f"custom_{new_id()}"
    if is_builtin(preset_id):
        raise ValueError("BUILTIN_PRESET_READONLY")
    preset = PresetTemplate(
        preset_id=preset_id,
        name=payload.name,
        scene=payload.scene,
        description=payload.description,
        engine_id=payload.engine_id,
        sample_text=payload.sample_text,
        parameters=payload.parameters,
        source_test_id=payload.source_test_id,
        recommended_voice_type=payload.recommended_voice_type,
        tags=payload.tags,
    )
    data = preset.model_dump(mode="json")
    data["updated_at"] = now_iso()
    database.upsert("presets", preset.preset_id, data)
    return preset


def delete_preset(preset_id: str) -> bool:
    if is_builtin(preset_id):
        raise ValueError("BUILTIN_PRESET_READONLY")
    if not database.get_one("presets", "preset_id", preset_id):
        return False
    database.delete_one("presets", "preset_id", preset_id)
    return True
