from __future__ import annotations

from app.schemas.voice_studio import EngineDetail, EngineManifest, EngineState, EngineStatus, ParameterSchema
from app.services import doubao_client, mimo_client


EMOTION_OPTIONS = [
    {"label": "自然 calm", "value": "calm"},
    {"label": "高兴 happy", "value": "happy"},
    {"label": "悲伤 sad", "value": "sad"},
    {"label": "愤怒 angry", "value": "angry"},
    {"label": "恐惧 afraid", "value": "afraid"},
    {"label": "反感 disgusted", "value": "disgusted"},
    {"label": "低落 melancholic", "value": "melancholic"},
    {"label": "惊讶 surprised", "value": "surprised"},
]

EMOTIVOICE_PROMPTS = [
    {"label": "开心", "value": "开心"},
    {"label": "兴奋", "value": "兴奋"},
    {"label": "悲伤", "value": "悲伤"},
    {"label": "愤怒", "value": "愤怒"},
    {"label": "害怕", "value": "害怕"},
    {"label": "惊讶", "value": "惊讶"},
    {"label": "厌恶", "value": "厌恶"},
    {"label": "中立", "value": "中立"},
]

COSYVOICE_SPEAKERS = [
    {"label": "中文女", "value": "中文女"},
    {"label": "中文男", "value": "中文男"},
    {"label": "粤语女", "value": "粤语女"},
    {"label": "日语男", "value": "日语男"},
    {"label": "韩语女", "value": "韩语女"},
]

EMOTIVOICE_SPEAKERS = [
    {"label": "8051 Maria Kasper 女声 · 清晰舒缓", "value": "8051"},
    {"label": "11614 Sylviamb 女声 · 清脆旋律", "value": "11614"},
    {"label": "9017 John Van Stan 男声 · 浑厚共鸣", "value": "9017"},
    {"label": "6097 Phil Benson 男声 · 平滑醇厚", "value": "6097"},
    {"label": "6671 Tony Oliva 男声 · 有魅力", "value": "6671"},
    {"label": "6670 Mike Pelton 男声", "value": "6670"},
    {"label": "9136 Helen Taylor 女声", "value": "9136"},
    {"label": "11697 Celine Major 女声", "value": "11697"},
    {"label": "92 Cori Samuel 女声 · 活泼有能量", "value": "92"},
    {"label": "12787 LikeManyWaters 女声", "value": "12787"},
    {"label": "1006 Marta Kornowska 女声", "value": "1006"},
    {"label": "1018 JimmyLogan 男声", "value": "1018"},
]

OMNIVOICE_LANGUAGES = [
    {"label": "自动", "value": "auto"},
    {"label": "中文 zh", "value": "zh"},
    {"label": "英文 en", "value": "en"},
    {"label": "日语 ja", "value": "ja"},
    {"label": "韩语 ko", "value": "ko"},
    {"label": "法语 fr", "value": "fr"},
    {"label": "德语 de", "value": "de"},
    {"label": "西班牙语 es", "value": "es"},
]

CONFUCIUS4_LANGUAGES = [
    {"label": "中文 zh", "value": "zh"},
    {"label": "英文 en", "value": "en"},
    {"label": "日语 ja", "value": "ja"},
    {"label": "韩语 ko", "value": "ko"},
    {"label": "德语 de", "value": "de"},
    {"label": "法语 fr", "value": "fr"},
    {"label": "西班牙语 es", "value": "es"},
    {"label": "印尼语 id", "value": "id"},
    {"label": "意大利语 it", "value": "it"},
    {"label": "泰语 th", "value": "th"},
    {"label": "葡萄牙语 pt", "value": "pt"},
    {"label": "俄语 ru", "value": "ru"},
    {"label": "马来语 ms", "value": "ms"},
    {"label": "越南语 vi", "value": "vi"},
]

