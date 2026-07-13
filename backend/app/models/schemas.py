"""Legacy stable schema path kept for the Voice Studio 1.x line.

New backend code should prefer ``app.schemas.voice_studio``. This module remains
the implementation source during the compatibility window so old imports keep
class identity unchanged.
"""

from __future__ import annotations

import uuid
import os
import urllib.parse
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def default_data_dir() -> str:
    return os.environ.get("VOICE_STUDIO_DATA_DIR", "~/VoiceStudio")


def default_data_subdir(name: str) -> str:
    return os.environ.get(f"VOICE_STUDIO_{name.upper()}_DIR", f"{default_data_dir()}/{name}")


class EngineType(str, Enum):
    local = "local"
    cloud = "cloud"


class EngineStatus(str, Enum):
    not_installed = "not_installed"
    stopped = "stopped"
    loading = "loading"
    loaded = "loaded"
    running = "running"
    error = "error"


class TaskStatus(str, Enum):
    pending = "pending"
    queued = "queued"
    running = "running"
    postprocessing = "postprocessing"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"
    retrying = "retrying"


class TimestampMode(str, Enum):
    none = "none"
    native = "native"
    supplemented = "supplemented"


class VoiceType(str, Enum):
    real_person = "real_person"
    virtual_character = "virtual_character"
    host = "host"
    singer = "singer"
    narrator = "narrator"
    emotion_reference = "emotion_reference"
    test_sample = "test_sample"


class LicenseStatus(str, Enum):
    self_voice = "self_voice"
    company_authorized = "company_authorized"
    authorized = "authorized"
    localized = "本土化"
    localized_dub_source = "localized_dub_source"
    test_only = "test_only"
    unknown = "unknown"
    commercial_forbidden = "commercial_forbidden"


class EmotionMode(str, Enum):
    follow_reference = "follow_reference"
    emotion_vector = "emotion_vector"
    emotion_text = "emotion_text"


class VoiceSource(str, Enum):
    voice_library = "voice_library"
    reference_audio = "reference_audio"
    model_preset = "model_preset"
    voice_design = "voice_design"


EMOTIONS = ["happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"]


class ParameterSchema(BaseModel):
    key: str
    label: str
    description: str | None = None
    type: Literal["text", "textarea", "number", "slider", "select", "toggle", "file"]
    level: Literal["basic", "advanced", "developer"] = "basic"
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[dict[str, Any]] = Field(default_factory=list)
    required: bool = False
    capability: str | None = None


class EngineManifest(BaseModel):
    engine_id: str
    display_name: str
    engine_type: EngineType = EngineType.local
    provider: str = ""
    version: str = ""
    description: str = ""
    supported_languages: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    sample_rate: int | None = None
    max_tokens: int | None = None
    privacy_level: str = "local_only"
    default_use_case: str = ""
    parameter_schema: list[ParameterSchema] = Field(default_factory=list)
    input_modes: list[Literal["text", "audio", "image"]] = Field(default_factory=list)
    max_reference_audio: int = 0
    max_reference_image: int = 0
    mutually_exclusive_inputs: list[list[str]] = Field(default_factory=list)
    prompt_reference_syntax: str | None = None
    supported_output_formats: list[str] = Field(default_factory=list)
    supported_sample_rates: list[int] = Field(default_factory=list)
    max_prompt_chars: int | None = None
    max_output_seconds: int | None = None


class EngineState(BaseModel):
    engine_id: str
    status: EngineStatus = EngineStatus.stopped
    model_path: str | None = None
    error_message: str | None = None
    loaded_at: str | None = None


class EngineSpeaker(BaseModel):
    speaker_id: str
    name: str
    gender: str = ""
    description: str = ""
    label: str
    age: str = ""
    languages: list[str] = Field(default_factory=list)
    emotions: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    normal_labels: list[str] = Field(default_factory=list)
    special_labels: list[str] = Field(default_factory=list)
    trial_url: str | None = None
    short_trial_url: str | None = None
    preview_text: str = ""
    avatar_url: str | None = None
    resource_id: str | None = None
    catalog_source: str = "bundled"
    catalog_updated_at: str | None = None
    catalog_stale: bool = True
    authorization_status: str = "unknown"
    deprecated: bool = False


class EngineDetail(BaseModel):
    manifest: EngineManifest
    state: EngineState


class AppSettings(BaseModel):
    data_dir: str = Field(default_factory=default_data_dir)
    model_dir: str = Field(default_factory=lambda: default_data_subdir("models"))
    voice_dir: str = Field(default_factory=lambda: default_data_subdir("voices"))
    output_dir: str = Field(default_factory=lambda: default_data_subdir("outputs"))
    export_dir: str = Field(default_factory=lambda: default_data_subdir("exports"))
    project_dir: str = Field(default_factory=lambda: default_data_subdir("projects"))
    cache_dir: str = Field(default_factory=lambda: default_data_subdir("cache"))
    log_dir: str = Field(default_factory=lambda: default_data_subdir("logs"))
    default_engine_id: str = "indextts-v2"
    default_voice_id: str | None = None
    default_language: str = "zh"
    default_output_format: Literal["wav", "mp3", "flac"] = "wav"
    device: Literal["auto", "mps", "cpu"] = "auto"
    cloud_enabled: bool = False
    mimo_base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    mimo_api_key_configured: bool = False
    mimo_default_voice: str = "mimo_default"
    mimo_voiceclone_confirm_upload: bool = True
    doubao_base_url: str = "https://openspeech.bytedance.com"
    doubao_api_key_configured: bool = False
    volcengine_access_key_id_configured: bool = False
    volcengine_secret_access_key_configured: bool = False
    doubao_default_tts_resource_id: str = "seed-tts-2.0"
    doubao_default_icl_resource_id: str = "seed-icl-2.0"
    doubao_upload_confirm: bool = True
    default_emotion: str = "calm"
    default_emo_alpha: float = 0.6
    theme: Literal["system", "dark", "light"] = "system"

    @field_validator("doubao_base_url")
    @classmethod
    def validate_doubao_base_url(cls, value: str) -> str:
        parsed = urllib.parse.urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "openspeech.bytedance.com"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("doubao_base_url 必须是火山引擎官方 HTTPS 地址")
        return value.rstrip("/")


class MimoSecretUpdate(BaseModel):
    api_key: str | None = None
    clear: bool = False


class DoubaoSecretUpdate(BaseModel):
    api_key: str | None = None
    clear: bool = False


class VolcengineDirectorySecretUpdate(BaseModel):
    access_key_id: str | None = None
    secret_access_key: str | None = None
    clear_access_key_id: bool = False
    clear_secret_access_key: bool = False


class DoubaoVoiceCloneTrainRequest(BaseModel):
    custom_speaker_id: str | None = None
    speaker_id: str | None = None
    demo_text: str | None = None
    language: str = "zh"
    enable_audio_denoise: bool = True
    disable_volume_normalization: bool = False
    confirm_upload: bool = False


class VoiceAssetCreate(BaseModel):
    name: str
    voice_type: VoiceType = VoiceType.test_sample
    description: str = ""
    default_language: str = "zh"
    tags: list[str] = Field(default_factory=list)
    reference_text: str = ""
    recommended_engine_id: str | None = None
    reference_audio_ids: list[str] = Field(default_factory=list)
    license_status: LicenseStatus = LicenseStatus.unknown
    external_provider: str | None = None
    external_voice_id: str | None = None
    external_status: str | None = None
    external_metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceAssetUpdate(BaseModel):
    name: str | None = None
    voice_type: VoiceType | None = None
    description: str | None = None
    default_language: str | None = None
    tags: list[str] | None = None
    reference_text: str | None = None
    recommended_engine_id: str | None = None
    reference_audio_ids: list[str] | None = None
    license_status: LicenseStatus | None = None
    quality_status: str | None = None
    quality_notes: str | None = None
    favorite: bool | None = None
    emotion_tags: list[str] | None = None
    external_provider: str | None = None
    external_voice_id: str | None = None
    external_status: str | None = None
    external_metadata: dict[str, Any] | None = None


class VoiceEngineBinding(BaseModel):
    engine_id: str
    mode: Literal["reference_audio", "preset_voice", "voice_design", "voice_clone"]
    available: bool
    reason: str = ""
    external_voice_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class VoiceAsset(VoiceAssetCreate):
    voice_id: str = Field(default_factory=new_id)
    quality_status: str = "unchecked"
    quality_notes: str = ""
    favorite: bool = False
    emotion_tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    last_used_at: str | None = None
    engine_bindings: list[VoiceEngineBinding] = Field(default_factory=list)


class DoubaoVoiceCloneResponse(BaseModel):
    voice: VoiceAsset
    summary: dict[str, Any] = Field(default_factory=dict)


class VoiceFile(BaseModel):
    file_id: str = Field(default_factory=new_id)
    original_name: str
    path: str
    mime_type: str = "audio/wav"
    duration_ms: int | None = None
    sample_rate: int | None = None
    size_bytes: int = 0
    created_at: str = Field(default_factory=now_iso)


class TranscriptionSegment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    language: str | None = None


class TranscriptionRecord(BaseModel):
    transcription_id: str = Field(default_factory=new_id)
    engine_id: str = "mimo-v2.5-asr"
    filename: str
    language: Literal["auto", "zh", "en"] = "auto"
    text: str
    segments: list[TranscriptionSegment] = Field(default_factory=list)
    has_source_audio: bool = False
    timestamp_mode: TimestampMode = TimestampMode.none
    timestamp_source_engine_id: str | None = None
    duration_ms: int | None = None
    size_bytes: int = 0
    usage_seconds: int | None = None
    provider_response_id: str | None = None
    created_at: str = Field(default_factory=now_iso)