QWEN3_TTS_SPEAKERS = [
    {"label": "Vivian · 中文/英文女声", "value": "Vivian"},
    {"label": "Serena · 中文/英文女声", "value": "Serena"},
    {"label": "Uncle_Fu · 中文男声", "value": "Uncle_Fu"},
    {"label": "Dylan · 中文男声", "value": "Dylan"},
    {"label": "Eric · 中文男声", "value": "Eric"},
    {"label": "Ryan · 英文男声", "value": "Ryan"},
    {"label": "Aiden · 英文男声", "value": "Aiden"},
    {"label": "Ethan · 英文男声", "value": "Ethan"},
    {"label": "Chelsie · 英文女声", "value": "Chelsie"},
    {"label": "Ono_Anna · 日文女声", "value": "Ono_Anna"},
    {"label": "Sohee · 韩文女声", "value": "Sohee"},
]




def common_params(v2: bool = False) -> list[ParameterSchema]:
    params = [
        ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05, description="控制说话速度。1.0=正常语速，大于1加速，小于1减速"),
        ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.8 if v2 else 1.0, min=0.1, max=2.0, step=0.05, level="advanced" if v2 else None, description="控制语音随机性，越低越稳定，越高越有变化"),
        ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.8, min=0, max=1, step=0.05, level="advanced", description="核采样概率，控制词汇选择范围"),
        ParameterSchema(key="top_k", label="候选数量 Top-K", type="slider", default=30, min=1, max=100, step=1, level="advanced", description="保留最高概率的词汇数量"),
        ParameterSchema(key="max_text_tokens_per_segment", label="分段 Token", type="slider", default=120, min=20, max=500, step=10, level="advanced", description="每段文本最大token数，影响分段长度"),
        ParameterSchema(key="interval_silence", label="段间静默 ms", type="slider", default=200, min=0, max=2000, step=50, level="advanced", description="段落间静音时长(毫秒)"),
    ]
    if v2:
        params.extend([
            ParameterSchema(
                key="emotion",
                label="情绪",
                type="select",
                default="calm",
                options=EMOTION_OPTIONS,
                capability="emotion_control",
                description="选择情感类型，如高兴、悲伤、愤怒等",
            ),
            ParameterSchema(key="emo_alpha", label="情绪强度", type="slider", default=0.6, min=0, max=1, step=0.05, capability="emotion_control", description="情感强度，0.0=无情感，1.0=最大情感表达"),
            ParameterSchema(key="max_mel_tokens", label="最大 Mel Token", type="slider", default=1500, min=50, max=1815, step=50, level="advanced", description="生成 Mel Token 最大数量，过小会导致音频截断"),
            ParameterSchema(key="repetition_penalty", label="重复惩罚", type="slider", default=10.0, min=0.1, max=20.0, step=0.5, level="advanced", description="重复惩罚系数，越高越不容易重复"),
            ParameterSchema(key="diffusion_steps", label="扩散步数 Diffusion Steps", type="slider", default=25, min=5, max=60, step=1, level="advanced", description="扩散模型步数，越高音质越好但速度越慢"),
            ParameterSchema(key="cfg_rate", label="引导强度 CFG Rate", type="slider", default=0.7, min=0, max=1, step=0.05, level="advanced", description="无分类器引导率，控制生成与提示的匹配度"),
        ])
    return params