class VoiceClipTranscribeRequest(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    language: Literal["auto", "zh", "en"] = "auto"
    engine_id: str = "qwen3-asr-mlx"


class VoiceClipTranscribeResponse(BaseModel):
    file_id: str
    filename: str
    path: str
    quality: dict[str, Any] = Field(default_factory=dict)
    voice_file: VoiceFile
    transcription: TranscriptionRecord


class TranscriptionTask(BaseModel):
    task_id: str = Field(default_factory=new_id)
    engine_id: str = "mimo-v2.5-asr"
    filename: str
    language: Literal["auto", "zh", "en"] = "auto"
    status: TaskStatus = TaskStatus.queued
    text: str | None = None
    segments: list[TranscriptionSegment] = Field(default_factory=list)
    has_source_audio: bool = False
    timestamp_mode: TimestampMode = TimestampMode.none
    timestamp_source_engine_id: str | None = None
    transcription_id: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    size_bytes: int = 0
    usage_seconds: int | None = None
    provider_response_id: str | None = None
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class TimestampSupplementRequest(BaseModel):
    strategy: Literal["auto", "forced_aligner", "qwen3-asr-mlx"] = "auto"
    overwrite: bool = False


class TranscriptionBatchDeleteRequest(BaseModel):
    transcription_ids: list[str] = Field(default_factory=list)


class TranscriptionBatchSupplementRequest(TimestampSupplementRequest):
    transcription_ids: list[str] = Field(default_factory=list)


class EngineInputAsset(BaseModel):
    """Managed input reference used by model-specific request adapters.

    Raw local paths and Base64 are intentionally not part of this public
    envelope. A later resolver must turn stable file IDs into validated paths.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    asset_id: str
    type: Literal["audio", "image", "speaker"]
    source: Literal["voice_library", "upload", "cloud_speaker", "preset"]
    file_id: str | None = None
    voice_id: str | None = None
    speaker_id: str | None = None
    display_name: str | None = None
    ref_text: str | None = None
    source_file_id: str | None = None
    clip_file_id: str | None = None
    trim_start_ms: int | None = Field(default=None, ge=0)
    trim_end_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    license_status: LicenseStatus | None = None

    @model_validator(mode="after")
    def validate_managed_identifier(self):
        if self.type == "speaker" and not self.speaker_id:
            raise PydanticCustomError("speaker_id_required", "speaker 类型素材必须提供 speaker_id")
        if self.type in {"audio", "image"} and not any((self.file_id, self.source_file_id, self.clip_file_id)):
            raise PydanticCustomError("file_id_required", "音频和图片素材必须提供受管理的 file_id")
        if self.trim_start_ms is not None and self.trim_end_ms is not None and self.trim_end_ms < self.trim_start_ms:
            raise PydanticCustomError("invalid_trim_range", "trim_end_ms must be greater than or equal to trim_start_ms")
        return self


class GenerateRequest(BaseModel):
    text: str
    engine_id: str = "indextts-v2"
    source: str | None = None
    project_id: str | None = None
    segment_id: str | None = None
    input_mode: Literal["text", "audio", "image"] | None = None
    input_assets: list[EngineInputAsset] = Field(default_factory=list)
    engine_parameters: dict[str, Any] = Field(default_factory=dict)
    voice_id: str | None = None
    reference_audio_path: str | None = None
    voice_source: VoiceSource | None = None
    reference_audio_license_status: LicenseStatus | None = None
    reference_audio_tags: list[str] = Field(default_factory=list)
    ref_text: str | None = None
    custom_reference_source_audio_path: str | None = None
    custom_reference_source_duration_ms: int | None = None
    custom_reference_trim_start_ms: int | None = None
    custom_reference_trim_end_ms: int | None = None
    language: str = "zh"
    emotion_mode: EmotionMode = EmotionMode.follow_reference
    emotion: str | None = None
    emotion_values: dict[str, float] | None = None
    emotion_text: str | None = None
    style_instruction: str | None = None
    voice_design_prompt: str | None = None
    optimize_text_preview: bool = False
    mimo_voice: str | None = None
    idempotency_marker: str | None = None
    speaker_id: str | None = None
    prompt: str | None = None
    nfe_step: int = Field(default=32, ge=4, le=64)
    cfg_strength: float = Field(default=2.0, ge=0.1, le=5.0)
    target_rms: float = Field(default=0.1, ge=0.01, le=1.0)
    cross_fade_duration: float = Field(default=0.15, ge=0.0, le=1.0)
    sway_sampling_coef: float = Field(default=-1.0, ge=-1.0, le=1.0)
    fix_duration: float = Field(default=0.0, ge=0.0, le=600.0)
    remove_silence: bool = False
    emo_alpha: float = Field(default=0.6, ge=0, le=1)
    speed: float = Field(default=1.0, ge=0.5, le=3.0)
    pitch_rate: int | None = Field(default=None, ge=-12, le=12)
    sample_rate: Literal[8000, 16000, 22050, 24000, 32000, 44100, 48000] | None = None
    bit_rate: int | None = Field(default=None, ge=64000, le=160000)
    loudness_rate: int | None = Field(default=None, ge=-50, le=100)
    enable_subtitle: bool = False
    silence_duration: int = Field(default=0, ge=0, le=30000)
    aigc_watermark: bool = False
    temperature: float = Field(default=0.8, ge=0.1, le=2.0)
    top_p: float = Field(default=0.8, ge=0.0, le=1.0)
    top_k: int = Field(default=30, ge=1, le=100)
    repetition_penalty: float = Field(default=10.0, ge=1.0, le=20.0)
    seed: int | None = None
    max_mel_tokens: int = Field(default=800, ge=100, le=2500)
    max_text_tokens_per_segment: int = Field(default=120, ge=10, le=500)
    interval_silence: int = Field(default=200, ge=0, le=2000)
    segment_overlap_ms: int = Field(default=50, ge=0, le=500)
    diffusion_steps: int = Field(default=25, ge=1, le=100)
    cfg_rate: float = Field(default=0.7, ge=0.0, le=1.0)
    guidance_scale: float = Field(default=2.0, ge=0.0, le=10.0)
    duration: float = Field(default=0.0, ge=0.0, le=600.0)
    audio_chunk_duration: float = Field(default=15.0, ge=1.0, le=120.0)
    audio_chunk_threshold: float = Field(default=30.0, ge=1.0, le=600.0)
    max_tokens: int = Field(default=1200, ge=100, le=4096)
    cfg_scale: float | None = Field(default=None, ge=0.0, le=20.0)
    ddpm_steps: int | None = Field(default=None, ge=1, le=200)
    output_format: Literal["wav", "mp3", "flac"] = "wav"


class GenerateResponse(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.queued


class GeneratePlanRequest(BaseModel):
    text: str
    engine_id: str = "indextts-v2"
    planner_mode: Literal["auto", "rules", "llm"] = "auto"
    target_format: Literal["wav", "mp3", "flac"] = "mp3"


class PlannedTextSegment(BaseModel):
    index: int
    text: str
    char_count: int
    segment_reason: str = "sentence_boundary"


class GeneratePlanResponse(BaseModel):
    planner: Literal["rules", "llm"] = "rules"
    llm_available: bool = False
    mode: Literal["direct", "longform_recommended", "longform_strongly_recommended"] = "direct"
    recommended_action: Literal["direct_generate", "direct_generate_with_verification", "split_generate", "split_verify_merge"] = "direct_generate"
    requires_user_confirmation: bool = False
    text_length: int = 0
    threshold: int = 0
    hard_threshold: int = 0
    warnings: list[str] = Field(default_factory=list)
    privacy_notice: str = ""
    planner_reason: str = ""
    segments: list[PlannedTextSegment] = Field(default_factory=list)


class TTSVerificationRequest(BaseModel):
    result_id: str | None = None
    expected_text: str | None = None
    transcript_text: str | None = None
    asr_engine_id: str = "qwen3-asr-mlx"
    language: Literal["auto", "zh", "en"] = "auto"


class TTSVerificationSegment(BaseModel):
    index: int
    expected_text: str
    normalized_expected: str
    coverage: float
    status: Literal["passed", "warning", "failed"]


class TTSVerificationResponse(BaseModel):
    status: Literal["passed", "warning", "failed", "skipped"]
    coverage: float
    similarity: float
    expected_text: str
    transcript_text: str
    normalized_expected: str
    normalized_transcript: str
    missing_segments: list[TTSVerificationSegment] = Field(default_factory=list)
    segment_results: list[TTSVerificationSegment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    result_id: str | None = None
    transcription_id: str | None = None
    asr_engine_id: str | None = None


class LongformGenerateRequest(BaseModel):
    generate_request: GenerateRequest
    segments: list[PlannedTextSegment] | None = None
    verify_enabled: bool = True
    merge_enabled: bool = True
    max_retries: int = Field(default=2, ge=0, le=5)
    stop_merge_on_verification_failed: bool = True
    asr_engine_id: str = "qwen3-asr-mlx"
    silence_ms: int = Field(default=300, ge=0, le=5000)
    normalize: bool = False

    @model_validator(mode="after")
    def reject_single_only_engines(self):
        if self.generate_request.engine_id == "doubao-seed-audio-1.0":
            raise PydanticCustomError("single_generation_only", "Seed Audio 1.0 暂只支持单次生成")
        return self


class LongformSegmentTask(BaseModel):
    index: int
    text: str
    char_count: int
    status: TaskStatus = TaskStatus.pending
    attempts: int = 0
    task_id: str | None = None
    result_id: str | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    verification: TTSVerificationResponse | None = None


class LongformTask(BaseModel):
    longform_task_id: str = Field(default_factory=new_id)
    engine_id: str
    voice_id: str | None = None
    input_text: str
    status: TaskStatus = TaskStatus.pending
    progress: float = 0.0
    error_message: str | None = None
    segments: list[LongformSegmentTask] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    export_id: str | None = None
    export_path: str | None = None
    verify_enabled: bool = True
    merge_enabled: bool = True
    max_retries: int = 2
    stop_merge_on_verification_failed: bool = True
    asr_engine_id: str = "qwen3-asr-mlx"
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class BatchSegmentInput(BaseModel):
    segment_id: str | None = None
    chapter: str | None = None
    step: int | None = None
    text: str
    audio: str | None = None
    engine_id: str | None = None
    voice_id: str | None = None
    reference_audio_path: str | None = None
    voice_source: VoiceSource | None = None
    reference_audio_license_status: LicenseStatus | None = None
    reference_audio_tags: list[str] = Field(default_factory=list)
    ref_text: str | None = None
    language: str | None = None
    emotion: str | None = None
    emotion_text: str | None = None
    style_instruction: str | None = None
    voice_design_prompt: str | None = None
    mimo_voice: str | None = None
    speed: float | None = Field(default=None, ge=0.5, le=3.0)
    parameters: dict[str, Any] = Field(default_factory=dict)


class BatchGenerateRequest(BaseModel):
    project_name: str = "批量语音项目"
    engine_id: str = "indextts-v2"
    voice_id: str | None = None
    reference_audio_path: str | None = None
    voice_source: VoiceSource | None = None
    reference_audio_license_status: LicenseStatus | None = None
    reference_audio_tags: list[str] = Field(default_factory=list)
    ref_text: str | None = None
    language: str = "zh"
    output_dir: str | None = None
    output_format: Literal["wav", "mp3", "flac"] = "mp3"
    partial_success: bool = False
    segments: list[BatchSegmentInput]
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_single_only_engines(self):
        if self.engine_id == "doubao-seed-audio-1.0" or any(
            segment.engine_id == "doubao-seed-audio-1.0" for segment in self.segments
        ):
            raise PydanticCustomError("single_generation_only", "Seed Audio 1.0 暂只支持单次生成")
        return self


class BatchSegmentResult(BaseModel):
    segment_id: str
    chapter: str | None = None
    step: int | None = None
    text: str
    audio: str | None = None
    output_path: str | None = None
    duration_ms: int | None = None
    status: TaskStatus = TaskStatus.pending
    error_message: str | None = None


class BatchTask(BaseModel):
    batch_task_id: str = Field(default_factory=new_id)
    project_name: str = "批量语音项目"
    engine_id: str = "indextts-v2"
    voice_id: str | None = None
    output_dir: str | None = None
    output_format: str = "mp3"
    status: TaskStatus = TaskStatus.pending
    progress: float = 0.0
    error_message: str | None = None
    segments: list[BatchSegmentResult] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class GenerationTask(BaseModel):
    task_id: str = Field(default_factory=new_id)
    task_type: Literal["single", "segment", "batch", "export"] = "single"
    engine_id: str
    voice_id: str | None = None
    project_id: str | None = None
    segment_id: str | None = None
    longform_task_id: str | None = None
    longform_segment_index: int | None = None
    longform_segment_count: int | None = None
    longform_export_id: str | None = None
    input_text: str
    status: TaskStatus = TaskStatus.pending
    progress: float = 0.0
    error_message: str | None = None
    result_audio_id: str | None = None
    result_id: str | None = None
    result_duration_ms: int | None = None
    generation_time_ms: int | None = None
    provider_request_id: str | None = None
    provider_log_id: str | None = None
    provider_state_uncertain: bool = False
    original_duration_ms: int | None = None
    subtitle: dict[str, Any] | None = None
    response_source: str | None = None
    verification: TTSVerificationResponse | None = None
    verification_error: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    logs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class HistoryItem(BaseModel):
    result_id: str = Field(default_factory=new_id)
    task_id: str
    engine_id: str
    voice_id: str | None = None
    voice_name: str | None = None
    project_id: str | None = None
    segment_id: str | None = None
    longform_task_id: str | None = None
    longform_segment_index: int | None = None
    longform_segment_count: int | None = None
    longform_export_id: str | None = None
    input_text: str
    output_audio_id: str | None = None
    output_path: str | None = None
    duration_ms: int | None = None
    generation_time_ms: int | None = None
    provider_request_id: str | None = None
    provider_log_id: str | None = None
    original_duration_ms: int | None = None
    subtitle: dict[str, Any] | None = None
    response_source: str | None = None
    verification: TTSVerificationResponse | None = None
    verification_error: str | None = None
    parameter_snapshot: dict[str, Any] = Field(default_factory=dict)
    favorite: bool = False
    created_at: str = Field(default_factory=now_iso)


class Role(BaseModel):
    role_id: str = Field(default_factory=new_id)
    name: str
    color: str = "#3B82F6"
    default_voice_id: str | None = None
    default_engine_id: str | None = None
    default_language: str = "zh"
    default_emotion: str | None = None
    default_speed: float = 1.0
    default_parameters: dict[str, Any] = Field(default_factory=dict)


class SegmentStatus(str, Enum):
    empty = "empty"
    ready = "ready"
    queued = "queued"
    generating = "generating"
    completed = "completed"
    failed = "failed"
    locked = "locked"


class ScriptSegment(BaseModel):
    segment_id: str = Field(default_factory=new_id)
    index: int
    text: str = ""
    source_start_ms: int | None = None
    source_end_ms: int | None = None
    role_id: str | None = None
    voice_id: str | None = None
    engine_id: str | None = None
    language: str = "zh"
    emotion: str | None = None
    speed: float | None = None
    status: SegmentStatus = SegmentStatus.empty
    result_audio_id: str | None = None
    result_id: str | None = None
    error_message: str | None = None
    locked: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    default_engine_id: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_engine_id: str | None = None


class ProjectTranscriptionImportRequest(BaseModel):
    transcription_ids: list[str] = Field(default_factory=list)
    mode: Literal["append", "replace"] = "append"
    role_id: str | None = None
    default_engine_id: str | None = None
    default_voice_id: str | None = None


class Project(BaseModel):
    project_id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    default_engine_id: str | None = "indextts-v2"
    parameters: dict[str, Any] = Field(default_factory=dict)
    roles: list[Role] = Field(default_factory=list)
    segments: list[ScriptSegment] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ProjectTranscriptionImportResponse(BaseModel):
    project: Project
    imported_count: int = 0
    skipped_count: int = 0


class VideoLocalizationExtensibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class VideoLocalizationSourceMedia(VideoLocalizationExtensibleModel):
    filename: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    video_path: str | None = None
    audio_path: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    frame_rate: float | None = Field(default=None, ge=0)
    imported_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VideoLocalizationStems(VideoLocalizationExtensibleModel):
    vocals_clean_path: str | None = None
    background_path: str | None = None
    original_audio_path: str | None = None
    separation_engine_id: str | None = None
    separation_status: Literal["pending", "running", "completed", "failed", "cancelled", "skipped"] = "pending"
    quality_flags: list[str] = Field(default_factory=list)


class VideoLocalizationTimeRange(VideoLocalizationExtensibleModel):
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    source: str | None = None

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        return self


class VideoLocalizationSpeaker(VideoLocalizationExtensibleModel):
    speaker_id: str
    display_name: str | None = None
    route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] = "manual_review"
    reference_clip_ids: list[str] = Field(default_factory=list)
    time_ranges: list[VideoLocalizationTimeRange] = Field(default_factory=list)
    review_status: Literal["needs_review", "ready", "blocked", "locked"] = "needs_review"
    notes: str | None = None


class VideoLocalizationSpeakerCreate(BaseModel):
    speaker_id: str | None = None
    display_name: str | None = None
    route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] = "manual_review"
    review_status: Literal["needs_review", "ready", "blocked", "locked"] = "needs_review"
    notes: str | None = None


class VideoLocalizationSpeakerUpdate(BaseModel):
    display_name: str | None = None
    route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] | None = None
    review_status: Literal["needs_review", "ready", "blocked", "locked"] | None = None
    notes: str | None = None


class VideoLocalizationReferenceClip(VideoLocalizationExtensibleModel):
    reference_clip_id: str
    speaker_id: str | None = None
    title: str | None = None
    person_name: str | None = None
    emotion: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    cover_frame_path: str | None = None
    source_stem: Literal["vocals_clean", "original_audio", "uploaded_reference", "generated_tts"] = "vocals_clean"
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    audio_path: str | None = None
    cleanliness: Literal["clean", "needs_review", "blocked", "mixed", "unknown"] = "unknown"
    asr_text: str | None = None
    asr_status: Literal["pending", "candidate", "verified", "failed", "skipped"] = "pending"
    license_status: str | None = None
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        return self


class VideoLocalizationReferenceClipCreate(BaseModel):
    cue_id: str | None = None
    speaker_id: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    asr_text: str | None = None
    title: str | None = None
    person_name: str | None = None
    emotion: str | None = None
    tags: list[str] | None = None
    description: str | None = None
    cover_frame_path: str | None = None

    @model_validator(mode="after")
    def validate_time_range(self):
        if (self.start_ms is None) != (self.end_ms is None):
            raise PydanticCustomError("incomplete_time_range", "start_ms and end_ms must be provided together")
        if self.start_ms is not None and self.end_ms is not None and self.end_ms <= self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than start_ms")
        return self


class VideoLocalizationReferenceClipUpdate(BaseModel):
    title: str | None = None
    person_name: str | None = None
    emotion: str | None = None
    tags: list[str] | None = None
    description: str | None = None
    cover_frame_path: str | None = None
    cleanliness: Literal["clean", "needs_review", "blocked", "mixed", "unknown"] | None = None
    asr_status: Literal["pending", "candidate", "verified", "failed", "skipped"] | None = None
    asr_text: str | None = None
    notes: str | None = None


class VideoLocalizationCue(VideoLocalizationExtensibleModel):
    cue_id: str
    speaker_id: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    audio_route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] = "manual_review"
    en_subtitle_text: str | None = None
    zh_localized_subtitle_text: str | None = None
    tts_recommended_text: str | None = None
    reference_clip_id: str | None = None
    tts_result_id: str | None = None
    tts_audio_path: str | None = None
    tts_batch_task_id: str | None = None
    tts_batch_status: str | None = None
    tts_batch_error: str | None = None
    tts_attempted_at: str | None = None
    source_duration_ms: int | None = Field(default=None, ge=0)
    generated_duration_ms: int | None = Field(default=None, ge=0)
    review_status: Literal["needs_review", "ready", "blocked", "locked"] = "needs_review"
    quality_flags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.start_ms is not None and self.end_ms is not None and self.end_ms < self.start_ms:
            raise PydanticCustomError("invalid_time_range", "end_ms must be greater than or equal to start_ms")
        return self


class VideoLocalizationCueUpdate(BaseModel):
    speaker_id: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    audio_route: Literal["clone_from_source", "preset_tts", "preserve_original_audio", "manual_review"] | None = None
    en_subtitle_text: str | None = None
    zh_localized_subtitle_text: str | None = None
    tts_recommended_text: str | None = None
    reference_clip_id: str | None = None
    review_status: Literal["needs_review", "ready", "blocked", "locked"] | None = None
    quality_flags: list[str] | None = None
    notes: str | None = None


class VideoLocalizationSubtitleImportRequest(BaseModel):
    srt_text: str = Field(min_length=1)
    update_timing: bool = True
    overwrite_tts: bool = False


class VideoLocalizationQualityIssue(VideoLocalizationExtensibleModel):
    code: str
    message: str
    severity: Literal["blocker", "warning", "info"] = "warning"
    cue_id: str | None = None
    speaker_id: str | None = None
    reference_clip_id: str | None = None


class VideoLocalizationQualityGate(VideoLocalizationExtensibleModel):
    status: Literal["unknown", "pass", "warning", "blocked"] = "unknown"
    pending_issues: int = Field(default=0, ge=0)
    blockers: list[VideoLocalizationQualityIssue] = Field(default_factory=list)
    warnings: list[VideoLocalizationQualityIssue] = Field(default_factory=list)
    checked_at: str | None = None


class VideoLocalizationExportState(VideoLocalizationExtensibleModel):
    production_json_path: str | None = None
    subtitle_paths: dict[str, str] = Field(default_factory=dict)
    timeline_audio_package_path: str | None = None
    timeline_audio_manifest_path: str | None = None
    localized_video_path: str | None = None
    last_exported_at: str | None = None


class VideoLocalizationOperation(VideoLocalizationExtensibleModel):
    operation_id: str = Field(default_factory=new_id)
    project_id: str
    kind: Literal["source_audio", "stems", "english_asr", "reference_clips"]
    status: Literal["queued", "running", "success", "failed", "cancelled"] = "queued"
    label: str | None = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested: bool = False
    result_summary: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None


class VideoLocalizationOperationRequest(BaseModel):
    kind: Literal["source_audio", "stems", "english_asr", "reference_clips"]
    parameters: dict[str, Any] = Field(default_factory=dict)


class VideoLocalizationDraft(BaseModel):
    project_type: Literal["video_localization"] = "video_localization"
    schema_version: str = "v1"
    status: Literal["draft", "reviewing", "ready_for_tts", "tts_running", "candidate", "blocked"] = "draft"
    source_media: VideoLocalizationSourceMedia = Field(default_factory=VideoLocalizationSourceMedia)
    stems: VideoLocalizationStems = Field(default_factory=VideoLocalizationStems)
    speakers: list[VideoLocalizationSpeaker] = Field(default_factory=list)
    reference_clips: list[VideoLocalizationReferenceClip] = Field(default_factory=list)
    cues: list[VideoLocalizationCue] = Field(default_factory=list)
    quality_gate: VideoLocalizationQualityGate = Field(default_factory=VideoLocalizationQualityGate)
    exports: VideoLocalizationExportState = Field(default_factory=VideoLocalizationExportState)
    operations: list[VideoLocalizationOperation] = Field(default_factory=list)
    ui_state: dict[str, Any] = Field(default_factory=dict)
    project_voice_samples: list[dict[str, Any]] = Field(default_factory=list)
    voice_recipes: list[dict[str, Any]] = Field(default_factory=list)
    generated_candidates: list[dict[str, Any]] = Field(default_factory=list)
    timeline_clips: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str | None = None


class VideoLocalizationExport(VideoLocalizationDraft):
    project_id: str
    project_name: str
    exported_at: str = Field(default_factory=now_iso)
    export_summary: dict[str, Any] = Field(default_factory=dict)


class ExportRequest(BaseModel):
    result_ids: list[str] = Field(default_factory=list)
    audio_ids: list[str] = Field(default_factory=list)
    project_id: str | None = None
    format: Literal["wav", "mp3", "flac"] = "wav"
    silence_ms: int = Field(default=300, ge=0, le=5000)
    normalize: bool = False


class ExportRecord(BaseModel):
    export_id: str = Field(default_factory=new_id)
    path: str
    format: str
    source_count: int
    created_at: str = Field(default_factory=now_iso)


class AudioQualityResult(BaseModel):
    duration_ms: int = 0
    sample_rate: int = 0
    peak: float = 0.0
    rms: float = 0.0
    silence_ratio: float = 1.0
    size_bytes: int = 0
    passed: bool = False
    warnings: list[str] = Field(default_factory=list)


class PresetTemplate(BaseModel):
    preset_id: str
    name: str
    scene: str
    description: str
    engine_id: str
    input_mode: Literal["text", "audio", "image"] | None = None
    input_assets: list[EngineInputAsset] = Field(default_factory=list)
    sample_text: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_test_id: str | None = None
    recommended_voice_type: str = "reference_voice"
    tags: list[str] = Field(default_factory=list)


class PresetTemplateUpsert(BaseModel):
    preset_id: str | None = None
    name: str
    scene: str = ""
    description: str = ""
    engine_id: str = "indextts-v2"
    input_mode: Literal["text", "audio", "image"] | None = None
    input_assets: list[EngineInputAsset] = Field(default_factory=list)
    sample_text: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_test_id: str | None = None
    recommended_voice_type: str = "reference_voice"
    tags: list[str] = Field(default_factory=list)


class VoiceSeed(BaseModel):
    seed_id: str
    name: str
    description: str = ""
    source: str
    download_url: str
    recommended_engine_id: str = "indextts-v2"
    reference_text: str = ""
    tags: list[str] = Field(default_factory=list)
    license_status: LicenseStatus = LicenseStatus.test_only
    imported_voice_id: str | None = None
    quality: AudioQualityResult | None = None


class VoiceSeedImportRequest(BaseModel):
    seed_id: str


class CommunityVoiceCandidate(BaseModel):
    candidate_id: str
    name: str
    description: str = ""
    source: str
    download_url: str
    recommended_engine_id: str = "indextts-v2"
    reference_text: str = ""
    tags: list[str] = Field(default_factory=list)
    license_status: LicenseStatus = LicenseStatus.test_only
    imported_voice_id: str | None = None
    quality: AudioQualityResult | None = None


class CommunityVoicePack(BaseModel):
    pack_id: str
    name: str
    description: str
    source: str
    license_summary: str
    tags: list[str] = Field(default_factory=list)
    candidates: list[CommunityVoiceCandidate] = Field(default_factory=list)
    imported_count: int = 0


class CommunityVoicePackImportRequest(BaseModel):
    pack_id: str
    candidate_ids: list[str] = Field(default_factory=list)


class EngineAudioDiagnosisRequest(BaseModel):
    text: str = "这是本地引擎音频诊断测试，用来确认生成结果是否清晰可听。"
    reference_audio_path: str | None = None
    voice_id: str | None = None
    language: str = "zh"
    emotion: str | None = None
    emotion_text: str | None = "女，青年，中音调"


class SERPredictRequest(BaseModel):
    voice_id: str


class SERPredictFileRequest(BaseModel):
    file_id: str


class SERBatchPredictRequest(BaseModel):
    voice_ids: list[str] = Field(default_factory=list)
    all: bool = False


class SEREmotionResult(BaseModel):
    voice_id: str
    top_emotion: str | None = None
    emotion_scores: dict[str, float] = Field(default_factory=dict)
    error: str | None = None