ENGINES: dict[str, EngineDetail] = {
    "indextts-v2": EngineDetail(
        manifest=EngineManifest(
            engine_id="indextts-v2",
            display_name="IndexTTS v2",
            provider="Index Team",
            version="2.0",
            description="Bilibili IndexTeam 工业级零样本 TTS，支持 8 种情绪控制和声音克隆",
            supported_languages=["zh", "en"],
            capabilities=["local_inference", "voice_clone", "emotion_control", "long_text", "pinyin_control"],
            sample_rate=22050,
            max_tokens=1815,
            default_use_case="中文/英文情绪化配音",
            parameter_schema=common_params(v2=True),
        ),
        state=EngineState(engine_id="indextts-v2", status=EngineStatus.stopped),
    ),
    "omnivoice": EngineDetail(
        manifest=EngineManifest(
            engine_id="omnivoice",
            display_name="OmniVoice",
            provider="k2-fsa",
            version="0.1.5",
            description="k2-fsa 出品，600+ 语言零样本声音克隆与声音设计",
            supported_languages=["auto", "zh", "en", "ja", "ko", "fr", "de", "es"],
            capabilities=["local_inference", "voice_clone", "voice_design", "multilingual", "nonverbal_tags", "pinyin_control"],
            sample_rate=24000,
            default_use_case="多语言克隆与声音设计",
            parameter_schema=[
                ParameterSchema(key="language", label="语言", type="select", default="auto", options=OMNIVOICE_LANGUAGES, description="语言提示。自动可混语，手动选择通常更稳。"),
                ParameterSchema(key="emotion_text", label="声音描述/指令", type="textarea", default="", capability="voice_design", description="用文字描述想要的声音特征"),
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05, description="生成后无变调调整语速，优先保证文本完整覆盖"),
                ParameterSchema(key="diffusion_steps", label="扩散步数 Num Step", type="slider", default=32, min=4, max=64, step=1, level="advanced", description="OmniVoice 生成步数。越高通常越细致但更慢。"),
                ParameterSchema(key="guidance_scale", label="引导强度 Guidance", type="slider", default=2.0, min=0.1, max=5.0, step=0.1, level="advanced", description="控制生成结果贴合文本、音色或声音描述的力度。默认 2.0。"),
                ParameterSchema(key="duration", label="固定时长 s", type="number", default=0, min=0, max=120, step=0.5, level="advanced", description="可选。0 表示自动估算；填秒数会在完整生成后拉伸/压缩到目标时长。"),
                ParameterSchema(key="audio_chunk_duration", label="长文本分段目标 s", type="number", default=15.0, min=1.0, max=120.0, step=1.0, level="advanced", description="OmniVoice 官方长文本切分目标时长。默认每段约 15 秒。"),
                ParameterSchema(key="audio_chunk_threshold", label="长文本切分阈值 s", type="number", default=30.0, min=1.0, max=600.0, step=1.0, level="advanced", description="预计音频超过该时长时启用 OmniVoice 内置长文本切分。官方默认 30 秒。"),
            ],
        ),
        state=EngineState(engine_id="omnivoice", status=EngineStatus.stopped),
    ),
    "emotivoice": EngineDetail(
        manifest=EngineManifest(
            engine_id="emotivoice",
            display_name="EmotiVoice",
            provider="NetEase Youdao",
            version="open-source",
            description="网易有道开源中英双语情感 TTS，2000+ 预置音色，提示词控制情绪",
            supported_languages=["zh"],
            capabilities=["local_inference", "preset_voice", "emotion_control"],
            sample_rate=16000,
            default_use_case="中文角色感、情绪短句和音色预设试听",
            parameter_schema=[
                ParameterSchema(
                    key="speaker_id",
                    label="说话人",
                    type="select",
                    default="8051",
                    options=EMOTIVOICE_SPEAKERS,
                    required=True,
                    capability="preset_voice",
                    description="选择说话人ID",
                ),
                ParameterSchema(
                    key="prompt",
                    label="情绪提示",
                    type="select",
                    default="开心",
                    options=EMOTIVOICE_PROMPTS,
                    capability="emotion_control",
                    description="情感提示词，如'开心地'、'悲伤地'",
                ),
            ],
        ),
        state=EngineState(engine_id="emotivoice", status=EngineStatus.stopped),
    ),
    "confucius4-mlx-int8": EngineDetail(
        manifest=EngineManifest(
            engine_id="confucius4-mlx-int8",
            display_name="Confucius4-TTS MLX int8",
            provider="NetEase Youdao / Hert4 MLX",
            version="int8 MLX",
            description="网易有道子曰4 TTS 的 Apple Silicon MLX int8 版本，支持跨语种零样本声音克隆和情绪迁移",
            supported_languages=[item["value"] for item in CONFUCIUS4_LANGUAGES],
            capabilities=["local_inference", "voice_clone", "zero_shot", "emotion_transfer", "cross_lingual", "multilingual"],
            sample_rate=22050,
            default_use_case="使用音色库或自定义参考音频做跨语种情绪迁移",
            privacy_level="local_only_research",
            parameter_schema=[
                ParameterSchema(
                    key="language",
                    label="目标语言",
                    type="select",
                    default="zh",
                    options=CONFUCIUS4_LANGUAGES,
                    description="目标文本语言提示。跨语种生成时建议手动选择对应语言。",
                ),
                ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.8, min=0.1, max=1.5, step=0.05, level="advanced", description="控制生成随机性。官方默认 0.8；较低更稳定，较高更有变化。"),
                ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.8, min=0.01, max=1.0, step=0.01, level="advanced", description="核采样概率，控制候选范围。默认 0.8。"),
                ParameterSchema(key="top_k", label="候选数量 Top-K", type="slider", default=30, min=1, max=100, step=1, level="advanced", description="保留最高概率候选数量。默认 30。"),
                ParameterSchema(key="repetition_penalty", label="重复惩罚", type="slider", default=10.0, min=1.0, max=20.0, step=0.5, level="advanced", description="抑制重复发音或重复片段。默认 10。"),
                ParameterSchema(key="diffusion_steps", label="声学采样步数", type="slider", default=25, min=1, max=60, step=1, level="advanced", description="Confucius4 S2A 采样步数。官方默认 25；越高通常越细但更慢。"),
                ParameterSchema(key="cfg_rate", label="声学引导强度 CFG", type="slider", default=0.7, min=0.0, max=1.0, step=0.05, level="advanced", description="Confucius4 S2A classifier-free guidance。官方默认 0.7。"),
                ParameterSchema(key="seed", label="随机种子", type="number", default=0, min=0, max=2147483647, step=1, level="developer", description="用于可复现测试。0 表示使用固定默认种子。"),
            ],
        ),
        state=EngineState(engine_id="confucius4-mlx-int8", status=EngineStatus.stopped),
    ),
    "qwen3-tts-mlx-0.6b": EngineDetail(
        manifest=EngineManifest(
            engine_id="qwen3-tts-mlx-0.6b",
            display_name="Qwen3-TTS MLX 0.6B",
            provider="Qwen / MLX Community",
            version="0.6B 8-bit PoC",
            description="千问 Qwen3-TTS 的 Apple Silicon MLX 量化实验引擎，支持预置声音、声音设计、自然语言风格指令和本地参考音色克隆",
            supported_languages=["zh", "en", "ja", "ko"],
            capabilities=["local_inference", "preset_voice", "voice_design", "voice_clone", "natural_language_control", "multilingual"],
            sample_rate=24000,
            max_tokens=1200,
            default_use_case="本地短句口播、预置音色试听、声音设计和声音克隆 PoC",
            privacy_level="local_only_research",
            parameter_schema=[
                ParameterSchema(
                    key="language",
                    label="目标语言",
                    type="select",
                    default="zh",
                    options=[
                        {"label": "中文 zh", "value": "zh"},
                        {"label": "英文 en", "value": "en"},
                        {"label": "日文 ja", "value": "ja"},
                        {"label": "韩文 ko", "value": "ko"},
                    ],
                    description="目标文本语言。中文口播用 zh，英文用 en。",
                ),
                ParameterSchema(
                    key="speaker_id",
                    label="预置声音",
                    type="select",
                    default="Vivian",
                    options=QWEN3_TTS_SPEAKERS,
                    capability="preset_voice",
                    description="未选择参考音色时使用的 Qwen3 预置声音。",
                ),
                ParameterSchema(
                    key="style_instruction",
                    label="情绪/风格指令",
                    type="textarea",
                    default="",
                    capability="natural_language_control",
                    description="官方 CustomVoice instruct 参数，控制预置声音的情绪、风格和语速倾向；支持中文或英文描述。例如：语气温柔，语速稍慢，像在讲解课程。留空时后端按官方示例使用 Normal tone。",
                ),
                ParameterSchema(
                    key="voice_design_prompt",
                    label="声音描述",
                    type="textarea",
                    default="",
                    capability="voice_design",
                    description="官方 VoiceDesign instruct 参数，用来描述声音本身；支持中文或英文描述。例如：年轻中文女声，声线温暖，吐字清晰。填写后使用 VoiceDesign 模型；为空时使用预置音色或参考音色。",
                ),
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05, description="官方 speed 参数。README 推荐 Normal 1.0、Fast 1.3、Slow 0.8。"),
                ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.7, min=0.1, max=2.0, step=0.05, level="advanced", description="官方 temperature 参数，较低更稳定，较高更有变化。"),
                ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.9, min=0.01, max=1.0, step=0.01, level="advanced", description="mlx_audio 官方 top_p 参数，控制核采样范围。"),
                ParameterSchema(key="top_k", label="候选数量 Top-K", type="number", default=50, min=1, max=200, step=1, level="advanced", description="mlx_audio 官方 top_k 参数，控制候选 token 数量。"),
                ParameterSchema(key="repetition_penalty", label="重复惩罚", type="number", default=1.1, min=1.0, max=3.0, step=0.05, level="advanced", description="mlx_audio 官方 repetition_penalty 参数，抑制重复发音或重复片段。"),
                ParameterSchema(key="max_tokens", label="最大 Token", type="number", default=1200, min=100, max=4096, step=50, level="advanced", description="官方 max_tokens 参数，限制单次生成 token 上限。"),
                ParameterSchema(key="cfg_scale", label="CFG Scale", type="number", default=1.5, min=0.0, max=5.0, step=0.1, level="advanced", description="mlx_audio CLI 默认 1.5；官方帮助提示 1.0-1.5 通常更稳定。"),
                ParameterSchema(key="ddpm_steps", label="DDPM Steps", type="number", default=None, min=1, max=100, step=1, level="advanced", description="官方 ddpm_steps 参数；留空则使用模型默认。官方帮助建议可尝试 30-50，数值越高越慢。"),
            ],
        ),
        state=EngineState(engine_id="qwen3-tts-mlx-0.6b", status=EngineStatus.stopped),
    ),
    "f5-tts": EngineDetail(
        manifest=EngineManifest(
            engine_id="f5-tts",
            display_name="F5-TTS",
            provider="SWivid",
            version="v1 Base",
            description="基于 Flow Matching + DiT 的非自回归零样本 TTS，5 秒参考音频即可克隆",
            supported_languages=["zh", "en"],
            capabilities=["local_inference", "voice_clone", "multilingual"],
            sample_rate=24000,
            default_use_case="已授权参考音频的音色迁移和中英文生成研究",
            privacy_level="local_only_noncommercial_model",
            parameter_schema=[
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05, description="控制说话速度"),
                ParameterSchema(key="nfe_step", label="采样步数 NFE", type="slider", default=32, min=4, max=64, step=1, level="advanced", description="流匹配步数，越高音质越好"),
                ParameterSchema(key="cfg_strength", label="引导强度 CFG", type="slider", default=2.0, min=0.1, max=5.0, step=0.1, level="advanced", description="无分类器引导强度"),
                ParameterSchema(key="target_rms", label="响度目标 RMS", type="slider", default=0.1, min=0.01, max=0.5, step=0.01, level="advanced", description="目标音量(RMS)，控制输出响度"),
                ParameterSchema(key="cross_fade_duration", label="分段交叉淡化", type="slider", default=0.15, min=0, max=1, step=0.05, level="advanced", description="交叉淡入淡出时长(秒)"),
                ParameterSchema(key="sway_sampling_coef", label="采样摆动 Sway", type="slider", default=-1.0, min=-1.0, max=1.0, step=0.1, level="developer", description="F5-TTS 采样时间步修正系数。默认 -1，通常无需调整。"),
                ParameterSchema(key="fix_duration", label="固定总时长 s", type="number", default=0.0, min=0.0, max=600.0, step=0.1, level="developer", description="可选。0 表示自动估算；填写秒数会尝试固定参考音频与生成音频总时长。"),
                ParameterSchema(key="remove_silence", label="移除静音", type="toggle", default=False, level="advanced", description="是否自动去除静音段"),
            ],
        ),
        state=EngineState(engine_id="f5-tts", status=EngineStatus.stopped),
    ),
    "cosyvoice-sft": EngineDetail(
        manifest=EngineManifest(
            engine_id="cosyvoice-sft",
            display_name="CosyVoice SFT",
            provider="FunAudioLLM",
            version="300M-SFT",
            description="阿里巴巴 FunAudioLLM 多语种 TTS，官方 SFT 预置音色，支持中粤日韩英",
            supported_languages=["zh", "yue", "ja", "ko", "en"],
            capabilities=["local_inference", "preset_voice", "multilingual"],
            sample_rate=22050,
            default_use_case="高质量官方预置音色口播和多语种试听",
            parameter_schema=[
                ParameterSchema(
                    key="speaker_id",
                    label="预置音色",
                    type="select",
                    default="中文女",
                    options=COSYVOICE_SPEAKERS,
                    required=True,
                    capability="preset_voice",
                    description="选择预设说话人",
                ),
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05, description="控制说话速度"),
            ],
        ),
        state=EngineState(engine_id="cosyvoice-sft", status=EngineStatus.stopped),
    ),
    "cosyvoice-zero-shot": EngineDetail(
        manifest=EngineManifest(
            engine_id="cosyvoice-zero-shot",
            display_name="CosyVoice Zero-Shot",
            provider="FunAudioLLM",
            version="300M-SFT zero-shot path",
            description="CosyVoice 零样本声音克隆，提供参考音频即可复刻任意音色，支持跨语言",
            supported_languages=["zh", "en", "yue", "ja", "ko"],
            capabilities=["local_inference", "voice_clone", "zero_shot", "multilingual"],
            sample_rate=22050,
            default_use_case="使用已授权参考音频做 CosyVoice 本地音色复刻",
            parameter_schema=[
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05, description="控制说话速度"),
            ],
        ),
        state=EngineState(engine_id="cosyvoice-zero-shot", status=EngineStatus.stopped),
    ),
    "mimo-v2.5-tts-preset": EngineDetail(
        manifest=EngineManifest(
            engine_id="mimo-v2.5-tts-preset",
            display_name="MiMo V2.5 TTS Preset",
            engine_type="cloud",
            provider="Xiaomi MiMo",
            version="2.5",
            description="小米云端精品音色合成，支持自然语言风格控制和唱歌标签",
            supported_languages=["zh", "en"],
            capabilities=["cloud_api", "preset_voice", "natural_language_control", "audio_tags", "singing"],
            default_use_case="云端中文/英文口播、唱歌和官方预置音色快速合成",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(
                    key="mimo_voice",
                    label="MiMo 官方音色",
                    type="select",
                    default="mimo_default",
                    options=[{"label": item["label"], "value": item["voice_id"]} for item in mimo_client.MIMO_PRESET_VOICES],
                    required=True,
                    capability="preset_voice",
                    description="选择预设音色",
                ),
                ParameterSchema(key="style_instruction", label="风格指令", type="textarea", default="", capability="natural_language_control", description="风格指令，描述想要的说话风格"),
                ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.6, min=0, max=1.5, step=0.05, level="advanced", description="控制语音随机性，越低越稳定，越高越有变化"),
                ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.95, min=0.01, max=1.0, step=0.01, level="advanced", description="核采样概率，控制词汇选择范围"),
            ],
        ),
        state=EngineState(engine_id="mimo-v2.5-tts-preset", status=EngineStatus.stopped),
    ),
    "mimo-v2.5-tts-voicedesign": EngineDetail(
        manifest=EngineManifest(
            engine_id="mimo-v2.5-tts-voicedesign",
            display_name="MiMo V2.5 TTS VoiceDesign",
            engine_type="cloud",
            provider="Xiaomi MiMo",
            version="2.5",
            description="小米云端音色设计，用一段文字描述即可生成全新声音",
            supported_languages=["zh", "en"],
            capabilities=["cloud_api", "voice_design", "natural_language_control"],
            default_use_case="探索角色音色、一次性生成定制声音样本",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(key="voice_design_prompt", label="音色描述", type="textarea", default="", required=True, capability="voice_design", description="声音设计提示，描述声音特征"),
                ParameterSchema(
                    key="optimize_text_preview",
                    label="润色播报文本",
                    type="toggle",
                    default=False,
                    level="advanced",
                    capability="voice_design",
                    description="是否自动润色播报文本内容",
                ),
                ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.6, min=0, max=1.5, step=0.05, level="advanced", description="控制语音随机性，越低越稳定，越高越有变化"),
                ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.95, min=0.01, max=1.0, step=0.01, level="advanced", description="核采样概率，控制词汇选择范围"),
            ],
        ),
        state=EngineState(engine_id="mimo-v2.5-tts-voicedesign", status=EngineStatus.stopped),
    ),
    "mimo-v2.5-tts-voiceclone": EngineDetail(
        manifest=EngineManifest(
            engine_id="mimo-v2.5-tts-voiceclone",
            display_name="MiMo V2.5 TTS VoiceClone",
            engine_type="cloud",
            provider="Xiaomi MiMo",
            version="2.5",
            description="小米云端音色复刻，上传参考音频即可零样本克隆任意说话人",
            supported_languages=["zh", "en"],
            capabilities=["cloud_api", "voice_clone", "natural_language_control", "audio_tags"],
            default_use_case="使用已授权的本地参考音色做云端复刻合成",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(key="style_instruction", label="风格指令", type="textarea", default="", capability="natural_language_control", description="风格指令，描述想要的说话风格"),
                ParameterSchema(key="temperature", label="随机性 Temperature", type="slider", default=0.6, min=0, max=1.5, step=0.05, level="advanced", description="控制语音随机性，越低越稳定，越高越有变化"),
                ParameterSchema(key="top_p", label="采样范围 Top-P", type="slider", default=0.95, min=0.01, max=1.0, step=0.01, level="advanced", description="核采样概率，控制词汇选择范围"),
            ],
        ),
        state=EngineState(engine_id="mimo-v2.5-tts-voiceclone", status=EngineStatus.stopped),
    ),
    "mimo-v2.5-asr": EngineDetail(
        manifest=EngineManifest(
            engine_id="mimo-v2.5-asr",
            display_name="MiMo V2.5 ASR",
            engine_type="cloud",
            provider="Xiaomi MiMo",
            version="2.5",
            description="小米云端语音识别，支持中英文音频转写",
            supported_languages=["auto", "zh", "en"],
            capabilities=["cloud_api", "speech_recognition", "transcription"],
            default_use_case="会议、录音和素材音频转文字",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(key="language", label="识别语言", type="select", default="auto", options=[{"label": x, "value": x} for x in ["auto", "zh", "en"]], description="选择识别语言，auto 自动检测"),
            ],
        ),
        state=EngineState(engine_id="mimo-v2.5-asr", status=EngineStatus.stopped),
    ),
    "doubao-tts-preset": EngineDetail(
        manifest=EngineManifest(
            engine_id="doubao-tts-preset",
            display_name="豆包语音 TTS 2.0",
            engine_type="cloud",
            provider="Volcengine / Doubao",
            version="seed-tts-2.0",
            description="火山引擎豆包语音合成模型 2.0，使用官方预置音色进行短文本云端合成",
            supported_languages=["zh"],
            capabilities=["cloud_api", "preset_voice", "natural_language_control"],
            sample_rate=24000,
            default_use_case="中文短句口播、视频旁白和官方预置音色试听",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(
                    key="speaker_id",
                    label="豆包官方音色",
                    type="select",
                    default="zh_female_vv_uranus_bigtts",
                    options=[{"label": item["label"], "value": item["voice_id"]} for item in doubao_client.DOUBAO_TTS_PRESET_SPEAKERS],
                    required=True,
                    capability="preset_voice",
                    description="选择与 seed-tts-2.0 资源匹配的官方音色",
                ),
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05, description="映射到豆包 speech_rate，1.0 为正常语速"),
                ParameterSchema(
                    key="style_instruction",
                    label="语音指令",
                    type="textarea",
                    default="",
                    capability="natural_language_control",
                    description=(
                        "可选，支持中文或英文。会作为豆包 TTS 2.0 的 context_texts 发送，用来影响语气、情绪、节奏和停顿。\n"
                        "示例：语速慢一点，语气更惊讶，句尾带一点感叹。\n"
                        "官方还支持在正文句前写自由描述式语音标签，例如：[怒目圆睁，冲着你大声怒吼]。"
                        "这不是 OmniVoice 那种固定标签按钮清单；未实测确认前不把它做成快捷按钮。"
                    ),
                ),
            ],
        ),
        state=EngineState(engine_id="doubao-tts-preset", status=EngineStatus.stopped),
    ),
    "doubao-tts-voiceclone": EngineDetail(
        manifest=EngineManifest(
            engine_id="doubao-tts-voiceclone",
            display_name="豆包语音 TTS 2.0 · 声音复刻",
            engine_type="cloud",
            provider="Volcengine / Doubao",
            version="seed-icl-2.0",
            description="火山引擎豆包声音复刻 2.0，使用已训练成功的云端 speaker_id 合成新文本",
            supported_languages=["zh"],
            capabilities=["cloud_api", "voice_clone", "natural_language_control"],
            sample_rate=24000,
            default_use_case="使用音色库中已训练成功的豆包云端复刻音色做中文口播",
            privacy_level="cloud_required",
            parameter_schema=[
                ParameterSchema(key="speed", label="语速", type="slider", default=1.0, min=0.5, max=2.0, step=0.05, description="映射到豆包 speech_rate，1.0 为正常语速"),
                ParameterSchema(
                    key="style_instruction",
                    label="语音指令",
                    type="textarea",
                    default="",
                    capability="natural_language_control",
                    description=(
                        "可选，支持中文或英文。会作为豆包 TTS 2.0 的 context_texts 发送，用来影响语气、情绪、节奏和停顿。\n"
                        "示例：语速慢一点，语气更惊讶，句尾带一点感叹。\n"
                        "使用声音库里已训练成功的豆包云端 speaker_id；不会在合成时再次上传参考音频。"
                    ),
                ),
            ],
        ),
        state=EngineState(engine_id="doubao-tts-voiceclone", status=EngineStatus.stopped),
    ),
    "qwen3-asr-mlx": EngineDetail(
        manifest=EngineManifest(
            engine_id="qwen3-asr-mlx",
            display_name="Qwen3-ASR MLX",
            provider="Qwen + MLX Community",
            version="1.7B 8-bit",
            description="本地 Apple Silicon 语音识别，Qwen3-ASR 1.7B MLX 量化推理，超越 Whisper-large-v3",
            supported_languages=["auto", "zh", "en"],
            capabilities=["local_inference", "speech_recognition", "transcription", "language_identification"],
            default_use_case="离线音频转写与云端 ASR 备选",
            parameter_schema=[
                ParameterSchema(key="language", label="识别语言", type="select", default="auto", options=[{"label": x, "value": x} for x in ["auto", "zh", "en"]], description="选择识别语言，auto 自动检测")
            ],
        ),
        state=EngineState(engine_id="qwen3-asr-mlx", status=EngineStatus.stopped),
    ),
    "faster-whisper-turbo": EngineDetail(
        manifest=EngineManifest(
            engine_id="faster-whisper-turbo",
            display_name="Faster Whisper Turbo",
            provider="SYSTRAN faster-whisper + OpenAI Whisper",
            version="large-v3-turbo / CTranslate2",
            description="本地 faster-whisper 英文优先转写，面向视频本土化初稿字幕；原始模型 openai/whisper-large-v3-turbo，运行时使用 CTranslate2 转换模型。",
            supported_languages=["auto", "en"],
            capabilities=["local_inference", "speech_recognition", "transcription", "language_identification", "vad"],
            default_use_case="英文视频快速转写、时间轴字幕初稿和人工校对入口",
            privacy_level="local_only",
            parameter_schema=[
                ParameterSchema(key="language", label="识别语言", type="select", default="en", options=[{"label": x, "value": x} for x in ["en", "auto"]], description="视频本土化默认英文识别；auto 用于少量混语素材。")
            ],
        ),
        state=EngineState(engine_id="faster-whisper-turbo", status=EngineStatus.stopped),
    ),
}
